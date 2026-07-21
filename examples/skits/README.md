# Skit-to-Video Pipeline

Reproducible pipeline that turns a reviewed dialogue script into a multi-shot
character video via seedance (TokenRouter tencent-vod route). Distilled from
the chiikawa burger-debate production run.

## Flow

```
script (rehearsal JSON, blind-review passed)
  │  split turns into shots (<=15s each)
  ▼
project.json  ──  characters (portrait + clean voice + voice_desc)
  │               shots (camera / action / line / in_frame)
  ▼
scripts/run_skit_video.py
  upload  → plan  → submit → poll+download → verify → assemble
```

Planner logic (pure, tested): `src/sceneactor/skit_pipeline.py`
Driver (network + ffmpeg):    `scripts/run_skit_video.py`
Tests:                        `tests/test_skit_pipeline.py`

## Hard-won consistency rules (baked into the planner)

1. **Identity = images, not prose.** Every character visible in a shot gets its
   own single-character portrait attached as `identity_anchor`. Prompt text
   only points at the references. Portraits are cropped from ONE gpt-image
   model sheet so they share style/proportions.
2. **Voice refs must be clean.** Separate vocals with demucs, trim silence,
   loudnorm to -16 LUFS. Raw anime clips with BGM make voice cloning erratic.
   Note the provider does *reference-styled* voices, not spectral clones — for
   strict CV-accurate timbre swap in a TTS track afterwards.
3. **tencent-vod quirks.** `first_frame` cannot be mixed with other reference
   media (use `identity_anchor`/`scene_style` instead); VS 2.0 durations are
   integers in [4, 15]; task detail lives at `Response.AigcVideoTask` with
   `ErrCode != 0` meaning failure even when `Status == FINISH`.
4. **Strict clauses are load-bearing.** "NO human characters" and
   "NO subtitles/captions" prevent the two most common seedance mutations.
5. **Verify every take.** whisper transcript vs expected line (similarity
   >= 0.75) + resemblyzer voice match against the clean anchors. Regenerate
   only failed shots with a new `--tag`, then assemble with a tag map.

## Run the shipped example

```bash
cd ~/Workspace/SceneActor

# full run (upload assets, generate all 14 shots, verify)
PYTHONPATH=src python3 scripts/run_skit_video.py \
    --project examples/skits/burger_debate_zh/project.json \
    --workdir .tmp/skit-burger-zh

# inspect .tmp/skit-burger-zh/verify_v1.json, regenerate failed shots
PYTHONPATH=src python3 scripts/run_skit_video.py \
    --project examples/skits/burger_debate_zh/project.json \
    --workdir .tmp/skit-burger-zh --shots z11,z12 --tag v2

# assemble with per-shot take selection
echo '{"z11": "v2", "z12": "v2"}' > .tmp/skit-burger-zh/takes.json
PYTHONPATH=src python3 scripts/run_skit_video.py \
    --project examples/skits/burger_debate_zh/project.json \
    --workdir .tmp/skit-burger-zh --assemble .tmp/skit-burger-zh/takes.json
```

Requires: TokenRouter credential (`vendor/GodotAvatarVideoGen/credentials/`),
ffmpeg, and a verify venv with `openai-whisper` + `resemblyzer`
(default `~/Workspace/TemplateStudio/.tmp/venv-voice`).

## Building a new skit project

1. Get a script through rehearsal + blind review
   (`scripts/run_public_figure_rehearsal.py` or `run_script_rehearsal.py`).
2. Make anchors: one gpt-image **model sheet** with all characters labeled,
   crop per-character portraits; one **master stage** wide shot generated from
   the same references.
3. Make voice refs from the character library
   (`TemplateStudio/data/characters/*/manifest.json`), demucs + loudnorm.
4. Split turns into shots: one speaker per shot, <=15s, split long turns;
   physical actions from the script's `action`/`delivery` fields.
5. Write `project.json` (schema enforced by `SkitProject.load`), run the
   driver, iterate on failed shots via `--shots`/`--tag`.

Rights: all shipped assets are research-only; `publication_authorized: false`
carries over from the source packs.
