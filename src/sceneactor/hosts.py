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
    receipts: dict[str, HostReceipt] = field(default_factory=dict)

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
            outcome = ResolvedOutcome(
                status="failed",
                action_kind=command.action_kind,
                observable_facts=("动作未执行：场景状态已经变化",),
                error="scene revision conflict",
            )
            receipt = HostReceipt(
                command_id=command.command_id,
                status="failed",
                host_operation_id=f"op:{command.command_id}",
                scene_revision_before=self.revision,
                scene_revision_after=self.revision,
                outcome=outcome,
                error=outcome.error,
            )
            self.receipts[command.command_id] = receipt
            return receipt
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
        receipt = HostReceipt(
            command_id=command.command_id,
            status="completed",
            host_operation_id=f"op:{command.command_id}",
            scene_revision_before=before,
            scene_revision_after=self.revision,
            outcome=outcome,
        )
        self.receipts[command.command_id] = receipt
        return receipt

    def query(self, command_id: str) -> HostReceipt:
        receipt = self.receipts.get(command_id)
        if receipt is not None:
            return receipt
        return HostReceipt(
            command_id=command_id,
            status="failed",
            host_operation_id=f"op:{command_id}",
            scene_revision_before=self.revision,
            scene_revision_after=self.revision,
            error="unknown command",
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
