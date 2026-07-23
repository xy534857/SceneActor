"""Storyboard v3: boards drawn FROM the actual character + scene references.

v2 boards were drawn from text only — the image model imagined the characters
per panel, so head-body ratio drifted panel to panel, and seedance (told the
board is the composition authority) faithfully amplified that noise.

v3 feeds the board model the same anchors the video model gets:
  image 1..N  character 3-view turnarounds (identity + BODY SCALE truth)
  image N+1.. scene multi-angle sheets (spatial truth)
plus the shot table (speech/action/gaze from the rehearsal script).

Board pages are audited with the storyboard REVIEW gate (now including the
head-body-ratio check) and regenerated up to MAX_ROUNDS on fail.

Run: PYTHONPATH=src python3 examples/zhang_laoshi/ablation/regen_storyboard_v3.py
"""
import base64
import json
import mimetypes
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sceneactor.adapters.storyboard import (  # noqa: E402
    paginate_briefs,
    review_prompt,
)

HERE = Path(__file__).resolve().parent
GATEWAY = "http://161.118.219.11:8081/v1"
KEY = re.search(r"apiKey:\s*(\S+)", (Path.home() / ".omp/agent/models.yml").read_text()).group(1)
OPENAI_KEY = ""  # set via env or paste; falls back to gateway image model when empty
MAX_ROUNDS = 3

ACTOR_KEY = {"zhang-laoshi-parody": "zhang", "father-liu": "father"}
SCENE_DESC = {
    "zhang": "深夜书房式咨询台（原木桌、麦克风在左、笔记本电脑在右、台灯在右后、便签板在背景墙）",
    "father": "傍晚县城小店柜台（左手举老式手机贴耳、皱纸单据在手边、工具在右侧、货架在背景）",
}
PERSONA_NOTES = {
    "zhang": "四十多岁咨询师气质，戴眼镜，快而直接",
    "father": "五十岁小店老板，急切固执",
}


def openai_image_edit(prompt: str, refs: list[Path], out_path: Path, api_key: str) -> None:
    boundary = uuid.uuid4().hex
    parts = []

    def field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )

    field("model", "gpt-image-2")
    field("prompt", prompt)
    field("size", "1536x1024")
    for ref in refs:
        mime = mimetypes.guess_type(str(ref))[0] or "image/png"
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
                f'filename="{ref.name}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
            + ref.read_bytes()
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/edits",
        data=b"".join(parts),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        payload = json.loads(response.read())
    out_path.write_bytes(base64.b64decode(payload["data"][0]["b64_json"]))


