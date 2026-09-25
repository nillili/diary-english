from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from diary_english.domain import DataState, PlayState
from diary_english.ui.main_window import MainWindow

from .test_controller import Env


@pytest.fixture
def ui(qtbot, tmp_path):
    env = Env(tmp_path)
    win = MainWindow(env.c)
    # 테스트 정리 시 미저장 확인 창을 띄우지 않음
    qtbot.addWidget(win, before_close_func=lambda w: setattr(w, "_close_ok", True))
    win.show()
    env.c.start()
    return env, win


def buttons(widget) -> list[str]:
    return sorted(b.text() for b in widget.findChildren(QPushButton))


def test_study_tab_layout(ui):  # R02, R06, R08
    env, win = ui
    st = win.study_tab
    assert st.translation_view.isReadOnly() and not st.input_edit.isReadOnly()
    assert buttons(st) == ["변환", "재생", "저장", "정지"]
    assert not st.play_button.isEnabled() and not st.save_button.isEnabled()
    assert not st.convert_button.isEnabled()


def test_library_and_settings_buttons(ui):  # R09
    env, win = ui
    assert buttons(win.library_tab) == ["저장 폴더 열기", "찾기"]
    assert not win.library_tab.open_folder_button.isEnabled()
    assert buttons(win.settings_tab) == ["설정 저장", "취소", "폴더 선택"]


def test_typing_only_enables_convert(ui, qtbot):  # R03, A01
    env, win = ui
    st = win.study_tab
    qtbot.keyClicks(st.input_edit, "walk today")  # QTest 는 ASCII 키만 지원
    assert env.translator.calls == 0
    assert st.convert_button.isEnabled()
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)
    assert env.translator.calls == 1
    assert st.play_button.isEnabled() and st.save_button.isEnabled()
    assert "[DEV] Sentence 1" in st.translation_view.toPlainText()
    assert st.status_label.text() == "변환 완료: 아직 저장하지 않았습니다."


def test_save_button_then_disabled(ui, qtbot):
    env, win = ui
    st = win.study_tab
    st.input_edit.setPlainText("저장할 일기")
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)
    assert env.saved_count() == 0
    qtbot.mouseClick(st.save_button, Qt.MouseButton.LeftButton)
    assert env.saved_count() == 1 and not st.save_button.isEnabled()
    assert win.library_tab.table.rowCount() == 1


def test_library_single_click_opens_study(ui, qtbot):  # A11, A12
    env, win = ui
    st = win.study_tab
    st.input_edit.setPlainText("보관함 일기")
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(st.save_button, Qt.MouseButton.LeftButton)
    st.input_edit.clear()
    win.tabs.setCurrentWidget(win.library_tab)
    env.c.refresh_library()  # 갱신만으로는 열리지 않음
    assert win.tabs.currentWidget() is win.library_tab
    table = win.library_tab.table
    rect = table.visualRect(table.model().index(0, 1))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert win.tabs.currentWidget() is win.study_tab
    assert st.input_edit.toPlainText() == "보관함 일기"
    assert env.c.data == DataState.SAVED_READY and env.player.last_played() is None
    assert win.library_tab.open_folder_button.isEnabled()


def test_shortcuts_only_in_play_area(ui, qtbot):  # A09, A10
    env, win = ui
    st = win.study_tab
    st.input_edit.setPlainText("하나\n둘\n셋")
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)

    # 입력창에서는 글자 입력
    win.activateWindow()
    qtbot.waitUntil(lambda: win.isActiveWindow())
    st.input_edit.setFocus()
    qtbot.keyClick(st.input_edit, Qt.Key.Key_R)
    qtbot.keyClick(st.input_edit, Qt.Key.Key_Space)
    assert st.input_edit.toPlainText() == "r 하나\n둘\n셋"  # 글자로 입력됨
    assert env.player.last_played() is None

    # 재생 영역: Space → 다음 문장, R → 현재 문장 반복 (단축키는 활성 창·초점이 필요)
    win.activateWindow()
    qtbot.waitUntil(lambda: win.isActiveWindow())
    st.play_area.setFocus()
    qtbot.keyClick(st.play_area, Qt.Key.Key_Space)
    assert env.c.playback.index == 1 and env.player.last_played() == "002.mp3"
    qtbot.keyClick(st.play_area, Qt.Key.Key_R)
    assert env.player.log[-1] == ("play", "002.mp3", 0)

    # 재생 버튼에 초점이 있을 때 Space: 버튼 클릭 없이 다음 문장만
    st.play_button.setFocus()
    state_before = env.c.playback.state
    qtbot.keyClick(st.play_button, Qt.Key.Key_Space)
    assert env.c.playback.index == 2 and env.c.playback.state == state_before == PlayState.PLAYING

    # 검색창에서는 글자 입력
    win.tabs.setCurrentWidget(win.library_tab)
    qtbot.keyClicks(win.library_tab.search_edit, "r ")
    assert win.library_tab.search_edit.text() == "r "
    assert env.c.playback.index == 2


