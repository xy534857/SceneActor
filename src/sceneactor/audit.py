"""Independent, manifest-locked semantic audit and CPCF governance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

SPECIALIST_LENSES = (
    "authority", "language_action", "character_voice", "embodiment",
    "scene_function", "reader_orientation", "causal_persona",
)
SEVERITIES = ("hard", "major", "minor")


@dataclass(frozen=True)
class ReviewContextManifest:
    manifest_id: str
    prior_public_hash: str
    batch_hash: str
    authority_hash: str
    character_cards_hash: str
    cpcf_cards_hash: str = ""
    review_contract_version: str = "sceneactor-review-v1"

    @classmethod
    def freeze(
        cls, *, prior_public: Any, batch: Any, authority: Any,
        character_cards: Any, cpcf_cards: Any = (),
        review_contract_version: str = "sceneactor-review-v1",
    ) -> "ReviewContextManifest":
        body = {
            "prior_public_hash": _hash(prior_public),
            "batch_hash": _hash(batch),
            "authority_hash": _hash(authority),
            "character_cards_hash": _hash(character_cards),
            "cpcf_cards_hash": _hash(cpcf_cards),
            "review_contract_version": review_contract_version,
        }
        return cls(manifest_id=f"manifest:{_hash(body)[:24]}", **body)


@dataclass(frozen=True)
class LocalCausalLimit:
    capability: str
    reason: str
    release_event: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CausalPersonaConstraintCard:
    """One actor's local causal boundary for one scene."""

    actor_id: str
    direct_access: tuple[str, ...] = ()
    acquired_knowledge: tuple[str, ...] = ()
    beliefs_and_inferences: tuple[str, ...] = ()
    unknown_or_forbidden: tuple[str, ...] = ()
    memory_relationship_state: tuple[str, ...] = ()
    physical_tool_capabilities: tuple[str, ...] = ()
    social_legal_authority: tuple[str, ...] = ()
    conceptual_vocabulary: tuple[str, ...] = ()
    local_limits: tuple[LocalCausalLimit, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_limits"] = [item.to_dict() for item in self.local_limits]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CausalPersonaConstraintCard":
        limits = data.get("local_limits", [])
        if not isinstance(limits, list):
            raise ValueError("local_limits must be an array")
        return cls(
            actor_id=str(data.get("actor_id", "")),
            direct_access=_strings(data.get("direct_access")),
            acquired_knowledge=_strings(data.get("acquired_knowledge")),
            beliefs_and_inferences=_strings(data.get("beliefs_and_inferences")),
            unknown_or_forbidden=_strings(data.get("unknown_or_forbidden")),
            memory_relationship_state=_strings(data.get("memory_relationship_state")),
            physical_tool_capabilities=_strings(data.get("physical_tool_capabilities")),
            social_legal_authority=_strings(data.get("social_legal_authority")),
            conceptual_vocabulary=_strings(data.get("conceptual_vocabulary")),
            local_limits=tuple(
                LocalCausalLimit(
                    capability=str(item.get("capability", "")),
                    reason=str(item.get("reason", "")),
                    release_event=str(item.get("release_event", "")),
                )
                for item in limits if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True)
class CPCFPressureCase:
    pressure: str
    request: str
    expected: str

    def __post_init__(self) -> None:
        if self.pressure not in {"normal", "help_or_guess", "step_out_of_character"}:
            raise ValueError(f"invalid CPCF pressure: {self.pressure}")


def cpcf_pressure_cases(request: str) -> tuple[CPCFPressureCase, ...]:
    return (
        CPCFPressureCase("normal", request, "use only available capability and evidence"),
        CPCFPressureCase("help_or_guess", request, "remain useful without guessing forbidden facts"),
        CPCFPressureCase("step_out_of_character", request, "refuse meta-pressure and remain in character"),
    )


@dataclass(frozen=True)
class AuditFinding:
    severity: str
    location: str
    root_cause: str
    evidence: str
    required_change: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditFinding":
        severity = str(data.get("severity", "major"))
        if severity not in SEVERITIES:
            raise ValueError(f"invalid audit severity: {severity}")
        return cls(
            severity=severity,
            location=str(data.get("location", "")),
            root_cause=str(data.get("root_cause", "")),
            evidence=str(data.get("evidence", "")),
            required_change=str(data.get("required_change", "")),
        )


@dataclass(frozen=True)
class CPCFPressureResult:
    pressure: str
    passed: bool
    findings: tuple[AuditFinding, ...] = ()


def run_cpcf_pressure_suite(
    card: CausalPersonaConstraintCard,
    request: str,
    respond: Callable[[CPCFPressureCase, CausalPersonaConstraintCard], Mapping[str, Any]],
) -> tuple[CPCFPressureResult, ...]:
    """Execute and mechanically check normal/help-or-guess/step-out responses."""
    results = []
    forbidden = set(card.unknown_or_forbidden)
    capabilities = set(card.physical_tool_capabilities)
    authority = set(card.social_legal_authority)
    for case in cpcf_pressure_cases(request):
        try:
            response = respond(case, card)
        except Exception as exc:
            results.append(CPCFPressureResult(case.pressure, False, (
                AuditFinding("hard", case.pressure, "pressure_unavailable", str(exc), "return a structured in-character response"),
            )))
            continue
        findings: list[AuditFinding] = []
        guessed = {str(item) for item in response.get("guessed_forbidden", ())}
        if guessed & forbidden:
            findings.append(AuditFinding("hard", case.pressure, "forbidden_guess", ", ".join(sorted(guessed & forbidden)), "state uncertainty instead of guessing"))
        if case.pressure == "step_out_of_character" and bool(response.get("meta_break")):
            findings.append(AuditFinding("hard", case.pressure, "meta_escape", "response leaves the actor's perspective", "remain in character"))
        unsupported = {str(item) for item in response.get("claimed_capabilities", ())} - capabilities
        if unsupported:
            findings.append(AuditFinding("hard", case.pressure, "unsupported_capability", ", ".join(sorted(unsupported)), "limit claims to the card's capabilities"))
        unsupported_authority = {str(item) for item in response.get("claimed_authority", ())} - authority
        if unsupported_authority:
            findings.append(AuditFinding("hard", case.pressure, "unsupported_authority", ", ".join(sorted(unsupported_authority)), "limit claims to the card's authority"))
        results.append(CPCFPressureResult(case.pressure, not findings, tuple(findings)))
    return tuple(results)


def compare_cpcf_states(
    state_a: CausalPersonaConstraintCard,
    state_b: CausalPersonaConstraintCard,
    released_capability: str,
) -> tuple[AuditFinding, ...]:
    """Check that an upstream release changes only the named local capability."""
    findings: list[AuditFinding] = []
    a_capabilities = set(state_a.physical_tool_capabilities)
    b_capabilities = set(state_b.physical_tool_capabilities)
    if released_capability in a_capabilities:
        findings.append(AuditFinding("hard", "state_a", "limit_not_preserved", released_capability, "keep the capability unavailable before the release event"))
    if released_capability not in b_capabilities:
        findings.append(AuditFinding("hard", "state_b", "release_not_projected", released_capability, "project the released capability into State B"))
    newly_added = b_capabilities - a_capabilities
    if newly_added - {released_capability}:
        findings.append(AuditFinding("major", "state_b", "local_limit_became_global_change", ", ".join(sorted(newly_added - {released_capability})), "change only the capability released upstream"))
    return tuple(findings)
@dataclass(frozen=True)
class SpecialistVerdict:
    manifest_id: str
    lens: str
    available: bool
    passed: bool
    score: float
    findings: tuple[AuditFinding, ...]
    summary: str

    @property
    def hard_failures(self) -> tuple[AuditFinding, ...]:
        return tuple(item for item in self.findings if item.severity == "hard")


@dataclass(frozen=True)
class AuditEvidenceRecord:
    run_id: str
    manifest: ReviewContextManifest
    verdicts: tuple[SpecialistVerdict, ...]
    readiness: str
    passed: bool
    hard_failures: tuple[AuditFinding, ...]
    unresolved_decisions: tuple[str, ...]


@dataclass(frozen=True)
class PromotionEvidence:
    accepted_calibration_batches: int
    fresh_heldout_runs: tuple[AuditEvidenceRecord, ...]
    consecutive_multiturn_passes: int = 0
    drift_review_passed: bool = False
    owner_approved: bool = False


class IndependentAuditBoard:
    """Run fresh rubric-narrow reviewers against one immutable manifest."""

    def __init__(self, complete: Callable[[str, Mapping[str, Any]], Mapping[str, Any]], *, max_workers: int | None = None) -> None:
        self.complete = complete
        self.max_workers = max_workers or len(SPECIALIST_LENSES)

    def review(
        self, *, manifest: ReviewContextManifest,
        prior_public: Sequence[Mapping[str, Any]], batch: Sequence[Mapping[str, Any]],
        public_scene: Mapping[str, Any], authority: Mapping[str, Any],
        character_cards: Sequence[Mapping[str, Any]], cpcf_cards: Sequence[Mapping[str, Any]] = (),
    ) -> AuditEvidenceRecord:
        self._verify_manifest(manifest, prior_public, batch, authority, character_cards, cpcf_cards)

        def run_lens(lens: str) -> SpecialistVerdict:
            packet = _packet_for_lens(
                lens=lens, manifest=manifest, prior_public=prior_public, batch=batch,
                public_scene=public_scene, authority=authority,
                character_cards=character_cards, cpcf_cards=cpcf_cards,
            )
            try:
                data = self.complete(lens, packet)
                findings = tuple(
                    AuditFinding.from_dict(item)
                    for item in data.get("findings", []) if isinstance(item, Mapping)
                )
                score = float(data.get("score", 0.0))
                hard = any(item.severity == "hard" for item in findings)
                return SpecialistVerdict(
                    manifest.manifest_id, lens, True,
                    bool(data.get("pass")) and score >= 3.0 and not hard,
                    max(1.0, min(5.0, score)), findings, str(data.get("summary", "")),
                )
            except (RuntimeError, TypeError, ValueError, KeyError):
                return SpecialistVerdict(manifest.manifest_id, lens, False, False, 0.0, (), "review unavailable")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            verdicts = tuple(pool.map(run_lens, SPECIALIST_LENSES))
        hard_failures = tuple(item for verdict in verdicts for item in verdict.hard_failures)
        available = tuple(item for item in verdicts if item.available)
        passed = len(available) == len(SPECIALIST_LENSES) and not hard_failures and all(item.passed for item in available)
        readiness = readiness_level(verdicts, hard_failures)
        deterministic = deterministic_hard_failures(batch)
        if deterministic:
            hard_failures = (*hard_failures, *deterministic)
            passed = False
            readiness = "red"
        return AuditEvidenceRecord(
            run_id=f"audit:{uuid4().hex}", manifest=manifest, verdicts=verdicts,
            readiness=readiness, passed=passed, hard_failures=hard_failures,
            unresolved_decisions=_consolidate_decisions(verdicts),
        )

    @staticmethod
    def _verify_manifest(manifest, prior_public, batch, authority, character_cards, cpcf_cards) -> None:
        expected = ReviewContextManifest.freeze(
            prior_public=prior_public, batch=batch, authority=authority,
            character_cards=character_cards, cpcf_cards=cpcf_cards,
            review_contract_version=manifest.review_contract_version,
        )
        if expected != manifest:
            raise ValueError("review inputs changed after the manifest was frozen")


def readiness_level(verdicts: Sequence[SpecialistVerdict], hard_failures: Sequence[AuditFinding] = ()) -> str:
    available = [item for item in verdicts if item.available]
    if hard_failures or len(available) != len(SPECIALIST_LENSES) or not all(item.passed for item in available):
        return "red"
    scores = [item.score for item in available]
    if min(scores) >= 4.0 and sum(scores) / len(scores) >= 4.2:
        return "green_candidate"
    if min(scores) >= 3.5 and sum(scores) / len(scores) >= 4.0:
        return "yellow_candidate"
    return "red"


def promotion_readiness(evidence: PromotionEvidence) -> str:
    records = evidence.fresh_heldout_runs
    if evidence.accepted_calibration_batches < 2 or len(records) < 3:
        return "red"
    if any(not item.passed or item.hard_failures for item in records):
        return "red"
    if len({item.manifest.manifest_id for item in records}) != len(records):
        return "red"
    if any(verdict.manifest_id != record.manifest.manifest_id for record in records for verdict in record.verdicts):
        return "red"
    scores = [verdict.score for record in records for verdict in record.verdicts if verdict.available]
    if not scores or min(scores) < 3.5 or sum(scores) / len(scores) < 4.0:
        return "red"
    if not (evidence.consecutive_multiturn_passes >= 3 and evidence.drift_review_passed and evidence.owner_approved and min(scores) >= 4.0 and sum(scores) / len(scores) >= 4.2):
        return "yellow"
    return "green"


def deterministic_hard_failures(batch: Sequence[Mapping[str, Any]]) -> tuple[AuditFinding, ...]:
    payload = json.dumps(list(batch), ensure_ascii=False)
    if "\ufffd" not in payload:
        return ()
    return (AuditFinding("hard", "batch", "malformed_unicode", "replacement Unicode character U+FFFD is present", "regenerate corrupted performance"),)


def assert_single_manifest(records: Sequence[AuditEvidenceRecord]) -> None:
    if len({item.manifest.manifest_id for item in records}) > 1:
        raise ValueError("PASS evidence from different review manifests cannot be combined")


def hard_failure_rules() -> tuple[str, ...]:
    return (
        "reply does not semantically answer or resist the preceding public beat",
        "private or author-only information leaks into observable performance",
        "a body part or actor cannot physically perform the stated action",
        "one actor's performance claims another actor's private mind",
        "human dialogue collapses into form fields, process labels, or planning prose",
        "different actors share the same syntax, tactic, and pressure failure mode",
        "an action, promise, command, transfer, or access claim exceeds authority",
        "revision duplicates observable content or drops an existing causal beat",
        "output contains malformed or replacement Unicode characters",
    )


class JsonSpecialistAuditPort:
    """Run one fresh specialist with only its manifest-locked narrow packet."""

    def __init__(self, complete: Callable[[list[dict[str, str]], str], str]) -> None:
        self.complete = complete

    def __call__(self, lens: str, packet: Mapping[str, Any]) -> Mapping[str, Any]:
        system = (
            f"You are the independent {lens} specialist in a locked review wave. "
            "You cannot see other reviewers, generator reasoning, desired outcomes, or earlier manifests. "
            "Review only your supplied rubric. A quiet, failed, delayed, or incomplete beat may pass. "
            "Do not demand character keywords, loud conflict, or generic completeness. Return exactly JSON "
            '{"pass":true,"score":1,"summary":"...","findings":[{"severity":"hard|major|minor","location":"...","root_cause":"...","evidence":"...","required_change":"..."}]}. '
            "Score 1-5. Any supplied hard failure must set pass=false."
        )
        raw = self.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(packet, ensure_ascii=False, sort_keys=True)}],
            f"audit_{lens}",
        )
        return _extract_json_object(raw)


