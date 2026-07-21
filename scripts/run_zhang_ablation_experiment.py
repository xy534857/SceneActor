#!/usr/bin/env python3
"""A/B ablation: full Zhang persona (footage corpus, register license, anger
ladder, dual-audience) vs a simple persona with father-level detail only.

Both variants play the same short livestream consult against the same father,
same scene facts, same models. Outputs two transcripts for side-by-side
comparison; no review gate (this is a raw comparison experiment).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import FallbackModel, GatewayCompletion, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal

parser = argparse.ArgumentParser()
parser.add_argument("--full-pack", default="examples/zhang_laoshi/persona_pack.json")
parser.add_argument("--ablated-pack", default="examples/zhang_laoshi/zhang_ablated_persona.json")
parser.add_argument("--father-pack", default="examples/zhang_laoshi/stubborn_father_persona.json")
parser.add_argument("--output-dir", default="examples/zhang_laoshi/ablation")
parser.add_argument("--turns", type=int, default=8)
parser.add_argument("--model", default="claude-opus-4.8")
parser.add_argument("--fallback-model", default="gemini-3.1-flash-lite")
parser.add_argument("--gateway-url", default="")
parser.add_argument("--gateway-key", default="")
args = parser.parse_args()

father_pack = json.loads(Path(args.father_pack).read_text(encoding="utf-8"))


def completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key)
    return OmpCliCompletion(thinking="low")


def run_variant(variant: str) -> dict:
    pack_path = args.full_pack if variant == "full" else args.ablated_pack
    zhang_pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    zhang = Persona.from_dict(zhang_pack["runtime_persona"])
    father = Persona.from_dict(father_pack["runtime_persona"])
    disclosure = zhang_pack["portrayal_contract"]["required_disclosure"]
    generation = FallbackModel(completion(), primary=args.model, fallback=args.fallback_model)

    opening = (
        "高考志愿填报直播。导播接入河南男家长刘师傅。孩子理科589分，刚过一本线3分。"
        "刘师傅认定必须报本省偏远公办一本的土木工程；孩子自己想去外省二本读软件工程。"
    )
    scene = SceneSetup(
        f"zhang-ablation-{variant}",
        "明确标注AI生成虚构角色扮演的高考志愿直播间；张老师坐在直播桌前，刘师傅手机连麦。",
        opening,
        ("livestream-desk", "phone-link", "score-sheet", "laptop"),
        max_turns=args.turns,
    )
    host = InMemorySceneHost(
        scene.scene_id,
        facts={
            "O.current": opening,
            "O.consult_facts": (
                "刘师傅自己开口报的：河南理科589分；一本线586；他想报本省偏远公办一本土木工程。"
                "连麦背景音里他儿子插过半句想去外省学软件，被他摁下去了。"
            ),
        },
        targets=(zhang.id, father.id, "live_audience"),
        capabilities=("speak", "wait", "interact"),
    )
    run = create_rehearsal(
        scene,
        (
            ActorSetup(
                zhang,
                "接好这单连麦：把参数问清，把该给的判断给了；不能让家长把孩子坑了还以为是对的。",
                "直播咨询老师对来找认同的家长。",
                {"portrayal_mode": "explicit_fictional_parody"},
                "open",
            ),
            ActorSetup(
                father,
                "让张老师公开说'过一本线就该上一本'，拿这句话回家压住儿子；不主动承认为了面子。",
                "把张老师当权威背书；不给背书就当他不了解自家情况。",
                {"scene_constraint": "至少换学历门槛、国企稳定两套理由抵抗，不能被一个回答说服。"},
                "guarded",
            ),
        ),
        host=host,
        cognition={zhang.id: JsonCognitionPort(generation), father.id: JsonCognitionPort(generation)},
        performance=JsonPerformancePort(generation),
    )
    order = [father.id, zhang.id] * (args.turns // 2)
    transcript = []
    for index, actor_id in enumerate(order, start=1):
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            print(json.dumps({"variant": variant, "turn": index, "protocol_failure": str(exc)[:200]}, ensure_ascii=False), flush=True)
            return {}
        if result.draft is None:
            return {}
        entry = asdict(result.draft)
        transcript.append(entry)
        host.facts["O.current"] = f"上一位刚才：{(entry.get('speech') or entry.get('action', ''))[:180]}"
        if index == args.turns - 1:
            host.facts["O.time_pressure"] = "导播提示本次连麦只剩最后一轮。"
        print(json.dumps({"variant": variant, "turn_done": index, "actor": actor_id}, ensure_ascii=False), flush=True)

    result = {
        "schema_version": "sceneactor-ablation-experiment/1.0",
        "disclosure": disclosure,
        "variant": variant,
        "zhang_pack": zhang_pack["pack_id"],
        "scene": {"setting": scene.setting, "opening": opening, "language": "zh-CN"},
        "turns": transcript,
        "models": {"generation_primary": generation.primary, "generation_fallback": generation.fallback},
    }
    out = Path(args.output_dir) / f"consult_{variant}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"variant": variant, "output": str(out), "turns": len(transcript)}, ensure_ascii=False), flush=True)
    return result


with ThreadPoolExecutor(max_workers=2) as pool:
    outcomes = list(pool.map(run_variant, ("full", "ablated")))
if not all(outcomes):
    raise RuntimeError("at least one variant failed to complete")
print(json.dumps({"done": [o["variant"] for o in outcomes]}, ensure_ascii=False), flush=True)
