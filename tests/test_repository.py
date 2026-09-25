from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from diary_english import config
from diary_english.domain import Draft, InvalidStudyError, Settings, StorageError
from diary_english.services import repository as repo_mod
from diary_english.services.repository import FileStudyRepository, read_artifact, search_entries

from .conftest import make_temp_artifact


@pytest.fixture
def repo():
    return FileStudyRepository()


def test_save_creates_one_complete_set(repo, temp_artifact, tmp_path):
    root = tmp_path / "학습 자료"  # 한글·공백 경로 (A16)
    saved = repo.save(temp_artifact, root)
    assert saved.saved and saved.folder.parent == root
    names = sorted(p.name for p in saved.folder.rglob("*") if p.is_file())
    assert names == ["001.mp3", "002.mp3", "full.mp3", "manifest.json", "original.txt", "translation.txt"]
    assert [p.name for p in root.iterdir()] == [saved.folder.name]
    loaded = repo.load(saved.folder)
    assert loaded.original == temp_artifact.original
    assert loaded.translation == temp_artifact.translation
    # 임시 결과는 저장소가 지우지 않는다(controller 책임)
    assert temp_artifact.folder.exists()


def test_save_twice_is_idempotent(repo, temp_artifact, tmp_path):
    root = tmp_path / "lib"
    a = repo.save(temp_artifact, root)
    b = repo.save(temp_artifact, root)
    assert a.folder == b.folder
    assert len(list(root.iterdir())) == 1  # A04


def test_save_failure_keeps_temp_and_leaves_no_pending(repo, temp_artifact, tmp_path, monkeypatch):
    root = tmp_path / "lib"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(repo_mod.os, "rename", boom)
    with pytest.raises(StorageError):
        repo.save(temp_artifact, root)
    assert list(root.iterdir()) == []  # pending 정리, 완성 자료 없음 (A14)
    assert read_artifact(temp_artifact.folder, saved=False)  # 임시 결과 보존
    monkeypatch.undo()
    saved = repo.save(temp_artifact, root)  # 재시도 가능
    assert saved.saved


def test_save_does_not_touch_existing_material(repo, tmp_path):
    root = tmp_path / "lib"
    first = repo.save(make_temp_artifact(tmp_path / "t1"), root)
    before = (first.folder / "manifest.json").read_bytes()
    second = make_temp_artifact(tmp_path / "t2")
    (second.folder / "full.mp3").unlink()  # 원본 손상 → 저장 실패
    with pytest.raises(StorageError):
        repo.save(second, root)
    assert (first.folder / "manifest.json").read_bytes() == before
    assert [p.name for p in root.iterdir()] == [first.folder.name]


def test_list_skips_pending_and_reports_damage(repo, tmp_path):
    root = tmp_path / "lib"
    good = repo.save(make_temp_artifact(tmp_path / "t1"), root)
    bad = repo.save(make_temp_artifact(tmp_path / "t2", original="둘째 일기"), root)
    (bad.folder / "sentences" / "001.mp3").unlink()  # 누락 MP3 (A17)
    (root / ".pending-abc").mkdir()
    (root / "unrelated folder").mkdir()

    entries = repo.list_entries([root])
    by_ok = {e.ok: e for e in entries}
    assert len(entries) == 2
    assert by_ok[True].folder == good.folder
    assert "001.mp3" in by_ok[False].error
    with pytest.raises(InvalidStudyError):
        repo.load(bad.folder)


def test_list_multiple_roots_sorted_desc(repo, tmp_path):
    a = repo.save(make_temp_artifact(tmp_path / "t1"), tmp_path / "old")
    data = json.loads((a.folder / "manifest.json").read_text())
    data["created_at"] = "2020-01-01T00:00:00+09:00"
    (a.folder / "manifest.json").write_text(json.dumps(data))
    b = repo.save(make_temp_artifact(tmp_path / "t2"), tmp_path / "new")
    entries = repo.list_entries([tmp_path / "old", tmp_path / "new", tmp_path / "missing"])
    assert [e.folder for e in entries] == [b.folder, a.folder]  # A19


def test_manifest_path_escape_rejected(repo, tmp_path):
    saved = repo.save(make_temp_artifact(tmp_path / "t1"), tmp_path / "lib")
    data = json.loads((saved.folder / "manifest.json").read_text())
    data["sentences"][0]["audio_path"] = "../../outside.mp3"
    (saved.folder / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(InvalidStudyError):
        repo.load(saved.folder)


def test_translation_mismatch_rejected(repo, tmp_path):
    saved = repo.save(make_temp_artifact(tmp_path / "t1"), tmp_path / "lib")
    (saved.folder / "translation.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(InvalidStudyError):
        repo.load(saved.folder)


def test_list_temp_finds_only_complete(repo, tmp_path):
    temp = tmp_path / "temp"
    ok = make_temp_artifact(temp / "a")
    broken = make_temp_artifact(temp / "b")
    (broken.folder / "manifest.json").unlink()
    (temp / "c").mkdir()
    found = repo.list_temp(temp)
    assert [a.artifact_id for a in found] == [ok.artifact_id]


def test_search(repo, tmp_path):
    root = tmp_path / "lib"
    repo.save(make_temp_artifact(tmp_path / "t1", original="친구와 점심\n맛있었다"), root)
    repo.save(make_temp_artifact(tmp_path / "t2", original="주말 장보기"), root)
    entries = repo.list_entries([root])
    assert [e.title for e in search_entries(entries, "맛있")] == ["친구와 점심"]
    assert len(search_entries(entries, entries[0].date_text)) == 2
    assert len(search_entries(entries, "")) == 2


# ------------------------------------------------------------ 설정·초안

def test_settings_atomic_save_and_load(tmp_path):
    p = tmp_path / "cfg" / "settings.json"
    s = Settings(style_id="casual", storage_root=str(tmp_path / "lib"))
    config.save_settings(p, s)
    loaded, warn = config.load_settings(p, tmp_path / "default")
    assert warn == "" and loaded.style_id == "casual"
    assert loaded.library_roots == (str(tmp_path / "lib"),)


def test_settings_corrupt_uses_default(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{broken", encoding="utf-8")
    loaded, warn = config.load_settings(p, tmp_path / "default")
    assert warn and loaded.storage_root == str(tmp_path / "default")


def test_settings_save_failure_keeps_old(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    config.save_settings(p, Settings(style_id="casual"))
    before = p.read_text()

    def boom(*a, **k):
        raise OSError("no space")

    monkeypatch.setattr("diary_english.paths.os.replace", boom)
    with pytest.raises(StorageError):
        config.save_settings(p, Settings(style_id="work_daily"))
    assert p.read_text() == before
    assert [x.name for x in tmp_path.iterdir()] == ["settings.json"]


def test_draft_roundtrip(tmp_path):
    p = tmp_path / "draft.json"
    assert config.load_draft(p) is None
    config.save_draft(p, Draft("초안 일기"))
    assert config.load_draft(p).text == "초안 일기"
    config.clear_draft(p)
    assert not p.exists()