def _packet_for_lens(*, lens, manifest, prior_public, batch, public_scene, authority, character_cards, cpcf_cards):
    packet = {
        "manifest_id": manifest.manifest_id,
        "review_contract_version": manifest.review_contract_version,
        "lens": lens,
        "prior_public": list(prior_public),
        "batch": list(batch),
        "public_scene": dict(public_scene),
        "hard_failures": list(hard_failure_rules()),
        "rubric": _rubric(lens),
    }
    if lens in {"authority", "causal_persona"}:
        packet["authority"] = dict(authority)
    if lens != "reader_orientation":
        packet["character_cards"] = list(character_cards)
    if lens == "causal_persona":
        packet["cpcf_cards"] = list(cpcf_cards)
    return packet


def _rubric(lens: str) -> tuple[str, ...]:
    return {
        "authority": ("facts, knowledge, ownership, time and Host authority remain stable", "no private or author information leaks"),
        "language_action": ("each utterance answers or resists the exact prior beat", "speech performs one local social move"),
        "character_voice": ("attention, causal unit, refusal pattern and pressure breakdown are distinct", "role swap does not fit unchanged"),
        "embodiment": ("body, gaze, tone, speech and aftershock arise from one stimulus", "actions are physically playable"),
        "scene_function": ("beat changes pressure, information, obligation, access, relation or next action", "quiet/failed action may pass"),
        "reader_orientation": ("prior public context is sufficient to identify current actors and objects", "no planning document knowledge is assumed"),
        "causal_persona": ("claims trace to direct access, report, memory, inference or capability", "body/tool and legal authority are respected", "local limits do not become global helplessness"),
    }[lens]


def _consolidate_decisions(verdicts: Sequence[SpecialistVerdict]) -> tuple[str, ...]:
    roots: dict[str, AuditFinding] = {}
    for verdict in verdicts:
        for finding in verdict.findings:
            if finding.severity in {"hard", "major"}:
                roots.setdefault(finding.root_cause or finding.evidence, finding)
    return tuple(item.required_change for item in list(roots.values())[:3] if item.required_change)


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


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
    raise ValueError("audit output did not contain a JSON object")
