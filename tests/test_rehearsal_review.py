from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sceneactor.contracts import ActionIntent, Appraisal, DecisionContract, DecisionFrame, EmotionChange, PerformancePolicy
from sceneactor.governance import scan_semantic_hardcode
from sceneactor.hosts import InMemorySceneHost
from sceneactor.persona import Persona, VoiceProfile
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.runtime import CognitionPort
from sceneactor.contracts import SpeechAtom
from tests.test_runtime import FailOncePerformance, FakeCognition, FakePerformance


class AlternateCognition:
    def __init__(self, actor_id: str):
        self.actor_id = actor_id

    def decide(self, frame: DecisionFrame):
        del frame
        return FakeCognition().decide(
            DecisionFrame(
                scene_id="s", turn_id="t", actor_id=self.actor_id,
                private_state={}, relationships={}, emotions={},
                observation=type("O", (), {"facts": {"O.current": "门仍关闭"}, "available_targets": ("guard",), "capabilities": ("speak",)})(),
                recent_history=(), continuity={},
                decision_contract=DecisionContract(allowed_actions=("speak",)),
            )
        )



class CapturingCognition:
    def __init__(self):
        self.frames = []

    def decide(self, frame: DecisionFrame):
        self.frames.append(frame)
        return FakeCognition().decide(frame)


class CapturingPerformance(FakePerformance):
    def __init__(self):
        self.intents = []

    def realize(self, intent, outcome, recent_history):
        self.intents.append(intent)
        return super().realize(intent, outcome, recent_history)


class ScriptedIntentCognition:
    """Turn 1: rewrite forming an intent; later turns: continue carrying it."""

    def __init__(self):
        self.frames = []

    def decide(self, frame: DecisionFrame):
        self.frames.append(frame)
        appraisal, policy = FakeCognition().decide(frame)
        from dataclasses import replace
        if "standing_intent" not in frame.private_state:
            return appraisal, replace(
                policy,
                intent_mode="rewrite",
                case_notes=("对方压线过一本", "孩子想学软件"),
            )
        return appraisal, replace(
            policy,
            intent_mode="continue",
            current_intent="",
            chosen_strategy="",
            expected_response="",
            withhold=(),
        )

