"""Layer-safe projections from an accepted template resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import AssetRef, RelationshipRole, TemporalAnchor
from .provider import ResolvedTemplate


@dataclass(frozen=True)
class TemplateBinding:
    template_id: str
    version: str
    contract_hash: str
    resolution_hash: str


@dataclass(frozen=True)
class HostTemplateProjection:
    binding: TemplateBinding
    public_rules: tuple[str, ...]
    selected_slots: Mapping[str, str]


@dataclass(frozen=True)
class ActorTemplateProjection:
    """The only template data eligible for an actor-visible decision frame."""

    public_rules: tuple[str, ...]
    selected_slots: Mapping[str, str]

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "public_rules": list(self.public_rules),
            "selected_slots": dict(self.selected_slots),
        }


@dataclass(frozen=True)
class ReviewTemplateProjection:
    binding: TemplateBinding
    mother_formula: str
    emotional_core: str
    reality_mode: str
    performance_mode: str
    narrative_rules: tuple[str, ...]
    selected_slots: Mapping[str, str]
    relationship_roles: tuple[RelationshipRole, ...]
    payoff_condition: str
    allowed_variation: tuple[str, ...]
    forbidden_drift: tuple[str, ...]
    rights_ref: str
    rights_status: str


@dataclass(frozen=True)
class AdapterTemplateProjection:
    binding: TemplateBinding
    performance_mode: str
    selected_slots: Mapping[str, str]
    assets: tuple[AssetRef, ...]
    visual_asset_ids: tuple[str, ...]
    temporal_anchors: tuple[TemporalAnchor, ...]
    reaction_target: str


@dataclass(frozen=True)
class TemplateProjections:
    host: HostTemplateProjection
    actor: ActorTemplateProjection
    review: ReviewTemplateProjection
    adapter: AdapterTemplateProjection


def project_template(resolved: ResolvedTemplate) -> TemplateProjections:
    contract = resolved.contract
    binding = TemplateBinding(
        template_id=contract.template_id,
        version=contract.version,
        contract_hash=contract.content_hash,
        resolution_hash=resolved.resolution_hash,
    )
    definitions = {item.slot_id: item for item in contract.slots}
    public_slots = {
        key: value for key, value in resolved.selected_slots.items()
        if definitions[key].visibility == "public"
    }
    adapter_slots = {
        key: value for key, value in resolved.selected_slots.items()
        if definitions[key].visibility in {"public", "adapter"}
    }
    return TemplateProjections(
        host=HostTemplateProjection(
            binding=binding,
            public_rules=contract.public_rules,
            selected_slots=public_slots,
        ),
        actor=ActorTemplateProjection(
            public_rules=contract.public_rules,
            selected_slots=public_slots,
        ),
        review=ReviewTemplateProjection(
            binding=binding,
            mother_formula=contract.mother_formula,
            emotional_core=contract.emotional_core,
            reality_mode=contract.reality_mode,
            performance_mode=contract.performance_mode,
            narrative_rules=contract.fixed_anchors.narrative_rules,
            selected_slots=dict(resolved.selected_slots),
            relationship_roles=contract.relationship_roles,
            payoff_condition=contract.payoff_condition,
            allowed_variation=contract.allowed_variation,
            forbidden_drift=contract.forbidden_drift,
            rights_ref=contract.rights.rights_ref,
            rights_status=contract.rights.status,
        ),
        adapter=AdapterTemplateProjection(
            binding=binding,
            performance_mode=contract.performance_mode,
            selected_slots=adapter_slots,
            assets=contract.assets,
            visual_asset_ids=contract.fixed_anchors.visual_asset_ids,
            temporal_anchors=contract.fixed_anchors.temporal,
            reaction_target=contract.reaction_target,
        ),
    )
