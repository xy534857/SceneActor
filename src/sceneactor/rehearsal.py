"""Disposable two-NPC rehearsal host over the portable runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from .contracts import DecisionContract, DecisionFrame
from .hosts import InMemorySceneHost
from .events import EventLedger
from .persona import Persona
from .reducers import RuntimeState
from .runtime import CognitionPort, PerformancePort, TurnOrchestrator, TurnResult


@dataclass(frozen=True)
class SceneSetup:
    scene_id: str
    setting: str
    opening: str
    affordances: tuple[str, ...]
    max_turns: int = 8

    def __post_init__(self) -> None:
        if not self.scene_id.strip() or not self.setting.strip() or not self.opening.strip():
            raise ValueError("scene id, setting, and opening are required")
        if not 1 <= self.max_turns <= 24:
            raise ValueError("max_turns must be between 1 and 24")
        if not self.affordances:
            raise ValueError("scene requires at least one affordance")


@dataclass(frozen=True)
class ActorSetup:
    persona: Persona
    goal: str
    relationship: str = ""
    private_state: Mapping[str, Any] = field(default_factory=dict)
    disclosure: str = "guarded"

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("actor goal is required")
        if self.disclosure not in {"closed", "guarded", "open"}:
            raise ValueError("invalid disclosure")


@dataclass
class RehearsalRun:
    run_id: str
    scene: SceneSetup
    actors: tuple[ActorSetup, ActorSetup]
    host: InMemorySceneHost
    cognition: Mapping[str, CognitionPort]
    performance: PerformancePort
    ledger: EventLedger = field(default_factory=EventLedger)
    turns: list[TurnResult] = field(default_factory=list)
    active_actor_index: int = 0
    actor_revisions: dict[str, int] = field(default_factory=dict)
    complete_reason: str = ""

    def __post_init__(self) -> None:
        if self.actors[0].persona.id == self.actors[1].persona.id:
            raise ValueError("rehearsal requires two different actors")
        self.actor_revisions = {
            actor.persona.id: 0 for actor in self.actors
        }

    @property
    def complete(self) -> bool:
        return bool(self.complete_reason) or len(self.turns) >= self.scene.max_turns

    def advance(self) -> TurnResult:
        if self.complete:
            raise RuntimeError("rehearsal is complete")
        actor = self.actors[self.active_actor_index]
        other = self.actors[1 - self.active_actor_index]
        actor_state = self._actor_state(actor.persona.id)
        observation = self.host.observe(actor.persona.id)
        frame = DecisionFrame(
            scene_id=self.scene.scene_id,
            turn_id=f"{self.run_id}:turn:{len(self.turns)}",
            actor_id=actor.persona.id,
            private_state={
                "goal": actor.goal,
                "relationship": actor.relationship,
                "disclosure": actor.disclosure,
                **dict(actor.private_state),
                "counterpart": other.persona.name,
                "scene_setting": self.scene.setting,
            },
            relationships={other.persona.id: {"summary": other.relationship or actor.relationship, "disclosure": actor.disclosure}},
            emotions=dict(actor_state.emotions),
            observation=observation,
            recent_history=self._history_for(actor.persona.id),
            continuity={"goal": actor.goal, "turns": len(self.turns)},
            decision_contract=DecisionContract(
                allowed_actions=("speak", "wait", "interact"),
                required_arguments={"interact": ("affordance_ref",)},
            ),
            identity_evidence={
                "age": actor.persona.age,
                "role": actor.persona.role,
                "background": actor.persona.background,
                "values": actor.persona.values,
                "preferences": actor.persona.preferences,
                "competencies": actor.persona.competencies,
                "cognition_lens": actor.persona.cognition_lens,
                "voice": actor.persona.voice.to_dict(),
            },
        )
        orchestrator = TurnOrchestrator(
            branch_id=self.run_id,
            host=self.host,
            cognition=self.cognition[actor.persona.id],
            performance=self.performance,
            ledger=self.ledger,
            initial_order=len(self.turns),
        )
        result = orchestrator.run_turn(
            frame=frame,
            actor_revisions=dict(self.actor_revisions),
            scene_revision=self.host.revision,
            scene_id=self.scene.scene_id,
        )
        self.turns.append(result)
        self.actor_revisions[actor.persona.id] = result.actors[actor.persona.id].revision
        self.active_actor_index = 1 - self.active_actor_index
        if len(self.turns) >= self.scene.max_turns:
            self.complete_reason = "safety_cap"
        return result

    def _actor_state(self, actor_id: str) -> RuntimeState:
        if not self.turns:
            return RuntimeState(actor_id)
        for result in reversed(self.turns):
            if actor_id in result.actors:
                return result.actors[actor_id]
        return RuntimeState(actor_id)

    def _history_for(self, actor_id: str) -> tuple[dict[str, str], ...]:
        history: list[dict[str, str]] = []
        for result in self.turns:
            if result.draft is None:
                continue
            history.append(
                {
                    "role": "npc" if result.draft.actor_id == actor_id else "other",
                    "actor_id": result.draft.actor_id,
                    "speech": result.draft.speech,
                    "action": result.draft.action,
                    "content": result.draft.speech or result.draft.action,
                }
            )
        return tuple(history[-8:])


def create_rehearsal(
    scene: SceneSetup,
    actors: tuple[ActorSetup, ActorSetup],
    *,
    host: InMemorySceneHost,
    cognition: Mapping[str, CognitionPort],
    performance: PerformancePort,
) -> RehearsalRun:
    missing = [actor.persona.id for actor in actors if actor.persona.id not in cognition]
    if missing:
        raise ValueError(f"missing cognition ports: {missing}")
    return RehearsalRun(uuid4().hex, scene, actors, host, cognition, performance)
