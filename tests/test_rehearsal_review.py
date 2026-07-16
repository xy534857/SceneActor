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
from tests.test_runtime import FakeCognition, FakePerformance


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

class RehearsalReviewTests(unittest.TestCase):
    def test_two_actor_rehearsal_alternates_and_caps(self) -> None:
        personas = (
            Persona("a", "甲", role="辩手", values="不愿被安排", voice=VoiceProfile(entry_point="先抓对方上一句")),
            Persona("b", "乙", role="辩手", values="先把手续做完", voice=VoiceProfile(entry_point="先纠正具体事实")),
        )
        cognition_a = CapturingCognition()
        cognition_b = CapturingCognition()
        scene = SceneSetup("s", "门口", "雨里有人等着", ("door",), max_turns=2)
        host = InMemorySceneHost("s", facts={"O.current": "门仍关闭"}, targets=("guard",))
        run = create_rehearsal(
            scene,
            (ActorSetup(personas[0], "进入"), ActorSetup(personas[1], "完成登记")),
            host=host,
            cognition={"a": cognition_a, "b": cognition_b},
            performance=FakePerformance(),
        )
        first = run.advance()
        second = run.advance()
        self.assertEqual(first.draft.actor_id, "a")
        self.assertEqual(second.draft.actor_id, "b")
        self.assertTrue(run.complete)
        self.assertEqual(cognition_a.frames[0].identity_evidence["voice"]["entry_point"], "先抓对方上一句")
        heard = cognition_b.frames[0].recent_history[0]
        self.assertEqual(heard["speech"], first.draft.speech)
        self.assertEqual(heard["action"], first.draft.action)
        self.assertEqual(heard["actor_id"], "a")
        with self.assertRaisesRegex(RuntimeError, "complete"):
            run.advance()

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
