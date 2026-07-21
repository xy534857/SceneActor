"""Cognitive genome: validation, causal rules, and composition semantics."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sceneactor.genome import (
    AxisScore,
    CognitiveGenome,
    GenomeError,
    Heuristic,
    InternalConflict,
    EmotionTrigger,
    MentalModel,
    compose_genomes,
)


def _genome(genome_id="g1", *, axes=None, real=False):
    base = {
        "control_need": 0.9, "uncertainty_tolerance": 0.2, "trust_propensity": 0.3,
        "risk_appetite": 0.2, "self_efficacy": 0.9, "autonomy_need": 0.8,
        "intimacy_need": 0.5, "status_sensitivity": 0.7, "empathy_reactivity": 0.6,
    }
    base.update(axes or {})
    return CognitiveGenome(
        genome_id=genome_id,
        source=f"{genome_id}-pack",
        axes={k: AxisScore(v, f"{k}证据") for k, v in base.items()},
        core_models=tuple(MentalModel(f"模型{i}", f"规则{i}", (f"证据{i}",)) for i in range(3)),
        heuristics=tuple(Heuristic(f"如果{i}", f"那么{i}") for i in range(5)),
        value_weights={"功利": 0.5, "面子": 0.5},
        internal_conflicts=(
            InternalConflict("力A", "力B", "外显1"),
            InternalConflict("力C", "力D", "外显2"),
        ),
        emotion_triggers=(EmotionTrigger("触发", "反应", "升级"),),
        blind_spots=("盲区1",),
        source_real_person=real,
    )


def test_genome_validates_counts():
    with pytest.raises(GenomeError, match="3-7 core models"):
        CognitiveGenome(
            genome_id="bad", source="", axes=_genome().axes,
            core_models=(MentalModel("一", "1"),),
            heuristics=_genome().heuristics,
            value_weights={"x": 1.0},
            internal_conflicts=_genome().internal_conflicts,
            emotion_triggers=_genome().emotion_triggers,
            blind_spots=("b",),
        )


def test_value_weights_must_normalize():
    with pytest.raises(GenomeError, match="value weights sum"):
        CognitiveGenome(
            genome_id="bad", source="", axes=_genome().axes,
            core_models=_genome().core_models,
            heuristics=_genome().heuristics,
            value_weights={"x": 0.2},
            internal_conflicts=_genome().internal_conflicts,
            emotion_triggers=_genome().emotion_triggers,
            blind_spots=("b",),
        )


def test_causal_rules_fire_from_axes():
    solo = compose_genomes("solo", [(_genome(), None)])
    rule_ids = {d["rule_id"] for d in solo["derived_dispositions"]}
    # control 0.9 + uncertainty 0.2 + trust 0.3 -> micromanager
    assert "micromanager" in rule_ids
    # empathy 0.6 + self_efficacy 0.9 + trust 0.3 -> savior_burnout
    assert "savior_burnout" in rule_ids
    # status 0.7 + uncertainty 0.2 -> brittle_performer
    assert "brittle_performer" in rule_ids


def test_composition_changes_dispositions_not_just_unions():
    trusting = _genome("g2", axes={"trust_propensity": 0.8})
    blend = compose_genomes("mix", [(_genome(), None), (trusting, None)])
    rule_ids = {d["rule_id"] for d in blend["derived_dispositions"]}
    # blended trust 0.55 breaks micromanager's low-trust condition
    assert "micromanager" not in rule_ids


def test_large_axis_spread_becomes_emergent_conflict():
    bold = _genome("g3", axes={"risk_appetite": 0.9})
    blend = compose_genomes("mix", [(_genome(), None), (bold, None)])
    emergent = [c for c in blend["internal_conflicts"] if c["from"] == "emergent"]
    assert any("risk_appetite" in c["force_a"] for c in emergent)


def test_multi_real_source_requires_disclosure():
    one = _genome("r1", real=True)
    two = _genome("r2", real=True)
    blend = compose_genomes("mix", [(one, None), (two, None)])
    assert blend["provenance"]["fictional_composite"] is True
    assert blend["provenance"]["required_disclosure"]


def test_models_and_heuristics_deduplicate_and_carry_provenance():
    a, b = _genome("a"), _genome("b")  # identical rules -> dedupe
    blend = compose_genomes("mix", [(a, None), (b, None)])
    assert len(blend["core_models"]) == 3
    assert all(m["from"] == "a" for m in blend["core_models"])


def test_axis_weights_bias_blend():
    low = _genome("low", axes={"self_efficacy": 0.1})
    blend = compose_genomes("mix", [(_genome(), {"self_efficacy": 3.0}), (low, None)])
    assert blend["trait_axes"]["self_efficacy"] > 0.6


def test_compile_persona_from_composite():
    from sceneactor.genome import compile_persona
    blend = compose_genomes("mix", [(_genome(real=True), None)])
    persona = compile_persona(
        blend, persona_id="composite-x", name="合成角色X",
        shell={"role": "测试角色", "age": "四十岁"},
        voice={"turn_shape": "短句"},
    )
    assert persona.id == "composite-x"
    assert "模型0" in persona.cognition_lens
    assert "若如果0" in persona.preferences
    assert persona.voice.pressure_change  # triggers -> escalation ladder
    assert persona.extensions["genome"]["composite_id"] == "mix"


def test_compile_persona_carries_disclosure_for_multi_real():
    from sceneactor.genome import compile_persona
    blend = compose_genomes("mix", [(_genome("r1", real=True), None),
                                    (_genome("r2", real=True), None)])
    persona = compile_persona(blend, persona_id="c", name="c",
                              shell={"role": "x"})
    assert "虚构合成" in persona.extensions["required_disclosure"]


def test_rules_from_specs_parse_and_fire():
    from sceneactor.genome import rules_from_specs, DEFAULT_RULES, TRAIT_AXES
    spec = {
        "rule_id": "performative_dominance",
        "description": "高地位敏感+高自我效能+低共情 → 表演式支配",
        "when": {"status_sensitivity": ">=0.7", "self_efficacy": ">=0.7",
                 "empathy_reactivity": "<=0.4"},
        "disposition": "表演式支配",
    }
    rules = rules_from_specs([spec])
    assert len(rules) == len(DEFAULT_RULES) + 1
    axes = {a: 0.5 for a in TRAIT_AXES}
    axes.update(status_sensitivity=0.9, self_efficacy=0.9, empathy_reactivity=0.2)
    assert rules[-1].condition(axes)
    axes["empathy_reactivity"] = 0.8
    assert not rules[-1].condition(axes)


def test_rules_from_specs_override_by_id():
    from sceneactor.genome import rules_from_specs, DEFAULT_RULES
    spec = {"rule_id": "micromanager", "description": "重定义",
            "when": {"control_need": ">=0.9"}, "disposition": "新定势"}
    rules = rules_from_specs([spec])
    assert len(rules) == len(DEFAULT_RULES)
    assert [r for r in rules if r.rule_id == "micromanager"][0].disposition == "新定势"


def test_rules_from_specs_reject_bad_axis_or_expr():
    from sceneactor.genome import rules_from_specs
    with pytest.raises(GenomeError, match="unknown axis"):
        rules_from_specs([{"rule_id": "x", "when": {"nope": ">=0.5"}, "disposition": "d"}])
    with pytest.raises(GenomeError, match="bad expr"):
        rules_from_specs([{"rule_id": "x", "when": {"control_need": ">0.5"}, "disposition": "d"}])
