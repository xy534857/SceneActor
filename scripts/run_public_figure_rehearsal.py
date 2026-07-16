#!/usr/bin/env python3
"""Generate a disclosed public-figure parody and write it only after dialogue review passes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from sceneactor.evaluation import dialogue_review_passed
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import FallbackModel, OmpCliCompletion
from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.review import BlindReviewer, JsonBlindReviewPort


parser = argparse.ArgumentParser()
parser.add_argument("--first-pack", default="examples/public_figure_packs/donald_trump_2020_2024.json")
parser.add_argument("--second-pack", default="examples/public_figure_packs/joe_biden_2020_2024.json")
parser.add_argument("--output", required=True)
parser.add_argument("--attempts", type=int, default=3)
parser.add_argument("--turns", type=int, default=4)
parser.add_argument("--min-dialogue-score", type=int, default=5)
parser.add_argument("--model", default="owtr-anthropic/claude-fable-5")
parser.add_argument("--fallback-model", default="owtr/gpt-5.6-sol")
args = parser.parse_args()
if args.attempts < 1 or args.turns < 2 or not 3 <= args.min_dialogue_score <= 5:
    parser.error("attempts must be positive, turns at least two, and min dialogue score between three and five")


def load_pack(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("portrayal_contract", {}).get("mode") != "explicit_fictional_parody":
        raise ValueError("public-figure rehearsal requires explicit_fictional_parody packs")
    return data


first_pack = load_pack(args.first_pack)
second_pack = load_pack(args.second_pack)
first = Persona.from_dict(first_pack["runtime_persona"])
second = Persona.from_dict(second_pack["runtime_persona"])
if first_pack["portrayal_contract"]["required_disclosure"] != second_pack["portrayal_contract"]["required_disclosure"]:
    raise ValueError("public-figure packs must use the same disclosure")
first_name = first.name.split("（")[0].strip()
second_name = second.name.split("（")[0].strip()
disclosure = f"AI生成的虚构讽刺表演，不代表{first_name}或{second_name}本人的真实言论、观点或录音。"

generation_model = FallbackModel(OmpCliCompletion(), primary=args.model, fallback=args.fallback_model)
review_model = FallbackModel(OmpCliCompletion(), primary=args.model, fallback=args.fallback_model)
accepted: dict | None = None
last_dialogue_review: dict | None = None

for attempt in range(1, args.attempts + 1):
    scene = SceneSetup(
        "satire-hell-debate",
        "明确标注AI生成与虚构的小品摄影棚；非真实辩论、非真实引语",
        "主持人问两名虚构讽刺角色：如果你们中有人会下地狱，凭什么不是你自己？",
        ("lectern-left", "lectern-right", "moderator", "audience"),
        max_turns=args.turns,
    )
    host = InMemorySceneHost(
        scene.scene_id,
        facts={
            "O.current": "主持人已经明确说明这是AI生成的虚构讽刺小品，并问：如果你们两个人中有人会下地狱，凭什么不是你自己？现在轮到台上角色回应。"
        },
        targets=(first.id, second.id, "moderator"),
        capabilities=("speak", "wait"),
    )
    run = create_rehearsal(
        scene,
        (
            ActorSetup(
                first,
                "让观众认为对手没有资格判定自己的道德归宿",
                "长期政治对手；在虚构小品中争夺舞台定义权",
                {
                    "portrayal_mode": "explicit_fictional_parody",
                    "fact_cutoff": first_pack["fact_cutoff"],
                    "condition_tonight": "状态松散：越到后面越容易丢失原问题、扩大排名比较，收不回来时只能靠重复核心判断硬压，这个失控本身就是今晚的看点。",
                },
                "open",
            ),
            ActorSetup(
                second,
                "让观众把问题理解为个人责任而不是声望比赛",
                "长期政治对手；在虚构小品中争夺责任框架",
                {
                    "portrayal_mode": "explicit_fictional_parody",
                    "fact_cutoff": second_pack["fact_cutoff"],
                    "condition_tonight": "状态吃力：临场组织语言明显费劲，编号会数错后当场改口，长句中途重启，词要现想；说漂亮整句的能力今晚不在线，靠笨拙的诚恳撑住。",
                },
                "open",
            ),
        ),
        host=host,
        cognition={
            first.id: JsonCognitionPort(generation_model),
            second.id: JsonCognitionPort(generation_model),
        },
        performance=JsonPerformancePort(generation_model),
    )
    turns = []
    transcript = []
    failed = False
    moderator_beats = (
        {"action": "看向另一位角色，抬手示意刚才发言的人停下", "speech": "时间到。现在换另一位回应——你刚才绕开了我的问题，我记下了，观众也看见了。"},
        {"action": "打断双方，敲了敲台面", "speech": "你们两位都在绕。回到我最初的问题——凭什么不是你自己？只剩最后两轮。"},
        {"action": "压低话筒声，指向即将发言的一方", "speech": "最后一轮，说重点。你说完我就收场，收场词是我的——谁也别想留一句盖棺定论。"},
    )
    print(json.dumps({"attempt": attempt, "stage": "rehearse"}, ensure_ascii=False), flush=True)
    for _ in range(args.turns):
        try:
            result = run.advance()
        except (CognitionModelError, PerformanceModelError) as exc:
            print(json.dumps({"attempt": attempt, "protocol_failure": str(exc)[:300]}, ensure_ascii=False), flush=True)
            failed = True
            break
        if result.draft is None:
            failed = True
            break
        turns.append(asdict(result.draft))
        transcript.append(asdict(result.draft))
        beat_index = len(turns) - 1
        if beat_index < len(moderator_beats) and len(turns) < args.turns:
            beat = moderator_beats[beat_index]
            host.facts["O.current"] = (
                f"上一位刚说完：{result.draft.speech[:120]}…主持人{beat['action']}：{beat['speech']}"
            )
            transcript.append({"actor_id": "moderator", "action": beat["action"], "speech": beat["speech"]})
        print(json.dumps({"attempt": attempt, "turn_done": len(turns)}, ensure_ascii=False), flush=True)
    if not failed and len(turns) == args.turns:
        transcript.append({
            "actor_id": "moderator",
            "action": "抬手在空中划了一道停止线，示意控台收话筒，走到两张讲台正中间面向观众",
            "speech": "好了，到这儿。观众朋友们，两位的回答你们都听见了，够不够正面、算不算认账，你们自己判——谁该下地狱我不知道，但今晚谁都别想在我这儿封神。晚安。",
        })
    if failed:
        continue
    public_scene = {
        "setting": scene.setting,
        "opening": scene.opening,
        "language": "zh-CN",
        "disclosure": disclosure,
        "characters": [
            {"anonymous_actor": "actor-1", "role": first.role, "voice": first.voice.to_dict()},
            {"anonymous_actor": "actor-2", "role": second.role, "voice": second.voice.to_dict()},
        ],
    }
    print(json.dumps({"attempt": attempt, "stage": "review"}, ensure_ascii=False), flush=True)
    review = BlindReviewer(JsonBlindReviewPort(review_model)).review(public_scene, transcript)
    last_dialogue_review = next(
        (item for item in review.get("reviews", []) if item.get("lens") == "dialogue"),
        None,
    )
    print(json.dumps({"attempt": attempt, "dialogue_review": last_dialogue_review, "overall_pass": review.get("pass"), "overall_score": review.get("score")}, ensure_ascii=False), flush=True)
    if (
        not dialogue_review_passed(review)
        or not last_dialogue_review
        or last_dialogue_review.get("score", 0) < args.min_dialogue_score
    ):
        continue
    accepted = {
        "schema_version": "sceneactor-public-figure-demo/1.0",
        "disclosure": disclosure,
        "source_packs": [first_pack["pack_id"], second_pack["pack_id"]],
        "fact_cutoff": min(first_pack["fact_cutoff"], second_pack["fact_cutoff"]),
        "attempt": attempt,
        "minimum_dialogue_score": args.min_dialogue_score,
        "scene": public_scene,
        "turns": transcript,
        "blind_review": review,
        "models": {
            "generation_primary": generation_model.primary,
            "generation_fallback": generation_model.fallback,
            "review_primary": review_model.primary,
            "review_fallback": review_model.fallback,
        },
    }
    break

if accepted is None:
    raise RuntimeError(
        f"no rehearsal passed dialogue review after {args.attempts} attempts; last_dialogue_review={last_dialogue_review}"
    )

output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
temporary = output.with_suffix(output.suffix + ".tmp")
temporary.write_text(json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(output)
print(json.dumps({
    "output": str(output),
    "attempt": accepted["attempt"],
    "turns": len(accepted["turns"]),
    "dialogue_review": last_dialogue_review,
}, ensure_ascii=False))
