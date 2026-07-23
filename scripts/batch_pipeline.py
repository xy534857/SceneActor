#!/usr/bin/env python3
"""Run research + distill for many people with a bounded worker pool.

Every stage is a plain gateway HTTP call with a socket timeout — nothing can
hang forever. Failures are logged and skipped; re-run to retry stragglers.

Usage:
  PYTHONPATH=src python3 scripts/batch_pipeline.py --limit 20 --workers 6
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_person(person_id: str, model: str) -> str:
    research = ROOT / "data/genomes/research" / person_id
    env = {"PYTHONPATH": str(ROOT / "src")}
    if not (research / "02-conversations.txt").is_file():
        gen = subprocess.run(
            [sys.executable, "scripts/generate_research.py", person_id, "--model", model],
            cwd=ROOT, capture_output=True, text=True, timeout=3000, env={**env, "PATH": "/usr/bin:/bin"},
        )
        if gen.returncode != 0:
            return f"{person_id}: research FAILED — {gen.stdout.strip()[-200:]} {gen.stderr.strip()[-200:]}"
    dist = subprocess.run(
        [sys.executable, "scripts/distill_genome.py", person_id, "--model", model],
        cwd=ROOT, capture_output=True, text=True, timeout=3000, env={**env, "PATH": "/usr/bin:/bin"},
    )
    if dist.returncode != 0:
        return f"{person_id}: distill FAILED — {dist.stdout.strip()[-200:]} {dist.stderr.strip()[-200:]}"
    return f"{person_id}: OK"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default="claude-opus-4.8")
    args = parser.parse_args()

    index = json.loads((ROOT / "data/genomes/index.json").read_text(encoding="utf-8"))
    pending = [p["person_id"] for p in index["people"] if p["status"] == "planned"][: args.limit]
    print(f"pipeline: {len(pending)} people, {args.workers} workers", flush=True)

    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_person, pid, args.model): pid for pid in pending}
        for future in as_completed(futures):
            result = future.result()
            print(result, flush=True)
            if result.endswith("OK"):
                done += 1
            else:
                failed += 1
    print(f"pipeline done: {done} ok, {failed} failed", flush=True)


if __name__ == "__main__":
    main()
