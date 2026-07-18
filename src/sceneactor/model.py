"""Provider-neutral model selection with explicit fallback telemetry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
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


class OmpCliCompletion:
    """Invoke the configured OMP provider/model registry in noninteractive mode."""

    def __init__(self, executable: str = "omp", thinking: str = "high", timeout: float = 180.0) -> None:
        self.executable = executable
        self.thinking = thinking
        self.timeout = timeout

    def __call__(self, messages: list[dict[str, str]], purpose: str, model: str) -> str:
        del purpose
        system = "\n\n".join(item["content"] for item in messages if item.get("role") == "system")
        conversation = "\n\n".join(
            f"{item.get('role', 'user').upper()}: {item.get('content', '')}"
            for item in messages if item.get("role") != "system"
        )
        command = [
            self.executable,
            "-p",
            "--no-tools",
            "--no-session",
            "--no-title",
            "--model",
            model,
            "--thinking",
            self.thinking,
        ]
        if system:
            command.extend(("--system-prompt", system))
        command.append(conversation)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"OMP model invocation failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"OMP model invocation failed ({completed.returncode}): {detail[:500]}")
        if not completed.stdout.strip():
            raise RuntimeError("OMP model invocation returned empty content")
        return completed.stdout.strip()


class GatewayCompletion:
    """Call an OpenAI-compatible chat completions gateway directly over HTTP."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        *,
        timeout: float = 300.0,
        max_tokens: int = 32768,
    ) -> None:
        self.base_url = (base_url or os.environ.get("SCENEACTOR_GATEWAY_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("SCENEACTOR_GATEWAY_KEY", "")
        if not self.base_url or not self.api_key:
            raise ValueError("gateway base_url and api_key are required")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def __call__(self, messages: list[dict[str, str]], purpose: str, model: str) -> str:
        del purpose
        payload = json.dumps(
            {"model": model, "messages": messages, "max_tokens": self.max_tokens}
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500] if exc.fp else str(exc)
            raise RuntimeError(f"gateway completion failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"gateway completion failed: {exc}") from exc
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not content or not str(content).strip():
            raise RuntimeError("gateway completion returned empty content")
        return str(content).strip()
