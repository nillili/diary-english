"""실제 번역 + MeloTTS 로 임시 학습 자료 하나를 끝까지 만들어 본다(통합 확인용).

사용: python scripts/smoke_melotts.py [출력 폴더] [번역 모델]
번역 서버가 없으면 --no-translate 로 고정 영어 문장만 음성 생성한다.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diary_english.domain import ConversionRequest, Settings, TranslationResult  # noqa: E402
from diary_english.services.speech import MeloSpeech  # noqa: E402
from diary_english.services.translator import OllamaTranslator  # noqa: E402
from diary_english.workers import run_conversion  # noqa: E402

DIARY = "오늘은 퇴근하고 동네 공원을 걸었다.\n날씨가 선선해서 기분이 좋았다."


class FixedTranslator:
    def translate(self, request):
        return TranslationResult(
            ("I took a walk in the park after work today.", "The cool weather put me in a good mood."),
            model="fixed-text",
        )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0]) if args else Path("smoke_output")
    model = args[1] if len(args) > 1 else "qwen2.5:7b"
    translator = FixedTranslator() if "--no-translate" in sys.argv else OllamaTranslator(model)

    speech = MeloSpeech()
    t0 = time.time()
    print("voices:", [(v.id, v.locale, v.gender) for v in speech.list_voices()])
    print(f"model load {time.time() - t0:.1f}s")

    t0 = time.time()
    art = run_conversion(
        ConversionRequest.create(DIARY, Settings(voice_id="EN-US")),
        translator, speech, out,
        lambda stage, cur, tot: print(f"  [{time.time() - t0:6.1f}s] {stage} {cur}/{tot}"),
        threading.Event(),
    )
    print(f"done {time.time() - t0:.1f}s → {art.folder}")
    for s in art.sentences:
        print(f"  {s.audio_path} {s.duration_ms}ms  {s.text}")


if __name__ == "__main__":
    main()
