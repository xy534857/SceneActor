#!/usr/bin/env python3
"""SceneActor as a service.

POST /v1/productions            {"material": "<free-form script + character docs>"}
                                -> {"production_id", "spec"}          (sync, ~1 min)
POST /v1/performances           {"production_id" | "spec", "review": true}
                                -> {"job_id", "status": "running"}   (async)
GET  /v1/performances/<job_id>  -> {"status": "running|done|failed", "progress",
                                    "document"?}
GET  /v1/health                 -> {"ok": true}

A performance runs minutes to an hour depending on the generation model, so it
is job-based: submit, then poll. Compiled specs and finished documents are kept
in memory and mirrored to --state-dir for inspection and restart recovery.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sceneactor.model import FallbackModel, GatewayCompletion
from sceneactor.service import ProductionCompileError, ProductionSpec, compile_production, perform

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8377)
parser.add_argument("--gateway-url", required=True)
parser.add_argument("--gateway-key", required=True)
parser.add_argument("--compile-model", default="claude-opus-4.8")
parser.add_argument("--compile-fallback", default="gemini-3.1-flash-lite")
parser.add_argument("--generation-model", default="claude-fable-5")
parser.add_argument("--generation-fallback", default="claude-opus-4.8")
parser.add_argument("--review-model", default="claude-fable-5")
parser.add_argument("--review-fallback", default="claude-opus-4.8")
parser.add_argument("--state-dir", default=".sceneactor-service")
args = parser.parse_args()

STATE = Path(args.state_dir)
(STATE / "productions").mkdir(parents=True, exist_ok=True)
(STATE / "performances").mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_PRODUCTIONS: dict[str, ProductionSpec] = {}
_JOBS: dict[str, dict] = {}


def _model(primary: str, fallback: str) -> FallbackModel:
    return FallbackModel(
        GatewayCompletion(args.gateway_url, args.gateway_key),
        primary=primary,
        fallback=fallback,
    )


def _load_state() -> None:
    for path in (STATE / "productions").glob("*.json"):
        try:
            _PRODUCTIONS[path.stem] = ProductionSpec.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (ValueError, json.JSONDecodeError):
            continue
    for path in (STATE / "performances").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if record.get("status") == "running":
            record["status"] = "failed"
            record["error"] = "service restarted mid-performance"
        _JOBS[path.stem] = record


def _persist_job(job_id: str) -> None:
    (STATE / "performances" / f"{job_id}.json").write_text(
        json.dumps(_JOBS[job_id], ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _run_performance(job_id: str, spec: ProductionSpec, want_review: bool) -> None:
    total = sum(episode.scene.max_turns for episode in spec.episodes)
    done_scenes: dict[str, int] = {}

    def on_turn(scene_id: str, turn: int, actor_id: str) -> None:
        done_scenes[scene_id] = turn
        with _LOCK:
            _JOBS[job_id]["progress"] = {
                "scene": scene_id,
                "turn": turn,
                "done": sum(done_scenes.values()),
                "of": total,
                "actor": actor_id,
            }

    try:
        document = perform(
            spec,
            _model(args.generation_model, args.generation_fallback),
            review=_model(args.review_model, args.review_fallback) if want_review else None,
            on_turn=on_turn,
        )
        with _LOCK:
            _JOBS[job_id].update(status="done", document=document)
    except Exception as exc:  # noqa: BLE001 — job boundary must capture, not crash the server
        with _LOCK:
            _JOBS[job_id].update(status="failed", error=str(exc)[:1000])
    _persist_job(job_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "SceneActor/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 2_000_000:
            raise ValueError("body must be 1 byte to 2 MB")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        if self.path == "/v1/health":
            self._send(200, {"ok": True})
            return
        if self.path.startswith("/v1/performances/"):
            job_id = self.path.rsplit("/", 1)[-1]
            with _LOCK:
                record = _JOBS.get(job_id)
            if record is None:
                self._send(404, {"error": "unknown job"})
                return
            self._send(200, record)
            return
        self._send(404, {"error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
            return
        if self.path == "/v1/productions":
            self._compile(body)
            return
        if self.path == "/v1/performances":
            self._perform(body)
            return
        self._send(404, {"error": "unknown path"})

    def _compile(self, body: dict) -> None:
        material = body.get("material", "")
        if not isinstance(material, str) or not material.strip():
            self._send(400, {"error": "material (non-empty string) is required"})
            return
        try:
            spec = compile_production(material, _model(args.compile_model, args.compile_fallback))
        except ProductionCompileError as exc:
            self._send(422, {"error": f"compile failed: {exc}"})
            return
        production_id = f"prod:{uuid4().hex[:12]}"
        with _LOCK:
            _PRODUCTIONS[production_id] = spec
        (STATE / "productions" / f"{production_id}.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        self._send(201, {"production_id": production_id, "spec": spec.to_dict()})

    def _perform(self, body: dict) -> None:
        spec: ProductionSpec | None = None
        production_id = body.get("production_id")
        if isinstance(production_id, str):
            with _LOCK:
                spec = _PRODUCTIONS.get(production_id)
            if spec is None:
                self._send(404, {"error": "unknown production_id"})
                return
        elif isinstance(body.get("spec"), dict):
            try:
                spec = ProductionSpec.from_dict(body["spec"])
            except ValueError as exc:
                self._send(400, {"error": f"invalid spec: {exc}"})
                return
        else:
            self._send(400, {"error": "production_id or spec is required"})
            return
        job_id = f"job:{uuid4().hex[:12]}"
        with _LOCK:
            _JOBS[job_id] = {"status": "running", "progress": {"done": 0, "of": sum(e.scene.max_turns for e in spec.episodes)}}
        _persist_job(job_id)
        threading.Thread(
            target=_run_performance,
            args=(job_id, spec, bool(body.get("review", True))),
            daemon=True,
        ).start()
        self._send(202, {"job_id": job_id, "status": "running"})

    def log_message(self, format: str, *values) -> None:  # noqa: A002
        print(json.dumps({"http": format % values}, ensure_ascii=False), flush=True)


_load_state()
server = ThreadingHTTPServer((args.host, args.port), Handler)
print(json.dumps({"listening": f"http://{args.host}:{args.port}", "state": str(STATE)}), flush=True)
server.serve_forever()
