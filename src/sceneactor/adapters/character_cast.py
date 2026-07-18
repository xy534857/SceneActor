"""Adapter-layer character substitution from TemplateStudio character packs.

Cognition and performance beats keep their original actor IDs. This adapter
only replaces rendered appearance: actor IDs are deterministically mapped to a
research character pack (round-robin by default), then local identity images
are uploaded/registered for TokenRouter video generation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .seedance import ReferenceBinding
from .tokenrouter import ReferenceMedia, TokenRouterVideoClient


class CharacterCastError(RuntimeError):
    pass


def default_character_library_root() -> Path:
    configured = os.environ.get("SCENEACTOR_CHARACTER_LIBRARY", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Workspace" / "TemplateStudio" / "data" / "characters"


def default_substitution_config_path() -> Path:
    configured = os.environ.get("SCENEACTOR_CHARACTER_CAST_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[3] / "config" / "character_substitution.json"


@dataclass(frozen=True)
class CharacterSubstitutionConfig:
    enabled: bool
    pack_id: str
    strategy: str
    character_order: tuple[str, ...]
    research_only: bool

    @classmethod
    def load(cls, path: Path | None = None) -> "CharacterSubstitutionConfig":
        source = path or default_substitution_config_path()
        raw = _object(source)
        strategy = str(raw.get("strategy", "round_robin"))
        if strategy != "round_robin":
            raise CharacterCastError(f"unsupported character casting strategy: {strategy}")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            pack_id=str(raw.get("pack_id", "")),
            strategy=strategy,
            character_order=tuple(str(item) for item in raw.get("character_order", [])),
            research_only=bool(raw.get("research_only", True)),
        )


@dataclass(frozen=True)
class CharacterIdentity:
    character_id: str
    code: str
    official_name_ja: str
    display_name_zh: str
    content_hash: str
    local_path: Path
    source_url: str
    material_real_person: bool


@dataclass(frozen=True)
class CharacterAudioReference:
    audio_id: str
    role: str
    local_path: Path
    source_url: str


@dataclass(frozen=True)
class CharacterCast:
    pack_id: str
    assignments: Mapping[str, CharacterIdentity]
    rights_status: str
    publication_authorized: bool

    def character_for(self, actor_id: str) -> CharacterIdentity:
        try:
            return self.assignments[actor_id]
        except KeyError:
            raise CharacterCastError(f"actor has no character assignment: {actor_id}") from None

    def reference_bindings(self) -> tuple[ReferenceBinding, ...]:
        return tuple(
            ReferenceBinding(
                asset_id=identity.character_id,
                entity_id=actor_id,
                role="identity_anchor",
            )
            for actor_id, identity in self.assignments.items()
        )

    def prompt_prefix(self) -> str:
        lines = ["Render cast substitutions (appearance only; keep original actor behavior):"]
        for actor_id, identity in self.assignments.items():
            lines.append(f"- {actor_id} uses {identity.official_name_ja} ({identity.display_name_zh}) appearance.")
        return "\n".join(lines)

    def require_publication_authorized(self) -> None:
        if not self.publication_authorized:
            raise CharacterCastError(
                f"{self.pack_id} is {self.rights_status}; publication is not authorized"
            )


class CharacterReferenceLibrary:
    """Read TemplateStudio's versioned ``data/characters`` manifests."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_character_library_root()).expanduser()
        self.index_path = self.root / "index.json"
        if not self.index_path.is_file():
            raise CharacterCastError(f"character library index not found: {self.index_path}")

    def load_cast(
        self,
        actor_ids: Sequence[str],
        config: CharacterSubstitutionConfig | None = None,
    ) -> CharacterCast:
        cfg = config or CharacterSubstitutionConfig.load()
        if not cfg.enabled:
            raise CharacterCastError("character substitution is disabled")
        index = _object(self.index_path)
        entry = next((item for item in index.get("packs", []) if item.get("pack_id") == cfg.pack_id), None)
        if entry is None:
            raise CharacterCastError(f"unknown character pack: {cfg.pack_id}")
        raw = _object(self.root / str(entry["manifest"]))
        rights = raw.get("rights") or {}
        character_list = tuple(_identity(item) for item in raw.get("characters", []))
        characters = {item.character_id: item for item in character_list}
        order = cfg.character_order or tuple(str(item) for item in raw.get("default_cast_order", []))
        if not order:
            raise CharacterCastError(f"{cfg.pack_id} has no default cast order")
        missing = set(order) - set(characters)
        if missing:
            raise CharacterCastError("cast order cites unknown characters: " + ", ".join(sorted(missing)))
        assignments = {
            actor_id: characters[order[index % len(order)]]
            for index, actor_id in enumerate(dict.fromkeys(actor_ids))
        }
        return CharacterCast(
            pack_id=cfg.pack_id,
            assignments=assignments,
            rights_status=str(rights.get("status", "unknown")),
            publication_authorized=bool(rights.get("publication_authorized", False)),
        )

    def audio_references(self, pack_id: str) -> tuple[CharacterAudioReference, ...]:
        index = _object(self.index_path)
        entry = next((item for item in index.get("packs", []) if item.get("pack_id") == pack_id), None)
        if entry is None:
            raise CharacterCastError(f"unknown character pack: {pack_id}")
        raw = _object(self.root / str(entry["manifest"]))
        result = []
        for item in raw.get("audio_references", []):
            path = Path(str(item.get("local_path", "")))
            if not path.is_file():
                raise CharacterCastError(f"character audio reference missing: {path}")
            result.append(CharacterAudioReference(
                audio_id=str(item["audio_id"]),
                role=str(item.get("role", "research_reference")),
                local_path=path,
                source_url=str(item.get("source_url", "")),
            ))
        return tuple(result)


class CharacterMaterialRegistry:
    """Local cache from content hash to TokenRouter ``asset://`` material URL."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.home() / ".sceneactor" / "character_materials.json")

    def prepare(
        self,
        client: TokenRouterVideoClient,
        identity: CharacterIdentity,
    ) -> ReferenceMedia:
        cache = _object(self.path) if self.path.is_file() else {}
        key = f"{client.config.base_url}|{identity.content_hash}"
        asset_url = str(cache.get(key, ""))
        if not asset_url:
            public_url = client.upload_reference(identity.local_path)
            asset_url = client.register_material(
                public_url,
                name=f"sceneactor-{identity.code}",
                group="sceneactor-character-cast",
                real_person=identity.material_real_person,
            )
            cache[key] = asset_url
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        return ReferenceMedia(url=asset_url, category="image", role="identity_anchor")


def _identity(raw: Mapping[str, Any]) -> CharacterIdentity:
    image = raw.get("image") or {}
    path = Path(str(image.get("local_path", "")))
    if not path.is_file():
        raise CharacterCastError(f"character image missing: {path}")
    return CharacterIdentity(
        character_id=str(raw["character_id"]),
        code=str(raw["code"]),
        official_name_ja=str(raw.get("official_name_ja", "")),
        display_name_zh=str(raw.get("display_name_zh", "")),
        content_hash=str(image["content_hash"]),
        local_path=path,
        source_url=str(raw.get("source_url", "")),
        material_real_person=bool(raw.get("material_real_person", False)),
    )


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CharacterCastError(f"cannot read character data {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CharacterCastError(f"character data must be a JSON object: {path}")
    return value
