from __future__ import annotations

import json
import unittest

from sceneactor.benchmark import BehaviorBenchmark
from sceneactor.cognition import JsonCognitionPort
from sceneactor.evaluation import FullBehaviorEvaluator
from sceneactor.performance import JsonPerformancePort
from sceneactor.review import BlindReviewer, JsonCounterfactualReviewPort
from tests.test_benchmark import benchmark_completion


def performance_completion(messages, purpose):
    assert purpose == "realization"
    payload = json.loads(messages[-1]["content"])
    intent = payload["intent"]
    speech = " ".join(item["text"] for item in intent["speech_atoms"])
    return json.dumps(
        {
            "action": "把手里的纸放回桌边",
            "speech": speech,
            "addressee": intent["target"],
            "attention_target": "眼前的人",
            "gaze": "看向对方",
            "blocking": "保持原位",
            "posture_change": "",
            "delivery": {"pace": "steady"},
            "physical_residue": "手仍压在纸边",
            "observable_outcome": payload["outcome"]["observable_facts"],
            "response_hook": intent["response_hook"],
        },
        ensure_ascii=False,
    )


def review_completion(lens, packet):
    assert packet["clean_transcript"]
    return {"pass": True, "score": 4, "verdict": f"{lens} pass", "problems": []}


class CounterfactualStub:
    def review(self, packet):
        return {"pass": True, "score": 4, "distinct_dimensions": ["attention"], "verdict": "different"}


class EvaluationTests(unittest.TestCase):
    def evaluator(self):
        return FullBehaviorEvaluator(
            cognition=JsonCognitionPort(benchmark_completion),
            performance=JsonPerformancePort(performance_completion),
            reviewer=BlindReviewer(review_completion),
            counterfactual_reviewer=CounterfactualStub(),
        )

    def test_full_case_contains_performance_and_four_reviews(self):
        case = BehaviorBenchmark.load("benchmarks/behavior_v1.json").cases[0]
        result = self.evaluator().evaluate_case(case, "actor-1")
        self.assertFalse(result.protocol_error)
        self.assertTrue(result.performance["speech"])
        self.assertEqual(len(result.review["reviews"]), 4)
        self.assertTrue(result.review["pass"])

    def test_role_swap_and_same_stimulus_are_blind_packets(self):
        cases = BehaviorBenchmark.load("benchmarks/behavior_v1.json").cases
        swap = self.evaluator().role_swap(cases[0], cases[1])
        self.assertNotIn("name", swap["A"]["character_card"])
        same = self.evaluator().same_stimulus(
            stimulus={"O.current": "门关闭"},
            personas=(cases[0].persona, cases[1].persona),
            target="guard",
        )
        self.assertEqual(len(same["actors"]), 2)
        self.assertTrue(same["counterfactual_review"]["pass"])


if __name__ == "__main__":
    unittest.main()
