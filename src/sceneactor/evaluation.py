"""Full performance, blind review, role-swap, and same-stimulus evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .benchmark import BehaviorCase, same_stimulus_frames
from .cognition import JsonCognitionPort
from .contracts import PerformanceDraft, PublicPerformanceIntent, ResolvedOutcome
from .performance import JsonPerformancePort
from .review import BlindReviewer, JsonCounterfactualReviewPort


@dataclass(frozen=True)
class EvaluatedPerformance:
    case_id: str
    category: str
    character_card: Mapping[str, Any]
    public_observation: Mapping[str, Any]
    appraisal: Mapping[str, Any]
    public_intent: Mapping[str, Any]
    performance: Mapping[str, Any]
    review: Mapping[str, Any]
    protocol_error: str = ""


class EvaluationBatchError(RuntimeError):
    """A generation batch contains protocol failures and cannot be reviewed."""


def assert_reviewable_evaluations(items: Sequence[Mapping[str, Any]]) -> None:
    failures = []
    for item in items:
        case_id = str(item.get("case_id", "unknown"))
        error = str(item.get("protocol_error", "")).strip()
        performance = item.get("performance")
        if error or not isinstance(performance, Mapping) or not performance:
            failures.append(f"{case_id}: {error or 'missing performance'}")
    if failures:
        raise EvaluationBatchError("review batch contains generation failures: " + "; ".join(failures))


def dialogue_review_passed(review: Mapping[str, Any]) -> bool:
    reviews = review.get("reviews", [])
    if not isinstance(reviews, list):
        return False
    for item in reviews:
        if not isinstance(item, Mapping) or item.get("lens") != "dialogue":
            continue
        return bool(item.get("available")) and bool(item.get("passed")) and float(item.get("score", 0)) >= 3
    return False


class FullBehaviorEvaluator:
    def __init__(
        self,
        cognition: JsonCognitionPort,
        performance: JsonPerformancePort,
        reviewer: BlindReviewer,
        counterfactual_reviewer: JsonCounterfactualReviewPort,
    ) -> None:
        self.cognition = cognition
        self.performance = performance
        self.reviewer = reviewer
        self.counterfactual_reviewer = counterfactual_reviewer

    def evaluate_case(self, case: BehaviorCase, anonymous_id: str) -> EvaluatedPerformance:
        frame = case.frame()
        character_card = _anonymous_card(case.persona, anonymous_id)
        try:
            appraisal, policy = self.cognition.decide(frame)
            intent = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
        except Exception as exc:
            return EvaluatedPerformance(
                case_id=case.id, category=case.category, character_card=character_card,
                public_observation=dict(case.observation), appraisal={}, public_intent={},
                performance={}, review={}, protocol_error=f"cognition: {exc}",
            )
        appraisal_data = {
            "subjective_observation": appraisal.subjective_observation,
            "emotion_changes": [asdict(item) for item in appraisal.changes],
            "grounded_refs": list(appraisal.grounded_refs),
        }
        try:
            outcome = ResolvedOutcome(
                status="succeeded",
                action_kind=intent.authorized_action.action_kind,
                observable_facts=(),
            )
            draft = self.performance.realize(intent, outcome, ())
            draft.validate(intent, outcome)
        except Exception as exc:
            return EvaluatedPerformance(
                case_id=case.id, category=case.category, character_card=character_card,
                public_observation=dict(case.observation), appraisal=appraisal_data,
                public_intent=intent.to_dict(), performance={}, review={},
                protocol_error=f"performance: {exc}",
            )
        performance = _performance_dict(draft)
        public_scene = {
            "category": case.category,
            "character_card": _anonymous_card(case.persona, anonymous_id),
            "observation": dict(case.observation),
        }
        review = self.reviewer.review(public_scene, (performance,))
        return EvaluatedPerformance(
            case_id=case.id,
            category=case.category,
            character_card=public_scene["character_card"],
            public_observation=dict(case.observation),
            appraisal={
                "subjective_observation": appraisal.subjective_observation,
                "emotion_changes": [asdict(item) for item in appraisal.changes],
                "grounded_refs": list(appraisal.grounded_refs),
            },
            public_intent=intent.to_dict(),
            performance=performance,
            review=review,
        )

    def role_swap(self, original: BehaviorCase, donor: BehaviorCase) -> Mapping[str, Any]:
        swapped = BehaviorCase(
            id=f"{original.id}__identity_from__{donor.id}",
            category="role_swap",
            persona=donor.persona,
            private_state=original.private_state,
            relationship=original.relationship,
            observation=original.observation,
            targets=original.targets,
            capabilities=original.capabilities,
            actions=original.actions,
        )
        left = self.evaluate_case(original, "actor-A")
        right = self.evaluate_case(swapped, "actor-B")
        packet = {
            "same_public_situation": dict(original.observation),
            "A": {"card": left.character_card, "performance": left.performance},
            "B": {"card": right.character_card, "performance": right.performance},
        }
        review = self.counterfactual_reviewer.review(packet) if not left.protocol_error and not right.protocol_error else {}
        return {
            "original_case": original.id,
            "donor_identity": donor.id,
            "A": asdict(left),
            "B": asdict(right),
            "counterfactual_review": dict(review),
        }

    def same_stimulus(
        self,
        *,
        stimulus: Mapping[str, Any],
        personas: Sequence[Mapping[str, Any]],
        target: str,
        private_state: Mapping[str, Any] | None = None,
        relationships: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> Mapping[str, Any]:
        frames = same_stimulus_frames(
            stimulus=stimulus,
            personas=personas,
            target=target,
            private_state=private_state,
            relationships=relationships,
        )
        outputs: list[dict[str, Any]] = []
        for index, frame in enumerate(frames):
            try:
                appraisal, policy = self.cognition.decide(frame)
                intent = PublicPerformanceIntent.from_policy(frame.actor_id, policy, frame)
                outcome = ResolvedOutcome("succeeded", intent.authorized_action.action_kind)
                draft = self.performance.realize(intent, outcome, ())
                draft.validate(intent, outcome)
                outputs.append(
                    {
                        "anonymous_actor": f"actor-{index + 1}",
                        "card": _anonymous_card(personas[index], f"actor-{index + 1}"),
                        "appraisal": appraisal.subjective_observation,
                        "public_intent": intent.to_dict(),
                        "performance": _performance_dict(draft),
                        "protocol_error": "",
                    }
                )
            except Exception as exc:
                outputs.append({"anonymous_actor": f"actor-{index + 1}", "protocol_error": str(exc)})
        packet = {"stimulus": dict(stimulus), "actors": outputs}
        valid = all(not item.get("protocol_error") for item in outputs)
        review = self.counterfactual_reviewer.review(packet) if valid else {}
        return {**packet, "counterfactual_review": dict(review)}


def _anonymous_card(persona: Mapping[str, Any], anonymous_id: str) -> dict[str, Any]:
    return {
        "anonymous_actor": anonymous_id,
        **{key: value for key, value in persona.items() if key not in {"id", "name"} and value},
    }


def _performance_dict(draft: PerformanceDraft) -> dict[str, Any]:
    return {
        "action": draft.action,
        "speech": draft.speech,
        "addressee": draft.addressee,
        "attention_target": draft.attention_target,
        "gaze": draft.gaze,
        "blocking": draft.blocking,
        "posture_change": draft.posture_change,
        "delivery": asdict(draft.delivery),
        "physical_residue": draft.physical_residue,
        "response_hook": draft.response_hook,
    }
