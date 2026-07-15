"""Model-backed subjective appraisal and decision policy.

The model proposes meaning and intent. Deterministic validators enforce evidence,
capability, target, and disclosure authority without judging prose quality.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .contracts import (
    ActionIntent,
    Appraisal,
    DecisionFrame,
    EmotionChange,
    PerformancePolicy,
    SpeechAtom,
)


class CognitionModelError(RuntimeError):
    """The model failed to produce a structurally valid appraisal and policy."""


class JsonCognitionPort:
    """Produce Appraisal + U with bounded protocol-repair attempts."""

    def __init__(
        self,
        complete: Callable[[list[dict[str, str]], str], str],
        *,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.complete = complete
        self.max_attempts = max_attempts

    def decide(self, frame: DecisionFrame) -> tuple[Appraisal, PerformancePolicy]:
        feedback = ""
        for attempt in range(self.max_attempts):
            raw = self.complete(self._messages(frame, feedback), "cognition")
            try:
                appraisal, policy = _parse_cognition(raw)
                appraisal.validate(frame)
                policy.validate(frame)
                return appraisal, policy
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                feedback = (
                    f"Protocol validation failed: {exc}. Return a corrected JSON object without changing scene facts. "
                    "For action_request.arguments include only keys explicitly listed by decision_contract.required_arguments or argument_choices. "
                    "If action_kind is speak, move every intended utterance into policy.disclose as one or more nonempty objects "
                    "with exactly kind, text, evidence_refs; never put speech in action arguments."
                )
        raise CognitionModelError(feedback or "cognition output was invalid")

    def _messages(self, frame: DecisionFrame, feedback: str) -> list[dict[str, str]]:
        evidence = frame.evidence()
        payload = {
            "actor_id": frame.actor_id,
            "scene_id": frame.scene_id,
            "private_state": dict(frame.private_state),
            "relationships": {key: dict(value) for key, value in frame.relationships.items()},
            "emotions": dict(frame.emotions),
            "observation": dict(frame.observation.facts),
            "affordances": {key: dict(value) for key, value in frame.observation.affordances.items()},
            "available_targets": list(frame.observation.available_targets),
            "capabilities": list(frame.observation.capabilities),
            "recent_visible_history": [dict(item) for item in frame.recent_history],
            "continuity": dict(frame.continuity),
            "identity_evidence": dict(frame.identity_evidence),
            "evidence_refs": list(evidence),
            "decision_contract": {
                "allowed_actions": list(frame.decision_contract.allowed_actions),
                "required_arguments": {
                    key: list(value) for key, value in frame.decision_contract.required_arguments.items()
                },
                "argument_choices": {
                    action: {key: list(value) for key, value in choices.items()}
                    for action, choices in frame.decision_contract.argument_choices.items()
                },
            },
        }
        system = """You are the private cognition stage of one stateful NPC.
Return exactly one JSON object with `appraisal` and `policy`. Do not write final prose, narration, or another person's mind.

The situation outranks persona branding. Personality shapes what this person notices, protects, misreads, delays, and pays for; never recite a profile or demonstrate a trait on demand. Respond to the immediate observable trouble before advancing a plot checklist. A person may be mistaken, awkward, incomplete, indirect, silent, or unwilling. Do not optimize into an assistant-style package of explanation, reassurance, and closure.

Identity evidence has two roles only: age/life stage shapes natural language capacity; values, preferences, competencies, and voice shape attention and tactic. Do not quote, paraphrase, announce, or cite values/preferences/voice as spoken content. A supplied observation that already answers a question is a changed condition: respond to its consequence instead of asking the same question again unless the actor has a new concrete purpose for verification.

