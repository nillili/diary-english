"""번역 Protocol 과 로컬 Ollama 구현."""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol

from ..domain import MAX_INPUT_CHARS, ConversionRequest, TranslationError, TranslationResult, diary_lines
from .prompts import RESPONSE_SCHEMA, build_messages, retry_prompt

log = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = 180.0


class Translator(Protocol):
    def translate(self, request: ConversionRequest) -> TranslationResult: ...


Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def http_post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        raise TranslationError(f"번역 서버 오류 ({exc.code}) {detail}".strip()) from exc
    except (urllib.error.URLError, ConnectionError) as exc:
        raise TranslationError("번역 서버(Ollama)에 연결할 수 없습니다. Ollama 가 실행 중인지 확인해 주세요.") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise TranslationError("번역 서버 응답 시간이 초과되었습니다.") from exc
    except ValueError as exc:
        raise TranslationError("번역 서버 응답을 해석할 수 없습니다.") from exc


def parse_response(content: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """모델 응답 본문을 (문장, 표현 예시) 로 검증한다. 잘라내거나 고쳐서 성공 처리하지 않는다."""
    if not isinstance(content, str) or not content.strip():
        raise TranslationError("번역 응답이 비어 있습니다.")
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise TranslationError("번역 응답이 JSON 형식이 아닙니다.") from exc
    if not isinstance(data, dict) or set(data.keys()) != {"sentences", "examples"}:
        raise TranslationError("번역 응답 구조가 올바르지 않습니다.")
    sentences, examples = data["sentences"], data["examples"]
    for items in (sentences, examples):
        if not isinstance(items, list) or not all(isinstance(s, str) for s in items):
            raise TranslationError("번역 응답의 목록 형식이 올바르지 않습니다.")
    return tuple(s.strip() for s in sentences), tuple(e.strip() for e in examples)


def _result(content: str, model: str, line_count: int) -> TranslationResult:
    sentences, examples = parse_response(content)
    if len(sentences) != line_count:
        raise TranslationError(f"번역 줄 수({len(sentences)})가 일기 줄 수({line_count})와 다릅니다.")
    return TranslationResult(sentences, model=model, examples=examples)


class _JsonTranslator:
    """번역 요청·검증·교정 1회 공통 절차. 하위 클래스는 _chat 만 구현한다."""

    model = ""

    def _chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError

    def translate(self, request: ConversionRequest) -> TranslationResult:
        original = request.original.strip()
        if not original:
            raise TranslationError("번역할 일기가 비어 있습니다.")
        if len(request.original) > MAX_INPUT_CHARS:
            raise TranslationError(f"일기가 {MAX_INPUT_CHARS}자를 넘습니다.")
        if not self.model:
            raise TranslationError("번역 모델이 설정되지 않았습니다.")

        line_count = len(diary_lines(original))
        messages = build_messages(original, request.settings.style_id)
        content = self._chat(messages)
        try:
            return _result(content, self.model, line_count)
        except TranslationError as first:
            log.info("번역 형식 오류, 교정 1회 요청: %s", first)
        # 형식 오류에 한해 교정 요청 1회
        retry = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": retry_prompt(original)},
        ]
        return _result(self._chat(retry), self.model, line_count)


class OllamaTranslator(_JsonTranslator):
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: Transport = http_post_json,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._post = transport

    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "think": False,
            "options": {"temperature": 0.2},
        }
        resp = self._post(f"{self.base_url}/api/chat", payload, self.timeout)
        msg = resp.get("message") if isinstance(resp, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            raise TranslationError("번역 서버 응답에 message.content 가 없습니다.")
        return content


CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_SCHEMA = {**RESPONSE_SCHEMA, "additionalProperties": False}


class ClaudeTranslator(_JsonTranslator):
    """Anthropic Claude API 로 번역한다. 일기 원문이 Anthropic 서버로 전송된다."""

    def __init__(self, api_key: str, model: str = CLAUDE_MODEL, timeout: float = 60.0, client=None) -> None:
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._client = client

    def _chat(self, messages: list[dict[str, str]]) -> str:
        import anthropic

        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system,
                messages=convo,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA}},
            )
        except anthropic.AuthenticationError as exc:
            raise TranslationError("Claude API 키가 올바르지 않습니다. 환경설정에서 확인해 주세요.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise TranslationError("이 API 키로는 Claude 를 사용할 수 없습니다.") from exc
        except anthropic.RateLimitError as exc:
            raise TranslationError("Claude API 사용 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.") from exc
        except anthropic.APIStatusError as exc:
            raise TranslationError(f"Claude API 오류 ({exc.status_code})") from exc
        except anthropic.APIConnectionError as exc:
            raise TranslationError("Claude API 에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.") from exc

        if response.stop_reason == "refusal":
            raise TranslationError("Claude 가 이 일기의 번역을 거절했습니다.")
        if response.stop_reason == "max_tokens":
            raise TranslationError("번역 응답이 너무 길어 중간에 끊겼습니다.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not isinstance(text, str):
            raise TranslationError("Claude 응답에 번역 결과가 없습니다.")
        log.info("Claude 사용량: 입력 %s, 출력 %s 토큰", response.usage.input_tokens, response.usage.output_tokens)
        return text
