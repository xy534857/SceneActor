#!/usr/bin/env python3
"""Meme-persona pairing rehearsal driver.

Same engine as run_script_rehearsal.py, but actor goals/relationships come
from each scene's ``actor_goals``/``actor_relationships`` (pairing scenes are
data, not code). Scenes run in parallel; each scene gets N attempts and the
best dialogue-passing attempt wins.
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
from sceneactor.model import FallbackModel, GatewayCompletion, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.review import BlindReviewer, JsonBlindReviewPort

parser = argparse.ArgumentParser()
parser.add_argument("--personas", required=True)
parser.add_argument("--scenes", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--attempts", type=int, default=2)
parser.add_argument("--min-dialogue-score", type=int, default=4)
parser.add_argument("--scene-parallel", type=int, default=5)
parser.add_argument("--model", default="owtr-anthropic/claude-fable-5")
parser.add_argument("--fallback-model", default="owtr/gpt-5.6-sol")
parser.add_argument("--thinking", default="low")
parser.add_argument("--gateway-url", default="")
parser.add_argument("--gateway-key", default="")
parser.add_argument("--only", default="", help="comma-separated scene ids")
args = parser.parse_args()

persona_pack = json.loads(Path(args.personas).read_text(encoding="utf-8"))
scene_pack = json.loads(Path(args.scenes).read_text(encoding="utf-8"))
personas = {
    entry["id"]: Persona.from_dict(entry)
    for entry in persona_pack["personas"].values()
}
shared_setting = scene_pack["shared_setting"]
world_facts = dict(scene_pack.get("world_facts", {}))


def _completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key)
    return OmpCliCompletion(thinking=args.thinking)


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def rehearse(scene_cfg: dict, attempt_tag: int) -> tuple[list[dict], dict] | None:
    model = FallbackModel(_completion(), primary=args.model, fallback=args.fallback_model)
    turn_order = list(scene_cfg["turn_order"])
    cast_ids = list(dict.fromkeys(turn_order))
    goals = scene_cfg["actor_goals"]
    rels = scene_cfg["actor_relationships"]
    host = InMemorySceneHost(
        scene_cfg["scene_id"],
        facts={"O.current": scene_cfg["opening_fact"], **world_facts},
        targets=tuple(cast_ids),
        capabilities=("speak", "wait", "interact"),
    )
    scene = SceneSetup(
        scene_cfg["scene_id"],
        shared_setting,
        scene_cfg["opening_fact"],
        ("street", "counter", "table", "door", "props"),
        max_turns=len(turn_order),
    )
    actors = tuple(
        ActorSetup(
            personas[actor_id],
            goals[actor_id],
            rels[actor_id],
            {"scene_emotion": scene_cfg["core_emotion"]},
            "open",
        )
        for actor_id in cast_ids
    )
    run = create_rehearsal(
        scene,
        actors,
        host=host,
        cognition={actor_id: JsonCognitionPort(model) for actor_id in cast_ids},
        performance=JsonPerformancePort(model),
    )
    transcript: list[dict] = []
    events = {int(e["before_turn"]): e for e in scene_cfg.get("scene_events", [])}
    for index, actor_id in enumerate(turn_order, start=1):
        if index in events:
            host.facts.update(events[index].get("facts", {}))
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            log(scene=scene_cfg["scene_id"], attempt=attempt_tag, protocol_failure=str(exc)[:200])
            return None
        if result.draft is None:
            return None
        transcript.append(asdict(result.draft))
        last_line = f"上一位（{result.draft.actor_id}）刚才：{(result.draft.speech or result.draft.action)[:120]}"
        if index + 1 in events:
            # keep the injected observation dominant over the recap
            host.facts["O.recap"] = last_line
        else:
            host.facts["O.current"] = last_line
    public_scene = {
        "setting": shared_setting,
        "opening": scene_cfg["opening_fact"],
        "language": "zh-CN",
        "core_emotion": scene_cfg["core_emotion"],
        "characters": [
            {
                "anonymous_actor": aid,
                "role": personas[aid].role,
                "voice": personas[aid].voice.to_dict(),
                "register_license": personas[aid].extensions.get("register_license", []),
                **(
                    {"speech_corpus": personas[aid].extensions["speech_corpus"]}
                    if "speech_corpus" in personas[aid].extensions
                    else {}
                ),
            }
            for aid in cast_ids
        ],
        "character_cards": {
            aid: {"role": personas[aid].role, "cognition_lens": personas[aid].cognition_lens, "goal": goals[aid]}
            for aid in cast_ids
        },
    }
    review_model = FallbackModel(_completion(), primary=args.model, fallback=args.fallback_model)
    review = BlindReviewer(JsonBlindReviewPort(review_model), lenses=("dialogue",)).review(public_scene, transcript)
    dialogue = next((r for r in review.get("reviews", []) if r.get("lens") == "dialogue"), None)
    log(scene=scene_cfg["scene_id"], attempt=attempt_tag,
        dialogue_score=(dialogue or {}).get("score"), passed=(dialogue or {}).get("passed"))
    outcome = {"transcript": transcript, "review": {"blind_review": review, "dialogue": dialogue}}
    if not dialogue or not dialogue.get("passed") or dialogue.get("score", 0) < args.min_dialogue_score:
        return {"passed": False, **outcome}
    return {"passed": True, **outcome}


def run_scene(scene_cfg: dict) -> dict:
    log(scene=scene_cfg["scene_id"], starting=True)
    best = None
    with ThreadPoolExecutor(max_workers=args.attempts) as pool:
        outcomes = list(pool.map(lambda tag: rehearse(scene_cfg, tag), range(1, args.attempts + 1)))
    rejected = []
    for outcome in outcomes:
        if outcome is None:
            continue
        score = outcome["review"]["dialogue"].get("score", 0) if outcome["review"]["dialogue"] else 0
        if outcome["passed"]:
            if best is None or score > best[2]:
                best = (outcome["transcript"], outcome["review"], score)
        else:
            rejected.append(outcome)
    if best is None:
        evidence = max(rejected, key=lambda o: (o["review"]["dialogue"] or {}).get("score", 0), default=None)
        return {
            "scene_id": scene_cfg["scene_id"],
            "status": "failed",
            "core_emotion": scene_cfg["core_emotion"],
            **({"best_rejected": evidence} if evidence else {}),
        }
    return {
        "scene_id": scene_cfg["scene_id"],
        "status": "passed",
        "core_emotion": scene_cfg["core_emotion"],
        "dialogue_score": best[2],
        "transcript": best[0],
        "review": best[1],
    }


selected = [
    cfg for cfg in scene_pack["scenes"]
    if not args.only or cfg["scene_id"] in args.only.split(",")
]
with ThreadPoolExecutor(max_workers=args.scene_parallel) as pool:
    performed = list(pool.map(run_scene, selected))

output = {
    "source_personas": args.personas,
    "source_scenes": args.scenes,
    "model": args.model,
    "scenes": performed,
}
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
log(output=args.output,
    passed=sum(1 for s in performed if s["status"] == "passed"),
    failed=sum(1 for s in performed if s["status"] == "failed"))
