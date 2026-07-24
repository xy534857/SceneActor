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
    ref: str = ""  # optional prop model-sheet image key; attached as a visual reference when the prop is in shot


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
    # -- xyz-style shot structure: JSON storyboard + still-frame anchors.
    #    NO multi-panel storyboard images — every visual anchor is a single
    #    per-shot still generated from character portraits + scene sheet.
    time_beats: tuple[str, ...] = ()  # e.g. "0-3秒：…" lines for multi-phase shots
    start_state: str = ""  # first-frame story anchor: poised-to-act state (NOT mid-action)
    end_state: str = ""  # last-frame story anchor: the reached state (NOT "maybe")
    anchor_stills: str = "none"  # none | first | first_last — generate still refs for this shot
    chain_from_previous: bool = False  # start from previous shot's real last frame
    transition_in: str = "cut"  # cut | dissolve | flash — editing joint FROM previous shot
    transition_duration: float = 0.5  # seconds, used by dissolve/flash
    # -- blocking & spatial contracts --
    props_in_shot: tuple[str, ...] = ()  # prop_ids visible in this shot
    pose_contract: tuple[str, ...] = ()  # physical support relations that MUST hold
    gaze_target: str = ""  # who/where the speaker looks: "对面讲台的水獭"
    shot_delta: tuple[str, ...] = ()  # the ONLY changes this shot may make
    # -- multi-location formats (e.g. split-screen voice-call skits) --
    stage_override: str = ""  # per-shot stage text; empty = project.stage
    scene_ref_override: str = ""  # per-shot scene sheet key; empty = project.scene_ref
    portrait_overrides: Mapping[str, str] = field(default_factory=dict)  # code -> portrait key (e.g. headset-on variant)

    def stage_text(self, project: "SkitProject") -> str:
        return self.stage_override or project.stage

    def scene_key(self, project: "SkitProject") -> str:
        return self.scene_ref_override or project.scene_ref

    def portrait_key(self, project: "SkitProject", code: str) -> str:
        return self.portrait_overrides.get(code) or project.characters[code].portrait

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
                ref=str(item.get("ref", "")),
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
                start_state=str(item.get("start_state", "")),
                end_state=str(item.get("end_state", "")),
                anchor_stills=str(item.get("anchor_stills", "none")),
                chain_from_previous=bool(item.get("chain_from_previous", False)),
                transition_in=str(item.get("transition_in", "cut")),
                transition_duration=float(item.get("transition_duration", 0.5)),
                props_in_shot=tuple(str(p) for p in item.get("props_in_shot", [])),
                pose_contract=tuple(str(p) for p in item.get("pose_contract", [])),
                gaze_target=str(item.get("gaze_target", "")),
                shot_delta=tuple(str(d) for d in item.get("shot_delta", [])),
                stage_override=str(item.get("stage_override", "")),
                scene_ref_override=str(item.get("scene_ref_override", "")),
                portrait_overrides={str(k): str(v) for k, v in dict(item.get("portrait_overrides", {})).items()},
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
            if shot.anchor_stills not in ("none", "first", "first_last"):
                raise SkitProjectError(
                    f"{shot.shot_id}: anchor_stills must be none|first|first_last"
                )
            if shot.anchor_stills != "none" and not shot.start_state.strip():
                raise SkitProjectError(
                    f"{shot.shot_id}: anchor_stills needs start_state (the still's story anchor)"
                )
            if shot.anchor_stills == "first_last" and not shot.end_state.strip():
                raise SkitProjectError(
                    f"{shot.shot_id}: anchor_stills=first_last needs end_state"
                )
            if shot.transition_in not in ("cut", "dissolve", "flash"):
                raise SkitProjectError(
                    f"{shot.shot_id}: transition_in must be cut|dissolve|flash"
                )
            if shot.chain_from_previous and shot.anchor_stills != "none":
                raise SkitProjectError(
                    f"{shot.shot_id}: chained shots inherit the previous real frame; "
                    "anchor_stills must be none"
                )
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
    start_txt = ""
    if shot.start_state:
        start_txt = f"START STATE: the shot opens on: {shot.start_state}\n"
    still_txt = ""
    if shot.anchor_stills != "none":
        which = ("the attached composition still defines this shot's opening framing"
                 if shot.anchor_stills == "first" else
                 "the two attached composition stills define this shot's opening and closing framing")
        still_txt = (
            f"COMPOSITION: {which} — camera angle, character placement, blocking. "
            "Match the framing; character appearance still follows the portrait references.\n"
        )
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
            if prop.ref:
                bits.append("its EXACT shape/colors are defined by an attached prop reference image — follow it, do not redesign")
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
        f"STAGE: {shot.stage_text(project)}\n"
        f"{facts_txt}{props_txt}"
        f"SHOT: {shot.camera}.\n"
        f"SPEAKING CHARACTER: {speaker.identity_desc}.{others_txt}\n"
        f"ACTION: {shot.action}. Mouth movements sync to the dialogue. "
        "Subtle idle motion otherwise; steady TV framing.\n"
        f"{pose_txt}{gaze_txt}{delta_txt}"
        f"{still_txt}{start_txt}{beats_txt}{end_txt}{chain_txt}"
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


