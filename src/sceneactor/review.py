"""Structural gates and injected semantic review boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .contracts import PerformanceBeat


@dataclass(frozen=True)
class StructuralIssue:
    code: str
    message: str
    severity: str = "error"


class StructuralValidator:
    """Checks contracts and authority, never literary quality."""

    def validate_beat(self, beat: PerformanceBeat, *, known_actor: str, known_hooks: set[str]) -> tuple[StructuralIssue, ...]:
        issues: list[StructuralIssue] = []
        if beat.actor_id != known_actor:
            issues.append(StructuralIssue("actor_ownership", "beat actor is not the current actor"))
        if not beat.beat_id or not beat.source_event_ids:
            issues.append(StructuralIssue("event_ownership", "committed beat requires source events"))
        if beat.response_hook and beat.response_hook not in known_hooks:
            issues.append(StructuralIssue("unknown_hook", "beat introduces an unregistered response hook"))
        return tuple(issues)


@dataclass(frozen=True)
class BlindReview:
    lens: str
    available: bool
    passed: bool
    score: int
    verdict: str
    problems: tuple[str, ...] = ()


class BlindReviewer:
    """Semantic review is injected and sees only clean public transcript."""

    LENSES = ("reader", "character", "dramaturgy", "performance")

    def __init__(self, complete: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]) -> None:
        self.complete = complete

    def review(self, public_scene: Mapping[str, Any], transcript: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        reviews: list[BlindReview] = []
        for lens in self.LENSES:
            try:
                data = self.complete(
                    lens,
                    {"public_scene": dict(public_scene), "clean_transcript": list(transcript)},
                )
                score = int(data.get("score", 0))
                reviews.append(
                    BlindReview(
                        lens=lens,
                        available=True,
                        passed=bool(data.get("pass")) and score >= 3,
                        score=max(1, min(5, score)),
                        verdict=str(data.get("verdict", "")),
                        problems=tuple(str(item) for item in data.get("problems", [])[:3]),
                    )
                )
            except (RuntimeError, TypeError, ValueError, KeyError):
                reviews.append(BlindReview(lens, False, False, 0, "review unavailable"))
        available = [item for item in reviews if item.available]
        scores = sorted(item.score for item in available)
        median = scores[len(scores) // 2] if scores else 0
        votes = sum(item.passed for item in available)
        return {
            "available": bool(available),
            "pass": bool(available) and votes >= len(available) // 2 + 1 and median >= 3,
            "score": median,
            "reviews": [item.__dict__ for item in reviews],
            "aggregation": "majority_median_v1",
        }
