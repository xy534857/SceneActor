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
from urllib.parse import unquote, urlsplit
from datetime import datetime, timezone
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
parser.add_argument("--api-key", default="", help="Bearer token required for all endpoints except /v1/health")
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
UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"


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


def _models_from(body: dict) -> dict:
    """Per-request model overrides: {"models": {"generation": "...", "generation_fallback": "...", "review": "...", "review_fallback": "...", "compile": "...", "compile_fallback": "..."}}."""
    raw = body.get("models", {})
    if not isinstance(raw, dict):
        raise ValueError("models must be an object")
    allowed = {"generation", "generation_fallback", "review", "review_fallback", "compile", "compile_fallback"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown model roles: {sorted(unknown)}; allowed: {sorted(allowed)}")
    return {key: str(value) for key, value in raw.items() if str(value).strip()}


def _run_performance(job_id: str, spec: ProductionSpec, want_review: bool, models: dict) -> None:
    with _LOCK:
        _JOBS[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()
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

    generation = _model(
        models.get("generation", args.generation_model),
        models.get("generation_fallback", args.generation_fallback),
    )
    reviewer = _model(
        models.get("review", args.review_model),
        models.get("review_fallback", args.review_fallback),
    ) if want_review else None
    try:
        document = perform(spec, generation, review=reviewer, on_turn=on_turn)
        with _LOCK:
            _JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
            _JOBS[job_id].update(status="done", document=document)
    except Exception as exc:  # noqa: BLE001 — job boundary must capture, not crash the server
        with _LOCK:
            _JOBS[job_id].update(status="failed", error=str(exc)[:1000])
            _JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
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

    def _send_html(self, path: Path) -> None:
        if not path.is_file():
            self._send(404, {"error": "UI asset missing"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _production_summary(self, production_id: str, spec: ProductionSpec) -> dict:
        path = STATE / "productions" / f"{production_id}.json"
        return {
            "production_id": production_id,
            "scenes": [episode.scene.scene_id for episode in spec.episodes],
            "actors": [actor.persona.name for actor in spec.actors],
            "turns": sum(episode.scene.max_turns for episode in spec.episodes),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if path.exists() else "",
        }

    def _authorized(self) -> bool:
        if not args.api_key:
            return True
        supplied = self.headers.get("Authorization", "")
        if supplied == f"Bearer {args.api_key}":
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 2_000_000:
            raise ValueError("body must be 1 byte to 2 MB")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        path = urlsplit(self.path).path
        if path in ("/", "/ui", "/index.html"):
            self._send_html(UI_FILE)
            return
        if path == "/v1/health":
            self._send(200, {"ok": True})
            return
        if not self._authorized():
            return
        if path == "/v1/config":
            self._send(200, {"models": {
                "compile": args.compile_model,
                "compile_fallback": args.compile_fallback,
                "generation": args.generation_model,
                "generation_fallback": args.generation_fallback,
                "review": args.review_model,
                "review_fallback": args.review_fallback,
            }})
            return
        if path == "/v1/productions":
            with _LOCK:
                items = [self._production_summary(pid, spec) for pid, spec in _PRODUCTIONS.items()]
            self._send(200, {"productions": sorted(items, key=lambda item: item["updated_at"], reverse=True)})
            return
        if path.startswith("/v1/productions/"):
            production_id = unquote(path.rsplit("/", 1)[-1])
            with _LOCK:
                spec = _PRODUCTIONS.get(production_id)
            if spec is None:
                self._send(404, {"error": "unknown production"})
                return
            self._send(200, {"production_id": production_id, "spec": spec.to_dict()})
            return
        if path == "/v1/performances":
            with _LOCK:
                items = []
                for job_id, record in _JOBS.items():
                    items.append({key: value for key, value in record.items() if key != "document"} | {"job_id": job_id})
            self._send(200, {"jobs": sorted(items, key=lambda item: item.get("started_at", ""), reverse=True)})
            return
        if path.startswith("/v1/performances/"):
            job_id = unquote(path.rsplit("/", 1)[-1])
            with _LOCK:
                record = _JOBS.get(job_id)
            if record is None:
                self._send(404, {"error": "unknown job"})
                return
            self._send(200, record)
            return
        self._send(404, {"error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        if not self._authorized():
            return
        path = urlsplit(self.path).path
        try:
            body = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
            return
        if path == "/v1/productions":
            self._compile(body)
            return
        if path == "/v1/performances":
            self._perform(body)
            return
        self._send(404, {"error": "unknown path"})

    def _compile(self, body: dict) -> None:
        try:
            models = _models_from(body)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        material = body.get("material", "")
        if not isinstance(material, str) or not material.strip():
            self._send(400, {"error": "material (non-empty string) is required"})
            return
        try:
            spec = compile_production(
                material,
                _model(
                    models.get("compile", args.compile_model),
                    models.get("compile_fallback", args.compile_fallback),
                ),
            )
        except ProductionCompileError as exc:
            self._send(422, {"error": f"compile failed: {exc}"})
            return
        production_id = f"prod:{uuid4().hex[:12]}"
        with _LOCK:
            _PRODUCTIONS[production_id] = spec
        compiled_at = datetime.now(timezone.utc).isoformat()
        (STATE / "productions" / f"{production_id}.json").write_text(
            json.dumps(spec.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        self._send(201, {"production_id": production_id, "spec": spec.to_dict(), "compiled_at": compiled_at})
    def _perform(self, body: dict) -> None:
        try:
            models = _models_from(body)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
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
            _JOBS[job_id] = {
                "status": "running",
                "progress": {"done": 0, "of": sum(e.scene.max_turns for e in spec.episodes)},
                "production_id": production_id if isinstance(production_id, str) else "inline-spec",
                "models": {
                    "generation": models.get("generation", args.generation_model),
                    "review": models.get("review", args.review_model),
                },
            }
        _persist_job(job_id)
        threading.Thread(
            target=_run_performance,
            args=(job_id, spec, bool(body.get("review", True)), models),
            daemon=True,
        ).start()
        self._send(202, {"job_id": job_id, "status": "running"})

    def log_message(self, format: str, *values) -> None:  # noqa: A002
        print(json.dumps({"http": format % values}, ensure_ascii=False), flush=True)


_load_state()
server = ThreadingHTTPServer((args.host, args.port), Handler)
print(json.dumps({"listening": f"http://{args.host}:{args.port}", "state": str(STATE)}), flush=True)
server.serve_forever()
