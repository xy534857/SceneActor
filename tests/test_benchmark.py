from __future__ import annotations

import json
import unittest

from sceneactor.benchmark import BehaviorBenchmark, same_stimulus_frames
from sceneactor.cognition import JsonCognitionPort


def benchmark_completion(messages, purpose):
    assert purpose == "cognition"
    payload = json.loads(messages[-1]["content"])
    observation_ref = next(key for key in payload["evidence_refs"] if key.startswith("O."))
    identity_refs = [key for key in payload["evidence_refs"] if key.startswith("S.identity.")]
    target = payload["available_targets"][0]
    action = payload["decision_contract"]["allowed_actions"][0]
    refs = [observation_ref, *identity_refs[:1]]
    return json.dumps(
        {
            "appraisal": {
                "subjective_observation": "眼前的阻力要求我先作出回应",
                "emotion_changes": [],
                "grounded_refs": [observation_ref],
            },
            "policy": {
                "attention": [observation_ref],
                "interpretation": "这件事还没有解决",
                "current_intent": "让局面产生一个可回答的下一步",
                "chosen_strategy": "回应眼前具体问题",
                "action_request": {
                    "action_kind": action,
                    "target": target,
                    "arguments": {},
                    "required_capabilities": [action],
                    "grounded_refs": [observation_ref],
                },
                "disclose": [{"kind": "question", "text": "现在卡在哪一步？", "evidence_refs": []}] if action == "speak" else [],
                "withhold": [],
                "expected_response": "对方说明当前阻力",
                "response_hook": "对方可以说明当前阻力",
                "surface_action_intent": "",
                "accepted_cost": "",
                "relationship_transition": "keep",
                "interaction_move": "ask" if action == "speak" else "pause",
                "delivery_mode": "practical",
                "public_move": "information" if action == "speak" else "stance",
                "disposition": "continue",
                "grounded_refs": refs,
            },
        },
        ensure_ascii=False,
    )


class BenchmarkTests(unittest.TestCase):
    def test_frozen_benchmark_runs_and_anonymizes_identity(self) -> None:
        benchmark = BehaviorBenchmark.load("benchmarks/behavior_v1.json")
        records = benchmark.run(JsonCognitionPort(benchmark_completion))
        self.assertEqual(len(records), 6)
        self.assertEqual(records[0].anonymous_packet["anonymous_actor"], "actor-1")
        packet_text = json.dumps(records[0].anonymous_packet, ensure_ascii=False)
        self.assertNotIn("林妲", packet_text)
        self.assertNotIn("linda", packet_text)

    def test_same_stimulus_changes_only_identity_evidence(self) -> None:
        frames = same_stimulus_frames(
            stimulus={"O.current": "门关闭，外面开始下雨"},
            personas=(
                {"id": "a", "values": "先保护同行的人"},
                {"id": "b", "values": "先确认门为什么关闭"},
            ),
            target="guard",
        )
        self.assertEqual(frames[0].observation.facts, frames[1].observation.facts)
        self.assertNotEqual(frames[0].identity_evidence, frames[1].identity_evidence)
        self.assertEqual(frames[0].decision_contract, frames[1].decision_contract)


if __name__ == "__main__":
    unittest.main()
