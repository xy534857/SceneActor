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
class SkitProp:
    """One UNIQUE scene entity (entity-registry pattern, from xyz-video-skill).

    Declaring a prop here makes three guarantees enter every prompt that shows
    it: uniqueness (count), who holds it and with which hand, and its
    persistent visual state. This is what prevents "two identical umbrellas"
    and "the platter teleports between lecterns".
    """

    prop_id: str
    desc: str  # visual description, e.g. "银色托盘，堆成金字塔的卡通汉堡"
    count: int = 1
    holder: str = ""  # "" = free-standing; else "character_code" or "character_code.right_hand"
    location: str = ""  # spatial anchor when free-standing, e.g. "左侧讲台台面"
    persistent_state: str = ""  # state that never changes unless a shot_delta says so


@dataclass(frozen=True)
class SceneContinuity:
    """Project-level stable facts: the DEFAULT baseline every shot inherits.

    Split into itemized facts (not prose) so each fact can be injected into a
    prompt and checked by the reviewer independently. A shot may only deviate
    from these facts by declaring the change in its ``shot_delta``.
    """

    spatial_layout: tuple[str, ...] = ()  # "兔子始终在画面左侧讲台后，水獭始终在右侧讲台后"
    environment: tuple[str, ...] = ()  # "背景大屏始终显示 INFLATION 折线图"
    character_states: tuple[str, ...] = ()  # "三个角色整场不离开各自的位置"

    def facts(self) -> tuple[str, ...]:
        return (*self.spatial_layout, *self.environment, *self.character_states)


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
    # -- long-video consistency layer (from public seedance skills: timestamp
    #    storyboarding / end-state anchoring / chained continuation) --
    time_beats: tuple[str, ...] = ()  # e.g. "0-3秒：…" lines for multi-phase shots
    end_state: str = ""  # visual landing point; prevents random wandering
    chain_from_previous: bool = False  # start from previous shot's real last frame
    # -- blocking & spatial contracts (xyz-video-skill patterns, hardened) --
    props_in_shot: tuple[str, ...] = ()  # prop_ids visible in this shot
    pose_contract: tuple[str, ...] = ()  # physical support relations that MUST hold
    gaze_target: str = ""  # who/where the speaker looks: "对面讲台的水獭"
    shot_delta: tuple[str, ...] = ()  # the ONLY changes this shot may make

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
    props: Mapping[str, SkitProp] = field(default_factory=dict)
    continuity: SceneContinuity = field(default_factory=SceneContinuity)
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
        props: dict[str, SkitProp] = {}
        for item in raw.get("props", []):
            prop = SkitProp(
                prop_id=str(item["prop_id"]),
                desc=str(item["desc"]),
                count=int(item.get("count", 1)),
                holder=str(item.get("holder", "")),
                location=str(item.get("location", "")),
                persistent_state=str(item.get("persistent_state", "")),
            )
            props[prop.prop_id] = prop
        cont_raw = raw.get("continuity", {})
        continuity = SceneContinuity(
            spatial_layout=tuple(str(f) for f in cont_raw.get("spatial_layout", [])),
            environment=tuple(str(f) for f in cont_raw.get("environment", [])),
            character_states=tuple(str(f) for f in cont_raw.get("character_states", [])),
        )
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
                time_beats=tuple(str(b) for b in item.get("time_beats", [])),
                end_state=str(item.get("end_state", "")),
                chain_from_previous=bool(item.get("chain_from_previous", False)),
                props_in_shot=tuple(str(p) for p in item.get("props_in_shot", [])),
                pose_contract=tuple(str(p) for p in item.get("pose_contract", [])),
                gaze_target=str(item.get("gaze_target", "")),
                shot_delta=tuple(str(d) for d in item.get("shot_delta", [])),
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
            props=props,
            continuity=continuity,
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
            for pid in shot.props_in_shot:
                if pid not in self.props:
                    raise SkitProjectError(f"{shot.shot_id}: unknown prop {pid!r}")
        for prop in self.props.values():
            if prop.holder:
                holder_code = prop.holder.split(".", 1)[0]
                if holder_code not in self.characters:
                    raise SkitProjectError(
                        f"prop {prop.prop_id}: holder {holder_code!r} is not a character"
                    )
        # chained shots need a precise landing point on their predecessor:
        # the previous end_state BECOMES the next first frame.
        for index, shot in enumerate(self.shots):
            if shot.chain_from_previous:
                if index == 0:
                    raise SkitProjectError(f"{shot.shot_id}: first shot cannot chain")
                prev = self.shots[index - 1]
                if not prev.end_state.strip():
                    raise SkitProjectError(
                        f"{prev.shot_id}: end_state required — {shot.shot_id} chains from it"
                    )


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
    beats_txt = ""
    if shot.time_beats:
        beats_txt = "TIMELINE: " + " ".join(shot.time_beats) + "\n"
    end_txt = ""
    if shot.end_state:
        end_txt = (
            f"END STATE: by the last second the frame settles on: {shot.end_state} "
            "Do not wander past this landing point.\n"
        )
    chain_txt = ""
    if shot.chain_from_previous:
        chain_txt = (
            "CONTINUITY: the last attached reference image is the final frame of the "
            "previous shot — this shot continues directly from that exact state: same "
            "positions, same lighting, same props.\n"
        )
    # STABLE FACTS: the inherited baseline (scene continuity, xyz pattern).
    # Items a shot_delta explicitly changes are filtered out for that shot.
    facts = [
        fact for fact in project.continuity.facts()
        if not any(_mentions_same_subject(fact, delta) for delta in shot.shot_delta)
    ]
    facts_txt = ""
    if facts:
        facts_txt = "STABLE FACTS (must hold in this shot):\n" + "".join(
            f"- {fact}\n" for fact in facts)
    props_txt = ""
    if shot.props_in_shot:
        lines = []
        for pid in shot.props_in_shot:
            prop = project.props[pid]
            bits = [f"exactly {prop.count}x {prop.desc}"]
            if prop.holder:
                bits.append(f"held by {prop.holder}")
            if prop.location:
                bits.append(f"fixed at {prop.location}")
            if prop.persistent_state:
                bits.append(f"state: {prop.persistent_state}")
            lines.append("- " + ", ".join(bits) + "; never duplicated, never teleported\n")
        props_txt = "PROPS (unique entities):\n" + "".join(lines)
    pose_txt = ""
    if shot.pose_contract:
        pose_txt = "POSE CONTRACT (body support must hold the whole shot):\n" + "".join(
            f"- {p}\n" for p in shot.pose_contract)
    gaze_txt = ""
    if shot.gaze_target:
        gaze_txt = f"GAZE: the speaker's eyes stay on {shot.gaze_target}.\n"
    delta_txt = ""
    if shot.shot_delta:
        delta_txt = (
            "SHOT DELTA (the ONLY things allowed to change in this shot):\n"
            + "".join(f"- {d}\n" for d in shot.shot_delta)
            + "Everything not listed above stays exactly as established.\n"
        )
    return (
        "IDENTITY: the attached portrait reference image(s) define each character's "
        "EXACT appearance — head shape, colors, outfit, markings, proportions. "
        "Reproduce them faithfully; do not redesign, do not invent extra characters.\n"
        f"STAGE: {project.stage}\n"
        f"{facts_txt}{props_txt}"
        f"SHOT: {shot.camera}.\n"
        f"SPEAKING CHARACTER: {speaker.identity_desc}.{others_txt}\n"
        f"ACTION: {shot.action}. Mouth movements sync to the dialogue. "
        "Subtle idle motion otherwise; steady TV framing.\n"
        f"{pose_txt}{gaze_txt}{delta_txt}"
        f"{beats_txt}{end_txt}{chain_txt}"
        f"DIALOGUE ({lang}, spoken aloud in {speaker.voice_desc}, cloned from the "
        f"reference audio — match its timbre exactly): {quote.format(shot.line)}\n"
        f"{strict_txt}{extra}"
        f"STYLE: {project.style} "
        "Audio: only the character's voice plus faint room tone; no music."
    )


