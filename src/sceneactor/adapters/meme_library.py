"""Meme reference library bridge to TemplateStudio.

Reads TemplateStudio's research meme database (``data/memes``) and projects
each package's ``seedance_assets.json`` manifest into SceneActor adapter
inputs: temporal-anchor prompt guidance plus reference media for video
generation.

TemplateStudio remains the source of truth — this module only reads its
published manifests, never its internals. Point it at a checkout via
``SCENEACTOR_MEME_LIBRARY`` (path to the ``data/memes`` directory) or pass
the root explicitly.

Rights guard: every package carries ``rights_status`` (currently all
``research_only``) and ``publication_authorized``. Both are surfaced on the
loaded pack, and ``require_publication_authorized`` fails fast for any flow
that would publish output.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .seedance import ReferenceBinding
from .tokenrouter import ReferenceMedia

# TemplateStudio asset kind -> tencent-vod FileInfos category.
_KIND_TO_CATEGORY = {
    "video": "video",
    "audio": "audio",
    "visual": "image",
    "image": "image",
}


class MemeLibraryError(RuntimeError):
    """Missing, malformed, or rights-restricted library content."""


def default_library_root() -> Path:
    env = os.environ.get("SCENEACTOR_MEME_LIBRARY", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "Workspace" / "TemplateStudio" / "data" / "memes"


@dataclass(frozen=True)
class MemePackageInfo:
    package_id: str
    path: str
    type: str
    status: str
    contract_hash: str
    reference_asset_count: int


@dataclass(frozen=True)
class MemeAnchor:
    anchor_id: str
    kind: str
    order: int
    instruction: str
    soft_time_hint: str
    asset_id: str
    required: bool


@dataclass(frozen=True)
class MemeAsset:
    asset_id: str
    kind: str
    content_hash: str
    local_path: Path

    @property
    def category(self) -> str:
        try:
            return _KIND_TO_CATEGORY[self.kind]
        except KeyError:
            raise MemeLibraryError(f"unmapped asset kind: {self.kind!r}") from None


@dataclass(frozen=True)
class MemeReferencePack:
    """One loaded meme package, ready for prompt + reference projection."""

    template_id: str
    template_version: str
    template_hash: str
    performance_mode: str
    rights_status: str
    publication_authorized: bool
    anchors: tuple[MemeAnchor, ...]
    assets: tuple[MemeAsset, ...]

    def asset(self, asset_id: str) -> MemeAsset:
        for item in self.assets:
            if item.asset_id == asset_id:
                return item
        raise MemeLibraryError(f"unknown asset in {self.template_id}: {asset_id!r}")

    def anchor_prompt(self) -> str:
        """Compile temporal anchors into shot-guidance text for prompt injection."""
        lines = [f"Meme grammar ({self.template_id}, mode={self.performance_mode}); follow these beats in order:"]
        for anchor in sorted(self.anchors, key=lambda item: item.order):
            hint = f" (timing: {anchor.soft_time_hint})" if anchor.soft_time_hint else ""
            lines.append(f"{anchor.order + 1}. [{anchor.kind}] {anchor.instruction}{hint}")
        return "\n".join(lines)

    def reference_bindings(self, entity_id: str, *, role: str = "reference") -> tuple[ReferenceBinding, ...]:
        """Project reference assets for SeedanceCompiler shot validation."""
        return tuple(
            ReferenceBinding(asset_id=item.asset_id, entity_id=entity_id, role=role)
            for item in self.assets
        )

    def reference_media(
        self,
        uploader: Callable[[Path], str],
        *,
        role: str = "reference",
        kinds: Sequence[str] = ("video", "visual", "image", "audio"),
    ) -> tuple[ReferenceMedia, ...]:
        """Materialize assets as public-URL ReferenceMedia via ``uploader``.

        ``uploader`` maps a local file to a publicly fetchable URL (for the
        tencent-vod route use ``TokenRouterVideoClient.upload_reference``).
        """
        media = []
        for item in self.assets:
            if item.kind not in kinds:
                continue
            media.append(ReferenceMedia(url=uploader(item.local_path), category=item.category, role=role))
        return tuple(media)

    def require_publication_authorized(self) -> None:
        if not self.publication_authorized:
            raise MemeLibraryError(
                f"{self.template_id} is {self.rights_status} and not authorized for "
                "publication; generated output must stay internal/research."
            )


class MemeLibrary:
    """Read-only view over TemplateStudio's meme database directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_library_root()).expanduser()
        self._index_path = self.root / "index.json"
        if not self._index_path.is_file():
            raise MemeLibraryError(
                f"meme library index not found: {self._index_path}. "
                "Set SCENEACTOR_MEME_LIBRARY to TemplateStudio's data/memes directory."
            )

    def packages(self) -> tuple[MemePackageInfo, ...]:
        index = _load_json(self._index_path)
        result = []
        for entry in index.get("packages", []):
            result.append(
                MemePackageInfo(
                    package_id=str(entry.get("package_id", "")),
                    path=str(entry.get("path", "")),
                    type=str(entry.get("type", "")),
                    status=str(entry.get("status", "")),
                    contract_hash=str(entry.get("contract_hash", "")),
                    reference_asset_count=int(entry.get("reference_asset_count", 0)),
                )
            )
        return tuple(result)

    def load(self, package_id: str) -> MemeReferencePack:
        info = next((item for item in self.packages() if item.package_id == package_id), None)
        if info is None:
            known = ", ".join(item.package_id for item in self.packages())
            raise MemeLibraryError(f"unknown meme package {package_id!r}; library has: {known}")
        manifest = _load_json(self.root / info.path / "seedance_assets.json")

        if str(manifest.get("contract_hash", manifest.get("template_hash", ""))) != info.contract_hash:
            raise MemeLibraryError(
                f"{package_id}: seedance manifest hash does not match index contract_hash; "
                "library may be mid-update — re-sync TemplateStudio."
            )

        anchors = tuple(
            MemeAnchor(
                anchor_id=str(anchor["anchor_id"]),
                kind=str(anchor["kind"]),
                order=int(anchor["order"]),
                instruction=str(anchor["instruction"]),
                soft_time_hint=str(anchor.get("soft_time_hint", "")),
                asset_id=str(anchor.get("asset_id", "")),
                required=bool(anchor.get("required", True)),
            )
            for anchor in manifest.get("temporal_anchors", [])
        )
        assets = []
        for entry in manifest.get("reference_assets", []):
            local = Path(str(entry["local_path"]))
            if not local.is_file():
                raise MemeLibraryError(
                    f"{package_id}: reference media missing on disk: {local}. "
                    "TemplateStudio's var/media store is machine-local; re-run its collection."
                )
            assets.append(
                MemeAsset(
                    asset_id=str(entry["asset_id"]),
                    kind=str(entry["kind"]),
                    content_hash=str(entry["content_hash"]),
                    local_path=local,
                )
            )
        return MemeReferencePack(
            template_id=str(manifest.get("template_id", package_id)),
            template_version=str(manifest.get("template_version", "")),
            template_hash=str(manifest.get("template_hash", "")),
            performance_mode=str(manifest.get("performance_mode", "")),
            rights_status=str(manifest.get("rights_status", "unknown")),
            publication_authorized=bool(manifest.get("publication_authorized", False)),
            anchors=anchors,
            assets=tuple(assets),
        )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MemeLibraryError(f"missing library file: {path}") from None
    except json.JSONDecodeError as exc:
        raise MemeLibraryError(f"malformed library file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MemeLibraryError(f"library file must be a JSON object: {path}")
    return value
