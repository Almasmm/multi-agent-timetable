"""Pure helpers for published timetable versions: quality score, change diff between two
versions, immutable snapshots and per-group calendar (.ics) export."""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import zlib
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SNAPSHOT_FILES = ("timetable.csv", "timetable.xlsx", "timetable.html", "report.md", "result.json")


def quality_score(soft: dict) -> float:
    """Penalty hours per week, lower is better: gaps + 2 x after 18:00 + 3 x Saturday + 0.5 x
    practice-before-lecture. One number so the Publisher can compare two timetables."""
    return round(float(soft.get("gap_hours_total", 0)) + 2 * float(soft.get("late_hours_18_plus", 0))
                 + 3 * float(soft.get("saturday_hours", 0)) + 0.5 * float(soft.get("precedence_violations", 0)), 1)


def read_rows(path: Path) -> list[dict]:
    """timetable.csv -> one dict per group and academic hour (the format the web UI uses)."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{"group": r["group"], "program": r["program"], "year": int(r["year"]), "day": int(r["day_index"]),
                 "hour": int(r["hour_index"]), "time": r["time"], "course": r["discipline"], "kind": r["type"],
                 "room": r["classroom"], "stream": int(r["stream_size"])} for r in csv.DictReader(fh)]


def diff_rows(old: list[dict], new: list[dict]) -> dict:
    """What changed for students: academic hours that moved in time, room changes, affected groups."""
    if not old:
        return {"first": True, "hours_moved": 0, "room_changes": 0, "groups_affected": 0, "groups": {},
                "total_hours": len(new)}
    when = lambda r: (r["group"], r["day"], r["hour"], r["course"], r["kind"])  # noqa: E731
    old_t, new_t = Counter(map(when, old)), Counter(map(when, new))
    moved = new_t - old_t
    room_old = {when(r): r["room"] for r in old}
    rooms = [when(r) for r in new if when(r) in room_old and room_old[when(r)] != r["room"]]
    by_group: dict[str, dict] = {}
    for k, n in moved.items():
        by_group.setdefault(k[0], {"moved": 0, "rooms": 0})["moved"] += n
    for k in rooms:
        by_group.setdefault(k[0], {"moved": 0, "rooms": 0})["rooms"] += 1
    return {"first": False, "hours_moved": sum(moved.values()), "room_changes": len(rooms),
            "groups_affected": len(by_group), "total_hours": len(new),
            "groups": dict(sorted(by_group.items(), key=lambda kv: -kv[1]["moved"] - kv[1]["rooms"])[:300])}


def snapshot(run_dir: Path, target: Path) -> list[str]:
    """Copy the run's deliverables into an immutable version folder; starts.json / rooms.json
    (session id -> start slot / room, from the run's blackboard) make the next re-generation a warm start."""
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in SNAPSHOT_FILES:
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, target / name)
            copied.append(name)
    con = sqlite3.connect(f"file:{(run_dir / 'state.sqlite').as_posix()}?mode=ro", uri=True)
    try:
        for key in ("starts", "rooms_of"):
            row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            name = "starts.json" if key == "starts" else "rooms.json"
            (target / name).write_text(row[0] if row else "{}", encoding="utf-8")
            copied.append(name)
    finally:
        con.close()
    return copied


def to_ics(rows: list[dict], group: str, term_start: str, weeks: int) -> str:
    """Weekly recurring events of one group for a calendar app (Google / Outlook / iOS).
    Times are floating local time; consecutive hours of the same class become one event."""
    monday = date.fromisoformat(term_start)
    monday -= timedelta(days=monday.weekday())
    mine = sorted((r for r in rows if r["group"] == group), key=lambda r: (r["day"], r["hour"]))
    events: list[dict] = []
    for r in mine:
        last = events[-1] if events else None
        if last and last["day"] == r["day"] and last["end_hour"] == r["hour"] - 1 and \
                (last["course"], last["kind"], last["room"]) == (r["course"], r["kind"], r["room"]):
            last["end_hour"], last["end"] = r["hour"], r["time"].split("-")[1]
            continue
        start, end = r["time"].split("-")
        events.append({**r, "end_hour": r["hour"], "start": start, "end": end})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kinds = {"lecture": "лекция", "practice": "практика", "lab": "лабораторная"}
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Timetable Agents//AITU//RU", "CALSCALE:GREGORIAN",
           f"X-WR-CALNAME:{_ics_text(group)}"]
    for e in events:
        day = monday + timedelta(days=e["day"])
        hhmm = lambda s: s.replace(":", "")  # noqa: E731
        place = "онлайн" if e["room"] == "online" else e["room"]
        out += ["BEGIN:VEVENT", f"UID:{group}-{e['day']}-{e['hour']}-{zlib.crc32(e['course'].encode()):08x}@timetable-agents",
                f"DTSTAMP:{stamp}", f"DTSTART:{day:%Y%m%d}T{hhmm(e['start'])}00", f"DTEND:{day:%Y%m%d}T{hhmm(e['end'])}00",
                f"RRULE:FREQ=WEEKLY;COUNT={weeks}", f"SUMMARY:{_ics_text(e['course'])} ({kinds.get(e['kind'], e['kind'])})",
                f"LOCATION:{_ics_text(place)}", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def _ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
