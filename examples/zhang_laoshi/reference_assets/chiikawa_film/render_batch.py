"""Batch-render the chiikawa film shots via TokenRouter VS (seedance 2.0).

v4: NO frame chaining. Chaining last->first frame compounds generation loss
(each hop re-encodes an already-generated frame, so the picture degrades) and
VS rejects first_frame mixed with reference media anyway. Instead every shot
uses the SAME fixed per-actor anchor frame (scene_style Reference) exported
once from the best approved shot, so the room/camera/character stay identical
without cumulative blur — and shots can render fully in parallel.

Audio: the character wav is timbre reference only; the prompt now pins the
voice description and forbids replaying the reference clip's content, which
was leaking as strange noises at shot start.

Resumable: finished shots live at shots/<shot_id>.mp4 and are skipped.
Run: PYTHONPATH=src python3 examples/zhang_laoshi/reference_assets/chiikawa_film/render_batch.py
"""
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sceneactor.adapters.tokenrouter import ReferenceMedia, TokenRouterVideoClient  # noqa: E402

HERE = Path(__file__).resolve().parent
PLAN = json.loads((HERE / "film_plan.json").read_text(encoding="utf-8"))
SHOT_DIR = HERE / "shots"
SHOT_DIR.mkdir(exist_ok=True)

STYLE = PLAN["style"]
SCENES = PLAN["scenes"]
CAST = PLAN["cast"]
ANCHORS = PLAN["anchor_frames"]
VOICE_TEXT = PLAN["voice_text"]

client = TokenRouterVideoClient()

SANITIZE = {
    "说不要你就不要你": "想辞你就辞你",
    "他妈": "",
}


def sanitize(text):
    for bad, good in SANITIZE.items():
        text = text.replace(bad, good)
    return text


def shot_prompt(s, soften=False):
    scene = SCENES[s["actor"]]
    other = "电话那头" if s["actor"] == "father-liu" else "连麦那头"
    expr_note = "表情参考图里真人的表情就是这只小生物此刻的表情强度，把它移植到圆脸上。\n" if s.get("expr_ref") else ""
    return (
        f"{STYLE}\n{scene}\n"
        f"画面严格延续场景参考图：同一房间、同一机位、同一角色造型，只有表情和动作变化。\n"
        f"镜头：中景固定机位，角色占画面中心，正对镜头方向说话。\n"
        f"{s['expr_text']}\n{expr_note}"
        f"动作：{s['action'][:60]}\n"
        f"{VOICE_TEXT[s['actor']]}\n"
        f"参考音频只提供音色，不要复现参考音频里的内容；视频从角色开口说话开始，没有前奏杂音。\n"
        f"角色用中文大声说这句台词（对{other}说，语气与表情匹配，口型对上）：\n"
        f"「{sanitize(s['speech']) if soften else s['speech']}」\n"
        f"画面里没有其他角色出现，没有字幕，没有文字。"
    )


def refs_for(s):
    info = CAST[s["actor"]]
    refs = [
        ReferenceMedia(url=ANCHORS[s["actor"]], category="image", role="scene_style"),
        ReferenceMedia(url=info["identity_asset"], category="image", role="identity_anchor"),
        ReferenceMedia(url=info["voice_url"], category="audio", role="reference"),
    ]
    if s.get("expr_ref"):
        refs.append(ReferenceMedia(url=s["expr_ref"], category="image", role="face_anchor"))
    return refs


def render_shot(s):
    dest = SHOT_DIR / f"{s['shot_id']}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[skip] {s['shot_id']}", flush=True)
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
            print(f"[done] {s['shot_id']} {result.elapsed_seconds:.0f}s", flush=True)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {s['shot_id']} attempt {attempt}: {exc}", flush=True)
            if attempt < 3:
                time.sleep(10)
    return False


def main():
    shots = PLAN["shots"]
    print(f"rendering {len(shots)} shots", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(render_shot, shots))
    ok = sum(results)
    print(f"finished: {ok}/{len(shots)} ok", flush=True)
    if ok < len(shots):
        failed = [s["shot_id"] for s, r in zip(shots, results) if not r]
        print("failed shots:", ", ".join(failed), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
