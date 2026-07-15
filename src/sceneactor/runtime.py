"""Vertical U -> X -> Y transaction orchestration.

The model ports are injected. Deterministic test ports and a production
OpenAI-compatible port can share this transaction without changing authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import uuid4

from .contracts import (
    ActionCommand,
    Appraisal,
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
    def decide(self, frame: DecisionFrame) -> tuple[Appraisal, PerformancePolicy]: ...


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
        initial_order: int = 0,
        journal: HostCommandJournal | None = None,
    ) -> None:
        self.branch_id = branch_id
        self.host = host
        self.cognition = cognition
        self.performance = performance
        self.ledger = ledger or EventLedger()
        self.journal = journal
        self._turn_number = initial_order

    def run_turn(
        self,
        *,
        frame: DecisionFrame,
        actor_revisions: dict[str, int],
        scene_revision: int,
        scene_id: str,
    ) -> TurnResult:
        appraisal, policy = self.cognition.decide(frame)
        appraisal.validate(frame)
        ppi = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
        command = ActionCommand.from_intent(
            command_id=f"command:{self.branch_id}:{frame.turn_id}:{uuid4().hex[:12]}",
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            actor_id=frame.actor_id,
            expected_scene_revision=scene_revision,
            intent=ppi.authorized_action,
        )
        self._record_command(command, "prepared")
        receipt = self.host.submit(command)
        self._record_command(command, receipt.status, receipt)
        batch_id = f"batch:{self.branch_id}:{frame.turn_id}"
        appraisal_events = self._appraisal_events(appraisal, frame, batch_id, command.command_id, scene_id)
        if receipt.status == "pending":
            batch = self._pending_batch(
                frame, command, receipt, actor_revisions, scene_revision, scene_id,
                appraisal_events, ppi,
            )
            self.ledger.append_batch(batch)
            actors, scene = self._reduce(actor_revisions, scene_id)
            return TurnResult(batch, receipt, None, actors, scene)
        if receipt.outcome is None:
            raise RuntimeError("terminal Host receipt must contain an outcome")
        outcome = receipt.outcome
        host_event = self._host_event(frame, batch_id, command.command_id, outcome, scene_id)
        recent = tuple(dict(item) for item in frame.recent_history[-3:])
        try:
            draft = self.performance.realize(ppi, outcome, recent)
            draft.validate(ppi, outcome)
        except (RuntimeError, ValueError):
            batch = TurnEventBatch.create(
                batch_id=batch_id,
                branch_id=self.branch_id,
                turn_id=frame.turn_id,
                command_id=command.command_id,
                expected_actor_revisions=actor_revisions,
                expected_scene_revision=scene_revision,
                host_receipt=_receipt_dict(receipt),
                lifecycle="performance_pending",
                performance_status="pending",
                events=(*appraisal_events, host_event),
                public_performance_intent=ppi.to_dict(),
            )
            self.ledger.append_batch(batch)
            actors, scene = self._reduce(actor_revisions, scene_id)
            return TurnResult(batch, receipt, None, actors, scene)
        performance_event = self._performance_event(frame, batch_id, command.command_id, draft, ppi, scene_id)
        batch = TurnEventBatch.create(
            batch_id=batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command.command_id,
            expected_actor_revisions=actor_revisions,
            expected_scene_revision=scene_revision,
            host_receipt=_receipt_dict(receipt),
            lifecycle="committed",
            performance_status="complete",
            events=(*appraisal_events, host_event, performance_event),
            public_performance_intent=ppi.to_dict(),
        )
        self.ledger.append_batch(batch)
        self._turn_number += 1
        actors, scene = self._reduce(actor_revisions, scene_id)
        return TurnResult(batch, receipt, draft, actors, scene)
    def resume_pending_host(
        self,
        *,
        frame: DecisionFrame,
        batch: TurnEventBatch,
        scene_id: str,
    ) -> TurnResult:
        """Query one persisted command and continue without resubmitting it."""
        if self.ledger.batch_status(batch.branch_id, batch.batch_id) != "host_pending":
            raise ValueError("batch is not waiting for Host completion")
        receipt = self.host.query(batch.command_id)
        if receipt.status == "pending":
            actors, scene = self._reduce(batch.expected_actor_revisions, scene_id)
            return TurnResult(batch, receipt, None, actors, scene)
        if receipt.outcome is None:
            raise RuntimeError("terminal Host receipt must contain an outcome")
        if not batch.public_performance_intent:
            raise ValueError("pending batch has no persisted public performance intent")
        ppi = PublicPerformanceIntent.from_dict(batch.public_performance_intent)
        outcome = receipt.outcome
        host_event = self._host_event(frame, batch.batch_id, batch.command_id, outcome, scene_id)
        try:
            draft = self.performance.realize(
                ppi, outcome, tuple(dict(item) for item in frame.recent_history[-3:])
            )
            draft.validate(ppi, outcome)
        except (RuntimeError, ValueError):
            follow_up = TurnEventFollowUp.create(
                follow_up_id=f"follow:{batch.batch_id}:host",
                parent_batch_id=batch.batch_id,
                branch_id=batch.branch_id,
                expected_batch_status="host_pending",
                lifecycle_status="performance_pending",
                performance_status="pending",
                events=(host_event,),
                host_receipt=_receipt_dict(receipt),
            )
            self.ledger.append_follow_up(follow_up)
            actors, scene = self._reduce(batch.expected_actor_revisions, scene_id)
            return TurnResult(batch, receipt, None, actors, scene)
        performance_event = self._performance_event(
            frame, batch.batch_id, batch.command_id, draft, ppi, scene_id
        )
        follow_up = TurnEventFollowUp.create(
            follow_up_id=f"follow:{batch.batch_id}:host",
            parent_batch_id=batch.batch_id,
            branch_id=batch.branch_id,
            expected_batch_status="host_pending",
            lifecycle_status="committed",
            performance_status="complete",
            events=(host_event, performance_event),
            host_receipt=_receipt_dict(receipt),
        )
        self.ledger.append_follow_up(follow_up)
        actors, scene = self._reduce(batch.expected_actor_revisions, scene_id)
        return TurnResult(batch, receipt, draft, actors, scene)


    def complete_pending_performance(
        self,
        *,
        frame: DecisionFrame,
        batch: TurnEventBatch,
        outcome: ResolvedOutcome,
        scene_id: str,
    ) -> TurnResult:
        """Retry only Y from the persisted public intent; never call Cognition again."""
        if self.ledger.batch_status(batch.branch_id, batch.batch_id) != "performance_pending":
            raise ValueError("batch is not waiting for performance")
        if not batch.public_performance_intent:
            raise ValueError("pending batch has no persisted public performance intent")
        ppi = PublicPerformanceIntent.from_dict(batch.public_performance_intent)
        draft = self.performance.realize(
            ppi, outcome, tuple(dict(item) for item in frame.recent_history[-3:])
        )
        draft.validate(ppi, outcome)
        event = self._performance_event(
            frame, batch.batch_id, batch.command_id, draft, ppi, scene_id
        )
        follow_up = TurnEventFollowUp.create(
            follow_up_id=f"follow:{batch.batch_id}:performance",
            parent_batch_id=batch.batch_id,
            branch_id=batch.branch_id,
            expected_batch_status="performance_pending",
            lifecycle_status="committed",
            performance_status="complete",
            events=(event,),
            host_receipt=batch.host_receipt,
        )
        self.ledger.append_follow_up(follow_up)
        actors, scene = self._reduce(batch.expected_actor_revisions, scene_id)
        receipt = HostReceipt(
            command_id=batch.command_id,
            status="completed",
            host_operation_id=str(batch.host_receipt.get("host_operation_id", "")),
            scene_revision_before=int(batch.host_receipt.get("scene_revision_before", 0)),
            scene_revision_after=int(batch.host_receipt.get("scene_revision_after", 0)),
            outcome=outcome,
        )
        return TurnResult(batch, receipt, draft, actors, scene)


    def _pending_batch(
        self, frame, command, receipt, actor_revisions, scene_revision, scene_id,
        appraisal_events, ppi,
    ):
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
            events=(*appraisal_events, event),
            public_performance_intent=ppi.to_dict(),
        )

    def _record_command(self, command, status, receipt=None) -> None:
        if not self.journal:
            return
        self.journal.record(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            branch_id=command.branch_id,
            turn_id=command.turn_id,
            actor_id=command.actor_id,
            immutable_command_hash=command.immutable_command_hash,
            expected_scene_revision=command.expected_scene_revision,
            status=status,
            host_receipt=_receipt_dict(receipt) if receipt else {},
        )

    def _appraisal_events(self, appraisal, frame, batch_id, command_id, scene_id):
        base = RuntimeEvent.create(
            batch_id=batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command_id,
            kind="npc.appraisal_committed",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={
                "subjective_observation": appraisal.subjective_observation,
                "grounded_refs": list(appraisal.grounded_refs),
            },
            evidence_refs=appraisal.grounded_refs,
            order=self._turn_number,
        )
        changes = tuple(
            RuntimeEvent.create(
                batch_id=batch_id,
                branch_id=self.branch_id,
                turn_id=frame.turn_id,
                command_id=command_id,
                kind="npc.emotion_changed",
                actor_id=frame.actor_id,
                scene_id=scene_id,
                payload=asdict(change),
                evidence_refs=appraisal.grounded_refs,
                order=self._turn_number,
            )
            for change in appraisal.changes
        )
        return (base, *changes)

    def _host_event(self, frame, batch_id, command_id, outcome, scene_id):
        return RuntimeEvent.create(
            batch_id=batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command_id,
            kind="world.action_resolved",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={"outcome": _outcome_dict(outcome), "mutation": asdict(outcome.mutation)},
            visible_to=tuple(frame.relationships),
            evidence_refs=outcome.evidence_refs,
            order=self._turn_number,
        )

    def _performance_event(self, frame, batch_id, command_id, draft, ppi, scene_id):
        return RuntimeEvent.create(
            batch_id=batch_id,
            branch_id=self.branch_id,
            turn_id=frame.turn_id,
            command_id=command_id,
            kind="performance.committed",
            actor_id=frame.actor_id,
            scene_id=scene_id,
            payload={
                "action": draft.action,
                "speech": draft.speech,
                "observable_outcome": list(draft.observable_outcome),
                "response_hook": draft.response_hook,
                "attention_target": draft.attention_target,
                "gaze": draft.gaze,
                "blocking": draft.blocking,
                "posture_change": draft.posture_change,
                "physical_residue": draft.physical_residue,
                "delivery": asdict(draft.delivery),
            },
            visible_to=tuple(frame.relationships),
            evidence_refs=ppi.authorization_refs,
            order=self._turn_number,
        )

    def _reduce(self, actor_revisions, scene_id):
        return reduce_events(
            self.ledger.events(self.branch_id), actor_ids=actor_revisions, scene_id=scene_id
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
        "mutation": asdict(outcome.mutation),
    }
