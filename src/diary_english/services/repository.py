"""학습 자료의 영구 저장·조회·검증.

저장 순서: .pending 폴더에 복사 → manifest 마지막 기록 → 실제 파일 검증 → 최종 폴더로 rename.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from ..domain import (
    FULL_AUDIO_FILE,
    MANIFEST_FILE,
    ORIGINAL_FILE,
    TRANSLATION_FILE,
    InvalidStudyError,
    LibraryEntry,
    StorageError,
    StudyArtifact,
    artifact_to_manifest,
    check_relative_path,
    folder_name,
    manifest_to_artifact,
)
from ..paths import atomic_write_text
from .audio_files import read_duration_ms

log = logging.getLogger(__name__)

PENDING_PREFIX = ".pending-"
FOLDER_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{32}$")


class StudyRepository(Protocol):
    def save(self, artifact: StudyArtifact, root: Path) -> StudyArtifact: ...
    def list_entries(self, roots: list[Path]) -> list[LibraryEntry]: ...
    def load(self, folder: Path) -> StudyArtifact: ...


def write_texts_and_manifest(folder: Path, artifact: StudyArtifact) -> None:
    """원문·번역을 쓰고 manifest 를 마지막에 쓴다."""
    (folder / ORIGINAL_FILE).write_text(artifact.original, encoding="utf-8", newline="\n")
    (folder / TRANSLATION_FILE).write_text(artifact.translation, encoding="utf-8", newline="\n")
    atomic_write_text(
        folder / MANIFEST_FILE,
        json.dumps(artifact_to_manifest(artifact), ensure_ascii=False, indent=2),
    )


def read_artifact(folder: Path, *, saved: bool, verify_audio: bool = False) -> StudyArtifact:
    """폴더의 manifest 와 파일을 검증해서 읽는다. 문제가 있으면 InvalidStudyError."""
    manifest_path = folder / MANIFEST_FILE
    if not manifest_path.is_file():
        raise InvalidStudyError("manifest.json 이 없습니다.")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidStudyError(f"manifest.json 을 읽을 수 없습니다: {exc}") from exc

    original_path = check_relative_path(ORIGINAL_FILE, folder)
    try:
        original = original_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidStudyError("original.txt 를 읽을 수 없습니다.") from exc

    artifact = manifest_to_artifact(data, folder, original, saved=saved)

    try:
        translation = check_relative_path(TRANSLATION_FILE, folder).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidStudyError("translation.txt 를 읽을 수 없습니다.") from exc
    if translation != artifact.translation:
        raise InvalidStudyError("translation.txt 가 문장 목록과 다릅니다.")

    missing: list[str] = []
    for rel in [s.audio_path for s in artifact.sentences] + [FULL_AUDIO_FILE]:
        p = check_relative_path(rel, folder)
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(rel)
    if missing:
        raise InvalidStudyError("음성 파일이 없거나 비어 있습니다: " + ", ".join(missing))

    if verify_audio:
        for s in artifact.sentences:
            read_duration_ms(artifact.sentence_file(s.index))
        read_duration_ms(artifact.full_audio_file)
    return artifact


class FileStudyRepository:
    def save(self, artifact: StudyArtifact, root: Path) -> StudyArtifact:
        final = root / folder_name(artifact.created_at, artifact.artifact_id)

        if final.exists():
            return self._already_saved(final, artifact)

        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"저장 위치를 만들 수 없습니다: {exc}") from exc

        pending = root / f"{PENDING_PREFIX}{artifact.artifact_id}-{uuid.uuid4().hex[:8]}"
        created = False
        try:
            pending.mkdir()
            created = True
            for s in artifact.sentences:
                dest = pending / s.audio_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact.sentence_file(s.index), dest)
            shutil.copyfile(artifact.full_audio_file, pending / FULL_AUDIO_FILE)
            write_texts_and_manifest(pending, artifact)

            check = read_artifact(pending, saved=True, verify_audio=True)
            if check.artifact_id != artifact.artifact_id or check.translation != artifact.translation:
                raise StorageError("저장한 내용이 원본과 다릅니다.")

            if final.exists():
                raise StorageError("같은 이름의 폴더가 이미 있습니다.")
            os.rename(pending, final)
            created = False
        except (OSError, InvalidStudyError, StorageError) as exc:
            if created:
                shutil.rmtree(pending, ignore_errors=True)
            if isinstance(exc, StorageError):
                raise
            raise StorageError(f"저장 실패: {exc}") from exc
        except BaseException:
            if created:
                shutil.rmtree(pending, ignore_errors=True)
            raise

        return replace(artifact, folder=final, saved=True)

    def _already_saved(self, final: Path, artifact: StudyArtifact) -> StudyArtifact:
        """최종 폴더가 이미 있으면 같은 자료가 온전히 저장됐는지만 확인한다. 덮어쓰지 않는다."""
        try:
            existing = read_artifact(final, saved=True)
        except InvalidStudyError as exc:
            raise StorageError(f"같은 이름의 폴더가 손상된 상태로 있습니다: {exc}") from exc
        if existing.artifact_id != artifact.artifact_id:
            raise StorageError("같은 이름의 다른 자료가 이미 있습니다.")
        return existing

    def load(self, folder: Path) -> StudyArtifact:
        return read_artifact(folder, saved=True)

    def list_entries(self, roots: list[Path]) -> list[LibraryEntry]:
        entries: list[LibraryEntry] = []
        seen: set[Path] = set()
        for root in roots:
            try:
                children = sorted(root.iterdir()) if root.is_dir() else []
            except OSError as exc:
                log.warning("보관 위치를 읽지 못함: %s (%s)", root, exc)
                continue
            for child in children:
                if child.name.startswith(".") or not child.is_dir():
                    continue
                try:
                    key = child.resolve()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                entry = self._entry(child)
                if entry is not None:
                    entries.append(entry)
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    def _entry(self, folder: Path) -> LibraryEntry | None:
        has_manifest = (folder / MANIFEST_FILE).is_file()
        if not has_manifest and not FOLDER_PATTERN.match(folder.name):
            return None  # 앱이 만든 폴더가 아니면 무시
        try:
            a = read_artifact(folder, saved=True)
        except InvalidStudyError as exc:
            return LibraryEntry(
                folder=folder, artifact_id="", created_at=_date_from_name(folder.name),
                title=folder.name, sentence_count=0, style_id="", voice_gender="unknown",
                error=str(exc),
            )
        return LibraryEntry(
            folder=folder, artifact_id=a.artifact_id, created_at=a.created_at, title=a.title,
            sentence_count=len(a.sentences), style_id=a.style_id, voice_gender=a.speech.gender,
            original=a.original,
        )

    def list_temp(self, temp_root: Path) -> list[StudyArtifact]:
        """이전 실행에서 남은 완성된 임시 결과(미저장)를 찾는다. 불완전한 것은 제외."""
        found: list[StudyArtifact] = []
        if not temp_root.is_dir():
            return found
        for child in sorted(temp_root.iterdir()):
            if child.is_dir() and (child / MANIFEST_FILE).is_file():
                try:
                    found.append(read_artifact(child, saved=False))
                except InvalidStudyError:
                    continue
        found.sort(key=lambda a: a.created_at, reverse=True)
        return found


def _date_from_name(name: str) -> str:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else ""


def search_entries(entries: list[LibraryEntry], query: str) -> list[LibraryEntry]:
    q = query.strip().lower()
    if not q:
        return list(entries)
    return [e for e in entries if q in e.date_text or q in e.title.lower() or q in e.original.lower()]
