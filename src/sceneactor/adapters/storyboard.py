"""Director storyboard pipeline: speech-timed shot splitting, image-2 board
panels, and post-render silence trimming.

Solves three production problems observed on the seedance route:

1. **Shot duration vs speech length** — seedance renders a fixed-length clip;
   when the line ends early the tail is dead air. Duration is *estimated* from
   a measured speaking rate (chars/sec, default calibrated at 3.5 from real
   renders), then *corrected* after render by `speech_end()` +
   `trim_to_speech()` which cut the clip right after the last voiced frame.
2. **Composition drift** — every shot gets a storyboard panel (black-and-white
   pencil, annotated arrows) drawn by an image model from the SAME identity
   references used by seedance; the panel is then passed to seedance as an
   extra composition reference alongside the identity/scene/voice anchors.
3. **Consistency** — panels and clips share one reference set: identity anchor
   (who), scene anchor frame (where/camera), voice anchor wav (timbre).

The storyboard prompt template follows the "director board" convention:
16:9 board paper, N cinematic panels, black-and-white rough pencil, gesture
energy, annotation colours (red=body motion, blue=camera, green=composition,
orange=light, purple=emotion, black=lens notes).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

# Measured on 25 seedance VS-2.0 renders (see ablation experiment): median
# 3.58 chars/sec, floor 3.2 for angry fast lines, joking beats ~2.7.
DEFAULT_CHARS_PER_SECOND = 3.5
_PUNCT = re.compile(r"[，。！？；：…—、'‘’\"“”「」（）()\s]")


def spoken_chars(speech: str) -> int:
    """Characters that actually take voice time (punctuation stripped)."""
    return len(_PUNCT.sub("", speech))


def estimate_duration(
    speech: str,
    *,
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
    floor: int = 4,
    ceiling: int = 15,
    pad_seconds: float = 0.6,
) -> int:
    """Provider-facing duration request: speech time + a breath, snapped to
    the provider's integer-seconds contract. Deliberately tight — the trim
    pass removes leftovers, but nothing can fill a clip that ran out."""
    seconds = spoken_chars(speech) / chars_per_second + pad_seconds
    return max(floor, min(ceiling, round(seconds)))


def split_dialogue(
    speech: str,
    *,
    max_seconds: float = 15.0,
    chars_per_second: float = DEFAULT_CHARS_PER_SECOND,
) -> list[str]:
    """Split one turn into shot-sized segments on clause boundaries.

    The budget is TIME, not characters: max_seconds at the calibrated rate,
    minus a safety margin so a slow read still fits the provider ceiling.
    """
    budget = int(max_seconds * chars_per_second * 0.85)
    clauses = [c for c in re.split(r"(?<=[。！？；…])|(?<=——)", speech) if c and c.strip()]
    segments: list[str] = []
    current = ""
    for clause in clauses:
        if current and spoken_chars(current) + spoken_chars(clause) > budget:
            segments.append(current)
            current = clause
        else:
            current += clause
    if current:
        segments.append(current)
    result: list[str] = []
    for segment in segments:
        while spoken_chars(segment) > budget:
            cut = _hard_cut_index(segment, budget)
            result.append(segment[:cut])
            segment = segment[cut:]
        result.append(segment)
    return result


def _hard_cut_index(text: str, budget: int) -> int:
    """Last soft boundary (comma/dash) before the budget, else the budget."""
    voiced = 0
    soft = 0
    for index, char in enumerate(text):
        if not _PUNCT.match(char):
            voiced += 1
        elif char in "，、—":
            soft = index + 1
        if voiced >= budget:
            return soft if soft > budget // 3 else index + 1
    return len(text)


# ---------------------------------------------------------------- trimming

def speech_end(video: Path, *, noise: str = "-38dB", min_silence: float = 0.5) -> tuple[float, float]:
    """(last voiced second, container duration) via band-passed silencedetect.

    The band-pass (200-3500 Hz) isolates voice from room tone. Fast and free,
    but blind to clips where music/effects share the voice band — prefer
    `speech_end_gemini` when BGM contamination is possible.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    detect = subprocess.run(
        ["ffmpeg", "-i", str(video), "-af",
         f"highpass=f=200,lowpass=f=3500,silencedetect=noise={noise}:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    events = re.findall(r"silence_(start|end): ([\d.]+)", detect.stderr)
    starts = [float(value) for kind, value in events if kind == "start"]
    ends = [float(value) for kind, value in events if kind == "end"]
    if starts:
        last_start = starts[-1]
        trailing_ends = [end for end in ends if end > last_start]
        if not trailing_ends or trailing_ends[-1] >= duration - 0.3:
            return last_start, duration
    return duration, duration


# Fastest plausible Mandarin delivery observed in our renders; the speech of a
# clip can never END before spoken_chars/FAST_CPS seconds have elapsed.
FAST_CHARS_PER_SECOND = 5.0


def _tail_key(speech: str, length: int = 5) -> str:
    """Normalized last characters of a line, for completeness checks."""
    return _PUNCT.sub("", speech)[-length:]


def speech_end_gemini(
    video: Path,
    speech: str,
    *,
    gateway_url: str,
    api_key: str,
    model: str = "gemini-3.5-flash",
    retries: int = 3,
) -> tuple[float, float]:
    """(last spoken-word second, container duration) judged by Gemini.

    Semantic, not spectral: asks WHEN the character finishes the LINE, so
    trailing BGM, room tone, sound effects, or hummed music do not fool it
    the way an energy detector does. Falls back to `speech_end` when the
    model answer is unusable.
    """
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    compressed = video.with_suffix(".audit.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "scale=480:-2",
         "-c:v", "libx264", "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(compressed)],
        capture_output=True,
    )
    try:
        encoded = base64.b64encode(compressed.read_bytes()).decode()
        tail = _tail_key(speech)
        prompt = (
            f"这段视频里角色念的台词是：「{speech}」\n"
            f"视频总长 {duration:.1f} 秒。请完成两件事：\n"
            "1. 逐字转写角色实际说出的台词（人声部分，背景音乐/音效/哼唱不算）。\n"
            "2. 精确判断角色说完台词最后一个字是在第几秒。\n"
            '只输出严格JSON：{"transcript": "...", "speech_end_seconds": <数字>, '
            '"line_complete": true/false}  其中 line_complete 表示台词是否说完整'
            f"（结尾应落在「{tail}」附近，允许口音同音字差异）。"
        )
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{encoded}"}},
                {"type": "text", "text": prompt},
            ]},],
        }).encode()
        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    f"{gateway_url.rstrip('/')}/chat/completions", data=body,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(request, timeout=600) as response:
                    payload = json.loads(response.read())
                text = payload["choices"][0]["message"]["content"]
                match = re.search(r'\{[^{}]*"speech_end_seconds"[^{}]*\}', text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    end = float(data["speech_end_seconds"])
                    transcript = _PUNCT.sub("", str(data.get("transcript", "")))
                    complete = bool(data.get("line_complete", True))
                    # completeness guard: if the model itself says the line is
                    # unfinished, or the transcribed tail shares no character
                    # with the expected tail, DO NOT trim — a wrong early end
                    # here is what cuts lines mid-word.
                    tail_seen = (not transcript) or any(ch in transcript[-12:] for ch in tail)
                    if not complete or not tail_seen:
                        return duration, duration
                    # physics guard: speech cannot end before the line could
                    # possibly have been spoken at maximum plausible speed.
                    floor_end = spoken_chars(speech) / FAST_CHARS_PER_SECOND
                    end = max(end, floor_end)
                    if 0 < end <= duration + 0.5:
                        return min(end, duration), duration
            except Exception:  # noqa: BLE001
                if attempt == retries - 1:
                    break
                time.sleep(10)
    finally:
        compressed.unlink(missing_ok=True)
    return speech_end(video)


def trim_to_speech(
    video: Path,
    *,
    speech: str = "",
    pad_seconds: float = 0.45,
    min_gap: float = 0.8,
    crf: int = 18,
    gateway_url: str = "",
    api_key: str = "",
) -> tuple[float, float]:
    """Cut the clip shortly after the last spoken word. Returns
    (old_duration, new_duration); no-op when the tail is already tight.

    With `gateway_url` + `api_key` + `speech` the end point comes from
    `speech_end_gemini` (BGM-proof); otherwise the spectral detector.
    """
    if gateway_url and api_key and speech:
        end, duration = speech_end_gemini(video, speech, gateway_url=gateway_url, api_key=api_key)
    else:
        end, duration = speech_end(video)
    if duration - end < min_gap:
        return duration, duration
    cut = min(duration, end + pad_seconds)
    temporary = video.with_suffix(".trim.mp4")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-t", f"{cut:.2f}",
         "-c:v", "libx264", "-crf", str(crf), "-preset", "fast",
         "-c:a", "aac", "-b:a", "192k", str(temporary)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not temporary.exists():
        raise RuntimeError(f"trim failed for {video.name}: {proc.stderr[-300:]}")
    temporary.replace(video)
    return duration, cut


# ---------------------------------------------------------------- storyboard

ANNOTATION_LEGEND = (
    "注释颜色系统：红色箭头=身体运动；蓝色箭头=摄像机运动；绿色标记=构图笔记；"
    "橙色标记=光线方向；紫色标记=情感强调；黑色文字=镜头笔记和面板标签。"
)

BOARD_STYLE = (
    "电影导演分镜故事板。16:9 故事板纸。实际故事板绘画仅使用黑色和白色："
    "粗铅笔线条，细节最少，快速手势绘画能量，简单解剖结构构建和强烈的轮廓可读性。"
    "保持画面轻量、动态、未完成，像早期分镜草稿。彩色部分只允许出现在注释箭头和标记上。"
)


@dataclass(frozen=True)
class StoryboardBrief:
    """Raw material handed to the image model: it directs, we review.

    Instead of dictating per-panel composition/camera/arrows, the brief gives
    the model the same things a human storyboard artist gets — dialogue lines,
    character sheets, scene descriptions — and lets it design shot framing,
    motion arrows, and annotations itself.
    """

    title: str
    shots: tuple[Mapping[str, Any], ...]
    actor_labels: tuple[str, ...]
    scene_notes: Mapping[str, str] = field(default_factory=dict)
    persona_notes: Mapping[str, str] = field(default_factory=dict)
    scene_ref_labels: tuple[str, ...] = ()
    """Actors whose multi-angle scene sheets are attached as reference images,
    numbered right after the character references. When set, the board model
    draws backgrounds from these images instead of imagining them from text."""

    def to_prompt(self) -> str:
        numbered = "、".join(
            f"参考图{index + 1}作为角色{label}"
            for index, label in enumerate(self.actor_labels)
        )
        offset = len(self.actor_labels)
        scene_numbered = "、".join(
            f"参考图{offset + index + 1}是{label}的场景多角度表"
            for index, label in enumerate(self.scene_ref_labels)
        )
        lines = []
        for shot in self.shots:
            label = shot.get("actor_label", shot.get("actor", ""))
            parts = [f"{shot['shot_id']} [{label}]"]
            if shot.get("emotion") or shot.get("expr_text"):
                parts.append(f"情绪：{shot.get('expr_text') or shot.get('emotion')}")
            parts.append(f"台词：{shot['speech']}")
            if shot.get("action"):
                parts.append(f"动作：{shot['action']}")
            if shot.get("gaze"):
                parts.append(f"视线：{shot['gaze']}")
            lines.append("\n  ".join(parts))
        personas = "\n".join(f"{k}：{v}" for k, v in self.persona_notes.items())
        scenes = "\n".join(f"{k}的场景：{v}" for k, v in self.scene_notes.items())
        return (
            f"你是电影分镜导演。请为下面的对白设计导演分镜故事板：先自己理解每句台词的"
            f"戏剧功能和角色此刻的心理，再决定每个镜头的构图、景别、角色姿态和身体动作——"
            f"这些由你设计，不要每格都画成同样的正面中景。\n\n"
            f"使用{numbered}——面板里的人物必须与参考图的发型、眼镜、体型、服装一致。\n"
            + (f"{scene_numbered}——每格背景必须取自对应场景表的某个视角，"
               f"陈设/道具/方位与场景表完全一致，不要自己发明背景。\n" if scene_numbered else "")
            + f"{personas}\n{scenes}\n\n"
            f"空间连续性硬要求：同一角色的所有面板画的是同一个真实空间——场景描述里列出的道具"
            f"是这个空间的固定陈设，位置和方位在所有面板中保持不变。景别变化只是取景框在动，"
            f"空间本身不动：中景里在桌上的东西，另一格中景里必须还在原位；只有推到特写时"
            f"才允许道具自然出画。禁止同一景别下道具时有时无。\n\n"
            f"体型尺度硬要求：角色的头身比和体型以参考图为唯一标准，在所有面板中保持完全一致——"
            f"景别推近时是取景框变化，不是人变大；相同景别下角色占画面的比例必须相同。"
            f"每个面板用固定陈设（桌沿高度、台灯、货架层高）作为角色身高的标尺：同一角色"
            f"坐姿时头顶相对桌面的高度在所有面板中不变。禁止同一角色在不同面板中头身比漂移。\n\n"
            f"镜头表（每个镜头一个面板，按顺序；情绪/动作/视线是表演事实，构图和景别由你设计）：\n"
            + "\n".join(lines) + "\n\n"
            f"{BOARD_STYLE}\n{ANNOTATION_LEGEND}\n"
            f"标题：{self.title}。网格排列，每面板左上角用黑色文字写镜头号。"
        )


def paginate_briefs(
    shots: Sequence[Mapping[str, Any]],
    *,
    per_page: int = 6,
    title: str = "storyboard",
    actor_labels: Sequence[str] = (),
    scene_notes: Mapping[str, str] | None = None,
    persona_notes: Mapping[str, str] | None = None,
    scene_ref_labels: Sequence[str] = (),
) -> list[StoryboardBrief]:
    """Split shots into board-page briefs; the image model designs each panel."""
    briefs: list[StoryboardBrief] = []
    for offset in range(0, len(shots), per_page):
        chunk = tuple(shots[offset:offset + per_page])
        briefs.append(StoryboardBrief(
            title=f"{title} 第{offset // per_page + 1}页",
            shots=chunk,
            actor_labels=tuple(actor_labels),
            scene_notes=dict(scene_notes or {}),
            persona_notes=dict(persona_notes or {}),
            scene_ref_labels=tuple(scene_ref_labels),
        ))
    return briefs


REVIEW_QUESTIONS = (
    "A. 镜头对应：每个面板是否对应正确的镜头号和说话角色？",
    "B. 角色一致性：同一角色在所有面板里发型/眼镜/体型/服装是否一致，且与参考图一致？"
    "重点核查头身比：同一角色在相同景别的面板之间，头身比和相对固定陈设（桌面/台灯/货架）的"
    "身高是否一致——同一角色一格里三头身、另一格里五头身，或坐姿头顶忽高忽低，即为fail。",
    "C. 空间一致性：同一场景的面板之间，桌子/道具/门窗方位是否稳定？有没有越轴（角色朝向突然翻转）？"
    "重点核查：同一场景中相同或相近景别的面板，桌面和背景道具是否完全相同——一件道具（如桌上的设备、"
    "纸张、工具）在一格出现、在另一格同景别里消失，即为fail。",
    "D. 画面一致性：不同角色各自的场景是否始终可区分，没有互相串场（一个角色的场景道具出现在另一个角色的背景里）？",
    "E. 物品摆放合理性：道具的位置和持握方式是否符合物理常识和台词动作？道具与场景描述列出的固定陈设是否一致？",
    "F. 镜头合理性：景别与戏剧强度是否匹配（爆发拍近景、要参数拍中景等），构图有没有变化而不是每格同样正面中景？",
    "G. 注释系统：红蓝箭头和彩色标记是否存在且语义合理？",
    "H. 画风：是否黑白粗铅笔草稿，彩色只出现在注释上？",
    "I. 面板比例：每个有画面的面板是否都是16:9横构图？任何面板被拉成超宽横幅"
    "（明显宽于16:9，如整行一条）或压成竖构图，即为fail——分镜格是成片构图权威，比例必须与16:9成片一致。",
)


def review_prompt(brief: StoryboardBrief) -> str:
    """Audit prompt for a rendered board page — we only review, not design."""
    shot_list = "、".join(str(shot["shot_id"]) for shot in brief.shots)
    questions = "\n".join(REVIEW_QUESTIONS)
    return (
        f"这是一页导演分镜故事板，应包含镜头：{shot_list}。逐面板检查：\n{questions}\n"
        '只在确定有缺陷时判fail。最后一行输出严格JSON：'
        '{"issues":["A:说明",...],"verdict":"pass|fail"}'
    )
