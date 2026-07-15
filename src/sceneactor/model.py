"""Provider-neutral model selection with explicit fallback telemetry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ChatCompletion(Protocol):
    def __call__(self, messages: list[dict[str, str]], purpose: str, model: str) -> str: ...


@dataclass(frozen=True)
class ModelAttempt:
    model: str
    purpose: str
    error: str = ""


class FallbackModel:
    """Try primary once, then fallback only for provider/transport failure."""

    def __init__(self, complete: ChatCompletion, primary: str = "owtr/gpt-5.6-sol", fallback: str = "owtr/gpt-5.6-luna") -> None:
        self.complete = complete
        self.primary = primary
        self.fallback = fallback
        self.attempts: list[ModelAttempt] = []

    def __call__(self, messages: list[dict[str, str]], purpose: str) -> str:
        self.attempts.clear()
        for model in (self.primary, self.fallback):
            try:
                result = self.complete(messages, purpose, model)
                self.attempts.append(ModelAttempt(model=model, purpose=purpose))
                return result
            except (TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
                self.attempts.append(ModelAttempt(model=model, purpose=purpose, error=str(exc)))
                continue
        raise RuntimeError(f"all configured models failed for {purpose}")


def configured_fallback(complete: ChatCompletion) -> FallbackModel:
    """Use OMP's current default pair unless the caller injects another pair."""
    return FallbackModel(complete)


class OpenAICompatibleCompletion:
    """Small dependency-free client for an injected OpenAI-compatible gateway."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 120.0) -> None:
        self.base_url = (base_url or os.getenv("SCENEACTOR_BASE_URL") or "http://127.0.0.1:8462/v1").rstrip("/")
        self.api_key = api_key or os.getenv("SCENEACTOR_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN", "")
        self.timeout = timeout

    def __call__(self, messages: list[dict[str, str]], purpose: str, model: str) -> str:
        del purpose
        payload = json.dumps({"model": model, "messages": messages, "temperature": 0.7}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"model gateway HTTP {exc.code}") from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"model gateway unavailable: {exc}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("model gateway returned unsupported response") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("model gateway returned empty content")
        return content