def _mentions_same_subject(fact: str, delta: str) -> bool:
    """A stable fact is suspended when a shot_delta names the same subject.

    CJK trigrams / latin words with a 2-hit threshold: shared generic bigrams
    (讲台/画面) alone must NOT suspend a fact about a different subject, while
    a delta naming the same actor+landmark does. Reviewers still check footage.
    """
    def tokens(text: str) -> set[str]:
        import re as _re
        words = set(_re.findall(r"[a-zA-Z]{3,}", text.lower()))
        cjk = _re.findall(r"[\u4e00-\u9fff0-9a-zA-Z]", text)
        words.update("".join(t) for t in zip(cjk, cjk[1:], cjk[2:]))
        return words
    overlap = tokens(fact) & tokens(delta)
    return len(overlap) >= 2


def build_shot_references(
    project: SkitProject,
    shot: SkitShot,
    uploads: Mapping[str, str],
    *,
    prev_last_frame: str = "",
) -> tuple[ReferenceMedia, ...]:
    """identity anchors for every visible character + scene style + speaker voice.

    ``uploads`` maps portrait/voice/scene keys (or paths) to public URLs.
    ``prev_last_frame``: public URL of the previous shot's real final frame;
    attached LAST when ``shot.chain_from_previous`` so the prompt's CONTINUITY
    clause can reference "the last attached reference image".
    """
    refs: list[ReferenceMedia] = []
    for code in shot.visible():
        ch = project.characters[code]
        refs.append(ReferenceMedia(url=_resolve(uploads, ch.portrait), category="Image", role="identity_anchor"))
    refs.append(ReferenceMedia(url=_resolve(uploads, project.scene_ref), category="Image", role="scene_style"))
    speaker = project.characters[shot.speaker]
    refs.append(ReferenceMedia(url=_resolve(uploads, speaker.voice_ref), category="Audio", role="reference"))
    if shot.chain_from_previous and prev_last_frame:
        refs.append(ReferenceMedia(url=prev_last_frame, category="Image", role="identity_anchor"))
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
