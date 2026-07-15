"""Small deterministic Host implementation for rehearsal and contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import ActionCommand, HostReceipt, ObservationView, ResolvedOutcome


@dataclass
class InMemorySceneHost:
    scene_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    affordances: dict[str, dict[str, Any]] = field(default_factory=dict)
    targets: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("speak",)
    revision: int = 0
    outcomes: dict[str, ResolvedOutcome] = field(default_factory=dict)
    received: list[str] = field(default_factory=list)

    def observe(self, actor_id: str) -> ObservationView:
        del actor_id
        return ObservationView(
            facts=dict(self.facts),
            affordances={key: dict(value) for key, value in self.affordances.items()},
            available_targets=self.targets,
            capabilities=self.capabilities,
            scene_revision=self.revision,
        )

    def submit(self, command: ActionCommand) -> HostReceipt:
        if command.command_id in self.received:
            return self.query(command.command_id)
        if command.expected_scene_revision != self.revision:
            return HostReceipt(
                command_id=command.command_id,
                status="failed",
                host_operation_id=f"op:{command.command_id}",
                scene_revision_before=self.revision,
                scene_revision_after=self.revision,
                error="scene revision conflict",
            )
        self.received.append(command.command_id)
        outcome = self.outcomes.get(command.action_kind)
        if outcome is None:
            outcome = ResolvedOutcome(
                status="succeeded",
                action_kind=command.action_kind,
                observable_facts=(f"{command.actor_id} performed {command.action_kind}",),
                changed_refs=(),
            )
        before = self.revision
        self.revision += 1
        return HostReceipt(
            command_id=command.command_id,
            status="completed",
            host_operation_id=f"op:{command.command_id}",
            scene_revision_before=before,
            scene_revision_after=self.revision,
            outcome=outcome,
        )

    def query(self, command_id: str) -> HostReceipt:
        if command_id not in self.received:
            return HostReceipt(
                command_id=command_id,
                status="failed",
                host_operation_id=f"op:{command_id}",
                scene_revision_before=self.revision,
                scene_revision_after=self.revision,
                error="unknown command",
            )
        return HostReceipt(
            command_id=command_id,
            status="completed",
            host_operation_id=f"op:{command_id}",
            scene_revision_before=max(0, self.revision - 1),
            scene_revision_after=self.revision,
            outcome=ResolvedOutcome(
                status="succeeded",
                action_kind="unknown",
                observable_facts=("command already completed",),
            ),
        )

    def cancel(self, command_id: str) -> HostReceipt:
        return HostReceipt(
            command_id=command_id,
            status="cancelled",
            host_operation_id=f"op:{command_id}",
            scene_revision_before=self.revision,
            scene_revision_after=self.revision,
            error="cancelled by host",
        )
