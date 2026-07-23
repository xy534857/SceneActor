"""Forge custom personas: questionnaire synthesis and library fusion.

Two entry points, both returning a record dict in the same shape as
``data/genomes/records/*.json`` so ChatSession can consume it unchanged:

- ``forge_from_questionnaire``: user answers situational questions that pin
  the 9 trait axes plus identity/style/values; one model call synthesizes the
  qualitative layers (mental models, heuristics, conflicts, triggers, blind
  spots) consistent with those axes.
- ``forge_from_fusion``: weighted causal composition of existing records via
  ``compose_genomes`` — deterministic, no model call.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Mapping, Sequence

from sceneactor.genome import (
    TRAIT_AXES,
    CognitiveGenome,
    GenomeError,
    compose_genomes,
)

AXIS_LABELS = {
    "control_need": "控制欲",
    "uncertainty_tolerance": "不确定容忍",
    "trust_propensity": "信任倾向",
    "risk_appetite": "风险偏好",
    "self_efficacy": "自我效能",
    "autonomy_need": "自主需求",
    "intimacy_need": "亲密需求",
    "status_sensitivity": "地位敏感",
    "empathy_reactivity": "共情反应",
}

_SYNTHESIS_PROMPT = """你是角色设计师。用户通过问卷捏了一个虚构人物，你要把问卷结果补全为完整的认知基因组。

人物设定：
- 姓名：{name}
- 身份背景：{background}
- 说话风格/口头禅：{style}
- 核心价值观（按重要度）：{values}
- 最受不了的事：{fear}

九轴分数（0-1，问卷测得，你必须严格遵守，不得改动）：
{axes_block}

生成与这些轴分**因果一致**的其余层。硬要求：
- core_models 3-5个：这个人理解世界的筛子，每个有 name 和 rule（一句话规则）
- heuristics 5-8条：具体情境的 if-then 条件反射（condition/action），要能从轴分推出来
- internal_conflicts 2-3个：两股内在力量的对抗 + 可观察的行为特征（force_a/force_b/behavioral_signature）
- emotion_triggers 2-3个：什么话/事点炸他（trigger/reaction/escalation），必须涵盖用户写的"最受不了的事"
- blind_spots 2-4条：这个人看不见自己什么（从高分轴的阴影面推导）
所有文本用中文，具体、口语化、可表演，不要人格测试报告腔。

