"""Cognitive genome: causally-constrained persona composition.

A genome is NOT a bag of tags. It is a typed extraction from first-hand
corpus evidence:

- 9 trait axes (0-1) with quoted evidence
- 3-7 core mental models (the filters every problem passes through)
- 5-10 situational heuristics (if X then Y)
- one normalized value-weight vector
- 2-4 internal conflicts (two opposing forces + a visible behavioral signature)
- emotion triggers (trigger -> reaction -> escalation path)
- explicit blind spots (what the person cannot see about themselves)

Composition is governed by CAUSAL RULES, not concatenation:

- Trait axes combine and then RULES derive dispositions, e.g.
  high control + low uncertainty tolerance + low trust -> micromanagement;
  high control + high risk + high self-efficacy -> expansionist gambling;
  high autonomy + high intimacy -> approach-avoidance relational tension.
- When two source genomes disagree strongly on an axis, the disagreement is
  NOT averaged away: it becomes an internal conflict of the composite
  (a person can hold both forces; a number cannot).
- Value weights renormalize; models/heuristics/triggers carry provenance and
  are deduplicated by rule text, never rewritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

TRAIT_AXES = (
    "control_need",
    "uncertainty_tolerance",
    "trust_propensity",
    "risk_appetite",
    "self_efficacy",
    "autonomy_need",
    "intimacy_need",
    "status_sensitivity",
    "empathy_reactivity",
)

# Axis distance beyond which a merged pair stops being one blended trait and
# becomes an internal conflict of the composite character.
CONFLICT_THRESHOLD = 0.45


class GenomeError(ValueError):
    pass


@dataclass(frozen=True)
class AxisScore:
    score: float
    evidence: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise GenomeError(f"axis score {self.score} out of [0,1]")


@dataclass(frozen=True)
class MentalModel:
    name: str
    rule: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Heuristic:
    condition: str
    action: str
    evidence: str = ""


@dataclass(frozen=True)
class InternalConflict:
    force_a: str
    force_b: str
    behavioral_signature: str


@dataclass(frozen=True)
class EmotionTrigger:
    trigger: str
    reaction: str
    escalation: str = ""


@dataclass(frozen=True)
class CognitiveGenome:
    """Validated extraction of how one person thinks, judges, and breaks."""

    genome_id: str
    source: str                       # corpus/pack provenance
    axes: Mapping[str, AxisScore]
    core_models: tuple[MentalModel, ...]
    heuristics: tuple[Heuristic, ...]
    value_weights: Mapping[str, float]
    internal_conflicts: tuple[InternalConflict, ...]
    emotion_triggers: tuple[EmotionTrigger, ...]
    blind_spots: tuple[str, ...]
    source_real_person: bool = False

    def __post_init__(self) -> None:
        missing = [axis for axis in TRAIT_AXES if axis not in self.axes]
        if missing:
            raise GenomeError(f"{self.genome_id}: missing trait axes {missing}")
        if not 3 <= len(self.core_models) <= 7:
            raise GenomeError(f"{self.genome_id}: need 3-7 core models, got {len(self.core_models)}")
        if not 5 <= len(self.heuristics) <= 10:
            raise GenomeError(f"{self.genome_id}: need 5-10 heuristics, got {len(self.heuristics)}")
        if not 2 <= len(self.internal_conflicts) <= 4:
            raise GenomeError(
                f"{self.genome_id}: need 2-4 internal conflicts, got {len(self.internal_conflicts)}")
        if not self.emotion_triggers:
            raise GenomeError(f"{self.genome_id}: at least one emotion trigger required")
        if not self.blind_spots:
            raise GenomeError(f"{self.genome_id}: blind spots are mandatory")
        total = sum(self.value_weights.values())
        if not 0.95 <= total <= 1.05:
            raise GenomeError(f"{self.genome_id}: value weights sum {total:.2f}, expected ~1")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, genome_id: str = "", source: str = "",
                  source_real_person: bool = False) -> "CognitiveGenome":
        axes = {
            name: AxisScore(float(item.get("score", 0.0)), str(item.get("evidence", "")))
            for name, item in (data.get("trait_axes") or {}).items()
        }
        return cls(
            genome_id=genome_id or str(data.get("genome_id", "genome")),
            source=source or str(data.get("source", "")),
            axes=axes,
            core_models=tuple(
                MentalModel(str(m.get("name", "")), str(m.get("rule", "")),
                            tuple(str(e) for e in m.get("evidence", ())))
                for m in data.get("core_models", ())),
            heuristics=tuple(
                Heuristic(str(h.get("if", "")), str(h.get("then", "")), str(h.get("evidence", "")))
                for h in data.get("heuristics", ())),
            value_weights={str(k): float(v) for k, v in (data.get("value_weights") or {}).items()},
            internal_conflicts=tuple(
                InternalConflict(str(c.get("force_a", "")), str(c.get("force_b", "")),
                                 str(c.get("behavioral_signature", "")))
                for c in data.get("internal_conflicts", ())),
            emotion_triggers=tuple(
                EmotionTrigger(str(t.get("trigger", "")), str(t.get("reaction", "")),
                               str(t.get("escalation", "")))
                for t in data.get("emotion_triggers", ())),
            blind_spots=tuple(str(b) for b in data.get("blind_spots", ())),
            source_real_person=source_real_person or bool(data.get("source_real_person", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "genome_id": self.genome_id,
            "source": self.source,
            "source_real_person": self.source_real_person,
            "trait_axes": {k: {"score": v.score, "evidence": v.evidence} for k, v in self.axes.items()},
            "core_models": [
                {"name": m.name, "rule": m.rule, "evidence": list(m.evidence)} for m in self.core_models],
            "heuristics": [
                {"if": h.condition, "then": h.action, "evidence": h.evidence} for h in self.heuristics],
            "value_weights": dict(self.value_weights),
            "internal_conflicts": [
                {"force_a": c.force_a, "force_b": c.force_b,
                 "behavioral_signature": c.behavioral_signature} for c in self.internal_conflicts],
            "emotion_triggers": [
                {"trigger": t.trigger, "reaction": t.reaction, "escalation": t.escalation}
                for t in self.emotion_triggers],
            "blind_spots": list(self.blind_spots),
        }


# ---------------------------------------------------------------- causal rules

@dataclass(frozen=True)
class CausalRule:
    """Axes pattern -> derived disposition. The reason compositions feel
    organic instead of tag soup: dispositions are CONSEQUENCES, not inputs."""

    rule_id: str
    description: str
    condition: Callable[[Mapping[str, float]], bool]
    disposition: str
    behavioral_notes: str = ""


def _high(threshold: float = 0.65) -> Callable[[float], bool]:
    return lambda value: value >= threshold


def _low(threshold: float = 0.35) -> Callable[[float], bool]:
    return lambda value: value <= threshold


DEFAULT_RULES: tuple[CausalRule, ...] = (
    CausalRule(
        "micromanager",
        "高控制欲 + 强不确定性厌恶 + 低信任 → 事必躬亲",
        lambda a: a["control_need"] >= 0.65 and a["uncertainty_tolerance"] <= 0.35
        and a["trust_propensity"] <= 0.35,
        "事必躬亲：不放权，凡事要过自己的手，下属/家人的判断默认不可靠",
        "口头禅式复核；打断别人替对方说完；把'我来'挂嘴边",
    ),
    CausalRule(
        "expansionist",
        "高控制欲 + 高风险偏好 + 高自我效能 → 扩张与冒险",
        lambda a: a["control_need"] >= 0.65 and a["risk_appetite"] >= 0.65
        and a["self_efficacy"] >= 0.65,
        "扩张冒险：主动开辟新战场，赌注下在自己身上，输了怪环境不怪判断",
        "宣布式决策；把风险说成机会；对劝阻者不耐烦",
    ),
    CausalRule(
        "approach_avoidance",
        "高自主需求 + 高亲密需求 → 接近-逃避式关系张力",
        lambda a: a["autonomy_need"] >= 0.65 and a["intimacy_need"] >= 0.65,
        "接近-逃避：渴望深度关系又怕被吞没，靠近后主动制造距离",
        "热络后突然冷淡；用忙碌当挡箭牌；关系推进由对方买单",
    ),
    CausalRule(
        "gatekeeper",
        "高控制欲 + 低风险偏好 + 高地位敏感 → 守门人心态",
        lambda a: a["control_need"] >= 0.65 and a["risk_appetite"] <= 0.35
        and a["status_sensitivity"] >= 0.65,
        "守门人：用规则和门槛替自己说不，向上谄媚向下压制的倾向",
        "引用规定；先问资格再听内容；对越级者变脸",
    ),
    CausalRule(
        "savior_burnout",
        "高共情反应 + 高自我效能 + 低信任 → 救世主式包办",
        lambda a: a["empathy_reactivity"] >= 0.5 and a["self_efficacy"] >= 0.65
        and a["trust_propensity"] <= 0.35,
        "救世主包办：见弱者立刻接管，别人的问题变成自己的战场，累了又怨没人领情",
        "嘴狠手软；骂完照给方案；把'我要不管你就完了'说出口",
    ),
    CausalRule(
        "brittle_performer",
        "高地位敏感 + 低不确定性容忍 → 面子脆化",
        lambda a: a["status_sensitivity"] >= 0.65 and a["uncertainty_tolerance"] <= 0.35,
        "面子脆化：公开场合被质疑即触发防御，宁可硬撑也不当场认错",
        "被戳穿时提高音量；转移话题到对方资格；事后私下改口",
    ),
)


# ---------------------------------------------------------------- composition

def compose_genomes(
    composite_id: str,
    parts: Sequence[tuple[CognitiveGenome, Mapping[str, float] | None]],
    *,
    rules: Sequence[CausalRule] = DEFAULT_RULES,
    max_models: int = 7,
    max_heuristics: int = 10,
) -> dict[str, Any]:
    """Blend genomes under causal constraints.

    ``parts`` is a sequence of (genome, axis_weights) where axis_weights maps
    axis name -> weight of THIS genome for that axis (None = equal weight).
    Returns a composite dict: blended axes, derived dispositions (from rules),
    inherited + emergent conflicts, merged models/heuristics/triggers with
    provenance, union blind spots, renormalized values, and a provenance block.
    """
    if not parts:
        raise GenomeError("composition needs at least one genome")

    blended: dict[str, float] = {}
    emergent_conflicts: list[InternalConflict] = []
    for axis in TRAIT_AXES:
        weighted: list[tuple[float, float]] = []  # (score, weight)
        for genome, weights in parts:
            weight = 1.0 if weights is None else float(weights.get(axis, 1.0))
            weighted.append((genome.axes[axis].score, weight))
        total_weight = sum(w for _, w in weighted) or 1.0
        blended[axis] = sum(s * w for s, w in weighted) / total_weight
        scores = [s for s, _ in weighted]
        spread = max(scores) - min(scores)
        if len(parts) > 1 and spread >= CONFLICT_THRESHOLD:
            lo = min(parts, key=lambda p: p[0].axes[axis].score)[0]
            hi = max(parts, key=lambda p: p[0].axes[axis].score)[0]
            emergent_conflicts.append(InternalConflict(
                force_a=f"{axis}高位（承自{hi.genome_id}: {hi.axes[axis].evidence[:40]}）",
                force_b=f"{axis}低位（承自{lo.genome_id}: {lo.axes[axis].evidence[:40]}）",
                behavioral_signature=f"该维度情境摇摆：压力下先走高位反应，事后又滑回低位（{axis}谱系冲突）",
            ))

    dispositions = [
        {"rule_id": rule.rule_id, "disposition": rule.disposition,
         "behavioral_notes": rule.behavioral_notes, "why": rule.description}
        for rule in rules if rule.condition(blended)
    ]

    models: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for genome, _ in parts:
        for model in genome.core_models:
            if model.rule in seen_rules:
                continue
            seen_rules.add(model.rule)
            models.append({"name": model.name, "rule": model.rule,
                           "evidence": list(model.evidence), "from": genome.genome_id})
    heuristics: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for genome, _ in parts:
        for heuristic in genome.heuristics:
            key = heuristic.condition + heuristic.action
            if key in seen_conditions:
                continue
            seen_conditions.add(key)
            heuristics.append({"if": heuristic.condition, "then": heuristic.action,
                               "evidence": heuristic.evidence, "from": genome.genome_id})

    values: dict[str, float] = {}
    for genome, _ in parts:
        for name, weight in genome.value_weights.items():
            values[name] = values.get(name, 0.0) + weight / len(parts)
    total = sum(values.values()) or 1.0
    values = {k: round(v / total, 4) for k, v in values.items()}

    inherited_conflicts = [
        {"force_a": c.force_a, "force_b": c.force_b,
         "behavioral_signature": c.behavioral_signature, "from": genome.genome_id}
        for genome, _ in parts for c in genome.internal_conflicts
    ]
    triggers = [
        {"trigger": t.trigger, "reaction": t.reaction, "escalation": t.escalation,
         "from": genome.genome_id}
        for genome, _ in parts for t in genome.emotion_triggers
    ]
    blind = sorted({spot for genome, _ in parts for spot in genome.blind_spots})

    real_sources = sorted({g.source or g.genome_id for g, _ in parts if g.source_real_person})
    return {
        "composite_id": composite_id,
        "trait_axes": {k: round(v, 3) for k, v in blended.items()},
        "derived_dispositions": dispositions,
        "core_models": models[:max_models],
        "heuristics": heuristics[:max_heuristics],
        "value_weights": values,
        "internal_conflicts": inherited_conflicts + [
            {"force_a": c.force_a, "force_b": c.force_b,
             "behavioral_signature": c.behavioral_signature, "from": "emergent"}
            for c in emergent_conflicts],
        "emotion_triggers": triggers,
        "blind_spots": blind,
        "provenance": {
            "parts": [g.genome_id for g, _ in parts],
            "real_person_sources": real_sources,
            "fictional_composite": len(parts) > 1 or not real_sources,
            "required_disclosure": (
                "AI生成的虚构合成角色，其认知特征拼合自多个真实人物的公开素材，不代表其中任何一人。"
                if len(real_sources) > 1 else ""),
        },
    }


def load_genome(path: Path, **kwargs: Any) -> CognitiveGenome:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return CognitiveGenome.from_dict(data, **kwargs)


# ---------------------------------------------------------------- persona compile

def compile_persona(
    composite: Mapping[str, Any],
    *,
    persona_id: str,
    name: str,
    shell: Mapping[str, str] | None = None,
    voice: Mapping[str, str] | None = None,
) -> "Persona":
    """Compile a composed genome into a runtime Persona.

    The genome supplies cognition (lens/values/heuristics), drama (conflicts,
    triggers, blind spots) and dispositions. ``shell`` supplies the scene
    shell (role/background/age...) and ``voice`` the speech layer — both are
    scenario-specific and deliberately NOT part of the genome: cognition
    transfers between scenarios, a job title does not.
    """
    from .persona import Persona

    models = composite.get("core_models", [])
    heuristics = composite.get("heuristics", [])
    dispositions = composite.get("derived_dispositions", [])
    conflicts = composite.get("internal_conflicts", [])
    triggers = composite.get("emotion_triggers", [])
    blind = composite.get("blind_spots", [])
    values = composite.get("value_weights", {})

    lens = "；".join(f"{m['name']}：{m['rule']}" for m in models)
    if dispositions:
        lens += "。行为定势：" + "；".join(d["disposition"] for d in dispositions)
    prefs = "；".join(f"若{h['if']}，则{h['then']}" for h in heuristics)
    value_line = "、".join(
        f"{name_}({weight:.0%})" for name_, weight in
        sorted(values.items(), key=lambda kv: -kv[1])
    )
    contradiction = "；".join(
        f"{c['force_a']}与{c['force_b']}并存——{c['behavioral_signature']}"
        for c in conflicts[:4]
    )
    pressure = "；".join(
        f"遇到{t['trigger']}：先{t['reaction']}，压不住则{t['escalation']}"
        for t in triggers if t.get("escalation")
    )
    blind_line = "；".join(blind[:6])

    data: dict[str, Any] = {
        "id": persona_id,
        "name": name,
        "cognition_lens": lens,
        "preferences": prefs,
        "values": value_line,
        "contradiction": contradiction,
        "feared_truth": blind_line,
        "voice": dict(voice or {}),
        "extensions": {
            "genome": dict(composite),
            **({"required_disclosure": composite["provenance"]["required_disclosure"]}
               if composite.get("provenance", {}).get("required_disclosure") else {}),
        },
    }
    if pressure and "pressure_change" not in data["voice"]:
        data["voice"]["pressure_change"] = pressure
    for key, value in (shell or {}).items():
        if key not in {"id", "name", "voice", "extensions"}:
            data[key] = value
    return Persona.from_dict(data)
