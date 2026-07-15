"""Append-only turn events, idempotent batches, and command journaling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from time import time_ns
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RuntimeEvent:
    id: str
    batch_id: str
    branch_id: str
    turn_id: str
    command_id: str
    kind: str
    actor_id: str
    scene_id: str
    payload: Mapping[str, Any]
    visible_to: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    caused_by: tuple[str, ...] = ()
    order: int = 0

    @classmethod
    def create(
        cls,
        *,
        batch_id: str,
        branch_id: str,
        turn_id: str,
        command_id: str,
        kind: str,
        actor_id: str,
        scene_id: str,
        payload: Mapping[str, Any],
        visible_to: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
        caused_by: Iterable[str] = (),
        order: int = 0,
    ) -> "RuntimeEvent":
        body = {
            "batch_id": batch_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "command_id": command_id,
            "kind": kind,
            "actor_id": actor_id,
            "scene_id": scene_id,
            "payload": dict(payload),
            "visible_to": list(visible_to),
            "evidence_refs": list(evidence_refs),
            "caused_by": list(caused_by),
            "order": order,
        }
        return cls(id=f"event:{_digest(body)[:24]}", **body)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeEvent":
        return cls(
            id=str(data["id"]),
            batch_id=str(data["batch_id"]),
            branch_id=str(data["branch_id"]),
            turn_id=str(data["turn_id"]),
            command_id=str(data.get("command_id", "")),
            kind=str(data["kind"]),
            actor_id=str(data["actor_id"]),
            scene_id=str(data["scene_id"]),
            payload=dict(data.get("payload", {})),
            visible_to=tuple(str(item) for item in data.get("visible_to", [])),
            evidence_refs=tuple(str(item) for item in data.get("evidence_refs", [])),
            caused_by=tuple(str(item) for item in data.get("caused_by", [])),
            order=int(data.get("order", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurnEventBatch:
    batch_id: str
    branch_id: str
    turn_id: str
    command_id: str
    idempotency_key: str
    expected_actor_revisions: Mapping[str, int]
    expected_scene_revision: int
    host_receipt: Mapping[str, Any]
    lifecycle: str
    performance_status: str
    completion_reason: str
    public_performance_intent: Mapping[str, Any]
    events: tuple[RuntimeEvent, ...]
    immutable_content_hash: str

    @classmethod
    def create(
        cls,
        *,
        batch_id: str,
        branch_id: str,
        turn_id: str,
        command_id: str,
        expected_actor_revisions: Mapping[str, int],
        expected_scene_revision: int,
        host_receipt: Mapping[str, Any],
        lifecycle: str,
        performance_status: str,
        events: Iterable[RuntimeEvent],
        public_performance_intent: Mapping[str, Any] | None = None,
        completion_reason: str = "",
    ) -> "TurnEventBatch":
        items = tuple(events)
        if lifecycle not in {"prepared", "host_pending", "host_completed", "performance_pending", "committed"}:
            raise ValueError(f"invalid batch lifecycle: {lifecycle}")
        if performance_status not in {"pending", "complete", "failed"}:
            raise ValueError(f"invalid performance status: {performance_status}")
        body = {
            "batch_id": batch_id,
            "branch_id": branch_id,
            "turn_id": turn_id,
            "command_id": command_id,
            "expected_actor_revisions": dict(expected_actor_revisions),
            "expected_scene_revision": expected_scene_revision,
            "host_receipt": dict(host_receipt),
            "lifecycle": lifecycle,
            "performance_status": performance_status,
            "public_performance_intent": dict(public_performance_intent or {}),
            "completion_reason": completion_reason,
            "events": [item.to_dict() for item in items],
        }
        return cls(
            idempotency_key=f"batch:{branch_id}:{batch_id}",
            immutable_content_hash=_digest(body),
            events=items,
            **{key: value for key, value in body.items() if key != "events"},
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnEventBatch":
        return cls(
            batch_id=str(data["batch_id"]),
            branch_id=str(data["branch_id"]),
            turn_id=str(data["turn_id"]),
            command_id=str(data.get("command_id", "")),
            idempotency_key=str(data["idempotency_key"]),
            expected_actor_revisions={str(key): int(value) for key, value in dict(data.get("expected_actor_revisions", {})).items()},
            expected_scene_revision=int(data.get("expected_scene_revision", 0)),
            host_receipt=dict(data.get("host_receipt", {})),
            lifecycle=str(data["lifecycle"]),
            performance_status=str(data["performance_status"]),
            public_performance_intent=dict(data.get("public_performance_intent", {})),
            completion_reason=str(data.get("completion_reason", "")),
            events=tuple(RuntimeEvent.from_dict(item) for item in data.get("events", [])),
            immutable_content_hash=str(data["immutable_content_hash"]),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["events"] = [event.to_dict() for event in self.events]
        return data


@dataclass(frozen=True)
class TurnEventFollowUp:
    follow_up_id: str
    parent_batch_id: str
    branch_id: str
    idempotency_key: str
    expected_batch_status: str
    lifecycle_status: str
    performance_status: str
    host_receipt: Mapping[str, Any]
    events: tuple[RuntimeEvent, ...]
    immutable_content_hash: str

    @classmethod
    def create(
        cls,
        *,
        follow_up_id: str,
        parent_batch_id: str,
        branch_id: str,
        expected_batch_status: str,
        lifecycle_status: str,
        performance_status: str,
        events: Iterable[RuntimeEvent],
        host_receipt: Mapping[str, Any] | None = None,
    ) -> "TurnEventFollowUp":
        items = tuple(events)
        if expected_batch_status not in {"host_pending", "performance_pending"}:
            raise ValueError("follow-up requires a recoverable parent state")
        if lifecycle_status not in {"performance_pending", "committed", "failed"}:
            raise ValueError("invalid follow-up lifecycle state")
        if performance_status not in {"pending", "complete", "failed"}:
            raise ValueError("invalid follow-up performance status")
        body = {
            "follow_up_id": follow_up_id,
            "parent_batch_id": parent_batch_id,
            "branch_id": branch_id,
            "expected_batch_status": expected_batch_status,
            "lifecycle_status": lifecycle_status,
            "performance_status": performance_status,
            "host_receipt": dict(host_receipt or {}),
            "events": [event.to_dict() for event in items],
        }
        return cls(
            idempotency_key=f"follow-up:{branch_id}:{follow_up_id}",
            immutable_content_hash=_digest(body),
            events=items,
            **{key: value for key, value in body.items() if key != "events"},
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TurnEventFollowUp":
        return cls(
            follow_up_id=str(data["follow_up_id"]),
            parent_batch_id=str(data["parent_batch_id"]),
            branch_id=str(data["branch_id"]),
            idempotency_key=str(data["idempotency_key"]),
            expected_batch_status=str(data["expected_batch_status"]),
            lifecycle_status=str(data["lifecycle_status"]),
            performance_status=str(data["performance_status"]),
            host_receipt=dict(data.get("host_receipt", {})),
            events=tuple(RuntimeEvent.from_dict(item) for item in data.get("events", [])),
            immutable_content_hash=str(data["immutable_content_hash"]),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["events"] = [event.to_dict() for event in self.events]
        return data


class EventLedger:
    """In-memory branch-aware ledger with idempotent batch transitions."""

    def __init__(self) -> None:
        self._batches: dict[tuple[str, str], TurnEventBatch] = {}
        self._commands: dict[tuple[str, str], str] = {}
        self._follow_ups: dict[tuple[str, str], TurnEventFollowUp] = {}
        self._events: list[RuntimeEvent] = []
        self._lock = RLock()

    def append_batch(self, batch: TurnEventBatch) -> bool:
        with self._lock:
            key = (batch.branch_id, batch.batch_id)
            command_key = (batch.branch_id, batch.command_id)
            existing = self._batches.get(key)
            if existing:
                if existing.immutable_content_hash != batch.immutable_content_hash:
                    raise ValueError("batch id reused with different content")
                return False
            used_batch = self._commands.get(command_key)
            if used_batch and used_batch != batch.batch_id:
                raise ValueError("command id already belongs to another batch")
            self._batches[key] = batch
            self._commands[command_key] = batch.batch_id
            self._events.extend(batch.events)
            return True

    def append_follow_up(self, follow_up: TurnEventFollowUp) -> bool:
        with self._lock:
            key = (follow_up.branch_id, follow_up.follow_up_id)
            existing = self._follow_ups.get(key)
            if existing:
                if existing.immutable_content_hash != follow_up.immutable_content_hash:
                    raise ValueError("follow-up id reused with different content")
                return False
            parent = self._batches.get((follow_up.branch_id, follow_up.parent_batch_id))
            if parent is None:
                raise ValueError("follow-up parent batch does not exist")
            if self.batch_status(follow_up.branch_id, follow_up.parent_batch_id) != follow_up.expected_batch_status:
                raise ValueError("follow-up parent is not in expected state")
            self._follow_ups[key] = follow_up
            self._events.extend(follow_up.events)
            return True

    def events(self, branch_id: str | None = None) -> tuple[RuntimeEvent, ...]:
        with self._lock:
            if branch_id is None:
                return tuple(self._events)
            return tuple(event for event in self._events if event.branch_id == branch_id)

    def batch_status(self, branch_id: str, batch_id: str) -> str:
        batch = self._batches.get((branch_id, batch_id))
        if batch is None:
            raise ValueError("unknown batch")
        follow_ups = [
            item
            for (candidate_branch, _), item in self._follow_ups.items()
            if candidate_branch == branch_id and item.parent_batch_id == batch_id
        ]
        return follow_ups[-1].lifecycle_status if follow_ups else batch.lifecycle


class JsonlEventStore:
    """Durable JSONL wrapper. Each line is one whole batch or follow-up."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.ledger = EventLedger()
        self._load()

    def append_batch(self, batch: TurnEventBatch) -> bool:
        with self._lock:
            changed = self.ledger.append_batch(batch)
            if changed:
                self._append({"type": "batch", "data": batch.to_dict()})
            return changed

    def append_follow_up(self, follow_up: TurnEventFollowUp) -> bool:
        with self._lock:
            changed = self.ledger.append_follow_up(follow_up)
            if changed:
                self._append({"type": "follow_up", "data": follow_up.to_dict()})
            return changed

    def _append(self, record: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") == "batch":
                self.ledger.append_batch(TurnEventBatch.from_dict(record["data"]))
            elif record.get("type") == "follow_up":
                self.ledger.append_follow_up(TurnEventFollowUp.from_dict(record["data"]))


@dataclass(frozen=True)
class HostCommandRecord:
    command_id: str
    idempotency_key: str
    branch_id: str
    turn_id: str
    actor_id: str
    immutable_command_hash: str
    expected_scene_revision: int
    status: str
    host_receipt: Mapping[str, Any]
    timestamp_ns: int


class HostCommandJournal:
    """Durable latest-state command journal written before external dispatch."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, HostCommandRecord] = {}
        self._lock = RLock()
        self._load()

    def record(
        self,
        *,
        command_id: str,
        idempotency_key: str,
        branch_id: str,
        turn_id: str,
        actor_id: str,
        immutable_command_hash: str,
        expected_scene_revision: int,
        status: str,
        host_receipt: Mapping[str, Any] | None = None,
    ) -> HostCommandRecord:
        if status not in {"prepared", "sent", "pending", "completed", "failed"}:
            raise ValueError(f"invalid command status: {status}")
        existing = self._records.get(command_id)
        if existing and existing.immutable_command_hash != immutable_command_hash:
            raise ValueError("command id reused with different immutable content")
        item = HostCommandRecord(
            command_id=command_id,
            idempotency_key=idempotency_key,
            branch_id=branch_id,
            turn_id=turn_id,
            actor_id=actor_id,
            immutable_command_hash=immutable_command_hash,
            expected_scene_revision=expected_scene_revision,
            status=status,
            host_receipt=dict(host_receipt or {}),
            timestamp_ns=time_ns(),
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._records[command_id] = item
        return item

    def get(self, command_id: str) -> HostCommandRecord | None:
        return self._records.get(command_id)

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            item = HostCommandRecord(**data)
            self._records[item.command_id] = item


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()
