from __future__ import annotations
import unittest

from sceneactor.contracts import (
    ActionIntent,
    Appraisal,
    DecisionContract,
    DecisionFrame,
    Delivery,
    EmotionChange,
    HostReceipt,
    ObservationView,
    PerformanceDraft,
    PerformancePolicy,
    ResolvedOutcome,
    WorldMutation,
)
from sceneactor.hosts import InMemorySceneHost
from sceneactor.runtime import TurnOrchestrator


class PendingHost(InMemorySceneHost):
    def __init__(self):
        super().__init__("s1", facts={"O.current": "门还关着"}, targets=("guard",))
        self.submit_calls = 0
        self.query_calls = 0

    def submit(self, command):
        self.submit_calls += 1
        self.received.append(command.command_id)
        return HostReceipt(command.command_id, "pending", "op:pending", self.revision, self.revision)

    def query(self, command_id):
        self.query_calls += 1
        return HostReceipt(
            command_id, "completed", "op:pending", 0, 1,
            ResolvedOutcome("succeeded", "speak", ("pending action completed",)),
        )


class FakeCognition:
    def decide(self, frame):
        appraisal = Appraisal(
            subjective_observation="门还关着，对方正在挡我",
            changes=(EmotionChange("fear", "rise", "minor"),),
            grounded_refs=("O.current",),
        )
        policy = PerformancePolicy(
            attention=("O.current",),
            interpretation="对方还没有让我通过",
            current_intent="让对方回答",
            chosen_strategy="直接问",
            action_request=ActionIntent("speak", "guard", {}, (), ("O.current",)),
            disclose=(),
            withhold=("不说自己其实很急",),
            expected_response="对方给出理由",
            response_hook="对方仍可回答为什么不让我进",
            surface_action_intent="看向门口的登记牌",
            accepted_cost="",
            relationship_transition="keep",
            interaction_move="ask",
            delivery_mode="probing",
            public_move="stance",
            disposition="continue",
            grounded_refs=("O.current",),
        )
        return appraisal, policy

class CountingCognition(FakeCognition):
    def __init__(self):
        self.calls = 0

    def decide(self, frame):
        self.calls += 1
        return super().decide(frame)




class FakePerformance:
    def realize(self, intent, outcome, recent_history):
        del recent_history
        return PerformanceDraft(
            actor_id=intent.actor_id,
            action="她看了一眼门口的登记牌。",
            speech="你先告诉我，为什么不能进去？",
            addressee=intent.target,
            attention_target="门口的登记牌",
            gaze="看向登记牌，再看向保安",
            blocking="站在门外，不越过门槛",
            posture_change="身体仍朝向门内",
            delivery=Delivery(pace="快一点", volume="低", breath="说到末尾停一下"),
            physical_residue="手指仍捏着湿掉的证件边角",
            observable_outcome=outcome.observable_facts,
            response_hook=intent.response_hook,
        )

class FailOncePerformance(FakePerformance):
    def __init__(self):
        self.calls = 0

    def realize(self, intent, outcome, recent_history):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary presentation failure")
        return super().realize(intent, outcome, recent_history)


class RuntimeTests(unittest.TestCase):
    def _frame(self, host):
        return DecisionFrame(
            scene_id="s1",
            turn_id="t1",
            actor_id="a",
            private_state={"goal": "进去"},
            relationships={"guard": {"summary": "挡在门口"}},
            emotions={"fear": 0.2},
            observation=host.observe("a"),
            recent_history=(),
            continuity={},
            decision_contract=DecisionContract(allowed_actions=("speak",)),
            identity_evidence={"values": "不喜欢被人替自己决定"},
        )

    def test_turn_commits_host_result_and_performance(self):
        host = InMemorySceneHost("s1", facts={"O.current": "门还关着"}, targets=("guard",))
        orchestrator = TurnOrchestrator(
            branch_id="branch-1",
            host=host,
            cognition=FakeCognition(),
            performance=FakePerformance(),
        )
        result = orchestrator.run_turn(
            frame=self._frame(host), actor_revisions={"a": 0}, scene_revision=0, scene_id="s1"
        )
        self.assertEqual(result.receipt.status, "completed")
        self.assertEqual(result.draft.addressee, "guard")
        self.assertEqual(len(result.actors["a"].recent_performance), 1)
        self.assertEqual(len(result.scene.task_evidence), 0)
        self.assertEqual(host.received.__len__(), 1)

    def test_appraisal_and_mutation_enter_replayed_state(self):
        host = InMemorySceneHost(
            "s1", facts={"O.current": "门还关着"}, targets=("guard",),
            outcomes={"speak": ResolvedOutcome(
                status="succeeded", action_kind="speak",
                observable_facts=("门锁状态被登记",),
                mutation=WorldMutation(location="门外", access={"door": "observed"}, task_evidence=("door:observed",)),
            )},
        )
        result = TurnOrchestrator(
            branch_id="branch-1", host=host, cognition=FakeCognition(), performance=FakePerformance()
        ).run_turn(frame=self._frame(host), actor_revisions={"a": 0}, scene_revision=0, scene_id="s1")
        state = result.actors["a"]
        self.assertEqual(state.last_appraisal["subjective_observation"], "门还关着，对方正在挡我")
        self.assertAlmostEqual(state.emotions["fear"], 0.08)
        self.assertEqual(result.scene.locations["a"], "门外")
        self.assertEqual(result.scene.access["door"], "observed")
        self.assertIn("door:observed", result.scene.task_evidence)

    def test_y_failure_retries_presentation_without_cognition_or_host_repeat(self):
        host = InMemorySceneHost("s1", facts={"O.current": "门还关着"}, targets=("guard",))
        cognition = CountingCognition()
        performance = FailOncePerformance()
        orchestrator = TurnOrchestrator(branch_id="branch-1", host=host, cognition=cognition, performance=performance)
        first = orchestrator.run_turn(frame=self._frame(host), actor_revisions={"a": 0}, scene_revision=0, scene_id="s1")
        self.assertEqual(first.batch.lifecycle, "performance_pending")
        self.assertEqual(cognition.calls, 1)
        self.assertEqual(len(host.received), 1)
        resumed = orchestrator.complete_pending_performance(
            frame=self._frame(host), batch=first.batch,
            outcome=first.receipt.outcome, scene_id="s1",
        )
        self.assertIsNotNone(resumed.draft)
        self.assertEqual(cognition.calls, 1)
        self.assertEqual(len(host.received), 1)

    def test_host_pending_queries_same_command_without_resubmit(self):
        host = PendingHost()
        orchestrator = TurnOrchestrator(
            branch_id="branch-1", host=host, cognition=FakeCognition(), performance=FakePerformance()
        )
        first = orchestrator.run_turn(
            frame=self._frame(host), actor_revisions={"a": 0}, scene_revision=0, scene_id="s1"
        )
        self.assertEqual(first.batch.lifecycle, "host_pending")
        resumed = orchestrator.resume_pending_host(
            frame=self._frame(host), batch=first.batch, scene_id="s1"
        )
        self.assertEqual(resumed.receipt.status, "completed")
        self.assertEqual(host.submit_calls, 1)
        self.assertEqual(host.query_calls, 1)
        self.assertIsNotNone(resumed.draft)


if __name__ == "__main__":
    unittest.main()
