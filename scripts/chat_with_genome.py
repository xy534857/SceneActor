#!/usr/bin/env python3
"""Chat with any distilled genome from the database.

Usage:
  PYTHONPATH=src python3 scripts/chat_with_genome.py sam-altman
  PYTHONPATH=src python3 scripts/chat_with_genome.py guo-degang --scene "后台化妆间，刚下台"

The genome supplies cognition (models/heuristics/conflicts/triggers/blind
spots); a light scene shell and voice are synthesized on the fly. The runtime
is the same independent-actor stack used for performances.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.genome import compile_persona, compose_genomes, load_genome, rules_from_specs
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import FallbackModel, GatewayCompletion, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal

ROOT = Path(__file__).resolve().parent.parent

parser = argparse.ArgumentParser()
parser.add_argument("person_id", help="a distilled record under data/genomes/records/")
parser.add_argument("--scene", default="", help="override the scene shell (one sentence)")
parser.add_argument("--model", default="claude-fable-5")
parser.add_argument("--fallback-model", default="claude-opus-4.8")
parser.add_argument("--gateway-url", default="http://161.118.219.11:8081/v1")
parser.add_argument("--gateway-key", default="")
parser.add_argument("--lang", default="", help="force output language, e.g. zh-CN / en-US")
parser.add_argument("--transcript", default="")
args = parser.parse_args()

record_path = ROOT / "data/genomes/records" / f"{args.person_id}.json"
if not record_path.is_file():
    available = sorted(p.stem for p in (ROOT / "data/genomes/records").glob("*.json"))
    raise SystemExit(f"no record for {args.person_id!r}. available: {', '.join(available)}")
record = json.loads(record_path.read_text(encoding="utf-8"))

# ---- genome -> persona ------------------------------------------------------
from sceneactor.genome import CognitiveGenome  # noqa: E402

g = CognitiveGenome.from_dict(
    record["genome"], genome_id=record["person_id"],
    source=f"{record['person_id']}-genome-db", source_real_person=True,
)
composite = compose_genomes(
    record["person_id"], [(g, None)],
    rules=rules_from_specs(record["genome"].get("causal_rules", [])),
)

display = record.get("display_name") or record["person_id"]
region = record.get("region", "INTL")
lang = args.lang or ("zh-CN" if region == "CN" else "en-US")
industries = "、".join(record.get("industries", [])) or "公众人物"
default_scene = (
    f"一场轻松的一对一长谈。{display}状态放松，对面是一位好奇但功课做得不错的访谈者。"
    if lang == "zh-CN" else
    f"A relaxed one-on-one long-form conversation. {display} is at ease; the "
    f"interviewer opposite is curious and well-prepared."
)
scene_text = args.scene or default_scene

persona = compile_persona(
    composite,
    persona_id=record["person_id"],
    name=f"{display}（AI虚构演绎）",
    shell={
        "role": f"{industries}领域公众人物的非欺骗性AI演绎",
        "gender": record.get("gender", ""),
        "background": f"基于公开语料蒸馏的认知基因组驱动。场景：{scene_text}",
    },
    voice={
        "output_language": lang,
        "turn_economy": "每回合1-4句，像真人聊天，不做演讲，不列清单。",
        "localization_rule": (
            "说人话：口语、具体、允许不完整句和现场修正；绝不用客服腔和总结腔。"
            if lang == "zh-CN" else
            "Talk like a person: colloquial, concrete, self-corrections allowed; "
            "never sound like support staff or a summary."
        ),
    },
)

DISCLOSURE = (
    f"AI生成的虚构角色演绎，基于{display}的公开语料蒸馏，不代表本人真实观点。"
)

USER_ID = "visitor"
SCENE_ID = f"genome-chat-{record['person_id']}"


def completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key or _key_from_models_yml())
    return OmpCliCompletion(thinking="low")


def _key_from_models_yml() -> str:
    import re
    text = (Path.home() / ".omp/agent/models.yml").read_text(encoding="utf-8")
    return re.search(r"apiKey:\s*(\S+)", text).group(1)


model = FallbackModel(completion(), primary=args.model, fallback=args.fallback_model)

host = InMemorySceneHost(
    SCENE_ID,
    facts={
        "O.current": "对话刚开始，对面的人还没说话。" if lang == "zh-CN" else
                     "The conversation just started; the visitor hasn't spoken yet.",
        "W.disclosure": DISCLOSURE,
        "W.format": scene_text,
    },
    targets=(persona.id, USER_ID),
    capabilities=("speak", "wait", "interact"),
)
scene = SceneSetup(SCENE_ID, scene_text, "访谈者刚坐下。", ("seat", "table"), max_turns=24)

visitor = Persona(USER_ID, "访谈者", role="真人输入", background="不详，随对话展开")


class _HumanSeat:
    """Cognition port for the human seat; never advanced by this script."""

    def decide(self, frame):  # pragma: no cover - guard only
        raise RuntimeError("human seat should never be advanced")


run = create_rehearsal(
    scene,
    (
        ActorSetup(
            persona,
            "像本人一样接住对话：有自己的议程和边界，不迎合，不表演金句" if lang == "zh-CN" else
            "Hold the conversation like the real person: own agenda, own boundaries, no pandering.",
            "对访谈者：初次见面" if lang == "zh-CN" else "First meeting with the visitor.",
            {"portrayal_mode": "explicit_fictional_parody", "disclosure": DISCLOSURE},
            "open",
        ),
        ActorSetup(visitor, "聊天", "初次见面", {}, "open"),
    ),
    host=host,
    cognition={persona.id: JsonCognitionPort(model), USER_ID: _HumanSeat()},
    performance=JsonPerformancePort(model),
)

transcript: list[dict] = []
print(f"[{DISCLOSURE}]")
print(f"[场景: {scene_text}]")
print("[直接打字说话；/quit 退出；/genome 看认知档案]")
print()

while True:
    try:
        result = run.advance(persona.id)
    except (CognitionModelError, PerformanceModelError) as exc:
        print(f"[协议失败，重试一次: {str(exc)[:120]}]")
        try:
            result = run.advance(persona.id)
        except (CognitionModelError, PerformanceModelError) as exc2:
            print(f"[仍失败，退出: {str(exc2)[:120]}]")
            break
    if result.draft is not None:
        draft = result.draft
        transcript.append({"actor_id": persona.id, "speech": draft.speech, "action": draft.action})
        if draft.speech:
            print(f"{display}：{draft.speech}")
        if draft.action and draft.action != "speak":
            print(f"    （{draft.action}）")
        print()
    try:
        line = input("你 > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if line in ("/quit", "/q", "exit"):
        break
    if line == "/genome":
        gd = record["genome"]
        print(json.dumps({
            "trait_axes": {k: v["score"] for k, v in gd["trait_axes"].items()},
            "core_models": [m["name"] for m in gd["core_models"]],
            "dispositions": [d["rule_id"] for d in composite["derived_dispositions"]],
            "blind_spots": gd["blind_spots"][:3],
        }, ensure_ascii=False, indent=1))
        continue
    if not line:
        line = "（沉默）" if lang == "zh-CN" else "(silence)"
    transcript.append({"actor_id": USER_ID, "speech": line, "action": ""})
    host.facts["O.current"] = (f"对面的人刚说：{line}" if lang == "zh-CN"
                               else f"The visitor just said: {line}")
    host.facts["H.recent_dialogue"] = json.dumps(transcript[-6:], ensure_ascii=False)

if args.transcript and transcript:
    Path(args.transcript).parent.mkdir(parents=True, exist_ok=True)
    Path(args.transcript).write_text(
        json.dumps({"disclosure": DISCLOSURE, "person": record["person_id"],
                    "turns": transcript}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[对话已存至 {args.transcript}]")
