"""Run manager for the web UI: starts pipeline runs in background threads and reads the
artefacts a run leaves behind (events.jsonl, state.sqlite, result.json, timetable.csv).

The web layer never touches agents directly - it calls :func:`timetable.app.run` exactly
like the CLI does and then reads the same files, so the UI shows what really happened."""
from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..app import run as run_pipeline
from ..config import DAYS, ROOT, Settings
from ..core.errors import DataError
from ..domain.constraints import parse_offline
from ..domain.loader import normalize, read_workbook

DATASET_DIRS = [ROOT / "data", ROOT / "dataset-archive", ROOT / "data" / "uploads"]
UPLOAD_DIR = ROOT / "data" / "uploads"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\- ]{0,80}$")
DOWNLOADABLE = {"timetable.xlsx", "timetable.csv", "timetable.html", "report.md", "events.jsonl",
                "result.json", "state.sqlite"}


# ----------------------------------------------------------------------------------------
# Datasets
# ----------------------------------------------------------------------------------------
_dataset_cache: dict[str, tuple[float, dict]] = {}


def _dataset_summary(path: Path) -> dict:
    """Validate a workbook with the same loader the Data agent uses and summarise it."""
    info: dict[str, Any] = {"id": path.name, "name": path.stem, "path": str(path), "dir": path.parent.name,
                            "size_kb": round(path.stat().st_size / 1024), "valid": False}
    try:
        ds = normalize(read_workbook(path), Settings().weeks_per_term)
    except DataError as exc:
        info["error"] = str(exc)
        return info
    except Exception as exc:  # unexpected workbook content
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    programs: dict[str, dict] = {}
    for g in ds.groups:
        p = programs.setdefault(g.program, {"code": g.program, "groups": 0, "years": set(), "students": 0})
        p["groups"] += 1
        p["years"].add(g.year)
        p["students"] += g.headcount
    for p in programs.values():
        p["years"] = sorted(p["years"])
    info.update(valid=True, programs=sorted(programs.values(), key=lambda p: p["code"]),
                groups=len(ds.groups), rooms=len(ds.rooms), courses=len(ds.courses),
                students=sum(g.headcount for g in ds.groups), slots=len(ds.slot_labels) * 6,
                issues={"fixed": sum(i["level"] == "fixed" for i in ds.issues),
                        "warning": sum(i["level"] == "warning" for i in ds.issues)},
                trimesters=sorted({c.trimester for c in ds.courses}))
    return info


_entity_cache: dict[str, tuple[float, dict]] = {}


def dataset_entities(path: Path) -> dict:
    """Programme / room / course / group names of a workbook (for constraint preview)."""
    mtime = path.stat().st_mtime
    hit = _entity_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    ds = normalize(read_workbook(path), Settings().weeks_per_term)
    ent = {"programs": sorted({g.program for g in ds.groups}), "rooms": [r.code for r in ds.rooms],
           "courses": sorted({c.name for c in ds.courses}), "groups": [g.code for g in ds.groups]}
    _entity_cache[str(path)] = (mtime, ent)
    return ent


def preview_constraints(dataset_id: str, texts: list[str]) -> dict:
    """What the offline parser makes of the requirements (the run itself may use Claude)."""
    ent = dataset_entities(resolve_dataset(dataset_id))
    cset = parse_offline([t for t in texts if t.strip()], ent["programs"], ent["courses"], ent["rooms"])
    programs, rooms, groups = set(ent["programs"]), set(ent["rooms"]), set(ent["groups"])
    parsed = []
    for spec in cset.constraints:
        sc = spec.scope
        unknown = ([p for p in sc.programs if p not in programs] + [r for r in sc.rooms if r not in rooms]
                   + [g for g in sc.groups if g not in groups])
        parsed.append({**spec.model_dump(), "unknown": unknown})
    return {"parsed": parsed, "unparsed": cset.unparsed}


def list_datasets() -> list[dict]:
    out = []
    for d in DATASET_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.xlsx")):
            if p.name.startswith("~$"):
                continue
            mtime = p.stat().st_mtime
            cached = _dataset_cache.get(str(p))
            if cached is None or cached[0] != mtime:
                cached = (mtime, _dataset_summary(p))
                _dataset_cache[str(p)] = cached
            out.append(cached[1])
    return out


def resolve_dataset(dataset_id: str) -> Path:
    """Map a dataset id (file name) back to a file inside one of the allowed folders."""
    for d in list_datasets():
        if d["id"] == dataset_id:
            return Path(d["path"])
    raise FileNotFoundError(f"dataset '{dataset_id}' not found")


def save_upload(filename: str, content: bytes) -> dict:
    name = Path(filename).name
    if not name.lower().endswith(".xlsx") or not SAFE_NAME.match(name):
        raise ValueError("expected an .xlsx file with a simple name")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / name
    target.write_bytes(content)
    _dataset_cache.pop(str(target), None)
    return _dataset_summary(target)


