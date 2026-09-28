from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl

from diary_english.domain import PlayState
from diary_english.services.playback import PlaybackManager

from .conftest import make_temp_artifact
from .fakes_qt import MS, FakePlayer


@pytest.fixture
def pb(qapp, tmp_path):
    player = FakePlayer()
    mgr = PlaybackManager(player)
    art = make_temp_artifact(tmp_path / "a", sentences=("One.", "Two.", "Three."))
    mgr.set_artifact(art)
    return mgr, player


def test_starts_waiting_at_first_sentence(pb):
    mgr, player = pb
    assert mgr.index == 0 and mgr.state == PlayState.STOPPED
    assert player.last_played() is None  # 자동 재생 없음


def test_pause_resume_keeps_position(pb):  # A06
    mgr, player = pb
    mgr.next()  # 두 번째 문장
    player.position = 700
    mgr.toggle()
    assert mgr.state == PlayState.PAUSED and player.position == 700
    mgr.toggle()
    assert mgr.state == PlayState.PLAYING
    assert player.log[-1] == ("play", "002.mp3", 700)


def test_stop_then_play_restarts(pb):  # A07
    mgr, player = pb
    mgr.next()
    player.position = 500
    mgr.stop()
    assert mgr.state == PlayState.STOPPED and mgr.index == 1
    mgr.toggle()
    assert player.log[-1] == ("play", "002.mp3", 0)


def test_natural_end_waits(pb):  # A08
    mgr, player = pb
    mgr.toggle()
    player.finish()
    assert mgr.state == PlayState.STOPPED and mgr.index == 0
    assert player.last_played() == "001.mp3"


def test_repeat_next_and_last(pb):  # A09
    mgr, player = pb
    mgr.toggle()
    player.position = 400
    mgr.repeat()
    assert player.log[-1] == ("play", "001.mp3", 0)
    mgr.next()
    assert mgr.index == 1 and player.last_played() == "002.mp3"
    mgr.next()
    assert mgr.index == 2 and player.last_played() == "003.mp3"
    mgr.next()  # 마지막 다음은 첫 문장
    assert mgr.index == 0 and player.last_played() == "001.mp3"
    assert mgr.state == PlayState.PLAYING


def test_rapid_next_plays_only_final_choice(pb):
    mgr, player = pb
    mgr.next()
    mgr.next()
    assert player.source().fileName() == "003.mp3"
    assert player.last_played() == "003.mp3"


def test_stale_signal_from_old_source_ignored(pb):
    mgr, player = pb
    mgr.toggle()
    old = player.source()
    mgr.next()
    # 이전 파일의 늦은 종료 신호를 흉내: 소스가 다를 때는 무시되어야 한다
    player._source = old
    player.mediaStatusChanged.emit(MS.EndOfMedia)
    player._source = QUrl.fromLocalFile(str(mgr._artifact.sentence_file(1)))
    assert mgr.state == PlayState.PLAYING and mgr.index == 1


def test_invalid_media_reports_error(pb):
    mgr, player = pb
    errors = []
    mgr.error.connect(errors.append)
    mgr.toggle()
    player.mediaStatusChanged.emit(MS.InvalidMedia)
    assert mgr.state == PlayState.STOPPED and errors
