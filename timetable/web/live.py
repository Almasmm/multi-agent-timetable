"""Real-time views for the web UI.

* ``live_feed`` - one stream of what the agents do: the generation run (``runs/<id>/events.jsonl``), the 24/7
  service ticks (``runs/_service/events-<day>.jsonl``) and the chat / proposal discussions
  (``runs/_service/chat-<day>.jsonl``). Files are read incrementally by byte offset, like the run log.
* ``run_board`` - how the timetable of a running generation is being built: chunk states (quota -> GA first
  generation -> CP-SAT solution -> Critic check), the week heat map of placed classes, progress per programme and the
  week of one group, read from the run's blackboard (``state.sqlite``) while the run is going.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DAYS = 6
HOURS = 12
_TAIL = 250


def _read(path: Path, offset: int | None) -> tuple[list[dict], int]:
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if offset is None or offset > size:  # first read: only the tail of the day
        with path.open("rb") as fh:
            fh.seek(max(0, size - 400_000))
            lines = fh.read().splitlines()[1 if size > 400_000 else 0:]
        lines, offset = lines[-_TAIL:], size
    else:
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
        cut = data.rfind(b"\n") + 1  # a half-written last line is read next time
        lines, offset = data[:cut].splitlines(), offset + cut
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out, offset


def _slim(e: dict) -> dict:
    p = e.get("payload")
    if isinstance(p, dict) and len(json.dumps(p, ensure_ascii=False, default=str)) > 700:
        e = {**e, "payload": {k: v for k, v in p.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) < 160}}
    e.pop("trace", None)
    return e


def live_feed(out_root: Path, run_id: str | None, cursors: dict[str, str]) -> dict:
    """``cursors``: {source: "file:offset"} from the previous answer (empty on the first call)."""
    day = time.strftime("%Y%m%d")
    files = {"svc": out_root / "_service" / f"events-{day}.jsonl", "chat": out_root / "_service" / f"chat-{day}.jsonl"}
    if run_id:
        files["run"] = out_root / run_id / "events.jsonl"
    events, out = [], {}
    for src, path in files.items():
        name, _, off = (cursors.get(src) or "").rpartition(":")
        offset = int(off) if name == path.name and off.isdigit() else None
        evs, new = _read(path, offset)
        out[src] = f"{path.name}:{new}"
        events += [{**_slim(e), "src": src} for e in evs]
    events.sort(key=lambda e: e.get("ts", 0))
    return {"events": events[-600:], "cursors": out, "run": run_id}


# ------------------------------------------------------------------------------------------- run board
_cache: dict[str, tuple[float, list]] = {}


def _kv(con: sqlite3.Connection) -> dict[str, tuple[str, float]]:
    return {k: (v, u) for k, v, u in con.execute("SELECT key, value, updated FROM kv")}


def _val(kv: dict) -> dict | None:
    if "validation" not in kv:
        return None
    v = json.loads(kv["validation"][0])
    return {"hard_violations": v.get("hard_violations"), "by_type": v.get("by_type"), "soft": v.get("soft")}


def run_board(run_dir: Path, group: str | None = None) -> dict:
    db = run_dir / "state.sqlite"
    res = None
    if (run_dir / "result.json").exists():
        try:
            res = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            res = None
    if not db.exists():
        return {"ready": False, "result": res}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        kv = _kv(con)
    except sqlite3.Error:
        return {"ready": False, "result": res}
    finally:
        con.close()
    if "sessions" not in kv:
        return {"ready": False, "stage": "data", "result": res}
    key = str(db)
    raw, upd = kv["sessions"]
    if key not in _cache or _cache[key][0] != upd:
        _cache[key] = (upd, json.loads(raw))
    sessions: list[dict[str, Any]] = _cache[key][1]
    by_id = {s["id"]: s for s in sessions}
    chunks = json.loads(kv["chunks"][0]) if "chunks" in kv else []
    unit_chunk = {u: k for k, units in enumerate(chunks) for u in units}

    starts: dict[str, int] = {}
    tentative: dict[str, int] = {}
    states = []
    for k, units in enumerate(chunks):
        sol = json.loads(kv[f"solution:{k}"][0]) if f"solution:{k}" in kv else None
        seed = json.loads(kv[f"seed:{k}"][0]) if f"seed:{k}" in kv else None
        val = json.loads(kv[f"validated:{k}"][0]) if f"validated:{k}" in kv else None
        state = ("validated" if val is True else "retry" if val is False else "solved" if sol else "ga" if seed
                 else "quota" if f"quota:{k}" in kv else "waiting")
        if sol:
            starts.update(sol)
        elif seed:
            tentative.update(seed)
        ts = [kv[x][1] for x in (f"quota:{k}", f"seed:{k}", f"solution:{k}", f"validated:{k}") if x in kv]
        n_groups = len({g for s in sessions if f"{s['program']}-{s['year']}" in units for g in s["groups"]})
        states.append({"k": k, "units": units, "state": state, "groups": n_groups,
                       "sessions": sum(1 for s in sessions if f"{s['program']}-{s['year']}" in units),
                       "started": min(ts) if ts else None, "updated": max(ts) if ts else None})
    if "starts" in kv:  # final, after rooms and repairs
        starts = json.loads(kv["starts"][0])
        tentative = {}

    grid = [[0] * HOURS for _ in range(DAYS)]
    ghost = [[0] * HOURS for _ in range(DAYS)]
    progs: dict[str, list[int]] = {}
    for s in sessions:
        progs.setdefault(s["program"], [0, 0])[1] += 1
    for table, place in ((starts, grid), (tentative, ghost)):
        for sid, t in table.items():
            s = by_id.get(sid)
            if not s or t is None:
                continue
            if table is starts:
                progs[s["program"]][0] += 1
            for u in range(int(t), int(t) + s["duration"]):
                d, h = divmod(u, HOURS)
                if d < DAYS and h < HOURS:
                    place[d][h] += 1
    groups = sorted({g for s in sessions for g in s["groups"]})
    week = []
    if group:
        for s in sessions:
            if group in s["groups"]:
                t = starts.get(s["id"], tentative.get(s["id"]))
                if t is not None:
                    d, h = divmod(int(t), HOURS)
                    week.append({"day": d, "hour": h, "duration": s["duration"], "course": s["course"], "kind": s["kind"],
                                 "online": s["online"], "tentative": s["id"] not in starts, "chunk": unit_chunk.get(f"{s['program']}-{s['year']}")})
    last = max((c for c in states if c["state"] in ("solved", "validated")), key=lambda c: c["updated"] or 0, default=None)
    recent = []
    if last and f"solution:{last['k']}" in kv:
        sol = json.loads(kv[f"solution:{last['k']}"][0])
        for sid, t in list(sol.items())[:14]:
            s = by_id.get(sid)
            if s:
                d, h = divmod(int(t), HOURS)
                recent.append({"groups": s["groups"][:3], "more": max(0, len(s["groups"]) - 3), "course": s["course"],
                               "kind": s["kind"], "online": s["online"], "day": d, "hour": h, "duration": s["duration"]})
    return {"ready": True, "chunks": states, "grid": grid, "ghost": ghost, "programs": progs, "groups": groups,
            "week": week, "group": group, "recent": recent, "chunk_recent": last["k"] if last else None,
            "total": len(sessions), "placed": len(starts), "tentative": len(tentative),
            "hours_total": sum(s["duration"] * len(s["groups"]) for s in sessions),
            "final": "starts" in kv, "validation": _val(kv),
            "result": res}
