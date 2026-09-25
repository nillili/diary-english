from __future__ import annotations

import json

import pytest

from diary_english.domain import ConversionRequest, Settings, TranslationError
from diary_english.services.prompts import STYLE_GUIDES
from diary_english.services.translator import OllamaTranslator, parse_response


class FakeTransport:
    def __init__(self, contents):
        self.contents = list(contents)
        self.payloads = []

    def __call__(self, url, payload, timeout):
        self.payloads.append(payload)
        item = self.contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"message": {"role": "assistant", "content": item}}


def req(text="오늘은 산책하지 않았다.", style="casual"):
    return ConversionRequest.create(text, Settings(style_id=style))


def test_style_and_schema_are_sent():
    t = FakeTransport([json.dumps({"sentences": ["I didn't take a walk today."], "examples": ["Skipped my walk."]})])
    r = OllamaTranslator("m", transport=t).translate(req(style="casual"))
    assert r.sentences == ("I didn't take a walk today.",) and r.examples == ("Skipped my walk.",)
    p = t.payloads[0]
    assert p["stream"] is False and p["format"]["required"] == ["sentences", "examples"]
    assert "not like a textbook" in p["messages"][0]["content"]  # 구어체 지시
    assert "I took a walk around the park after work today." in p["messages"][0]["content"]  # 좋은 번역 예시
    assert STYLE_GUIDES["casual"] in p["messages"][0]["content"]
    assert "<diary>" in p["messages"][1]["content"]


def test_one_retry_on_format_error_then_success():
    t = FakeTransport(["Sure! Here it is", json.dumps({"sentences": ["A.", "B."], "examples": []})])
    r = OllamaTranslator("m", transport=t).translate(req("첫 줄\n둘째 줄"))
    assert r.sentences == ("A.", "B.") and len(t.payloads) == 2


def test_only_one_retry():
    t = FakeTransport(["bad", "still bad", json.dumps({"sentences": ["A."], "examples": []})])
    with pytest.raises(TranslationError):
        OllamaTranslator("m", transport=t).translate(req())
    assert len(t.payloads) == 2


def test_server_error_propagates_without_retry():
    t = FakeTransport([TranslationError("연결 불가")])
    with pytest.raises(TranslationError):
        OllamaTranslator("m", transport=t).translate(req())
    assert len(t.payloads) == 1


@pytest.mark.parametrize(
    "content",
    ["", "[]", '{"sentences": [], "examples": []}', '{"sentences": ["ok", 3], "examples": []}',
     '{"sentences": ["a"], "examples": [], "note": "x"}', '{"sentences": ["   "], "examples": []}',
     '{"sentences": ["a"]}', '{"sentences": ["a"], "examples": ["ok", 1]}', '{"sentences": ["a"], "examples": ["  "]}'],
)
def test_bad_content_rejected(content):
    with pytest.raises(TranslationError):
        from diary_english.domain import TranslationResult

        sentences, examples = parse_response(content)
        TranslationResult(sentences, examples=examples)


def test_input_limits():
    t = FakeTransport([])
    tr = OllamaTranslator("m", transport=t)
    with pytest.raises(TranslationError):
        tr.translate(req("   "))
    with pytest.raises(TranslationError):
        tr.translate(req("가" * 2001))
    assert t.payloads == []


def test_real_connection_refused_is_translation_error():
    tr = OllamaTranslator("m", base_url="http://127.0.0.1:9", timeout=2)
    with pytest.raises(TranslationError):
        tr.translate(req())


def test_line_count_must_match_diary_lines():
    diary = "첫 줄이다. 두 문장이다.\n\n둘째 줄\n셋째 줄"  # 빈 줄 제외 3줄
    ok = json.dumps({"sentences": ["One. Two.", "Second.", "Third."], "examples": []})
    t = FakeTransport([json.dumps({"sentences": ["One.", "Two.", "Second.", "Third."], "examples": []}), ok])
    r = OllamaTranslator("m", transport=t).translate(req(diary))
    assert r.sentences == ("One. Two.", "Second.", "Third.")
    assert "Diary lines: 3" in t.payloads[0]["messages"][1]["content"]
    assert "exactly 3 elements" in t.payloads[1]["messages"][-1]["content"]  # 교정 요청 1회


def test_line_count_mismatch_twice_fails():
    bad = json.dumps({"sentences": ["One.", "Two."], "examples": []})
    t = FakeTransport([bad, bad])
    with pytest.raises(TranslationError):
        OllamaTranslator("m", transport=t).translate(req("한 줄뿐"))
    assert len(t.payloads) == 2


# ------------------------------------------------------------ Claude

from types import SimpleNamespace  # noqa: E402

from diary_english.services.translator import CLAUDE_MODEL, ClaudeTranslator  # noqa: E402


class FakeClaude:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        text, stop = self.replies.pop(0)
        return SimpleNamespace(
            stop_reason=stop,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def test_claude_request_shape_and_result():
    ok = json.dumps({"sentences": ["Worked the field this morning.", "Left early."], "examples": ["Headed out early."]})
    fake = FakeClaude([(ok, "end_turn")])
    r = ClaudeTranslator("k", client=fake).translate(req("아침에 밭일\n먼저 나옴"))
    assert r.sentences == ("Worked the field this morning.", "Left early.") and r.model == CLAUDE_MODEL == "claude-sonnet-5"
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert "not like a textbook" in call["system"]
    assert [m["role"] for m in call["messages"]] == ["user"]  # system 은 별도 필드로
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False


def test_claude_line_mismatch_retries_once():
    bad = json.dumps({"sentences": ["A.", "B."], "examples": []})
    ok = json.dumps({"sentences": ["A. B."], "examples": []})
    fake = FakeClaude([(bad, "end_turn"), (ok, "end_turn")])
    r = ClaudeTranslator("k", client=fake).translate(req("한 줄"))
    assert r.sentences == ("A. B.",)
    assert [m["role"] for m in fake.calls[1]["messages"]] == ["user", "assistant", "user"]


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_bad_stop_reason_is_error(stop):
    fake = FakeClaude([("{}", stop), ("{}", stop)])
    with pytest.raises(TranslationError):
        ClaudeTranslator("k", client=fake).translate(req())


def test_api_key_saved_but_hidden_from_repr():
    s = Settings(anthropic_api_key="sk-ant-secret")
    assert Settings.from_dict(s.to_dict()).anthropic_api_key == "sk-ant-secret"
    assert "sk-ant-secret" not in repr(s)
    assert Settings.from_dict({}).anthropic_api_key == ""
