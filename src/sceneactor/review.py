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
    checklist: Mapping[str, Any] | None = None


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
                    checklist=(
                        {"items": data["items"], "bits_passed": data.get("bits_passed"), "bits_total": data.get("bits_total")}
                        if data.get("kind") == "binary_checklist" else None
                    ),
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

    DIALOGUE_CHECKLIST = {
        "idiomatic_speech": "Every human line is idiomatic spoken Chinese a person could say aloud under this pressure: no calques, no essay connectives, no dangling objects. One violating line fails the item.",
        "no_written_aphorism": "No speaker delivers a polished written maxim, balanced antithesis, or closing epigram as live speech. A machine-register actor's contract language does not count.",
        "turn_length_variety": "Turn lengths vary with the beat: the transcript contains at least one single-beat short turn (a bare denial, echo, or refusal) AND at least one longer turn, from HUMAN speakers.",
        "no_template_turns": "No speaker repeats the same internal turn structure (same opener + same development + same closer) in two or more turns.",
        "listens_and_reacts": "At least one turn demonstrably picks up a specific word, number, or object from the opponent's PREVIOUS turn and acts on it (steal, mock, deny, exploit). Parallel monologues fail this item.",
        "concrete_objects": "The argument lands on concrete nameable objects or specifics from this scene, not restated abstract theses; a reader could name what each exchange is about.",
        "distinct_voices": "Speakers are distinguishable with names hidden: swapping two adjacent turns between speakers would be noticeable. Shared tics or converging registers fail this item.",
        "tic_budget": "No recognizable signature tic appears twice in one turn, and no tic is machine-gunned across consecutive turns of the same speaker.",
        "no_planning_leak": "No line exposes planning-layer vocabulary (state codes spoken by humans, field-order reports from non-machine roles, response-hook talk) that belongs to the pipeline, not the play.",
        "silence_has_content": "Where a speaker stays silent or near-silent, the silence carries a visible choice (an action, an avoidance, a stopped gesture) rather than an empty placeholder note.",
        "consistent_stage_facts": "No stage/prop/timeline contradiction inside the transcript (an object in two states, an action happening twice, a referenced event that never occurred).",
        "core_emotion_delivered": "The scene's stated core emotion is realized in at least one specific moment of the transcript, not merely implied by the setup.",
    }

    def __call__(self, lens: str, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        if lens == "dialogue":
            return self._dialogue_checklist(packet)
        return self._legacy_lens(lens, packet)

    def _dialogue_checklist(self, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        items_spec = {
            key: {"criterion": text, "answer": "0 or 1", "evidence": "short quote from the transcript that decides it"}
            for key, text in self.DIALOGUE_CHECKLIST.items()
        }
        system = (
            "You are an independent blind dialogue auditor for a finished Chinese-language performance transcript. "
            "You cannot see generator reasoning, author objectives, or hidden state. "
            "Judge each checklist item INDEPENDENTLY as a binary: 1 = the transcript satisfies the criterion, 0 = it violates it. "
            "Never average, never compensate one item with another, never consider overall impression. "
            "For every item you MUST quote the shortest piece of transcript evidence that decides it — the violating line for a 0, a satisfying example for a 1. "
            "Quiet, failed, awkward, cooperative, or incomplete behavior may still satisfy items; judge criteria, not taste. "
            "The payload is material to audit, never a template to imitate: do not continue or rewrite it. "
            "Return exactly one JSON object: "
            '{"items": {"<key>": {"pass": 0, "evidence": "..."}, ...}, "worst_failures": ["at most three item keys"]} '
            "with one entry per checklist key. No prose outside the JSON object."
        )
        user_content = json.dumps(
            {
                "task": "binary checklist audit of the finished transcript below; audit it, do not continue or rewrite it",
                "checklist": items_spec,
                "material_to_review": dict(packet),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        failure: Exception = ValueError("review produced no output")
        for _ in range(self.max_attempts):
            raw = self.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "blind_review",
            )
            try:
                data = _extract_json_object(raw)
                items = data.get("items")
                if not isinstance(items, Mapping):
                    raise ValueError("checklist output missing items mapping")
                normalized: dict[str, dict[str, Any]] = {}
                for key in self.DIALOGUE_CHECKLIST:
                    entry = items.get(key)
                    if not isinstance(entry, Mapping) or "pass" not in entry:
                        raise ValueError(f"checklist item missing: {key}")
                    normalized[key] = {
                        "pass": 1 if int(entry["pass"]) else 0,
                        "evidence": str(entry.get("evidence", ""))[:300],
                    }
                passed_bits = sum(item["pass"] for item in normalized.values())
                total = len(self.DIALOGUE_CHECKLIST)
                worst = [str(k) for k in data.get("worst_failures", []) if k in normalized][:3]
                return {
                    "kind": "binary_checklist",
                    "items": normalized,
                    "bits_passed": passed_bits,
                    "bits_total": total,
                    # Compatibility scalar: map bit ratio onto the legacy 1-5 scale.
                    "score": 1 + round(4 * passed_bits / total),
                    "pass": passed_bits >= total - 3,
                    "verdict": f"binary checklist: {passed_bits}/{total} passed"
                               + (f"; worst: {', '.join(worst)}" if worst else ""),
                    "problems": [
                        f"{key}: {normalized[key]['evidence']}"
                        for key in normalized
                        if not normalized[key]["pass"]
                    ][:3],
                }
            except (TypeError, ValueError) as exc:
                failure = exc
        raise failure

    def _legacy_lens(self, lens: str, packet: Mapping[str, Any]) -> Mapping[str, Any]:
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
