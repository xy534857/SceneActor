"""Reusable skit-to-video pipeline (the "chiikawa burger debate" flow).

Declarative project JSON in, seedance shot tasks out. This module holds the
pure, testable core; network I/O stays in ``TokenRouterVideoClient`` and the
CLI driver (``scripts/run_skit_video.py``).

Pipeline stages (driver orchestrates, this module plans):

1. cast      — characters with portrait anchors + clean voice references
2. storyboard — shots (<=15s) with camera/action/dialogue per shot
3. prompts   — one deterministic prompt per shot from project + shot fields
4. tasks     — provider payloads (identity/scene/voice references attached)
5. verify    — transcript similarity + voice match + frame review (driver)
6. assemble  — ffmpeg concat list (driver runs ffmpeg)

Design rules learned the hard way:
- identity anchoring MUST be image references (one portrait per on-screen
  character), never prose descriptions alone;
- voice references MUST be separated vocals (no BGM), loudness-normalized;
- ``first_frame`` cannot be mixed with other reference media on tencent-vod;
- VS 2.0 durations: integers in [4, 15].
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters.tokenrouter import ReferenceMedia

MIN_SHOT_SECONDS = 4
MAX_SHOT_SECONDS = 15


class SkitProjectError(ValueError):
    """Raised when a skit project file violates the pipeline contract."""


@dataclass(frozen=True)
class SkitCharacter:
    """One performer: identity portrait + voice reference + voice description."""

    code: str
    identity_desc: str
    portrait: str  # upload key or local path of the single-character portrait
    voice_ref: str  # upload key or local path of the CLEAN (vocals-only) wav
    voice_desc: str  # audible register description, e.g. "low gruff cartoon voice"


@dataclass(frozen=True)
class SkitShot:
    shot_id: str
    duration: int
    speaker: str  # character code
    camera: str
    action: str
    line: str
    in_frame: tuple[str, ...] = ()  # character codes visible; default: speaker only
    extra_constraints: str = ""

    def visible(self) -> tuple[str, ...]:
        return self.in_frame or (self.speaker,)


@dataclass(frozen=True)
class SkitProject:
    project_id: str
    language: str  # "zh" | "en" — dialogue language
    style: str
    stage: str
    scene_ref: str  # upload key / path of master stage image
    characters: Mapping[str, SkitCharacter]
    shots: tuple[SkitShot, ...]
    resolution: str = "720P"
    aspect_ratio: str = "16:9"
    model: str = "VS"
    version: str = "2.0"
    strict_no_humans: bool = True
    strict_no_subtitles: bool = True

    @classmethod
    def load(cls, path: Path | str) -> "SkitProject":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SkitProject":
        characters = {}
        for item in raw.get("characters", []):
            ch = SkitCharacter(
                code=str(item["code"]),
                identity_desc=str(item["identity_desc"]),
                portrait=str(item["portrait"]),
                voice_ref=str(item["voice_ref"]),
                voice_desc=str(item["voice_desc"]),
            )
            characters[ch.code] = ch
        if not characters:
            raise SkitProjectError("project has no characters")
        shots = []
        for item in raw.get("shots", []):
            shot = SkitShot(
                shot_id=str(item["shot_id"]),
                duration=int(item["duration"]),
                speaker=str(item["speaker"]),
                camera=str(item["camera"]),
                action=str(item["action"]),
                line=str(item["line"]),
                in_frame=tuple(str(c) for c in item.get("in_frame", [])),
                extra_constraints=str(item.get("extra_constraints", "")),
            )
            shots.append(shot)
        if not shots:
            raise SkitProjectError("project has no shots")
        project = cls(
            project_id=str(raw["project_id"]),
            language=str(raw.get("language", "en")),
            style=str(raw["style"]),
            stage=str(raw["stage"]),
            scene_ref=str(raw["scene_ref"]),
            characters=characters,
            shots=tuple(shots),
            resolution=str(raw.get("resolution", "720P")),
            aspect_ratio=str(raw.get("aspect_ratio", "16:9")),
            model=str(raw.get("model", "VS")),
            version=str(raw.get("version", "2.0")),
            strict_no_humans=bool(raw.get("strict_no_humans", True)),
            strict_no_subtitles=bool(raw.get("strict_no_subtitles", True)),
        )
        project.validate()
        return project

    def validate(self) -> None:
        seen: set[str] = set()
        for shot in self.shots:
            if shot.shot_id in seen:
                raise SkitProjectError(f"duplicate shot_id: {shot.shot_id}")
            seen.add(shot.shot_id)
            if not (MIN_SHOT_SECONDS <= shot.duration <= MAX_SHOT_SECONDS):
                raise SkitProjectError(
                    f"{shot.shot_id}: duration {shot.duration}s outside "
                    f"[{MIN_SHOT_SECONDS}, {MAX_SHOT_SECONDS}]"
                )
            if shot.speaker not in self.characters:
                raise SkitProjectError(f"{shot.shot_id}: unknown speaker {shot.speaker!r}")
            for code in shot.visible():
                if code not in self.characters:
                    raise SkitProjectError(f"{shot.shot_id}: unknown in_frame character {code!r}")
            if shot.speaker not in shot.visible():
                raise SkitProjectError(f"{shot.shot_id}: speaker must be in_frame")
            if not shot.line.strip():
                raise SkitProjectError(f"{shot.shot_id}: empty dialogue line")


_LANG_NAME = {"zh": "Mandarin Chinese", "en": "English", "ja": "Japanese"}


def build_shot_prompt(project: SkitProject, shot: SkitShot) -> str:
    """Deterministic prompt assembly; every consistency rule lives here."""
    speaker = project.characters[shot.speaker]
    lang = _LANG_NAME.get(project.language, project.language)
    others = [c for c in shot.visible() if c != shot.speaker]
    others_txt = ""
    if others:
        described = "; ".join(project.characters[c].identity_desc for c in others)
        others_txt = f" Also visible, NOT speaking: {described}."
    quote = "「{}」" if project.language == "zh" else '"{}"'
    strict: list[str] = []
    if project.strict_no_humans:
        strict.append(
            "NO human characters anywhere; only the referenced characters may appear"
        )
    if project.strict_no_subtitles:
        strict.append("NO subtitles, NO captions, NO text overlays on the frame")
    strict_txt = ("STRICT: " + "; ".join(strict) + ".\n") if strict else ""
    extra = (shot.extra_constraints + "\n") if shot.extra_constraints else ""
    return (
        "IDENTITY: the attached portrait reference image(s) define each character's "
        "EXACT appearance — head shape, colors, outfit, markings, proportions. "
        "Reproduce them faithfully; do not redesign, do not invent extra characters.\n"
        f"STAGE: {project.stage}\n"
        f"SHOT: {shot.camera}.\n"
        f"SPEAKING CHARACTER: {speaker.identity_desc}.{others_txt}\n"
        f"ACTION: {shot.action}. Mouth movements sync to the dialogue. "
        "Subtle idle motion otherwise; steady TV framing.\n"
        f"DIALOGUE ({lang}, spoken aloud in {speaker.voice_desc}, cloned from the "
        f"reference audio — match its timbre exactly): {quote.format(shot.line)}\n"
        f"{strict_txt}{extra}"
        f"STYLE: {project.style} "
        "Audio: only the character's voice plus faint room tone; no music."
    )


def build_shot_references(
    project: SkitProject,
    shot: SkitShot,
    uploads: Mapping[str, str],
) -> tuple[ReferenceMedia, ...]:
    """identity anchors for every visible character + scene style + speaker voice.

    ``uploads`` maps portrait/voice/scene keys (or paths) to public URLs.
    """
    refs: list[ReferenceMedia] = []
    for code in shot.visible():
        ch = project.characters[code]
        refs.append(ReferenceMedia(url=_resolve(uploads, ch.portrait), category="Image", role="identity_anchor"))
    refs.append(ReferenceMedia(url=_resolve(uploads, project.scene_ref), category="Image", role="scene_style"))
    speaker = project.characters[shot.speaker]
    refs.append(ReferenceMedia(url=_resolve(uploads, speaker.voice_ref), category="Audio", role="reference"))
    return tuple(refs)


def _resolve(uploads: Mapping[str, str], key: str) -> str:
    if key in uploads:
        return uploads[key]
    if key.startswith(("http://", "https://")):
        return key
    raise SkitProjectError(f"no uploaded URL for reference {key!r}")


def plan_tasks(
    project: SkitProject,
    uploads: Mapping[str, str],
) -> list[dict[str, Any]]:
    """One provider-ready plan entry per shot (payload built by the client)."""
    plan = []
    for shot in project.shots:
        plan.append(
            {
                "shot_id": shot.shot_id,
                "prompt": build_shot_prompt(project, shot),
                "duration": shot.duration,
                "resolution": project.resolution,
                "aspect_ratio": project.aspect_ratio,
                "model": project.model,
                "version": project.version,
                "references": build_shot_references(project, shot, uploads),
            }
        )
    return plan


def upload_keys(project: SkitProject) -> tuple[str, ...]:
    """Every asset key the driver must upload before ``plan_tasks``."""
    keys: list[str] = [project.scene_ref]
    for ch in project.characters.values():
        keys.append(ch.portrait)
        keys.append(ch.voice_ref)
    # de-dup, keep order
    return tuple(dict.fromkeys(keys))


def concat_manifest(project: SkitProject, clips_dir: Path, tag_by_shot: Mapping[str, str]) -> str:
    """ffmpeg concat file body honoring per-shot take tags (iteration output)."""
    lines = []
    for shot in project.shots:
        tag = tag_by_shot.get(shot.shot_id, "v1")
        lines.append(f"file '{clips_dir / f'{shot.shot_id}_{tag}.mp4'}'")
    return "\n".join(lines) + "\n"
