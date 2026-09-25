"""백그라운드 작업.

앞부분은 Qt 와 무관한 변환 절차, 뒷부분은 QThread 에서 작업을 실행하는 러너다.
작업자는 UI 를 직접 건드리지 않고 progress / partial / succeeded / failed 신호만 보낸다.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .domain import (
    FULL_AUDIO_FILE,
    SENTENCE_GAP_MS,
    AppError,
    CancelledError,
    ConversionRequest,
    SentenceAsset,
    SpeechError,
    SpeechInfo,
    StudyArtifact,
    make_title,
    now_iso,
    sentence_audio_path,
)
from .services.audio_files import concat_with_gap, write_mp3
from .services.repository import read_artifact, write_texts_and_manifest
from .services.speech import SpeechSynthesizer
from .services.translator import Translator

log = logging.getLogger(__name__)

# progress(stage, current, total, data=None). data 가 있으면 중간 결과(partial)로도 전달된다.
Progress = Callable[..., None]


def run_conversion(
    request: ConversionRequest,
    translator: Translator,
    synthesizer: SpeechSynthesizer,
    temp_root: Path,
    progress: Progress,
    cancel: threading.Event,
) -> StudyArtifact:
    """번역 → 문장별 MP3 → 전체 MP3 → manifest 를 임시 폴더에 만든다. 실패하면 폴더를 지운다."""
    settings = request.settings
    artifact_id = str(uuid.uuid4())
    folder = temp_root / uuid.UUID(artifact_id).hex
    folder.mkdir(parents=True, exist_ok=False)
    try:
        progress("translate", 0, 1)
        result = translator.translate(request)
        progress("translated", 0, len(result.sentences), result)
        if cancel.is_set():
            raise CancelledError("작업이 취소되었습니다.")

        voices = {v.id: v for v in synthesizer.list_voices()}
        voice = voices.get(settings.voice_id)
        if voice is None:
            raise SpeechError(f"선택한 음성을 사용할 수 없습니다: {settings.voice_id}")

        total = len(result.sentences)
        buffers = []
        assets: list[SentenceAsset] = []
        for i, text in enumerate(result.sentences):
            if cancel.is_set():
                raise CancelledError("작업이 취소되었습니다.")
            progress("speech", i + 1, total)
            buf = synthesizer.synthesize(text, voice.id, settings.speed)
            rel = sentence_audio_path(i)
            duration = write_mp3(folder / rel, buf)
            buffers.append(buf)
            assets.append(SentenceAsset(i, text, rel, duration))

        progress("full_audio", 0, 1)
        write_mp3(folder / FULL_AUDIO_FILE, concat_with_gap(buffers, SENTENCE_GAP_MS))

        artifact = StudyArtifact(
            artifact_id=artifact_id,
            created_at=now_iso(),
            title=make_title(request.original),
            original=request.original,
            style_id=settings.style_id,
            translation_model=result.model,
            speech=SpeechInfo(engine=voice.engine_id, voice_id=voice.id, gender=voice.gender, speed=settings.speed),
            sentences=tuple(assets),
            folder=folder,
            saved=False,
            source_language=settings.source_language,
            target_language=settings.target_language,
            sentence_gap_ms=SENTENCE_GAP_MS,
            examples=result.examples,
        )
        write_texts_and_manifest(folder, artifact)
        read_artifact(folder, saved=False, verify_audio=True)
        return artifact
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


# ---------------------------------------------------------------- Qt 러너

Job = Callable[[Progress, threading.Event], Any]


class _Worker(QObject):
    progress = Signal(str, str, int, int)
    partial = Signal(str, object)
    succeeded = Signal(str, object)
    failed = Signal(str, object)

    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel

    @Slot(str, object)
    def run(self, request_id: str, job: Job) -> None:
        def report(stage: str, current: int, total: int, data: object = None) -> None:
            if data is not None:
                self.partial.emit(request_id, data)
            self.progress.emit(request_id, stage, current, total)

        try:
            result = job(report, self._cancel)
        except AppError as exc:
            self.failed.emit(request_id, exc)
        except Exception as exc:  # 예상 못 한 오류도 UI 가 멈추지 않도록 전달
            log.exception("작업 실패")
            self.failed.emit(request_id, exc)
        else:
            self.succeeded.emit(request_id, result)


class TaskRunner(QObject):
    """작업 하나씩 순서대로 실행하는 전용 스레드. 같은 러너의 작업은 동시에 돌지 않는다."""

    progress = Signal(str, str, int, int)
    partial = Signal(str, object)
    succeeded = Signal(str, object)
    failed = Signal(str, object)
    _submit = Signal(str, object)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.cancel_event = threading.Event()
        self._thread = QThread()
        self._thread.setObjectName(name)
        self._worker = _Worker(self.cancel_event)
        self._worker.moveToThread(self._thread)
        self._submit.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.partial.connect(self.partial)
        self._worker.succeeded.connect(self._on_done_ok)
        self._worker.failed.connect(self._on_done_fail)
        self._running = 0
        self._thread.start()

    @property
    def busy(self) -> bool:
        return self._running > 0

    def submit(self, request_id: str, job: Job) -> None:
        self._running += 1
        self._submit.emit(request_id, job)

    def _on_done_ok(self, rid: str, result: object) -> None:
        self._running -= 1
        self.succeeded.emit(rid, result)

    def _on_done_fail(self, rid: str, error: object) -> None:
        self._running -= 1
        self.failed.emit(rid, error)

    def shutdown(self, wait_ms: int = -1) -> bool:
        """중단을 요청하고 현재 작업이 경계에서 끝나길 기다린다. 스레드를 강제 종료하지 않는다."""
        self.cancel_event.set()
        self._thread.quit()
        if wait_ms < 0:
            return self._thread.wait()
        return self._thread.wait(wait_ms)


class SyncRunner(QObject):
    """테스트용: 호출 즉시 같은 스레드에서 실행한다. TaskRunner 와 같은 신호를 가진다."""

    progress = Signal(str, str, int, int)
    partial = Signal(str, object)
    succeeded = Signal(str, object)
    failed = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.cancel_event = threading.Event()
        self.busy = False

    def submit(self, request_id: str, job: Job) -> None:
        self.busy = True
        try:
            def report(stage: str, current: int, total: int, data: object = None) -> None:
                if data is not None:
                    self.partial.emit(request_id, data)
                self.progress.emit(request_id, stage, current, total)

            result = job(report, self.cancel_event)
        except Exception as exc:
            self.busy = False
            self.failed.emit(request_id, exc)
        else:
            self.busy = False
            self.succeeded.emit(request_id, result)

    def shutdown(self, wait_ms: int = -1) -> bool:
        self.cancel_event.set()
        return True
