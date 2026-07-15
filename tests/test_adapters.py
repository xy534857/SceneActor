from __future__ import annotations

import unittest

from sceneactor.adapters.game import GamePresentationAdapter
from sceneactor.adapters.seedance import ProviderCapabilities, ReferenceBinding, SeedanceCompiler
from sceneactor.contracts import Delivery, PerformanceBeat, PerformanceContinuity


class AdapterTests(unittest.TestCase):
    def _beat(self):
        return PerformanceBeat(
            beat_id="beat-1", actor_id="a", action="看向门口的登记牌",
            speech="你先告诉我，为什么不能进去？", addressee="guard",
            attention_target="登记牌", gaze="看向登记牌", blocking="站在门外",
            posture_change="没有越过门槛",
            delivery=Delivery(pace="快一点", volume="低"),
            physical_residue="手指捏着湿证件", observable_outcome=("门仍关闭",),
            response_hook="guard still can answer", source_event_ids=("e1",),
        )

    def test_seedance_compiler_keeps_reference_roles_and_soft_time(self):
        compiler = SeedanceCompiler(ProviderCapabilities("test", "seedance", supported_reference_roles=("identity_anchor", "scene_style")))
        shot = compiler.compile_beat(
            self._beat(), shot_id="S01-001", dramatic_function="establish boundary",
            continuity=PerformanceContinuity(response_hook="guard still can answer"),
            references=(ReferenceBinding("img-a", "a", "identity_anchor"),), soft_time_hint="short opening beat",
        )
        self.assertIn("@img-a (identity_anchor)", shot.to_prompt(compiler.capabilities))
        self.assertIn("soft hint", shot.to_prompt(compiler.capabilities))

    def test_game_presentation_is_observable_only(self):
        command = GamePresentationAdapter().project(self._beat())
        self.assertEqual(command.beat_id, "beat-1")
        self.assertEqual(command.speech, "你先告诉我，为什么不能进去？")
        self.assertEqual(command.gaze_target, "看向登记牌")


if __name__ == "__main__":
    unittest.main()
