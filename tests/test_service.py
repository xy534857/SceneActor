from __future__ import annotations

import json
import unittest

from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup
from sceneactor.service import (
    ProductionCompileError,
    ProductionSpec,
    SceneEpisode,
    compile_production,
    perform,
)
from tests.test_runtime import FakeCognition, FakePerformance


def _actors() -> tuple[ActorSetup, ...]:
    return (
        ActorSetup(Persona("a", "甲"), "进入"),
        ActorSetup(Persona("guard", "乙"), "登记"),
    )


def _episode(scene_id: str = "s1", max_turns: int = 2) -> SceneEpisode:
    return SceneEpisode(
        scene=SceneSetup(scene_id, "门口", "雨里有人等着", ("door",), max_turns=max_turns),
        host_facts={"O.weather": "下着雨"},
    )


def _spec(episodes: tuple[SceneEpisode, ...] = ()) -> ProductionSpec:
    return ProductionSpec(actors=_actors(), episodes=episodes or (_episode(),))


class ProductionSpecTests(unittest.TestCase):
    def test_round_trips_through_dict(self) -> None:
        spec = _spec((_episode("s1"), _episode("s2")))
        restored = ProductionSpec.from_dict(spec.to_dict())
        self.assertEqual([e.scene.scene_id for e in restored.episodes], ["s1", "s2"])
        self.assertEqual([a.persona.id for a in restored.actors], ["a", "guard"])
        self.assertEqual(restored.episodes[0].host_facts, {"O.weather": "下着雨"})

    def test_accepts_legacy_single_scene_form(self) -> None:
        legacy = {
            "scene": {"scene_id": "s", "setting": "门口", "opening": "雨里有人等着", "affordances": ["door"], "max_turns": 2},
            "actors": _spec().to_dict()["actors"],
            "host_facts": {"O.weather": "下着雨"},
        }
        restored = ProductionSpec.from_dict(legacy)
        self.assertEqual(len(restored.episodes), 1)
        self.assertEqual(restored.episodes[0].host_facts, {"O.weather": "下着雨"})

    def test_rejects_duplicates_bad_facts_and_unknown_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            ProductionSpec(
                actors=(ActorSetup(Persona("a", "甲"), "x"), ActorSetup(Persona("a", "乙"), "y")),
                episodes=(_episode(),),
            )
        with self.assertRaisesRegex(ValueError, "duplicate scene_id"):
            ProductionSpec(actors=_actors(), episodes=(_episode("s1"), _episode("s1")))
        with self.assertRaisesRegex(ValueError, "O\\."):
            SceneEpisode(scene=_episode().scene, host_facts={"weather": "雨"})
        with self.assertRaisesRegex(ValueError, "unknown actors"):
            ProductionSpec(
                actors=_actors(),
                episodes=(SceneEpisode(scene=_episode().scene, speaking_order=("ghost",)),),
            )


class CompileTests(unittest.TestCase):
    def test_model_output_is_repaired_with_validator_feedback(self) -> None:
        calls: list[str] = []
        good = json.dumps(_spec().to_dict(), ensure_ascii=False)

        def complete(messages, purpose):
            calls.append(purpose)
            payload = json.loads(messages[-1]["content"])
            if len(calls) == 1:
                self.assertEqual(payload["validation_feedback"], "")
                return "{\"scenes\": [], \"actors\": []}"
            self.assertTrue(payload["validation_feedback"])
            return good

        spec = compile_production("剧本：门口登记。", complete)
        self.assertEqual(len(calls), 2)
        self.assertEqual(spec.episodes[0].scene.opening, "雨里有人等着")

    def test_exhausted_repairs_raise(self) -> None:
        with self.assertRaises(ProductionCompileError):
            compile_production("剧本", lambda messages, purpose: "not json", max_attempts=2)


