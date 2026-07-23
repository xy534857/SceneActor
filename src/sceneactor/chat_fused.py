"""Fused cognition+performance port for one-on-one chat.

The general runtime keeps cognition and performance as two model calls with a
Host arbitration in between, because physical actions can FAIL and the actor
must perform the arbitrated outcome, not the wish. Chat sessions have no
failable actions — capabilities are speak/wait/interact on an in-memory host
where every submit resolves instantly — so that mid-turn arbitration carries
no information. This port exploits exactly that: ONE model call per turn
produces {appraisal, policy, performance}; the performance section is cached
and replayed when the runtime asks for realization.

Safety properties preserved:
- Appraisal/policy validation is untouched (same _parse_cognition + validate).
- The performance section only contributes the OBSERVABLE fields; speech still
  comes exclusively from validated policy.disclose atoms, so the evidence
  whitelist for spoken words keeps working.
- Action text that restates authorized speech verbatim is rejected the same
  way JsonPerformancePort rejects it.
- If the model omits or corrupts the performance section, we degrade to a
  minimal neutral draft (speech intact, no invented staging) instead of
  burning a second model call: in chat the words carry the turn.

NOT for multi-actor scenes or hosts with failable actions: the fused prompt
writes the performance before the Host has resolved the action, which is only
sound when resolution is trivially always-success.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .cognition import JsonCognitionPort, _parse_cognition
from .contracts import (
    Appraisal,
    DecisionFrame,
    Delivery,
    PerformanceDraft,
    PerformancePolicy,
    PublicPerformanceIntent,
    ResolvedOutcome,
)
from .performance import PerformanceModelError, _action_text, _authorized_speech, _extract_object, _text

_PERFORMANCE_CONTRACT: Mapping[str, Any] = {
    "performance_keys": [
        "action", "attention_target", "gaze", "blocking",
        "posture_change", "delivery", "physical_residue",
    ],
    "delivery_keys": ["pace", "volume", "breath", "articulation", "pause", "vocal_target"],
    "performance_rule": (
        "performance describes only what a camera would see and a microphone would hear "
        "while the disclose atoms are spoken: visible movement, gaze, posture, audible "
        "delivery. Never restate the spoken words inside action; never name psychology "
        "labels; when speech is nonempty give at least one audible delivery direction."
    ),
}


class FusedChatPort(JsonCognitionPort):
    """One model call per turn: cognition contract + performance section.

    Implements the CognitionPort protocol via ``decide`` and the
    PerformancePort protocol via ``realize``. Register the same instance as
    both ports of a chat rehearsal.
    """

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str], *, max_attempts: int = 3) -> None:
        super().__init__(complete, max_attempts=max_attempts)
        self._staged: dict[str, Any] | None = None

    # -- cognition side ------------------------------------------------------

    def decide(self, frame: DecisionFrame) -> tuple[Appraisal, PerformancePolicy]:
        feedback = ""
        self._staged = None
        for _ in range(self.max_attempts):
            raw = self.complete(self._messages(frame, feedback), "cognition+performance")
            try:
                appraisal, policy = _parse_cognition(raw)
                appraisal.validate(frame)
                policy.validate(frame)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                feedback = (
                    f"Protocol validation failed: {exc}. Return a corrected JSON object; "
                    "keep the same top-level shape {appraisal, policy, performance}."
                )
                continue
            data = _extract_object(raw)
            staged = data.get("performance")
            self._staged = staged if isinstance(staged, Mapping) else None
            return appraisal, policy
        from .cognition import CognitionModelError

        raise CognitionModelError(feedback or "cognition output was invalid")

    def _messages(self, frame: DecisionFrame, feedback: str) -> list[dict[str, str]]:
        messages = super()._messages(frame, feedback)
        # extend the response contract: same call also returns the performance
        addendum = {
            "fused_response_extension": {
                "top_level_keys": ["appraisal", "policy", "performance"],
                **_PERFORMANCE_CONTRACT,
            }
        }
        messages.append({
            "role": "system",
            "content": (
                "FUSED MODE: this scene has no failable physical actions; your speak/wait "
                "action will resolve as submitted. Therefore return ONE JSON object with "
                "THREE top-level keys: appraisal, policy, AND performance — the observable "
                "staging of this same turn. " + json.dumps(addendum, ensure_ascii=False)
            ),
        })
        return messages

    # -- performance side ------------------------------------------------------

    def realize(
        self,
        intent: PublicPerformanceIntent,
        outcome: ResolvedOutcome,
        recent_history: tuple[dict[str, str], ...],
    ) -> PerformanceDraft:
        del recent_history
        staged = self._staged
        self._staged = None
        draft = self._draft_from(staged or {}, intent, outcome)
        draft.validate(intent, outcome)
        return draft

    def _draft_from(
        self,
        data: Mapping[str, Any],
        intent: PublicPerformanceIntent,
        outcome: ResolvedOutcome,
    ) -> PerformanceDraft:
        delivery_raw = data.get("delivery", {})
        if not isinstance(delivery_raw, Mapping):
            delivery_raw = {}
        try:
            action = _action_text(data, intent)
        except ValueError:
            action = ""  # drop speech-restating action instead of a second call
        speech = _authorized_speech(intent)
        delivery = Delivery(
            pace=_text(delivery_raw, "pace"), volume=_text(delivery_raw, "volume"),
            breath=_text(delivery_raw, "breath"), articulation=_text(delivery_raw, "articulation"),
            pause=_text(delivery_raw, "pause"), vocal_target=_text(delivery_raw, "vocal_target"),
        )
        if speech and not delivery.has_audible_direction():
            delivery = Delivery(pace="平常语速，直接对着对面说" if _looks_chinese(speech) else "even pace, straight at the visitor")
        draft = PerformanceDraft(
            actor_id=intent.actor_id,
            action=action,
            speech=speech,
            addressee=intent.target,
            attention_target=_text(data, "attention_target") or intent.target,
            gaze=_text(data, "gaze"),
            blocking=_text(data, "blocking"),
            posture_change=_text(data, "posture_change"),
            delivery=delivery,
            physical_residue=_text(data, "physical_residue"),
            observable_outcome=tuple(outcome.observable_facts),
            response_hook=intent.response_hook,
        )
        if not draft.action and not draft.speech:
            raise PerformanceModelError("performance must contain action or speech")
        return draft


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
