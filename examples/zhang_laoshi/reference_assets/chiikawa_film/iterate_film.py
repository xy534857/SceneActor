"""Analyze -> fix -> re-render loop for the chiikawa film.

Each round: every shot is transcribed and audited by Gemini (video+audio),
shots with real defects (doubled lines, garbage noises, unintelligible or
missing speech, wrong timbre) are deleted and re-rendered with a stricter
prompt. Loops until all shots pass or MAX_ROUNDS is hit, then concats the
final film and writes an audit report.

Run: PYTHONPATH=src python3 examples/zhang_laoshi/reference_assets/chiikawa_film/iterate_film.py
"""
import base64
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sceneactor.adapters.tokenrouter import ReferenceMedia, TokenRouterVideoClient  # noqa: E402

HERE = Path(__file__).resolve().parent
PLAN_PATH = HERE / "film_plan.json"
SHOT_DIR = HERE / "shots"
TMP_DIR = HERE / "audit_tmp"
TMP_DIR.mkdir(exist_ok=True)
REPORT_PATH = HERE / "audit_report.json"

MAX_ROUNDS = 3
GEMINI_MODEL = "gemini-3.5-flash"
GATEWAY = "http://161.118.219.11:8081/v1/chat/completions"


def _api_key() -> str:
    text = (Path.home() / ".omp/agent/models.yml").read_text(encoding="utf-8")
    match = re.search(r"apiKey:\s*(\S+)", text)
    if not match:
        raise RuntimeError("no apiKey in ~/.omp/agent/models.yml")
    return match.group(1)


KEY = _api_key()
client = TokenRouterVideoClient()
PLAN = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

SANITIZE = {
    "说不要你就不要你": "想辞你就辞你",
    "他妈": "",
}


def sanitize(text):
    for bad, good in SANITIZE.items():
        text = text.replace(bad, good)
    return text


# ---------------------------------------------------------------- rendering

def shot_prompt(s, soften=False):
    scene = PLAN["scenes"][s["actor"]]
    other = "电话那头" if s["actor"] == "father-liu" else "连麦那头"
    expr_note = "表情参考图里真人的表情就是这只小生物此刻的表情强度，把它移植到圆脸上。\n" if s.get("expr_ref") else ""
    return (
        f"{PLAN['style']}\n{scene}\n"
        f"画面严格延续场景参考图：同一房间、同一机位、同一角色造型，只有表情和动作变化。\n"
        f"镜头：中景固定机位，角色占画面中心，正对镜头方向说话。\n"
        f"{s['expr_text']}\n{expr_note}"
        f"动作：{s['action'][:60]}\n"
        f"{PLAN['voice_text'][s['actor']]}\n"
        f"参考音频只提供音色，不要复现参考音频里的内容。\n"
        f"角色用中文清楚地说这句台词，整段台词只念一遍，念完就闭嘴，"
        f"保持安静的小动作到视频结束，绝不把台词念第二遍（对{other}说，语气与表情匹配，口型对上）：\n"
        f"「{sanitize(s['speech']) if soften else s['speech']}」\n"
        f"视频一开始角色就直接开口，没有前奏杂音。全程没有背景音乐、没有配乐、没有转场音效、"
        f"没有电话提示音；只允许角色人声和画面里真实动作的声音（比如拍桌子）。"
        f"台词一口气流畅说完，中途不卡壳不停顿不换气重启。"
        f"画面里没有其他角色，没有字幕，没有文字。"
    )


def refs_for(s):
    info = PLAN["cast"][s["actor"]]
    refs = [
        ReferenceMedia(url=PLAN["anchor_frames"][s["actor"]], category="image", role="scene_style"),
        ReferenceMedia(url=info["identity_asset"], category="image", role="identity_anchor"),
        ReferenceMedia(url=info["voice_url"], category="audio", role="reference"),
    ]
    if s.get("expr_ref"):
        refs.append(ReferenceMedia(url=s["expr_ref"], category="image", role="face_anchor"))
    return refs


def render_shot(s):
    dest = SHOT_DIR / f"{s['shot_id']}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in (1, 2, 3):
        try:
            result = client.generate(
                shot_prompt(s, soften=attempt == 3),
                model="VS", version="2.0", resolution="720P",
                duration=s["duration"], aspect_ratio="16:9",
                audio=True, references=refs_for(s),
                timeout_seconds=1500,
            )
            client.download(result.video_url, dest)
            print(f"[render] {s['shot_id']} done {result.elapsed_seconds:.0f}s", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[render] {s['shot_id']} attempt {attempt} failed: {exc}", flush=True)
            if attempt < 3:
                time.sleep(10)
    return False


# ---------------------------------------------------------------- analysis

VOICE_EXPECT = {
    "zhang-laoshi-parody": "软糯高频的幼儿奶音（音调高、可爱），语气急而凶",
    "father-liu": "亢奋沙哑的尖嗓门，扯着嗓子喊",
}


def gemini(content, retries=3):
    body = json.dumps({
        "model": GEMINI_MODEL,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    for i in range(retries):
        req = urllib.request.Request(GATEWAY, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
        })
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            if i == retries - 1:
                raise
            print(f"[gemini] retry {i + 1}: {str(exc)[:120]}", flush=True)
            time.sleep(15)


def compress(sid):
    src = SHOT_DIR / f"{sid}.mp4"
    dst = TMP_DIR / f"{sid}_{int(src.stat().st_mtime)}.mp4"
    if not dst.exists():
        for stale in TMP_DIR.glob(f"{sid}_*.mp4"):
            stale.unlink()
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vf", "scale=480:-2",
             "-c:v", "libx264", "-crf", "30", "-c:a", "aac", "-b:a", "64k", str(dst)],
            capture_output=True,
        )
    return dst