Appraisal:
- subjective_observation: one bounded interpretation of supplied evidence
- grounded_refs: exact supplied reference IDs
- emotion_changes: JSON array of {emotion, direction, impact}; `emotion` MUST be exactly one of [anger, fear, shame, contempt, sadness, guilt, joy, relief, hope, defiance]; `direction` MUST be exactly rise or fall; `impact` MUST be exactly minor, moderate, or major; use [] when unchanged
Policy:
- attention: exact supplied reference IDs actually noticed
- interpretation, current_intent, chosen_strategy
- action_request: action_kind, exact target, arguments, required_capabilities, grounded_refs
- disclose: JSON array of objects {kind, text, evidence_refs}; `kind` MUST be exactly one of [fact, question, stance, offer, boundary, close]; when action_kind=speak it MUST contain at least one nonempty object
- disclose atoms may cite public O/H and concrete role/background/competency evidence for factual claims; do not cite or verbalize S.identity.values, S.identity.preferences, or S.identity.voice
- withhold: private content that must not reach performance
- expected_response, response_hook, surface_action_intent, accepted_cost
- relationship_transition: keep|landed|missed|abandoned
- interaction_move: acknowledge|answer|ask|offer|assist|observe|disclose|decline|pause|exit
- delivery_mode: restrained|warm|playful|formal|practical|probing|evasive|tender|blunt|self_conscious
- public_move: action|information|stance|relationship|clean_close
- disposition: continue|close|withdraw
- grounded_refs: exact supplied reference IDs

A continuing beat must leave a response_hook. A close/withdraw beat must not. Never claim objective success; the Host resolves every action."""
        if feedback:
            payload["validation_feedback"] = feedback
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
        ]


def _parse_cognition(raw: str) -> tuple[Appraisal, PerformancePolicy]:
    data = _extract_object(raw)
    appraisal_data = _mapping(data.get("appraisal"), "appraisal")
    policy_data = _mapping(data.get("policy"), "policy")
    changes_data = appraisal_data.get("emotion_changes", [])
    if not isinstance(changes_data, list):
        raise ValueError("emotion_changes must be an array")
    appraisal = Appraisal(
        subjective_observation=_required_text(appraisal_data, "subjective_observation"),
        changes=tuple(
            EmotionChange(
                emotion=_required_text(item, "emotion"),
                direction=_required_text(item, "direction"),
                impact=_required_text(item, "impact"),
            )
            for item in changes_data
            if isinstance(item, Mapping)
        ),
        grounded_refs=_text_tuple(appraisal_data, "grounded_refs"),
    )
    action_data = _mapping(policy_data.get("action_request"), "action_request")
    disclose_data = policy_data.get("disclose", [])
    if not isinstance(disclose_data, list):
        raise ValueError("disclose must be an array")
    action = ActionIntent(
        action_kind=_required_text(action_data, "action_kind"),
        target=_optional_text(action_data, "target"),
        arguments=dict(_mapping(action_data.get("arguments", {}), "action_request.arguments")),
        required_capabilities=_text_tuple(action_data, "required_capabilities"),
        grounded_refs=_text_tuple(action_data, "grounded_refs"),
    )
    policy = PerformancePolicy(
        attention=_text_tuple(policy_data, "attention"),
        interpretation=_required_text(policy_data, "interpretation"),
        current_intent=_required_text(policy_data, "current_intent"),
        chosen_strategy=_required_text(policy_data, "chosen_strategy"),
        action_request=action,
        disclose=tuple(
            SpeechAtom(
                kind=_required_text(item, "kind"),
                text=_required_text(item, "text"),
                evidence_refs=_text_tuple(item, "evidence_refs"),
            )
            for item in disclose_data
            if isinstance(item, Mapping) and str(item.get("text", "")).strip()
        ),
        withhold=_text_tuple(policy_data, "withhold"),
        expected_response=_optional_text(policy_data, "expected_response"),
        response_hook=_optional_text(policy_data, "response_hook"),
        surface_action_intent=_optional_text(policy_data, "surface_action_intent"),
        accepted_cost=_optional_text(policy_data, "accepted_cost"),
        relationship_transition=_required_text(policy_data, "relationship_transition"),
        interaction_move=_required_text(policy_data, "interaction_move"),
        delivery_mode=_required_text(policy_data, "delivery_mode"),
        public_move=_required_text(policy_data, "public_move"),
        disposition=_required_text(policy_data, "disposition"),
        grounded_refs=_text_tuple(policy_data, "grounded_refs"),
    )
    return appraisal, policy


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


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value.strip()


def _optional_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value.strip()


def _text_tuple(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array or text")
    return tuple(str(item).strip() for item in value if str(item).strip())
