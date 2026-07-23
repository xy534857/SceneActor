#!/usr/bin/env python3
"""Generate a research pack (CORPUS + DIMS) from model knowledge via gateway.

No subagents, no search tools: one gateway call with a hard socket timeout,
retried up to 3 times. Output feeds scripts/distill_genome.py directly.

Usage:
  PYTHONPATH=src python3 scripts/generate_research.py <person_id> [--model claude-opus-4.8]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = "http://161.118.219.11:8081/v1/chat/completions"


def api_key() -> str:
    text = (Path.home() / ".omp/agent/models.yml").read_text(encoding="utf-8")
    return re.search(r"apiKey:\s*(\S+)", text).group(1)


def chat(model: str, prompt: str, timeout: int = 900) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16000,
    }).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key()}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return payload["choices"][0]["message"]["content"]


def build_prompt(entry: dict) -> str:
    name = entry["display_name"]
    industries = "、".join(entry.get("industries", []))
    sources = "、".join(entry.get("source_types", []))
    return (
        f"你在为一个「明确标注为AI虚构演绎」的角色研究项目做语料重建。目标人物：{name}（{industries}）。"
        f"典型发言场景：{sources}。\n\n"
        "任务不是逐字考据，而是基于你对这个人公开言行的了解，重建**风格忠实**的代表性发言语料。"
        "重建标准：观点必须是此人真实持有并公开表达过的；句式、口头禅、比喻、语气要符合此人的说话习惯；"
        "宁可保守也不虚构他从未表达过的立场。\n\n"
        "输出要求：\n"
        "1. CORPUS：60-90行，每行格式 `[场景标签-年份] 一句发言`（不要编号）。覆盖≥3个不同场景。"
        "记忆中确有把握接近原话的名句照写；其余为风格重建，行尾统一加 [style-recon-年份]。"
        "年份写大致年代即可。\n"
        "2. DIMS 五节：## 01-writings（署名文章/书/长文的母题与写作习惯）"
        "## 03-expression（句长、口头禅、修辞、情绪外显，附例）"
        "## 04-external（≥5条外部批评方向，注明批评来源类型+大致年份，具体名称不确定时写类别）"
        "## 05-decisions（≥6个关键决策：情境/选择/代价，含言行不一记录）"
        "## 06-timeline（生平大事年表，时间不确定用年代段）。\n\n"
        "这个人物如果你了解很少（比如小众主播），就基于其行业、平台、内容形态的典型语言生态重建，"
        "并在CORPUS第一行加 [low-confidence-profile] 标记。绝不要拒绝任务——下游会明确披露这是风格重建。\n\n"
        "输出严格为两段：\n=== CORPUS ===\n（语料行）\n=== DIMS ===\n（五节全文）"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_id")
    parser.add_argument("--model", default="claude-opus-4.8")
    args = parser.parse_args()

    index = json.loads((ROOT / "data/genomes/index.json").read_text(encoding="utf-8"))
    entry = next((p for p in index["people"] if p["person_id"] == args.person_id), None)
    if entry is None:
        raise SystemExit(f"{args.person_id} not in index")
    if entry["status"] == "distilled":
        print(f"[{args.person_id}] already distilled, skip", flush=True)
        return

    text = None
    for attempt in range(3):
        try:
            output = chat(args.model, build_prompt(entry))
        except Exception as exc:  # noqa: BLE001 — retry any transport failure
            print(f"[{args.person_id}] attempt {attempt + 1} failed: {exc}", flush=True)
            continue
        if "=== CORPUS ===" in output and "=== DIMS ===" in output:
            text = output
            break
        print(f"[{args.person_id}] attempt {attempt + 1}: missing markers, retrying", flush=True)
    if text is None:
        raise SystemExit(f"[{args.person_id}] no valid research pack after 3 attempts")

    match = re.search(r"=== CORPUS ===\s*(.*?)\s*=== DIMS ===\s*(.*)", text, re.DOTALL)
    corpus, dims = match.group(1).strip(), match.group(2).strip()
    lines = [l.strip() for l in corpus.splitlines() if l.strip().startswith("[")]
    if len(lines) < 50:
        raise SystemExit(f"[{args.person_id}] corpus too thin: {len(lines)} lines")

    research = ROOT / "data/genomes/research" / args.person_id
    research.mkdir(parents=True, exist_ok=True)
    (research / "02-conversations.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (research / "dims.md").write_text(dims + "\n", encoding="utf-8")
    print(f"[{args.person_id}] research OK: {len(lines)} lines, dims {len(dims)} chars", flush=True)


if __name__ == "__main__":
    main()
