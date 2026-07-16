"""Model-backed performance realization.

This layer only turns a public intent and resolved outcome into an observable
PerformanceDraft. It never sees raw U or mutates world state.
"""

from __future__ import annotations

import json
from dataclasses import replace
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
                "interaction_move": intent.interaction_move,
                "speech_atoms": [
                    {"kind": atom.kind, "text": atom.text}
                    for atom in intent.speech_atoms
                ],
                "delivery_mode": intent.delivery_mode,
                "visible_cost_signal": intent.visible_cost_signal,
                "response_hook": intent.response_hook,
                "disposition": intent.disposition,
                "actor_constraints": dict(intent.actor_constraints),
            },
            "public_evidence": dict(intent.public_evidence),
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
        previous_invalid_output = ""
        for attempt in range(1, self.max_attempts + 1):
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are the observable performance stage of one stateful NPC. Return exactly one JSON object "
                        "with action, attention_target, gaze, blocking, posture_change, delivery, physical_residue, observable_outcome, response_hook. "
                        "The words to be spoken are already supplied under intent.speech_atoms. Return no speech field and do not alter, summarize, interrupt, or answer those words. "
                        "Actor and listener identifiers are resolved outside this call; never turn an identifier into visible text. Use only supplied public intent, public history, facts, and authorized actions. "
                        "Write only the locally visible action around those exact words; continue an existing physical task instead of attaching a symbolic gesture to every line. "
                        "Action, blocking, posture_change, and physical_residue must describe one physically compatible simultaneous end state. "
                        "When several limbs differ, identify left/right or one/the other; never say both arms are down while a hand or finger remains at an object. "
                        "Only public_evidence, recent_surface, and outcome are established public context. Do not invent narrator-known props, measurements, names, or prior actions; introduce a currently visible object without words such as still, again, continue, or no longer. "
                        "Respect actor_constraints: never assign biological breathing, tears, pulse, or other human mechanisms to a nonhuman actor unless those constraints explicitly support them. "
                        "If speech is nonempty, delivery must contain at least one externally audible direction among pace, volume, breath, articulation, pause, vocal_target, chosen to embody the authorized delivery mode without naming emotion. "
                        "Describe visible body and voice changes, never psychology labels. Preserve observable_outcome and response_hook exactly."
                    ),
                },
                {"role": "user", "content": json.dumps({
                    **payload,
                    "repair_attempt": attempt,
                    "validation_feedback": feedback,
                    "previous_invalid_output": previous_invalid_output,
                }, ensure_ascii=False)},
            ]
            raw = self.complete(messages, "realization")
            draft: PerformanceDraft | None = None
            try:
                data = _extract_object(raw)
                delivery = data.get("delivery", {})
                if not isinstance(delivery, Mapping):
                    delivery = {}
                draft = PerformanceDraft(
                    actor_id=intent.actor_id,
                    action=_text(data, "action"),
                    speech=_authorized_speech(intent),
                    addressee=intent.target,
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
                feedback = (
                    f"Protocol validation failed on repair attempt {attempt}: {exc}. "
                    "Repair the previous JSON in place. Preserve intent, outcome, response_hook, and scene facts; "
                    "return every required field, and when speech is nonempty provide at least one audible delivery direction."
                )
                if draft is not None and draft.speech and not draft.delivery.has_audible_direction():
                    repaired_delivery = self._repair_delivery(draft, intent)
                    if repaired_delivery is not None:
                        repaired = replace(draft, delivery=repaired_delivery)
                        repaired.validate(intent, outcome)
                        return repaired
                previous_invalid_output = raw
        raise PerformanceModelError(feedback or "invalid performance JSON")

    def _repair_delivery(self, draft: PerformanceDraft, intent: PublicPerformanceIntent) -> Delivery | None:
        """Repair only a missing delivery object while freezing the accepted performance."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Return exactly one JSON object with keys pace, volume, breath, articulation, pause, vocal_target. "
                    "Supply at least one externally audible acting direction for the fixed speech. "
                    "Do not rewrite the speech, action, facts, or name a psychology label. "
                    "Do not assign breathing, tears, pulse, or any biological mechanism unless identity evidence explicitly supports it."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "speech": draft.speech,
                    "action": draft.action,
                    "addressee": draft.addressee,
                    "delivery_mode": intent.delivery_mode,
                    "visible_cost_signal": intent.visible_cost_signal,
                    "actor_constraints": dict(intent.actor_constraints),
                }, ensure_ascii=False),
            },
        ]
        try:
            data = _extract_object(self.complete(messages, "delivery_repair"))
            delivery = Delivery(
                pace=_text(data, "pace"), volume=_text(data, "volume"),
                breath=_text(data, "breath"), articulation=_text(data, "articulation"),
                pause=_text(data, "pause"), vocal_target=_text(data, "vocal_target"),
            )
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return delivery if delivery.has_audible_direction() else None


def _authorized_speech(intent: PublicPerformanceIntent) -> str:
    return "".join(atom.text.strip() for atom in intent.speech_atoms if atom.text.strip())

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
