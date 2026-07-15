"""Model-backed performance realization.

This layer only turns a public intent and resolved outcome into an observable
PerformanceDraft. It never sees raw U or mutates world state.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .contracts import (
    Delivery,
    PerformanceDraft,
    PublicPerformanceIntent,
    ResolvedOutcome,
)


class PerformanceModelError(RuntimeError):
    """The model could not produce a contract-valid performance draft."""


class JsonPerformancePort:
    """Inject a JSON completion function; keep provider/model selection outside."""

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str], *, max_attempts: int = 3) -> None:
        self.complete = complete
        self.max_attempts = max_attempts

    def realize(
        self,
        intent: PublicPerformanceIntent,
        outcome: ResolvedOutcome,
        recent_history: tuple[dict[str, str], ...],
    ) -> PerformanceDraft:
        payload = {
            "intent": {
                "actor_id": intent.actor_id,
                "target": intent.target,
                "interaction_move": intent.interaction_move,
                "speech_atoms": [
                    {"kind": atom.kind, "text": atom.text}
                    for atom in intent.speech_atoms
                ],
                "delivery_mode": intent.delivery_mode,
                "visible_cost_signal": intent.visible_cost_signal,
                "response_hook": intent.response_hook,
                "disposition": intent.disposition,
                "evidence": intent.evidence_anchors,
            },
            "outcome": {
                "status": outcome.status,
                "action_kind": outcome.action_kind,
                "observable_facts": list(outcome.observable_facts),
                "changed_refs": list(outcome.changed_refs),
                "error": outcome.error,
            },
            "recent_surface": list(recent_history[-3:]),
        }
        feedback = ""
        for _ in range(self.max_attempts):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are the observable performance stage of one stateful NPC. Return exactly one JSON object "
                        "with action, speech, addressee, attention_target, gaze, blocking, posture_change, delivery, "
                        "physical_residue, observable_outcome, response_hook. Use only supplied facts and authorized actions. "
                        "Continue the person's existing physical task instead of attaching a symbolic gesture to every line. "
                        "Speech is locally sufficient for the person in front of them, not a complete explanation of the scene. "
                        "Allow interruption, self-correction, mis-timing, or an unfinished sentence when authorized content supports it. "
                        "If speech is nonempty, delivery must contain at least one externally audible direction among pace, volume, breath, articulation, pause, vocal_target, chosen to embody the authorized delivery mode without naming emotion. "
                        "Describe visible body and voice changes, never psychology labels. Preserve observable_outcome and response_hook exactly."
                    ),
                },
                {"role": "user", "content": json.dumps({**payload, "validation_feedback": feedback}, ensure_ascii=False)},
            ]
            raw = self.complete(messages, "realization")
            try:
                data = _extract_object(raw)
                delivery = data.get("delivery", {})
                if not isinstance(delivery, Mapping):
                    delivery = {}
                draft = PerformanceDraft(
                    actor_id=intent.actor_id,
                    action=_text(data, "action"),
                    speech=_text(data, "speech"),
                    addressee=_text(data, "addressee") or intent.target,
                    attention_target=_text(data, "attention_target"),
                    gaze=_text(data, "gaze"),
                    blocking=_text(data, "blocking"),
                    posture_change=_text(data, "posture_change"),
                    delivery=Delivery(
                        pace=_text(delivery, "pace"), volume=_text(delivery, "volume"),
                        breath=_text(delivery, "breath"), articulation=_text(delivery, "articulation"),
                        pause=_text(delivery, "pause"), vocal_target=_text(delivery, "vocal_target"),
                    ),
                    physical_residue=_text(data, "physical_residue"),
                    observable_outcome=tuple(_texts(data.get("observable_outcome"))),
                    response_hook=_text(data, "response_hook") or intent.response_hook,
                )
                if not draft.action and not draft.speech:
                    raise PerformanceModelError("performance must contain action or speech")
                draft.validate(intent, outcome)
                return draft
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, PerformanceModelError) as exc:
                feedback = f"Protocol validation failed: {exc}. Correct the JSON without changing intent, outcome, or scene facts."
        raise PerformanceModelError(feedback or "invalid performance JSON")


def _extract_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model output did not contain an object")


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _texts(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
