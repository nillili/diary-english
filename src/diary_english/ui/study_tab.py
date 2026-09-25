"""오늘의 학습 탭: 입력 · 변환 · 번역 · 재생 · 저장."""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..controller import AppController
from ..domain import GENDER_LABELS, DataState, PlayState, StudyArtifact, WorkState, sentence_display_number


class PlayArea(QFrame):
    """R / Space 단축키가 동작하는 재생 영역.

    초점이 이 영역(또는 안쪽 위젯)에 있을 때만 동작한다. 입력창·검색창·다른 탭에서는 글자로 입력된다.
    단축키가 키를 먼저 소비하므로 버튼의 Space 클릭과 겹치지 않는다. 자동 반복은 끈다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.repeat_shortcut = self._shortcut(Qt.Key.Key_R)
        self.next_shortcut = self._shortcut(Qt.Key.Key_Space)

    def _shortcut(self, key: Qt.Key) -> QShortcut:
        sc = QShortcut(QKeySequence(key), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.setAutoRepeat(False)
        return sc


def examples_html(examples) -> str:
    """번역 아래(같은 칸)에 표현 예시를 붙인다. 음성으로 만들지 않는다."""
    if not examples:
        return ""
    items = "".join(f"<p style='color:gray'>&nbsp;&nbsp;{html.escape(e)}</p>" for e in examples)
    return "<hr><p style='color:gray'><small>표현 예시</small></p>" + items


class StudyTab(QWidget):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller

        self.date_label = QLabel(f"날짜: {date.today():%Y-%m-%d}")
        head = QHBoxLayout()
        head.addWidget(QLabel("오늘의 일기"))
        head.addStretch(1)
        head.addWidget(self.date_label)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName("diaryInput")
        self.input_edit.setPlaceholderText("오늘의 일기를 2~3줄 적어 보세요.")
        # 4줄 정도 보이는 높이
        line = self.input_edit.fontMetrics().lineSpacing()
        self.input_edit.setFixedHeight(line * 4 + 2 * self.input_edit.frameWidth() + 12)
        self.convert_button = QPushButton("변환")
        self.convert_button.setObjectName("convertButton")
        self.option_label = QLabel()

        self.translation_view = QTextEdit()
        self.translation_view.setObjectName("translationView")
        self.translation_view.setReadOnly(True)

        self.sentence_label = QLabel("현재 문장: - / -")
        self.state_label = QLabel("")
        info_row = QHBoxLayout()
        info_row.addWidget(self.sentence_label)
        info_row.addStretch(1)
        info_row.addWidget(self.state_label)

        self.play_button = QPushButton("재생")
        self.play_button.setObjectName("playButton")
        self.stop_button = QPushButton("정지")
        self.stop_button.setObjectName("stopButton")
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.play_button)
        btn_row.addWidget(self.stop_button)
        btn_row.addWidget(QLabel("R: 현재 문장 다시 듣기     Space: 다음 문장"))
        btn_row.addStretch(1)

        self.play_area = PlayArea()
        self.play_area.setObjectName("playArea")
        pa = QVBoxLayout(self.play_area)
        pa.setContentsMargins(0, 0, 0, 0)
        pa.addWidget(QLabel("영어 번역"))
        pa.addWidget(self.translation_view, 1)
        pa.addLayout(info_row)
        pa.addLayout(btn_row)
        self.play_area.repeat_shortcut.activated.connect(self._repeat)
        self.play_area.next_shortcut.activated.connect(self._next)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.save_button = QPushButton("저장")
        self.save_button.setObjectName("saveButton")
        bottom = QFrame()
        bottom.setFrameShape(QFrame.Shape.StyledPanel)
        save_row = QHBoxLayout(bottom)
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.status_label, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self.input_edit)
        conv_row = QHBoxLayout()
        conv_row.addWidget(self.convert_button)
        conv_row.addWidget(self.option_label)
        conv_row.addStretch(1)
        layout.addLayout(conv_row)
        layout.addWidget(self.play_area, 3)
        layout.addWidget(bottom)

        # 연결
        self.input_edit.textChanged.connect(self._on_text_changed)
        self.convert_button.clicked.connect(self.c.convert)
        self.play_button.clicked.connect(self._toggle)
        self.stop_button.clicked.connect(self._stop)
        self.save_button.clicked.connect(self._save)

        c = self.c
        c.work_changed.connect(self.refresh)
        c.data_changed.connect(self.refresh)
        c.artifact_changed.connect(self._on_artifact)
        c.translation_preview.connect(self._show_preview)
        c.input_replaced.connect(self._on_input_replaced)
        c.status.connect(self.status_label.setText)
        c.settings_changed.connect(self._update_option_label)
        c.voices_changed.connect(self._update_option_label)
        c.playback.state_changed.connect(self.refresh)
        c.playback.index_changed.connect(self._render_translation)
        c.playback.message.connect(self.status_label.setText)
        c.playback.error.connect(self.status_label.setText)

        self._update_option_label()
        self.refresh()

    # ------------------------------------------------------------ 이벤트
    def _on_text_changed(self) -> None:
        self.c.set_input_text(self.input_edit.toPlainText())
        self.refresh()

    def _on_input_replaced(self, text: str) -> None:
        if self.input_edit.toPlainText() != text:
            self.input_edit.blockSignals(True)
            self.input_edit.setPlainText(text)
            self.input_edit.blockSignals(False)
        self.c.set_input_text(text)
        self.refresh()

    def _on_artifact(self, artifact: StudyArtifact | None) -> None:
        self._render_translation()
        self.refresh()
        if artifact is not None:
            self.play_area.setFocus()

    def _save(self) -> None:
        self.c.save()

    def _show_preview(self, result) -> None:
        """MP3 생성 전에 번역 결과만 먼저 보여 준다."""
        rows = [f"<p>&nbsp;&nbsp;{html.escape(text)}</p>" for text in result.sentences]
        self.translation_view.setHtml("".join(rows) + examples_html(result.examples))
        self.sentence_label.setText(f"현재 문장: - / {len(result.sentences)}")

    def _toggle(self) -> None:
        if self.c.can_play:
            self.c.playback.toggle()

    def _stop(self) -> None:
        if self.c.can_play:
            self.c.playback.stop()

    def _repeat(self) -> None:
        if self.c.can_play:
            self.c.playback.repeat()

    def _next(self) -> None:
        if self.c.can_play:
            self.c.playback.next()

    # ------------------------------------------------------------ 표시
    def _update_option_label(self, *_args) -> None:
        s = self.c.settings
        voice = next((v for v in self.c.voices if v.id == s.voice_id), None)
        gender = GENDER_LABELS[voice.gender] if voice else "음성 확인 중"
        self.option_label.setText(f"미국식 영어 / {self.c.style_label(s.style_id)} / {gender}")

    def _render_translation(self, *_args) -> None:
        a = self.c.artifact
        pb = self.c.playback
        if a is None:
            self.translation_view.clear()
            self.sentence_label.setText("현재 문장: - / -")
            return
        rows = []
        for s in a.sentences:
            text = html.escape(s.text)
            if s.index == pb.index:
                rows.append(f"<p><b>&gt; {text}</b></p>")
            else:
                rows.append(f"<p>&nbsp;&nbsp;{text}</p>")
        self.translation_view.setHtml("".join(rows) + examples_html(a.examples))
        self.sentence_label.setText(f"현재 문장: {sentence_display_number(pb.index)} / {len(a.sentences)}")

    def refresh(self, *_args) -> None:
        c = self.c
        busy = c.work != WorkState.IDLE
        self.input_edit.setReadOnly(c.work == WorkState.CONVERTING)
        self.convert_button.setEnabled(c.can_convert)
        self.play_button.setEnabled(c.can_play)
        self.stop_button.setEnabled(c.can_play)
        self.save_button.setEnabled(c.can_save)
        self.play_button.setText("일시정지" if c.playback.state == PlayState.PLAYING else "재생")
        if busy:
            self.state_label.setText("상태: 작업 중")
        elif c.data == DataState.NONE:
            self.state_label.setText("")
        else:
            self.state_label.setText(
                {
                    PlayState.PLAYING: "상태: 재생 중",
                    PlayState.PAUSED: "상태: 일시정지",
                    PlayState.STOPPED: "상태: 재생 대기",
                }[c.playback.state]
            )
