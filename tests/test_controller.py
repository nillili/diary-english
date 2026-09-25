from __future__ import annotations

from pathlib import Path

import pytest

from diary_english import config
from diary_english.controller import INPUT_CHANGED_MSG, AppController
from diary_english.domain import DataState, Settings, TranslationError, WorkState
from diary_english.paths import AppPaths
from diary_english.services.fakes import FakeSpeech, FakeTranslator
from diary_english.services.playback import PlaybackManager
from diary_english.services.repository import FileStudyRepository
from diary_english.workers import SyncRunner

from .fakes_qt import FakePlayer


class ScriptedPrompter:
    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {"result": [], "draft": [], "recover": []}
        self.asked: list[str] = []
        self.errors: list[str] = []

    def ask_unsaved_result(self, draft_differs: bool) -> str:
        self.asked.append(f"result:{draft_differs}")
        return self.answers["result"].pop(0)

    def ask_unsaved_draft(self) -> str:
        self.asked.append("draft")
        return self.answers["draft"].pop(0)

    def ask_recover(self, kind: str, summary: str) -> str:
        self.asked.append(f"recover:{kind}")
        return self.answers["recover"].pop(0)

    def show_error(self, title: str, message: str) -> None:
        self.errors.append(message)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.paths = AppPaths(tmp_path / "appdata")
        self.paths.ensure()
        self.root = tmp_path / "lib"
        self.translator = FakeTranslator()
        self.speech = FakeSpeech()
        self.player = FakePlayer()
        self.prompter = ScriptedPrompter()
        self.repo = FileStudyRepository()
        self.c = AppController(
            paths=self.paths,
            settings=config.with_root_registered(Settings(storage_root=str(self.root))),
            translator_factory=lambda s: self.translator,
            synthesizer=self.speech,
            repository=self.repo,
            playback=PlaybackManager(self.player),
            model_runner=SyncRunner(),
            io_runner=SyncRunner(),
            prompter=self.prompter,
        )
        self.closed = []
        self.c.close_ready.connect(lambda: self.closed.append(True))

    def saved_count(self) -> int:
        return len(self.repo.list_entries([self.root]))

    def convert(self, text: str) -> None:
        self.c.set_input_text(text)
        self.c.convert()


@pytest.fixture
def env(qapp, tmp_path):
    e = Env(tmp_path)
    e.c.start()
    return e


def test_typing_does_not_translate(env):  # A01
    env.c.set_input_text("오늘은 산책했다.")
    assert env.translator.calls == 0 and env.speech.calls == 0


def test_convert_then_preview_without_saving(env):  # A02
    env.convert("오늘은 산책했다.\n좋았다.")
    c = env.c
    assert c.data == DataState.TEMP_READY and c.work == WorkState.IDLE
    assert len(c.artifact.sentences) == 2 and c.can_play and c.can_save
    assert env.saved_count() == 0
    assert c.artifact.folder.is_relative_to(env.paths.temp_dir)


def test_save_once_and_twice(env):  # A03, A04
    env.convert("오늘은 산책했다.")
    temp_folder = env.c.artifact.folder
    env.c.save()
    env.c.save()
    assert env.c.data == DataState.SAVED_READY and not env.c.can_save
    assert env.saved_count() == 1
    assert not temp_folder.exists()  # 저장 후 임시 결과 정리
    assert env.c.artifact.folder.parent == env.root


def test_save_uses_converted_original_not_edited_text(env):  # A05
    env.convert("원래 일기")
    env.c.set_input_text("고친 일기")
    env.c.save()
    assert env.c.artifact.original == "원래 일기"
    assert env.c.input_text == "고친 일기"  # 새 초안은 입력창에 유지


def test_input_changed_message(env):
    msgs = []
    env.c.status.connect(msgs.append)
    env.convert("원래 일기")
    env.c.set_input_text("원래 일기 수정")
    assert msgs[-1] == INPUT_CHANGED_MSG


