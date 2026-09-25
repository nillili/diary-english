"""PCM 검증, MP3 쓰기, 문장 음성 합치기."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..domain import SENTENCE_GAP_MS, AudioBuffer, AudioEncodingError

log = logging.getLogger(__name__)

PEAK_LIMIT = 0.99


def prepare_pcm(buf: AudioBuffer) -> np.ndarray:
    """float32 mono 로 바꾸고, 피크가 1.0 을 넘을 때만 PEAK_LIMIT 로 낮춘다. 증폭은 하지 않는다."""
    pcm = np.asarray(buf.samples, dtype=np.float32)
    peak = float(np.max(np.abs(pcm)))
    if peak > 1.0:
        log.info("피크 %.3f → %.2f 로 감쇠 정규화", peak, PEAK_LIMIT)
        pcm = pcm * (PEAK_LIMIT / peak)
    return pcm


def write_mp3(path: Path, buf: AudioBuffer) -> int:
    """MP3 로 쓰고 다시 읽어 확인한 재생 길이(ms)를 돌려준다."""
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(str(path), prepare_pcm(buf), buf.sample_rate, format="MP3")
    except Exception as exc:  # soundfile/libsndfile 는 여러 예외 형태를 쓴다
        raise AudioEncodingError(f"MP3 쓰기 실패: {exc}") from exc
    return read_duration_ms(path)


def read_duration_ms(path: Path) -> int:
    """실제로 디코딩한 프레임 수 / 샘플레이트로 길이를 구한다."""
    import soundfile as sf

    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AudioEncodingError(f"MP3 읽기 실패: {path.name}: {exc}") from exc
    if data.shape[0] == 0 or rate <= 0:
        raise AudioEncodingError(f"MP3 에 음성이 없습니다: {path.name}")
    return int(round(data.shape[0] * 1000 / rate))


def concat_with_gap(buffers: list[AudioBuffer], gap_ms: int = SENTENCE_GAP_MS) -> AudioBuffer:
    """원본 PCM 사이에 정확히 gap_ms 의 무음을 넣어 합친다. 마지막 뒤에는 넣지 않는다."""
    if not buffers:
        raise AudioEncodingError("합칠 음성이 없습니다.")
    rate = buffers[0].sample_rate
    if any(b.sample_rate != rate for b in buffers):
        raise AudioEncodingError("문장별 샘플레이트가 서로 다릅니다.")
    gap = np.zeros(int(round(rate * gap_ms / 1000)), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i, b in enumerate(buffers):
        if i:
            parts.append(gap)
        parts.append(prepare_pcm(b))
    return AudioBuffer(np.concatenate(parts), rate)
