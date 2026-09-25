"""데이터 루트, 캐시, 경로 검증."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "diary-english"
DEFAULT_LIBRARY_SUBDIR = ("영어회화", "학습자료")


@dataclass(frozen=True)
class AppPaths:
    """앱 설정·초안·임시 자료 위치. 학습 자료(보관함)는 여기에 두지 않는다."""

    data_dir: Path

    @property
    def config_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def draft_file(self) -> Path:
        return self.data_dir / "draft.json"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "temp"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "app.lock"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "app.log"

    def ensure(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)


def qt_app_paths() -> tuple[AppPaths, Path]:
    """QStandardPaths 로 (앱 데이터 경로, 문서 폴더) 를 구한다. QApplication 생성 후 호출."""
    from PySide6.QtCore import QStandardPaths

    data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    data_dir = Path(data) if data else Path.home() / f".{APP_DIR_NAME}"
    docs_dir = Path(docs) if docs else Path.home()
    return AppPaths(data_dir), docs_dir


def default_storage_root(documents_dir: Path) -> Path:
    return documents_dir.joinpath(*DEFAULT_LIBRARY_SUBDIR)


def is_writable_dir(path: Path) -> bool:
    """폴더를 만들 수 있고 실제로 파일을 쓸 수 있는지 시험한다."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".write-test-", dir=path)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def atomic_write_text(path: Path, text: str) -> None:
    """같은 폴더의 임시 파일에 쓴 뒤 교체한다. 실패하면 기존 파일은 그대로다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SingleInstanceLock:
    """같은 사용자 데이터 경로에서 앱 한 개만 실행되게 하는 잠금."""

    def __init__(self, lock_file: Path) -> None:
        self._path = lock_file
        self._fh = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
