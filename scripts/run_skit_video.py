"""Skit-to-video pipeline driver (chiikawa burger-debate flow, reusable).

Usage:
  PYTHONPATH=src python3 scripts/run_skit_video.py \
      --project examples/skits/burger_debate_zh/project.json \
      --workdir .tmp/skit-burger-zh \
      [--shots z01,z05]          # subset (iteration on failed shots)
      [--tag v2]                 # take tag for this run (default v1)
      [--verify-only]            # skip generation; verify existing clips
      [--assemble tags.json]     # concat using per-shot take tags

Stages: upload -> plan -> submit -> poll+download -> verify -> assemble.

Every stage writes its artifacts under --workdir so any stage can be re-run:
  uploads.json        asset key -> public URL
  plan.json           per-shot prompts (inspect before submitting)
  tasks_<tag>.json    shot_id -> provider task id
  clips/<shot>_<tag>.mp4
  verify_<tag>.json   transcript similarity + voice match per shot
  final.mp4

Verification needs a venv with openai-whisper + resemblyzer:
  --voice-venv PATH   (default: ~/Workspace/TemplateStudio/.tmp/venv-voice)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sceneactor.adapters.tokenrouter import (  # noqa: E402
    TokenRouterProviderConfig,
    TokenRouterVideoClient,
)
from sceneactor.skit_pipeline import (  # noqa: E402
    SkitProject,
    assemble_command,
    concat_manifest,
    plan_tasks,
    upload_keys,
)

parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True)
parser.add_argument("--workdir", required=True)
parser.add_argument("--shots", default="", help="comma-separated shot ids; default all")
parser.add_argument("--tag", default="v1", help="take tag for generated clips")
parser.add_argument("--verify-only", action="store_true")
parser.add_argument("--assemble", default="", help="JSON file: shot_id -> take tag")
parser.add_argument("--voice-venv", default=str(Path.home() / "Workspace/TemplateStudio/.tmp/venv-voice"))
parser.add_argument("--poll-timeout", type=int, default=2800)
args = parser.parse_args()

project = SkitProject.load(args.project)
workdir = Path(args.workdir)
clips = workdir / "clips"
clips.mkdir(parents=True, exist_ok=True)
project_dir = Path(args.project).resolve().parent
selected = [s for s in project.shots if not args.shots or s.shot_id in args.shots.split(",")]
client = TokenRouterVideoClient(TokenRouterProviderConfig.load())
log = lambda **kw: print(json.dumps(kw, ensure_ascii=False), flush=True)  # noqa: E731


def stage_upload() -> dict[str, str]:
    path = workdir / "uploads.json"
    uploads: dict[str, str] = json.loads(path.read_text()) if path.is_file() else {}
    for key in upload_keys(project):
        if key in uploads or key.startswith(("http://", "https://")):
            continue
        local = (project_dir / key).resolve()
        if not local.is_file():
            raise FileNotFoundError(f"asset not found: {local}")
        uploads[key] = client.upload_reference(local)
        log(stage="upload", key=key)
    path.write_text(json.dumps(uploads, indent=2))
    return uploads


def _extract_last_frame(video: Path) -> Path:
    """Grab the real final frame of a rendered clip for chained continuation."""
    frame = video.with_suffix(".last.jpg")
    # -sseof must land inside the VIDEO stream: audio often outlasts video by
    # ~0.2s, so a tiny offset seeks past the final frame and emits nothing.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(video),
         "-frames:v", "1", "-update", "1", "-q:v", "2", str(frame)], check=True)
    if not frame.is_file() or frame.stat().st_size == 0:
        raise RuntimeError(f"last-frame extraction produced nothing: {video}")
    return frame


def stage_submit_poll(uploads: dict[str, str]) -> dict[str, dict]:
    """Submit shots in chain-aware waves.

    Unchained shots are submitted in parallel. A shot with
    ``chain_from_previous`` waits for its predecessor's clip, extracts the real
    last frame, uploads it, and attaches it as the final identity anchor.
    """
    from sceneactor.skit_pipeline import (
        build_shot_prompt,
        build_shot_references,
        build_still_prompts,
    )

    selected_ids = {s.shot_id for s in selected}
    results: dict[str, dict] = {}
    tasks: dict[str, str] = {}
    prev_frame_url: dict[str, str] = {}  # shot_id -> its own last-frame URL
    order = [s for s in project.shots if s.shot_id in selected_ids]
    plan_dump = []

    def _gen_still(shot, which: str, prompt: str) -> str:
        """Generate one composition still via gpt-image-2 edits (v3-proven path)."""
        import base64
        import mimetypes
        import uuid
        refs = [project_dir / project.characters[c].portrait for c in shot.visible()]
        refs.append(project_dir / project.scene_ref)
        out = workdir / "stills" / f"{shot.shot_id}_{which}.png"
        out.parent.mkdir(exist_ok=True)
        if not out.is_file():
            key_file = Path(__file__).resolve().parents[1] / \
                "examples/zhang_laoshi/ablation/_openai_key.txt"
            api_key = key_file.read_text().strip()
            boundary = uuid.uuid4().hex
            parts = []
            for name, value in (("model", "gpt-image-2"), ("prompt", prompt),
                                ("size", "1536x1024")):
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; '
                    f'name="{name}"\r\n\r\n{value}\r\n'.encode())
            for rp in refs:
                mime = mimetypes.guess_type(str(rp))[0] or "image/png"
                parts.append(
                    (f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; '
                     f'filename="{rp.name}"\r\nContent-Type: {mime}\r\n\r\n').encode()
                    + rp.read_bytes() + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            req = urllib.request.Request(
                "https://api.openai.com/v1/images/edits", data=b"".join(parts),
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                payload = json.loads(resp.read())
            out.write_bytes(base64.b64decode(payload["data"][0]["b64_json"]))
        url = client.upload_reference(out)
        log(stage="still", shot=shot.shot_id, which=which)
        return url

    def submit_one(shot) -> str:
        prev_url = ""
        if shot.chain_from_previous:
            idx = [s.shot_id for s in project.shots].index(shot.shot_id)
            if idx > 0:
                prev_id = project.shots[idx - 1].shot_id
                prev_url = prev_frame_url.get(prev_id, "")
                if not prev_url:
                    prev_clip = clips / f"{prev_id}_{args.tag}.mp4"
                    if prev_clip.is_file():
                        prev_url = client.upload_reference(_extract_last_frame(prev_clip))
                        prev_frame_url[prev_id] = prev_url
        still_urls = {}
        for which, sprompt in build_still_prompts(project, shot).items():
            still_urls[which] = _gen_still(shot, which, sprompt)
        refs = build_shot_references(
            project, shot, uploads, prev_last_frame=prev_url,
            still_first=still_urls.get("first", ""),
            still_last=still_urls.get("last", ""))
        prompt = build_shot_prompt(project, shot)
        plan_dump.append({"shot_id": shot.shot_id, "prompt": prompt,
                          "references": [r.to_file_info() for r in refs]})
        payload = client.build_create_payload(
            prompt, model=project.model, version=project.version,
            resolution=project.resolution, duration=shot.duration,
            aspect_ratio=project.aspect_ratio, audio=True, references=refs)
        tid = client.create_task(payload)
        log(stage="submit", shot=shot.shot_id, chained=bool(prev_url), task=tid[-16:])
        return tid

    # wave 1: everything not chained
    for shot in order:
        if not shot.chain_from_previous:
            tasks[shot.shot_id] = submit_one(shot)
    results.update(stage_poll(tasks))
    # chained shots: submit sequentially as predecessors land
    for shot in order:
        if shot.chain_from_previous:
            wave = {shot.shot_id: submit_one(shot)}
            results.update(stage_poll(wave))
            clip = clips / f"{shot.shot_id}_{args.tag}.mp4"
            if clip.is_file():
                prev_frame_url[shot.shot_id] = ""  # lazily extracted if needed
    (workdir / "plan.json").write_text(json.dumps(plan_dump, ensure_ascii=False, indent=2))
    (workdir / f"tasks_{args.tag}.json").write_text(json.dumps(tasks, indent=2))
    return results


def stage_poll(tasks: dict[str, str]) -> dict[str, dict]:
    pending, results, t0 = dict(tasks), {}, time.time()
    while pending and time.time() - t0 < args.poll_timeout:
        for sid, tid in list(pending.items()):
            body = client._request("DescribeTaskDetail", {"TaskId": tid})
            info = body.get("Response", {})
            av = info.get("AigcVideoTask") or {}
            if str(info.get("Status", "")) != "FINISH":
                continue
            err = av.get("ErrCode", 0)
            if err:
                results[sid] = {"ok": False, "msg": str(av.get("Message", ""))[:300]}
                log(stage="poll", shot=sid, ok=False, msg=results[sid]["msg"][:120])
            else:
                infos = (av.get("Output") or {}).get("FileInfos") or []
                url = infos[0].get("FileUrl", "") if infos else ""
                fp = clips / f"{sid}_{args.tag}.mp4"
                urllib.request.urlretrieve(url, fp)
                results[sid] = {"ok": True, "path": str(fp), "bytes": fp.stat().st_size}
                log(stage="poll", shot=sid, ok=True, kb=fp.stat().st_size // 1024)
            del pending[sid]
        if pending:
            time.sleep(40)
    if pending:
        log(stage="poll", timeout=sorted(pending))
    return results


VERIFY_SNIPPET = r"""
import sys, json, subprocess, tempfile
from pathlib import Path
import numpy as np
import whisper
from resemblyzer import VoiceEncoder, preprocess_wav
lang, refs_json, specs = sys.argv[1], sys.argv[2], sys.argv[3:]
model = whisper.load_model("small")
enc = VoiceEncoder()
anchors = {k: enc.embed_utterance(preprocess_wav(v)) for k, v in json.loads(refs_json).items()}
out = {}
for spec in specs:
    sid, speaker, path = spec.split("::", 2)
    heard = " ".join(x["text"].strip() for x in model.transcribe(path, language=lang)["segments"])
    w = Path(tempfile.mktemp(suffix=".wav"))
    subprocess.run(["ffmpeg","-y","-v","error","-i",path,"-vn","-ac","1","-ar","16000",str(w)],check=True)
    emb = enc.embed_utterance(preprocess_wav(w)); w.unlink()
    sims = {k: float(np.dot(emb, a)) for k, a in anchors.items()}
    best = max(sims, key=sims.get)
    out[sid] = {"heard": heard, "voice_best": best, "voice_sim": round(sims[speaker], 3)}
