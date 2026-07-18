#!/usr/bin/env python3
"""Benchmark multiple models on the same rehearsal scene with a fixed judge.

For each candidate model: run N attempts of the benchmark scene with the
candidate driving cognition+performance, judge every transcript with the
SAME fixed reviewer model, and record protocol failures, retries, wall
time, and per-turn stats. Writes one JSON report.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import GatewayCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.review import BlindReviewer, JsonBlindReviewPort

parser = argparse.ArgumentParser()
parser.add_argument("--models", required=True, help="comma-separated candidate model ids")
parser.add_argument("--judge-model", default="claude-fable-5")
parser.add_argument("--personas", default="examples/alien_visit/personas.json")
parser.add_argument("--scenes", default="examples/alien_visit/scenes.json")
parser.add_argument("--scene-id", default="alien-visit-persuade")
parser.add_argument("--attempts", type=int, default=2, help="attempts per model")
parser.add_argument("--gateway-url", default="http://161.118.219.11:8081/v1")
parser.add_argument("--gateway-key", default="owtr_mg9HFay14JcQQ3G3Rnpr7-xHfR-gt6QL765ApMSz6Ew")
parser.add_argument("--output", required=True)
args = parser.parse_args()

candidate_models = [item.strip() for item in args.models.split(",") if item.strip()]
persona_pack = json.loads(Path(args.personas).read_text(encoding="utf-8"))
scene_pack = json.loads(Path(args.scenes).read_text(encoding="utf-8"))
scene_cfg = next(s for s in scene_pack["scenes"] if s["scene_id"] == args.scene_id)

personas = {
    entry["id"]: Persona.from_dict(entry)
    for entry in (persona_pack["personas"][key] for key in persona_pack["personas"])
}

ACTOR_GOALS = {
    "laoren-jia": "把今天过成和昨天一样的一天：开门、扫院、倒水、等电话。来的人不是孩子，就不相干。",
    "waixingren-jia": "按流程核实孤独标准、宣读福利、给对象状态归类，按时结案。",
    "waixingren-yi": "逐条核实并精确记录，完成存档。",
}
ACTOR_RELATIONSHIPS = {
    "laoren-jia": "对两个来访者：上门办事的，不认识，礼数照走，心不动。",
    "waixingren-jia": "对老人是执行者对对象；与乙是默契搭档，甲说乙记。",
    "waixingren-yi": "对老人是仪器对样本；与甲是零件对零件。",
}
ACTOR_CONDITIONS = {
    "laoren-jia": "今天和每一天一样。手上永远有活。别人说话的时候，活不停。",
    "waixingren-jia": "本单流程顺利，对象配合度异常但不影响推进。",
    "waixingren-yi": "现场数据充足，采集顺利。",
}

shared_setting = scene_pack["shared_setting"]
world_facts = dict(scene_pack.get("world_facts", {}))


class InstrumentedModel:
    """Single-model completion that counts calls and total latency."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.gateway = GatewayCompletion(args.gateway_url, args.gateway_key)
        self.calls = 0
        self.seconds = 0.0
        self.attempts: list = []

    def __call__(self, messages: list[dict[str, str]], purpose: str) -> str:
        started = time.time()
        try:
            return self.gateway(messages, purpose, self.model_id)
        finally:
            self.calls += 1
            self.seconds += time.time() - started


