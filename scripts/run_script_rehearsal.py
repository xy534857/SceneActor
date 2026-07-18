#!/usr/bin/env python3
"""Run a script-driven multi-scene rehearsal and write it only after dialogue review passes.

Adapts an external screenplay package (personas + scene cards) onto the
SceneActor runtime: fixed speaker order per scene, beat facts injected
between turns, per-scene blind review with a dialogue gate.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import FallbackModel, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.review import BlindReviewer, JsonBlindReviewPort

parser = argparse.ArgumentParser()
parser.add_argument("--personas", default="examples/alien_visit/personas.json")
parser.add_argument("--scenes", default="examples/alien_visit/scenes.json")
parser.add_argument("--output", required=True)
parser.add_argument("--attempts", type=int, default=3, help="parallel attempts per scene; best passing one wins")
parser.add_argument("--min-dialogue-score", type=int, default=4)
parser.add_argument("--model", default="owtr-anthropic/claude-fable-5")
parser.add_argument("--fallback-model", default="owtr/gpt-5.6-sol")
parser.add_argument("--review-model", default="", help="review model; defaults to --model")
parser.add_argument("--review-fallback-model", default="", help="review fallback; defaults to --fallback-model")
parser.add_argument("--thinking", default="low", help="omp thinking level for all calls")
parser.add_argument("--review-lenses", default="dialogue", help="comma-separated blind review lenses")
args = parser.parse_args()
if args.attempts < 1 or not 3 <= args.min_dialogue_score <= 5:
    parser.error("attempts must be positive and min dialogue score between three and five")

persona_pack = json.loads(Path(args.personas).read_text(encoding="utf-8"))
scene_pack = json.loads(Path(args.scenes).read_text(encoding="utf-8"))

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

def build_model() -> FallbackModel:
    return FallbackModel(OmpCliCompletion(thinking=args.thinking), primary=args.model, fallback=args.fallback_model)


def build_review_model() -> FallbackModel:
    return FallbackModel(
        OmpCliCompletion(thinking=args.thinking),
        primary=args.review_model or args.model,
        fallback=args.review_fallback_model or args.fallback_model,
    )
review_lenses = tuple(item.strip() for item in args.review_lenses.split(",") if item.strip())

shared_setting = scene_pack["shared_setting"]
world_facts = dict(scene_pack.get("world_facts", {}))


def rehearse_scene(scene_cfg: dict, prev_summary: str, attempt_tag: int) -> tuple[list[dict], dict] | None:
    """One attempt at one scene; returns (transcript, dialogue_review) or None."""
    generation_model = build_model()
    review_model = build_review_model()
    turn_order = list(scene_cfg["turn_order"])
    cast_ids = list(dict.fromkeys(turn_order))
    host = InMemorySceneHost(
        scene_cfg["scene_id"],
        facts={
            "O.current": (prev_summary + " " if prev_summary else "") + scene_cfg["opening_fact"],
            **world_facts,
        },
        targets=tuple(cast_ids),
        capabilities=("speak", "wait", "interact"),
    )
    scene = SceneSetup(
        scene_cfg["scene_id"],
        shared_setting,
        scene_cfg["opening_fact"],
        ("courtyard", "front-door", "main-room", "phone", "broom", "chickens"),
        max_turns=len(turn_order),
    )
    actors = tuple(
        ActorSetup(
            personas[actor_id],
            ACTOR_GOALS[actor_id],
            ACTOR_RELATIONSHIPS[actor_id],
            {
                "condition_tonight": ACTOR_CONDITIONS[actor_id],
                "scene_emotion": scene_cfg["core_emotion"],
                **({"closing_hint": scene_cfg["closing_hint"]} if scene_cfg.get("closing_hint") and actor_id == "laoren-jia" else {}),
                **({"scene_hint": scene_cfg["actor_hints"][actor_id]} if actor_id in scene_cfg.get("actor_hints", {}) else {}),
            },
            "guarded" if actor_id == "laoren-jia" else "open",
        )
        for actor_id in cast_ids
    )
    run = create_rehearsal(
        scene,
        actors,
        host=host,
        cognition={actor_id: JsonCognitionPort(generation_model) for actor_id in cast_ids},
        performance=JsonPerformancePort(generation_model),
    )
    beats = {beat["after_turn"]: beat["fact"] for beat in scene_cfg.get("beats", [])}
    transcript: list[dict] = []
    for index, actor_id in enumerate(turn_order, start=1):
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            print(json.dumps({"scene": scene_cfg["scene_id"], "attempt": attempt_tag, "protocol_failure": str(exc)[:300]}, ensure_ascii=False), flush=True)
            return None
        if result.draft is None:
            return None
        entry = asdict(result.draft)
        transcript.append(entry)
        if index in beats:
            host.facts["O.current"] = beats[index]
            transcript.append({"actor_id": "scene", "action": beats[index], "speech": ""})
        else:
            host.facts["O.current"] = (
                f"上一位（{result.draft.actor_id}）刚才：{(result.draft.speech or result.draft.action)[:120]}"
            )
        print(json.dumps({"scene": scene_cfg["scene_id"], "attempt": attempt_tag, "turn_done": index}, ensure_ascii=False), flush=True)
    public_scene = {
        "setting": shared_setting,
        "opening": scene_cfg["opening_fact"],
        "language": "zh-CN",
        "core_emotion": scene_cfg["core_emotion"],
        "characters": [
            {"anonymous_actor": actor_id, "role": personas[actor_id].role, "voice": personas[actor_id].voice.to_dict()}
            for actor_id in cast_ids
        ],
    }
    review = BlindReviewer(JsonBlindReviewPort(review_model), lenses=review_lenses).review(public_scene, transcript)
    dialogue = next((item for item in review.get("reviews", []) if item.get("lens") == "dialogue"), None)
    print(json.dumps({"scene": scene_cfg["scene_id"], "attempt": attempt_tag, "dialogue_review": dialogue, "overall_score": review.get("score")}, ensure_ascii=False), flush=True)
    if not dialogue or not dialogue.get("passed") or dialogue.get("score", 0) < args.min_dialogue_score:
        return None
    return transcript, {"blind_review": review, "dialogue": dialogue}


performed_scenes = []
prev_summary = ""
for scene_cfg in scene_pack["scenes"]:
    print(json.dumps({"scene": scene_cfg["scene_id"], "parallel_attempts": args.attempts}, ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=args.attempts) as pool:
        results = list(pool.map(
            lambda tag: rehearse_scene(scene_cfg, prev_summary, tag),
            range(1, args.attempts + 1),
        ))
    passing = [item for item in results if item]
    if not passing:
        raise RuntimeError(f"scene {scene_cfg['scene_id']} failed dialogue review in all {args.attempts} parallel attempts")
    accepted = max(passing, key=lambda item: item[1]["dialogue"].get("score", 0))
    transcript, review_info = accepted
    spoken = [t for t in transcript if t.get("speech")]
    last_line = spoken[-1]["speech"] if spoken else ""
    prev_summary = f"（上一场《{scene_cfg['title']}》结束时：{last_line[:80] or '无人说话，只有动作。'}）"
    performed_scenes.append({
        "scene_id": scene_cfg["scene_id"],
        "title": scene_cfg["title"],
        "core_emotion": scene_cfg["core_emotion"],
        "turns": transcript,
        "review": review_info,
    })

output = {
    "schema_version": "sceneactor-script-performance/1.0",
    "source_script": scene_pack.get("source", args.scenes),
    "disclosure": "AI生成的虚构表演，改编自原创剧本设定。",
    "setting": shared_setting,
    "scenes": performed_scenes,
    "models": {"generation": args.model, "fallback": args.fallback_model, "thinking": args.thinking},
}
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": args.output, "scenes": len(performed_scenes)}, ensure_ascii=False), flush=True)