print(json.dumps(out, ensure_ascii=False))
"""


def stage_verify() -> dict:
    vpy = Path(args.voice_venv) / "bin/python"
    refs = {c.code: str((project_dir / c.voice_ref).resolve()) for c in project.characters.values()}
    specs, expected = [], {}
    for s in selected:
        fp = clips / f"{s.shot_id}_{args.tag}.mp4"
        if fp.is_file():
            specs.append(f"{s.shot_id}::{s.speaker}::{fp}")
            expected[s.shot_id] = s
    snippet = workdir / "_verify.py"
    snippet.write_text(VERIFY_SNIPPET)
    proc = subprocess.run(
        [str(vpy), str(snippet), project.language, json.dumps(refs), *specs],
        capture_output=True, text=True, timeout=1800)
    if proc.returncode:
        raise RuntimeError(f"verify failed: {proc.stderr[-500:]}")
    raw = json.loads(proc.stdout.strip().splitlines()[-1])
    report = {}
    for sid, r in raw.items():
        s = expected[sid]
        norm = lambda t: re.sub(r"[^0-9a-z\u4e00-\u9fff ]", "", t.lower())  # noqa: E731
        sim = difflib.SequenceMatcher(None, norm(s.line), norm(r["heard"])).ratio()
        report[sid] = {
            "line_sim": round(sim, 2),
            "heard": r["heard"],
            "voice_best": r["voice_best"],
            "voice_expected": s.speaker,
            "voice_sim": r["voice_sim"],
            "voice_ok": r["voice_best"] == s.speaker,
            "line_ok": sim >= 0.75,
        }
        log(stage="verify", shot=sid, line_sim=report[sid]["line_sim"],
            voice_ok=report[sid]["voice_ok"])
    (workdir / f"verify_{args.tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _clip_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def stage_assemble(tag_by_shot: dict[str, str]) -> Path:
    final = workdir / "final.mp4"
    has_transitions = any(s.transition_in != "cut" for s in project.shots[1:])
    if has_transitions:
        durations = {
            s.shot_id: _clip_duration(clips / f"{s.shot_id}_{tag_by_shot.get(s.shot_id, 'v1')}.mp4")
            for s in project.shots}
        cmd = assemble_command(project, clips, tag_by_shot, durations, final)
        log(stage="assemble", mode="xfade",
            transitions=[s.transition_in for s in project.shots[1:]])
        subprocess.run(cmd, check=True)
    else:
        concat = workdir / "concat.txt"
        concat.write_text(concat_manifest(project, clips, tag_by_shot))
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
             "-vf", "scale=1280:720,fps=24", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(final)],
            check=True)
        log(stage="assemble", mode="concat")
    log(stage="assemble", final=str(final), kb=final.stat().st_size // 1024)
    return final


if args.assemble:
    stage_assemble(json.loads(Path(args.assemble).read_text()))
elif args.verify_only:
    stage_verify()
else:
    uploads = stage_upload()
    results = stage_submit_poll(uploads)
    ok = [sid for sid, r in results.items() if r.get("ok")]
    if ok:
        stage_verify()
    failed = [sid for sid, r in results.items() if not r.get("ok")]
    log(stage="done", ok=len(ok), failed=failed)
