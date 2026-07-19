from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sceneactor.adapters.character_cast import (
    CharacterCastError,
    CharacterMaterialRegistry,
    CharacterReferenceLibrary,
    CharacterSubstitutionConfig,
)


def _library(tmp_path: Path) -> tuple[CharacterReferenceLibrary, CharacterSubstitutionConfig]:
    root = tmp_path / "characters"
    pack_dir = root / "pack"
    pack_dir.mkdir(parents=True)
    chars = []
    order = []
    for code in ("a", "b"):
        image = tmp_path / f"{code}.png"
        image.write_bytes(code.encode())
        voice = tmp_path / f"{code}.wav"
        voice.write_bytes(b"wav" + code.encode())
        character_id = f"cast:{code}"
        order.append(character_id)
        chars.append({
            "character_id": character_id,
            "code": code,
            "official_name_ja": code.upper(),
            "display_name_zh": f"角色{code}",
            "source_url": "https://official.example/chara",
            "reference_role": "identity_anchor",
            "material_real_person": False,
            "image": {
                "content_hash": code * 64,
                "local_path": str(image),
            },
            "audio": {
                "audio_id": f"cast:{code}:voice",
                "role": "official_character_intro_voice",
                "local_path": str(voice),
                "source_url": "https://official.example/pv",
                "voice_clone_allowed": False,
            },
        })
    manifest = {
        "pack_id": "character.test",
        "rights": {"status": "research_only", "publication_authorized": False},
        "default_cast_order": order,
        "characters": chars,
        "audio_references": [],
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({
        "packs": [{"pack_id": "character.test", "manifest": "pack/manifest.json"}]
    }), encoding="utf-8")
    config = CharacterSubstitutionConfig(
        enabled=True,
        pack_id="character.test",
        strategy="round_robin",
        character_order=tuple(order),
        research_only=True,
    )
    return CharacterReferenceLibrary(root), config


def test_round_robin_cast_preserves_actor_ids(tmp_path: Path) -> None:
    library, config = _library(tmp_path)
    cast = library.load_cast(["alice", "bob", "carol", "alice"], config)
    assert cast.character_for("alice").character_id == "cast:a"
    assert cast.character_for("bob").character_id == "cast:b"
    assert cast.character_for("carol").character_id == "cast:a"
    assert [item.entity_id for item in cast.reference_bindings()] == ["alice", "bob", "carol"]
    assert "alice uses A" in cast.prompt_prefix()
    voice = cast.character_for("alice").voice_reference_path
    assert voice is not None and voice.read_bytes() == b"wava"
    assert cast.character_for("bob").voice_reference_url == "https://official.example/pv"


def test_research_pack_publication_guard(tmp_path: Path) -> None:
    library, config = _library(tmp_path)
    with pytest.raises(CharacterCastError, match="publication is not authorized"):
        library.load_cast(["alice"], config).require_publication_authorized()


def test_disabled_config_rejected(tmp_path: Path) -> None:
    library, config = _library(tmp_path)
    disabled = CharacterSubstitutionConfig(False, config.pack_id, config.strategy, config.character_order, True)
    with pytest.raises(CharacterCastError, match="disabled"):
        library.load_cast(["alice"], disabled)


def test_material_registry_registers_once_and_reuses_cache(tmp_path: Path) -> None:
    library, config = _library(tmp_path)
    identity = library.load_cast(["alice"], config).character_for("alice")

    class FakeClient:
        config = SimpleNamespace(base_url="https://router")

        def __init__(self) -> None:
            self.uploads = 0
            self.registrations = 0

        def upload_reference(self, path: Path) -> str:
            self.uploads += 1
            return "https://bucket/a.png"

        def register_material(self, url: str, **kwargs) -> str:
            self.registrations += 1
            assert kwargs["real_person"] is False
            return "asset://a"

    client = FakeClient()
    registry = CharacterMaterialRegistry(tmp_path / "materials.json")
    first = registry.prepare(client, identity)
    second = registry.prepare(client, identity)
    assert first.url == second.url == "asset://a"
    assert first.role == "identity_anchor"
    assert client.uploads == client.registrations == 1
