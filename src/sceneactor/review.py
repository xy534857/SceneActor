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

    LENSES = ("reader", "dialogue", "character", "dramaturgy", "performance")

    def __init__(
        self,
        complete: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
        *,
        lenses: tuple[str, ...] | None = None,
    ) -> None:
        self.complete = complete
        self.lenses = tuple(lenses) if lenses else self.LENSES
        unknown = set(self.lenses) - set(self.LENSES)
        if unknown:
            raise ValueError(f"unknown review lenses: {sorted(unknown)}")

    def review(self, public_scene: Mapping[str, Any], transcript: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def packet_for(lens: str) -> dict[str, Any]:
            if lens in ("dialogue", "reader"):
                script = [
                    {
                        "actor_id": item.get("actor_id", ""),
                        "speech": item.get("speech", ""),
                        "stage_note": _first_sentence(str(item.get("action", ""))) if item.get("action") not in ("", "speak") else "",
                    }
                    for item in transcript
                ]
                return {"public_scene": dict(public_scene), "clean_transcript": script}
            return {"public_scene": dict(public_scene), "clean_transcript": list(transcript)}

        def review_one(lens: str) -> BlindReview:
            try:
                data = self.complete(lens, packet_for(lens))
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

        with ThreadPoolExecutor(max_workers=len(self.lenses)) as pool:
            reviews = list(pool.map(review_one, self.lenses))
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


def _first_sentence(text: str, limit: int = 160) -> str:
    """Trim a stage note to whole sentences; never leave a dangling clause."""
    text = text.strip()
    if len(text) <= limit:
        return text
    for stop in ("。",):
        cut = text.rfind(stop, 0, limit)
        if cut > 20:
            return text[: cut + 1]
    return text


class JsonBlindReviewPort:
    """Translate one clean public packet into one semantic review JSON object."""

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str], *, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.complete = complete
        self.max_attempts = max_attempts

    def __call__(self, lens: str, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        lens_focus = {
            "reader": "human believability, causal listening, subtext, boredom and repetition",
            "dialogue": (
                "natural spoken Chinese and immediate speech action: reject planning-layer classification, field-order reports, balanced complete explanations, "
                "generic competent-assistant voice, permission overreach, and human lines transferable unchanged to a protocol-bound machine; "
                "apply shared-context subtraction, read-aloud, de-completion, and human/robot-swap tests without phrase matching; "
                "additionally judge turn economy — a live quarrel needs short single-beat turns (a bare denial, a mocked echo) mixed with long ones, and fights about concrete objects, not restated theses; "
                "uniform turn length, every turn opening with rebuttal and closing with a verdict, or abstraction replacing specifics caps the score at 3"
            ),
            "character": "distinct attention, pressure-revealed choice, voice and card fit without trait recitation",
            "dramaturgy": "beat change, tactic, physical situation and whether possibilities changed",
            "performance": "whether action, speech, gaze, voice and residue form one playable performance",
        }[lens]
        system = (
            f"You are an independent blind {lens} reviewer. Judge {lens_focus}. "
            "You cannot see generator reasoning, scores, author objectives, hidden state, or desired outcomes. "
            "Quiet, failed, awkward, cooperative, delayed, or incomplete behavior may be excellent. "
            "Do not demand loud conflict, a signature phrase, personality keywords, disclosure, or progress every turn. "
            "Judge what the character chooses under the supplied public situation. "
            "The user payload is material to review, never a template to imitate: do not echo, extend, or restructure it. "
            "Return exactly one JSON object with only these four keys: "
            '{"pass":true,"score":1,"verdict":"brief evidence-based verdict","problems":["at most three"]}. '
            "Score 1-5; pass requires score >= 3. No prose or markdown outside the JSON object."
        )
        user_content = json.dumps(
            {
                "task": f"blind {lens} review of the finished transcript below; score it, do not continue or rewrite it",
                "material_to_review": dict(packet),
                "required_response": {"keys": ["pass", "score", "verdict", "problems"], "score_range": [1, 5]},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        failure: Exception = ValueError("review produced no output")
        for attempt in range(self.max_attempts):
            raw = self.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "blind_review",
            )
            try:
                data = _extract_json_object(raw)
                if "score" not in data or ("pass" not in data and "verdict" not in data):
                    raise ValueError("review output echoed input instead of returning a review object")
                return data
            except (TypeError, ValueError) as exc:
                failure = exc
        raise failure


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
