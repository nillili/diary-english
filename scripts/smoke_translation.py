"""로컬 Ollama 번역 확인: 스타일별 결과와 소요 시간을 출력한다(사람이 검수).

사용: python scripts/smoke_translation.py MODEL [STYLE ...]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diary_english.domain import STYLES, ConversionRequest, Settings  # noqa: E402
from diary_english.services.translator import OllamaTranslator  # noqa: E402

DIARIES = [
    "오늘은 퇴근하고 동네 공원을 걸었다.\n날씨가 선선해서 기분이 좋았다.",
    "오늘은 산책하지 않았다. 내일은 공원에 갈 예정이다.",
    "점심에 민수와 김치찌개를 먹었는데 3만 원이 나왔다.\n좀 비쌌지만 맛있어서 괜찮았다. KTX 표도 예매했다.",
    "아침에 늦잠을 자서 회사에 지각했다.\n팀장님께 혼날까 봐 걱정했는데 다행히 아무 말씀 없으셨다.",
    "저녁에 아내와 드라마를 보다가 잠들었다.\n요즘 너무 피곤하다.",
    "주말에 가족과 함께 캠핑을 갔다\n저녁에 고기를 구워 먹었고\n밤에는 별을 보며 이야기를 나눴다",
]


def main() -> None:
    model = sys.argv[1]
    styles = sys.argv[2:] or list(STYLES)
    tr = OllamaTranslator(model)
    for style in styles:
        print(f"\n=== {model} / {STYLES[style]} ({style}) ===")
        for d in DIARIES:
            t = time.time()
            try:
                r = tr.translate(ConversionRequest.create(d, Settings(style_id=style)))
                out = "\n    ".join(r.sentences) + "\n    (예시) " + " / ".join(r.examples)
            except Exception as exc:  # 결과 확인용 스크립트
                out = f"실패: {exc}"
            print(f"[{time.time() - t:5.1f}s] {d!r}\n    {out}")


if __name__ == "__main__":
    main()