def run_attempt(model_id: str, attempt_tag: int) -> dict:
    model = InstrumentedModel(model_id)
    judge = InstrumentedModel(args.judge_model)
    turn_order = list(scene_cfg["turn_order"])
    cast_ids = list(dict.fromkeys(turn_order))
    host = InMemorySceneHost(
        scene_cfg["scene_id"],
        facts={"O.current": scene_cfg["opening_fact"], **world_facts},
        targets=tuple(cast_ids),
        capabilities=("speak", "wait", "interact"),
    )
    scene = SceneSetup(
        scene_cfg["scene_id"], shared_setting, scene_cfg["opening_fact"],
        ("courtyard", "front-door", "main-room", "phone", "broom", "chickens"),
        max_turns=len(turn_order),
    )
    actors = tuple(
        ActorSetup(
            personas[actor_id], ACTOR_GOALS[actor_id], ACTOR_RELATIONSHIPS[actor_id],
            {
                "condition_tonight": ACTOR_CONDITIONS[actor_id],
                "scene_emotion": scene_cfg["core_emotion"],
                **({"scene_hint": scene_cfg["actor_hints"][actor_id]} if actor_id in scene_cfg.get("actor_hints", {}) else {}),
            },
            "guarded" if actor_id == "laoren-jia" else "open",
        )
        for actor_id in cast_ids
    )
    run = create_rehearsal(
        scene, actors, host=host,
        cognition={actor_id: JsonCognitionPort(model) for actor_id in cast_ids},
        performance=JsonPerformancePort(model),
    )
    beats = {beat["after_turn"]: beat["fact"] for beat in scene_cfg.get("beats", [])}
    transcript: list[dict] = []
    result_row: dict = {
        "model": model_id, "attempt": attempt_tag,
        "turns_completed": 0, "protocol_failure": "", "wall_seconds": 0.0,
        "generation_calls": 0, "generation_seconds": 0.0,
        "dialogue_score": 0, "dialogue_passed": False, "verdict": "", "problems": [],
        "speech_lengths": [],
    }
    started = time.time()
    for index, actor_id in enumerate(turn_order, start=1):
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            result_row["protocol_failure"] = str(exc)[:300]
            break
        except RuntimeError as exc:
            result_row["protocol_failure"] = f"runtime: {str(exc)[:300]}"
            break
        if result.draft is None:
            result_row["protocol_failure"] = "no draft produced"
            break
        entry = asdict(result.draft)
        transcript.append(entry)
        if entry.get("speech"):
            result_row["speech_lengths"].append(len(entry["speech"]))
        result_row["turns_completed"] = index
        if index in beats:
            host.facts["O.current"] = beats[index]
            transcript.append({"actor_id": "scene", "action": beats[index], "speech": ""})
        else:
            host.facts["O.current"] = (
                f"上一位（{result.draft.actor_id}）刚才：{(result.draft.speech or result.draft.action)[:120]}"
            )
        print(json.dumps({"model": model_id, "attempt": attempt_tag, "turn_done": index}, ensure_ascii=False), flush=True)
    result_row["wall_seconds"] = round(time.time() - started, 1)
    result_row["generation_calls"] = model.calls
    result_row["generation_seconds"] = round(model.seconds, 1)
    if result_row["turns_completed"] == len(turn_order):
        public_scene = {
            "setting": shared_setting, "opening": scene_cfg["opening_fact"],
            "language": "zh-CN", "core_emotion": scene_cfg["core_emotion"],
            "characters": [
                {"anonymous_actor": actor_id, "role": personas[actor_id].role, "voice": personas[actor_id].voice.to_dict()}
                for actor_id in cast_ids
            ],
        }
        review = BlindReviewer(JsonBlindReviewPort(lambda m, p: judge(m, p)), lenses=("dialogue",)).review(public_scene, transcript)
        dialogue = next((item for item in review.get("reviews", []) if item.get("lens") == "dialogue"), None)
        if dialogue:
            result_row["dialogue_score"] = dialogue.get("score", 0)
            result_row["dialogue_passed"] = bool(dialogue.get("passed"))
            result_row["verdict"] = dialogue.get("verdict", "")
            result_row["problems"] = list(dialogue.get("problems", []))
    result_row["transcript"] = transcript
    print(json.dumps({
        "model": model_id, "attempt": attempt_tag,
        "score": result_row["dialogue_score"], "turns": result_row["turns_completed"],
        "wall_s": result_row["wall_seconds"], "failure": result_row["protocol_failure"][:80],
    }, ensure_ascii=False), flush=True)
    return result_row


jobs = [(model_id, tag) for model_id in candidate_models for tag in range(1, args.attempts + 1)]
with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
    rows = list(pool.map(lambda job: run_attempt(*job), jobs))

report = {
    "schema_version": "sceneactor-model-benchmark/1.0",
    "scene_id": args.scene_id,
    "judge_model": args.judge_model,
    "attempts_per_model": args.attempts,
    "results": rows,
}
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": args.output, "rows": len(rows)}, ensure_ascii=False), flush=True)
