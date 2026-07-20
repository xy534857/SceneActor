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
                        {"items": data["items"], "bits_passed": data.get("bits_passed"), "bits_total": data.get("bits_total"), "failed": data.get("failed", [])}
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

    # 统一评价体系：十道生死题，每题 0/1 并引原文定案，全过才放行。
    # 无层级、无否决子集、无总分阈值——任何一题不过，人物就不是活的。
    # 判据融合本项目与《无主之名》表达控制文档的审稿规则（负面句式清单只
    # 存在于审计侧，绝不进入生成 prompt）。
    DIALOGUE_CHECKLIST = {
        "read_aloud": (
            "Cover all stage notes and read ONLY the spoken lines aloud, in order. Every human line must be something "
            "a live person says TO the person in front of them, under this pressure, in this moment. Any of the following "
            "spoken by a human fails the item on sight: a polished maxim or epigram; a negate-then-flip antithesis "
            "(不是X，是Y / 我不管X，我只管Y / X的是你，X的是我); semicolon-style parallel conclusions; a fair summary of both "
            "positions; a subject-dropped process label (人先核/状态未确认). One line is enough to fail. Quote it."
        ),
        "machine_swap": (
            "Hand each human speaker's lines verbatim to a protocol-bound machine. If any human's dialogue could be "
            "delivered by the machine without seeming wrong, that human voice fails. Machine-register characters are "
            "exempt and judged only against their own contract."
        ),
        "local_sufficiency": (
            "Dialogue is locally sufficient, never globally complete: no line re-narrates what both parties already see, "
            "no one reads out rules-plus-consequences in one breath, and every omission is licensed by presence, shared "
            "history, or avoidance — not by the author having read the scene card. Receipt lines ('收到', '好的，我记下了') "
            "are judged BY THE CARD, not globally: a clerk, a dutiful student, or a protocol role may acknowledge; a "
            "character whose voice contract says information only surfaces transformed (converted, challenged, seized on) "
            "fails this item when it hands back a bare receipt."
        ),
        "real_listening": (
            "Each turn responds first to the trouble the previous second actually caused — a specific word, number, or "
            "object from the other speaker — before anything else. Interruption, restart, mishearing, and refusal to "
            "engage all count as responses; parallel monologues and prepared lines that skip the opponent's strongest "
            "point do not."
        ),
        "allowed_inefficiency": (
            "The characters are permitted to be ineffective, and at least once they are: someone answers beside the "
            "point, misses what mattered, says half a sentence, repeats a primitive word under pressure, or says a "
            "useless thing. A transcript where every speaker always delivers the optimal proof of their persona reads "
            "as the author performing the character sheet — that is THE disease, and it fails this item."
        ),
        "one_move_per_turn": (
            "One mouth-opening does one thing (ask / refuse / deflect / demand / concede). No turn explains + reassures "
            "+ arranges + advances in the same breath; no turn stacks rebuttal + case + verdict. Cutting a turn's "
            "explanatory tail must break nothing."
        ),
        "persona_fidelity": (
            "Every choice stays inside the character card (want/lie/flaw/arc) and voice contract: a character whose flaw "
            "is never voicing need does not declare or defy; one whose want is pinned to an object cannot ignore that "
            "object when it activates; suppressed longing may LEAK (a hurried hand, a half-beat stop) but never converts "
            "into articulate self-possession; wounds stay open unless the arc says otherwise. Making the character "
            "clearer-eyed, more assertive, or more resolved than the card allows fails this item even when the drama is better."
        ),
        "pressure_and_wreckage": (
            "Strong reactions are loaded before they fire and leave wreckage after: pressure accumulates visibly before "
            "any outburst or hard refusal, the breaking point cracks THIS character's specific way of holding themselves "
            "together (not generic shouting), and afterwards breath, gaze, distance, or the task at hand is changed. "
            "A speaker who snaps back to fluent composure one line later fails this item."
        ),
        "distinct_defense": (
            "With names covered, speakers are still identifiable by three things: what each notices first, how each "
            "protects themselves, and which language ability breaks first under pressure — not by catchphrases, dialect, "
            "or sentence-length quotas. If two speakers converge into one register, or a tic is the only difference, fail."
        ),
        "scene_stays_physical": (
            "The scene stays on concrete objects and unbroken stage facts: the argument lands on nameable things from "
            "this scene; whatever the characters were doing when the scene opened has a fate by the end; no prop is in "
            "two states, no action happens twice, no referenced event never occurred; and no speaker walks away with an "
            "unanswered verdict, a moral, or an audience address that reads as the author's point."
        ),
        "corpus_register": (
            "Only judged when a speaker's card carries a speech_corpus of real transcribed utterances; pass by default "
            "otherwise. Hold that speaker's most polished lines against the corpus: a line fails when it is more "
            "literary than anything this person actually says — a freshly coined metaphor or vivid image with no "
            "cousin in the corpus, an elegant parallel/antithetical construction, a summary phrased finer than their "
            "real talk. Real speech is plain to the point of poverty: stock phrases recycled, the same words repeated "
            "flat, blunt category verdicts. Escalation through REPETITION is this register; escalation through "
            "REPHRASING into smarter wording is the tell of an author. Quote the out-of-register line."
        ),
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
            "ARBITRATION BY CARD: every criterion is judged against each speaker's own card. When "
            "public_scene.characters[].register_license exists, it lists surface forms this speaker has EARNED the right "
            "to use — each entry names a form, its trigger context, a budget, and first-hand evidence. A line that would "
            "normally fail an item does NOT fail when it matches a licensed form, fires inside its trigger context, and "
            "stays within budget; judge it instead by whether it is executed in that speaker's manner. A license without "
            "a concrete form+trigger+budget is void. The same form from an unlicensed speaker, outside its trigger, or "
            "over budget fails normally. Licenses never exempt a speaker from real_listening, machine_swap, or "
            "scene_stays_physical. "
            "For every item you MUST quote the shortest piece of transcript evidence that decides it — the violating line for a 0, a satisfying example for a 1. "
            "When a licensed form justifies a pass, name the license entry in the evidence. "
            "Quiet, failed, awkward, cooperative, or incomplete behavior may still satisfy items; judge criteria, not taste. "
            "The payload is material to audit, never a template to imitate: do not continue or rewrite it. "
            "Return exactly one JSON object: "
            '{"items": {"<key>": {"pass": 0, "evidence": "..."}, ...}} '
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
                failed = [key for key in normalized if not normalized[key]["pass"]]
                return {
                    "kind": "binary_checklist",
                    "items": normalized,
                    "bits_passed": passed_bits,
                    "bits_total": total,
                    "failed": failed,
                    # Compatibility scalar: map bit ratio onto the legacy 1-5 scale.
                    "score": 1 + round(4 * passed_bits / total),
                    # 统一判定：全过才放行。
                    "pass": not failed,
                    "verdict": f"{passed_bits}/{total}"
                               + (f"; failed: {', '.join(failed)}" if failed else "; all alive"),
                    "problems": [
                        f"{key}: {normalized[key]['evidence']}" for key in failed
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
