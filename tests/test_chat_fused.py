"""Fused chat port: one call per turn, staged performance, safe degradation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from sceneactor.chat_fused import FusedChatPort
from sceneactor.contracts import (
    DecisionContract,
    DecisionFrame,
    ObservationView,
    PerformanceDraft,
    PublicPerformanceIntent,
    ResolvedOutcome,
)


def _frame() -> DecisionFrame:
    return DecisionFrame(
        scene_id="s",
        turn_id="t1",
        actor_id="npc",
        private_state={},
        relationships={},
        emotions={},
        observation=ObservationView(
            facts={"O.current": "对面的人刚说：你好"},
            affordances={},
            available_targets=("visitor",),
            capabilities=("speak", "wait"),
        ),
        recent_history=(),
        continuity={},
        decision_contract=DecisionContract(
            allowed_actions=("speak", "wait"),
            required_arguments={},
            argument_choices={},
        ),
        identity_evidence={"role": "测试角色"},
    )


def _fused_response(with_performance=True, action_text="抬眼看向对面", corrupt=False):
    body = {
        "appraisal": {
            "subjective_observation": "对面先开了口",
            "emotion_changes": [],
            "grounded_refs": ["O.current"],
        },
        "policy": {
            "intent_mode": "rewrite",
            "rewrite_trigger": "对方的问候",
            "attention": ["O.current"],
            "interpretation": "寒暄开场",
            "current_intent": "接住问候",
            "chosen_strategy": "直接回应",
            "expected_response": "对方继续说",
            "interaction_move": "acknowledge",
            "delivery_mode": "warm",
            "public_move": "information",
            "disposition": "continue",
            "action_request": {
                "action_kind": "speak",
                "target": "visitor",
                "arguments": {},
                "required_capabilities": ["speak"],
                "grounded_refs": ["O.current"],
            },
            "disclose": [
                {"kind": "fact", "text": "你好。", "evidence_refs": ["O.current"]}
            ],
            "withhold": [],
            "response_hook": "等对方接话",
            "relationship_transition": "keep",
        },
    }
    if with_performance:
        body["performance"] = "not-an-object" if corrupt else {
            "action": action_text,
            "attention_target": "visitor",
            "gaze": "直视",
            "blocking": "",
            "posture_change": "",
            "delivery": {"pace": "从容", "volume": "", "breath": "", "articulation": "", "pause": "", "vocal_target": "对面"},
            "physical_residue": "",
        }
    return json.dumps(body, ensure_ascii=False)


class CountingModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, messages, purpose):
        self.calls += 1
        self.purpose = purpose
        self.last_messages = messages
        return self.response


def _run_turn(model):
    port = FusedChatPort(model)
    frame = _frame()
    appraisal, policy = port.decide(frame)
    intent = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
    outcome = ResolvedOutcome(status="succeeded", action_kind="speak", observable_facts=("npc performed speak",))
    draft = port.realize(intent, outcome, ())
    return model, draft


def test_single_call_per_turn():
    model, draft = _run_turn(CountingModel(_fused_response()))
    assert model.calls == 1
    assert isinstance(draft, PerformanceDraft)
    assert draft.speech == "你好。"
    assert draft.action == "抬眼看向对面"
    assert draft.delivery.pace == "从容"


def test_prompt_declares_fused_contract():
    model = CountingModel(_fused_response())
    _run_turn(model)
    joined = json.dumps([m["content"] for m in model.last_messages], ensure_ascii=False)
    assert "FUSED MODE" in joined
    assert "performance" in joined


def test_missing_performance_degrades_not_second_call():
    model, draft = _run_turn(CountingModel(_fused_response(with_performance=False)))
    assert model.calls == 1
    assert draft.speech == "你好。"          # words survive
    assert draft.delivery.has_audible_direction()  # neutral audible fallback


def test_corrupt_performance_section_degrades():
    model, draft = _run_turn(CountingModel(_fused_response(corrupt=True)))
    assert model.calls == 1
    assert draft.speech == "你好。"


def test_speech_restating_action_is_dropped():
    model, draft = _run_turn(CountingModel(_fused_response(action_text="他说出你好。这句话")))
    assert draft.action == ""              # leaked action discarded
    assert draft.speech == "你好。"


def test_staged_performance_not_reused_across_turns():
    model = CountingModel(_fused_response())
    port = FusedChatPort(model)
    frame = _frame()
    _, policy = port.decide(frame)
    intent = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
    outcome = ResolvedOutcome(status="succeeded", action_kind="speak", observable_facts=("npc performed speak",))
    port.realize(intent, outcome, ())
    # second realize without a fresh decide: staged cache must be cleared
    draft2 = port.realize(intent, outcome, ())
    assert draft2.speech == "你好。"  # degrades to neutral, still valid
    assert draft2.gaze == ""          # no stale staging replayed