def audit_shot(s):
    sid = s["shot_id"]
    try:
        clip = compress(sid)
        b64 = base64.b64encode(clip.read_bytes()).decode()
        prompt = (
            f"这是一段动画配音质检。角色应念台词（一遍）：「{s['speech']}」\n"
            f"角色声线应为：{VOICE_EXPECT[s['actor']]}\n\n"
            "请仔细听音频，逐字转写实际说出的内容（包括重复、卡顿、怪声、外语、无意义音节），"
            "然后严格审核以下缺陷：\n"
            "A. 同一句/整段台词念了两遍或以上\n"
            "B. 开头或结尾有怪声、杂音、别人的声音、参考音频漏播\n"
            "C. 出现听不懂的内容（外语、含糊不清、无意义音节）\n"
            "D. 漏了台词的关键部分（少几个语气词不算）\n"
            "E. 音色明显不符（比如变成低沉男声）\n"
            "F. 语音突然中断没说完\n"
            "G. 出现背景音乐、配乐、转场音效、电话提示音、弹簧等卡通音效——注意：画面里真实动作发出的声音"
            "（拍桌子、敲桌子、纸张响）是正常的，不算缺陷\n"
            "H. 说话中途卡壳、异常停顿、明显换气重启（正常的句读停顿不算）\n"
            "只在确定有缺陷时才判fail。轻微语气差异、语速快慢、与画面动作相符的音效都算pass。\n\n"
            '最后一行输出严格JSON：{"transcript":"实际内容","issues":["A:说明",...],"verdict":"pass|fail"}'
        )
        text = gemini([
            {"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ])
        match = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
        if not match:
            return sid, "error", {"raw": text[:400]}
        verdict = json.loads(match.group(0))
        return sid, verdict.get("verdict", "error"), verdict
    except Exception as exc:  # noqa: BLE001
        return sid, "error", {"exception": str(exc)[:300]}


# ---------------------------------------------------------------- loop

def concat_film():
    list_file = HERE / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file 'shots/{s['shot_id']}.mp4'" for s in PLAN["shots"]),
        encoding="utf-8",
    )
    final = HERE / "chiikawa_zhang_consult_full.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(final)],
        capture_output=True, text=True, cwd=str(HERE),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"concat failed: {proc.stderr[-400:]}")
    return final


def main():
    shots = PLAN["shots"]
    history = []
    missing = [s for s in shots if not (SHOT_DIR / f"{s['shot_id']}.mp4").exists()]
    if missing:
        print(f"=== pre-render: {len(missing)} missing shots ===", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(render_shot, missing))
        for s, ok in zip(missing, outcomes):
            if not ok:
                print(f"[render] {s['shot_id']} PERMANENT FAIL", flush=True)
    for round_no in range(1, MAX_ROUNDS + 1):
        print(f"=== round {round_no}: auditing {len(shots)} shots ===", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(audit_shot, shots))
        fails, errors = [], []
        for sid, verdict, detail in results:
            if verdict == "fail":
                fails.append(sid)
                print(f"[audit] {sid} FAIL: {json.dumps(detail.get('issues', []), ensure_ascii=False)}", flush=True)
            elif verdict == "error":
                errors.append(sid)
                print(f"[audit] {sid} ERROR: {json.dumps(detail, ensure_ascii=False)[:200]}", flush=True)
            else:
                print(f"[audit] {sid} pass", flush=True)
        history.append({
            "round": round_no,
            "fails": fails,
            "errors": errors,
            "details": {sid: d for sid, v, d in results if v != "pass"},
        })
        REPORT_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        if not fails:
            print("=== all shots pass ===", flush=True)
            break
        if round_no == MAX_ROUNDS:
            print(f"=== max rounds reached; still failing: {fails} ===", flush=True)
            break
        redo = [s for s in shots if s["shot_id"] in fails]
        for s in redo:
            target = SHOT_DIR / f"{s['shot_id']}.mp4"
            if target.exists():
                target.unlink()
        print(f"=== round {round_no}: re-rendering {len(redo)} shots ===", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(render_shot, redo))
        for s, ok in zip(redo, outcomes):
            if not ok:
                print(f"[render] {s['shot_id']} PERMANENT FAIL", flush=True)
    final = concat_film()
    size_mb = final.stat().st_size / 1048576
    print(f"=== final film: {final.name} {size_mb:.0f}MB ===", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
