"""작업 흐름과 화면 상태 전환.

UI 는 이 클래스의 메서드를 부르고 신호를 받아 화면만 갱신한다.
HTTP·TTS·파일 작업은 러너(작업 스레드)에서 실행한다.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

from PySide6.QtCore import QObject, Signal

from . import config
from .domain import (
    MAX_INPUT_CHARS,
    STYLES,
    AppError,
    CancelledError,
    ConversionRequest,
    DataState,
    Draft,
    LibraryEntry,
    Settings,
    StudyArtifact,
    VoiceInfo,
    WorkState,
)
from .paths import AppPaths, is_writable_dir
from .services.playback import PlaybackManager
from .services.repository import FileStudyRepository
from .services.speech import SpeechSynthesizer
from .services.translator import Translator
from .workers import run_conversion

log = logging.getLogger(__name__)

INPUT_CHANGED_MSG = "입력이 변경되었습니다. 다시 변환해 주세요."


class Prompter(Protocol):
    def ask_unsaved_result(self, draft_differs: bool) -> str: ...  # save | discard | cancel
    def ask_unsaved_draft(self) -> str: ...  # keep | discard | cancel
    def ask_recover(self, kind: str, summary: str) -> str: ...  # restore | discard | later
    def show_error(self, title: str, message: str) -> None: ...


class Runner(Protocol):
    progress: Signal
    partial: Signal
    succeeded: Signal
    failed: Signal
    cancel_event: object

    def submit(self, request_id: str, job) -> None: ...
    def shutdown(self, wait_ms: int = -1) -> bool: ...


def error_text(exc: object) -> str:
    if isinstance(exc, AppError):
        return f"{exc.stage} 실패: {exc}"
    return f"알 수 없는 오류: {exc}"


class AppController(QObject):
    work_changed = Signal(object)  # WorkState
    data_changed = Signal(object)  # DataState
    artifact_changed = Signal(object)  # StudyArtifact | None
    translation_preview = Signal(object)  # TranslationResult: MP3 생성 전 번역 결과
    input_replaced = Signal(str)  # 입력창 내용을 바꿔야 할 때
    status = Signal(str)
    library_changed = Signal(list)
    settings_changed = Signal(object)
    voices_changed = Signal(list)
    voices_error = Signal(str)
    show_study = Signal()
    close_ready = Signal()

    def __init__(
        self,
        *,
        paths: AppPaths,
        settings: Settings,
        translator_factory: Callable[[Settings], Translator],
        synthesizer: SpeechSynthesizer,
        repository: FileStudyRepository,
        playback: PlaybackManager,
        model_runner: Runner,
        io_runner: Runner,
        prompter: Prompter,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.settings = settings
        self._translator_factory = translator_factory
        self._synth = synthesizer
        self.repo = repository
        self.playback = playback
        self._model = model_runner
        self._io = io_runner
        self.prompter = prompter

        self.work = WorkState.IDLE
        self.data = DataState.NONE
        self.artifact: StudyArtifact | None = None
        self.input_text = ""
        self.entries: list[LibraryEntry] = []
        self.voices: list[VoiceInfo] = []
        self._closing = False

        self._conv_rid: str | None = None
        self._io_jobs: dict[str, tuple[str, Callable[[object], None] | None]] = {}
        self._voices_rid: str | None = None

        self._model.progress.connect(self._on_progress)
        self._model.partial.connect(self._on_partial)
        self._model.succeeded.connect(self._on_model_ok)
        self._model.failed.connect(self._on_model_fail)
        self._io.succeeded.connect(self._on_io_ok)
        self._io.failed.connect(self._on_io_fail)

    # ============================================================ 시작
    def start(self) -> None:
        self.refresh_library()
        self.load_voices()
        self._offer_recovery()

    def _offer_recovery(self) -> None:
        for temp in self.repo.list_temp(self.paths.temp_dir):
            ans = self.prompter.ask_recover(
                "result", f"{temp.created_at[:16].replace('T', ' ')} · {temp.title} ({len(temp.sentences)}문장)"
            )
            if ans == "restore":
                self._apply_artifact(temp, DataState.TEMP_READY)
                self._set_input(temp.original)
                break
            if ans == "discard":
                shutil.rmtree(temp.folder, ignore_errors=True)
        draft = config.load_draft(self.paths.draft_file)
        if draft is not None:
            ans = self.prompter.ask_recover("draft", draft.text.strip().splitlines()[0][:30])
            if ans == "restore":
                self._set_input(draft.text)
                config.clear_draft(self.paths.draft_file)
            elif ans == "discard":
                config.clear_draft(self.paths.draft_file)

    def load_voices(self) -> None:
        rid = uuid.uuid4().hex
        self._voices_rid = rid
        synth = self._synth
        self._model.submit(rid, lambda progress, cancel: synth.list_voices())

    # ============================================================ 입력
    def set_input_text(self, text: str) -> None:
        self.input_text = text
        if self.artifact is not None and self.work == WorkState.IDLE:
            if self.input_dirty:
                self.status.emit(INPUT_CHANGED_MSG)
            else:
                self.status.emit(self._data_message())

    @property
    def input_dirty(self) -> bool:
        """현재 자료의 원문과 다른, 변환하지 않은 입력이 있는가."""
        base = self.artifact.original if self.artifact else ""
        return bool(self.input_text.strip()) and self.input_text != base

    def _set_input(self, text: str) -> None:
        self.input_text = text
        self.input_replaced.emit(text)

    # ============================================================ 가능 여부
    @property
    def can_convert(self) -> bool:
        return (
            self.work == WorkState.IDLE
            and bool(self.input_text.strip())
            and len(self.input_text) <= MAX_INPUT_CHARS
        )

    @property
    def can_play(self) -> bool:
        return self.work == WorkState.IDLE and self.data != DataState.NONE

    @property
    def can_save(self) -> bool:
        return self.work == WorkState.IDLE and self.data == DataState.TEMP_READY

    @property
    def can_switch(self) -> bool:
        return self.work == WorkState.IDLE

    # ============================================================ 변환
    def convert(self) -> None:
        if not self.can_convert:
            if len(self.input_text) > MAX_INPUT_CHARS:
                self.status.emit(f"일기는 {MAX_INPUT_CHARS}자 이하로 입력해 주세요.")
            return
        if self.data == DataState.TEMP_READY:
            ans = self.prompter.ask_unsaved_result(False)
            if ans == "cancel":
                return
            if ans == "save":
                self.save(then=self._start_convert)
                return
        self._start_convert()

    def _start_convert(self) -> None:
        if not self.can_convert:
            return
        self.playback.stop()
        request = ConversionRequest.create(self.input_text, self.settings)
        self._conv_rid = request.request_id
        self._model.cancel_event.clear()
        self._set_work(WorkState.CONVERTING)
        self.status.emit("번역 중")
        translator = self._translator_factory(request.settings)
        synth, temp_root = self._synth, self.paths.temp_dir
        self._model.submit(
            request.request_id,
            lambda progress, cancel: run_conversion(request, translator, synth, temp_root, progress, cancel),
        )

    def _on_progress(self, rid: str, stage: str, current: int, total: int) -> None:
        if rid != self._conv_rid:
            return
        if stage == "translate":
            self.status.emit("번역 중")
        elif stage == "speech":
            self.status.emit(f"MP3 생성 중: {current}/{total}")
        elif stage == "full_audio":
            self.status.emit("전체 MP3 만드는 중")

    def _on_partial(self, rid: str, data: object) -> None:
        if rid == self._conv_rid:
            self.translation_preview.emit(data)
            self.status.emit(f"MP3 생성 중: 0/{len(data.sentences)}")  # type: ignore[attr-defined]

    def _on_model_ok(self, rid: str, result: object) -> None:
        if rid == self._voices_rid:
            self._voices_rid = None
            self.voices = list(result)  # type: ignore[arg-type]
            self.voices_changed.emit(self.voices)
            return
        if rid != self._conv_rid:
            # 오래된 작업 결과: 화면에 적용하지 않고 임시 파일만 정리
            if isinstance(result, StudyArtifact) and not result.saved:
                shutil.rmtree(result.folder, ignore_errors=True)
            return
        self._conv_rid = None
        assert isinstance(result, StudyArtifact)
        self._set_work(WorkState.IDLE)
        self._apply_artifact(result, DataState.TEMP_READY)
        self.set_input_text(self.input_text)
        self._after_work()

    def _on_model_fail(self, rid: str, error: object) -> None:
        if rid == self._voices_rid:
            self._voices_rid = None
            self.voices_error.emit(error_text(error))
            return
        if rid != self._conv_rid:
            return
        self._conv_rid = None
        self._set_work(WorkState.IDLE)
        self.artifact_changed.emit(self.artifact)  # 미리 보인 번역 대신 이전 결과로 되돌림
        if isinstance(error, CancelledError):
            self.status.emit("변환이 취소되었습니다.")
        else:
            self.status.emit(error_text(error) + " 다시 시도해 주세요.")
        self._after_work()

    # ============================================================ 저장
    def save(self, then: Callable[[], None] | None = None) -> None:
        if not self.can_save or self.artifact is None:
            return
        self.playback.stop()
        artifact = self.artifact
        root = Path(self.settings.storage_root)
        self._set_work(WorkState.SAVING)
        self.status.emit("저장 중")
        rid = uuid.uuid4().hex
        self._io_jobs[rid] = ("save", then)
        repo = self.repo
        self._io.submit(rid, lambda progress, cancel: repo.save(artifact, root))

    def _on_saved(self, saved: StudyArtifact, then: Callable[[], None] | None) -> None:
        self._set_work(WorkState.IDLE)
        old = self.artifact
        if old is not None and old.artifact_id == saved.artifact_id:
            if not old.saved and old.folder != saved.folder:
                shutil.rmtree(old.folder, ignore_errors=True)
            self._apply_artifact(saved, DataState.SAVED_READY, keep_position=True)
            self.status.emit("저장 완료")
        self.refresh_library()
        if then is not None:
            then()
        else:
            self._after_work()

    # ============================================================ 보관함
    def refresh_library(self) -> None:
        roots = [Path(r) for r in self.settings.library_roots]
        rid = uuid.uuid4().hex
        self._io_jobs[rid] = ("list", None)
        repo = self.repo
        self._io.submit(rid, lambda progress, cancel: repo.list_entries(roots))

    def open_entry(self, entry: LibraryEntry) -> None:
        if not self.can_switch:
            return  # LOADING 중 중복 클릭 등은 무시
        if not entry.ok:
            self.prompter.show_error("자료를 열 수 없습니다", f"{entry.folder.name}\n\n{entry.error}")
            return
        folder = entry.folder
        self._protect(lambda: self._start_load(folder))

    def _start_load(self, folder: Path) -> None:
        if not self.can_switch:
            return
        self.playback.stop()
        self._set_work(WorkState.LOADING)
        self.status.emit("불러오는 중")
        rid = uuid.uuid4().hex
        self._io_jobs[rid] = ("load", None)
        repo = self.repo
        self._io.submit(rid, lambda progress, cancel: repo.load(folder))

    def _on_loaded(self, artifact: StudyArtifact) -> None:
        self._set_work(WorkState.IDLE)
        self._apply_artifact(artifact, DataState.SAVED_READY)
        self._set_input(artifact.original)
        self.show_study.emit()
        self._after_work()

    # ============================================================ io 결과 분배
    def _on_io_ok(self, rid: str, result: object) -> None:
        kind, then = self._io_jobs.pop(rid, ("", None))
        if kind == "list":
            self.entries = list(result)  # type: ignore[arg-type]
            self.library_changed.emit(self.entries)
        elif kind == "save":
            self._on_saved(result, then)  # type: ignore[arg-type]
        elif kind == "load":
            self._on_loaded(result)  # type: ignore[arg-type]

    def _on_io_fail(self, rid: str, error: object) -> None:
        kind, _then = self._io_jobs.pop(rid, ("", None))
        if kind == "list":
            self.status.emit(error_text(error))
            return
        self._set_work(WorkState.IDLE)
        if kind == "save":
            # 예정된 이동·종료(then)는 실행하지 않는다
            self._closing = False
            self.status.emit(error_text(error) + " 다시 저장할 수 있습니다.")
            self._refresh_buttons()
        elif kind == "load":
            self.prompter.show_error("자료를 열 수 없습니다", error_text(error))
            self.status.emit(self._data_message())
            self._after_work()

    # ============================================================ 미저장 보호
    def _protect(self, action: Callable[[], None]) -> None:
        """미저장 결과 → 편집 초안 순서로 확인한 뒤 action 을 실행한다."""
        if self.data == DataState.TEMP_READY:
            ans = self.prompter.ask_unsaved_result(self.input_dirty)
            if ans == "cancel":
                self._closing = False
                return
            if ans == "save":
                self.save(then=lambda: self._protect_draft(action))
                return
        self._protect_draft(action)

    def _protect_draft(self, action: Callable[[], None]) -> None:
        if self.input_dirty:
            ans = self.prompter.ask_unsaved_draft()
            if ans == "cancel":
                self._closing = False
                return
            if ans == "keep":
                try:
                    config.save_draft(self.paths.draft_file, Draft(self.input_text))
                except AppError as exc:
                    self._closing = False
                    self.prompter.show_error("초안 보관 실패", str(exc))
                    return
        action()

    # ============================================================ 설정
    def apply_settings(self, new: Settings) -> bool:
        try:
            new = config.with_root_registered(new.validated())
            if not is_writable_dir(Path(new.storage_root)):
                raise ValueError(f"저장 위치에 쓸 수 없습니다: {new.storage_root}")
            config.save_settings(self.paths.config_file, new)
        except (ValueError, AppError) as exc:
            self.prompter.show_error("설정 저장 실패", str(exc))
            return False
        self.settings = new
        self.settings_changed.emit(new)
        self.refresh_library()
        return True

    # ============================================================ 종료
    def request_close(self) -> None:
        """창 닫기 요청. 정리가 끝나면 close_ready 를 보낸다."""
        self._closing = True
        if self.work != WorkState.IDLE:
            self._model.cancel_event.set()
            self.status.emit("작업을 마무리한 뒤 종료합니다…")
            return
        self._protect(self._finish_close)

    def _finish_close(self) -> None:
        # 사용자가 '버리기' 를 고른 미저장 임시 결과는 지운다
        if self.artifact is not None and not self.artifact.saved and self.data == DataState.TEMP_READY:
            shutil.rmtree(self.artifact.folder, ignore_errors=True)
        self.playback.stop()
        self.close_ready.emit()

    def shutdown(self) -> None:
        self._model.shutdown()
        self._io.shutdown()

    # ============================================================ 내부
    def _after_work(self) -> None:
        self._refresh_buttons()
        if self._closing and self.work == WorkState.IDLE:
            self._model.cancel_event.clear()
            self._protect(self._finish_close)

    def _refresh_buttons(self) -> None:
        self.work_changed.emit(self.work)

    def _set_work(self, work: WorkState) -> None:
        self.work = work
        self.work_changed.emit(work)

    def _apply_artifact(self, artifact: StudyArtifact, data: DataState, keep_position: bool = False) -> None:
        old = self.artifact
        if old is not None and not old.saved and old.artifact_id != artifact.artifact_id:
            # 새 자료로 바뀌면서 버려진 미저장 임시 결과 정리
            shutil.rmtree(old.folder, ignore_errors=True)
        self.artifact = artifact
        self.data = data
        if not keep_position:
            self.playback.set_artifact(artifact)
        else:
            self.playback.replace_folder(artifact)
        self.artifact_changed.emit(artifact)
        self.data_changed.emit(data)
        self.status.emit(self._data_message())

    def _data_message(self) -> str:
        if self.data == DataState.TEMP_READY:
            return "변환 완료: 아직 저장하지 않았습니다."
        if self.data == DataState.SAVED_READY:
            return "저장된 자료입니다."
        return ""

    def style_label(self, style_id: str) -> str:
        return STYLES.get(style_id, style_id)

    def us_voices(self) -> list[VoiceInfo]:
        return [v for v in self.voices if v.locale == "en-US"]

    def settings_with(self, **changes) -> Settings:
        return replace(self.settings, **changes)
