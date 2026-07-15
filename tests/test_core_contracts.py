from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sceneactor.contracts import (
    ActionIntent,
    DecisionContract,
    DecisionFrame,
    EmotionChange,
    ObservationView,
    PerformancePolicy,
    PublicPerformanceIntent,
    ResolvedOutcome,
)
from sceneactor.events import EventLedger, HostCommandJournal, JsonlEventStore, RuntimeEvent, TurnEventBatch, TurnEventFollowUp
from sceneactor.persona import Persona
from sceneactor.reducers import reduce_events


class CoreContractTests(unittest.TestCase):
    def _frame(self) -> DecisionFrame:
        return DecisionFrame(
            scene_id="scene-1",
            turn_id="turn-1",
            actor_id="a",
            private_state={"goal": "留在门口"},
            relationships={"b": {"summary": "对方挡住去路"}},
            emotions={"fear": 0.4},
            observation=ObservationView(
                facts={"O.current": "门仍关闭", "H.0": "对方说：先登记"},
                available_targets=("b", "door"),
                capabilities=("speak", "interact"),
            ),
            recent_history=({"role": "npc", "content": "我先看看门。"},),
            continuity={"L.goal": "留在门口"},
            decision_contract=DecisionContract(
                allowed_actions=("speak", "interact"),
                required_arguments={"interact": ("affordance_ref",)},
            ),
            identity_evidence={"values": "不愿被别人替自己决定"},
        )

    def test_persona_does_not_infer_missing_identity(self) -> None:
        persona = Persona.from_dict({"id": "a", "name": "甲"})
        self.assertEqual(persona.gender, "")
        self.assertEqual(persona.age, "")

    def test_policy_requires_grounded_action_and_public_projection(self) -> None:
        frame = self._frame()
        action = ActionIntent("speak", "b", {}, (), ("O.current",))
        policy = PerformancePolicy(
            attention=("O.current",),
            interpretation="门还没开，对方在阻止我进入",
            current_intent="让对方先回答",
            chosen_strategy="直接问",
            action_request=action,
            disclose=(),
            withhold=("不说我很怕被留下",),
            expected_response="对方解释登记条件",
            response_hook="对方仍可回答是否能让我进门",
            surface_action_intent="看向门锁",
            accepted_cost="",
            relationship_transition="keep",
            interaction_move="ask",
            delivery_mode="blunt",
            public_move="stance",
            disposition="continue",
            grounded_refs=("O.current",),
        )
        intent = PublicPerformanceIntent.from_policy("a", policy, frame)
        self.assertEqual(intent.target, "b")
        self.assertEqual(intent.evidence_anchors["O.current"], "门仍关闭")
        self.assertNotIn("不说我很怕被留下", intent.evidence_anchors)

    def test_action_rejects_ungrounded_claim(self) -> None:
        frame = self._frame()
        action = ActionIntent("speak", "b", {}, (), ("O.missing",))
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            action.validate(frame)

    def test_event_ledger_is_idempotent_and_rejects_reused_batch(self) -> None:
        event = RuntimeEvent.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            kind="scene.started", actor_id="a", scene_id="s", payload={}, order=1,
        )
        batch = TurnEventBatch.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            expected_actor_revisions={"a": 0}, expected_scene_revision=0,
            host_receipt={"status": "completed"}, lifecycle="committed",
            performance_status="complete", events=(event,),
        )
        ledger = EventLedger()
        self.assertTrue(ledger.append_batch(batch))
        self.assertFalse(ledger.append_batch(batch))
        conflicting = TurnEventBatch.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            expected_actor_revisions={"a": 0}, expected_scene_revision=0,
            host_receipt={"status": "completed", "other": True}, lifecycle="committed",
            performance_status="complete", events=(),
        )
        with self.assertRaisesRegex(ValueError, "different content"):
            ledger.append_batch(conflicting)

    def test_follow_up_requires_pending_parent(self) -> None:
        event = RuntimeEvent.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            kind="world.action_resolved", actor_id="a", scene_id="s", payload={}, order=1,
        )
        batch = TurnEventBatch.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            expected_actor_revisions={"a": 0}, expected_scene_revision=0,
            host_receipt={"status": "completed"}, lifecycle="performance_pending",
            performance_status="pending", events=(event,),
        )
        follow_event = RuntimeEvent.create(
            batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
            kind="performance.committed", actor_id="a", scene_id="s", payload={}, order=2,
        )
        follow = TurnEventFollowUp.create(
            follow_up_id="f1", parent_batch_id="b1", branch_id="branch",
            expected_batch_status="performance_pending", lifecycle_status="committed",
            performance_status="complete", events=(follow_event,), host_receipt={},
        )
        ledger = EventLedger()
        ledger.append_batch(batch)
        self.assertTrue(ledger.append_follow_up(follow))
        self.assertFalse(ledger.append_follow_up(follow))
        self.assertEqual(ledger.batch_status("branch", "b1"), "committed")

    def test_jsonl_store_recovers_batches(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "events.jsonl"
            event = RuntimeEvent.create(
                batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
                kind="scene.started", actor_id="a", scene_id="s", payload={}, order=1,
            )
            batch = TurnEventBatch.create(
                batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
                expected_actor_revisions={"a": 0}, expected_scene_revision=0,
                host_receipt={}, lifecycle="committed", performance_status="complete",
                events=(event,),
            )
            JsonlEventStore(path).append_batch(batch)
            restored = JsonlEventStore(path)
            self.assertEqual(len(restored.ledger.events("branch")), 1)

    def test_reducer_replay_is_deterministic(self) -> None:
        events = (
            RuntimeEvent.create(
                batch_id="b1", branch_id="branch", turn_id="t1", command_id="c1",
                kind="npc.goal_started", actor_id="a", scene_id="s",
                payload={"goal_id": "g", "description": "留下"}, order=1,
            ),
            RuntimeEvent.create(
                batch_id="b2", branch_id="branch", turn_id="t2", command_id="c2",
                kind="npc.emotion_changed", actor_id="a", scene_id="s",
                payload={"emotion": "fear", "direction": "rise", "impact": "moderate"}, order=2,
            ),
        )
        left = reduce_events(events, actor_ids=("a",), scene_id="s")
        right = reduce_events(events, actor_ids=("a",), scene_id="s")
        self.assertEqual(left[0]["a"].to_dict(), right[0]["a"].to_dict())
        self.assertEqual(left[0]["a"].emotions["fear"], 0.2)

    def test_host_journal_persists_before_external_retry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "commands.jsonl"
            journal = HostCommandJournal(path)
            journal.record(
                command_id="c1", idempotency_key="action:c1", branch_id="b",
                turn_id="t", actor_id="a", immutable_command_hash="hash",
                expected_scene_revision=0, status="prepared",
            )
            restored = HostCommandJournal(path)
            record = restored.get("c1")
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "prepared")


if __name__ == "__main__":
    unittest.main()
