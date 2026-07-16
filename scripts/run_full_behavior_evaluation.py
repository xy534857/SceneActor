#!/usr/bin/env python3
"""Checkpointed full performance, blind review, and counterfactual evaluation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from sceneactor.benchmark import BehaviorBenchmark, BehaviorCase, load_counterfactual_pairs
from sceneactor.cognition import JsonCognitionPort
from sceneactor.evaluation import FullBehaviorEvaluator, assert_reviewable_evaluations, dialogue_review_passed
from sceneactor.model import FallbackModel, OmpCliCompletion
from sceneactor.performance import JsonPerformancePort
from sceneactor.review import BlindReviewer, JsonBlindReviewPort, JsonCounterfactualReviewPort


parser = argparse.ArgumentParser()
parser.add_argument("--benchmark", default="benchmarks/behavior_v1.json")
parser.add_argument("--counterfactual", default="benchmarks/counterfactual_v1.json")
parser.add_argument("--output", default="/tmp/sceneactor-full-evaluation.json")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--stage", choices=("all", "performances", "counterfactual"), default="all")
parser.add_argument("--case-attempts", type=int, default=2)
args = parser.parse_args()
if args.case_attempts < 1:
    parser.error("--case-attempts must be positive")

path = Path(args.output)
benchmark = BehaviorBenchmark.load(args.benchmark)
generation_model = FallbackModel(OmpCliCompletion())
review_model = FallbackModel(OmpCliCompletion())
evaluator = FullBehaviorEvaluator(
    cognition=JsonCognitionPort(generation_model),
    performance=JsonPerformancePort(generation_model),
    reviewer=BlindReviewer(JsonBlindReviewPort(review_model)),
    counterfactual_reviewer=JsonCounterfactualReviewPort(review_model),
)

payload = {
    "benchmark": args.benchmark,
    "counterfactual_benchmark": args.counterfactual,
    "models": {
        "generation_primary": generation_model.primary,
        "generation_fallback": generation_model.fallback,
        "review_primary": review_model.primary,
        "review_fallback": review_model.fallback,
    },
    "performances": [],
    "role_swaps": [],
    "same_stimulus": {},
}
if args.resume and path.exists():
    restored = json.loads(path.read_text(encoding="utf-8"))
    if restored.get("benchmark") == args.benchmark and restored.get("counterfactual_benchmark") == args.counterfactual:
        payload = restored


def save() -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


if args.stage in {"all", "performances"}:
    payload["performances"] = [
        item for item in payload["performances"]
        if not item.get("protocol_error")
        and item.get("performance")
        and dialogue_review_passed(item.get("review", {}))
    ]
    completed_cases = {item["case_id"] for item in payload["performances"]}
    for index, case in enumerate(benchmark.cases):
        if case.id in completed_cases:
            continue
        result = None
        for _ in range(args.case_attempts):
            result = asdict(evaluator.evaluate_case(case, f"actor-{index + 1}"))
            if not result["protocol_error"] and dialogue_review_passed(result.get("review", {})):
                break
        assert result is not None
        payload["performances"].append(result)
        save()
        if result["protocol_error"]:
            raise RuntimeError(f"case {case.id} failed after {args.case_attempts} attempts: {result['protocol_error']}")
        if not dialogue_review_passed(result.get("review", {})):
            raise RuntimeError(f"case {case.id} failed dialogue review after {args.case_attempts} attempts")
    assert_reviewable_evaluations(payload["performances"])

if args.stage in {"all", "counterfactual"}:
    completed_swaps = {
        (item["original_case"], item["donor_identity"])
        for item in payload["role_swaps"]
    }
    for pair in load_counterfactual_pairs(args.counterfactual):
        actors = pair["actors"]
        base = BehaviorCase(
            id=f"counterfactual:{pair['id']}", category="counterfactual",
            persona=actors[0], private_state=pair["private_state"], relationship=pair["relationship"],
            observation=pair["stimulus"], targets=(pair["target"],),
            capabilities=("speak", "wait"), actions=("speak", "wait"),
        )
        for donor in actors[1:]:
            donor_case = BehaviorCase(
                id=f"counterfactual:{pair['id']}:{donor['id']}", category="counterfactual",
                persona=donor, private_state=pair["private_state"], relationship=pair["relationship"],
                observation=pair["stimulus"], targets=(pair["target"],),
                capabilities=("speak", "wait"), actions=("speak", "wait"),
            )
            key = (base.id, donor_case.id)
            if key in completed_swaps:
                continue
            payload["role_swaps"].append(evaluator.role_swap(base, donor_case))
            save()
    if not payload["same_stimulus"]:
        by_id = {case.id: case for case in benchmark.cases}
        payload["same_stimulus"] = evaluator.same_stimulus(
            stimulus={"O.current": "门关闭，外面开始下雨；守门人让所有人原地等候"},
            personas=(
                by_id["quiet_cooperation"].persona,
                by_id["direct_refusal"].persona,
                by_id["misunderstanding"].persona,
            ),
            target="guard",
            private_state={"goal": "回应守门人的等待要求"},
            relationships={"guard": {"summary": "控制入口的人", "disclosure": "guarded"}},
        )
        save()

print(json.dumps({
    "output": str(path),
    "performances": len(payload["performances"]),
    "swaps": len(payload["role_swaps"]),
    "same_stimulus": bool(payload["same_stimulus"]),
}, ensure_ascii=False))
