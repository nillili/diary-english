"""테스트·개발 모드 전용 대체 서비스. 일반 모드에서는 사용하지 않는다."""

from __future__ import annotations

import numpy as np

from ..domain import AudioBuffer, ConversionRequest, TranslationError, TranslationResult, VoiceInfo

FAKE_MODEL = "dev-fake"


class FakeTranslator:
    """원문 각 줄을 '[DEV] 번호' 문장으로 바꾼다. 실제 번역이 아니다."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_next: Exception | None = None

    def translate(self, request: ConversionRequest) -> TranslationResult:
        self.calls += 1
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        lines = [ln.strip() for ln in request.original.splitlines() if ln.strip()]
        if not lines:
            raise TranslationError("번역할 일기가 비어 있습니다.")
        return TranslationResult(
            tuple(f"[DEV] Sentence {i + 1} ({request.settings.style_id})." for i in range(len(lines))),
            model=FAKE_MODEL,
            examples=("[DEV] Example expression.",),
        )


class FakeSpeech:
    """문장 길이에 비례하는 짧은 사인파를 만든다."""

    engine_id = "fake"

    def __init__(self, sample_rate: int = 22050) -> None:
        self.sample_rate = sample_rate
        self.calls = 0
        self.fail_at: int | None = None

    def list_voices(self) -> list[VoiceInfo]:
        return [VoiceInfo("EN-US", "개발용 음성 (EN-US)", "en-US", "unknown", self.engine_id)]

    def synthesize(self, text: str, voice_id: str, speed: float) -> AudioBuffer:
        self.calls += 1
        if self.fail_at is not None and self.calls == self.fail_at:
            from ..domain import SpeechError

            raise SpeechError("의도된 테스트 실패")
        seconds = min(0.3 + 0.02 * len(text), 2.0) / speed
        t = np.arange(int(self.sample_rate * seconds), dtype=np.float32) / self.sample_rate
        return AudioBuffer((0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), self.sample_rate)