def build_still_prompts(project: SkitProject, shot: SkitShot) -> dict[str, str]:
    """Per-shot still-frame prompts (xyz pattern: single stills, NO panels).

    Returns {} / {"first": …} / {"first","last"} per ``anchor_stills``. Each
    still is generated by the image model from the character portraits + scene
    sheet, so identity and stage stay anchored; the still then locks this
    shot's COMPOSITION when attached to the video task.
    """
    if shot.anchor_stills == "none":
        return {}
    cast = "; ".join(project.characters[c].identity_desc for c in shot.visible())
    base = (
        f"Single cinematic still frame, {project.aspect_ratio} framing. "
        "Characters must match the attached portrait references EXACTLY "
        "(head shape, colors, outfit, markings, proportions); the setting must "
        f"match the attached scene reference.\n"
        f"STAGE: {shot.stage_text(project)}\n"
        f"SHOT: {shot.camera}.\n"
        f"CHARACTERS: {cast}.\n"
        f"STYLE: {project.style} "
        "One single frame — NO panels, NO grids, NO borders, NO annotations, "
        "NO text anywhere.\n"
    )
    stills = {"first": base + f"MOMENT: {shot.start_state}"}
    if shot.anchor_stills == "first_last":
        stills["last"] = base + f"MOMENT: {shot.end_state}"
    return stills


def build_shot_references(
    project: SkitProject,
    shot: SkitShot,
    uploads: Mapping[str, str],
    *,
    prev_last_frame: str = "",
    still_first: str = "",
    still_last: str = "",
) -> tuple[ReferenceMedia, ...]:
    """identity anchors + scene style + speaker voice + optional stills.

    ``still_first``/``still_last``: public URLs of this shot's generated
    composition stills; attached as scene_style references so seedance locks
    framing/blocking to them without inheriting compression artifacts as the
    generation base (unlike first_frame pixel-chaining).
    ``prev_last_frame``: previous shot's real final frame for chained shots;
    attached LAST so the CONTINUITY clause can say "the last attached image".
    """
    refs: list[ReferenceMedia] = []
    for code in shot.visible():
        refs.append(ReferenceMedia(url=_resolve(uploads, shot.portrait_key(project, code)), category="Image", role="identity_anchor"))
    refs.append(ReferenceMedia(url=_resolve(uploads, shot.scene_key(project)), category="Image", role="scene_style"))
    for pid in shot.props_in_shot:
        prop = project.props[pid]
        if prop.ref:
            refs.append(ReferenceMedia(url=_resolve(uploads, prop.ref), category="Image", role="prop"))
    if still_first:
        refs.append(ReferenceMedia(url=still_first, category="Image", role="scene_style"))
    if still_last:
        refs.append(ReferenceMedia(url=still_last, category="Image", role="scene_style"))
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


def concat_manifest(project: SkitProject, clips_dir: Path, tag_by_shot: Mapping[str, str]) -> str:
    """ffmpeg concat file body honoring per-shot take tags (cut-only path)."""
    lines = []
    for shot in project.shots:
        tag = tag_by_shot.get(shot.shot_id, "v1")
        lines.append(f"file '{(clips_dir / f'{shot.shot_id}_{tag}.mp4').resolve()}'")
    return "\n".join(lines) + "\n"


_XFADE = {"dissolve": "fade", "flash": "fadewhite"}


def assemble_command(
    project: SkitProject,
    clips_dir: Path,
    tag_by_shot: Mapping[str, str],
    durations: Mapping[str, float],
    output: Path,
) -> list[str]:
    """Transition-aware ffmpeg command (xyz editing layer: cut/dissolve/flash).

    ``durations``: measured clip duration per shot_id (ffprobe), needed to
    place xfade offsets. All-cut projects should keep using concat_manifest —
    it avoids re-encoding drift entirely.
    """
    shots = project.shots
    inputs: list[str] = []
    for shot in shots:
        tag = tag_by_shot.get(shot.shot_id, "v1")
        inputs += ["-i", str(clips_dir / f"{shot.shot_id}_{tag}.mp4")]
    filters: list[str] = []
    # normalize every input
    for index in range(len(shots)):
        filters.append(
            f"[{index}:v]scale=1280:720,fps=24,settb=AVTB[v{index}];"
            f"[{index}:a]aresample=44100,asetpts=PTS-STARTPTS[a{index}]"
        )
    video, audio = "v0", "a0"
    elapsed = durations[shots[0].shot_id]
    for index, shot in enumerate(shots[1:], start=1):
        nv, na = f"vx{index}", f"ax{index}"
        if shot.transition_in in _XFADE:
            dur = max(0.1, min(shot.transition_duration, 1.5))
            offset = max(0.0, elapsed - dur)
            style = _XFADE[shot.transition_in]
            filters.append(
                f"[{video}][v{index}]xfade=transition={style}:duration={dur}:offset={offset:.3f}[{nv}];"
                f"[{audio}][a{index}]acrossfade=d={dur}[{na}]"
            )
            elapsed = offset + dur + (durations[shot.shot_id] - dur)
        else:  # cut
            filters.append(
                f"[{video}][{audio}][v{index}][a{index}]concat=n=2:v=1:a=1[{nv}][{na}]"
            )
            elapsed += durations[shot.shot_id]
        video, audio = nv, na
    filter_complex = ";".join(filters)
    return [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{video}]", "-map", f"[{audio}]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", str(output),
    ]


def upload_keys(project: SkitProject) -> tuple[str, ...]:
    """Every asset key the driver must upload before ``plan_tasks``."""
    keys: list[str] = [project.scene_ref]
    keys.extend(s.scene_ref_override for s in project.shots if s.scene_ref_override)
    keys.extend(p.ref for p in project.props.values() if p.ref)
    for s in project.shots:
        keys.extend(s.portrait_overrides.values())
    for ch in project.characters.values():
        keys.append(ch.portrait)
        keys.append(ch.voice_ref)
    return tuple(dict.fromkeys(keys))

