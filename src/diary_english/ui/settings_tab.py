"""환경설정 탭: 편집 · 저장 · 취소. 변경은 다음 변환부터 적용된다."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..controller import AppController
from ..domain import STYLES, Settings, VoiceInfo

SPEEDS = [("느리게", 0.8), ("보통", 1.0), ("빠르게", 1.2)]


class SettingsTab(QWidget):
    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller

        # 번역 설정
        self.style_combo = QComboBox()
        self.style_combo.setObjectName("styleCombo")
        for sid, label in STYLES.items():
            self.style_combo.addItem(label, sid)
        tr = QGroupBox("번역 설정")
        tf = QFormLayout(tr)
        tf.addRow("입력 언어", QLabel("한국어"))
        tf.addRow("출력 언어", QLabel("미국식 영어"))
        tf.addRow("회화 스타일", self.style_combo)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setObjectName("apiKeyEdit")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-ant-...  (비우면 로컬 번역)")
        tf.addRow("Claude API 키", self.api_key_edit)

        # 음성 설정
        self.female_radio = QRadioButton("여성")
        self.male_radio = QRadioButton("남성")
        self.gender_group = QButtonGroup(self)
        self.gender_group.addButton(self.female_radio)
        self.gender_group.addButton(self.male_radio)
        gender_row = QHBoxLayout()
        gender_row.addWidget(self.female_radio)
        gender_row.addWidget(self.male_radio)
        gender_row.addStretch(1)
        self.voice_combo = QComboBox()
        self.voice_combo.setObjectName("voiceCombo")
        self.speed_combo = QComboBox()
        for label, value in SPEEDS:
            self.speed_combo.addItem(label, value)
        vo = QGroupBox("음성 설정")
        vf = QFormLayout(vo)
        vf.addRow("음성 성별", gender_row)
        vf.addRow("목소리", self.voice_combo)
        vf.addRow("생성 속도", self.speed_combo)
        vf.addRow("음성 생성 방식", QLabel("로컬 생성"))

        # 저장
        self.root_edit = QLineEdit()
        self.root_edit.setObjectName("rootEdit")
        self.root_button = QPushButton("폴더 선택")
        root_row = QHBoxLayout()
        root_row.addWidget(self.root_edit, 1)
        root_row.addWidget(self.root_button)
        st = QGroupBox("학습 파일 저장")
        sf = QFormLayout(st)
        sf.addRow("저장 위치", root_row)
        sf.addRow("기본 보관", QLabel("문장별 MP3 + 전체 MP3 + 원문 및 번역"))

        self.save_button = QPushButton("설정 저장")
        self.save_button.setObjectName("settingsSaveButton")
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setObjectName("settingsCancelButton")
        btns = QHBoxLayout()
        btns.addWidget(self.save_button)
        btns.addWidget(self.cancel_button)
        btns.addStretch(1)

        bottom = QFrame()
        bottom.setFrameShape(QFrame.Shape.StyledPanel)
        bl = QVBoxLayout(bottom)
        bl.addWidget(QLabel("언어와 음성 변경은 다음 변환부터 적용됩니다."))

        layout = QVBoxLayout(self)
        layout.addWidget(tr)
        layout.addWidget(vo)
        layout.addWidget(st)
        layout.addLayout(btns)
        layout.addStretch(1)
        layout.addWidget(bottom)

        self.root_button.clicked.connect(self._choose_root)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.load_from_settings)
        self.gender_group.buttonToggled.connect(self._on_gender_toggled)
        self.c.voices_changed.connect(self.load_from_settings)
        self.c.voices_error.connect(self.voice_combo.setPlaceholderText)
        self.c.settings_changed.connect(self.load_from_settings)

        self.load_from_settings()

    # ------------------------------------------------------------ 음성 목록
    def _voices_for(self, gender: str) -> list[VoiceInfo]:
        return [v for v in self.c.us_voices() if v.gender == gender]

    def _selected_gender(self) -> str:
        if self.female_radio.isChecked():
            return "female"
        if self.male_radio.isChecked():
            return "male"
        return "unknown"

    def _fill_voices(self, select_id: str | None = None) -> None:
        current = select_id if select_id is not None else self.voice_combo.currentData()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for v in self._voices_for(self._selected_gender()):
            self.voice_combo.addItem(v.label, v.id)
        idx = self.voice_combo.findData(current)
        if idx >= 0:
            self.voice_combo.setCurrentIndex(idx)
        self.voice_combo.blockSignals(False)
        self.voice_combo.setEnabled(self.voice_combo.count() > 0)

    def _on_gender_toggled(self, *_args) -> None:
        self._fill_voices()

    def _sync_gender_radios(self, voice_id: str) -> None:
        """실제로 확인된 성별 음성이 있을 때만 해당 라디오를 켠다."""
        has_f = bool(self._voices_for("female"))
        has_m = bool(self._voices_for("male"))
        self.female_radio.setEnabled(has_f)
        self.male_radio.setEnabled(has_m)
        voice = next((v for v in self.c.us_voices() if v.id == voice_id), None)
        self.gender_group.setExclusive(False)
        self.female_radio.setChecked(voice is not None and voice.gender == "female")
        self.male_radio.setChecked(voice is not None and voice.gender == "male")
        self.gender_group.setExclusive(True)

    # ------------------------------------------------------------ 불러오기 / 저장
    def load_from_settings(self, *_args) -> None:
        s = self.c.settings
        self.style_combo.setCurrentIndex(max(self.style_combo.findData(s.style_id), 0))
        self.gender_group.blockSignals(True)
        self._sync_gender_radios(s.voice_id)
        self.gender_group.blockSignals(False)
        self._fill_voices(s.voice_id)
        idx = self.speed_combo.findData(s.speed)
        self.speed_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self.root_edit.setText(s.storage_root)
        self.api_key_edit.setText(s.anthropic_api_key)

    def edited_settings(self) -> Settings:
        s = self.c.settings
        voice_id = self.voice_combo.currentData() or s.voice_id
        return replace(
            s,
            style_id=self.style_combo.currentData(),
            voice_id=voice_id,
            speed=float(self.speed_combo.currentData()),
            storage_root=self.root_edit.text().strip() or s.storage_root,
            anthropic_api_key=self.api_key_edit.text().strip(),
        )

    def _save(self) -> None:
        self.c.apply_settings(self.edited_settings())

    def _choose_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "저장 위치 선택", self.root_edit.text())
        if path:
            self.root_edit.setText(path)
