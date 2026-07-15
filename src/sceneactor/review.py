"""Structural gates and injected semantic review boundary."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Sequence
import json

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
        def review_one(lens: str) -> BlindReview:
            try:
                data = self.complete(
                    lens,
                    {"public_scene": dict(public_scene), "clean_transcript": list(transcript)},
                )
                score = int(data.get("score", 0))
                return BlindReview(
                    lens=lens,
                    available=True,
                    passed=bool(data.get("pass")) and score >= 3,
                    score=max(1, min(5, score)),
                    verdict=str(data.get("verdict", "")),
                    problems=tuple(str(item) for item in data.get("problems", [])[:3]),
                )
            except (RuntimeError, TypeError, ValueError, KeyError):
                return BlindReview(lens, False, False, 0, "review unavailable")

        with ThreadPoolExecutor(max_workers=len(self.LENSES)) as pool:
            reviews = list(pool.map(review_one, self.LENSES))
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


class JsonBlindReviewPort:
    """Translate one clean public packet into one semantic review JSON object."""

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str]) -> None:
        self.complete = complete

    def __call__(self, lens: str, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        lens_focus = {
            "reader": "human believability, causal listening, subtext, boredom and repetition",
            "character": "distinct attention, pressure-revealed choice, voice and card fit without trait recitation",
            "dramaturgy": "beat change, tactic, physical situation and whether possibilities changed",
            "performance": "whether action, speech, gaze, voice and residue form one playable performance",
        }[lens]
        system = (
            f"You are an independent blind {lens} reviewer. Judge {lens_focus}. "
            "You cannot see generator reasoning, scores, author objectives, hidden state, or desired outcomes. "
            "Quiet, failed, awkward, cooperative, delayed, or incomplete behavior may be excellent. "
            "Do not demand loud conflict, a signature phrase, personality keywords, disclosure, or progress every turn. "
            "Judge what the character chooses under the supplied public situation. Return exactly JSON: "
            '{"pass":true,"score":1,"verdict":"brief evidence-based verdict","problems":["at most three"]}. '
            "Score 1-5; pass requires score >= 3."
        )
        raw = self.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False, sort_keys=True)},
            ],
            "blind_review",
        )
        return _extract_json_object(raw)


class JsonCounterfactualReviewPort:
    """Review paired outputs without token overlap or local semantic heuristics."""

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str]) -> None:
        self.complete = complete

    def review(self, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        system = (
            "You are a blind counterfactual character reviewer. The public situation is held constant and identity cards differ. "
            "Decide whether the performances differ in attention, protection, tactic, accepted cost, language organization, or failure mode, "
            "and whether each difference is grounded in its card rather than a catchphrase or profession noun. "
            "Do not use surface word overlap as the criterion. Return exactly JSON: "
            '{"pass":true,"score":1,"distinct_dimensions":["..."],"generic_overlap":"...","verdict":"..."}. '
            "Score 1-5; pass requires meaningful identity-shaped differences."
        )
        raw = self.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False, sort_keys=True)},
            ],
            "counterfactual_review",
        )
        return _extract_json_object(raw)


def _extract_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("review output did not contain a JSON object")
