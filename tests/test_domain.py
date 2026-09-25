from __future__ import annotations

import copy
from pathlib import Path

import pytest

from diary_english.domain import (
    InvalidStudyError,
    Settings,
    TranslationError,
    TranslationResult,
    artifact_to_manifest,
    check_relative_path,
    folder_name,
    make_title,
    manifest_to_artifact,
    sentence_audio_path,
)


def test_sentence_numbering():
    assert sentence_audio_path(0) == "sentences/001.mp3"
    assert sentence_audio_path(11) == "sentences/012.mp3"


def test_title_from_first_nonblank_line():
    assert make_title("\n  \n  오늘은 퇴근하고 동네 공원을 걸었다. 날씨가 선선해서 좋았다.\n다음") == "오늘은 퇴근하고 동네 공원을 걸었다. 날씨가 선선해서"
    assert make_title("  ") == "(제목 없음)"


def test_folder_name_uses_full_uuid():
    name = folder_name("2026-09-24T20:45:00+09:00", "9ac6ba30-8fb8-4c99-809f-3f3ddedb3ed1")
    assert name == "2026-09-24_204500_9ac6ba308fb84c99809f3f3ddedb3ed1"


def test_manifest_roundtrip(temp_artifact):
    data = artifact_to_manifest(temp_artifact)
    back = manifest_to_artifact(data, temp_artifact.folder, temp_artifact.original, saved=False)
    assert back == temp_artifact


@pytest.mark.parametrize("path", ["/etc/passwd", "../x.mp3", "sentences/../../x.mp3", "a\\b.mp3", ""])
def test_bad_paths_rejected(tmp_path: Path, path):
    with pytest.raises(InvalidStudyError):
        check_relative_path(path, tmp_path)


def test_symlink_outside_rejected(tmp_path: Path):
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"x")
    folder = tmp_path / "study"
    folder.mkdir()
    (folder / "link.mp3").symlink_to(outside)
    with pytest.raises(InvalidStudyError):
        check_relative_path("link.mp3", folder)


def _mutated(temp_artifact, fn):
    data = copy.deepcopy(artifact_to_manifest(temp_artifact))
    fn(data)
    return data


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["sentences"][1].update(index=5),
        lambda d: d["sentences"][0].update(text="   "),
        lambda d: d["sentences"][1].update(audio_path=d["sentences"][0]["audio_path"]),
        lambda d: d["sentences"][0].update(audio_path="../evil.mp3"),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(created_at="2026-09-24T20:45:00"),
        lambda d: d.update(sentences=[]),
        lambda d: d["speech"].update(gender="robot"),
        lambda d: d.pop("artifact_id"),
    ],
)
def test_invalid_manifest_rejected(temp_artifact, mutate):
    data = _mutated(temp_artifact, mutate)
    with pytest.raises(InvalidStudyError):
        manifest_to_artifact(data, temp_artifact.folder, temp_artifact.original, saved=False)


def test_translation_result_limits():
    with pytest.raises(TranslationError):
        TranslationResult(())
    with pytest.raises(TranslationError):
        TranslationResult(("ok", ""))
    with pytest.raises(TranslationError):
        TranslationResult(tuple(f"s{i}." for i in range(31)))
    with pytest.raises(TranslationError):
        TranslationResult(("x" * 1001,))
    assert TranslationResult(("A.", "B.")).text == "A.\nB."


def test_settings_roundtrip_and_validation():
    s = Settings(style_id="casual", speed=1.2, storage_root="/a", library_roots=("/a", "/b"))
    assert Settings.from_dict(s.to_dict()) == s
    with pytest.raises(ValueError):
        Settings.from_dict({"style_id": "pirate"})


def test_manifest_without_examples_still_loads(temp_artifact):
    data = artifact_to_manifest(temp_artifact)
    data.pop("examples")
    back = manifest_to_artifact(data, temp_artifact.folder, temp_artifact.original, saved=False)
    assert back.examples == ()
