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
        # ---- language surface (AI-flavor tier 1: how sentences are built) ----
        "idiomatic_speech": "Every human line is idiomatic spoken Chinese a person could say aloud under this pressure: no calques, no essay connectives (但是/因此/然而 as clause glue), no dangling objects, no un-Chinese verb frames. One violating line fails the item.",
        "no_written_aphorism": "No speaker delivers a polished written maxim, balanced antithesis, or closing epigram as live speech. A machine-register actor's contract language does not count.",
        "no_mirror_symmetry": "No 我一句你一句 mirrored sentence frames: speakers do not answer a structure with the same structure (X的是你/X的是我, 你数你的/我数我的), and no snap-back formula is echoed more than once.",
        "no_enumeration_reflex": "Human speakers do not organize live speech into numbered lists or first/second/third scaffolds unless the voice contract prescribes it — and then at most once per scene.",
        "colloquial_particles": "Lines carry the small change of real Mandarin speech where pressure warrants it — 语气词, elision, incomplete predicates — instead of every sentence arriving fully inflated and grammatically complete.",
        # ---- turn mechanics (AI-flavor tier 2: how turns behave) ----
        "turn_length_variety": "Turn lengths follow the beats instead of one uniform shape: no speaker delivers all turns at the same length and structure. A deliberately laconic character whose voice contract prescribes near-silence satisfies this item through varied ACTIONS around the short lines.",
        "no_template_turns": "No speaker repeats the same internal turn structure (same opener + same development + same closer) in two or more turns.",
        "one_job_per_turn": "No single turn stacks rebuttal + case-making + verdict (or classify + read + confirm for procedural roles). Each turn does one job and leaves the rest unsaid.",
        "no_explanatory_tail": "Turns stop when the social move lands: no line continues into an explanatory tail (mechanism, consequence, scope, consent) that the recipient never asked for. De-completion test: cutting the tail should break nothing.",
        "leaves_hooks_open": "Speakers leave unequal knowledge and unanswered pressure on the table; nobody wraps each exchange into a closed, fully-resolved package before yielding the floor.",
        # ---- interaction (AI-flavor tier 3: whether anyone is listening) ----
        "listens_and_reacts": "At least one turn demonstrably picks up a specific word, number, or object from the opponent's PREVIOUS turn and acts on it (steal, mock, deny, exploit). Parallel monologues fail this item.",
        "answers_strongest_point": "Nobody consistently skips the opponent's strongest last point for a prepared line; at least once the hardest incoming hit is engaged rather than sidestepped.",
        "no_restating_visible": "No line re-narrates what both parties can already see (repeating the shared scene back, reporting the opponent's action to the opponent). Shared-context subtraction holds.",
        "concrete_objects": "The argument lands on concrete nameable objects or specifics from this scene, not restated abstract theses; a reader could name what each exchange is about.",
        "escalation_moves": "Across the scene the exchange changes tactic, angle, or referent at least once; re-performing the previous structure louder is not escalation.",
        # ---- character (AI-flavor tier 4: who is talking) ----
        "distinct_voices": "Speakers are distinguishable with names hidden: swapping two adjacent turns between speakers would be noticeable. Shared tics or converging registers fail this item.",
        "tic_budget": "No recognizable signature tic appears twice in one turn, and no tic is machine-gunned across consecutive turns of the same speaker.",
        "flaws_cost_something": "Where a voice contract prescribes a failure mode (restarts, miscounts, losing the thread), its traces COST the speaker something — a beat lost, an opening handed over — rather than resolving into a polished rhetorical device or self-aware joke. A behavior the contract frames as SIGNATURE TEXTURE (e.g. numbers inflating as boast) is voice fidelity, not an uncosted flaw; judge only traces the contract itself frames as failure.",
        "pressure_changes_speech": "Speech observably changes under pressure per the voice contract (shorter, repeated, derailed, hand stops) at least once; characters who sound identical in calm and under fire fail this item.",
        "no_authorial_verdict": "No speaker receives an unanswered closing verdict, moral of the story, or audience address that reads as the author's point; the scene does not crown a winner in its final beat unless a neutral third party owns the close.",
        "persona_fidelity": "When the packet supplies character_cards (want/need/lie/flaw/arc), every speaker's choices remain inside their card: a character whose flaw is never voicing need does not deliver declarations or defiance; a character whose want is pinned to an object cannot ignore that object when it activates; suppressed longing may LEAK (a hurried hand, a held gaze, a half-beat stop) but never convert into articulate self-possession. A performance that makes the character more clear-eyed, assertive, or resolved than the card allows fails this item even if the resulting drama is better.",
        "wound_stays_open": "The character's stated unresolved wound or wait stays unresolved and active: the scene may show hope rising and falling, but no speaker walks away cured, vindicated, or done waiting unless the script's arc says so.",
        # ---- production (craft hygiene) ----
        "no_planning_leak": "No line exposes planning-layer vocabulary (state codes spoken by humans, field-order reports from non-machine roles, response-hook talk) that belongs to the pipeline, not the play.",
        "silence_has_content": "Where a speaker stays silent or near-silent, the silence carries a visible choice (an action, an avoidance, a stopped gesture) rather than an empty placeholder note.",
        "consistent_stage_facts": "No stage/prop/timeline contradiction inside the transcript (an object in two states, an action happening twice, a referenced event that never occurred).",
        "core_emotion_delivered": "The scene's stated core emotion is realized in at least one specific moment of the transcript, not merely implied by the setup.",
    }

    CHECKLIST_TIERS = {
        "language_surface": ("idiomatic_speech", "no_written_aphorism", "no_mirror_symmetry", "no_enumeration_reflex", "colloquial_particles"),
        "turn_mechanics": ("turn_length_variety", "no_template_turns", "one_job_per_turn", "no_explanatory_tail", "leaves_hooks_open"),
        "interaction": ("listens_and_reacts", "answers_strongest_point", "no_restating_visible", "concrete_objects", "escalation_moves"),
        "character": ("distinct_voices", "tic_budget", "flaws_cost_something", "pressure_changes_speech", "no_authorial_verdict", "persona_fidelity", "wound_stays_open"),
        "production": ("no_planning_leak", "silence_has_content", "consistent_stage_facts", "core_emotion_delivered"),
    }

    CRITICAL_ITEMS = (
        "no_written_aphorism",
        "no_mirror_symmetry",
        "no_template_turns",
        "listens_and_reacts",
        "distinct_voices",
        "silence_has_content",
        "persona_fidelity",
    )

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
                tier_bits = {
                    tier: {
                        "passed": sum(normalized[k]["pass"] for k in keys),
                        "total": len(keys),
                        "failed": [k for k in keys if not normalized[k]["pass"]],
                    }
                    for tier, keys in self.CHECKLIST_TIERS.items()
                }
                worst = [str(k) for k in data.get("worst_failures", []) if k in normalized][:3]
                critical_failed = [k for k in self.CRITICAL_ITEMS if not normalized[k]["pass"]]
                gate = (
                    passed_bits >= total - 5
                    and all(t["passed"] >= t["total"] - 2 for t in tier_bits.values())
                    and not critical_failed
                )
                return {
                    "kind": "binary_checklist",
                    "items": normalized,
                    "bits_passed": passed_bits,
                    "bits_total": total,
                    "tiers": tier_bits,
                    "critical_failed": critical_failed,
                    # Compatibility scalar: map bit ratio onto the legacy 1-5 scale.
                    "score": 1 + round(4 * passed_bits / total),
                    "pass": gate,
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
