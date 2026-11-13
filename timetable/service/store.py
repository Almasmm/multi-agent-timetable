"""Durable state of the 24/7 service in ``runs/_service/service.sqlite``.

Five tables, all readable by the web UI:

* ``settings``  - the service profile (dataset, term, programmes, base requirements, cadence) and the
  on/off switch, so the autopilot resumes after a restart;
* ``requests``  - change requests from university staff ("room C1.3.361 is closed on Wednesday") and
  the Intake agent's verdict;
* ``jobs``      - every generation the service started (why, with which run, what came out);
* ``versions``  - published timetables (immutable snapshots under ``versions/v<N>/``);
* ``chat``      - the dispatcher's conversations with each agent (``service/chat.py``);
* ``proposals`` + ``proposal_msgs`` - the operator's change proposals and the agents' discussion of each one
  (``service/proposals.py``).

The draft of manual changes (``service/editor.py``) lives in ``settings`` under the key ``draft``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_PROFILE: dict[str, Any] = {
    "university": "Astana IT University",
    "dataset": "input.xlsx",
    "term": 1,
    "programs": [],             # empty = every programme
    "requirements": [],         # standing rules of the university (one per line)
    "solver_seconds": 25,
    "llm": "auto",
    "warm_start": True,         # re-generate from the published version (few moves for students)
    "improve_every_min": 60,    # idle improvement cadence
    "max_improve_attempts": 4,  # per published version
    "stable_after": "",         # date the term starts (YYYY-MM-DD, default TT_TERM_START): after it changes must be small
    "max_moved_share": 0.1,     # live term: an improvement may move at most 10 % of the academic hours
}
REQUEST_OPEN = ("new", "review")      # not yet decided
REQUEST_ACTIVE = ("accepted", "applied")  # part of the standing requirements


class ServiceStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "service.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, author TEXT,
                    text TEXT, status TEXT, verdict TEXT, job_id INTEGER, updated REAL);
                CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, reason TEXT,
                    status TEXT, run_id TEXT, params TEXT, outcome TEXT, finished REAL);
                CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, run_id TEXT,
                    job_id INTEGER, score REAL, metrics TEXT, diff TEXT, announcement TEXT);
                CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent TEXT, role TEXT,
                    text TEXT, actions TEXT, provider TEXT);
                CREATE INDEX IF NOT EXISTS chat_agent ON chat(agent, id);
                CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT, context TEXT,
                    kind TEXT, status TEXT, outcome TEXT, updated REAL);
                CREATE TABLE IF NOT EXISTS proposal_msgs(id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER, ts REAL,
                    agent TEXT, role TEXT, text TEXT, actions TEXT, provider TEXT);
                CREATE INDEX IF NOT EXISTS pmsg_pid ON proposal_msgs(pid, id);
            """)

    # --- helpers ---
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._db:
            return self._db.execute(sql, args)

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("verdict", "params", "outcome", "metrics", "diff", "actions", "context"):
                if k in d:
                    d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    # --- settings ---
    def get(self, key: str, default: Any = None) -> Any:
        rows = self._rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def profile(self) -> dict:
        return {**DEFAULT_PROFILE, **(self.get("profile") or {})}

    # --- requests ---
    def add_request(self, text: str, author: str) -> int:
        now = time.time()
        return self._exec("INSERT INTO requests(ts, author, text, status, updated) VALUES (?,?,?,?,?)",
                          (now, author, text, "new", now)).lastrowid

    def request(self, rid: int) -> dict | None:
        rows = self._rows("SELECT * FROM requests WHERE id=?", (rid,))
        return rows[0] if rows else None

    def requests(self, status: tuple[str, ...] | None = None) -> list[dict]:
        if status:
            return self._rows(f"SELECT * FROM requests WHERE status IN ({','.join('?' * len(status))}) ORDER BY id DESC",
                              status)
        return self._rows("SELECT * FROM requests ORDER BY id DESC")

    def update_request(self, rid: int, **fields: Any) -> None:
        if "verdict" in fields:
            fields["verdict"] = json.dumps(fields["verdict"], ensure_ascii=False)
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE requests SET {cols} WHERE id=?", (*fields.values(), rid))

    def standing_requirements(self) -> list[str]:
        """Base rules of the profile + every accepted staff request (oldest first)."""
        accepted = [r["text"] for r in reversed(self.requests(REQUEST_ACTIVE))]
        return [*self.profile()["requirements"], *accepted]

    # --- jobs ---
    def add_job(self, kind: str, reason: str, params: dict) -> int:
        return self._exec("INSERT INTO jobs(ts, kind, reason, status, params) VALUES (?,?,?,?,?)",
                          (time.time(), kind, reason, "queued", json.dumps(params, ensure_ascii=False))).lastrowid

    def update_job(self, jid: int, **fields: Any) -> None:
        for k in ("params", "outcome"):
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False, default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))

    def job(self, jid: int) -> dict | None:
        rows = self._rows("SELECT * FROM jobs WHERE id=?", (jid,))
        return rows[0] if rows else None

    def jobs(self, limit: int = 30) -> list[dict]:
        return self._rows("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    def active_job(self) -> dict | None:
        rows = self._rows("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    # --- versions ---
    def add_version(self, run_id: str, job_id: int | None, score: float, metrics: dict, diff: dict,
                    announcement: str) -> int:
        return self._exec("INSERT INTO versions(ts, run_id, job_id, score, metrics, diff, announcement) VALUES (?,?,?,?,?,?,?)",
                          (time.time(), run_id, job_id, score, json.dumps(metrics, ensure_ascii=False),
                           json.dumps(diff, ensure_ascii=False), announcement)).lastrowid

    def set_announcement(self, vid: int, text: str) -> None:
        self._exec("UPDATE versions SET announcement=? WHERE id=?", (text, vid))

    def versions(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM versions ORDER BY id DESC LIMIT ?", (limit,))

    def version(self, vid: int) -> dict | None:
        rows = self._rows("SELECT * FROM versions WHERE id=?", (vid,))
        return rows[0] if rows else None

    def current_version(self) -> dict | None:
        rows = self._rows("SELECT * FROM versions ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def version_dir(self, vid: int) -> Path:
        return self.root / "versions" / f"v{vid}"

    # --- agent chat ---
    def add_chat(self, agent: str, role: str, text: str, actions: list | None = None, provider: str = "") -> dict:
        rid = self._exec("INSERT INTO chat(ts, agent, role, text, actions, provider) VALUES (?,?,?,?,?,?)",
                         (time.time(), agent, role, text, json.dumps(actions or [], ensure_ascii=False, default=str),
                          provider)).lastrowid
        return self._rows("SELECT * FROM chat WHERE id=?", (rid,))[0]

    def chat_messages(self, agent: str, limit: int = 200) -> list[dict]:
        rows = self._rows("SELECT * FROM chat WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit))
        return list(reversed(rows))

    def chat_last(self) -> dict[str, dict]:
        rows = self._rows("SELECT * FROM chat WHERE id IN (SELECT MAX(id) FROM chat GROUP BY agent)")
        return {r["agent"]: {"ts": r["ts"], "role": r["role"], "text": r["text"][:140]} for r in rows}

    def clear_chat(self, agent: str) -> None:
        self._exec("DELETE FROM chat WHERE agent=?", (agent,))

    def close(self) -> None:
        self._db.close()

    # --- proposals (operator -> agents discussion) ---
    def add_proposal(self, text: str, context: list | None) -> int:
        now = time.time()
        return self._exec("INSERT INTO proposals(ts, text, context, kind, status, outcome, updated) VALUES (?,?,?,?,?,?,?)",
                          (now, text, json.dumps(context or [], ensure_ascii=False), None, "discussing",
                           json.dumps({}), now)).lastrowid

    def proposal(self, pid: int) -> dict | None:
        rows = self._rows("SELECT * FROM proposals WHERE id=?", (pid,))
        return rows[0] if rows else None

    def proposals(self, limit: int = 100) -> list[dict]:
        rows = self._rows("SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,))
        last = {r["pid"]: r for r in self._rows(
            "SELECT * FROM proposal_msgs WHERE id IN (SELECT MAX(id) FROM proposal_msgs GROUP BY pid)")}
        for r in rows:
            r["last"] = last.get(r["id"])
        return rows

    def update_proposal(self, pid: int, **fields: Any) -> None:
        for k in ("outcome", "context"):
            if k in fields:
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE proposals SET {sets}, updated=? WHERE id=?", (*fields.values(), time.time(), pid))

    def add_proposal_msg(self, pid: int, agent: str, role: str, text: str, actions: list | None = None,
                         provider: str | None = None) -> dict:
        mid = self._exec("INSERT INTO proposal_msgs(pid, ts, agent, role, text, actions, provider) VALUES (?,?,?,?,?,?,?)",
                         (pid, time.time(), agent, role, text, json.dumps(actions or [], ensure_ascii=False), provider)).lastrowid
        self._exec("UPDATE proposals SET updated=? WHERE id=?", (time.time(), pid))
        return self._rows("SELECT * FROM proposal_msgs WHERE id=?", (mid,))[0]

    def proposal_msgs(self, pid: int) -> list[dict]:
        return self._rows("SELECT * FROM proposal_msgs WHERE pid=? ORDER BY id", (pid,))