def audit_board(board: Path, brief) -> dict:
    encoded = base64.b64encode(board.read_bytes()).decode()
    manifest = "\n".join(
        f"第{idx + 1}格应为镜头{shot['shot_id']}，画面中只有{shot['actor_label']}一人"
        for idx, shot in enumerate(brief.shots)
    )
    strict = (
        f"\n\n面板清单（逐格核对，任何一条不符即fail）：\n{manifest}\n"
        "额外硬性fail条件：1) 任何镜头号重复出现或缺失 2) 任何一格出现两个人 "
        "3) 空白面板 4) 超过两格使用几乎相同的景别和机位（构图单调）。"
    )
    body = json.dumps({
        "model": "gemini-2.5-pro",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            {"type": "text", "text": review_prompt(brief) + strict},
        ]}],
    }).encode()
    for _ in range(3):
        try:
            request = urllib.request.Request(
                f"{GATEWAY}/chat/completions", data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = json.loads(response.read())
            text = payload["choices"][0]["message"]["content"]
            match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:  # noqa: BLE001
            time.sleep(10)
    return {"verdict": "error"}


def main() -> None:
    api_key = OPENAI_KEY or (HERE / "_openai_key.txt").read_text().strip()
    for variant, prefix in (("full", "full"), ("ablated", "abl")):
        plan = json.loads((HERE / f"shot_plan_{variant}.json").read_text())
        shots = [
            {
                "shot_id": shot["shot_id"],
                "actor_label": ACTOR_KEY[shot["actor"]],
                "speech": shot["speech"],
                "action": shot.get("action", ""),
                "gaze": shot.get("gaze", ""),
                "emotion": shot.get("emotion", ""),
            }
            for shot in plan
        ]
        briefs = paginate_briefs(
            shots, per_page=6, title=f"咨询连麦对照实验({variant})",
            actor_labels=["zhang", "father"],
            scene_notes={k: SCENE_DESC[k] for k in ("zhang", "father")},
            persona_notes=PERSONA_NOTES,
            scene_ref_labels=["zhang", "father"],
        )
        refs = [
            HERE / "char_sheet_zhang.png",     # 角色zhang三视图
            HERE / "char_sheet_father.png",    # 角色father三视图
            HERE / "scene_sheet_zhang.png",    # zhang场景多角度表
            HERE / "scene_sheet_father.png",   # father场景多角度表
        ]
        for page, brief in enumerate(briefs, start=1):
            out = HERE / f"storyboard_{prefix}_p{page}_v3.png"
            prev = HERE / f"storyboard_{prefix}_p{page - 1}_v3.png"
            chain = prev.exists() and page > 1
            page_refs = ([prev] if chain else []) + refs
            style_lock = (
                "画风连续性硬要求：参考图1是同一本分镜册的上一页，由同一位分镜师绘制。"
                "本页的画风、铅笔线宽、灰度、排版网格、标题字体、注释颜色系统必须与上一页完全一致，"
                "角色画法也必须与上一页完全一致——这是同一本册子的连续两页，不是新作品。"
                f"（因此角色参考图顺延为参考图{2}和{3}，场景表顺延为参考图{4}和{5}。）\n"
            ) if chain else ""
            text_rule = (
                "面板内和面板下方只写镜头号（如FUL01s1），不要书写台词原文——"
                "长中文文字会写错字。台词内容用画面表演传达，不用文字。\n"
                "面板比例硬要求：每个面板一律是16:9横构图（成片是16:9视频，分镜格是构图权威，"
                "比例必须与成片一致），网格每行2格；本页镜头数不足以填满网格时，"
                "剩余的格子画上斜叉线留空，绝不把面板拉成超宽横幅来占满页面。\n"
            )
            panel_manifest = "本页恰好包含以下面板，从左到右、从上到下依次为：\n" + "\n".join(
                f"第{idx + 1}格 = 镜头号{shot['shot_id']}，画面里只有{shot['actor_label']}一个人"
                for idx, shot in enumerate(brief.shots)
            ) + (
                f"\n共{len(brief.shots)}格，每个镜头号只出现一次，禁止重复、禁止遗漏、禁止空面板。"
                "两个角色分别在各自的空间连麦通话，任何一格里都绝不能同时出现两个人。\n"
                "镜头设计必须有变化：全页不许超过两格使用相同的景别和机位，"
                "要混用特写/中景/过肩/低角度/侧面等不同镜头语言，跟随台词的情绪强度起伏。\n"
            )
            base_prompt = brief.to_prompt()
            if chain:
                # the previous page occupies reference slot 1; shift every
                # 参考图N mentioned by the brief up by one to stay accurate
                base_prompt = re.sub(
                    r"参考图(\d+)", lambda m: f"参考图{int(m.group(1)) + 1}", base_prompt)
            for round_no in range(1, MAX_ROUNDS + 1):
                openai_image_edit(style_lock + text_rule + panel_manifest + base_prompt, page_refs, out, api_key)
                verdict = audit_board(out, brief)
                print(json.dumps({
                    "board": out.name, "round": round_no,
                    "verdict": verdict.get("verdict"),
                    "issues": verdict.get("issues", [])[:4],
                }, ensure_ascii=False), flush=True)
                if verdict.get("verdict") == "pass":
                    break


if __name__ == "__main__":
    main()