class PerformTests(unittest.TestCase):
    def test_multi_scene_run_carries_intent_and_context(self) -> None:
        seen: list[tuple[str, int, str]] = []
        seen_frames: list = []

        class RecordingCognition(FakeCognition):
            def decide(self, frame):
                seen_frames.append(frame)
                appraisal, policy = super().decide(frame)
                if "standing_intent" in frame.private_state:
                    from dataclasses import replace
                    policy = replace(policy, rewrite_trigger="对方还堵着门")
                return appraisal, policy

        document = perform(
            _spec((_episode("s1"), _episode("s2"))),
            generation=lambda messages, purpose: "",
            cognition_factory=lambda model: RecordingCognition(),
            performance_factory=lambda model: FakePerformance(),
            on_turn=lambda scene, turn, actor: seen.append((scene, turn, actor)),
        )
        self.assertTrue(document["completed"])
        self.assertEqual(len(document["scenes"]), 2)
        self.assertEqual(len(document["turns"]), 4)
        self.assertEqual(seen, [("s1", 1, "a"), ("s1", 2, "guard"), ("s2", 1, "a"), ("s2", 2, "guard")])
        # scene 2 frames carry the previous scene's closing context
        scene2_frames = seen_frames[2:]
        self.assertIn("previous_scene", scene2_frames[0].private_state)
        self.assertIn("上一场", scene2_frames[0].private_state["previous_scene"])
        # standing intent formed in scene 1 arrives in scene 2
        self.assertIn("standing_intent", scene2_frames[0].private_state)
        self.assertEqual(scene2_frames[0].private_state["standing_intent"]["intent"], "让对方回答")

    def test_single_scene_document_shape(self) -> None:
        document = perform(
            _spec(),
            generation=lambda messages, purpose: "",
            cognition_factory=lambda model: FakeCognition(),
            performance_factory=lambda model: FakePerformance(),
        )
        self.assertTrue(document["completed"])
        self.assertEqual(len(document["turns"]), 2)
        self.assertEqual(document["scene"]["characters"][0]["anonymous_actor"], "a")
        self.assertNotIn("blind_review", document)

    def test_scene_failure_does_not_abort_remaining_scenes(self) -> None:
        """Regression: a protocol failure in scene 1 must not swallow scene 2.

        Observed in production (job:a1dca993e019): a 13-scene spec returned a
        single scene because the first protocol failure broke the episode loop.
        """
        from sceneactor.cognition import CognitionModelError

        class FlakyCognition(FakeCognition):
            calls = 0

            def decide(self, frame):
                FlakyCognition.calls += 1
                if FlakyCognition.calls == 1:
                    raise CognitionModelError("Protocol validation failed: bad policy")
                return super().decide(frame)

        seen: list[tuple[str, int, str]] = []
        document = perform(
            _spec((_episode("s1"), _episode("s2"))),
            generation=lambda messages, purpose: "",
            cognition_factory=lambda model: FlakyCognition(),
            performance_factory=lambda model: FakePerformance(),
            on_turn=lambda scene, turn, actor: seen.append((scene, turn, actor)),
        )
        self.assertEqual([s["scene_id"] for s in document["scenes"]], ["s1", "s2"])
        self.assertEqual(document["scenes"][0]["turns"], [])
        self.assertIn("bad policy", document["scenes"][0]["protocol_failure"])
        self.assertEqual(len(document["scenes"][1]["turns"]), 2)
        self.assertEqual(document["scenes"][1]["protocol_failure"], "")
        self.assertFalse(document["completed"])
        self.assertEqual(len(document["scene_failures"]), 1)
        # scene 2 turns were reported to the progress callback
        self.assertEqual([s for s, _, _ in seen], ["s2", "s2"])

    def test_withdraw_exit_closes_scene_early_and_counts_complete(self) -> None:
        """An actor leaving via withdraw+exit ends the scene as a natural close,
        not a failure: remaining turns are skipped, completed stays True."""
        from sceneactor.contracts import ActionIntent, PerformancePolicy, SpeechAtom

        class WithdrawCognition(FakeCognition):
            calls = 0

            def decide(self, frame):
                WithdrawCognition.calls += 1
                appraisal, policy = super().decide(frame)
                if WithdrawCognition.calls < 2:
                    return appraisal, policy
                leaving = PerformancePolicy(
                    attention=policy.attention,
                    interpretation=policy.interpretation,
                    current_intent="离开这里",
                    chosen_strategy="道别后离开",
                    action_request=ActionIntent("exit", "", {}, (), ("O.current",)),
                    disclose=(SpeechAtom("close", "走了走了。"),),
                    withhold=(),
                    expected_response="",
                    response_hook="",
                    surface_action_intent="转身出门",
                    accepted_cost="",
                    relationship_transition="keep",
                    interaction_move="exit",
                    delivery_mode="restrained",
                    public_move="clean_close",
                    disposition="withdraw",
                    grounded_refs=("O.current",),
                )
                return appraisal, leaving

        class EchoPerformance(FakePerformance):
            def realize(self, intent, outcome, recent_history):
                draft = super().realize(intent, outcome, recent_history)
                from dataclasses import replace
                speech = "".join(a.text.strip() for a in intent.speech_atoms)
                return replace(draft, speech=speech, response_hook=intent.response_hook)

        document = perform(
            _spec((_episode("s1", max_turns=6),)),
            generation=lambda messages, purpose: "",
            cognition_factory=lambda model: WithdrawCognition(),
            performance_factory=lambda model: EchoPerformance(),
        )
        scene = document["scenes"][0]
        self.assertEqual(len(scene["turns"]), 2)
        self.assertIn("withdraw", scene["closed_early"])
        self.assertEqual(scene["protocol_failure"], "")
        self.assertTrue(document["completed"])


if __name__ == "__main__":
    unittest.main()
