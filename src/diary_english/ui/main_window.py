"""세 탭 조립과 확인 창."""

from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget, QWidget

from ..controller import AppController
from .library_tab import LibraryTab
from .settings_tab import SettingsTab
from .study_tab import StudyTab

APP_TITLE = "나의 일기 영어회화"


class QtPrompter:
    """controller 가 사용자에게 묻는 확인 창."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self.parent = parent

    def _choose(self, title: str, text: str, buttons: list[tuple[str, str, QMessageBox.ButtonRole]]) -> str:
        box = QMessageBox(self.parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        mapping = {}
        for label, value, role in buttons:
            mapping[box.addButton(label, role)] = value
        box.exec()
        return mapping.get(box.clickedButton(), "cancel")

    def ask_unsaved_result(self, draft_differs: bool) -> str:
        text = "변환한 결과를 아직 저장하지 않았습니다."
        if draft_differs:
            text += "\n저장되는 것은 변환할 때의 일기와 음성이며, 지금 입력창의 수정 내용과 다릅니다."
        R = QMessageBox.ButtonRole
        return self._choose(
            "저장하지 않은 결과",
            text,
            [("저장 후 계속", "save", R.AcceptRole), ("결과 버리고 계속", "discard", R.DestructiveRole),
             ("취소", "cancel", R.RejectRole)],
        )

    def ask_unsaved_draft(self) -> str:
        R = QMessageBox.ButtonRole
        return self._choose(
            "변환하지 않은 일기",
            "입력창에 변환하지 않은 일기가 있습니다.",
            [("초안 보관 후 계속", "keep", R.AcceptRole), ("초안 버리기", "discard", R.DestructiveRole),
             ("취소", "cancel", R.RejectRole)],
        )

    def ask_recover(self, kind: str, summary: str) -> str:
        what = "저장하지 않은 변환 결과" if kind == "result" else "보관해 둔 일기 초안"
        R = QMessageBox.ButtonRole
        return self._choose(
            "이전 작업 복구",
            f"{what}가 남아 있습니다.\n{summary}",
            [("복구", "restore", R.AcceptRole), ("삭제", "discard", R.DestructiveRole),
             ("나중에", "later", R.RejectRole)],
        )

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self.parent, title, message)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, title_suffix: str = "") -> None:
        super().__init__()
        self.c = controller
        self._close_ok = False
        self.setWindowTitle(APP_TITLE + title_suffix)
        self.resize(820, 760)

        self.tabs = QTabWidget()
        self.study_tab = StudyTab(controller)
        self.library_tab = LibraryTab(controller)
        self.settings_tab = SettingsTab(controller)
        self.tabs.addTab(self.study_tab, "오늘의 학습")
        self.tabs.addTab(self.library_tab, "학습 보관함")
        self.tabs.addTab(self.settings_tab, "환경설정")
        self.setCentralWidget(self.tabs)

        controller.show_study.connect(self._show_study)
        controller.close_ready.connect(self._on_close_ready)

    def _show_study(self) -> None:
        self.tabs.setCurrentWidget(self.study_tab)

    def _on_close_ready(self) -> None:
        self._close_ok = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._close_ok:
            event.accept()
            return
        event.ignore()
        self.c.request_close()
