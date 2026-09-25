"""실제 Ollama + MeloTTS + 작업 스레드 통합 확인. 기본 실행에서 제외된다.

실행: python -m pytest -m integration
"""

from __future__ import annotations

import pytest

from diary_english.controller import AppController
from diary_english.domain import DataState, Settings, WorkState
from diary_english.paths import AppPaths
from diary_english.services.playback import PlaybackManager
from diary_english.services.repository import FileStudyRepository
from diary_english.services.speech import MeloSpeech
from diary_english.services.translator import OllamaTranslator
from diary_english.workers import TaskRunner

from .fakes_qt import FakePlayer
from .test_controller import ScriptedPrompter

pytestmark = pytest.mark.integration


def test_real_convert_save_reopen(qtbot, tmp_path):
    paths = AppPaths(tmp_path / "appdata")
    paths.ensure()
    root = tmp_path / "학습 자료"
    model_runner, io_runner = TaskRunner("model"), TaskRunner("io")
    c = AppController(
        paths=paths,
        settings=Settings(storage_root=str(root), library_roots=(str(root),), translation_model="qwen2.5:7b"),
        translator_factory=lambda s: OllamaTranslator(s.translation_model),
        synthesizer=MeloSpeech(),
        repository=FileStudyRepository(),
        playback=PlaybackManager(FakePlayer()),
        model_runner=model_runner,
        io_runner=io_runner,
        prompter=ScriptedPrompter(),
    )
    try:
        c.start()
        previews = []
        c.translation_preview.connect(lambda r: previews.append((r.sentences, c.work)))
        c.set_input_text("오늘은 산책하지 않았다. 내일은 공원에 갈 예정이다.")
        c.convert()
        assert c.work == WorkState.CONVERTING
        qtbot.waitUntil(lambda: c.work == WorkState.IDLE, timeout=600_000)
        assert c.data == DataState.TEMP_READY, "변환 실패"
        assert previews and previews[0][1] == WorkState.CONVERTING  # 번역이 MP3 보다 먼저 전달됨
        assert previews[0][0] == tuple(s.text for s in c.artifact.sentences)
        print("\n".join(s.text for s in c.artifact.sentences))
        print("예시:", c.artifact.examples)
        c.save()
        qtbot.waitUntil(lambda: c.data == DataState.SAVED_READY, timeout=30_000)
        qtbot.waitUntil(lambda: len(c.entries) == 1, timeout=30_000)
        c.open_entry(c.entries[0])
        qtbot.waitUntil(lambda: c.work == WorkState.IDLE and c.data == DataState.SAVED_READY, timeout=30_000)
    finally:
        model_runner.shutdown()
        io_runner.shutdown()
