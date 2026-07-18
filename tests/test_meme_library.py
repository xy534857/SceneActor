"""Offline tests for the TemplateStudio meme library bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneactor.adapters.meme_library import (
    MemeLibrary,
    MemeLibraryError,
    MemeReferencePack,
    MemeAnchor,
    MemeAsset,
)


@pytest.fixture()
def library_root(tmp_path: Path) -> Path:
    """Minimal synthetic library matching TemplateStudio's published layout."""
    media = tmp_path / "media"
    media.mkdir()
    clip = media / "clip.mp4"
    clip.write_bytes(b"fake video bytes")
    sheet = media / "sheet.jpg"
    sheet.write_bytes(b"fake image bytes")

    package_dir = tmp_path / "test-meme"
    package_dir.mkdir()
    manifest = {
        "template_id": "meme.test-meme",
        "template_version": "1.0.0-research",
        "template_hash": "hash-1",
        "performance_mode": "actor_led",
        "visual_asset_ids": ["test-meme:video"],
        "temporal_anchors": [
            {
                "anchor_id": "beat:second",
                "kind": "reaction",
                "order": 1,
                "instruction": "反转",
                "soft_time_hint": "reveal",
                "asset_id": "test-meme:video",
                "required": True,
            },
            {
                "anchor_id": "beat:first",
                "kind": "action_keyframe",
                "order": 0,
                "instruction": "建立预期",
                "soft_time_hint": "opening",
                "asset_id": "test-meme:video",
                "required": True,
            },
        ],
        "reference_assets": [
            {
                "asset_id": "test-meme:video",
                "kind": "video",
                "content_hash": "c1",
                "storage_uri": "media://sha256/c1",
                "local_path": str(clip),
                "size_bytes": 16,
            },
            {
                "asset_id": "test-meme:sheet",
                "kind": "visual",
                "content_hash": "c2",
                "storage_uri": "media://sha256/c2",
                "local_path": str(sheet),
                "size_bytes": 16,
            },
        ],
        "rights_status": "research_only",
        "publication_authorized": False,
    }
    (package_dir / "seedance_assets.json").write_text(json.dumps(manifest), encoding="utf-8")
    index = {
        "database_version": "1.0.0-research",
        "packages": [
            {
                "package_id": "meme.test-meme",
                "path": "test-meme",
                "type": "narrative_grammar",
                "status": "candidate_research_only",
                "contract_hash": "hash-1",
                "reference_asset_count": 2,
            }
        ],
    }
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def pack(library_root: Path) -> MemeReferencePack:
    return MemeLibrary(library_root).load("meme.test-meme")


class TestLibrary:
    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(MemeLibraryError, match="index not found"):
            MemeLibrary(tmp_path / "nope")

    def test_lists_packages(self, library_root: Path) -> None:
        packages = MemeLibrary(library_root).packages()
        assert [item.package_id for item in packages] == ["meme.test-meme"]
        assert packages[0].reference_asset_count == 2

    def test_unknown_package(self, library_root: Path) -> None:
        with pytest.raises(MemeLibraryError, match="unknown meme package"):
            MemeLibrary(library_root).load("meme.nope")

    def test_hash_mismatch_detected(self, library_root: Path) -> None:
        index_path = library_root / "index.json"
        index = json.loads(index_path.read_text())
        index["packages"][0]["contract_hash"] = "stale"
        index_path.write_text(json.dumps(index))
        with pytest.raises(MemeLibraryError, match="does not match index"):
            MemeLibrary(library_root).load("meme.test-meme")

    def test_missing_media_detected(self, library_root: Path) -> None:
        (library_root / "media" / "clip.mp4").unlink()
        with pytest.raises(MemeLibraryError, match="missing on disk"):
            MemeLibrary(library_root).load("meme.test-meme")


class TestPackProjection:
    def test_anchor_prompt_ordered(self, pack: MemeReferencePack) -> None:
        prompt = pack.anchor_prompt()
        assert prompt.index("建立预期") < prompt.index("反转")
        assert "meme.test-meme" in prompt
        assert "(timing: opening)" in prompt

    def test_reference_bindings_for_compiler(self, pack: MemeReferencePack) -> None:
        bindings = pack.reference_bindings("npc-1", role="scene_style")
        assert [item.asset_id for item in bindings] == ["test-meme:video", "test-meme:sheet"]
        assert all(item.entity_id == "npc-1" and item.role == "scene_style" for item in bindings)

    def test_reference_media_via_uploader(self, pack: MemeReferencePack) -> None:
        uploaded: list[Path] = []

        def fake_uploader(path: Path) -> str:
            uploaded.append(path)
            return f"https://bucket/{path.name}"

        media = pack.reference_media(fake_uploader)
        assert [item.category for item in media] == ["video", "image"]
        assert media[0].url == "https://bucket/clip.mp4"
        assert len(uploaded) == 2

    def test_reference_media_kind_filter(self, pack: MemeReferencePack) -> None:
        media = pack.reference_media(lambda p: "https://x", kinds=("video",))
        assert len(media) == 1 and media[0].category == "video"

    def test_publication_guard(self, pack: MemeReferencePack) -> None:
        with pytest.raises(MemeLibraryError, match="not authorized for"):
            pack.require_publication_authorized()

    def test_unknown_asset_lookup(self, pack: MemeReferencePack) -> None:
        assert pack.asset("test-meme:video").kind == "video"
        with pytest.raises(MemeLibraryError, match="unknown asset"):
            pack.asset("test-meme:nope")

    def test_unmapped_kind_rejected(self) -> None:
        asset = MemeAsset(asset_id="x", kind="hologram", content_hash="h", local_path=Path("/tmp/x"))
        with pytest.raises(MemeLibraryError, match="unmapped asset kind"):
            _ = asset.category
