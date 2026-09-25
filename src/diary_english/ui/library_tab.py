"""학습 보관함 탭: 목록 클릭 → 오늘의 학습 탭."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..controller import AppController
from ..domain import GENDER_LABELS, LibraryEntry
from ..services.repository import search_entries


class LibraryTab(QWidget):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller
        self._shown: list[LibraryEntry] = []
        self._selected: LibraryEntry | None = None

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("날짜 또는 일기 내용")
        self.search_button = QPushButton("찾기")
        row = QHBoxLayout()
        row.addWidget(QLabel("검색:"))
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.search_button)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("libraryTable")
        self.table.setHorizontalHeaderLabels(["날짜", "제목", "문장 수", "회화 스타일"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self.selected_label = QLabel("선택한 자료: -")
        self.detail_label = QLabel("")
        self.open_folder_button = QPushButton("저장 폴더 열기")
        self.open_folder_button.setObjectName("openFolderButton")
        self.open_folder_button.setEnabled(False)

        bottom = QFrame()
        bottom.setFrameShape(QFrame.Shape.StyledPanel)
        bl = QVBoxLayout(bottom)
        bl.addWidget(QLabel("저장된 음성으로 학습합니다. 인터넷 연결 없이 이용할 수 있습니다."))

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.selected_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(QLabel("목록을 클릭하면 오늘의 학습 탭으로 바로 이동합니다."))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.open_folder_button)
        folder_row.addStretch(1)
        layout.addLayout(folder_row)
        layout.addWidget(bottom)

        self.search_button.clicked.connect(self.apply_filter)
        self.search_edit.returnPressed.connect(self.apply_filter)
        # 실제 사용자 클릭 신호만 연결. 목록 갱신·자동 선택으로는 자료를 열지 않는다.
        self.table.cellClicked.connect(self._on_clicked)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.c.library_changed.connect(self.apply_filter)

    def apply_filter(self, *_args) -> None:
        self._shown = search_entries(self.c.entries, self.search_edit.text())
        self.table.setRowCount(len(self._shown))
        for r, e in enumerate(self._shown):
            title = e.title if e.ok else f"(손상) {e.title}"
            cells = [
                e.date_text,
                title,
                str(e.sentence_count) if e.ok else "-",
                self.c.style_label(e.style_id) if e.ok else "-",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if not e.ok:
                    item.setToolTip(e.error)
                self.table.setItem(r, col, item)
        # 이전 선택이 목록에서 사라졌으면 선택 해제
        if self._selected is not None and self._selected not in self._shown:
            self._set_selected(None)

    def _on_clicked(self, row: int, _col: int) -> None:
        if not (0 <= row < len(self._shown)):
            return
        entry = self._shown[row]
        self._set_selected(entry)
        self.c.open_entry(entry)

    def _set_selected(self, entry: LibraryEntry | None) -> None:
        self._selected = entry
        self.open_folder_button.setEnabled(entry is not None)
        if entry is None:
            self.selected_label.setText("선택한 자료: -")
            self.detail_label.setText("")
        elif entry.ok:
            self.selected_label.setText(f"선택한 자료: {entry.title}")
            self.detail_label.setText(
                f"미국식 영어 / {GENDER_LABELS[entry.voice_gender]} 음성 / "
                f"문장별 MP3 {entry.sentence_count}개 / 전체 MP3 1개"
            )
        else:
            self.selected_label.setText(f"선택한 자료: {entry.folder.name}")
            self.detail_label.setText(f"문제: {entry.error}")

    def _open_folder(self) -> None:
        if self._selected is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._selected.folder)))
