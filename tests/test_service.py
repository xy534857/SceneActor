from __future__ import annotations

import json
import unittest

from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup
from sceneactor.service import (
    ProductionCompileError,
    ProductionSpec,
    compile_production,
    perform,
)
from tests.test_runtime import FakeCognition, FakePerformance


def _spec(max_turns: int = 2) -> ProductionSpec:
    return ProductionSpec(
        scene=SceneSetup("s", "门口", "雨里有人等着", ("door",), max_turns=max_turns),
        actors=(
            ActorSetup(Persona("a", "甲"), "进入"),
            ActorSetup(Persona("guard", "乙"), "登记"),
        ),
        host_facts={"O.weather": "下着雨"},
    )


class ProductionSpecTests(unittest.TestCase):
    def test_round_trips_through_dict(self) -> None:
        spec = _spec()
        restored = ProductionSpec.from_dict(spec.to_dict())
        self.assertEqual(restored.scene.scene_id, "s")
        self.assertEqual([a.persona.id for a in restored.actors], ["a", "guard"])

    def test_rejects_duplicate_ids_bad_facts_and_unknown_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            ProductionSpec(
                scene=_spec().scene,
                actors=(ActorSetup(Persona("a", "甲"), "x"), ActorSetup(Persona("a", "乙"), "y")),
            )
        with self.assertRaisesRegex(ValueError, "O\\."):
            ProductionSpec(scene=_spec().scene, actors=_spec().actors, host_facts={"weather": "雨"})
        with self.assertRaisesRegex(ValueError, "unknown actors"):
            ProductionSpec(scene=_spec().scene, actors=_spec().actors, speaking_order=("ghost",))


class CompileTests(unittest.TestCase):
    def test_model_output_is_repaired_with_validator_feedback(self) -> None:
        calls: list[str] = []
        good = json.dumps(_spec().to_dict(), ensure_ascii=False)

        def complete(messages, purpose):
            calls.append(purpose)
            payload = json.loads(messages[-1]["content"])
            if len(calls) == 1:
                self.assertEqual(payload["validation_feedback"], "")
                return "{\"scene\": {\"scene_id\": \"s\"}, \"actors\": []}"
            self.assertTrue(payload["validation_feedback"])
            return good

        spec = compile_production("剧本：门口登记。", complete)
        self.assertEqual(len(calls), 2)
        self.assertEqual(spec.scene.opening, "雨里有人等着")

    def test_exhausted_repairs_raise(self) -> None:
        with self.assertRaises(ProductionCompileError):
            compile_production("剧本", lambda messages, purpose: "not json", max_attempts=2)


class PerformTests(unittest.TestCase):
    def test_full_document_with_progress(self) -> None:
        seen: list[tuple[int, str]] = []
        document = perform(
            _spec(max_turns=2),
            generation=lambda messages, purpose: "",
            cognition_factory=lambda model: FakeCognition(),
            performance_factory=lambda model: FakePerformance(),
            on_turn=lambda turn, actor: seen.append((turn, actor)),
        )
        self.assertTrue(document["completed"])
        self.assertEqual(len(document["turns"]), 2)
        self.assertEqual(document["scene"]["characters"][0]["anonymous_actor"], "a")
        self.assertEqual(seen, [(1, "a"), (2, "guard")])


if __name__ == "__main__":
    unittest.main()
