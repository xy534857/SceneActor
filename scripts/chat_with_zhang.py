#!/usr/bin/env python3
"""Live consult with the Zhang Laoshi parody persona.

You are the caller (家长或学生), the NPC is 张老师. Every turn runs the
full cognition -> host -> performance chain: he decides what he noticed,
what he asks or judges, and how it lands. Nothing is scripted.

Usage:
  PYTHONPATH=src python3 scripts/chat_with_zhang.py \
      --gateway-url http://... --gateway-key ... [--model claude-fable-5]

Type your lines; /quit to leave, /scene to reprint the setting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.hosts import InMemorySceneHost
from sceneactor.model import FallbackModel, GatewayCompletion, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal

parser = argparse.ArgumentParser()
parser.add_argument("--pack", default="examples/zhang_laoshi/persona_pack.json")
parser.add_argument("--model", default="claude-fable-5")
parser.add_argument("--fallback-model", default="claude-opus-4.6")
parser.add_argument("--gateway-url", default="")
parser.add_argument("--gateway-key", default="")
parser.add_argument("--caller-name", default="连麦观众")
parser.add_argument("--transcript", default="", help="save the session transcript to this path")
args = parser.parse_args()

pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
zhang = Persona.from_dict(pack["runtime_persona"])
disclosure = pack["portrayal_contract"]["required_disclosure"]

CALLER_ID = "caller"
SCENE_ID = "zhang-live-consult"


def completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key)
    return OmpCliCompletion(thinking="low")


model = FallbackModel(completion(), primary=args.model, fallback=args.fallback_model)

host = InMemorySceneHost(
    SCENE_ID,
    facts={
        "O.current": "直播连麦刚接通。屏幕那头的观众还没说话，张老师刚喝了口保温杯里的水。",
        "W.disclosure": disclosure,
        "W.format": "高考志愿/考研咨询直播，一对一连麦，后面还排着几十个人，时间有限。",
    },
    targets=(zhang.id, CALLER_ID, "audience"),
    capabilities=("speak", "wait", "interact"),
)
scene = SceneSetup(
    SCENE_ID,
    "直播间。张老师面前一杯茶、一台电脑，弹幕在滚。连麦一对一，后面排着长队。",
    "一位观众连上了麦。",
    ("desk", "screen", "tea", "queue"),
    max_turns=10_000,
)

# The caller is a real human; give the runtime a stub persona for the seat.
caller = Persona(
    CALLER_ID, args.caller_name,
    role="连麦咨询的观众（真人输入）",
    background="不详，等对话展开",
)

run = create_rehearsal(
    scene,
    (
        ActorSetup(
            zhang,
            "在有限的连麦时间里把这个家庭的真实情况问清楚，给出具体到学校专业的可执行判断",
            "对连麦观众：第一次接触的咨询者，参数未知",
            {
                "condition_tonight": "嗓子有点哑，但状态在线；今天连麦的人多，节奏要快，废话要少。",
                "portrayal_mode": "explicit_fictional_parody",
                "disclosure": disclosure,
            },
            "open",
        ),
        ActorSetup(caller, "咨询", "对张老师：慕名而来", {}, "open"),
    ),
    host=host,
    cognition={zhang.id: JsonCognitionPort(model)},
    performance=JsonPerformancePort(model),
)

transcript: list[dict] = []
print(f"[{disclosure}]")
print("[已连麦。直接打字说话；/quit 退出]")
print()

# 张老师先开口接麦
pending_caller_line: str | None = None
while True:
    try:
        result = run.advance(zhang.id)
    except (CognitionModelError, PerformanceModelError) as exc:
        print(f"[协议失败，重试一次: {str(exc)[:120]}]")
        try:
            result = run.advance(zhang.id)
        except (CognitionModelError, PerformanceModelError) as exc2:
            print(f"[仍失败，退出: {str(exc2)[:120]}]")
            break
    if result.draft is None:
        print("[本回合无输出，继续]")
    else:
        draft = result.draft
        transcript.append({"actor_id": zhang.id, "speech": draft.speech, "action": draft.action})
        if draft.speech:
            print(f"张老师：{draft.speech}")
        if draft.action and draft.action != "speak":
            print(f"        （{draft.action}）")
        print()
    try:
        line = input("你 > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if line in ("/quit", "/q", "exit"):
        break
    if line == "/scene":
        print(f"[{scene.setting}]")
        continue
    if not line:
        line = "（沉默）"
    transcript.append({"actor_id": CALLER_ID, "speech": line, "action": ""})
    # 真人台词以观察事实进入张老师下一回合
    host.facts["O.current"] = f"连麦观众刚说：{line}"
    # 同时进入他的可见历史
    run.turns_external_append = getattr(run, "turns_external_append", None)
    # 直接借用 rehearsal 的历史机制：把真人回合注入 host 观察即可，
    # recent_history 由 runtime 从 turns 构建，真人不产生 runtime 回合，
    # 因此完整对话记录也随 O.current 滚动附带：
    recent = [t for t in transcript[-6:]]
    host.facts["H.recent_dialogue"] = json.dumps(recent, ensure_ascii=False)

if args.transcript and transcript:
    Path(args.transcript).parent.mkdir(parents=True, exist_ok=True)
    Path(args.transcript).write_text(
        json.dumps({"disclosure": disclosure, "turns": transcript}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[对话已存至 {args.transcript}]")