class RehearsalReviewTests(unittest.TestCase):
    def test_two_actor_rehearsal_alternates_and_caps(self) -> None:
        license_entries = [{"form": "verdict_maxim", "shape": "判决后总结", "trigger": "咨询链收束", "budget": "一场两次", "evidence": "一手转录"}]
        personas = (
            Persona("a", "甲", role="辩手", values="不愿被安排", voice=VoiceProfile(entry_point="先抓对方上一句"),
                    extensions={"register_license": license_entries}),
            Persona("b", "乙", role="辩手", values="先把手续做完", voice=VoiceProfile(entry_point="先纠正具体事实")),
        )
        cognition_a = CapturingCognition()
        cognition_b = CapturingCognition()
        scene = SceneSetup("s", "双方通过手机视频连线，不在同一空间", "雨里有人等着", ("door",), max_turns=2)
        host = InMemorySceneHost("s", facts={"O.current": "门仍关闭"}, targets=("guard",))
        performance = CapturingPerformance()
        run = create_rehearsal(
            scene,
            (ActorSetup(personas[0], "进入"), ActorSetup(personas[1], "完成登记")),
            host=host,
            cognition={"a": cognition_a, "b": cognition_b},
            performance=performance,
        )
        first = run.advance()
        second = run.advance()
        self.assertEqual(first.draft.actor_id, "a")
        self.assertEqual(second.draft.actor_id, "b")
        self.assertTrue(run.complete)
        self.assertEqual(cognition_a.frames[0].identity_evidence["voice"]["entry_point"], "先抓对方上一句")
        self.assertEqual(cognition_a.frames[0].identity_evidence["scene_setting"], scene.setting)
        self.assertEqual(performance.intents[0].actor_constraints["scene_setting"], scene.setting)
        self.assertEqual(cognition_a.frames[0].identity_evidence["register_license"], license_entries)
        self.assertNotIn("register_license", cognition_b.frames[0].identity_evidence)
        heard = cognition_b.frames[0].recent_history[0]
        self.assertEqual(heard["speech"], first.draft.speech)
        self.assertEqual(heard["action"], first.draft.action)
        self.assertEqual(heard["actor_id"], "a")
        with self.assertRaisesRegex(RuntimeError, "complete"):
            run.advance()

    def test_rehearsal_retries_pending_performance_without_redeciding(self) -> None:
        personas = (Persona("a", "甲"), Persona("b", "乙"))
        cognition_a = CapturingCognition()
        performance = FailOncePerformance()
        run = create_rehearsal(
            SceneSetup("s", "门口", "雨里有人等着", ("door",), max_turns=1),
            (ActorSetup(personas[0], "进入"), ActorSetup(personas[1], "登记")),
            host=InMemorySceneHost("s", facts={"O.current": "门仍关闭"}, targets=("guard",)),
            cognition={"a": cognition_a, "b": FakeCognition()},
            performance=performance,
        )

        result = run.advance("a")

        self.assertIsNotNone(result.draft)
        self.assertEqual(len(cognition_a.frames), 1)
        self.assertEqual(performance.calls, 2)

    def test_standing_intent_carries_across_turns_with_case_notes(self) -> None:
        cognition_a = ScriptedIntentCognition()
        run = create_rehearsal(
            SceneSetup("s", "直播连麦", "家长接入", ("desk",), max_turns=4),
            (ActorSetup(Persona("a", "甲"), "劝住对方"), ActorSetup(Persona("b", "乙"), "要背书")),
            host=InMemorySceneHost("s", facts={"O.current": "对方在等回答"}, targets=("guard",)),
            cognition={"a": cognition_a, "b": FakeCognition()},
            performance=FakePerformance(),
        )

        first = run.advance("a")
        run.advance("b")
        run.advance("a")

        # turn 1 was a rewrite: intent formed and stored
        self.assertEqual(first.policy.intent_mode, "rewrite")
        standing = cognition_a.frames[1].private_state["standing_intent"]
        self.assertEqual(standing["intent"], "让对方回答")
        self.assertEqual(standing["strategy"], "直接问")
        self.assertEqual(standing["case_notes"], ["对方压线过一本", "孩子想学软件"])
        # first turn had no standing intent
        self.assertNotIn("standing_intent", cognition_a.frames[0].private_state)
        # continue turn carried the notes forward untouched in run state
        self.assertEqual(
            run.standing_intents["a"]["case_notes"], ["对方压线过一本", "孩子想学软件"]
        )

    def test_continue_without_standing_intent_is_rejected(self) -> None:
        frame = DecisionFrame(
            scene_id="s", turn_id="t", actor_id="a",
            private_state={}, relationships={}, emotions={},
            observation=type("O", (), {"facts": {"O.current": "对方在等"}, "affordances": {}, "available_targets": ("guard",), "capabilities": ("speak",)})(),
            recent_history=(), continuity={},
            decision_contract=DecisionContract(allowed_actions=("speak",)),
        )
        from dataclasses import replace
        _, policy = FakeCognition().decide(frame)
        bad = replace(policy, intent_mode="continue")
        with self.assertRaisesRegex(ValueError, "standing intent"):
            bad.validate(frame)

    def test_rewrite_over_standing_intent_requires_trigger(self) -> None:
        frame = DecisionFrame(
            scene_id="s", turn_id="t", actor_id="a",
            private_state={"standing_intent": {"intent": "旧意图", "strategy": "旧策略"}},
            relationships={}, emotions={},
            observation=type("O", (), {"facts": {"O.current": "对方在等"}, "affordances": {}, "available_targets": ("guard",), "capabilities": ("speak",)})(),
            recent_history=(), continuity={},
            decision_contract=DecisionContract(allowed_actions=("speak",)),
        )
        from dataclasses import replace
        _, policy = FakeCognition().decide(frame)
        with self.assertRaisesRegex(ValueError, "trigger"):
            replace(policy, intent_mode="rewrite", rewrite_trigger="").validate(frame)
        replace(policy, intent_mode="rewrite", rewrite_trigger="对方第三次说一本就是一本").validate(frame)

    def test_governance_linter_is_outside_runtime_and_detects_quality_tables(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad.py"
            path.write_text("_GENERIC_PUBLIC_ATOM_TOKENS = {'x'}\n", encoding="utf-8")
            findings = scan_semantic_hardcode((path,))
        self.assertEqual(findings[0].code, "semantic_word_table")

    def test_governance_linter_rejects_local_semantic_alias_tables(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad_alias.py"
            path.write_text("aliases = {'concern': 'fear'}\n", encoding="utf-8")
            findings = scan_semantic_hardcode((path,))
        self.assertEqual(findings[0].code, "semantic_word_table")


if __name__ == "__main__":
    unittest.main()
