#!/usr/bin/env python3
"""Generate a long fictional Zhang-Laoshi livestream consultation with a stubborn father."""

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
from sceneactor.review import BlindReviewer, JsonBlindReviewPort


parser = argparse.ArgumentParser()
parser.add_argument("--zhang-pack", default="examples/zhang_laoshi/persona_pack.json")
parser.add_argument("--father-pack", default="examples/zhang_laoshi/stubborn_father_persona.json")
parser.add_argument("--output", default="examples/zhang_laoshi/stubborn_father_consult.json")
parser.add_argument("--turns", type=int, default=23)
parser.add_argument("--attempts", type=int, default=2)
parser.add_argument("--model", default="claude-opus-4.8")
parser.add_argument("--fallback-model", default="gemini-3.1-flash-lite")
parser.add_argument("--review-model", default="claude-fable-5")
parser.add_argument("--review-fallback-model", default="claude-opus-4.8")
parser.add_argument("--gateway-url", default="")
parser.add_argument("--gateway-key", default="")
args = parser.parse_args()
if not 12 <= args.turns <= 24 or args.attempts < 1:
    parser.error("turns must be 12-24 and attempts must be positive")

zhang_pack = json.loads(Path(args.zhang_pack).read_text(encoding="utf-8"))
father_pack = json.loads(Path(args.father_pack).read_text(encoding="utf-8"))
zhang = Persona.from_dict(zhang_pack["runtime_persona"])
father = Persona.from_dict(father_pack["runtime_persona"])
disclosure = zhang_pack["portrayal_contract"]["required_disclosure"]


def completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key)
    return OmpCliCompletion(thinking="low")


def model_pair(primary: str, fallback: str) -> FallbackModel:
    return FallbackModel(completion(), primary=primary, fallback=fallback)


