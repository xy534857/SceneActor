from __future__ import annotations

import json
from dataclasses import replace
import unittest

from sceneactor.contracts import ActionIntent, PublicPerformanceIntent, ResolvedOutcome, SpeechAtom
from sceneactor.performance import JsonPerformancePort, PerformanceModelError


class PerformanceRepairTests(unittest.TestCase):
    def intent(self) -> PublicPerformanceIntent:
        return PublicPerformanceIntent(
            actor_id="actor",
            target="other",
            interaction_move="answer",
            authorized_action=ActionIntent("speak", "other", {}, ("speak",), ("O.current",)),
            speech_atoms=(SpeechAtom("fact", "我听见了。", ("O.current",)),),
            delivery_mode="restrained",
            visible_cost_signal="暴露立场",
            response_hook="等待对方继续。",
            disposition="continue",
            actor_constraints={"role": "人类"},
            public_evidence={"O.current": "对方在等回答"},
            authorization_refs=("O.current",),
        )

    def test_missing_delivery_is_repaired_without_rewriting_performance(self) -> None:
        calls: list[str] = []
        invalid = json.dumps({
            "action": "抬头", "speech": "这是模型擅自改写的台词。", "addressee": "other",
            "attention_target": "other", "gaze": "看向对方", "blocking": "原地",
            "posture_change": "", "delivery": {}, "physical_residue": "仍在原地",
            "observable_outcome": [], "response_hook": "等待对方继续。",
        }, ensure_ascii=False)

        def complete(messages, purpose):
            calls.append(purpose)
            if purpose == "realization":
                return invalid
            self.assertEqual(purpose, "delivery_repair")
            payload = json.loads(messages[-1]["content"])
            self.assertEqual(payload["speech"], "我听见了。")
            return json.dumps({"volume": "近距离可听清"}, ensure_ascii=False)

        outcome = ResolvedOutcome("succeeded", "speak", ("host-visible fact",))
        draft = JsonPerformancePort(complete).realize(self.intent(), outcome, ())
        self.assertEqual(calls, ["realization", "delivery_repair"])
        self.assertEqual(draft.action, "抬头")
        self.assertEqual(draft.speech, "我听见了。")
        self.assertEqual(draft.delivery.volume, "近距离可听清")
        self.assertEqual(draft.observable_outcome, ("host-visible fact",))
        with self.assertRaisesRegex(ValueError, "authorized speech"):
            replace(draft, speech="擅自改写").validate(self.intent(), outcome)

    def test_exhausted_repairs_raise_instead_of_returning_empty_performance(self) -> None:
        invalid = json.dumps({"speech": "我听见了。", "delivery": {}, "response_hook": "等待对方继续。"}, ensure_ascii=False)
        with self.assertRaisesRegex(PerformanceModelError, "audible delivery"):
            JsonPerformancePort(lambda messages, purpose: invalid, max_attempts=2).realize(
                self.intent(), ResolvedOutcome("succeeded", "speak"), ()
            )


if __name__ == "__main__":
    unittest.main()
