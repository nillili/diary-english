from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from diary_english.domain import AudioBuffer, AudioEncodingError, SpeechError
from diary_english.services.audio_files import concat_with_gap, prepare_pcm, read_duration_ms, write_mp3

from .conftest import tone


def test_mp3_write_and_read_duration(tmp_path):
    ms = write_mp3(tmp_path / "한글 경로" / "a.mp3", tone(1.0))
    assert abs(ms - 1000) < 80  # MP3 인코더 패딩 허용


def test_gap_is_exact_and_not_after_last():
    a, b = tone(0.5), tone(0.25)
    full = concat_with_gap([a, b], 1000)
    rate = a.sample_rate
    assert full.samples.size == a.samples.size + rate + b.samples.size
    gap = full.samples[a.samples.size : a.samples.size + rate]
    assert np.all(gap == 0)


def test_full_mp3_duration(tmp_path):
    full = concat_with_gap([tone(0.5), tone(0.5)], 1000)
    ms = write_mp3(tmp_path / "full.mp3", full)
    assert abs(ms - 2000) < 100


def test_different_sample_rates_fail():
    with pytest.raises(AudioEncodingError):
        concat_with_gap([tone(0.2, 22050), tone(0.2, 44100)])


def test_invalid_pcm_rejected():
    with pytest.raises(SpeechError):
        AudioBuffer(np.array([], dtype=np.float32), 22050)
    with pytest.raises(SpeechError):
        AudioBuffer(np.array([0.0, np.nan], dtype=np.float32), 22050)
    with pytest.raises(SpeechError):
        AudioBuffer(np.zeros((2, 2), dtype=np.float32), 22050)
    with pytest.raises(SpeechError):
        AudioBuffer(np.zeros(10, dtype=np.float32), 0)


def test_peak_normalization_only_attenuates():
    loud = AudioBuffer(np.array([2.0, -1.0], dtype=np.float32), 100)
    assert np.max(np.abs(prepare_pcm(loud))) == pytest.approx(0.99)
    quiet = AudioBuffer(np.array([0.1, -0.05], dtype=np.float32), 100)
    assert np.array_equal(prepare_pcm(quiet), quiet.samples)


def test_broken_mp3_read_fails(tmp_path):
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"not an mp3")
    with pytest.raises(AudioEncodingError):
        read_duration_ms(p)


def test_soundfile_supports_mp3():
    assert "MP3" in sf.available_formats()
