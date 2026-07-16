"""Transport-neutral contracts for approved external template assets."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import unicodedata
from typing import Any, Mapping

PROTOCOL_VERSION = "sceneactor-template/1.0"
PERFORMANCE_MODES = ("actor_led", "anchor_led", "hybrid")
REALITY_MODES = ("realistic", "heightened", "absurd_but_consistent")
SLOT_KINDS = ("character", "scene", "task_or_prop", "ending")
SLOT_VISIBILITIES = ("public", "adapter", "review")
ASSET_KINDS = ("character", "visual", "audio", "video", "action", "prop", "scene")
TEMPORAL_ANCHOR_KINDS = ("audio_cue", "action_keyframe", "pause", "reaction", "loop")
RIGHTS_STATUSES = ("approved", "approved_noncommercial", "research_only", "restricted", "expired", "blocked")
USAGE_CONTEXTS = ("research", "noncommercial", "commercial")
RESPONSE_STATUSES = (
    "ok",
    "not_found",
    "version_conflict",
    "rights_restricted",
    "invalid_slots",
    "protocol_error",
    "unavailable",
)


@dataclass(frozen=True)
class AssetRef:
    asset_id: str
    version: str
    kind: str
    content_hash: str
    uri: str = ""
    rights_ref: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.asset_id, "asset id")
        _require_identifier(self.version, "asset version")
        if self.kind not in ASSET_KINDS:
            raise ValueError(f"invalid asset kind: {self.kind}")
        _require_hash(self.content_hash, "asset content hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "version": self.version,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "uri": self.uri,
            "rights_ref": self.rights_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRef":
        return cls(
            asset_id=_text(data, "asset_id"),
            version=_text(data, "version"),
            kind=_text(data, "kind"),
            content_hash=_text(data, "content_hash"),
            uri=_text(data, "uri"),
            rights_ref=_text(data, "rights_ref"),
        )


@dataclass(frozen=True)
class TemporalAnchor:
    anchor_id: str
    kind: str
    order: int
    instruction: str
    soft_time_hint: str = ""
    asset_id: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.anchor_id, "temporal anchor id")
        if self.kind not in TEMPORAL_ANCHOR_KINDS:
            raise ValueError(f"invalid temporal anchor kind: {self.kind}")
        if self.order < 0:
            raise ValueError("temporal anchor order must be non-negative")
        _require(self.instruction, "temporal anchor instruction")
        if self.asset_id:
            _require_identifier(self.asset_id, "temporal anchor asset id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "kind": self.kind,
            "order": self.order,
            "instruction": self.instruction,
            "soft_time_hint": self.soft_time_hint,
            "asset_id": self.asset_id,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemporalAnchor":
        return cls(
            anchor_id=_text(data, "anchor_id"),
            kind=_text(data, "kind"),
            order=int(data.get("order", 0)),
            instruction=_text(data, "instruction"),
            soft_time_hint=_text(data, "soft_time_hint"),
            asset_id=_text(data, "asset_id"),
            required=bool(data.get("required", True)),
        )


@dataclass(frozen=True)
class FixedAnchors:
    visual_asset_ids: tuple[str, ...] = ()
    temporal: tuple[TemporalAnchor, ...] = ()
    narrative_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _unique(self.visual_asset_ids, "visual anchor asset")
        _unique((item.anchor_id for item in self.temporal), "temporal anchor")
        orders = tuple(item.order for item in self.temporal)
        _unique(orders, "temporal anchor order")
        if tuple(sorted(orders)) != orders:
            raise ValueError("temporal anchors must be ordered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_asset_ids": list(self.visual_asset_ids),
            "temporal": [item.to_dict() for item in self.temporal],
            "narrative_rules": list(self.narrative_rules),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FixedAnchors":
        return cls(
            visual_asset_ids=_strings(data.get("visual_asset_ids")),
            temporal=tuple(
                TemporalAnchor.from_dict(item)
                for item in _mappings(data.get("temporal"))
            ),
            narrative_rules=_strings(data.get("narrative_rules")),
        )


@dataclass(frozen=True)
class SlotDefinition:
    slot_id: str
    kind: str
    description: str
    required: bool = True
    visibility: str = "public"
    allowed_values: tuple[str, ...] = ()
    default_value: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.slot_id, "slot id")
        if self.kind not in SLOT_KINDS:
            raise ValueError(f"invalid slot kind: {self.kind}")
        if self.visibility not in SLOT_VISIBILITIES:
            raise ValueError(f"invalid slot visibility: {self.visibility}")
        _require(self.description, "slot description")
        _unique(self.allowed_values, "allowed slot value")
        if self.default_value and self.allowed_values and self.default_value not in self.allowed_values:
            raise ValueError("slot default is not an allowed value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "description": self.description,
            "required": self.required,
            "visibility": self.visibility,
            "allowed_values": list(self.allowed_values),
            "default_value": self.default_value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SlotDefinition":
        return cls(
            slot_id=_text(data, "slot_id"),
            kind=_text(data, "kind"),
            description=_text(data, "description"),
            required=bool(data.get("required", True)),
            visibility=_text(data, "visibility") or "public",
            allowed_values=_strings(data.get("allowed_values")),
            default_value=_text(data, "default_value"),
        )


@dataclass(frozen=True)
class RelationshipRole:
    character_slot: str
    function: str
    stable_relation: str

    def __post_init__(self) -> None:
        _require(self.character_slot, "relationship character slot")
        _require(self.function, "relationship function")
        _require(self.stable_relation, "stable relation")

    def to_dict(self) -> dict[str, str]:
        return {
            "character_slot": self.character_slot,
            "function": self.function,
            "stable_relation": self.stable_relation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationshipRole":
        return cls(
            character_slot=_text(data, "character_slot"),
            function=_text(data, "function"),
            stable_relation=_text(data, "stable_relation"),
        )


@dataclass(frozen=True)
class RightsGrant:
    rights_ref: str
    status: str
    allowed_usage: tuple[str, ...]
    valid_until: str = ""
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.rights_ref, "rights reference")
        if self.status not in RIGHTS_STATUSES:
            raise ValueError(f"invalid rights status: {self.status}")
        if any(item not in USAGE_CONTEXTS for item in self.allowed_usage):
            raise ValueError("rights grant contains an invalid usage context")
        _unique(self.allowed_usage, "rights usage")

    def allows(self, usage_context: str) -> bool:
        if usage_context not in self.allowed_usage:
            return False
        if self.status == "approved":
            return True
        if self.status == "approved_noncommercial":
            return usage_context in {"research", "noncommercial"}
        if self.status == "research_only":
            return usage_context == "research"
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rights_ref": self.rights_ref,
            "status": self.status,
            "allowed_usage": list(self.allowed_usage),
            "valid_until": self.valid_until,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RightsGrant":
        return cls(
            rights_ref=_text(data, "rights_ref"),
            status=_text(data, "status"),
            allowed_usage=_strings(data.get("allowed_usage")),
            valid_until=_text(data, "valid_until"),
            source_refs=_strings(data.get("source_refs")),
        )


@dataclass(frozen=True)
class TemplatePerformanceContract:
    template_id: str
    version: str
    content_hash: str
    mother_formula: str
    emotional_core: str
    reality_mode: str
    performance_mode: str
    public_rules: tuple[str, ...]
    fixed_anchors: FixedAnchors
    slots: tuple[SlotDefinition, ...]
    relationship_roles: tuple[RelationshipRole, ...]
    payoff_condition: str
    reaction_target: str
    allowed_variation: tuple[str, ...]
    forbidden_drift: tuple[str, ...]
    assets: tuple[AssetRef, ...]
    rights: RightsGrant
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_identifier(self.template_id, "template id")
        _require_identifier(self.version, "template version")
        _require(self.mother_formula, "mother formula")
        _require(self.emotional_core, "emotional core")
        if self.version == "latest":
            raise ValueError("template version must be exact, not latest")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported template protocol: {self.protocol_version}")
        if self.reality_mode not in REALITY_MODES:
            raise ValueError(f"invalid reality mode: {self.reality_mode}")
        if self.performance_mode not in PERFORMANCE_MODES:
            raise ValueError(f"invalid performance mode: {self.performance_mode}")
        _unique((item.slot_id for item in self.slots), "slot")
        _unique((item.asset_id for item in self.assets), "asset")
        asset_ids = {item.asset_id for item in self.assets}
        missing_visual = set(self.fixed_anchors.visual_asset_ids) - asset_ids
        missing_temporal = {item.asset_id for item in self.fixed_anchors.temporal if item.asset_id} - asset_ids
        if missing_visual or missing_temporal:
            raise ValueError("template anchors reference unknown assets")
        if any(item.rights_ref != self.rights.rights_ref for item in self.assets):
            raise ValueError("every asset rights_ref must match the contract rights grant")
        character_slots = {item.slot_id for item in self.slots if item.kind == "character"}
        if any(item.character_slot not in character_slots for item in self.relationship_roles):
            raise ValueError("relationship role references an unknown character slot")
        if self.content_hash:
            _require_hash(self.content_hash, "template content hash")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "template_id": self.template_id,
            "version": self.version,
            "mother_formula": self.mother_formula,
            "emotional_core": self.emotional_core,
            "reality_mode": self.reality_mode,
            "performance_mode": self.performance_mode,
            "public_rules": list(self.public_rules),
            "fixed_anchors": self.fixed_anchors.to_dict(),
            "slots": [item.to_dict() for item in self.slots],
            "relationship_roles": [item.to_dict() for item in self.relationship_roles],
            "payoff_condition": self.payoff_condition,
            "reaction_target": self.reaction_target,
            "allowed_variation": list(self.allowed_variation),
            "forbidden_drift": list(self.forbidden_drift),
            "assets": [item.to_dict() for item in self.assets],
            "rights": self.rights.to_dict(),
        }

    def compute_hash(self) -> str:
        return _hash(self.unsigned_dict())

    def seal(self) -> "TemplatePerformanceContract":
        return replace(self, content_hash=self.compute_hash())

    def verify_integrity(self) -> None:
        if not self.content_hash or self.content_hash != self.compute_hash():
            raise ValueError("template contract content hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemplatePerformanceContract":
        return cls(
            protocol_version=_text(data, "protocol_version"),
            template_id=_text(data, "template_id"),
            version=_text(data, "version"),
            content_hash=_text(data, "content_hash"),
            mother_formula=_text(data, "mother_formula"),
            emotional_core=_text(data, "emotional_core"),
            reality_mode=_text(data, "reality_mode"),
            performance_mode=_text(data, "performance_mode"),
            public_rules=_strings(data.get("public_rules")),
            fixed_anchors=FixedAnchors.from_dict(_mapping(data.get("fixed_anchors"))),
            slots=tuple(SlotDefinition.from_dict(item) for item in _mappings(data.get("slots"))),
            relationship_roles=tuple(
                RelationshipRole.from_dict(item)
                for item in _mappings(data.get("relationship_roles"))
            ),
            payoff_condition=_text(data, "payoff_condition"),
            reaction_target=_text(data, "reaction_target"),
            allowed_variation=_strings(data.get("allowed_variation")),
            forbidden_drift=_strings(data.get("forbidden_drift")),
            assets=tuple(AssetRef.from_dict(item) for item in _mappings(data.get("assets"))),
            rights=RightsGrant.from_dict(_mapping(data.get("rights"))),
        )
@dataclass(frozen=True)
class TemplateResolveRequest:
    request_id: str
    template_id: str
    version: str
    selected_slots: Mapping[str, str]
    usage_context: str
    consumer: str = "sceneactor"
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_identifier(self.request_id, "request id")
        _require_identifier(self.template_id, "template id")
        _require_identifier(self.version, "template version")
        if self.version == "latest":
            raise ValueError("template request version must be exact, not latest")
        _require_identifier(self.consumer, "consumer")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported template protocol: {self.protocol_version}")
        if self.usage_context not in USAGE_CONTEXTS:
            raise ValueError(f"invalid usage context: {self.usage_context}")
        if any(not str(key).strip() or not str(value).strip() for key, value in self.selected_slots.items()):
            raise ValueError("selected slot keys and values must be non-empty strings")
        for key in self.selected_slots:
            _require_identifier(str(key), "selected slot id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "template_id": self.template_id,
            "version": self.version,
            "selected_slots": dict(self.selected_slots),
            "usage_context": self.usage_context,
            "consumer": self.consumer,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemplateResolveRequest":
        slots = data.get("selected_slots", {})
        if not isinstance(slots, Mapping):
            raise ValueError("selected_slots must be an object")
        return cls(
            protocol_version=_text(data, "protocol_version"),
            request_id=_text(data, "request_id"),
            template_id=_text(data, "template_id"),
            version=_text(data, "version"),
            selected_slots={str(key): str(value) for key, value in slots.items()},
            usage_context=_text(data, "usage_context"),
            consumer=_text(data, "consumer") or "sceneactor",
        )


@dataclass(frozen=True)
class ProtocolFault:
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.code, "fault code")
        _require(self.message, "fault message")
        if not isinstance(self.details, Mapping):
            raise ValueError("fault details must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtocolFault":
        return cls(
            code=_text(data, "code"),
            message=_text(data, "message"),
            retryable=bool(data.get("retryable", False)),
            details=dict(_mapping(data.get("details"))),
        )


@dataclass(frozen=True)
class TemplateResolveResponse:
    request_id: str
    status: str
    selected_slots: Mapping[str, str]
    contract: TemplatePerformanceContract | None = None
    fault: ProtocolFault | None = None
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        _require_identifier(self.request_id, "response request id")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported template protocol: {self.protocol_version}")
        if self.status not in RESPONSE_STATUSES:
            raise ValueError(f"invalid response status: {self.status}")
        if self.status == "ok" and (self.contract is None or self.fault is not None):
            raise ValueError("ok response requires a contract and no fault")
        if self.status != "ok" and (self.contract is not None or self.fault is None):
            raise ValueError("error response requires a fault and no contract")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "status": self.status,
            "selected_slots": dict(self.selected_slots),
            "contract": self.contract.to_dict() if self.contract else None,
            "fault": self.fault.to_dict() if self.fault else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TemplateResolveResponse":
        slots = data.get("selected_slots", {})
        if not isinstance(slots, Mapping):
            raise ValueError("selected_slots must be an object")
        contract = data.get("contract")
        fault = data.get("fault")
        return cls(
            protocol_version=_text(data, "protocol_version"),
            request_id=_text(data, "request_id"),
            status=_text(data, "status"),
            selected_slots={str(key): str(value) for key, value in slots.items()},
            contract=TemplatePerformanceContract.from_dict(contract) if isinstance(contract, Mapping) else None,
            fault=ProtocolFault.from_dict(fault) if isinstance(fault, Mapping) else None,
        )


def validate_selected_slots(
    contract: TemplatePerformanceContract,
    selected_slots: Mapping[str, str],
) -> dict[str, str]:
    definitions = {item.slot_id: item for item in contract.slots}
    unknown = set(selected_slots) - set(definitions)
    if unknown:
        raise ValueError("unknown selected slots: " + ", ".join(sorted(unknown)))
    resolved = dict(selected_slots)
    for slot in contract.slots:
        if slot.slot_id not in resolved and slot.default_value:
            resolved[slot.slot_id] = slot.default_value
        if slot.required and slot.slot_id not in resolved:
            raise ValueError(f"required slot is missing: {slot.slot_id}")
        value = resolved.get(slot.slot_id, "")
        if value and slot.allowed_values and value not in slot.allowed_values:
            raise ValueError(f"slot value is not allowed: {slot.slot_id}={value}")
    return resolved


def resolution_hash(
    contract: TemplatePerformanceContract,
    selected_slots: Mapping[str, str],
    usage_context: str,
) -> str:
    return _hash({
        "protocol_version": PROTOCOL_VERSION,
        "template_hash": contract.content_hash,
        "selected_slots": dict(selected_slots),
        "usage_context": usage_context,
    })


def _hash(value: Any) -> str:
    canonical = _canonicalize(value)
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            normalized = unicodedata.normalize("NFC", key)
            if normalized in result:
                raise ValueError("canonical JSON contains duplicate normalized keys")
            result[normalized] = _canonicalize(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, bool) or isinstance(value, int):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _require(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_identifier(value: str, name: str) -> None:
    _require(value, name)
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._:/@+")
    if not value.isascii() or any(character not in allowed for character in value):
        raise ValueError(f"{name} must use the protocol ASCII identifier alphabet")

def _require_hash(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _unique(values: Any, name: str) -> None:
    items = tuple(values)
    if len(set(items)) != len(items):
        raise ValueError(f"duplicate {name}")
