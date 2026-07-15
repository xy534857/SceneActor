"""Host-independent SceneActor contracts and structural validation.

This module validates authority, evidence, ownership, and protocol shape. It does
not judge whether prose is human, dramatic, distinctive, or emotionally good.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .persona import Persona

EMOTIONS = (
    "anger",
    "fear",
    "shame",
    "contempt",
    "sadness",
    "guilt",
    "joy",
    "relief",
    "hope",
    "defiance",
)
IMPACTS = ("minor", "moderate", "major")
DIRECTIONS = ("rise", "fall")
INTERACTION_MOVES = (
    "acknowledge",
    "answer",
    "ask",
    "offer",
    "assist",
    "observe",
    "disclose",
    "decline",
    "pause",
    "exit",
)
DELIVERY_MODES = (
    "restrained",
    "warm",
    "playful",
    "formal",
    "practical",
    "probing",
    "evasive",
    "tender",
    "blunt",
    "self_conscious",
)
DISPOSITIONS = ("continue", "close", "withdraw")
PUBLIC_MOVES = ("action", "information", "stance", "relationship", "clean_close")
SPEECH_KINDS = ("fact", "question", "stance", "offer", "boundary", "close")


@dataclass(frozen=True)
class ObservationView:
    """The objective Host projection visible to exactly one actor."""

    facts: Mapping[str, Any]
    affordances: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    available_targets: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    scene_revision: int = 0

    def __post_init__(self) -> None:
        if self.scene_revision < 0:
            raise ValueError("scene revision must be non-negative")


@dataclass(frozen=True)
class DecisionContract:
    allowed_actions: tuple[str, ...]
    required_arguments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    argument_choices: Mapping[str, Mapping[str, tuple[Any, ...]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.allowed_actions:
            raise ValueError("decision contract requires allowed actions")


@dataclass(frozen=True)
class DecisionFrame:
    scene_id: str
    turn_id: str
    actor_id: str
    private_state: Mapping[str, Any]
    relationships: Mapping[str, Mapping[str, Any]]
    emotions: Mapping[str, float]
    observation: ObservationView
    recent_history: tuple[Mapping[str, str], ...]
    continuity: Mapping[str, Any]
    decision_contract: DecisionContract
    identity_evidence: Mapping[str, Any] = field(default_factory=dict)

    def evidence(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        _flatten("S", self.private_state, result)
        _flatten("R", self.relationships, result)
        _flatten("E", self.emotions, result)
        _flatten("O", self.observation.facts, result)
        _flatten("L", self.continuity, result)
        _flatten("S.identity", self.identity_evidence, result)
        for index, item in enumerate(self.recent_history):
            result[f"H.{index}"] = dict(item)
        return result


@dataclass(frozen=True)
class EmotionChange:
    emotion: str
    direction: str
    impact: str

    def __post_init__(self) -> None:
        if self.emotion not in EMOTIONS:
            raise ValueError(f"unknown emotion: {self.emotion}")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"invalid emotion direction: {self.direction}")
        if self.impact not in IMPACTS:
            raise ValueError(f"invalid emotion impact: {self.impact}")


@dataclass(frozen=True)
class Appraisal:
    subjective_observation: str
    changes: tuple[EmotionChange, ...]
    grounded_refs: tuple[str, ...]

    def validate(self, frame: DecisionFrame) -> None:
        _require_refs(self.grounded_refs, frame.evidence(), "appraisal")


@dataclass(frozen=True)
class ActionIntent:
    action_kind: str
    target: str
    arguments: Mapping[str, Any]
    required_capabilities: tuple[str, ...]
    grounded_refs: tuple[str, ...]

    def validate(self, frame: DecisionFrame) -> None:
        contract = frame.decision_contract
        if self.action_kind not in contract.allowed_actions:
            raise ValueError(f"action is not allowed: {self.action_kind}")
        if self.target and self.target not in frame.observation.available_targets:
            raise ValueError(f"target is not available: {self.target}")
        missing_capabilities = set(self.required_capabilities) - set(frame.observation.capabilities)
        if missing_capabilities:
            raise ValueError(f"missing host capabilities: {sorted(missing_capabilities)}")
        missing_arguments = [
            key
            for key in contract.required_arguments.get(self.action_kind, ())
            if self.arguments.get(key) in (None, "", [], {})
        ]
        if missing_arguments:
            raise ValueError(f"missing action arguments: {missing_arguments}")
        for key, choices in contract.argument_choices.get(self.action_kind, {}).items():
            if self.arguments.get(key) not in choices:
                raise ValueError(f"invalid action argument: {key}")
        allowed_arguments = set(contract.required_arguments.get(self.action_kind, ()))
        allowed_arguments.update(contract.argument_choices.get(self.action_kind, {}))
        unknown_arguments = set(self.arguments) - allowed_arguments
        if unknown_arguments:
            raise ValueError(f"unsupported action arguments: {sorted(unknown_arguments)}")
        _require_refs(self.grounded_refs, frame.evidence(), "action")


@dataclass(frozen=True)
class SpeechAtom:
    kind: str
    text: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in SPEECH_KINDS:
            raise ValueError(f"invalid speech atom kind: {self.kind}")
        if not self.text.strip():
            raise ValueError("speech atom text is required")
        if self.kind == "fact" and not self.evidence_refs:
            raise ValueError("fact speech atom requires evidence refs")


@dataclass(frozen=True)
class PerformancePolicy:
    attention: tuple[str, ...]
    interpretation: str
    current_intent: str
    chosen_strategy: str
    action_request: ActionIntent
    disclose: tuple[SpeechAtom, ...]
    withhold: tuple[str, ...]
    expected_response: str
    response_hook: str
    surface_action_intent: str
    accepted_cost: str
    relationship_transition: str
    interaction_move: str
    delivery_mode: str
    public_move: str
    disposition: str
    grounded_refs: tuple[str, ...]

    def validate(self, frame: DecisionFrame) -> None:
        self.action_request.validate(frame)
        evidence = frame.evidence()
        _require_refs(self.grounded_refs, evidence, "policy")
        for atom in self.disclose:
            _require_refs(atom.evidence_refs, evidence, "speech atom")
        if self.action_request.action_kind == "speak" and not self.disclose:
            raise ValueError("speak action requires at least one speech atom")
        if self.interaction_move not in INTERACTION_MOVES:
            raise ValueError(f"invalid interaction move: {self.interaction_move}")
        if self.delivery_mode not in DELIVERY_MODES:
            raise ValueError(f"invalid delivery mode: {self.delivery_mode}")
        if self.public_move not in PUBLIC_MOVES:
            raise ValueError(f"invalid public move: {self.public_move}")
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"invalid disposition: {self.disposition}")
        if self.disposition == "continue" and not self.response_hook.strip():
            raise ValueError("continuing policy requires a concrete response hook")
        if self.disposition != "continue" and self.response_hook.strip():
            raise ValueError("terminal policy cannot create a response hook")
        if self.disposition == "withdraw" and self.action_request.action_kind != "exit":
            raise ValueError("withdraw disposition requires an exit action")


@dataclass(frozen=True)
class PublicPerformanceIntent:
    actor_id: str
    target: str
    interaction_move: str
    authorized_action: ActionIntent
    speech_atoms: tuple[SpeechAtom, ...]
    delivery_mode: str
    visible_cost_signal: str
    response_hook: str
    disposition: str
    evidence_anchors: Mapping[str, Any]

    @classmethod
    def from_policy(
        cls,
        actor_id: str,
        policy: PerformancePolicy,
        frame: DecisionFrame,
    ) -> "PublicPerformanceIntent":
        policy.validate(frame)
        evidence = frame.evidence()
        refs = set(policy.action_request.grounded_refs)
        refs.update(policy.grounded_refs)
        refs.update(ref for atom in policy.disclose for ref in atom.evidence_refs)
        anchors = {reference: evidence[reference] for reference in sorted(refs)}
        return cls(
            actor_id=actor_id,
            target=policy.action_request.target,
            interaction_move=policy.interaction_move,
            authorized_action=policy.action_request,
            speech_atoms=policy.disclose,
            delivery_mode=policy.delivery_mode,
            visible_cost_signal=policy.accepted_cost,
            response_hook=policy.response_hook,
            disposition=policy.disposition,
            evidence_anchors=anchors,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "target": self.target,
            "interaction_move": self.interaction_move,
            "authorized_action": asdict(self.authorized_action),
            "speech_atoms": [asdict(atom) for atom in self.speech_atoms],
            "delivery_mode": self.delivery_mode,
            "visible_cost_signal": self.visible_cost_signal,
            "response_hook": self.response_hook,
            "disposition": self.disposition,
            "evidence_anchors": dict(self.evidence_anchors),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PublicPerformanceIntent":
        action = data.get("authorized_action", {})
        if not isinstance(action, Mapping):
            raise ValueError("authorized_action must be an object")
        atoms = data.get("speech_atoms", [])
        if not isinstance(atoms, list):
            raise ValueError("speech_atoms must be an array")
        return cls(
            actor_id=str(data.get("actor_id", "")),
            target=str(data.get("target", "")),
            interaction_move=str(data.get("interaction_move", "")),
            authorized_action=ActionIntent(
                action_kind=str(action.get("action_kind", "")),
                target=str(action.get("target", "")),
                arguments=dict(action.get("arguments", {})),
                required_capabilities=tuple(str(item) for item in action.get("required_capabilities", [])),
                grounded_refs=tuple(str(item) for item in action.get("grounded_refs", [])),
            ),
            speech_atoms=tuple(
                SpeechAtom(
                    kind=str(item.get("kind", "")),
                    text=str(item.get("text", "")),
                    evidence_refs=tuple(str(ref) for ref in item.get("evidence_refs", [])),
                )
                for item in atoms if isinstance(item, Mapping)
            ),
            delivery_mode=str(data.get("delivery_mode", "")),
            visible_cost_signal=str(data.get("visible_cost_signal", "")),
            response_hook=str(data.get("response_hook", "")),
            disposition=str(data.get("disposition", "")),
            evidence_anchors=dict(data.get("evidence_anchors", {})),
        )
@dataclass(frozen=True)
class WorldMutation:
    location: str = ""
    ownership: Mapping[str, str] = field(default_factory=dict)
    access: Mapping[str, str] = field(default_factory=dict)
    injuries: Mapping[str, str] = field(default_factory=dict)
    participation: Mapping[str, str] = field(default_factory=dict)
    task_evidence: tuple[str, ...] = ()
    equipment: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)




@dataclass(frozen=True)
class ActionCommand:
    command_id: str
    idempotency_key: str
    branch_id: str
    turn_id: str
    actor_id: str
    action_kind: str
    target: str
    arguments: Mapping[str, Any]
    required_capabilities: tuple[str, ...]
    expected_scene_revision: int
    interrupt_policy: str = "interruptible"
    immutable_command_hash: str = ""

    @classmethod
    def from_intent(
        cls,
        *,
        command_id: str,
        branch_id: str,
        turn_id: str,
        actor_id: str,
        expected_scene_revision: int,
        intent: ActionIntent,
    ) -> "ActionCommand":
        body = {
            "command_id": command_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "actor_id": actor_id,
            "action_kind": intent.action_kind,
            "target": intent.target,
            "arguments": dict(intent.arguments),
            "required_capabilities": list(intent.required_capabilities),
            "expected_scene_revision": expected_scene_revision,
        }
        digest = _digest(body)
        return cls(
            idempotency_key=f"action:{command_id}",
            immutable_command_hash=digest,
            **{key: value for key, value in body.items() if key != "required_capabilities"},
            required_capabilities=intent.required_capabilities,
        )


@dataclass(frozen=True)
class ResolvedOutcome:
    status: str
    action_kind: str
    observable_facts: tuple[str, ...] = ()
    changed_refs: tuple[str, ...] = ()
    error: str = ""
    costs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    mutation: WorldMutation = field(default_factory=WorldMutation)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "partial", "deferred"}:
            raise ValueError(f"invalid outcome status: {self.status}")


@dataclass(frozen=True)
class HostReceipt:
    command_id: str
    status: str
    host_operation_id: str
    scene_revision_before: int
    scene_revision_after: int
    outcome: ResolvedOutcome | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"pending", "completed", "failed", "cancelled"}:
            raise ValueError(f"invalid receipt status: {self.status}")
        if self.status == "completed" and self.outcome is None:
            raise ValueError("completed receipt requires an outcome")


@dataclass(frozen=True)
class Delivery:
    pace: str = ""
    volume: str = ""
    breath: str = ""
    articulation: str = ""
    pause: str = ""
    vocal_target: str = ""


@dataclass(frozen=True)
class PerformanceDraft:
    actor_id: str
    action: str
    speech: str
    addressee: str
    attention_target: str
    gaze: str
    blocking: str
    posture_change: str
    delivery: Delivery
    physical_residue: str
    observable_outcome: tuple[str, ...]
    response_hook: str

    def validate(self, intent: PublicPerformanceIntent, outcome: ResolvedOutcome) -> None:
        if self.actor_id != intent.actor_id:
            raise ValueError("performance actor does not own the intent")
        if self.addressee and self.addressee != intent.target:
            raise ValueError("performance addressee differs from authorized target")
        if self.response_hook != intent.response_hook:
            raise ValueError("performance changed the authorized response hook")
        if self.action and not intent.authorized_action.action_kind:
            raise ValueError("performance contains an unauthorized action")
        if tuple(self.observable_outcome) != tuple(outcome.observable_facts):
            raise ValueError("performance must preserve the host observable outcome")


@dataclass(frozen=True)
class PerformanceBeat:
    beat_id: str
    actor_id: str
    action: str
    speech: str
    addressee: str
    attention_target: str
    gaze: str
    blocking: str
    posture_change: str
    delivery: Delivery
    physical_residue: str
    observable_outcome: tuple[str, ...]
    response_hook: str
    source_event_ids: tuple[str, ...]

    @classmethod
    def from_draft(
        cls,
        draft: PerformanceDraft,
        *,
        beat_id: str,
        source_event_ids: Sequence[str],
    ) -> "PerformanceBeat":
        if not beat_id or not source_event_ids:
            raise ValueError("committed performance requires beat and event ids")
        return cls(
            beat_id=beat_id,
            source_event_ids=tuple(source_event_ids),
            **asdict(draft),
        )


@dataclass(frozen=True)
class PerformanceContinuity:
    response_hook: str = ""
    posture_intent: str = ""
    gaze_intent: str = ""
    vocal_residue_intent: str = ""


@dataclass(frozen=True)
class AdapterProjectionState:
    camera_axis: str = ""
    shot_eyelines: Mapping[str, str] = field(default_factory=dict)
    rendered_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    accepted_asset_refs: tuple[str, ...] = ()
    candidate_lineage: tuple[str, ...] = ()


def _flatten(prefix: str, value: Any, result: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        if not value:
            result[prefix] = {}
        for key, item in value.items():
            key_text = str(key)
            child = key_text if key_text.startswith(prefix + ".") else f"{prefix}.{key_text}"
            _flatten(child, item, result)
        return
    result[prefix] = value


def _require_refs(refs: Sequence[str], evidence: Mapping[str, Any], owner: str) -> None:
    missing = [reference for reference in refs if reference not in evidence]
    if missing:
        raise ValueError(f"{owner} cites unknown evidence refs: {missing}")


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()
