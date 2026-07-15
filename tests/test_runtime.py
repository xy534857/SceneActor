from __future__ import annotations

import unittest

from sceneactor.contracts import (
    ActionIntent,
    Appraisal,
    DecisionContract,
    DecisionFrame,
    Delivery,
    EmotionChange,
    ObservationView,
    PerformanceDraft,
    PerformancePolicy,
)
from sceneactor.hosts import InMemorySceneHost
from sceneactor.runtime import TurnOrchestrator


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


if __name__ == "__main__":
    unittest.main()
