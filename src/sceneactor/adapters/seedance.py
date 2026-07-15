"""Seedance presentation adapter.

Canonical PerformanceBeat remains the source; this module only compiles it into
provider-specific shot and reference instructions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts import AdapterProjectionState, PerformanceBeat, PerformanceContinuity


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    model_id: str
    supported_reference_roles: tuple[str, ...] = ()
    max_images: int | None = None
    max_videos: int | None = None
    max_audios: int | None = None
    min_duration: int | None = None
    max_duration: int | None = None
    resolutions: tuple[str, ...] = ()
    supports_audio: bool | None = None


@dataclass(frozen=True)
class ReferenceBinding:
    asset_id: str
    entity_id: str
    role: str
    required: bool = True
    allowed_transfer: tuple[str, ...] = ()
    excluded_transfer: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShotSpec:
    shot_id: str
    beat_id: str
    dramatic_function: str
    visual_prompt: str
    camera_instruction: str
    audio_instruction: str
    soft_time_hint: str
    continuity_in: Mapping[str, Any]
    continuity_out: Mapping[str, Any]
    references: tuple[ReferenceBinding, ...] = ()

    def to_prompt(self, capabilities: ProviderCapabilities) -> str:
        refs = " ".join(f"@{item.asset_id} ({item.role})" for item in self.references)
        parts = [
            f"Task: generate one shot for {capabilities.model_id}.",
            f"Subject and visible action: {self.visual_prompt}",
            f"Camera: {self.camera_instruction}",
            f"Audio: {self.audio_instruction}" if self.audio_instruction else "",
            f"Shot order: {self.shot_id}; timing is a soft hint: {self.soft_time_hint}" if self.soft_time_hint else "",
            f"References: {refs}" if refs else "",
            "Keep the accepted character, wardrobe, prop ownership, screen direction, and scene facts consistent.",
        ]
        return "\n".join(part for part in parts if part)


class SeedanceCompiler:
    """Compile committed beats; no cognition, world mutation, or quality guessing."""

    def __init__(self, capabilities: ProviderCapabilities) -> None:
        self.capabilities = capabilities

    def compile_beat(
        self,
        beat: PerformanceBeat,
        *,
        shot_id: str,
        dramatic_function: str,
        continuity: PerformanceContinuity,
        projection: AdapterProjectionState | None = None,
        references: Sequence[ReferenceBinding] = (),
        soft_time_hint: str = "",
    ) -> ShotSpec:
        self._validate_references(references)
        action = beat.action or "no large visible action"
        visual = f"{beat.actor_id} {action}"
        if beat.gaze:
            visual += f" Gaze: {beat.gaze}."
        if beat.blocking:
            visual += f" Blocking: {beat.blocking}."
        if beat.posture_change:
            visual += f" Posture: {beat.posture_change}."
        camera = "stable medium coverage" if not beat.attention_target else f"focus on {beat.attention_target}"
        audio = beat.speech
        if beat.delivery:
            delivery = asdict(beat.delivery)
            audio = f"{audio} Delivery: " + ", ".join(f"{key}={value}" for key, value in delivery.items() if value)
        projection_data = asdict(projection) if projection else {}
        return ShotSpec(
            shot_id=shot_id,
            beat_id=beat.beat_id,
            dramatic_function=dramatic_function,
            visual_prompt=visual,
            camera_instruction=camera,
            audio_instruction=audio,
            soft_time_hint=soft_time_hint,
            continuity_in={"response_hook": continuity.response_hook, **projection_data},
            continuity_out={
                "response_hook": beat.response_hook,
                "physical_residue": beat.physical_residue,
            },
            references=tuple(references),
        )

    def _validate_references(self, references: Sequence[ReferenceBinding]) -> None:
        seen: set[str] = set()
        for item in references:
            if item.asset_id in seen:
                raise ValueError(f"duplicate reference asset: {item.asset_id}")
            seen.add(item.asset_id)
            if self.capabilities.supported_reference_roles and item.role not in self.capabilities.supported_reference_roles:
                raise ValueError(f"provider does not support reference role: {item.role}")
        if self.capabilities.max_images is not None:
            image_count = sum(item.role in {"identity_anchor", "face_anchor", "scene_style", "prop", "first_frame", "last_frame"} for item in references)
            if image_count > self.capabilities.max_images:
                raise ValueError("reference image count exceeds provider capability")
