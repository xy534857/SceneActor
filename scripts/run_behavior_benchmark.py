#!/usr/bin/env python3
"""Run the frozen cognition benchmark through OMP's configured model registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sceneactor.benchmark import BehaviorBenchmark
from sceneactor.cognition import JsonCognitionPort
from sceneactor.model import FallbackModel, OmpCliCompletion


parser = argparse.ArgumentParser()
parser.add_argument("--benchmark", default="benchmarks/behavior_v1.json")
parser.add_argument("--limit", type=int, default=0)
parser.add_argument("--output", default="")
args = parser.parse_args()

benchmark = BehaviorBenchmark.load(args.benchmark)
if args.limit:
    benchmark = BehaviorBenchmark(benchmark.cases[: args.limit])
model = FallbackModel(OmpCliCompletion())
cognition = JsonCognitionPort(model, max_attempts=2)
records = benchmark.run(cognition)
payload = {
    "benchmark": args.benchmark,
    "models": {"primary": model.primary, "fallback": model.fallback},
    "records": [
        {
            "case_id": item.case_id,
            "category": item.category,
            "protocol_error": item.protocol_error,
            "appraisal": item.appraisal,
            "public_intent": item.public_intent,
            "anonymous_packet": item.anonymous_packet,
        }
        for item in records
    ],
}
text = json.dumps(payload, ensure_ascii=False, indent=2)
if args.output:
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
else:
    print(text)