def test_conversion_failure_keeps_previous(env):
    env.convert("첫 일기")
    first = env.c.artifact
    env.translator.fail_next = TranslationError("서버 없음")
    env.prompter.answers["result"] = ["discard"]
    env.c.set_input_text("둘째 일기")
    env.c.convert()
    assert env.c.artifact is first and env.c.data == DataState.TEMP_READY
    assert first.folder.exists()
    assert env.c.input_text == "둘째 일기"


def test_partial_speech_failure_not_applied(env):  # A15
    env.speech.fail_at = 2
    env.convert("하나\n둘\n셋")
    assert env.c.data == DataState.NONE and env.c.artifact is None
    assert list(env.paths.temp_dir.iterdir()) == []


def test_open_entry_moves_to_study_and_waits(env):  # A11
    env.convert("보관할 일기")
    env.c.save()
    shown = []
    env.c.show_study.connect(lambda: shown.append(True))
    env.c.set_input_text("")
    env.c.open_entry(env.c.entries[0])
    assert shown and env.c.data == DataState.SAVED_READY
    assert env.c.input_text == "보관할 일기"
    assert env.c.playback.index == 0 and env.player.last_played() is None
    assert env.translator.calls == 1  # 불러오기에서 번역 호출 없음


def test_open_entry_with_unsaved_result_cancel(env):  # A20
    env.convert("저장한 일기")
    env.c.save()
    entry = env.c.entries[0]
    env.convert("새 일기")
    current = env.c.artifact
    env.prompter.answers["result"] = ["cancel"]
    env.c.open_entry(entry)
    assert env.c.artifact is current and env.c.data == DataState.TEMP_READY


def test_open_entry_with_unsaved_result_save_then_continue(env):
    env.convert("저장한 일기")
    env.c.save()
    entry = env.c.entries[-1]
    env.convert("새 일기")
    env.prompter.answers["result"] = ["save"]
    env.c.open_entry(entry)
    assert env.saved_count() == 2
    assert env.c.artifact.original == "저장한 일기"


def test_save_failure_cancels_continuation(env, monkeypatch):
    env.convert("저장한 일기")
    env.c.save()
    entry = env.c.entries[0]
    env.convert("새 일기")
    current = env.c.artifact

    def boom(*a, **k):
        from diary_english.domain import StorageError

        raise StorageError("디스크 오류")

    monkeypatch.setattr(env.repo, "save", boom)
    env.prompter.answers["result"] = ["save"]
    env.c.open_entry(entry)
    assert env.c.artifact is current and env.c.can_save  # 이동하지 않고 재시도 가능


def test_open_entry_discard_result_and_keep_draft(env):
    env.convert("저장한 일기")
    env.c.save()
    entry = env.c.entries[0]
    env.convert("새 일기")
    temp_folder = env.c.artifact.folder
    env.c.set_input_text("새 일기 다시 고침")
    env.prompter.answers["result"] = ["discard"]
    env.prompter.answers["draft"] = ["keep"]
    env.c.open_entry(entry)
    assert env.prompter.asked == ["result:True", "draft"]
    assert config.load_draft(env.paths.draft_file).text == "새 일기 다시 고침"
    assert not temp_folder.exists()
    assert env.c.data == DataState.SAVED_READY


def test_plain_open_needs_no_prompt(env):
    env.convert("저장한 일기")
    env.c.save()
    env.c.open_entry(env.c.entries[0])
    assert env.prompter.asked == []


def test_damaged_entry_shows_error_and_keeps_current(env):  # A17
    env.convert("저장한 일기")
    env.c.save()
    (env.c.artifact.folder / "full.mp3").unlink()
    env.c.refresh_library()
    current = env.c.artifact
    env.c.open_entry(env.c.entries[0])
    assert env.prompter.errors and env.c.artifact is current


def test_close_with_unsaved_result_discard(env):
    env.convert("임시 일기")
    folder = env.c.artifact.folder
    env.prompter.answers["result"] = ["discard"]
    env.c.request_close()
    assert env.closed and not folder.exists()


