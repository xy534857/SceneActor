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

from sceneactor.chat import ChatSession, validate_record
from sceneactor.forge import expand_style, forge_from_fusion, forge_from_questionnaire, next_question
from sceneactor.genome import GenomeError
from sceneactor.cognition import CognitionModelError
from sceneactor.model import FallbackModel, GatewayCompletion
from sceneactor.performance import PerformanceModelError
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
parser.add_argument("--chat-model", default="claude-fable-5")
parser.add_argument("--chat-fallback", default="claude-opus-4.8")
parser.add_argument("--triage-model", default="gemini-3.5-flash")
parser.add_argument("--genome-dir", default="data/genomes", help="persona library root (records/*.json + index.json)")
parser.add_argument("--state-dir", default=".sceneactor-service")
args = parser.parse_args()

STATE = Path(args.state_dir)
(STATE / "productions").mkdir(parents=True, exist_ok=True)
(STATE / "performances").mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_PRODUCTIONS: dict[str, ProductionSpec] = {}
_JOBS: dict[str, dict] = {}
_CHATS: dict[str, dict] = {}  # session_id -> {"session": ChatSession, "touched": float, "lock": Lock}
CHAT_TTL_SECONDS = 6 * 3600
CHAT_MAX_SESSIONS = 200
UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"
GENOME_DIR = Path(args.genome_dir)


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


def _card(record: dict, stem: str, library: str) -> dict:
    genome = record.get("genome", {})
    return {
        "person_id": record.get("person_id", stem),
        "display_name": record.get("display_name", stem),
        "name_en": record.get("name_en", ""),
        "region": record.get("region", ""),
        "industries": record.get("industries", []),
        "gender": record.get("gender", ""),
        "library": library,
        "forge": {k: v for k, v in (record.get("forge") or {}).items() if k in ("mode", "sources")},
        "trait_axes": {k: v.get("score") for k, v in genome.get("trait_axes", {}).items()},
        "blind_spots": [b.get("description", b) if isinstance(b, dict) else b for b in genome.get("blind_spots", [])][:2],
    }


def _library_index() -> list[dict]:
    """Distilled roster plus the custom/fusion partition."""
    cards = []
    for library, folder in (("roster", "records"), ("custom", "custom")):
        directory = GENOME_DIR / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            cards.append(_card(record, path.stem, library))
    return cards


def _load_record(person_id: str) -> dict | None:
    for folder in ("records", "custom"):
        path = GENOME_DIR / folder / f"{person_id}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
    return None