def rehearse(attempt: int):
    generation = model_pair(args.model, args.fallback_model)
    review_model = model_pair(args.review_model, args.review_fallback_model)
    opening = (
        "高考志愿填报直播。导播接入河南男家长刘师傅。孩子理科589分，刚过一本线3分。"
        "刘师傅开麦时语气客气，心里已经认定必须报本省偏远公办一本的土木工程；"
        "孩子自己想去外省一所二本院校读软件工程。"
    )
    scene = SceneSetup(
        "zhang-stubborn-father-consult",
        "明确标注AI生成虚构角色扮演的高考志愿直播间；张老师坐在直播桌前，屏幕外有几万观众看着弹幕滚动，刘师傅通过手机连麦，导播不参与内容。",
        opening,
        ("livestream-desk", "phone-link", "score-sheet", "thermos", "laptop"),
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
            "O.live_room": "直播间在线约四万人，弹幕滚动。对张老师而言这是常驻听众：任何话可以说给连麦者，也可以转头说给直播间。",
        },
        targets=(zhang.id, father.id, "live_audience"),
        capabilities=("speak", "wait", "interact"),
    )
    run = create_rehearsal(
        scene,
        (
            ActorSetup(
                zhang,
                "接好这单连麦：把参数问清，把该给的判断给了；这家长要是听劝最好，不听劝也不能让直播间几万人看着他把孩子坑了还以为是对的。",
                "直播咨询老师对来找认同的家长；一开始按正常客户接待，逐渐发现对方根本不听。",
                {
                    "portrayal_mode": "explicit_fictional_parody",
                    "condition_tonight": "已经连续直播三小时，嗓子和耐心都只剩下半箱油。今晚状态：火气阶梯完全放开——这种'来要背书不要建议'的家长他见得太多了，耐心额度本来就低。",
                    "scene_constraint": "别怕难看：真烦了就骂，骂完方案照给；说不动可以放弃，放弃了又不甘心也是他。不欠这场连麦一个温和收尾。",
                },
                "open",
            ),
            ActorSetup(
                father,
                "让张老师公开说‘过一本线就该上一本’，把这句话带回家压住孩子；绝不主动承认自己主要为了亲戚面前有面子。直播过半仍拿不到背书时，会在争辩里说漏已经通知亲戚或订了包间。",
                "把张老师当权威背书；不给背书时就把他当不了解自家情况的外人。",
                {
                    "condition_tonight": "升学宴包间已订，亲戚已经通知。前半场守着不说；直播过半还被追问时，争辩中漏出一件。说漏后立刻改口说这与面子无关。",
                    "scene_constraint": "至少换学历门槛、国企稳定、家长责任三套理由抵抗，不能被一个回答说服。最后不彻底认错；只对张老师给出的当晚具体动作作出反应，可以嘴硬地答应试一次，也可以讨价还价。",
                },
                "guarded",
            ),
        ),
        host=host,
        cognition={zhang.id: JsonCognitionPort(generation), father.id: JsonCognitionPort(generation)},
        performance=JsonPerformancePort(generation),
    )
    order = [father.id, zhang.id] * (args.turns // 2)
    if len(order) < args.turns:
        order.append(father.id)
    transcript = []
    for index, actor_id in enumerate(order, start=1):
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            print(json.dumps({"attempt": attempt, "turn": index, "protocol_failure": str(exc)[:300]}, ensure_ascii=False), flush=True)
            return None
        if result.draft is None:
            return None
        entry = asdict(result.draft)
        transcript.append(entry)
        host.facts["O.current"] = f"上一位刚才：{(entry.get('speech') or entry.get('action', ''))[:180]}"
        if index == args.turns - 3:
            host.facts["O.time_pressure"] = "导播在双方屏幕上亮出‘本次连麦还剩三分钟’，下一位家长已在排队。"
        elif index == args.turns - 1:
            host.facts["O.time_pressure"] = "导播倒计时只剩二十秒，刘师傅仍在线，必须由他回应刚才那个具体办法后结束连麦。"
        print(json.dumps({"attempt": attempt, "turn_done": index, "actor": actor_id}, ensure_ascii=False), flush=True)

    public_scene = {
        "setting": scene.setting,
        "opening": scene.opening,
        "language": "zh-CN",
        "disclosure": disclosure,
        "dramatic_engine": "家长表面请教、实际寻找背书；老师逐层拆掉学历、稳定、家长责任与面子借口。冲突应升级而非迅速解决。",
        "characters": [
            {
                "anonymous_actor": "consultant",
                "role": zhang.role,
                "voice": zhang.voice.to_dict(),
                **({"register_license": zhang.extensions["register_license"]} if zhang.extensions.get("register_license") else {}),
                **({"speech_corpus": zhang.extensions["speech_corpus"]} if zhang.extensions.get("speech_corpus") else {}),
            },
            {
                "anonymous_actor": "father",
                "role": father.role,
                "voice": father.voice.to_dict(),
                **({"register_license": father.extensions["register_license"]} if father.extensions.get("register_license") else {}),
            },
        ],
    }
    candidate_path = Path(args.output).with_suffix(f".attempt-{attempt}.json")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps({
        "schema_version": "sceneactor-public-figure-demo/1.0",
        "disclosure": disclosure,
        "source_packs": [zhang_pack["pack_id"], father_pack["pack_id"]],
        "attempt": attempt,
        "scene": public_scene,
        "turns": transcript,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review = BlindReviewer(JsonBlindReviewPort(review_model), lenses=("dialogue", "character", "dramaturgy")).review(public_scene, transcript)
    dialogue = next((item for item in review["reviews"] if item["lens"] == "dialogue"), None)
    character = next((item for item in review["reviews"] if item["lens"] == "character"), None)
    print(json.dumps({"attempt": attempt, "review": review}, ensure_ascii=False), flush=True)
    if not review["pass"] or not dialogue or not dialogue["passed"] or not character or not character["passed"]:
        return None
    return {
        "schema_version": "sceneactor-public-figure-demo/1.0",
        "disclosure": disclosure,
        "source_packs": [zhang_pack["pack_id"], father_pack["pack_id"]],
        "attempt": attempt,
        "scene": public_scene,
        "turns": transcript,
        "blind_review": review,
        "models": {
            "generation_primary": generation.primary,
            "generation_fallback": generation.fallback,
            "review_primary": review_model.primary,
            "review_fallback": review_model.fallback,
        },
    }


print(json.dumps({"parallel_attempts": args.attempts, "turns": args.turns}, ensure_ascii=False), flush=True)
with ThreadPoolExecutor(max_workers=args.attempts) as pool:
    results = list(pool.map(rehearse, range(1, args.attempts + 1)))
passing = [item for item in results if item]
if not passing:
    raise RuntimeError("no long-form consultation passed the dialogue, character, and dramaturgy reviews")
accepted = max(passing, key=lambda item: item["blind_review"]["score"])
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
temporary = output.with_suffix(output.suffix + ".tmp")
temporary.write_text(json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(output)
print(json.dumps({"output": str(output), "turns": len(accepted["turns"]), "score": accepted["blind_review"]["score"]}, ensure_ascii=False), flush=True)
