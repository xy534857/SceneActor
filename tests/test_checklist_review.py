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
        return json.dumps({"items": items})


class ChecklistReviewTests(unittest.TestCase):
    def test_all_pass_is_the_only_way_through(self) -> None:
        port = JsonBlindReviewPort(FakeChecklistModel())
        result = port("dialogue", {"clean_transcript": []})
        self.assertEqual(result["kind"], "binary_checklist")
        self.assertEqual(result["bits_passed"], result["bits_total"])
        self.assertEqual(result["score"], 5)
        self.assertTrue(result["pass"])
        self.assertEqual(result["failed"], [])

    def test_any_single_failure_fails_the_gate(self) -> None:
        for key in JsonBlindReviewPort.DIALOGUE_CHECKLIST:
            port = JsonBlindReviewPort(FakeChecklistModel(fail_keys=(key,)))
            result = port("dialogue", {"clean_transcript": []})
            self.assertFalse(result["pass"], f"{key} should be fatal")
            self.assertEqual(result["failed"], [key])
            self.assertIn(f"{key}: ev-{key}", result["problems"])

    def test_missing_item_is_rejected_not_defaulted(self) -> None:
        class BrokenModel:
            def __call__(self, messages, purpose):
                return json.dumps({"items": {"read_aloud": {"pass": 1, "evidence": "x"}}})

        port = JsonBlindReviewPort(BrokenModel(), max_attempts=1)
        with self.assertRaises(ValueError):
            port("dialogue", {"clean_transcript": []})

    def test_reviewer_aggregation_exposes_checklist(self) -> None:
        reviewer = BlindReviewer(
            JsonBlindReviewPort(FakeChecklistModel(fail_keys=("persona_fidelity",))),
            lenses=("dialogue",),
        )
        outcome = reviewer.review({"setting": "x"}, [])
        entry = outcome["reviews"][0]
        self.assertIsNotNone(entry["checklist"])
        self.assertFalse(entry["passed"])


if __name__ == "__main__":
    unittest.main()
