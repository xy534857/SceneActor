"""Full storyboard-driven pipeline for both ablation variants.

Per shot: seedance render with FOUR references (scene anchor frame,
storyboard page for composition, identity avatar, voice wav) -> gemini
audit (transcript + 9 defect classes incl. board bleed-through) ->
failed shots re-render up to MAX_ROUNDS -> passing clips are speech-end
trimmed (gemini judge, BGM-proof) -> both films concatenated.

Run: PYTHONPATH=src python3 examples/zhang_laoshi/ablation/render_with_boards.py
"""
import base64
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from sceneactor.adapters.storyboard import trim_to_speech  # noqa: E402
from sceneactor.adapters.tokenrouter import ReferenceMedia, TokenRouterVideoClient  # noqa: E402

HERE = Path(__file__).resolve().parent
GATEWAY = "http://161.118.219.11:8081/v1"
KEY = re.search(r"apiKey:\s*(\S+)", (Path.home() / ".omp/agent/models.yml").read_text()).group(1)
MAX_ROUNDS = 3

client = TokenRouterVideoClient()

AVATARS = {
    "zhang-laoshi-parody": "asset://asset-20260721142541-hmb7g",
    "father-liu": "asset://asset-20260721145252-jwj2f",
}
STYLE = "厚涂手绘卡通风格，大头小身Q版三头身角色，白描边贴纸质感，柔和光影，纯色背景干净利落。"
SCENES = {
    "zhang-laoshi-parody": (
        "场景：深夜的志愿咨询书房。原木桌上一支银色麦克风(左)、一台笔记本电脑(右)、一杯热茶和几本厚报考指南；"
        "背后暖黄台灯和贴满便签的软木板。角色是参考图里那个灰发凌乱、戴金丝眼镜、穿深蓝毛衣背心白衬衫的Q版中年男人，"
        "坐在桌前对着麦克风说话。"
    ),
    "father-liu": (
        "场景：傍晚的县城小店柜台。柜台上摊着一张皱成绩单、一把扳手、几个零件盒，背后货架堆满物品，"
        "头顶白炽灯泡。角色是参考图里那个乱发胡子拉碴、穿旧夹克衫的Q版中年男人，"
        "一只手举着老式手机贴在耳边大声说话，另一只手按着柜台上的成绩单。"
    ),
}
VOICE = {
    "zhang-laoshi-parody": "声线：四十多岁中年男人，男声，浑厚的中低音，东北口音，语速快，语气直接果断，绝不是女声。",
    "father-liu": "声线：五十岁中年男人，男声，沙哑粗嗓门，河南口音，急切大声。",
}
VOICE_EXPECT = {
    "zhang-laoshi-parody": "中年男声，浑厚中低音，东北口音",
    "father-liu": "五十岁男声，沙哑粗嗓门，河南口音",
}


def upload_refs():
    anchors = {
        "zhang-laoshi-parody": client.upload_reference(HERE / "_anchor_zhang.png"),
        "father-liu": client.upload_reference(HERE / "_anchor_father.png"),
    }
    voices = {
        "zhang-laoshi-parody": client.upload_reference(HERE / "_voice_zhang.wav"),
        "father-liu": client.upload_reference(HERE / "_voice_father.wav"),
    }
    boards = {}
    for page in (1, 2, 3):
        boards[f"full{page}"] = client.upload_reference(HERE / f"storyboard_full_p{page}_openai.png")
    for page in (1, 2):
        boards[f"abl{page}"] = client.upload_reference(HERE / f"storyboard_abl_p{page}_openai.png")
    return anchors, voices, boards


ANCHORS, VOICES, BOARDS = upload_refs()

PLANS = {v: json.loads((HERE / f"shot_plan_{v}.json").read_text()) for v in ("full", "ablated")}
PAGE_OF = {}
for variant, prefix, per_page in (("full", "full", 6), ("ablated", "abl", 6)):
    for index, shot in enumerate(PLANS[variant]):
        PAGE_OF[shot["shot_id"]] = f"{prefix}{index // per_page + 1}"


def shot_prompt(shot):
    other = "电话那头" if shot["actor"] == "father-liu" else "连麦那头"
    action = f"动作：{shot.get('action', '')[:60]}\n" if shot.get("action") else ""
    return (
        f"{STYLE}\n{SCENES[shot['actor']]}\n"
        f"画面延续场景参考图：同一房间、同一角色造型。\n"
        f"构图参考分镜板：板上标注{shot['shot_id']}的那一格就是本镜头的构图——按那格的景别、机位、角色姿态取景，"
        f"忽略板上其他格子和文字注释。\n"
        f"{action}{VOICE[shot['actor']]}\n"
        f"参考音频只提供音色，这就是这个角色的声音，不要复现参考音频里的内容。\n"
        f"角色用中文清楚流畅地说这句台词，只念一遍，念完就安静保持动作到结束（对{other}说，口型对上）：\n"
        f"「{shot['speech']}」\n"
        f"全程没有背景音乐、没有配乐、没有转场音效、没有电话提示音；只允许人声和画面真实动作声。"
        f"视频开始角色就直接开口。没有其他角色，没有字幕，没有文字。"
    )


