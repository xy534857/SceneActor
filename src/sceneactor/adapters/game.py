"""Game-facing ports. Engine-specific commands stay outside the NPC core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..contracts import ActionCommand, HostReceipt, PerformanceBeat


@dataclass(frozen=True)
class GameActionCommand:
    """Engine encoding of a core ActionCommand; it never changes its semantics."""

    core_command_id: str
    engine_entity_ref: str
    engine_arguments: Mapping[str, Any]
    engine_interrupt_policy: str


class GameEnginePort(Protocol):
    def submit(self, command: GameActionCommand) -> HostReceipt: ...
    def query(self, command_id: str) -> HostReceipt: ...
    def cancel(self, command_id: str) -> HostReceipt: ...


class GameSceneHost:
    """Maps core commands to an authoritative engine without semantic changes."""

    def __init__(self, engine: GameEnginePort, entity_map: Mapping[str, str] | None = None) -> None:
        self.engine = engine
        self.entity_map = dict(entity_map or {})

    def submit(self, command: ActionCommand) -> HostReceipt:
        entity = self.entity_map.get(command.target, command.target)
        engine_command = GameActionCommand(
            core_command_id=command.command_id,
            engine_entity_ref=entity,
            engine_arguments=dict(command.arguments),
            engine_interrupt_policy=command.interrupt_policy,
        )
        receipt = self.engine.submit(engine_command)
        if receipt.command_id != command.command_id:
            raise ValueError("engine receipt changed core command id")
        return receipt

    def query(self, command_id: str) -> HostReceipt:
        return self.engine.query(command_id)

    def cancel(self, command_id: str) -> HostReceipt:
        return self.engine.cancel(command_id)


@dataclass(frozen=True)
class GamePresentationCommand:
    beat_id: str
    cosmetic_animation_tags: tuple[str, ...] = ()
    gaze_target: str = ""
    posture_overlay: str = ""
    facial_intent: str = ""
    speech: str = ""
    voice_delivery: Mapping[str, str] | None = None
    subtitle: str = ""


class GamePresentationAdapter:
    """Projects committed performance only; it cannot mutate world state."""

    def project(self, beat: PerformanceBeat) -> GamePresentationCommand:
        return GamePresentationCommand(
            beat_id=beat.beat_id,
            cosmetic_animation_tags=tuple(item for item in (beat.action, beat.posture_change) if item),
            gaze_target=beat.gaze,
            posture_overlay=beat.posture_change,
            facial_intent=beat.physical_residue,
            speech=beat.speech,
            voice_delivery={
                key: value
                for key, value in {
                    "pace": beat.delivery.pace,
                    "volume": beat.delivery.volume,
                    "breath": beat.delivery.breath,
                    "articulation": beat.delivery.articulation,
                    "pause": beat.delivery.pause,
                    "vocal_target": beat.delivery.vocal_target,
                }.items()
                if value
            },
        )
