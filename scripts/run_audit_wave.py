#!/usr/bin/env python3
"""Run one manifest-locked independent SceneActor specialist review wave."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from sceneactor.audit import CausalPersonaConstraintCard, IndependentAuditBoard, JsonSpecialistAuditPort, ReviewContextManifest
from sceneactor.benchmark import BehaviorBenchmark
from sceneactor.model import FallbackModel, OmpCliCompletion


parser = argparse.ArgumentParser()
parser.add_argument("--evaluation", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

evaluation = json.loads(Path(args.evaluation).read_text(encoding="utf-8"))
prior_public: list[dict] = []
batch = [
    {
        "case_id": item["case_id"],
        "category": item["category"],
        "public_observation": item["public_observation"],
        "performance": item["performance"],
    }
    for item in evaluation.get("performances", [])
]
cpcf_cards = []
for case in BehaviorBenchmark.load("benchmarks/behavior_v1.json").cases:
    cpcf_cards.append(
        CausalPersonaConstraintCard(
            actor_id=str(case.persona.get("id", case.id)),
            direct_access=tuple(str(value) for value in case.observation.values()),
            acquired_knowledge=tuple(str(value) for value in case.private_state.values()),
            beliefs_and_inferences=(),
            unknown_or_forbidden=(),
            memory_relationship_state=tuple(str(value) for value in case.relationship.values()),
            physical_tool_capabilities=tuple(str(case.persona.get("competencies", "")),),
            social_legal_authority=(),
            conceptual_vocabulary=(),
        ).to_dict()
    )
character_cards = [item["character_card"] for item in evaluation.get("performances", [])]
authority = {
    "cases": [
        {
            "case_id": item["case_id"],
            "public_observation": item["public_observation"],
            "appraisal_grounded_refs": item.get("appraisal", {}).get("grounded_refs", []),
            "public_intent_evidence": item.get("public_intent", {}).get("evidence_anchors", {}),
        }
        for item in evaluation.get("performances", [])
    ]
}
public_scene = {"suite": "behavior_v1", "case_count": len(batch)}
manifest = ReviewContextManifest.freeze(
    prior_public=prior_public,
    batch=batch,
    authority=authority,
    character_cards=character_cards,
    cpcf_cards=cpcf_cards,
)
model = FallbackModel(OmpCliCompletion())
record = IndependentAuditBoard(JsonSpecialistAuditPort(model)).review(
    manifest=manifest,
    prior_public=prior_public,
    batch=batch,
    public_scene=public_scene,
    authority=authority,
    character_cards=character_cards,
    cpcf_cards=cpcf_cards,
)
output = {
    "source_evaluation": args.evaluation,
    "models": {"primary": model.primary, "fallback": model.fallback},
    "record": asdict(record),
}
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "output": str(path),
    "manifest": manifest.manifest_id,
    "readiness": record.readiness,
    "passed": record.passed,
    "hard_failures": len(record.hard_failures),
}, ensure_ascii=False))
