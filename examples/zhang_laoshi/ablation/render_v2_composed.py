"""Storyboard-led rendering v2: seedance composes shots itself.

Reference contract per shot (all as plain References so seedance is free
to compose them under the storyboard's direction):
  1. storyboard page   -- COMPOSITION AUTHORITY: framing/camera/pose per panel
  2. scene sheet       -- 2x2 multi-angle views of the empty set (spatial truth)
  3. character sheet   -- background-free 3-view turnaround (identity truth)
  4. voice wav         -- timbre anchor
The prompt names the storyboard as the boss: pick the panel for this shot,
frame the camera as drawn there, place THIS character inside THIS set.
No more "match the previous frame" instruction -- that suppressed the board.

Audit -> re-render loop and gemini speech-end trim as before; A/V-normalized
concat to avoid audio drift.

Run: PYTHONPATH=src python3 examples/zhang_laoshi/ablation/render_v2_composed.py
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

ACTOR_KEY = {"zhang-laoshi-parody": "zhang", "father-liu": "father"}

STYLE = "厚涂手绘卡通风格，大头小身Q版三头身角色，白描边贴纸质感，柔和光影。"
SCENE_DESC = {
    "zhang": "深夜书房式咨询台（原木桌、麦克风、笔记本电脑、台灯、贴满便签的软木板）",
    "father": "傍晚县城小店柜台（皱成绩单、扳手、零件盒、货架、白炽灯泡）",
}
VOICE = {
    "zhang": "声线：四十多岁中年男人，男声，浑厚的中低音，东北口音，语速快，语气直接果断，绝不是女声。",
    "father": "声线：五十岁中年男人，男声，沙哑粗嗓门，河南口音，急切大声。",
}
VOICE_EXPECT = {
    "zhang": "中年男声，浑厚中低音，东北口音",
    "father": "五十岁男声，沙哑粗嗓门，河南口音",
}


def upload_refs():
    refs = {}
    for key in ("zhang", "father"):
        refs[f"scene_{key}"] = client.upload_reference(HERE / f"scene_sheet_{key}.png")
        refs[f"char_{key}"] = client.upload_reference(HERE / f"char_sheet_{key}.png")
        refs[f"voice_{key}"] = client.upload_reference(HERE / f"_voice_{key}.wav")
    for page in (1, 2, 3):
        refs[f"board_full{page}"] = client.upload_reference(HERE / f"storyboard_full_p{page}_openai.png")
    for page in (1, 2):
        refs[f"board_abl{page}"] = client.upload_reference(HERE / f"storyboard_abl_p{page}_openai.png")
    return refs


REFS = upload_refs()
PLANS = {v: json.loads((HERE / f"shot_plan_{v}.json").read_text()) for v in ("full", "ablated")}
PAGE_OF = {}
for variant, prefix in (("full", "full"), ("ablated", "abl")):
    for index, shot in enumerate(PLANS[variant]):
        PAGE_OF[shot["shot_id"]] = f"{prefix}{index // 6 + 1}"


def shot_prompt(shot):
    actor = ACTOR_KEY[shot["actor"]]
    other = "电话那头" if actor == "father" else "连麦那头"
    action = f"表演动作：{shot.get('action', '')[:60]}\n" if shot.get("action") else ""
    return (
        f"{STYLE}\n"
        f"参考图1是分镜故事板：找到标注{shot['shot_id']}的那一格，本镜头的构图、景别、机位、"
        f"角色姿态【以那一格为准】——这是最高优先级，其他参考图只提供素材不提供构图。\n"
        f"参考图2是场景多角度参考表：{SCENE_DESC[actor]}的四个视角。按分镜格的机位，"
        f"从中选对应角度的陈设作为背景，道具布局与表内一致。\n"
        f"参考图3是角色三视图：这就是本镜头唯一的角色。按分镜格的姿态把这个角色放进场景里。\n"
        f"{action}{VOICE[actor]}\n"
        f"参考音频只提供音色，不要复现参考音频里的内容。\n"
        f"角色用中文清楚流畅地说这句台词，只念一遍，念完就安静保持姿态到结束（对{other}说，口型对上）：\n"
        f"「{shot['speech']}」\n"
        f"最终画面是正常的彩色卡通视频画面——不要出现分镜板的黑白铅笔质感、网格、文字注释。\n"
        f"全程没有背景音乐、没有配乐、没有转场音效、没有电话提示音；只允许人声和画面真实动作声。"
        f"视频开始角色就直接开口。没有其他角色，没有字幕，没有文字。"
    )


def shot_refs(shot):
    actor = ACTOR_KEY[shot["actor"]]
    return [
        ReferenceMedia(url=REFS[f"board_{PAGE_OF[shot['shot_id']]}"], category="image", role="reference"),
        ReferenceMedia(url=REFS[f"scene_{actor}"], category="image", role="scene_style"),
        ReferenceMedia(url=REFS[f"char_{actor}"], category="image", role="identity_anchor"),
        ReferenceMedia(url=REFS[f"voice_{actor}"], category="audio", role="reference"),
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
    actor = ACTOR_KEY[shot["actor"]]
    compressed = video.parent / f"_sm_{video.stem[:24]}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "scale=480:-2",
         "-c:v", "libx264", "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(compressed)],
        capture_output=True,
    )
    encoded = base64.b64encode(compressed.read_bytes()).decode()
    compressed.unlink(missing_ok=True)
    prompt = (
        f"角色应念台词（一遍）：「{shot['speech']}」（预期声线：{VOICE_EXPECT[actor]}）\n"
        "逐字转写实际内容，审核：A整句重复 B怪声/他人声音 C含糊听不懂 D漏关键部分 E声线不符 F中断 "
        "G背景音乐/配乐/转场音效（画面真实动作声不算）H卡壳重启 "
        "I画面异常：出现分镜板/网格/黑白铅笔画面/文字注释/三视图并排（画面必须是正常彩色卡通场景里的单个角色）。\n"
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
    dest_dir = HERE / f"shots_{variant}_v2"
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


def normalize_and_concat(variant):
    plan = PLANS[variant]
    norm = HERE / f"shots_{variant}_v2norm"
    norm.mkdir(exist_ok=True)
    for shot in plan:
        src = HERE / f"shots_{variant}_v2" / f"{shot['shot_id']}.mp4"
        if not src.exists():
            continue
        dst = norm / f"{shot['shot_id']}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af", "apad", "-shortest",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
             "-video_track_timescale", "24000", str(dst)],
            capture_output=True,
        )
    entries = "\n".join(
        f"file 'shots_{variant}_v2norm/{shot['shot_id']}.mp4'"
        for shot in plan if (norm / f"{shot['shot_id']}.mp4").exists()
    )
    list_file = HERE / f"concat_{variant}_v2.txt"
    list_file.write_text(entries, encoding="utf-8")
    final = HERE / f"consult_{variant}_v2.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(final)],
        capture_output=True, text=True, cwd=str(HERE),
    )
    duration = float(subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(final)],
        capture_output=True, text=True).stdout.strip() or 0)
    print(f"[film] {final.name} {duration / 60:.1f}min", flush=True)


def main():
    jobs = [(variant, shot) for variant in PLANS for shot in PLANS[variant]]
    print(f"v2 pipeline start: {len(jobs)} shots", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(process_shot, jobs))
    ok = sum(1 for _, status in results if status in ("ok", "skip"))
    print(f"shots done: {ok}/{len(jobs)}", flush=True)
    for sid, status in results:
        if status not in ("ok", "skip"):
            print(f"[giveup] {sid}", flush=True)
    for variant in PLANS:
        normalize_and_concat(variant)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