def _save_custom(record: dict) -> None:
    directory = GENOME_DIR / "custom"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['person_id']}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _reap_chats(now: float) -> None:
    dead = [sid for sid, item in _CHATS.items() if now - item["touched"] > CHAT_TTL_SECONDS]
    for sid in dead:
        _CHATS.pop(sid, None)
    if len(_CHATS) > CHAT_MAX_SESSIONS:
        for sid, _ in sorted(_CHATS.items(), key=lambda kv: kv[1]["touched"])[: len(_CHATS) - CHAT_MAX_SESSIONS]:
            _CHATS.pop(sid, None)


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
                "chat": args.chat_model,
                "chat_fallback": args.chat_fallback,
                "triage": args.triage_model,
            }})
            return
        if path == "/v1/personas":
            self._send(200, {"personas": _library_index()})
            return
        if path.startswith("/v1/personas/"):
            person_id = unquote(path.rsplit("/", 1)[-1])
            record = _load_record(person_id)
            if record is None:
                self._send(404, {"error": "unknown persona"})
                return
            self._send(200, {"record": record})
            return
        if path.startswith("/v1/chats/"):
            session_id = unquote(path.rsplit("/", 1)[-1])
            with _LOCK:
                item = _CHATS.get(session_id)
            if item is None:
                self._send(404, {"error": "unknown or expired chat session"})
                return
            self._send(200, {"session_id": session_id} | item["session"].to_dict())
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
        if path == "/v1/chats":
            self._chat_start(body)
            return
        if path.startswith("/v1/chats/"):
            self._chat_say(unquote(path.rsplit("/", 1)[-1]), body)
            return
        if path == "/v1/forge":
            self._forge(body)
            return
        if path == "/v1/forge/question":
            self._forge_question(body)
            return
        if path == "/v1/forge/style":
            self._forge_style(body)
            return
        self._send(404, {"error": "unknown path"})

    def _forge_style(self, body: dict) -> None:
        """Expand a natural-language style description into a speech spec."""
        try:
            result = expand_style(
                description=str(body.get("description", "")),
                name=str(body.get("name", "")),
                background=str(body.get("background", "")),
                complete=_model(str(body.get("model") or args.triage_model), args.chat_fallback),
            )
        except GenomeError as exc:
            self._send(422, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send(502, {"error": f"style model failed: {str(exc)[:200]}"})
            return
        self._send(200, {"style": result["text"], "style_pack": result["pack"]})

    def _forge_question(self, body: dict) -> None:
        """Generate the next adaptive questionnaire question."""
        try:
            question = next_question(
                name=str(body.get("name", "")),
                background=str(body.get("background", "")),
                style=str(body.get("style", "")),
                history=[
                    {"title": str(h.get("title", "")), "choice": str(h.get("choice", ""))}
                    for h in body.get("history", []) if isinstance(h, dict)
                ],
                complete=_model(str(body.get("model") or args.triage_model), args.chat_fallback),
            )
        except GenomeError as exc:
            self._send(422, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send(502, {"error": f"question model failed: {str(exc)[:200]}"})
            return
        self._send(200, question)

    def do_DELETE(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        if not self._authorized():
            return
        path = urlsplit(self.path).path
        if path.startswith("/v1/personas/"):
            person_id = unquote(path.rsplit("/", 1)[-1])
            target = GENOME_DIR / "custom" / f"{person_id}.json"
            if not person_id.startswith("custom-") or not target.is_file():
                self._send(404, {"error": "only existing custom personas can be deleted"})
                return
            target.unlink()
            self._send(200, {"deleted": person_id})
            return
        self._send(404, {"error": "unknown path"})

    def _forge(self, body: dict) -> None:
        """Create a custom persona from a questionnaire or by fusing library records."""
        mode = str(body.get("mode", ""))
        save = bool(body.get("save", True))
        try:
            if mode == "questionnaire":
                model = _model(str(body.get("model") or args.chat_model), args.chat_fallback)
                record = forge_from_questionnaire(
                    name=str(body.get("name", "")),
                    background=str(body.get("background", "")),
                    style=str(body.get("style", "")),
                    values=[str(v) for v in body.get("values", [])],
                    fear=str(body.get("fear", "")),
                    axes={k: v for k, v in (body.get("axes") or {}).items()},
                    complete=model,
                    history=[
                        {"title": str(h.get("title", "")), "choice": str(h.get("choice", ""))}
                        for h in body.get("history", []) if isinstance(h, dict)
                    ],
                )
            elif mode == "fusion":
                sources = body.get("sources", [])
                if not isinstance(sources, list):
                    self._send(400, {"error": "sources must be a list of {person_id, weight}"})
                    return
                parts = []
                for item in sources:
                    src = _load_record(str(item.get("person_id", "")))
                    if src is None:
                        self._send(404, {"error": f"unknown persona {item.get('person_id')!r}"})
                        return
                    parts.append((src, float(item.get("weight", 1.0))))
                record = forge_from_fusion(
                    name=str(body.get("name", "")),
                    parts=parts,
                    background=str(body.get("background", "")),
                    style=str(body.get("style", "")),
                    complete=_model(str(body.get("model") or args.triage_model), args.chat_fallback),
                )
            else:
                self._send(400, {"error": "mode must be questionnaire or fusion"})
                return
        except (GenomeError, ValueError) as exc:
            self._send(422, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._send(502, {"error": f"forge model failed: {str(exc)[:200]}"})
            return
        if save:
            _save_custom(record)
        self._send(201, {"record": record, "saved": save})

    def _chat_start(self, body: dict) -> None:
        """Create a chat session from the library (person_id) or an uploaded pack (record)."""
        record = None
        person_id = body.get("person_id")
        if isinstance(person_id, str) and person_id.strip():
            record = _load_record(person_id.strip())
            if record is None:
                self._send(404, {"error": f"unknown persona {person_id!r}"})
                return
        elif isinstance(body.get("record"), dict):
            record = body["record"]
            try:
                validate_record(record)
            except ValueError as exc:
                self._send(400, {"error": f"invalid persona pack: {exc}"})
                return
        else:
            self._send(400, {"error": "person_id (library) or record (uploaded pack) is required"})
            return
        chat_model = str(body.get("model") or args.chat_model)
        chat_fallback = str(body.get("fallback_model") or args.chat_fallback)
        search_enabled = bool(body.get("search", False))
        verbosity = str(body.get("verbosity") or "interview")
        if verbosity not in ("brief", "interview", "deep"):
            self._send(400, {"error": "verbosity must be one of: brief, interview, deep"})
            return
        try:
            session = ChatSession(
                record=record,
                model=_model(chat_model, chat_fallback),
                aux_model=_model(str(body.get("triage_model") or args.triage_model), chat_fallback) if search_enabled else None,
                scene=str(body.get("scene") or ""),
                lang=str(body.get("lang") or ""),
                search_enabled=search_enabled,
                verbosity=verbosity,
            )
        except (ValueError, KeyError) as exc:
            self._send(422, {"error": f"session build failed: {exc}"})
            return
        try:
            opening = session.open()
        except (CognitionModelError, PerformanceModelError, RuntimeError) as exc:
            self._send(502, {"error": f"opening turn failed: {str(exc)[:200]}"})
            return
        session_id = f"chat:{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).timestamp()
        with _LOCK:
            _reap_chats(now)
            _CHATS[session_id] = {"session": session, "touched": now, "lock": threading.Lock()}
        self._send(201, {
            "session_id": session_id,
            "person_id": session.record["person_id"],
            "display_name": session.display,
            "disclosure": session.disclosure,
            "scene": session.scene_text,
            "lang": session.lang,
            "search_enabled": session.search_enabled,
            "model": chat_model,
            "turn": opening.to_dict(),
        })

    def _chat_say(self, session_id: str, body: dict) -> None:
        line = body.get("message")
        if not isinstance(line, str) or not line.strip():
            self._send(400, {"error": "message (non-empty string) is required"})
            return
        with _LOCK:
            item = _CHATS.get(session_id)
            if item is not None:
                item["touched"] = datetime.now(timezone.utc).timestamp()
        if item is None:
            self._send(404, {"error": "unknown or expired chat session"})
            return
        if not item["lock"].acquire(blocking=False):
            self._send(409, {"error": "a turn is already in flight for this session"})
            return
        try:
            turn = item["session"].say(line.strip())
        except (CognitionModelError, PerformanceModelError, RuntimeError) as exc:
            self._send(502, {"error": f"turn failed: {str(exc)[:200]}"})
            return
        finally:
            item["lock"].release()
        self._send(200, {"session_id": session_id, "turn": turn.to_dict()})

    def _compile(self, body: dict) -> None:
        try:
            models = _models_from(body)
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        material = body.get("material", "")
        if isinstance(body.get("spec"), dict):
            # Register a caller-authored or hand-edited spec without recompiling.
            try:
                spec = ProductionSpec.from_dict(body["spec"])
            except ValueError as exc:
                self._send(400, {"error": f"invalid spec: {exc}"})
                return
        elif isinstance(material, str) and material.strip():
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
        else:
            self._send(400, {"error": "material (non-empty string) or spec (object) is required"})
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
