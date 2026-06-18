from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .session import LLMUnavailableError, SentenceGenerationError
except ImportError:  # pragma: no cover
    from session import LLMUnavailableError, SentenceGenerationError


def resolve_chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return urljoin(f"{normalized}/", "v1/chat/completions")


def _extract_message_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise SentenceGenerationError("LLM response has no choices.")
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        joined = "".join(text_parts).strip()
        if joined:
            return joined
    raise SentenceGenerationError("LLM response content was not text.")


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return stripped


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.strip()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "OpenAICompatibleClient":
        llm_config = config.get("llm", {})
        return cls(
            base_url=llm_config.get("base_url", ""),
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
        )

    def generate_sentence(self, prompt: str) -> dict[str, Any]:
        if not self.base_url or not self.model:
            raise LLMUnavailableError("LLM base_url and model must be configured.")

        request_body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        data = json.dumps(request_body).encode("utf-8")
        request = Request(
            resolve_chat_completions_url(self.base_url),
            data=data,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMUnavailableError(f"LLM request failed: HTTP {exc.code} - {body}") from exc
        except URLError as exc:
            raise LLMUnavailableError(f"LLM request failed: {exc.reason}") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"LLM request failed: {exc}") from exc

        content = _extract_message_content(payload)
        content = _strip_code_fences(content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SentenceGenerationError("LLM returned malformed JSON.") from exc
        if not isinstance(parsed, dict):
            raise SentenceGenerationError("LLM JSON payload was not an object.")
        return parsed
