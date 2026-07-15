"""Frozen behavior benchmark runner and anonymous review packets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .cognition import JsonCognitionPort
from .contracts import DecisionContract, DecisionFrame, ObservationView, PublicPerformanceIntent


@dataclass(frozen=True)
class BehaviorCase:
    id: str
    category: str
    persona: Mapping[str, Any]
    private_state: Mapping[str, Any]
    relationship: Mapping[str, Mapping[str, Any]]
    observation: Mapping[str, Any]
    targets: tuple[str, ...]
    capabilities: tuple[str, ...]
    actions: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BehaviorCase":
        return cls(
            id=str(data["id"]), category=str(data["category"]),
            persona=dict(data.get("persona", {})),
            private_state=dict(data.get("private_state", {})),
            relationship={str(key): dict(value) for key, value in dict(data.get("relationship", {})).items()},
            observation=dict(data.get("observation", {})),
            targets=tuple(str(item) for item in data.get("targets", [])),
            capabilities=tuple(str(item) for item in data.get("capabilities", [])),
            actions=tuple(str(item) for item in data.get("actions", [])),
        )

    def frame(self) -> DecisionFrame:
        actor_id = str(self.persona.get("id", self.id))
        return DecisionFrame(
            scene_id=f"benchmark:{self.id}", turn_id=f"benchmark:{self.id}:turn:0", actor_id=actor_id,
            private_state=dict(self.private_state), relationships=self.relationship,
            emotions={},
            observation=ObservationView(
                facts=dict(self.observation), available_targets=self.targets,
                capabilities=self.capabilities,
            ),
            recent_history=(), continuity={},
            decision_contract=DecisionContract(allowed_actions=self.actions),
            identity_evidence={
                key: value for key, value in self.persona.items()
                if key in {"age", "role", "background", "values", "preferences", "competencies", "voice"} and value
            },
        )


@dataclass(frozen=True)
class BehaviorRecord:
    case_id: str
    category: str
    appraisal: Mapping[str, Any]
    public_intent: Mapping[str, Any]
    anonymous_packet: Mapping[str, Any]
    protocol_error: str = ""


class BehaviorBenchmark:
    def __init__(self, cases: Sequence[BehaviorCase]) -> None:
        self.cases = tuple(cases)

    @classmethod
    def load(cls, path: str | Path) -> "BehaviorBenchmark":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not data.get("frozen"):
            raise ValueError("behavior benchmark must be explicitly frozen")
        return cls(tuple(BehaviorCase.from_dict(item) for item in data.get("cases", [])))

    def run(self, cognition: JsonCognitionPort) -> tuple[BehaviorRecord, ...]:
        records: list[BehaviorRecord] = []
        for index, case in enumerate(self.cases):
            frame = case.frame()
            try:
                appraisal, policy = cognition.decide(frame)
                ppi = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
            except Exception as exc:
                records.append(
                    BehaviorRecord(
                        case_id=case.id,
                        category=case.category,
                        appraisal={},
                        public_intent={},
                        anonymous_packet={"anonymous_actor": f"actor-{index + 1}", "category": case.category},
                        protocol_error=str(exc),
                    )
                )
                continue
            records.append(
                BehaviorRecord(
                    case_id=case.id,
                    category=case.category,
                    appraisal={
                        "subjective_observation": appraisal.subjective_observation,
                        "emotion_changes": [asdict(item) for item in appraisal.changes],
                        "grounded_refs": list(appraisal.grounded_refs),
                    },
                    public_intent=ppi.to_dict(),
                    anonymous_packet=_anonymous_packet(index, case, ppi),
                )
            )
        return tuple(records)


def load_counterfactual_pairs(path: str | Path) -> tuple[Mapping[str, Any], ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("frozen"):
        raise ValueError("counterfactual benchmark must be explicitly frozen")
    return tuple(dict(item) for item in data.get("pairs", []))


def same_stimulus_frames(
    *,
    stimulus: Mapping[str, Any],
    personas: Sequence[Mapping[str, Any]],
    target: str,
    private_state: Mapping[str, Any] | None = None,
    relationships: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[DecisionFrame, ...]:
    """Build counterfactual frames whose only changing input is identity."""
    frames: list[DecisionFrame] = []
    shared_state = dict(private_state or {"goal": "respond to the immediate situation"})
    shared_relationships = {
        key: dict(value)
        for key, value in (relationships or {target: {"summary": "current counterpart", "disclosure": "guarded"}}).items()
    }
    for index, persona in enumerate(personas):
        actor_id = str(persona.get("id", f"actor-{index}"))
        frames.append(
            DecisionFrame(
                scene_id="benchmark:same-stimulus", turn_id=f"same:{index}", actor_id=actor_id,
                private_state=shared_state,
                relationships=shared_relationships,
                emotions={},
                observation=ObservationView(
                    facts=dict(stimulus), available_targets=(target,), capabilities=("speak", "wait"),
                ),
                recent_history=(), continuity={},
                decision_contract=DecisionContract(allowed_actions=("speak", "wait")),
                identity_evidence={
                    key: value for key, value in persona.items()
                    if key in {"age", "role", "background", "values", "preferences", "competencies", "voice"} and value
                },
            )
        )
    return tuple(frames)


def _anonymous_packet(index: int, case: BehaviorCase, intent: PublicPerformanceIntent) -> dict[str, Any]:
    return {
        "anonymous_actor": f"actor-{index + 1}",
        "category": case.category,
        "public_observation": dict(case.observation),
        "interaction_move": intent.interaction_move,
        "speech_atoms": [atom.text for atom in intent.speech_atoms],
        "delivery_mode": intent.delivery_mode,
        "visible_cost_signal": intent.visible_cost_signal,
        "response_hook": intent.response_hook,
        "disposition": intent.disposition,
    }
