"""Stable, host-independent character identity."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class VoiceProfile:
    """How a person organizes speech; never a mandatory catchphrase list."""

    entry_point: str = ""
    ordering: str = ""
    turn_shape: str = ""
    interruption_recovery: str = ""
    avoidance_pattern: str = ""
    pressure_change: str = ""
    failure_mode: str = ""
    relationship_shifts: str = ""
    output_language: str = ""
    localization_rule: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "VoiceProfile":
        values = data or {}
        return cls(**{name: _optional_text(values, name) for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Persona:
    """Immutable identity. Only id and name are universally required."""

    id: str
    name: str
    gender: str = ""
    age: str = ""
    appearance: str = ""
    physical_constraints: str = ""
    role: str = ""
    background: str = ""
    values: str = ""
    preferences: str = ""
    competencies: str = ""
    desire: str = ""
    line: str = ""
    contradiction: str = ""
    feared_truth: str = ""
    soft_spot: str = ""
    secret: str = ""
    cognition_lens: str = ""
    voice: VoiceProfile = field(default_factory=VoiceProfile)
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("persona id and name are required")
        if self.id in {".", ".."} or "/" in self.id or "\\" in self.id:
            raise ValueError("persona id must be a safe path segment")
        if not isinstance(self.extensions, dict):
            raise ValueError("persona extensions must be an object")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Persona":
        if not isinstance(data, Mapping):
            raise ValueError("persona must be an object")
        fields = {
            name: _optional_text(data, name)
            for name in cls.__dataclass_fields__
            if name not in {"id", "name", "voice", "extensions"}
        }
        extensions = data.get("extensions", {})
        if not isinstance(extensions, dict):
            raise ValueError("persona extensions must be an object")
        return cls(
            id=_required_text(data, "id"),
            name=_required_text(data, "name"),
            voice=VoiceProfile.from_dict(_mapping(data.get("voice"))),
            extensions=dict(extensions),
            **fields,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["voice"] = self.voice.to_dict()
        return data


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
