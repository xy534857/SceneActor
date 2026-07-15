"""Pure reducers for subjective NPC state and objective scene projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .events import RuntimeEvent

_IMPACT_STEP = {"minor": 0.08, "moderate": 0.2, "major": 0.4}


@dataclass
class RuntimeState:
    actor_id: str
    emotions: dict[str, float] = field(default_factory=dict)
    last_appraisal: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)
    goals: dict[str, dict[str, Any]] = field(default_factory=dict)
    commitments: dict[str, dict[str, Any]] = field(default_factory=dict)
    beliefs: dict[str, dict[str, Any]] = field(default_factory=dict)
    memories: list[dict[str, Any]] = field(default_factory=list)
    recent_performance: list[dict[str, Any]] = field(default_factory=list)
    participation: str = "active"
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SceneState:
    scene_id: str
    locations: dict[str, str] = field(default_factory=dict)
    ownership: dict[str, str] = field(default_factory=dict)
    injuries: dict[str, list[str]] = field(default_factory=dict)
    access: dict[str, str] = field(default_factory=dict)
    participation: dict[str, str] = field(default_factory=dict)
    task_evidence: list[str] = field(default_factory=list)
    equipment: dict[str, dict[str, Any]] = field(default_factory=dict)
    completion_reason: str = ""
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reduce_events(
    events: Iterable[RuntimeEvent],
    *,
    actor_ids: Iterable[str] = (),
    scene_id: str = "",
) -> tuple[dict[str, RuntimeState], SceneState]:
    """Rebuild every canonical projection from an ordered event stream."""

    actor_states = {actor_id: RuntimeState(actor_id) for actor_id in actor_ids}
    scene = SceneState(scene_id)
    applied: set[str] = set()
    ordered = sorted(events, key=lambda item: (item.order, item.id))
    for event in ordered:
        if event.id in applied:
            continue
        applied.add(event.id)
        if not scene.scene_id:
            scene.scene_id = event.scene_id
        actor = actor_states.setdefault(event.actor_id, RuntimeState(event.actor_id))
        actor_changed = _reduce_actor(actor, event)
        scene_changed = _reduce_scene(scene, event)
        if actor_changed:
            actor.revision += 1
        if scene_changed:
            scene.revision += 1
    return actor_states, scene


def _reduce_actor(state: RuntimeState, event: RuntimeEvent) -> bool:
    payload = event.payload
    if event.kind == "npc.appraisal_committed":
        state.last_appraisal = dict(payload)
        return True
    if event.kind == "npc.emotion_changed":
        emotion = str(payload["emotion"])
        impact = str(payload["impact"])
        direction = str(payload["direction"])
        step = _IMPACT_STEP[impact]
        current = float(state.emotions.get(emotion, 0.0))
        state.emotions[emotion] = max(0.0, min(1.0, current + step if direction == "rise" else current - step))
        return True
    if event.kind == "npc.relationship_updated":
        target = str(payload["target"])
        state.relationships[target] = dict(payload.get("state", {}))
        return True
    if event.kind == "npc.goal_started":
        goal_id = str(payload["goal_id"])
        state.goals[goal_id] = {"description": str(payload["description"]), "status": "active"}
        return True
    if event.kind == "npc.goal_transitioned":
        return _transition(state.goals, payload, "goal_id")
    if event.kind == "npc.commitment_started":
        item_id = str(payload["commitment_id"])
        state.commitments[item_id] = {"description": str(payload["description"]), "status": "active"}
        return True
    if event.kind == "npc.commitment_transitioned":
        return _transition(state.commitments, payload, "commitment_id")
    if event.kind == "npc.belief_updated":
        belief_id = str(payload["belief_id"])
        state.beliefs[belief_id] = {
            "statement": str(payload["statement"]),
            "stance": str(payload["stance"]),
            "evidence_refs": list(payload.get("evidence_refs", [])),
        }
        return True
    if event.kind == "npc.memory_added":
        state.memories.append(dict(payload))
        state.memories = state.memories[-200:]
        return True
    if event.kind == "performance.committed":
        state.recent_performance.append(dict(payload))
        state.recent_performance = state.recent_performance[-20:]
        return True
    if event.kind == "npc.participation_changed":
        state.participation = str(payload["status"])
        return True
    return False


def _reduce_scene(state: SceneState, event: RuntimeEvent) -> bool:
    payload = event.payload
    if event.kind == "scene.started":
        state.locations.update({str(key): str(value) for key, value in dict(payload.get("locations", {})).items()})
        state.ownership.update({str(key): str(value) for key, value in dict(payload.get("ownership", {})).items()})
        state.participation.update({str(key): str(value) for key, value in dict(payload.get("participation", {})).items()})
        return True
    if event.kind == "world.action_resolved":
        mutation = payload.get("mutation", {})
        if not isinstance(mutation, Mapping):
            return False
        changed = False
        if location := mutation.get("location"):
            state.locations[event.actor_id] = str(location)
            changed = True
        for entity, owner in dict(mutation.get("ownership", {})).items():
            state.ownership[str(entity)] = str(owner)
            changed = True
        for actor_id, injury in dict(mutation.get("injuries", {})).items():
            state.injuries.setdefault(str(actor_id), []).append(str(injury))
            changed = True
        for key, value in dict(mutation.get("access", {})).items():
            state.access[str(key)] = str(value)
            changed = True
        for actor_id, status in dict(mutation.get("participation", {})).items():
            state.participation[str(actor_id)] = str(status)
            changed = True
        for reference in mutation.get("task_evidence", []):
            item = str(reference)
            if item not in state.task_evidence:
                state.task_evidence.append(item)
                changed = True
        for entity, equipment_state in dict(mutation.get("equipment", {})).items():
            state.equipment[str(entity)] = dict(equipment_state)
            changed = True
        return changed
    if event.kind == "scene.completed":
        state.completion_reason = str(payload["reason"])
        return True
    return False


def _transition(items: dict[str, dict[str, Any]], payload: Mapping[str, Any], id_key: str) -> bool:
    item_id = str(payload[id_key])
    item = items.get(item_id)
    if item is None:
        raise ValueError(f"cannot transition unknown {id_key}: {item_id}")
    transition = str(payload["transition"])
    if transition not in {"fulfilled", "renegotiated", "breached", "abandoned", "blocked"}:
        raise ValueError(f"invalid transition: {transition}")
    item["status"] = transition
    if consequence := payload.get("consequence"):
        item["consequence"] = str(consequence)
    return True
