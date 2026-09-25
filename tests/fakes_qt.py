"""QMediaPlayer 테스트 대역."""

from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

PS = QMediaPlayer.PlaybackState
MS = QMediaPlayer.MediaStatus


class FakePlayer(QObject):
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()
        self._source = QUrl()
        self._state = PS.StoppedState
        self.position = 0
        self.log: list[tuple] = []

    def source(self) -> QUrl:
        return self._source

    def setSource(self, url: QUrl) -> None:
        self._source = QUrl(url)
        self.position = 0
        self.log.append(("source", url.fileName()))

    def play(self) -> None:
        self.log.append(("play", self._source.fileName(), self.position))
        self._set(PS.PlayingState)

    def pause(self) -> None:
        self.log.append(("pause", self.position))
        self._set(PS.PausedState)

    def stop(self) -> None:
        self.position = 0
        self._set(PS.StoppedState)

    def setPosition(self, ms: int) -> None:
        self.position = ms

    def playbackState(self):
        return self._state

    def _set(self, st) -> None:
        if st != self._state:
            self._state = st
            self.playbackStateChanged.emit(st)

    # 테스트 조작
    def finish(self) -> None:
        self._state = PS.StoppedState
        self.mediaStatusChanged.emit(MS.EndOfMedia)
        self.playbackStateChanged.emit(PS.StoppedState)

    def last_played(self) -> str | None:
        plays = [e for e in self.log if e[0] == "play"]
        return plays[-1][1] if plays else None
