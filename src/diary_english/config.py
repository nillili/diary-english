"""설정·초안 읽기와 원자적 저장."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from .domain import Draft, Settings, StorageError
from .paths import atomic_write_text

log = logging.getLogger(__name__)


def load_settings(path: Path, default_root: Path) -> tuple[Settings, str]:
    """설정을 읽는다. (설정, 경고문) 을 돌려주며 경고문이 비어 있지 않으면 기본값을 쓴 것이다."""
    warning = ""
    settings = Settings()
    if path.exists():
        try:
            settings = Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("설정 파일 읽기 실패: %s", exc)
            warning = "설정 파일을 읽지 못해 기본 설정을 사용합니다."
            settings = Settings()
    if not settings.storage_root:
        settings = replace(settings, storage_root=str(default_root))
    return with_root_registered(settings), warning


def with_root_registered(settings: Settings) -> Settings:
    """현재 저장 위치를 library_roots 에 포함시킨다(이전 위치는 유지)."""
    if settings.storage_root and settings.storage_root not in settings.library_roots:
        return replace(settings, library_roots=settings.library_roots + (settings.storage_root,))
    return settings


def save_settings(path: Path, settings: Settings) -> None:
    try:
        atomic_write_text(path, json.dumps(settings.to_dict(), ensure_ascii=False, indent=2))
    except OSError as exc:
        raise StorageError(f"설정을 저장하지 못했습니다: {exc}") from exc


def load_draft(path: Path) -> Draft | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data["text"]
        if not isinstance(text, str) or not text.strip():
            return None
        return Draft(text=text, saved_at=str(data.get("saved_at", "")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("초안 파일 읽기 실패: %s", exc)
        return None


def save_draft(path: Path, draft: Draft) -> None:
    try:
        atomic_write_text(
            path, json.dumps({"text": draft.text, "saved_at": draft.saved_at}, ensure_ascii=False, indent=2)
        )
    except OSError as exc:
        raise StorageError(f"초안을 보관하지 못했습니다: {exc}") from exc


def clear_draft(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("초안 파일 삭제 실패: %s", exc)
