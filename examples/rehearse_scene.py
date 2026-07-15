"""Run a deterministic SceneActor rehearsal without a provider key.

Use this to verify the host-independent pipeline. Provider-backed cognition and
performance ports can replace the deterministic ports without changing the Host.
"""

from __future__ import annotations

import json

from sceneactor.contracts import ActionIntent, Appraisal, DecisionContract, EmotionChange, PerformancePolicy
from sceneactor.hosts import InMemorySceneHost
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.runtime import PerformancePort
from tests.test_runtime import FakeCognition, FakePerformance


class ActorCognition(FakeCognition):
    pass


if __name__ == "__main__":
    personas = (Persona("a", "甲", values="不愿被安排"), Persona("b", "乙", values="先把手续做完"))
    scene = SceneSetup("demo", "雨夜门口", "有人站在关闭的门外", ("door",), max_turns=2)
    host = InMemorySceneHost("demo", facts={"O.current": "门仍关闭"}, targets=("guard",))
    run = create_rehearsal(
        scene,
        (ActorSetup(personas[0], "进门"), ActorSetup(personas[1], "完成登记")),
        host=host,
        cognition={"a": ActorCognition(), "b": ActorCognition()},
        performance=FakePerformance(),
    )
    while not run.complete:
        run.advance()
    print(json.dumps({"turns": len(run.turns), "reason": run.complete_reason}, ensure_ascii=False))
