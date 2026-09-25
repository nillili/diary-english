"""문장 단위 재생 관리. QMediaPlayer 는 GUI 스레드에서만 다룬다."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

from ..domain import PlayState, StudyArtifact

log = logging.getLogger(__name__)


def create_media_player(parent: QObject | None = None) -> QMediaPlayer:
    from PySide6.QtMultimedia import QAudioOutput

    player = QMediaPlayer(parent)
    output = QAudioOutput(player)
    player.setAudioOutput(output)
    return player


class PlaybackManager(QObject):
    """재생/일시정지/정지/R/Space 규칙.

    player 는 QMediaPlayer 또는 같은 메서드·신호를 가진 테스트 대역이다.
    """

    state_changed = Signal(object)  # PlayState
    index_changed = Signal(int)
    message = Signal(str)
    error = Signal(str)

    def __init__(self, player, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = player
        self._artifact: StudyArtifact | None = None
        self._index = 0
        self._state = PlayState.STOPPED
        self._expected: QUrl | None = None  # 현재 선택 파일. 다른 소스의 늦은 신호는 무시
        player.playbackStateChanged.connect(self._on_player_state)
        player.mediaStatusChanged.connect(self._on_media_status)
        player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------ 조회
    @property
    def state(self) -> PlayState:
        return self._state

    @property
    def index(self) -> int:
        return self._index

    @property
    def count(self) -> int:
        return len(self._artifact.sentences) if self._artifact else 0

    @property
    def has_material(self) -> bool:
        return self._artifact is not None

    # ------------------------------------------------------------ 자료
    def set_artifact(self, artifact: StudyArtifact | None) -> None:
        """새 자료로 교체하고 첫 문장에서 대기한다. 자동 재생하지 않는다."""
        self.stop()
        self._artifact = artifact
        self._index = 0
        self._expected = None
        self._player.setSource(QUrl())
        self.index_changed.emit(0)

    def replace_folder(self, artifact: StudyArtifact) -> None:
        """같은 자료가 다른 폴더로 옮겨졌을 때(임시 → 저장) 현재 문장 위치는 유지한다."""
        self.stop()
        self._artifact = artifact
        self._expected = None
        self._player.setSource(QUrl())
        self._index = min(self._index, max(len(artifact.sentences) - 1, 0))
        self.index_changed.emit(self._index)

    def artifact_id(self) -> str | None:
        return self._artifact.artifact_id if self._artifact else None

    # ------------------------------------------------------------ 조작
    def toggle(self) -> None:
        if not self._artifact:
            return
        if self._state == PlayState.PLAYING:
            self._player.pause()
            self._set_state(PlayState.PAUSED)
        elif self._state == PlayState.PAUSED:
            self._player.play()
            self._set_state(PlayState.PLAYING)
        else:
            self._play_current_from_start()

    def stop(self) -> None:
        if self._state != PlayState.STOPPED or self._expected is not None:
            self._player.stop()
            self._player.setPosition(0)
        self._set_state(PlayState.STOPPED)

    def repeat(self) -> None:
        if self._artifact:
            self._play_current_from_start()

    def next(self) -> None:
        if not self._artifact:
            return
        if self._index + 1 >= self.count:
            self.message.emit("마지막 문장입니다.")
            return  # 재생 중인 문장은 그대로 둔다
        self._player.stop()
        self._index += 1
        self.index_changed.emit(self._index)
        self._play_current_from_start()

    # ------------------------------------------------------------ 내부
    def _current_url(self) -> QUrl:
        assert self._artifact is not None
        return QUrl.fromLocalFile(str(Path(self._artifact.sentence_file(self._index))))

    def _play_current_from_start(self) -> None:
        url = self._current_url()
        if self._expected != url or self._player.source() != url:
            self._player.stop()
            self._expected = url
            self._player.setSource(url)
        else:
            self._player.stop()
        self._player.setPosition(0)
        self._player.play()
        self._set_state(PlayState.PLAYING)

    def _set_state(self, state: PlayState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _is_current_source(self) -> bool:
        return self._expected is not None and self._player.source() == self._expected

    def _on_player_state(self, qstate) -> None:
        if not self._is_current_source():
            return
        if qstate == QMediaPlayer.PlaybackState.StoppedState and self._state != PlayState.STOPPED:
            self._set_state(PlayState.STOPPED)

    def _on_media_status(self, status) -> None:
        if not self._is_current_source():
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # 자연 종료: 같은 문장에 머물고 대기
            self._set_state(PlayState.STOPPED)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._fail("음성 파일을 재생할 수 없습니다.")

    def _on_error(self, err, text: str = "") -> None:
        if err == QMediaPlayer.Error.NoError or not self._is_current_source():
            return
        self._fail(f"재생 오류: {text or err}")

    def _fail(self, msg: str) -> None:
        log.warning(msg)
        self._set_state(PlayState.STOPPED)
        self.error.emit(msg)