def test_close_cancel(env):
    env.convert("임시 일기")
    env.prompter.answers["result"] = ["cancel"]
    env.c.request_close()
    assert not env.closed and env.c.artifact.folder.exists()


def test_recover_temp_and_draft_on_start(qapp, tmp_path):
    e1 = Env(tmp_path)
    e1.c.start()
    e1.convert("복구할 일기")
    config.save_draft(e1.paths.draft_file, config.Draft("초안 복구"))
    # 새 실행
    e2 = Env(tmp_path)
    e2.prompter.answers["recover"] = ["restore", "restore"]
    e2.c.start()
    assert e2.prompter.asked == ["recover:result", "recover:draft"]
    assert e2.c.data == DataState.TEMP_READY and e2.c.artifact.original == "복구할 일기"
    assert e2.c.input_text == "초안 복구"
    assert not e2.paths.draft_file.exists()


def test_recover_later_keeps_files(qapp, tmp_path):
    e1 = Env(tmp_path)
    e1.c.start()
    e1.convert("남길 일기")
    e2 = Env(tmp_path)
    e2.prompter.answers["recover"] = ["later"]
    e2.c.start()
    assert e2.c.data == DataState.NONE
    assert len(list(e2.paths.temp_dir.iterdir())) == 1


def test_settings_apply_to_next_conversion_and_keep_roots(env, tmp_path):
    env.convert("첫 일기")
    env.c.save()
    new_root = tmp_path / "새 위치"
    assert env.c.apply_settings(env.c.settings_with(style_id="casual", storage_root=str(new_root)))
    env.convert("둘째 일기")
    assert env.c.artifact.style_id == "casual"
    env.c.save()
    assert {e.folder.parent for e in env.c.entries} == {env.root, new_root}  # A19


def test_offline_reopen_without_services(qapp, tmp_path):  # A13
    e1 = Env(tmp_path)
    e1.c.start()
    e1.convert("오프라인 일기")
    e1.c.save()
    e2 = Env(tmp_path)
    e2.translator.fail_next = AssertionError("호출되면 안 됨")
    e2.c.start()
    e2.c.open_entry(e2.c.entries[0])
    assert e2.c.data == DataState.SAVED_READY and e2.translator.calls == 0


def test_translation_shown_before_mp3(env):
    events = []
    env.c.translation_preview.connect(lambda r: events.append(("preview", r.sentences, env.speech.calls, env.c.work)))
    env.c.status.connect(lambda m: events.append(("status", m)))
    env.c.artifact_changed.connect(lambda a: events.append(("artifact",)))
    env.convert("하나\n둘")
    preview = next(e for e in events if e[0] == "preview")
    assert preview[1] == ("[DEV] Sentence 1 (middle_age_daily).", "[DEV] Sentence 2 (middle_age_daily).")
    assert preview[2] == 0 and preview[3] == WorkState.CONVERTING  # MP3 만들기 전, 아직 변환 중
    i = events.index(preview)
    assert ("status", "MP3 생성 중: 0/2") in events[i:]
    assert ("status", "MP3 생성 중: 2/2") in events[i:]
    assert events.index(("artifact",)) > i


def test_mp3_failure_after_preview_restores_previous(env):
    env.convert("첫 일기")
    first = env.c.artifact
    shown = []
    env.c.artifact_changed.connect(shown.append)
    env.speech.fail_at = env.speech.calls + 1
    env.prompter.answers["result"] = ["discard"]
    env.convert("둘째 일기")
    assert env.c.artifact is first and shown[-1] is first


def test_examples_saved_but_not_voiced(env):
    env.convert("하나\n둘")
    assert env.speech.calls == 2  # 번역 문장 2개만 음성 생성, 예시는 제외
    assert env.c.artifact.examples == ("[DEV] Example expression.",)
    assert not any(p.name.startswith("003") for p in (env.c.artifact.folder / "sentences").iterdir())
    env.c.save()
    assert env.repo.load(env.c.artifact.folder).examples == ("[DEV] Example expression.",)
