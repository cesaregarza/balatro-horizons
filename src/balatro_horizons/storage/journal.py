"""Hash-chained, append-only events with process-safe write serialization."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from balatro_horizons.observations.projection import canonical_json


def now():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def identifier(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("INVALID_IDENTIFIER")
    return value


def atomic_json(path: Path, value, *, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        with path.open("x", encoding="utf8") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf8") as stream:
        stream.write(canonical_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


class Store:
    def __init__(self, root):
        self._heads = {}
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "private_runs").mkdir(mode=0o700, exist_ok=True)
        self.db = self.root / "index.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS episodes (id TEXT PRIMARY KEY, manifest TEXT NOT NULL, summary TEXT)"
            )

    def episode_path(self, eid, private=False):
        return self.root / ("private_runs" if private else "public_runs") / identifier(eid)

    def create(self, manifest, private=None, eid=None):
        eid = eid or uuid.uuid4().hex
        public = self.episode_path(eid)
        public.mkdir(parents=True, exist_ok=False)
        self.episode_path(eid, True).mkdir(mode=0o700)
        manifest = {"schema_version": "1.0", "episode_id": eid, "created_at": now(), **manifest}
        atomic_json(public / "manifest.json", manifest, immutable=True)
        atomic_json(self.episode_path(eid, True) / "manifest.json", private or {}, immutable=True)
        self.reindex(eid)
        return eid

    def manifest(self, eid, private=False):
        return json.loads((self.episode_path(eid, private) / "manifest.json").read_text())

    def events(self, eid):
        path = self.episode_path(eid) / "events.jsonl"
        if not path.exists():
            return []
        rows = []
        head = "0" * 64
        with path.open() as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("TORN_JOURNAL")
                row = json.loads(line)
                expected = row.pop("hash")
                if (
                    row["previous_hash"] != head
                    or digest(row) != expected
                    or row["sequence"] != len(rows)
                ):
                    raise ValueError("JOURNAL_INTEGRITY_FAILURE")
                row["hash"] = expected
                head = expected
                rows.append(row)
        return rows

    def append(self, eid, kind, payload, *, actor="runner", observation_id=None, request_id=None):
        path = self.episode_path(eid)
        if not (path / "manifest.json").exists():
            raise FileNotFoundError("UNKNOWN_EPISODE")
        with locked(path / ".writer.lock"):
            journal = path / "events.jsonl"
            stamp = journal.stat() if journal.exists() else None
            key = (
                (stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
                if stamp
                else None
            )
            cached = self._heads.get(eid)
            if cached and cached[0] == key:
                _, count, head, terminal = cached
            else:
                events = self.events(eid)
                count, head = len(events), events[-1]["hash"] if events else "0" * 64
                terminal = any(e["type"] == "terminal" for e in events)
            if terminal:
                raise ValueError("EPISODE_TERMINATED")
            row = {
                "schema_version": "1.0",
                "episode_id": eid,
                "event_id": uuid.uuid4().hex,
                "sequence": count,
                "timestamp": now(),
                "type": kind,
                "actor": actor,
                "observation_id": observation_id,
                "request_id": request_id,
                "payload": payload,
                "previous_hash": head,
            }
            row["hash"] = digest(row)
            with (path / "events.jsonl").open("a", encoding="utf8") as stream:
                stream.write(canonical_json(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            stamp = journal.stat()
            key = (stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
            self._heads[eid] = (key, count + 1, row["hash"], kind == "terminal")
            return row

    def private_json(self, eid, name, value):
        if not re.fullmatch(r"[a-z0-9_-]+\.json", name):
            raise ValueError("INVALID_PRIVATE_NAME")
        atomic_json(self.episode_path(eid, True) / name, value, immutable=True)

    def summary(self, eid):
        for event in reversed(self.events(eid)):
            if event["type"] == "terminal":
                return {
                    **event["payload"],
                    "terminal_event_id": event["event_id"],
                    "journal_head": event["hash"],
                }
        return None

    def finish(self, eid, summary):
        self.append(eid, "terminal", summary)
        atomic_json(self.episode_path(eid) / "summary.json", self.summary(eid), immutable=True)
        self.reindex(eid)

    def reindex(self, eid):
        manifest, summary = self.manifest(eid), self.summary(eid)
        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT OR REPLACE INTO episodes VALUES (?,?,?)",
                (eid, canonical_json(manifest), canonical_json(summary) if summary else None),
            )

    def list_episodes(self):
        with sqlite3.connect(self.db) as db:
            rows = db.execute(
                "SELECT id,manifest,summary FROM episodes ORDER BY rowid DESC"
            ).fetchall()
        return [
            {"episode_id": i, "manifest": json.loads(m), "summary": json.loads(s) if s else None}
            for i, m, s in rows
        ]

    def recover(self):
        recovered = []
        for path in sorted((self.root / "public_runs").glob("*")):
            eid = identifier(path.name)
            with locked(path / ".writer.lock"):
                journal = path / "events.jsonl"
                if journal.exists():
                    data = journal.read_bytes()
                    if data and not data.endswith(b"\n"):
                        boundary = data.rfind(b"\n") + 1
                        backup = self.episode_path(eid, True) / (
                            "torn-" + uuid.uuid4().hex + ".bin"
                        )
                        backup.write_bytes(data[boundary:])
                        with journal.open("r+b") as stream:
                            stream.truncate(boundary)
                            stream.flush()
                            os.fsync(stream.fileno())
            events = self.events(eid)
            settled = {
                e["request_id"]: e["payload"]["cost_usd"]
                for e in events
                if e["type"] == "provider_response"
            }
            if not self.summary(eid):
                intents = {e["request_id"] for e in events if e["type"] == "action_intent"}
                resolved = {
                    e["request_id"]
                    for e in events
                    if e["type"] in ("action_commit", "action_rejected")
                }
                self.finish(
                    eid,
                    {
                        "episode_id": eid,
                        "evidence_kind": self.manifest(eid)["evidence_kind"],
                        "outcome": "INFRASTRUCTURE_FAILURE",
                        "reason": "AMBIGUOUS_ACTION_AFTER_CRASH"
                        if intents - resolved
                        else "PROCESS_INTERRUPTED",
                        "committed_actions": sum(e["type"] == "action_commit" for e in events),
                        "provider_calls": sum(e["type"] == "provider_request" for e in events),
                        "cost_usd": sum(
                            settled.get(e["request_id"], e["payload"].get("reserved_usd", 0))
                            for e in events
                            if e["type"] == "provider_request"
                        ),
                    },
                )
                recovered.append(eid)
            else:
                self.reindex(eid)
        return recovered
