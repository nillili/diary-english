from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pytest

from diary_english.domain import (
    AudioBuffer,
    SentenceAsset,
    SpeechInfo,
    StudyArtifact,
    make_title,
    now_iso,
    sentence_audio_path,
)
from diary_english.services.audio_files import concat_with_gap, write_mp3
from diary_english.services.repository import write_texts_and_manifest


def tone(seconds: float = 0.3, rate: int = 22050) -> AudioBuffer:
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    return AudioBuffer((0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), rate)


def make_temp_artifact(folder: Path, sentences=("I took a walk.", "It was nice."), original="오늘은 산책했다.\n좋았다.") -> StudyArtifact:
    """테스트 임시 디렉터리 안에 완성된 임시 결과를 만든다."""
    folder.mkdir(parents=True, exist_ok=True)
    assets, bufs = [], []
    for i, text in enumerate(sentences):
        buf = tone(0.2 + 0.05 * i)
        rel = sentence_audio_path(i)
        assets.append(SentenceAsset(i, text, rel, write_mp3(folder / rel, buf)))
        bufs.append(buf)
    write_mp3(folder / "full.mp3", concat_with_gap(bufs))
    a = StudyArtifact(
        artifact_id=str(uuid.uuid4()),
        created_at=now_iso(),
        title=make_title(original),
        original=original,
        style_id="middle_age_daily",
        translation_model="test-model",
        speech=SpeechInfo("fake", "EN-US", "unknown", 1.0),
        sentences=tuple(assets),
        folder=folder,
    )
    write_texts_and_manifest(folder, a)
    return a


@pytest.fixture
def temp_artifact(tmp_path: Path) -> StudyArtifact:
    return make_temp_artifact(tmp_path / "temp" / "a1")
