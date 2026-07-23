"""Offline tests for the skit-to-video pipeline planner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneactor.skit_pipeline import (
    SkitProject,
    SkitProjectError,
    assemble_command,
    build_shot_prompt,
    build_shot_references,
    build_still_prompts,
    concat_manifest,
    plan_tasks,
    upload_keys,
)

REPO = Path(__file__).resolve().parents[1]


def _raw(**over) -> dict:
    raw = {
        "project_id": "skit.test",
        "language": "zh",
        "style": "flat chibi style.",
        "stage": "a tiny stage.",
        "scene_ref": "assets/stage.png",
        "characters": [
            {"code": "a", "identity_desc": "RABBIT (red tie)",
             "portrait": "assets/a.png", "voice_ref": "assets/a.wav",
             "voice_desc": "squeaky voice"},
            {"code": "b", "identity_desc": "OTTER (blue tie)",
             "portrait": "assets/b.png", "voice_ref": "assets/b.wav",
             "voice_desc": "low gruff voice"},
        ],
        "shots": [
            {"shot_id": "s01", "duration": 8, "speaker": "a",
             "camera": "close-up", "action": "waves", "line": "你好。"},
            {"shot_id": "s02", "duration": 15, "speaker": "b",
             "camera": "wide", "action": "nods", "line": "再见。",
             "in_frame": ["b", "a"]},
        ],
    }
    raw.update(over)
    return raw


UPLOADS = {
    "assets/stage.png": "https://x/stage.png",
    "assets/a.png": "https://x/a.png",
    "assets/b.png": "https://x/b.png",
    "assets/a.wav": "https://x/a.wav",
    "assets/b.wav": "https://x/b.wav",
}


class TestProjectContract:
    def test_loads_valid_project(self) -> None:
        p = SkitProject.from_mapping(_raw())
        assert p.project_id == "skit.test"
        assert len(p.shots) == 2

    def test_duration_bounds(self) -> None:
        for bad in (3, 16, 0):
            raw = _raw()
            raw["shots"][0]["duration"] = bad
            with pytest.raises(SkitProjectError, match="duration"):
                SkitProject.from_mapping(raw)

    def test_unknown_speaker_rejected(self) -> None:
        raw = _raw()
        raw["shots"][0]["speaker"] = "ghost"
        with pytest.raises(SkitProjectError, match="unknown speaker"):
            SkitProject.from_mapping(raw)

    def test_speaker_must_be_in_frame(self) -> None:
        raw = _raw()
        raw["shots"][1]["in_frame"] = ["a"]
        with pytest.raises(SkitProjectError, match="in_frame"):
            SkitProject.from_mapping(raw)

    def test_duplicate_shot_ids_rejected(self) -> None:
        raw = _raw()
        raw["shots"][1]["shot_id"] = "s01"
        with pytest.raises(SkitProjectError, match="duplicate"):
            SkitProject.from_mapping(raw)

    def test_empty_line_rejected(self) -> None:
        raw = _raw()
        raw["shots"][0]["line"] = "  "
        with pytest.raises(SkitProjectError, match="empty dialogue"):
            SkitProject.from_mapping(raw)


class TestPromptAssembly:
    def test_prompt_carries_all_consistency_rules(self) -> None:
        p = SkitProject.from_mapping(_raw())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "IDENTITY" in prompt
        assert "NO human characters" in prompt
        assert "NO subtitles" in prompt
        assert "Mandarin Chinese" in prompt
        assert "「你好。」" in prompt
        assert "squeaky voice" in prompt

    def test_non_speaking_characters_described(self) -> None:
        p = SkitProject.from_mapping(_raw())
        prompt = build_shot_prompt(p, p.shots[1])
        assert "NOT speaking" in prompt
        assert "RABBIT (red tie)" in prompt

    def test_english_quotes(self) -> None:
        raw = _raw(language="en")
        raw["shots"][0]["line"] = "Hello."
        p = SkitProject.from_mapping(raw)
        assert '"Hello."' in build_shot_prompt(p, p.shots[0])

    def test_strict_flags_can_be_disabled(self) -> None:
        p = SkitProject.from_mapping(_raw(strict_no_humans=False, strict_no_subtitles=False))
        prompt = build_shot_prompt(p, p.shots[0])
        assert "STRICT" not in prompt


class TestReferencePlan:
    def test_one_identity_anchor_per_visible_character(self) -> None:
        p = SkitProject.from_mapping(_raw())
        refs = build_shot_references(p, p.shots[1], UPLOADS)
        roles = [r.role for r in refs]
        assert roles.count("identity_anchor") == 2
        assert roles.count("scene_style") == 1
        assert [r.category for r in refs if r.role == "reference"] == ["Audio"]

    def test_voice_ref_is_speakers(self) -> None:
        p = SkitProject.from_mapping(_raw())
        refs = build_shot_references(p, p.shots[1], UPLOADS)
        audio = [r for r in refs if r.category == "Audio"][0]
        assert audio.url == "https://x/b.wav"

    def test_missing_upload_raises(self) -> None:
        p = SkitProject.from_mapping(_raw())
        with pytest.raises(SkitProjectError, match="no uploaded URL"):
            build_shot_references(p, p.shots[0], {})

class TestLongVideoConsistencyLayer:
    def test_time_beats_and_end_state_in_prompt(self) -> None:
        raw = _raw()
        raw["shots"][0]["time_beats"] = ["0-3秒：抬手", "3-8秒：放下"]
        raw["shots"][0]["end_state"] = "手放回桌面，视线看向对面。"
        p = SkitProject.from_mapping(raw)
        prompt = build_shot_prompt(p, p.shots[0])
        assert "TIMELINE: 0-3秒：抬手 3-8秒：放下" in prompt
        assert "END STATE" in prompt and "手放回桌面" in prompt

    def test_chain_adds_continuity_clause_and_ref(self) -> None:
        raw = _raw()
        raw["shots"][0]["end_state"] = "角色a的手放回桌面。"
        raw["shots"][1]["chain_from_previous"] = True
        p = SkitProject.from_mapping(raw)
        prompt = build_shot_prompt(p, p.shots[1])
        assert "CONTINUITY" in prompt and "previous shot" in prompt
        refs = build_shot_references(p, p.shots[1], UPLOADS, prev_last_frame="https://x/last.jpg")
        assert refs[-1].url == "https://x/last.jpg"
        assert refs[-1].role == "identity_anchor"

    def test_chain_without_frame_adds_no_ref(self) -> None:
        raw = _raw()
        raw["shots"][0]["end_state"] = "角色a的手放回桌面。"
        raw["shots"][1]["chain_from_previous"] = True
        p = SkitProject.from_mapping(raw)
        refs = build_shot_references(p, p.shots[1], UPLOADS)
        assert all(r.url != "https://x/last.jpg" for r in refs)

    def test_plain_shot_has_no_new_sections(self) -> None:
        p = SkitProject.from_mapping(_raw())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "TIMELINE" not in prompt
        assert "END STATE" not in prompt
        assert "CONTINUITY" not in prompt


class TestConsistencyContracts:
    def _raw_with_contracts(self) -> dict:
        raw = _raw()
        raw["props"] = [
            {"prop_id": "umbrella", "desc": "暖黄色油纸伞", "count": 1,
             "holder": "a.right_hand", "persistent_state": "伞面滴水、微微歪斜"},
            {"prop_id": "platter", "desc": "银色汉堡托盘", "location": "左侧讲台台面"},
        ]
        raw["continuity"] = {
            "spatial_layout": ["角色a始终在画面左侧讲台后", "角色b始终在右侧讲台后"],
            "environment": ["背景大屏始终显示INFLATION折线图"],
            "character_states": ["两个角色整场不离开各自讲台"],
        }
        raw["shots"][0]["props_in_shot"] = ["umbrella"]
        raw["shots"][0]["pose_contract"] = ["身体重心始终压在讲台后沿，不后退"]
        raw["shots"][0]["gaze_target"] = "对面讲台的b"
        raw["shots"][1]["shot_delta"] = ["角色b从右侧讲台走到画面中央"]
        return raw

    def test_stable_facts_injected(self) -> None:
        p = SkitProject.from_mapping(self._raw_with_contracts())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "STABLE FACTS" in prompt
        assert "角色a始终在画面左侧讲台后" in prompt
        assert "INFLATION" in prompt

    def test_prop_registry_uniqueness_and_holder(self) -> None:
        p = SkitProject.from_mapping(self._raw_with_contracts())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "PROPS" in prompt
        assert "exactly 1x 暖黄色油纸伞" in prompt
        assert "held by a.right_hand" in prompt
        assert "never duplicated, never teleported" in prompt

    def test_pose_and_gaze_contracts_injected(self) -> None:
        p = SkitProject.from_mapping(self._raw_with_contracts())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "POSE CONTRACT" in prompt and "身体重心始终压在讲台后沿" in prompt
        assert "GAZE" in prompt and "对面讲台的b" in prompt

    def test_shot_delta_suspends_conflicting_fact(self) -> None:
        p = SkitProject.from_mapping(self._raw_with_contracts())
        prompt = build_shot_prompt(p, p.shots[1])
        assert "SHOT DELTA" in prompt
        assert "角色b从右侧讲台走到画面中央" in prompt
        # the stable fact about b staying at the right lectern must be
        # suspended for this shot — one prompt must not contradict itself
        assert "角色b始终在右侧讲台后" not in prompt
        # unrelated facts survive
        assert "角色a始终在画面左侧讲台后" in prompt

    def test_unknown_prop_rejected(self) -> None:
        raw = self._raw_with_contracts()
        raw["shots"][0]["props_in_shot"] = ["ghost_prop"]
        with pytest.raises(SkitProjectError, match="unknown prop"):
            SkitProject.from_mapping(raw)

    def test_prop_holder_must_be_character(self) -> None:
        raw = self._raw_with_contracts()
        raw["props"][0]["holder"] = "nobody.left_hand"
        with pytest.raises(SkitProjectError, match="not a character"):
            SkitProject.from_mapping(raw)

    def test_chain_requires_predecessor_end_state(self) -> None:
        raw = self._raw_with_contracts()
        raw["shots"][1]["chain_from_previous"] = True
        # shot[0] has no end_state -> must be rejected
        with pytest.raises(SkitProjectError, match="end_state required"):
            SkitProject.from_mapping(raw)

    def test_first_shot_cannot_chain(self) -> None:
        raw = self._raw_with_contracts()
        raw["shots"][0]["chain_from_previous"] = True
        with pytest.raises(SkitProjectError, match="first shot cannot chain"):
            SkitProject.from_mapping(raw)


class TestXyzStillAnchorsAndTransitions:
    def _raw_stills(self) -> dict:
        raw = _raw()
        raw["shots"][0]["start_state"] = "角色a站在左讲台后，手刚抬起。"
        raw["shots"][0]["end_state"] = "角色a的手掌拍在讲台上。"
        raw["shots"][0]["anchor_stills"] = "first_last"
        raw["shots"][1]["transition_in"] = "dissolve"
        raw["shots"][1]["transition_duration"] = 0.8
        return raw

    def test_still_prompts_generated(self) -> None:
        p = SkitProject.from_mapping(self._raw_stills())
        stills = build_still_prompts(p, p.shots[0])
        assert set(stills) == {"first", "last"}
        assert "手刚抬起" in stills["first"]
        assert "拍在讲台上" in stills["last"]
        assert "NO panels" in stills["first"] and "NO grids" in stills["first"]

    def test_no_stills_for_plain_shot(self) -> None:
        p = SkitProject.from_mapping(_raw())
        assert build_still_prompts(p, p.shots[0]) == {}

    def test_still_urls_attached_as_scene_style(self) -> None:
        p = SkitProject.from_mapping(self._raw_stills())
        refs = build_shot_references(
            p, p.shots[0], UPLOADS,
            still_first="https://x/s1_first.png", still_last="https://x/s1_last.png")
        styles = [r.url for r in refs if r.role == "scene_style"]
        assert "https://x/s1_first.png" in styles and "https://x/s1_last.png" in styles

    def test_composition_clause_in_prompt(self) -> None:
        p = SkitProject.from_mapping(self._raw_stills())
        prompt = build_shot_prompt(p, p.shots[0])
        assert "COMPOSITION" in prompt
        assert "START STATE" in prompt

    def test_anchor_stills_requires_states(self) -> None:
        raw = _raw()
        raw["shots"][0]["anchor_stills"] = "first"
        with pytest.raises(SkitProjectError, match="needs start_state"):
            SkitProject.from_mapping(raw)

    def test_chained_shot_cannot_have_stills(self) -> None:
        raw = self._raw_stills()
        raw["shots"][1]["chain_from_previous"] = True
        raw["shots"][1]["anchor_stills"] = "first"
        raw["shots"][1]["start_state"] = "x"
        with pytest.raises(SkitProjectError, match="chained shots inherit"):
            SkitProject.from_mapping(raw)

    def test_bad_transition_rejected(self) -> None:
        raw = _raw()
        raw["shots"][1]["transition_in"] = "wipe"
        with pytest.raises(SkitProjectError, match="transition_in"):
            SkitProject.from_mapping(raw)

    def test_assemble_command_xfade_and_cut(self) -> None:
        p = SkitProject.from_mapping(self._raw_stills())
        cmd = assemble_command(
            p, Path("/tmp/clips"), {}, {"s01": 8.2, "s02": 15.1}, Path("/tmp/out.mp4"))
        joined = " ".join(cmd)
        assert "xfade=transition=fade:duration=0.8" in joined
        assert "offset=7.400" in joined  # 8.2 - 0.8
        assert "acrossfade=d=0.8" in joined

    def test_assemble_command_flash(self) -> None:
        raw = self._raw_stills()
        raw["shots"][1]["transition_in"] = "flash"
        p = SkitProject.from_mapping(raw)
        cmd = assemble_command(p, Path("/c"), {}, {"s01": 8.0, "s02": 15.0}, Path("/o.mp4"))
        assert "fadewhite" in " ".join(cmd)
    def test_upload_keys_deduplicated(self) -> None:
        p = SkitProject.from_mapping(_raw())
        keys = upload_keys(p)
        assert len(keys) == len(set(keys)) == 5


class TestPlanAndAssemble:
    def test_plan_covers_all_shots(self) -> None:
        p = SkitProject.from_mapping(_raw())
        plan = plan_tasks(p, UPLOADS)
        assert [item["shot_id"] for item in plan] == ["s01", "s02"]
        assert all(item["model"] == "VS" for item in plan)

    def test_concat_manifest_honors_take_tags(self) -> None:
        p = SkitProject.from_mapping(_raw())
        body = concat_manifest(p, Path("/tmp/clips"), {"s02": "v3"})
        assert "s01_v1.mp4" in body and "s02_v3.mp4" in body


class TestShippedExample:
    def test_burger_debate_zh_project_is_valid(self) -> None:
        path = REPO / "examples/skits/burger_debate_zh/project.json"
        project = SkitProject.load(path)
        assert len(project.shots) == 14
        assert set(project.characters) == {"usagi", "racco", "hachi"}
        for key in upload_keys(project):
            assert (path.parent / key).is_file(), f"missing asset {key}"
