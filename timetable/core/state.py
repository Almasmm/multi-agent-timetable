"""Blackboard: explicit task state shared by agents, persisted to SQLite.

Agents never pass huge objects inside messages; they put them on the blackboard under a
key and send the key in a JSON message. Every write is snapshotted to SQLite so a run can
be inspected (or resumed) after the fact.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .messages import Message


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (set, tuple)):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"not serialisable: {type(obj)}")


class Blackboard:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._mem: dict[str, Any] = {}
        with self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, writer TEXT, updated REAL)")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, ts REAL, sender TEXT, recipient TEXT,"
                " intent TEXT, task TEXT, payload TEXT, correlation_id TEXT)"
            )

    def put(self, key: str, value: Any, writer: str) -> None:
        with self._lock:
            self._mem[key] = value
            blob = json.dumps(value, ensure_ascii=False, default=_to_jsonable)
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO kv(key, value, writer, updated) VALUES (?,?,?,?)",
                    (key, blob, writer, time.time()),
                )

    def get(self, key: str, default: Any = None) -> Any:
        return self._mem.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self._mem:
            raise KeyError(f"state key '{key}' is missing - a previous step did not finish")
        return self._mem[key]

    def record(self, msg: Message) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?)",
                (msg.id, msg.ts, msg.sender, msg.recipient, msg.intent, msg.task,
                 json.dumps(msg.payload, ensure_ascii=False, default=_to_jsonable), msg.correlation_id),
            )

    def prune_messages(self, older_than: float) -> None:
        """Drop messages older than ``older_than`` (unix time); used by the long-running service."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM messages WHERE ts < ?", (older_than,))

    def close(self) -> None:
        self._db.close()
