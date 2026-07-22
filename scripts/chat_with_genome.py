#!/usr/bin/env python3
"""Chat with any distilled genome from the database.

Usage:
  PYTHONPATH=src python3 scripts/chat_with_genome.py sam-altman
  PYTHONPATH=src python3 scripts/chat_with_genome.py guo-degang --scene "后台化妆间，刚下台"
  PYTHONPATH=src python3 scripts/chat_with_genome.py sam-altman --search

The genome supplies cognition (models/heuristics/conflicts/triggers/blind
spots); a light scene shell and voice are synthesized on the fly. The runtime
is the same independent-actor stack used for performances.

--search enables web grounding: a cheap triage model decides whether your
line needs post-cutoff facts; if so a search briefing is fed to the actor as
world knowledge — the reply stays in character.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.chat import ChatSession
from sceneactor.cognition import CognitionModelError
from sceneactor.model import FallbackModel, GatewayCompletion, OmpCliCompletion
from sceneactor.performance import PerformanceModelError

ROOT = Path(__file__).resolve().parent.parent

parser = argparse.ArgumentParser()
parser.add_argument("person_id", help="a distilled record under data/genomes/records/")
parser.add_argument("--scene", default="", help="override the scene shell (one sentence)")
parser.add_argument("--model", default="claude-fable-5")
parser.add_argument("--fallback-model", default="claude-opus-4.8")
parser.add_argument("--triage-model", default="gemini-3.5-flash", help="cheap model for search triage")
parser.add_argument("--gateway-url", default="http://161.118.219.11:8081/v1")
parser.add_argument("--gateway-key", default="")
parser.add_argument("--lang", default="", help="force output language, e.g. zh-CN / en-US")
parser.add_argument("--search", action="store_true", help="enable web grounding for time-sensitive questions")
parser.add_argument("--verbosity", default="interview", choices=["brief", "interview", "deep"],
                    help="reply density: brief=short banter, interview=default long-form, deep=full paragraphs")
parser.add_argument("--transcript", default="")
args = parser.parse_args()

record_path = ROOT / "data/genomes/records" / f"{args.person_id}.json"
if not record_path.is_file():
    available = sorted(p.stem for p in (ROOT / "data/genomes/records").glob("*.json"))
    raise SystemExit(f"no record for {args.person_id!r}. available: {', '.join(available)}")
record = json.loads(record_path.read_text(encoding="utf-8"))


def _key_from_models_yml() -> str:
    import re

    text = (Path.home() / ".omp/agent/models.yml").read_text(encoding="utf-8")
    return re.search(r"apiKey:\s*(\S+)", text).group(1)


def completion():
    if args.gateway_url:
        return GatewayCompletion(args.gateway_url, args.gateway_key or _key_from_models_yml())
    return OmpCliCompletion(thinking="low")


gateway = completion()
model = FallbackModel(gateway, primary=args.model, fallback=args.fallback_model)
aux = FallbackModel(gateway, primary=args.triage_model, fallback=args.fallback_model)

session = ChatSession(
    record=record,
    model=model,
    aux_model=aux if args.search else None,
    scene=args.scene,
    lang=args.lang,
    search_enabled=args.search,
    verbosity=args.verbosity,
)

print(f"[{session.disclosure}]")
print(f"[场景: {session.scene_text}]")
mode = "联网检索已开启" if args.search else "纯表演模式（不联网）"
print(f"[{mode}；直接打字说话；/quit 退出；/genome 看认知档案]")
print()

try:
    turn = session.open()
except (CognitionModelError, PerformanceModelError) as exc:
    raise SystemExit(f"[开场失败: {str(exc)[:160]}]")
if turn.speech:
    print(f"{session.display}：{turn.speech}")
if turn.action:
    print(f"    （{turn.action}）")
print()

while True:
    try:
        line = input("你 > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if line in ("/quit", "/q", "exit"):
        break
    if line == "/genome":
        print(json.dumps(session.genome_card(), ensure_ascii=False, indent=1))
        continue
    try:
        turn = session.say(line)
    except (CognitionModelError, PerformanceModelError) as exc:
        print(f"[回合失败: {str(exc)[:160]}]")
        continue
    if turn.search:
        n = len(turn.search.get("hits", []))
        print(f"    [已检索「{turn.search['query']}」，{n} 条结果注入]")
    if turn.speech:
        print(f"{session.display}：{turn.speech}")
    if turn.action:
        print(f"    （{turn.action}）")
    print()

if args.transcript and session.transcript:
    Path(args.transcript).parent.mkdir(parents=True, exist_ok=True)
    Path(args.transcript).write_text(
        json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[对话已存至 {args.transcript}]")
