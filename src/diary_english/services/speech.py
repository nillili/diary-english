"""음성 생성 Protocol 과 MeloTTS 구현.

모델은 import 시점이 아니라 처음 필요할 때 로딩한다.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

import numpy as np

from ..domain import AudioBuffer, SpeechError, VoiceInfo

log = logging.getLogger(__name__)


class SpeechSynthesizer(Protocol):
    def list_voices(self) -> list[VoiceInfo]: ...
    def synthesize(self, text: str, voice_id: str, speed: float) -> AudioBuffer: ...


# MeloTTS 영어 화자 → 억양. 공식 문서는 성별을 밝히지 않으므로 gender 는 unknown.
MELO_EN_LOCALES = {
    "EN-US": ("미국 영어 (EN-US)", "en-US"),
    "EN-BR": ("영국 영어 (EN-BR)", "en-GB"),
    "EN_INDIA": ("인도 영어 (EN_INDIA)", "en-IN"),
    "EN-AU": ("호주 영어 (EN-AU)", "en-AU"),
    "EN-Default": ("영어 기본 (EN-Default)", "en"),
}


class MeloSpeech:
    engine_id = "melotts"

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self._model = None
        self._lock = threading.Lock()  # 모델을 여러 스레드에서 동시에 호출하지 않는다

    def _load(self):
        if self._model is None:
            try:
                from melo.api import TTS
            except ImportError as exc:
                raise SpeechError("MeloTTS 가 설치되어 있지 않습니다.") from exc
            log.info("MeloTTS 영어 모델 로딩 (device=%s)", self.device)
            try:
                self._model = TTS(language="EN", device=self.device)
            except Exception as exc:
                raise SpeechError(f"MeloTTS 모델 로딩 실패: {exc}") from exc
        return self._model

    def _speaker_ids(self) -> dict[str, int]:
        return dict(self._load().hps.data.spk2id)

    def list_voices(self) -> list[VoiceInfo]:
        with self._lock:
            ids = self._speaker_ids()
        voices = []
        for vid in ids:
            label, locale = MELO_EN_LOCALES.get(vid, (vid, "en"))
            voices.append(VoiceInfo(vid, label, locale, "unknown", self.engine_id))
        return voices

    def synthesize(self, text: str, voice_id: str, speed: float) -> AudioBuffer:
        if not text.strip():
            raise SpeechError("읽을 문장이 비어 있습니다.")
        with self._lock:
            model = self._load()
            ids = self._speaker_ids()
            if voice_id not in ids:
                raise SpeechError(f"MeloTTS 에 없는 음성입니다: {voice_id}")
            try:
                audio = model.tts_to_file(text, ids[voice_id], output_path=None, speed=speed, quiet=True)
            except Exception as exc:
                raise SpeechError(f"음성 생성 실패: {exc}") from exc
            rate = int(model.hps.data.sampling_rate)
        return AudioBuffer(np.asarray(audio, dtype=np.float32).reshape(-1), rate)
