"""JSONL logging of every message, tool call and LLM call + per-agent load accounting."""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, path: Path, run_id: str, echo: bool = True):
        self.path = path
        self.run_id = run_id
        self.echo = echo
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self.calls: Counter[str] = Counter()  # agent -> number of tool + LLM calls
        self.extra: dict[str, Any] = {}  # fields stamped on every record (e.g. the service tick number)

    def log(self, kind: str, agent: str, **fields: Any) -> None:
        rec = {"ts": round(time.time(), 3), "run": self.run_id, "kind": kind, "agent": agent, **self.extra, **fields}
        with self._lock:
            if kind in ("tool_call", "llm_call") and fields.get("attempt", 1) == 1:
                self.calls[agent] += 1
            self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
        if self.echo and kind in ("tool_call", "llm_call", "error", "info"):
            status = fields.get("status", "")
            name = fields.get("name", fields.get("msg", ""))
            dur = fields.get("duration_ms")
            extra = f" {dur} ms" if dur is not None else ""
            print(f"[{agent:>11}] {kind:<9} {name} {status}{extra}", flush=True)

    def load_report(self) -> dict[str, Any]:
        total = sum(self.calls.values()) or 1
        rows = {a: {"calls": n, "share": round(n / total, 3)} for a, n in self.calls.most_common()}
        return {"total_calls": total, "agents": rows, "max_share": max((r["share"] for r in rows.values()), default=0)}

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def load_from_jsonl(path: Path) -> dict[str, Any]:
    """Recompute the load distribution from a log file (used by tests / the report)."""
    calls: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["kind"] in ("tool_call", "llm_call") and rec.get("attempt", 1) == 1:
            calls[rec["agent"]] += 1
    total = sum(calls.values()) or 1
    return {a: round(n / total, 3) for a, n in calls.items()}
