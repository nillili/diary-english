"""자료형, enum, 데이터 검증.

이 모듈은 Qt·모델·네트워크를 import 하지 않는다.
"""

from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
SENTENCE_GAP_MS = 1000
MAX_INPUT_CHARS = 2000
MAX_SENTENCES = 30
MAX_SENTENCE_CHARS = 1000
MAX_EXAMPLES = 6
TITLE_CHARS = 30

ORIGINAL_FILE = "original.txt"
TRANSLATION_FILE = "translation.txt"
FULL_AUDIO_FILE = "full.mp3"
MANIFEST_FILE = "manifest.json"
SENTENCE_DIR = "sentences"

# 스타일 ID → 한국어 표시명. 순서가 화면 표시 순서다.
STYLES: dict[str, str] = {
    "middle_age_daily": "중년층 일상회화",
    "easy_daily": "쉬운 일상회화",
    "casual": "친근한 캐주얼",
    "polite_daily": "정중한 일상회화",
    "work_daily": "직장 일상회화",
}
DEFAULT_STYLE = "middle_age_daily"

GENDERS = ("male", "female", "unknown")
GENDER_LABELS = {"male": "남성", "female": "여성", "unknown": "성별 미확인"}


# ---------------------------------------------------------------- 예외

class AppError(Exception):
    """사용자에게 보여줄 수 있는 단계별 오류의 기반 클래스."""

    stage = "작업"


class TranslationError(AppError):
    stage = "번역"


class SpeechError(AppError):
    stage = "음성 생성"


class AudioEncodingError(AppError):
    stage = "MP3 인코딩"


class StorageError(AppError):
    stage = "저장"


class InvalidStudyError(AppError):
    stage = "자료 확인"


class CancelledError(AppError):
    stage = "취소"


# ---------------------------------------------------------------- 상태

class WorkState(enum.Enum):
    IDLE = "IDLE"
    CONVERTING = "CONVERTING"
    SAVING = "SAVING"
    LOADING = "LOADING"


class DataState(enum.Enum):
    NONE = "NONE"
    TEMP_READY = "TEMP_READY"
    SAVED_READY = "SAVED_READY"


