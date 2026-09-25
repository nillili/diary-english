"""QApplication 생성과 의존성 조립.

실행: python -m diary_english            (실제 번역·음성)
      python -m diary_english --dev-fake (개발 모드: 가짜 번역·음성, 창 제목에 표시)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from . import config
from .controller import AppController
from .paths import SingleInstanceLock, default_storage_root, is_writable_dir, qt_app_paths
from .services.playback import PlaybackManager, create_media_player
from .services.repository import FileStudyRepository
from .ui.main_window import MainWindow, QtPrompter
from .workers import TaskRunner

DEFAULT_TRANSLATION_MODEL = "qwen2.5:7b"
ICON_FILE = Path(__file__).resolve().parents[2] / "assets" / "icon.png"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diary_english")
    parser.add_argument("--dev-fake", action="store_true", help="개발 모드: 가짜 번역·음성 사용")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("diary-english")
    app.setOrganizationName("diary-english")
    app.setDesktopFileName("diary-english")  # 패널 고정: diary-english.desktop 과 연결
    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))

    paths, docs_dir = qt_app_paths()
    if args.dev_fake:
        paths = replace(paths, data_dir=paths.data_dir.with_name(paths.data_dir.name + "-dev"))
    paths.ensure()
    logging.basicConfig(
        filename=paths.log_file, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    lock = SingleInstanceLock(paths.lock_file)
    if not lock.acquire():
        QMessageBox.warning(None, "나의 일기 영어회화", "앱이 이미 실행 중입니다.")
        return 1

    default_root = default_storage_root(docs_dir)
    if args.dev_fake:
        default_root = paths.data_dir / "학습자료"
    settings, warning = config.load_settings(paths.config_file, default_root)
    if not settings.translation_model:
        settings = replace(settings, translation_model=DEFAULT_TRANSLATION_MODEL)

    # 저장 위치를 쓸 수 없으면 폴더 선택 안내
    if not is_writable_dir(Path(settings.storage_root)):
        QMessageBox.information(None, "저장 위치", "학습 자료 저장 위치를 쓸 수 없습니다. 폴더를 선택해 주세요.")
        chosen = QFileDialog.getExistingDirectory(None, "저장 위치 선택", str(Path.home()))
        if not chosen or not is_writable_dir(Path(chosen)):
            QMessageBox.warning(None, "저장 위치", "사용할 수 있는 저장 위치가 없어 종료합니다.")
            return 1
        settings = config.with_root_registered(replace(settings, storage_root=chosen))
    config.save_settings(paths.config_file, settings)

    if args.dev_fake:
        from .services.fakes import FakeSpeech, FakeTranslator

        fake_tr = FakeTranslator()
        translator_factory = lambda s: fake_tr  # noqa: E731
        synth = FakeSpeech()
        suffix = " — 개발 모드(가짜 번역·음성)"
    else:
        from .services.speech import MeloSpeech
        from .services.translator import DEFAULT_OLLAMA_URL, ClaudeTranslator, OllamaTranslator

        url = os.environ.get("OLLAMA_HOST_URL", DEFAULT_OLLAMA_URL)

        def translator_factory(s):
            # API 키가 있으면 Claude, 없으면 로컬 Ollama
            if s.anthropic_api_key:
                return ClaudeTranslator(s.anthropic_api_key)
            return OllamaTranslator(s.translation_model, base_url=url)
        synth = MeloSpeech(device=os.environ.get("DIARY_TTS_DEVICE", "cpu"))
        suffix = ""

    prompter = QtPrompter()
    playback = PlaybackManager(create_media_player())
    controller = AppController(
        paths=paths,
        settings=settings,
        translator_factory=translator_factory,
        synthesizer=synth,
        repository=FileStudyRepository(),
        playback=playback,
        model_runner=TaskRunner("model"),
        io_runner=TaskRunner("io"),
        prompter=prompter,
    )
    window = MainWindow(controller, suffix)
    prompter.parent = window
    window.show()
    if warning:
        QMessageBox.warning(window, "설정", warning)
    controller.start()

    code = app.exec()
    controller.shutdown()
    lock.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
