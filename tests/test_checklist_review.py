from __future__ import annotations

import json
import unittest

from sceneactor.review import BlindReviewer, JsonBlindReviewPort


class FakeChecklistModel:
    """Returns a fixed checklist verdict; records the prompt for assertions."""

    def __init__(self, fail_keys: tuple[str, ...] = ()) -> None:
        self.fail_keys = fail_keys
        self.prompts: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], purpose: str) -> str:
        self.prompts.append(messages)
        items = {
            key: {"pass": 0 if key in self.fail_keys else 1, "evidence": f"ev-{key}"}
            for key in JsonBlindReviewPort.DIALOGUE_CHECKLIST
        }
        return json.dumps({"items": items, "worst_failures": list(self.fail_keys)[:3]})


class ChecklistReviewTests(unittest.TestCase):
    def test_all_pass_maps_to_top_score(self) -> None:
        port = JsonBlindReviewPort(FakeChecklistModel())
        result = port("dialogue", {"clean_transcript": []})
        self.assertEqual(result["kind"], "binary_checklist")
        self.assertEqual(result["bits_passed"], result["bits_total"])
        self.assertEqual(result["score"], 5)
        self.assertTrue(result["pass"])

    def test_failures_reduce_bits_and_carry_evidence(self) -> None:
        port = JsonBlindReviewPort(FakeChecklistModel(fail_keys=("no_template_turns", "listens_and_reacts")))
        result = port("dialogue", {"clean_transcript": []})
        total = result["bits_total"]
        self.assertEqual(result["bits_passed"], total - 2)
        self.assertIn("no_template_turns: ev-no_template_turns", result["problems"])
        self.assertTrue(result["pass"])  # within 3-miss tolerance

    def test_four_failures_fail_the_gate(self) -> None:
        fails = tuple(list(JsonBlindReviewPort.DIALOGUE_CHECKLIST)[:4])
        port = JsonBlindReviewPort(FakeChecklistModel(fail_keys=fails))
        result = port("dialogue", {"clean_transcript": []})
        self.assertFalse(result["pass"])

    def test_missing_item_is_rejected_not_defaulted(self) -> None:
        class BrokenModel:
            def __call__(self, messages, purpose):
                return json.dumps({"items": {"idiomatic_speech": {"pass": 1, "evidence": "x"}}})

        port = JsonBlindReviewPort(BrokenModel(), max_attempts=1)
        with self.assertRaises(ValueError):
            port("dialogue", {"clean_transcript": []})

    def test_reviewer_aggregation_exposes_checklist(self) -> None:
        reviewer = BlindReviewer(
            JsonBlindReviewPort(FakeChecklistModel(fail_keys=("tic_budget",))),
            lenses=("dialogue",),
        )
        outcome = reviewer.review({"setting": "x"}, [])
        entry = outcome["reviews"][0]
        self.assertIsNotNone(entry["checklist"])
        self.assertEqual(entry["checklist"]["bits_passed"], entry["checklist"]["bits_total"] - 1)


if __name__ == "__main__":
    unittest.main()
