#!/usr/bin/env python3
"""Distill a cognitive genome from six-dimension research material.

Usage:
  PYTHONPATH=src python3 scripts/distill_genome.py <person_id> [--model claude-opus-4.8]

Expects data/genomes/research/<person_id>/ to contain:
  02-conversations.txt   (verbatim corpus, one [context-year] line per sentence)
  dims.md                (six-section research markdown incl. 04/05/06)

Produces data/genomes/records/<person_id>.json and updates index status.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sceneactor.genome import CognitiveGenome, rules_from_specs  # noqa: E402

GATEWAY = "http://161.118.219.11:8081/v1/chat/completions"

SCHEMA_SNIPPET = """{
 "trait_axes": {"control_need":{"score":0,"evidence":""},"uncertainty_tolerance":{},"trust_propensity":{},"risk_appetite":{},"self_efficacy":{},"autonomy_need":{},"intimacy_need":{},"status_sensitivity":{},"empathy_reactivity":{}},
 "core_models": [{"name":"","rule":"","evidence":["",""]}],
 "heuristics": [{"if":"","then":"","evidence":""}],
 "value_weights": {"...":0},
 "internal_conflicts": [{"force_a":"","force_b":"","behavioral_signature":""}],
 "emotion_triggers": [{"trigger":"","reaction":"","escalation":""}],
 "blind_spots": ["..."],
 "causal_rules": [{"rule_id":"","description":"","when":{"axis":">=0.65"},"disposition":"","behavioral_notes":""}]
}"""


def api_key() -> str:
    text = (Path.home() / ".omp/agent/models.yml").read_text(encoding="utf-8")
    return re.search(r"apiKey:\s*(\S+)", text).group(1)


def chat(model: str, prompt: str, timeout: int = 1200) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
    }).encode()
    request = urllib.request.Request(GATEWAY, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key()}",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return payload["choices"][0]["message"]["content"]


def build_prompt(corpus: str, dims: str) -> str:
    return (
        "You have SIX research dimensions for one public figure:\n"
        "- dim 02: verbatim speech corpus (below, [context-year] per line)\n"
        "- dims 01/03/04/05/06: writings, expression notes, external criticism, "
        "decision records, timeline (markdown below)\n\n"
        "Extract the person's cognitive genome. HARD RULES:\n"
        "- trait_axes: score 0-1; evidence ONLY verbatim quotes from dim-02\n"
        "- core_models 3-7 (filters every problem passes through); heuristics 5-10; "
        "value_weights sum to 1 and MUST be inferred from dim-05 decisions, not slogans\n"
        "- internal_conflicts 2-4: opposing forces + VISIBLE behavioral signature; "
        "cross-check dim-02 speech against dim-05 say-A-do-B records\n"
        "- emotion_triggers: trigger -> reaction -> escalation, from pressure lines in dim-02\n"
        "- blind_spots: each MUST cite a dim-04 external criticism item "
        "(format: '描述 [来源-年份]'); never inferred from self-speech alone\n"
        "- causal_rules: person-specific axis->disposition links you observe, "
        "spec format when:{axis:'>=x' or '<=x'}\n"
        "Output STRICT JSON only, per this schema (Chinese values natural, "
        "evidence in original language):\n" + SCHEMA_SNIPPET +
        "\n\n=== DIM-02 VERBATIM CORPUS ===\n" + corpus +
        "\n\n=== DIMS 01/03/04/05/06 RESEARCH ===\n" + dims
    )


def normalize(data: dict) -> dict:
    weights = data.get("value_weights", {})
    total = sum(weights.values()) or 1.0
    data["value_weights"] = {k: round(v / total, 4) for k, v in weights.items()}
    conflicts = data.get("internal_conflicts", [])
    if len(conflicts) > 4:
        data["internal_conflicts"] = conflicts[:4]
    models = data.get("core_models", [])
    if len(models) > 7:
        data["core_models"] = models[:7]
    heuristics = data.get("heuristics", [])
    if len(heuristics) > 10:
        data["heuristics"] = heuristics[:10]
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_id")
    parser.add_argument("--model", default="claude-opus-4.8")
    args = parser.parse_args()

    research = ROOT / "data/genomes/research" / args.person_id
    corpus_path = research / "02-conversations.txt"
    dims_path = research / "dims.md"
    if not corpus_path.is_file() or not dims_path.is_file():
        raise SystemExit(f"missing research files under {research}")
    corpus = corpus_path.read_text(encoding="utf-8")
    dims = dims_path.read_text(encoding="utf-8")
    lines = [l for l in corpus.splitlines() if l.strip().startswith("[")]
    print(f"[{args.person_id}] corpus {len(lines)} lines, dims {len(dims)} chars", flush=True)

    data = None
    for attempt in range(3):
        output = chat(args.model, build_prompt(corpus, dims))
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            print(f"[{args.person_id}] attempt {attempt + 1}: no JSON, retrying", flush=True)
            continue
        try:
            data = normalize(json.loads(match.group(0)))
            break
        except json.JSONDecodeError as exc:
            print(f"[{args.person_id}] attempt {attempt + 1}: bad JSON ({exc}), retrying", flush=True)
    if data is None:
        raise SystemExit("model returned no parseable JSON after 3 attempts")

    genome = CognitiveGenome.from_dict(
        data, genome_id=args.person_id,
        source=f"{args.person_id}-genome-db", source_real_person=True,
    )
    specs = data.get("causal_rules", [])
    if specs:
        rules_from_specs(specs)  # validates parseability

    index_path = ROOT / "data/genomes/index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next((p for p in index["people"] if p["person_id"] == args.person_id), None)

    record = {
        "person_id": args.person_id,
        "display_name": entry["display_name"] if entry else args.person_id,
        "name_en": (entry or {}).get("name_en", ""),
        "region": (entry or {}).get("region", "INTL"),
        "industries": (entry or {}).get("industries", []),
        "gender": (entry or {}).get("gender", "unknown"),
        "living": (entry or {}).get("living", True),
        "status": "distilled",
        "corpus_stats": {
            "verbatim_lines": len(lines),
            "conversation_contexts": len({l.split("]")[0] for l in lines}),
        },
        "research": {"dimensions": {
            "01-writings": {"status": "complete", "sources": []},
            "02-conversations": {"status": "complete", "sources": [],
                                 "artifact": f"research/{args.person_id}/02-conversations.txt"},
            "03-expression": {"status": "complete", "sources": []},
            "04-external": {"status": "complete", "sources": [],
                            "artifact": f"research/{args.person_id}/dims.md"},
            "05-decisions": {"status": "complete", "sources": [],
                             "artifact": f"research/{args.person_id}/dims.md"},
            "06-timeline": {"status": "complete", "sources": [],
                            "artifact": f"research/{args.person_id}/dims.md"},
        }},
        "genome": genome.to_dict() | {"causal_rules": specs},
    }
    records = ROOT / "data/genomes/records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{args.person_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if entry:
        entry["status"] = "distilled"
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{args.person_id}] distilled OK -> records/{args.person_id}.json", flush=True)


if __name__ == "__main__":
    main()