# ----------------------------------------------------------------------------------------
# Run artefacts
# ----------------------------------------------------------------------------------------
def runs_root() -> Path:
    return Settings().out_root


def run_dir(run_id: str) -> Path:
    if not SAFE_NAME.match(run_id) or ".." in run_id:
        raise ValueError("bad run id")
    p = runs_root() / run_id
    if not p.is_dir():
        raise FileNotFoundError(f"run '{run_id}' not found")
    return p


def read_result(d: Path) -> dict | None:
    p = d / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_events(d: Path, offset: int = 0, limit: int = 2000) -> tuple[list[dict], int, bool]:
    """Events after a byte offset (so the UI can poll incrementally)."""
    p = d / "events.jsonl"
    if not p.exists():
        return [], 0, False
    out: list[dict] = []
    with p.open("rb") as fh:
        fh.seek(offset)
        more = False
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):  # partially written line: read it next time
                break
            offset += len(line)
            try:
                out.append(json.loads(line.decode("utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if len(out) >= limit:
                more = True
                break
    return out, offset, more


def _db(d: Path) -> sqlite3.Connection | None:
    p = d / "state.sqlite"
    if not p.exists():
        return None
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    return con


def read_kv(d: Path, keys: list[str]) -> dict[str, Any]:
    con = _db(d)
    if con is None:
        return {}
    try:
        rows = con.execute(f"SELECT key, value FROM kv WHERE key IN ({','.join('?' * len(keys))})", keys).fetchall()
        return {k: json.loads(v) for k, v in rows}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def read_messages(d: Path) -> list[dict]:
    con = _db(d)
    if con is None:
        return []
    try:
        rows = con.execute("SELECT id, ts, sender, recipient, intent, task, payload, correlation_id FROM messages"
                           " ORDER BY ts").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [{"id": r[0], "ts": r[1], "sender": r[2], "recipient": r[3], "intent": r[4], "task": r[5],
             "payload": json.loads(r[6] or "{}"), "correlation_id": r[7]} for r in rows]


def read_timetable(d: Path) -> dict:
    p = d / "timetable.csv"
    if not p.exists():
        return {"rows": [], "groups": [], "rooms": [], "programs": [], "times": [], "days": DAYS}
    with p.open(encoding="utf-8-sig", newline="") as fh:
        rows = [{"group": r["group"], "program": r["program"], "year": int(r["year"]), "day": int(r["day_index"]),
                 "hour": int(r["hour_index"]), "time": r["time"], "course": r["discipline"], "kind": r["type"],
                 "room": r["classroom"], "stream": int(r["stream_size"])} for r in csv.DictReader(fh)]
    labels = (read_kv(d, ["dataset"]).get("dataset") or {}).get("slot_labels") or {}
    times = {int(k): v for k, v in labels.items()}
    for r in rows:
        times.setdefault(r["hour"], r["time"])
    return {"rows": rows, "groups": sorted({r["group"] for r in rows}),
            "rooms": sorted({r["room"] for r in rows if r["room"] not in ("online", "UNASSIGNED")}),
            "programs": sorted({r["program"] for r in rows}),
            "times": [times.get(h, f"{8 + h:02d}:00") for h in range(12)], "days": DAYS}


def read_details(d: Path) -> dict:
    """Everything the results page needs that is not the timetable itself: blackboard
    state (validation, constraints, concessions, repairs, data issues) and the
    negotiation-related messages with their full payloads."""
    kv = read_kv(d, ["validation", "constraint_specs", "constraint_warnings", "run_params", "repairs", "critique",
                     "data_issues_text", "dataset", "demand_summary", "chunks", "plan"])
    dataset = kv.pop("dataset", None) or {}
    msgs = read_messages(d)
    by_id = {m["id"]: m for m in msgs}
    conversation = []
    for m in msgs:
        if m["intent"] == "task":
            continue
        task_msg = by_id.get(m["correlation_id"] or "")
        if m["task"] in ("resolve", "repair", "validate_chunk", "solve_chunk", "validate_all", "allocate_quota"):
            conversation.append({"ts": m["ts"], "agent": m["sender"], "task": m["task"], "intent": m["intent"],
                                 "request": (task_msg or {}).get("payload", {}), "reply": m["payload"]})
    return {**kv, "data_issues": dataset.get("issues", []), "conversation": conversation,
            "rooms": dataset.get("rooms", []), "groups": dataset.get("groups", [])}


# ----------------------------------------------------------------------------------------
# Run manager
# ----------------------------------------------------------------------------------------
@dataclass
class RunHandle:
    run_id: str
    params: dict
    started: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    result: dict | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class RunManager:
    """One pipeline run at a time (CP-SAT already uses every core); the rest is bookkeeping."""

    def __init__(self):
        self._lock = threading.Lock()
        self._handles: dict[str, RunHandle] = {}

    # --- lifecycle ---
    def active(self) -> RunHandle | None:
        return next((h for h in self._handles.values() if h.running), None)

    def start(self, params: dict) -> RunHandle:
        with self._lock:
            if self.active() is not None:
                raise RuntimeError("another run is still in progress - wait for it or cancel it")
            data_path = resolve_dataset(params["dataset"])
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", params.get("name") or "run").strip("-")[:30] or "run"
            run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
            kw: dict[str, Any] = {"data_path": data_path, "term": int(params.get("term") or 1),
                                  "programs": tuple(params.get("programs") or ()),
                                  "allow_saturday": bool(params.get("allow_saturday")),
                                  "llm_provider": params.get("llm") or "auto"}
            if params.get("solver_seconds"):
                kw["solver_time_limit"] = float(params["solver_seconds"])
            if params.get("chunk_groups"):
                kw["chunk_target_groups"] = int(params["chunk_groups"])
            if params.get("stream"):
                kw["lecture_stream_size"] = int(params["stream"])
            settings = Settings().with_(**kw)
            reqs = [ln.strip() for ln in params.get("requirements") or [] if ln.strip()]
            h = RunHandle(run_id=run_id, params={**params, "dataset_path": str(data_path), "requirements": reqs})
            h.thread = threading.Thread(target=self._work, args=(h, settings, reqs), name=f"run-{run_id}", daemon=True)
            self._handles[run_id] = h
            h.thread.start()
            return h

    def _work(self, h: RunHandle, settings: Settings, reqs: list[str]) -> None:
        try:
            h.result = run_pipeline(settings, reqs, h.run_id, echo=False, cancel=h.cancel)
        except Exception as exc:  # the pipeline reports its own errors; this is a true crash
            h.error = f"{type(exc).__name__}: {exc}"
            d = runs_root() / h.run_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "result.json").write_text(json.dumps({"status": "failed", "reason": h.error, "wall_s": round(
                time.time() - h.started, 1), "requirements": reqs}, ensure_ascii=False, indent=2), encoding="utf-8")

    def cancel(self, run_id: str) -> bool:
        h = self._handles.get(run_id)
        if h is None or not h.running:
            return False
        h.cancel.set()
        return True

    # --- queries ---
    def status(self, run_id: str) -> dict:
        d = run_dir(run_id)
        h = self._handles.get(run_id)
        result = read_result(d)
        if h is not None and h.running:
            status = "cancelling" if h.cancel.is_set() else "running"
        elif result is not None:
            status = result.get("status", "unknown")
        else:
            status = "incomplete"  # process died before result.json was written
        started = h.started if h else d.stat().st_ctime
        return {"id": run_id, "status": status, "started": started,
                "elapsed_s": round((time.time() - started) if status in ("running", "cancelling")
                                   else float((result or {}).get("wall_s") or 0), 1),
                "params": h.params if h else _params_from_result(result), "result": result}

    def list(self) -> list[dict]:
        root = runs_root()
        out = []
        if not root.exists():
            return out
        for d in root.iterdir():
            if not d.is_dir() or not (d / "events.jsonl").exists():
                continue
            if not SAFE_NAME.match(d.name):
                continue
            try:
                st = self.status(d.name)
            except (FileNotFoundError, ValueError):
                continue
            res = st["result"] or {}
            out.append({"id": d.name, "status": st["status"], "started": st["started"], "elapsed_s": st["elapsed_s"],
                        "hard_violations": sum((res.get("hard_violations") or {}).values()) if res else None,
                        "soft": res.get("soft"), "programs": (st["params"] or {}).get("programs") or [],
                        "term": (st["params"] or {}).get("term"), "llm": res.get("llm"),
                        "max_share": (res.get("load") or {}).get("max_share"), "steps": res.get("steps"),
                        "groups": _count_groups(d) if res else None})
        out.sort(key=lambda r: r["started"], reverse=True)
        return out

    def delete(self, run_id: str) -> None:
        d = run_dir(run_id)
        h = self._handles.get(run_id)
        if h is not None and h.running:
            raise RuntimeError("cannot delete a running run")
        self._handles.pop(run_id, None)
        shutil.rmtree(d)


def _params_from_result(result: dict | None) -> dict:
    if not result:
        return {}
    s = result.get("settings") or {}
    return {"term": s.get("term"), "programs": s.get("programs") or [], "requirements": result.get("requirements") or [],
            "dataset_path": s.get("data_path"), "llm": s.get("llm_provider"), "solver_seconds": s.get("solver_time_limit"),
            "allow_saturday": s.get("allow_saturday")}


_groups_cache: dict[str, tuple[float, int]] = {}


def _count_groups(d: Path) -> int | None:
    p = d / "timetable.csv"
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    hit = _groups_cache.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    with p.open(encoding="utf-8-sig", newline="") as fh:
        n = len({r["group"] for r in csv.DictReader(fh)})
    _groups_cache[str(p)] = (mtime, n)
    return n
