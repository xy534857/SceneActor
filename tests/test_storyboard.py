"""Storyboard adapter: splitting, duration, briefs, and review prompts."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sceneactor.adapters.storyboard import (
    FAST_CHARS_PER_SECOND,
    REVIEW_QUESTIONS,
    _tail_key,
    estimate_duration,
    paginate_briefs,
    review_prompt,
    split_dialogue,
    spoken_chars,
)


def test_spoken_chars_strips_punctuation():
    assert spoken_chars("你好，世界！") == 4
    assert spoken_chars("——…“”") == 0


def test_estimate_duration_tracks_speech_length():
    short = estimate_duration("好。")
    long = estimate_duration("这是一句很长很长的台词" * 5)
    assert short == 4  # floor
    assert long == 15  # ceiling
    mid = estimate_duration("一二三四五六七八九十" * 3)  # 30 chars ~ 9.2s
    assert 8 <= mid <= 10


def test_split_dialogue_respects_time_budget():
    line = "第一句话。第二句话！第三句话呢？" * 8
    segments = split_dialogue(line, max_seconds=15.0)
    budget = int(15.0 * 3.5 * 0.85)
    assert len(segments) > 1
    assert "".join(segments) == line
    for segment in segments:
        assert spoken_chars(segment) <= budget


def test_split_dialogue_short_line_stays_whole():
    assert split_dialogue("就一句。") == ["就一句。"]


def test_briefs_are_scene_agnostic_and_carry_performance_facts():
    shots = [
        {"shot_id": "S01", "actor_label": "roleA", "speech": "你把伞拿走了？",
         "action": "伸手拦", "gaze": "盯着对方", "emotion": "质问"},
        {"shot_id": "S02", "actor_label": "roleB", "speech": "我有急事。"},
    ]
    briefs = paginate_briefs(
        shots, per_page=6, title="test",
        actor_labels=["roleA", "roleB"],
        scene_notes={"roleA": "雨夜公交站"},
        persona_notes={"roleA": "较真"},
    )
    prompt = briefs[0].to_prompt()
    assert "参考图1作为角色roleA" in prompt
    assert "动作：伸手拦" in prompt and "视线：盯着对方" in prompt
    assert "构图和景别由你设计" in prompt
    # generic template must not smuggle any production-specific nouns
    for banned in ("直播", "汽配", "麦克风", "张老师"):
        assert banned not in prompt


def test_review_covers_consistency_axes():
    joined = "".join(REVIEW_QUESTIONS)
    for axis in ("角色一致性", "空间一致性", "画面一致性", "物品摆放", "镜头合理性"):
        assert axis in joined
    shots = [{"shot_id": "S01", "actor_label": "a", "speech": "x"}]
    brief = paginate_briefs(shots, actor_labels=["a"])[0]
    prompt = review_prompt(brief)
    assert "S01" in prompt and "verdict" in prompt


def test_review_checks_body_scale_drift():
    checks = "".join(REVIEW_QUESTIONS)
    assert "头身比" in checks


def test_brief_prompt_pins_body_scale():
    shots = [{"shot_id": "S01", "actor_label": "a", "speech": "x"}]
    prompt = paginate_briefs(shots, actor_labels=["a"])[0].to_prompt()
    assert "体型尺度硬要求" in prompt
    assert "头身比" in prompt


def test_tail_key_strips_punctuation():
    assert _tail_key("我听人说的。") == "我听人说的"
    assert _tail_key("好！") == "好"


def test_fast_cps_floor_exceeds_default():
    # physics guard must be meaningfully faster than the planning speed,
    # otherwise it would override legitimate gemini answers
    assert FAST_CHARS_PER_SECOND > 3.5
