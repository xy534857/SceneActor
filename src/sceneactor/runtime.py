"""Vertical U -> X -> Y transaction orchestration.

The model ports are injected. Deterministic test ports and a production
OpenAI-compatible port can share this transaction without changing authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .contracts import (
    ActionCommand,
    DecisionFrame,
    HostReceipt,
    PerformanceDraft,
    PerformancePolicy,
    PublicPerformanceIntent,
    ResolvedOutcome,
)
from .events import EventLedger, HostCommandJournal, RuntimeEvent, TurnEventBatch, TurnEventFollowUp
from .reducers import RuntimeState, SceneState, reduce_events


class CognitionPort(Protocol):
    def decide(self, frame: DecisionFrame) -> tuple[object, PerformancePolicy]: ...


class PerformancePort(Protocol):
    def realize(
        self,
        intent: PublicPerformanceIntent,
        outcome: ResolvedOutcome,
        recent_history: tuple[dict[str, str], ...],
    ) -> PerformanceDraft: ...


class ActionCommandPort(Protocol):
    def submit(self, command: ActionCommand) -> HostReceipt: ...
    def query(self, command_id: str) -> HostReceipt: ...
    def cancel(self, command_id: str) -> HostReceipt: ...


@dataclass(frozen=True)
class TurnResult:
    batch: TurnEventBatch
    receipt: HostReceipt
    draft: PerformanceDraft | None
    actors: dict[str, RuntimeState]
    scene: SceneState


class TurnOrchestrator:
    """Owns private-frame assembly, receipt journaling, and event commit."""

    def __init__(
        self,
        *,
        branch_id: str,
        host: ActionCommandPort,
        cognition: CognitionPort,
        performance: PerformancePort,
        ledger: EventLedger | None = None,
        journal: HostCommandJournal | None = None,
    ) -> None:
        self.branch_id = branch_id
        self.host = host
        self.cognition = cognition
        self.performance = performance
        self.ledger = ledger or EventLedger()
        self.journal = journal
        self._turn_number = 0

    def run_turn(
        self,
        *,
        frame: DecisionFrame,
        actor_revisions: dict[str, int],
        scene_revision: int,
        scene_id: str,
    ) -> TurnResult:
        appraisal, policy = self.cognition.decide(frame)
        del appraisal  # appraisal is committed as a separate event below
        ppi = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
        command = ActionCommand.from_intent(
            command_id=f"command:{self.branch_id}:{frame.turn_id}:{uuid4().hex[:12]}",
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            actor_id=frame.actor_id,
            expected_scene_revision=scene_revision,
            intent=ppi.authorized_action,
        )
        if self.journal:
            self.journal.record(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                branch_id=command.branch_id,
                turn_id=command.turn_id,
                actor_id=command.actor_id,
                immutable_command_hash=command.immutable_command_hash,
                expected_scene_revision=command.expected_scene_revision,
                status="prepared",
            )
        receipt = self.host.submit(command)
        if self.journal:
            self.journal.record(
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                branch_id=command.branch_id,
                turn_id=command.turn_id,
                actor_id=command.actor_id,
                immutable_command_hash=command.immutable_command_hash,
                expected_scene_revision=command.expected_scene_revision,
                status=receipt.status,
                host_receipt=_receipt_dict(receipt),
            )
        if receipt.status == "pending":
            batch = self._pending_batch(frame, command, receipt, actor_revisions, scene_revision, scene_id)
            self.ledger.append_batch(batch)
            actors, scene = reduce_events(self.ledger.events(self.branch_id), actor_ids=actor_revisions, scene_id=scene_id)
            return TurnResult(batch, receipt, None, actors, scene)
        if receipt.outcome is None:
            raise RuntimeError("terminal Host receipt must contain an outcome")
        outcome = receipt.outcome
        recent = tuple(dict(item) for item in frame.recent_history[-3:])
        draft = self.performance.realize(ppi, outcome, recent)
        draft.validate(ppi, outcome)
        event = RuntimeEvent.create(
            batch_id=f"batch:{self.branch_id}:{frame.turn_id}",
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            kind="performance.committed",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={
                "action": draft.action,
                "speech": draft.speech,
                "observable_outcome": list(draft.observable_outcome),
                "response_hook": draft.response_hook,
            },
            visible_to=tuple(frame.relationships),
            evidence_refs=tuple(ppi.evidence_anchors),
            order=self._turn_number,
        )
        host_event = RuntimeEvent.create(
            batch_id=event.batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            kind="world.action_resolved",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={"outcome": _outcome_dict(outcome)},
            visible_to=tuple(frame.relationships),
            evidence_refs=outcome.evidence_refs,
            order=self._turn_number,
        )
        batch = TurnEventBatch.create(
            batch_id=event.batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            expected_actor_revisions=actor_revisions,
            expected_scene_revision=scene_revision,
            host_receipt=_receipt_dict(receipt),
            lifecycle="committed",
            performance_status="complete",
            events=(host_event, event),
        )
        self.ledger.append_batch(batch)
        self._turn_number += 1
        actors, scene = reduce_events(self.ledger.events(self.branch_id), actor_ids=actor_revisions, scene_id=scene_id)
        return TurnResult(batch, receipt, draft, actors, scene)

    def complete_pending_performance(
        self,
        *,
        frame: DecisionFrame,
        batch: TurnEventBatch,
        outcome: ResolvedOutcome,
        scene_id: str,
    ) -> TurnResult:
        """Retry Y only after X was durably committed; never resubmit the action."""
        if batch.lifecycle != "performance_pending":
            raise ValueError("batch is not waiting for performance")
        ppi = self.cognition_public_intent(frame)
        draft = self.performance.realize(ppi, outcome, tuple(dict(item) for item in frame.recent_history[-3:]))
        draft.validate(ppi, outcome)
        event = RuntimeEvent.create(
            batch_id=batch.batch_id,
            branch_id=batch.branch_id,
            turn_id=batch.turn_id,
            command_id=batch.command_id,
            kind="performance.committed",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={"action": draft.action, "speech": draft.speech, "observable_outcome": list(draft.observable_outcome)},
            visible_to=tuple(frame.relationships),
            order=self._turn_number,
        )
        follow_up = TurnEventFollowUp.create(
            follow_up_id=f"follow:{batch.batch_id}:{uuid4().hex[:8]}",
            parent_batch_id=batch.batch_id,
            branch_id=batch.branch_id,
            expected_batch_status="performance_pending",
            performance_status="complete",
            events=(event,),
        )
        self.ledger.append_follow_up(follow_up)
        actors, scene = reduce_events(self.ledger.events(self.branch_id), actor_ids=(frame.actor_id,), scene_id=scene_id)
        receipt = HostReceipt(
            command_id=batch.command_id,
            status="completed",
            host_operation_id=str(batch.host_receipt.get("host_operation_id", "")),
            scene_revision_before=int(batch.host_receipt.get("scene_revision_before", 0)),
            scene_revision_after=int(batch.host_receipt.get("scene_revision_after", 0)),
            outcome=outcome,
        )
        return TurnResult(batch, receipt, draft, actors, scene)

    def cognition_public_intent(self, frame: DecisionFrame) -> PublicPerformanceIntent:
        _, policy = self.cognition.decide(frame)
        return PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)

    def _pending_batch(self, frame, command, receipt, actor_revisions, scene_revision, scene_id):
        event = RuntimeEvent.create(
            batch_id=f"batch:{self.branch_id}:{frame.turn_id}",
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            kind="world.action_pending",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={"receipt": _receipt_dict(receipt)},
            visible_to=tuple(frame.relationships),
            order=self._turn_number,
        )
        return TurnEventBatch.create(
            batch_id=event.batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            expected_actor_revisions=actor_revisions,
            expected_scene_revision=scene_revision,
            host_receipt=_receipt_dict(receipt),
            lifecycle="host_pending",
            performance_status="pending",
            events=(event,),
        )


def _receipt_dict(receipt: HostReceipt) -> dict:
    data = {
        "command_id": receipt.command_id,
        "status": receipt.status,
        "host_operation_id": receipt.host_operation_id,
        "scene_revision_before": receipt.scene_revision_before,
        "scene_revision_after": receipt.scene_revision_after,
        "error": receipt.error,
    }
    if receipt.outcome is not None:
        data["outcome"] = _outcome_dict(receipt.outcome)
    return data


def _outcome_dict(outcome: ResolvedOutcome) -> dict:
    return {
        "status": outcome.status,
        "action_kind": outcome.action_kind,
        "observable_facts": list(outcome.observable_facts),
        "changed_refs": list(outcome.changed_refs),
        "error": outcome.error,
        "costs": list(outcome.costs),
        "evidence_refs": list(outcome.evidence_refs),
    }
