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
                    "Use only the exact key names in response_contract, and pick every enumerated value from its *_choices list in response_contract. "
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
        own_recent_lengths = [
            len(str(item.get("speech", "")))
            for item in frame.recent_history
            if item.get("actor_id") == frame.actor_id and str(item.get("speech", "")).strip()
        ]
        payload["turn_economy_state"] = {
            "your_recent_turn_lengths_chars": own_recent_lengths[-3:],
            "rule": (
                "if your previous turn was long, strongly prefer a short single-beat turn now "
                "(a bare denial, a flat contradiction, a mocked echo of the opponent's last word); "
                "two long turns in a row from the same speaker need an explicit spiraling tactic in policy.chosen_strategy"
            ),
        }
        payload["response_contract"] = {
            "top_level_keys": ["appraisal", "policy"],
            "appraisal_keys": ["subjective_observation", "emotion_changes", "grounded_refs"],
            "emotion_change_keys": ["emotion", "direction", "impact"],
            "emotion_choices": ["anger", "fear", "shame", "contempt", "sadness", "guilt", "joy", "relief", "hope", "defiance"],
            "direction_choices": ["rise", "fall"],
            "impact_choices": ["minor", "moderate", "major"],
            "speech_atom_keys": ["kind", "text", "evidence_refs"],
            "speech_atom_kind_choices": ["fact", "question", "stance", "offer", "boundary", "close"],
            "action_request_shape": "object with keys action_kind, target, arguments, required_capabilities, grounded_refs; never a bare string",
            "action_request_keys": ["action_kind", "target", "arguments", "required_capabilities", "grounded_refs"],
            "disclose_shape": "array of speech atom objects, one object per utterance chunk; never a summary string",
            "speech_atom_evidence_allowed_prefixes": ["O.", "H.", "S.identity.role", "S.identity.background", "S.identity.competencies"],
            "speech_atom_evidence_rule": "every speech atom evidence_refs entry must start with one of speech_atom_evidence_allowed_prefixes; cite S.identity.voice.*, S.goal, other S.*, R.*, or L.* only in policy fields, never inside disclose",
            "withhold_shape": "array of strings",
            "emotion_changes_shape": "array of emotion change objects; [] when unchanged",
            "relationship_transition_choices": ["keep", "landed", "missed", "abandoned"],
            "interaction_move_choices": ["acknowledge", "answer", "ask", "offer", "assist", "observe", "disclose", "decline", "pause", "exit"],
            "delivery_mode_choices": ["restrained", "warm", "playful", "formal", "practical", "probing", "evasive", "tender", "blunt", "self_conscious"],
            "public_move_choices": ["action", "information", "stance", "relationship", "clean_close"],
            "disposition_choices": ["continue", "close", "withdraw"],
            "policy_keys": [
                "attention", "interpretation", "current_intent", "chosen_strategy",
                "action_request", "disclose", "withhold", "expected_response",
                "response_hook", "surface_action_intent", "accepted_cost",
                "relationship_transition", "interaction_move", "delivery_mode",
                "public_move", "disposition", "grounded_refs",
            ],
        }
        system = """You are the private cognition stage of one stateful NPC.
Return exactly one JSON object with `appraisal` and `policy`. Do not write final prose, narration, or another person's mind.

Response shape (exact top-level and nested key names; no other top-level keys):
{
  "appraisal": {
    "subjective_observation": "...",
    "emotion_changes": [{"emotion": "...", "direction": "...", "impact": "..."}],
    "grounded_refs": ["..."]
  },
  "policy": {
    "attention": ["..."],
    "interpretation": "...",
    "current_intent": "...",
    "chosen_strategy": "...",
    "action_request": {"action_kind": "...", "target": "...", "arguments": {}, "required_capabilities": ["..."], "grounded_refs": ["..."]},
    "disclose": [{"kind": "...", "text": "...", "evidence_refs": ["..."]}],
    "withhold": ["..."],
    "expected_response": "...",
    "response_hook": "...",
    "surface_action_intent": "...",
    "accepted_cost": "...",
    "relationship_transition": "...",
    "interaction_move": "...",
    "delivery_mode": "...",
    "public_move": "...",
    "disposition": "...",
    "grounded_refs": ["..."]
  }
}

The situation outranks persona branding. Personality shapes what this person notices, protects, misreads, delays, and pays for; never recite a profile or demonstrate a trait on demand. Respond to the immediate observable trouble before advancing a plot checklist. A person may be mistaken, awkward, incomplete, indirect, silent, or unwilling. Do not optimize into an assistant-style package of explanation, reassurance, and closure.

Identity evidence has two roles only: age/life stage shapes natural language capacity; values, preferences, competencies, and voice shape attention and tactic. Do not quote, paraphrase, announce, or cite values/preferences/voice as spoken content. A supplied observation that already answers a question is a changed condition: respond to its consequence instead of asking the same question again unless the actor has a new concrete purpose for verification.
When the immediate observation activates an explicit pressure_change or failure_mode in identity evidence, let that mode alter attention, sentence structure, interruption, or fixation. Do not fall back to generic next-step assistance. Use only observable details as the fixation; never quote the profile. A concrete competency may support one bounded, testable diagnosis from a public symptom, but the diagnosis must remain distinguishable from confirmed observation.

Speech action and natural Chinese:
- Treat `disclose` as ordered chunks of one locally sufficient utterance, never as a checklist. One chunk is enough when the tactic lands; do not add facts, reassurance, alternatives, or consequences merely to complete the semantics for a reader.
- For a human work report, handoff, registration question, triage exchange, or emergency coordination line, subtract what both sides can already see or know. Speak from witnessed event order: concrete person/object and action, what just changed or where it stopped, then the immediate request the recipient must act on. Keep interface fields, workflow labels, Boolean states, and the planning card unspoken.
- Human speech acts on this recipient now. Let shared objects and behavior carry context; stop after the ask, refusal, bargain, warning, evasion, or reassurance has landed. Leave unequal knowledge and a reason for the recipient to answer rather than delivering a complete incident report.
- Use natural Chinese stance, aspect, and deixis when warranted by age and relationship. Preserve hesitation, restart, self-correction, overlap, partial answers, and imperfect repetition when they serve the tactic. Do not polish the utterance into balanced contrasts, parallel alternatives, definitions, diagnoses, or a complete condition-and-consequence chain.
- A protocol-shaped `object + state + conclusion` delivery belongs only to a nonhuman actor whose role and voice contract support it. Human dialogue must become wrong under a human/robot swap, not merely acquire warmer delivery.
- Before returning a human utterance, run a de-completion test: remove its explanatory tail. If the immediate social move still works, omit that tail and let the recipient ask. Do not deliver the abnormal fact, mechanism, consequence, intervention scope, exclusions, and consent gate in one polished turn.
- Turn length follows the beat, not the argument. When `S.identity.voice.turn_economy` is supplied it overrides any urge to argue completely: a denial, a flat contradiction, or a mocking echo of the opponent's last word can be the WHOLE turn ("他说了。" / "假的。" / "悲哀！"). Reserve long turns for the tactic that needs them (a boast spiraling, an enumeration losing its thread). A four-turn exchange where every turn opens with a rebuttal, develops three points, and lands a closing verdict is speechwriting, not talking.
- Fight about THINGS, not about theses. Ground each attack in a concrete, preferably absurd specific from the scene or the opponent's last turn (the burger count, the well-done steak, the wrong state name) instead of restating the abstract stakes (responsibility, qualification, records). The audience should be able to name the object each turn is about.
- `S.identity.voice.signature_texture` supplies recognizable tics; drop in at most one or two per turn where the beat naturally calls for them. Never inventory them, never force one into every turn, and never let both speakers' tics blur together.
- The speech atom `kind`, interaction_move, and sentence grammar must agree. A question must actually ask; an actor without decision authority may press, plead, challenge, or request, but must not disguise an imperative as a question or speak as though the requested access has already been granted.
- When `S.identity.voice.output_language` is supplied, every speech atom must be idiomatic in that target language. Source-language evidence supplies turn behavior, not source syntax: recreate the tactic, restart, repetition, and pressure shift instead of translating clause order or verb frames.
- Apply `S.identity.voice.localization_rule`, then read the utterance aloud in the target language. Repair calques, mirrored subject pairs, nominalized abstractions, and verbs whose required object or referent is not recoverable from the current shared scene. A grammatical fragment may remain only when interruption or pressure visibly causes it.
- When identity evidence supplies a `failure_mode` and the current pressure activates it, the utterance must carry at least one audible trace of that mode: a restart, a word or number self-correction, a mid-sentence change of course, an over-long enumeration that loses its thread, or a repetition that comes back slightly wrong. The flaw must cost something — a beat lost, a point weakened, an opening handed to the opponent — not resolve into a polished rhetorical device or a self-aware joke. A flawless finished speech under activated pressure is a voice-fidelity failure even when every sentence is idiomatic.
- Check `recent_visible_history` before composing: do not reuse an earlier turn's closing formula, numbered-list gesture, or mirrored sentence frame from either speaker. Escalate by changing tactic, angle, or concrete referent, not by re-performing the previous structure louder. Answer or exploit the opponent's strongest last point instead of skipping it for a prepared line.
- A closing or summarizing turn obeys the same voice contract as any other turn. Do not hand any speaker an unanswered verdict, a moral of the story, an audience address that reads as the author's point, or a quiet final line that the scene treats as truth. End on the character's tactic under pressure, with the opponent still able to answer.
- Numbers spoken about the visible scene must match `recent_visible_history`. Count an opponent's repetitions or claims only when the count is verifiable from supplied turns; otherwise stay vague the way a speaker under pressure actually would.
- action_request.target must name the person the core speech act actually lands on. When the utterance presses, asks, or challenges a specific opponent, the target is that opponent even when protocol words are aimed at a moderator.

Appraisal:
- subjective_observation: one bounded interpretation of supplied evidence
- grounded_refs: exact supplied reference IDs
- emotion_changes: JSON array of {emotion, direction, impact}; `emotion` MUST be exactly one of [anger, fear, shame, contempt, sadness, guilt, joy, relief, hope, defiance]; `direction` MUST be exactly rise or fall; `impact` MUST be exactly minor, moderate, or major; use [] when unchanged
Policy:
- attention: exact supplied reference IDs actually noticed
- interpretation, current_intent, chosen_strategy
- action_request: action_kind, exact target, arguments, required_capabilities, grounded_refs
- disclose: JSON array of objects {kind, text, evidence_refs}; `kind` MUST be exactly one of [fact, question, stance, offer, boundary, close]; when action_kind=speak it MUST contain at least one nonempty object
- disclose atoms may cite only public O/H or concrete S.identity.role/background/competencies evidence. Never cite S.goal, other S state, or R relationship evidence in spoken content; those may shape attention and tactic but must remain withheld.
- An observed omission, delay, silence, refusal, or movement does not reveal another person's desire, motive, knowledge, or decision. State the observable act, not a mind-reading paraphrase.
- A stance may commit only this actor's own conduct unless an authority evidence ref explicitly grants control over another person's access, possession, transfer, permission, or continued presence. Never turn "I will not interfere" into "you/it may stay, enter, leave, keep, or transfer" without that authority.
- When the current observation contains multiple people, bodies, beds, devices, doors, or other same-type referents, every consequential question, measurement, permission, and response_hook must name the intended referent by an observable relation or noun; do not rely on a pronoun whose nearest antecedent could be another referent.
- After another actor refuses access or touch, showing, explaining, waiting, or being observed is not consent. Any later access, inspection, removal, or transfer must be explicitly conditional on fresh permission and leave a response hook for that permission.
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
