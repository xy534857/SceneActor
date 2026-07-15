"""Provider-neutral model selection with explicit fallback telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


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