class PlayState(enum.Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


# ---------------------------------------------------------------- 자료형

@dataclass(frozen=True)
class Settings:
    source_language: str = "ko"
    target_language: str = "en-US"
    style_id: str = DEFAULT_STYLE
    engine_id: str = "melotts"
    voice_id: str = "EN-US"
    speed: float = 1.0
    storage_root: str = ""
    library_roots: tuple[str, ...] = ()
    translation_model: str = ""
    # Claude API 키. 있으면 Claude 로 번역하고, 없으면 로컬 Ollama 로 번역한다. 설정 파일에 그대로 저장된다.
    anthropic_api_key: str = field(default="", repr=False)

    def validated(self) -> "Settings":
        if self.style_id not in STYLES:
            raise ValueError(f"알 수 없는 스타일: {self.style_id}")
        if not (0.5 <= float(self.speed) <= 2.0):
            raise ValueError(f"속도 범위 오류: {self.speed}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "style_id": self.style_id,
            "engine_id": self.engine_id,
            "voice_id": self.voice_id,
            "speed": float(self.speed),
            "storage_root": self.storage_root,
            "library_roots": list(self.library_roots),
            "translation_model": self.translation_model,
            "anthropic_api_key": self.anthropic_api_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        base = cls()
        roots = data.get("library_roots", [])
        if not isinstance(roots, list):
            roots = []
        return replace(
            base,
            style_id=str(data.get("style_id", base.style_id)),
            engine_id=str(data.get("engine_id", base.engine_id)),
            voice_id=str(data.get("voice_id", base.voice_id)),
            speed=float(data.get("speed", base.speed)),
            storage_root=str(data.get("storage_root", base.storage_root)),
            library_roots=tuple(str(r) for r in roots if isinstance(r, str) and r),
            translation_model=str(data.get("translation_model", base.translation_model)),
            anthropic_api_key=str(data.get("anthropic_api_key", "")).strip(),
        ).validated()


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    label: str
    locale: str
    gender: str
    engine_id: str

    def __post_init__(self) -> None:
        if self.gender not in GENDERS:
            raise ValueError(f"잘못된 성별 값: {self.gender}")


@dataclass(frozen=True)
class ConversionRequest:
    request_id: str
    original: str
    settings: Settings

    @classmethod
    def create(cls, original: str, settings: Settings) -> "ConversionRequest":
        return cls(request_id=uuid.uuid4().hex, original=original, settings=settings)


@dataclass(frozen=True)
class TranslationResult:
    sentences: tuple[str, ...]
    model: str = ""
    examples: tuple[str, ...] = ()  # 표현 예시: 화면에만 보여 주고 음성으로 만들지 않는다

    def __post_init__(self) -> None:
        validate_sentences(self.sentences, TranslationError)
        validate_examples(self.examples, TranslationError)

    @property
    def text(self) -> str:
        return "\n".join(self.sentences)


@dataclass(frozen=True)
class AudioBuffer:
    """유한한 값의 1차원 mono PCM."""

    samples: Any  # numpy.ndarray (float32)
    sample_rate: int

    def __post_init__(self) -> None:
        import numpy as np

        arr = self.samples
        if not isinstance(arr, np.ndarray) or arr.ndim != 1:
            raise SpeechError("음성 데이터가 1차원 배열이 아닙니다.")
        if arr.size == 0:
            raise SpeechError("음성 데이터가 비어 있습니다.")
        if not np.all(np.isfinite(arr)):
            raise SpeechError("음성 데이터에 NaN/Inf 값이 있습니다.")
        if not isinstance(self.sample_rate, int) or self.sample_rate <= 0:
            raise SpeechError(f"잘못된 샘플레이트: {self.sample_rate}")


@dataclass(frozen=True)
class SentenceAsset:
    index: int
    text: str
    audio_path: str  # 학습 폴더 기준 상대 경로 (POSIX 구분자)
    duration_ms: int


@dataclass(frozen=True)
class SpeechInfo:
    engine: str
    voice_id: str
    gender: str
    speed: float


@dataclass(frozen=True)
class StudyArtifact:
    artifact_id: str
    created_at: str
    title: str
    original: str
    style_id: str
    translation_model: str
    speech: SpeechInfo
    sentences: tuple[SentenceAsset, ...]
    folder: Path  # 파일이 실제로 있는 폴더 (임시 또는 저장)
    saved: bool = False
    source_language: str = "ko"
    target_language: str = "en-US"
    sentence_gap_ms: int = SENTENCE_GAP_MS
    examples: tuple[str, ...] = ()

    @property
    def translation(self) -> str:
        return "\n".join(s.text for s in self.sentences)

    def sentence_file(self, index: int) -> Path:
        return self.folder / self.sentences[index].audio_path

    @property
    def full_audio_file(self) -> Path:
        return self.folder / FULL_AUDIO_FILE


@dataclass(frozen=True)
class LibraryEntry:
    folder: Path
    artifact_id: str
    created_at: str
    title: str
    sentence_count: int
    style_id: str
    voice_gender: str
    original: str = ""
    error: str = ""  # 비어 있지 않으면 손상 항목

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def date_text(self) -> str:
        return self.created_at[:10] if self.created_at else ""


@dataclass
class Draft:
    text: str
    saved_at: str = field(default_factory=lambda: now_iso())


# ---------------------------------------------------------------- 도우미

def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sentence_display_number(index: int) -> int:
    """내부 0-기반 index → 화면·파일 1-기반 번호. 변환은 이 함수와 아래 함수에서만 한다."""
    return index + 1


def sentence_audio_path(index: int) -> str:
    return f"{SENTENCE_DIR}/{sentence_display_number(index):03d}.mp3"


def diary_lines(original: str) -> list[str]:
    """빈 줄을 뺀 일기 줄 목록. 번역도 이 줄 수만큼 만든다."""
    return [ln.strip() for ln in original.splitlines() if ln.strip()]


def make_title(original: str) -> str:
    for line in original.splitlines():
        line = line.strip()
        if line:
            return line[:TITLE_CHARS].rstrip()
    return "(제목 없음)"


def folder_name(created_at: str, artifact_id: str) -> str:
    dt = datetime.fromisoformat(created_at)
    return f"{dt:%Y-%m-%d_%H%M%S}_{uuid.UUID(artifact_id).hex}"


def validate_sentences(sentences: tuple[str, ...] | list[str], error_cls: type[AppError]) -> None:
    if not sentences:
        raise error_cls("번역 결과 문장이 없습니다.")
    if len(sentences) > MAX_SENTENCES:
        raise error_cls(f"문장 수가 한도({MAX_SENTENCES})를 넘었습니다: {len(sentences)}")
    for s in sentences:
        if not isinstance(s, str) or not s.strip():
            raise error_cls("빈 문장이 포함되어 있습니다.")
        if len(s) > MAX_SENTENCE_CHARS:
            raise error_cls(f"문장 길이가 한도({MAX_SENTENCE_CHARS}자)를 넘었습니다.")
        if "\n" in s or "\r" in s:
            raise error_cls("문장 안에 줄바꿈이 있습니다.")


def validate_examples(examples: tuple[str, ...] | list[str], error_cls: type[AppError]) -> None:
    if len(examples) > MAX_EXAMPLES:
        raise error_cls(f"표현 예시가 한도({MAX_EXAMPLES})를 넘었습니다.")
    for e in examples:
        if not isinstance(e, str) or not e.strip() or len(e) > MAX_SENTENCE_CHARS or "\n" in e:
            raise error_cls("표현 예시 형식이 올바르지 않습니다.")


def check_relative_path(rel: Any, folder: Path) -> Path:
    """manifest 의 상대 경로를 검증하고 실제 경로를 돌려준다.

    절대 경로, `..`, 폴더 밖으로 나가는 symlink 는 거부한다.
    """
    if not isinstance(rel, str) or not rel:
        raise InvalidStudyError("파일 경로가 비어 있습니다.")
    if "\\" in rel:
        raise InvalidStudyError(f"허용되지 않는 경로 구분자: {rel}")
    p = PurePosixPath(rel)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or rel.startswith("/"):
        raise InvalidStudyError(f"허용되지 않는 경로: {rel}")
    full = folder.joinpath(*p.parts)
    try:
        resolved = full.resolve(strict=False)
        base = folder.resolve(strict=False)
    except OSError as exc:
        raise InvalidStudyError(f"경로 확인 실패: {rel}") from exc
    if base != resolved and base not in resolved.parents:
        raise InvalidStudyError(f"학습 폴더 밖을 가리키는 경로: {rel}")
    return full


# ---------------------------------------------------------------- manifest

def artifact_to_manifest(a: StudyArtifact) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": a.artifact_id,
        "created_at": a.created_at,
        "title": a.title,
        "source_language": a.source_language,
        "target_language": a.target_language,
        "style_id": a.style_id,
        "translation_model": a.translation_model,
        "speech": {
            "engine": a.speech.engine,
            "voice_id": a.speech.voice_id,
            "gender": a.speech.gender,
            "speed": a.speech.speed,
        },
        "original_path": ORIGINAL_FILE,
        "translation_path": TRANSLATION_FILE,
        "full_audio_path": FULL_AUDIO_FILE,
        "sentence_gap_ms": a.sentence_gap_ms,
        "sentences": [
            {"index": s.index, "text": s.text, "audio_path": s.audio_path, "duration_ms": s.duration_ms}
            for s in a.sentences
        ],
        "examples": list(a.examples),
    }


def _req(data: dict[str, Any], key: str, typ: type | tuple[type, ...]) -> Any:
    if key not in data:
        raise InvalidStudyError(f"manifest 에 '{key}' 항목이 없습니다.")
    val = data[key]
    if not isinstance(val, typ) or (typ is int and isinstance(val, bool)):
        raise InvalidStudyError(f"manifest '{key}' 형식 오류")
    return val


def manifest_to_artifact(data: Any, folder: Path, original: str, *, saved: bool) -> StudyArtifact:
    """manifest 구조를 검증해 StudyArtifact 로 만든다. 파일 존재 여부는 repository 가 확인한다."""
    if not isinstance(data, dict):
        raise InvalidStudyError("manifest 가 객체가 아닙니다.")
    version = _req(data, "schema_version", int)
    if version != SCHEMA_VERSION:
        raise InvalidStudyError(f"지원하지 않는 manifest 버전: {version}")

    artifact_id = _req(data, "artifact_id", str)
    try:
        uuid.UUID(artifact_id)
    except ValueError as exc:
        raise InvalidStudyError("artifact_id 형식 오류") from exc

    created_at = _req(data, "created_at", str)
    try:
        if datetime.fromisoformat(created_at).tzinfo is None:
            raise InvalidStudyError("created_at 에 시간대가 없습니다.")
    except ValueError as exc:
        raise InvalidStudyError("created_at 형식 오류") from exc

    style_id = _req(data, "style_id", str)
    speech = _req(data, "speech", dict)
    gender = _req(speech, "gender", str)
    if gender not in GENDERS:
        raise InvalidStudyError("speech.gender 값 오류")
    speed = _req(speech, "speed", (int, float))

    for key, expected in (
        ("original_path", ORIGINAL_FILE),
        ("translation_path", TRANSLATION_FILE),
        ("full_audio_path", FULL_AUDIO_FILE),
    ):
        check_relative_path(_req(data, key, str), folder)
        if data[key] != expected:
            raise InvalidStudyError(f"{key} 값이 예상과 다릅니다: {data[key]}")

    raw_sentences = _req(data, "sentences", list)
    sentences: list[SentenceAsset] = []
    seen_paths: set[str] = set()
    for i, raw in enumerate(raw_sentences):
        if not isinstance(raw, dict):
            raise InvalidStudyError("문장 항목 형식 오류")
        index = _req(raw, "index", int)
        if index != i:
            raise InvalidStudyError(f"문장 index 가 연속되지 않습니다: {index}")
        text = _req(raw, "text", str)
        audio_path = _req(raw, "audio_path", str)
        check_relative_path(audio_path, folder)
        if audio_path in seen_paths or audio_path in (ORIGINAL_FILE, TRANSLATION_FILE, FULL_AUDIO_FILE):
            raise InvalidStudyError(f"중복된 음성 경로: {audio_path}")
        seen_paths.add(audio_path)
        duration = _req(raw, "duration_ms", int)
        if duration <= 0:
            raise InvalidStudyError("duration_ms 값 오류")
        sentences.append(SentenceAsset(index, text, audio_path, duration))
    validate_sentences([s.text for s in sentences], InvalidStudyError)

    examples = data.get("examples", [])  # 예전 자료에는 없을 수 있다
    if not isinstance(examples, list):
        raise InvalidStudyError("examples 형식 오류")
    validate_examples(examples, InvalidStudyError)

    gap = _req(data, "sentence_gap_ms", int)
    if gap < 0:
        raise InvalidStudyError("sentence_gap_ms 값 오류")

    return StudyArtifact(
        artifact_id=artifact_id,
        created_at=created_at,
        title=_req(data, "title", str),
        original=original,
        style_id=style_id,
        translation_model=_req(data, "translation_model", str),
        speech=SpeechInfo(
            engine=_req(speech, "engine", str),
            voice_id=_req(speech, "voice_id", str),
            gender=gender,
            speed=float(speed) if math.isfinite(float(speed)) else 1.0,
        ),
        sentences=tuple(sentences),
        folder=folder,
        saved=saved,
        source_language=_req(data, "source_language", str),
        target_language=_req(data, "target_language", str),
        sentence_gap_ms=gap,
        examples=tuple(examples),
    )