def shot_refs(shot):
    actor = shot["actor"]
    return [
        ReferenceMedia(url=ANCHORS[actor], category="image", role="scene_style"),
        ReferenceMedia(url=BOARDS[PAGE_OF[shot["shot_id"]]], category="image", role="reference"),
        ReferenceMedia(url=AVATARS[actor], category="image", role="identity_anchor"),
        ReferenceMedia(url=VOICES[actor], category="audio", role="reference"),
    ]


def render(shot, dest):
    for attempt in (1, 2):
        try:
            result = client.generate(
                shot_prompt(shot), model="VS", version="2.0", resolution="720P",
                duration=shot["duration"], aspect_ratio="16:9", audio=True,
                references=shot_refs(shot), timeout_seconds=1500,
            )
            client.download(result.video_url, dest)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[render] {shot['shot_id']} attempt {attempt}: {str(exc)[:150]}", flush=True)
            if attempt == 1:
                time.sleep(10)
    return False


def audit(shot, video):
    compressed = video.parent / f"_sm_{video.stem[:24]}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "scale=480:-2",
         "-c:v", "libx264", "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(compressed)],
        capture_output=True,
    )
    encoded = base64.b64encode(compressed.read_bytes()).decode()
    compressed.unlink(missing_ok=True)
    prompt = (
        f"角色应念台词（一遍）：「{shot['speech']}」（预期声线：{VOICE_EXPECT[shot['actor']]}）\n"
        "逐字转写实际内容，审核：A整句重复 B怪声/他人声音 C含糊听不懂 D漏关键部分 E声线不符 F中断 "
        "G背景音乐/配乐/转场音效（画面真实动作声不算）H卡壳重启 "
        "I画面异常：出现分镜板/网格/黑白铅笔画面/文字注释（画面必须是正常彩色卡通场景）。\n"
        '最后一行严格JSON：{"transcript":"...","issues":[...],"verdict":"pass|fail"}'
    )
    body = json.dumps({"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{encoded}"}},
        {"type": "text", "text": prompt}]}]}).encode()
    for attempt in range(3):
        try:
            request = urllib.request.Request(f"{GATEWAY}/chat/completions", data=body,
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


def process_shot(args):
    variant, shot = args
    dest_dir = HERE / f"shots_{variant}_board"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{shot['shot_id']}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return shot["shot_id"], "skip"
    for round_no in range(1, MAX_ROUNDS + 1):
        if not render(shot, dest):
            continue
        verdict = audit(shot, dest)
        if verdict.get("verdict") == "pass":
            old, new = trim_to_speech(dest, speech=shot["speech"], gateway_url=GATEWAY, api_key=KEY)
            print(f"[done] {shot['shot_id']} round {round_no} trim {old:.1f}->{new:.1f}", flush=True)
            return shot["shot_id"], "ok"
        print(f"[audit] {shot['shot_id']} round {round_no} FAIL {verdict.get('issues')}", flush=True)
        dest.unlink(missing_ok=True)
    return shot["shot_id"], "give-up"


def main():
    jobs = [(variant, shot) for variant in PLANS for shot in PLANS[variant]]
    print(f"pipeline start: {len(jobs)} shots", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(process_shot, jobs))
    ok = sum(1 for _, status in results if status in ("ok", "skip"))
    print(f"shots done: {ok}/{len(jobs)}", flush=True)
    for sid, status in results:
        if status not in ("ok", "skip"):
            print(f"[giveup] {sid}", flush=True)
    for variant in PLANS:
        entries = "\n".join(
            f"file 'shots_{variant}_board/{shot['shot_id']}.mp4'"
            for shot in PLANS[variant]
            if (HERE / f"shots_{variant}_board" / f"{shot['shot_id']}.mp4").exists()
        )
        list_file = HERE / f"concat_{variant}_board.txt"
        list_file.write_text(entries, encoding="utf-8")
        final = HERE / f"consult_{variant}_board.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", "-b:a", "192k", str(final)],
            capture_output=True, text=True, cwd=str(HERE),
        )
        duration = float(subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
            capture_output=True, text=True).stdout.strip() or 0)
        print(f"[film] {final.name} {duration / 60:.1f}min", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