只输出JSON：
{{"core_models":[{{"name":"","rule":""}}],"heuristics":[{{"condition":"","action":""}}],"internal_conflicts":[{{"force_a":"","force_b":"","behavioral_signature":""}}],"emotion_triggers":[{{"trigger":"","reaction":"","escalation":""}}],"blind_spots":[""]}}"""


def _validate_record(record: dict) -> None:
    CognitiveGenome.from_dict(
        record["genome"], genome_id=record["person_id"],
        source=record["genome"].get("source", record["person_id"]),
    )


def forge_from_questionnaire(
    *,
    name: str,
    background: str,
    style: str,
    values: Sequence[str],
    fear: str,
    axes: Mapping[str, float],
    complete: Callable[[list[dict[str, str]], str], str],
) -> dict:
    """One model call fills the qualitative genome layers around fixed axes."""
    if not name.strip():
        raise GenomeError("name is required")
    clean_axes: dict[str, float] = {}
    for axis in TRAIT_AXES:
        try:
            score = float(axes[axis])
        except (KeyError, TypeError, ValueError) as exc:
            raise GenomeError(f"axis {axis} missing or invalid") from exc
        if not 0.0 <= score <= 1.0:
            raise GenomeError(f"axis {axis}={score} out of [0,1]")
        clean_axes[axis] = round(score, 3)

    value_list = [v.strip() for v in values if v.strip()][:4] or ["真实"]
    # Rank-weighted, normalized: first value matters most.
    raw = {v: 1.0 / (i + 1) for i, v in enumerate(value_list)}
    total = sum(raw.values())
    value_weights = {k: round(w / total, 4) for k, w in raw.items()}

    axes_block = "\n".join(
        f"- {axis} {AXIS_LABELS[axis]}: {score}" for axis, score in clean_axes.items()
    )
    prompt = _SYNTHESIS_PROMPT.format(
        name=name.strip(), background=background.strip() or "不详",
        style=style.strip() or "自然口语", values="、".join(value_list),
        fear=fear.strip() or "未提供", axes_block=axes_block,
    )

    data = None
    for _ in range(3):
        output = complete([{"role": "user", "content": prompt}], "forge")
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(0))
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        raise GenomeError("model returned no parseable synthesis JSON")

    person_id = f"custom-{uuid.uuid4().hex[:8]}"
    genome = {
        "source": f"{person_id}-questionnaire",
        "trait_axes": {
            axis: {"score": score, "evidence": "问卷自评"}
            for axis, score in clean_axes.items()
        },
        "core_models": data.get("core_models", [])[:5],
        "heuristics": data.get("heuristics", [])[:8],
        "value_weights": value_weights,
        "internal_conflicts": data.get("internal_conflicts", [])[:3],
        "emotion_triggers": data.get("emotion_triggers", [])[:3],
        "blind_spots": data.get("blind_spots", [])[:4],
        "causal_rules": [],
    }
    record = {
        "person_id": person_id,
        "display_name": name.strip(),
        "name_en": "",
        "region": "CN",
        "industries": [background.strip()[:24] or "自定义角色"],
        "gender": "unknown",
        "living": True,
        "status": "custom",
        "library": "custom",
        "forge": {"mode": "questionnaire", "style": style.strip(), "fear": fear.strip()},
        "genome": genome,
    }
    _validate_record(record)
    return record


def forge_from_fusion(
    *,
    name: str,
    parts: Sequence[tuple[dict, float]],
    background: str = "",
) -> dict:
    """Compose 2-3 existing records into a fictional composite record."""
    if not name.strip():
        raise GenomeError("name is required")
    if not 2 <= len(parts) <= 3:
        raise GenomeError("fusion needs 2-3 source personas")

    genome_parts = []
    for record, weight in parts:
        genome = CognitiveGenome.from_dict(
            record["genome"], genome_id=record["person_id"],
            source=record.get("display_name", record["person_id"]),
            source_real_person=record.get("library") != "custom",
        )
        axis_weights = {axis: max(0.05, float(weight)) for axis in TRAIT_AXES}
        genome_parts.append((genome, axis_weights))

    person_id = f"custom-{uuid.uuid4().hex[:8]}"
    composite = compose_genomes(person_id, genome_parts)

    genome = {
        "source": f"{person_id}-fusion",
        "trait_axes": {
            axis: {
                "score": score,
                "evidence": "融合自 " + " + ".join(r["display_name"] for r, _ in parts),
            }
            for axis, score in composite["trait_axes"].items()
        },
        "core_models": [
            {"name": m["name"], "rule": m["rule"], "evidence": [f"承自{m['from']}"]}
            for m in composite["core_models"]
        ][:7],
        "heuristics": [
            {"condition": h["if"], "action": h["then"], "evidence": f"承自{h['from']}"}
            for h in composite["heuristics"]
        ][:10],
        "value_weights": composite["value_weights"],
        "internal_conflicts": [
            {"force_a": c["force_a"], "force_b": c["force_b"],
             "behavioral_signature": c["behavioral_signature"]}
            for c in composite["internal_conflicts"]
        ][:4],
        "emotion_triggers": [
            {"trigger": t["trigger"], "reaction": t["reaction"],
             "escalation": t.get("escalation", "")}
            for t in composite["emotion_triggers"]
        ],
        "blind_spots": composite["blind_spots"],
        "causal_rules": [],
    }
    sources = [r["display_name"] for r, _ in parts]
    record = {
        "person_id": person_id,
        "display_name": name.strip(),
        "name_en": "",
        "region": "CN",
        "industries": [background.strip()[:24] or ("融合：" + "×".join(sources))[:24]],
        "gender": "unknown",
        "living": True,
        "status": "custom",
        "library": "custom",
        "forge": {
            "mode": "fusion",
            "sources": [
                {"person_id": r["person_id"], "display_name": r["display_name"], "weight": w}
                for r, w in parts
            ],
            "disclosure": composite["provenance"].get("required_disclosure", ""),
            "derived_dispositions": composite["derived_dispositions"],
        },
        "genome": genome,
    }
    _validate_record(record)
    return record
