from __future__ import annotations

import json
import unittest

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.contracts import DecisionContract, DecisionFrame, ObservationView


def frame() -> DecisionFrame:
    return DecisionFrame(
        scene_id="door",
        turn_id="turn-1",
        actor_id="mia",
        private_state={"goal": "进去找母亲", "withheld": "不承认自己害怕机器人离开"},
        relationships={"guard": {"summary": "他控制入口", "disclosure": "closed"}},
        emotions={"fear": 0.3, "defiance": 0.2},
        observation=ObservationView(
            facts={"O.current": "闸门仍关闭，闸员要求先核验身份牌"},
            available_targets=("guard",),
            capabilities=("speak",),
        ),
        recent_history=(),
        continuity={"goal": "进去找母亲"},
        decision_contract=DecisionContract(allowed_actions=("speak",)),
        identity_evidence={"values": "不愿让别人替自己做决定"},
    )


def valid_output() -> str:
    return json.dumps(
        {
            "appraisal": {
                "subjective_observation": "他还不肯放我进去，身份牌成了他手里的门闩",
                "emotion_changes": [{"emotion": "defiance", "direction": "rise", "impact": "minor"}],
                "grounded_refs": ["O.current"],
            },
            "policy": {
                "attention": ["O.current"],
                "interpretation": "闸员在用核验拖住我",
                "current_intent": "逼他说明哪一项不通过",
                "chosen_strategy": "抓住眼前的核验要求追问",
                "action_request": {
                    "action_kind": "speak",
                    "target": "guard",
                    "arguments": {},
                    "required_capabilities": ["speak"],
                    "grounded_refs": ["O.current"],
                },
                "disclose": [{"kind": "question", "text": "哪一项不对？", "evidence_refs": []}],
                "withhold": ["不说机器人可能被留下"],
                "expected_response": "对方指出具体字段",
                "response_hook": "闸员可以指出核验失败的具体字段",
                "surface_action_intent": "把身份牌举到扫描头下",
                "accepted_cost": "",
                "relationship_transition": "keep",
                "interaction_move": "ask",
                "delivery_mode": "blunt",
                "public_move": "information",
                "disposition": "continue",
                "grounded_refs": ["O.current", "S.identity.values"],
            },
        },
        ensure_ascii=False,
    )


class CognitionTests(unittest.TestCase):
    def test_valid_output_builds_grounded_appraisal_and_policy(self) -> None:
        port = JsonCognitionPort(lambda messages, purpose: valid_output())
        appraisal, policy = port.decide(frame())
        self.assertEqual(appraisal.changes[0].emotion, "defiance")
        self.assertEqual(policy.action_request.target, "guard")
        self.assertEqual(policy.disclose[0].kind, "question")

    def test_protocol_failure_retries_with_feedback_without_changing_model(self) -> None:
        calls = []

        def complete(messages, purpose):
            calls.append(messages)
            return "{}" if len(calls) == 1 else valid_output()

        appraisal, _ = JsonCognitionPort(complete, max_attempts=2).decide(frame())
        self.assertEqual(len(calls), 2)
        self.assertIn("validation_feedback", calls[1][-1]["content"])
        self.assertEqual(appraisal.grounded_refs, ("O.current",))

    def test_invalid_output_exhaustion_is_not_silently_fabricated(self) -> None:
        port = JsonCognitionPort(lambda messages, purpose: "{}", max_attempts=2)
        with self.assertRaises(CognitionModelError):
            port.decide(frame())

    def test_semantic_alias_is_rejected_not_translated(self) -> None:
        raw = json.loads(valid_output())
        raw["appraisal"]["emotion_changes"] = [{"emotion": "concern", "direction": "rise", "impact": "minor"}]
        with self.assertRaises(CognitionModelError):
            JsonCognitionPort(
                lambda messages, purpose: json.dumps(raw, ensure_ascii=False), max_attempts=1
            ).decide(frame())


if __name__ == "__main__":
    unittest.main()