def test_play_button_label_toggles(ui, qtbot):  # R06
    env, win = ui
    st = win.study_tab
    st.input_edit.setPlainText("하나")
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(st.play_button, Qt.MouseButton.LeftButton)
    assert st.play_button.text() == "일시정지"
    qtbot.mouseClick(st.play_button, Qt.MouseButton.LeftButton)
    assert st.play_button.text() == "재생"
    qtbot.mouseClick(st.play_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(st.stop_button, Qt.MouseButton.LeftButton)
    assert st.play_button.text() == "재생" and env.c.playback.state == PlayState.STOPPED


def test_settings_cancel_and_save(ui, qtbot, tmp_path):
    env, win = ui
    tab = win.settings_tab
    assert tab.style_combo.currentData() == "middle_age_daily"  # R05 기본값
    tab.style_combo.setCurrentIndex(tab.style_combo.findData("casual"))
    qtbot.mouseClick(tab.cancel_button, Qt.MouseButton.LeftButton)
    assert tab.style_combo.currentData() == "middle_age_daily" and env.c.settings.style_id == "middle_age_daily"
    tab.style_combo.setCurrentIndex(tab.style_combo.findData("work_daily"))
    qtbot.mouseClick(tab.save_button, Qt.MouseButton.LeftButton)
    assert env.c.settings.style_id == "work_daily"
    assert "직장 일상회화" in win.study_tab.option_label.text()


def test_gender_radios_disabled_without_verified_voices(ui):  # R11 미해결 상태 표시
    env, win = ui
    tab = win.settings_tab
    assert not tab.female_radio.isEnabled() and not tab.male_radio.isEnabled()
    assert tab.voice_combo.count() == 1 and tab.voice_combo.currentData() == "EN-US"


def test_preview_shows_translation_while_buttons_locked(ui, qtbot):
    env, win = ui
    st = win.study_tab
    seen = []

    def snap(_s):
        seen.append((st.translation_view.toPlainText(), st.play_button.isEnabled(), st.save_button.isEnabled(),
                     st.status_label.text()))

    env.c.translation_preview.connect(snap)
    st.input_edit.setPlainText("하나\n둘")
    qtbot.mouseClick(st.convert_button, Qt.MouseButton.LeftButton)
    text, play_on, save_on, _ = seen[0]
    assert "[DEV] Sentence 1" in text and "[DEV] Sentence 2" in text and "1." not in text
    assert text.index("[DEV] Sentence 2") < text.index("표현 예시") < text.index("[DEV] Example expression.")
    assert not play_on and not save_on


def test_input_box_about_four_lines(ui):
    env, win = ui
    edit = win.study_tab.input_edit
    line = edit.fontMetrics().lineSpacing()
    assert line * 4 <= edit.height() < line * 6


def test_compact_rows(ui):
    env, win = ui
    st = win.study_tab

    def same_row(a, b):
        return abs(a.mapTo(st, a.rect().center()).y() - b.mapTo(st, b.rect().center()).y()) < 6

    from PySide6.QtWidgets import QLabel

    hint = next(l for l in st.findChildren(QLabel) if l.text().startswith("R: "))
    assert same_row(st.convert_button, st.option_label)  # A
    assert same_row(st.stop_button, hint)  # B
    assert not [l for l in st.findChildren(QLabel) if "끝나면 기다립니다" in l.text()]  # C
    assert same_row(st.save_button, st.status_label)  # D


def test_api_key_field_saves_to_settings(ui, qtbot):
    env, win = ui
    tab = win.settings_tab
    assert tab.api_key_edit.echoMode() == tab.api_key_edit.EchoMode.Password
    tab.api_key_edit.setText("  sk-ant-test  ")
    qtbot.mouseClick(tab.save_button, Qt.MouseButton.LeftButton)
    assert env.c.settings.anthropic_api_key == "sk-ant-test"
    import json

    assert json.loads(env.paths.config_file.read_text())["anthropic_api_key"] == "sk-ant-test"
