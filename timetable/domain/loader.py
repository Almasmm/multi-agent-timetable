"""Excel ingestion + normalisation of the university input workbook.

Expected sheets: one curriculum sheet per programme (course_name, trimester, credits,
lecture_slots, practice_slots, lab_slots, delivery_mode, lecture_precedes_practice),
plus ``Groups``, ``Rooms`` and ``Timeslots``. Dirty data is repaired where it is safe and
every repair is recorded as an issue so a human can fix the source.
"""
from __future__ import annotations

import re
from datetime import time as dtime
from pathlib import Path

from ..core.errors import DataError
from .models import GYM, LAB, LECTURE, PRACTICE, Course, Dataset, Group, Room

SERVICE_SHEETS = {"Groups", "Rooms", "Timeslots"}
CURRICULUM_COLS = ["course_name", "trimester", "credits", "lecture_slots", "practice_slots",
                   "lab_slots", "delivery_mode", "lecture_precedes_practice"]
CYRILLIC_LOOKALIKES = str.maketrans("СсАаВЕеКкМНОоРрТХх", "CcAaBEeKkMHOoPpTXx")


def read_workbook(path: Path) -> dict[str, list[list]]:
    """Read every sheet into a list of rows (values only)."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise DataError("openpyxl is required to read .xlsx files (pip install openpyxl)") from exc
    if not path.exists():
        raise DataError(f"input workbook not found: {path}")
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise DataError(f"cannot open workbook {path.name}: {exc}") from exc
    sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    wb.close()
    missing = SERVICE_SHEETS - set(sheets)
    if missing:
        raise DataError(f"workbook is missing required sheet(s): {', '.join(sorted(missing))}")
    return sheets


def _num(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _bool(v, default=True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "1", "yes", "да"}


def _header_index(row: list) -> dict[str, int]:
    return {str(c).strip().lower(): i for i, c in enumerate(row) if c is not None and str(c).strip()}


def normalize(sheets: dict[str, list[list]], weeks: int, gym_pattern: str = "physical education") -> Dataset:
    issues: list[dict] = []

    def issue(level: str, where: str, text: str) -> None:
        issues.append({"level": level, "where": where, "issue": text})

    # --- curricula ---------------------------------------------------------------------
    courses: list[Course] = []
    for sheet, rows in sheets.items():
        if sheet in SERVICE_SHEETS or not rows:
            continue
        idx = _header_index(rows[0])
        if not {"course_name", "trimester"} <= set(idx):
            issue("warning", sheet, "sheet skipped: no course_name/trimester header")
            continue
        for rnum, row in enumerate(rows[1:], start=2):
            get = lambda col: row[idx[col]] if col in idx and idx[col] < len(row) else None  # noqa: E731
            name = str(get("course_name") or "").strip()
            if not name:
                continue
            raw_mode = str(get("delivery_mode") or "offline").strip().lower()
            if raw_mode not in ("online", "offline"):
                fixed = "online" if raw_mode.startswith("onl") else "offline"
                issue("fixed", f"{sheet}!{rnum}", f"delivery_mode '{raw_mode}' -> '{fixed}'")
                raw_mode = fixed
            hours = {}
            for kind, col in ((LECTURE, "lecture_slots"), (PRACTICE, "practice_slots"), (LAB, "lab_slots")):
                slots = _num(get(col))
                h = int(slots / weeks + 0.5)
                if slots and abs(slots / weeks - h) > 1e-9:
                    issue("fixed", f"{sheet}!{rnum}", f"{name}: {col}={slots:g} is not a multiple of {weeks}"
                          f" weeks, rounded to {h} h/week")
                if h:
                    hours[kind] = h
            if not hours:
                issue("warning", f"{sheet}!{rnum}", f"{name}: no lecture/practice/lab slots - course skipped")
                continue
            venue = GYM if gym_pattern in name.lower() else ""
            courses.append(Course(sheet, name, int(_num(get("trimester"))), int(_num(get("credits"))), hours,
                                  raw_mode == "online", _bool(get("lecture_precedes_practice")), venue))

    programs = {c.program for c in courses}

    # --- groups ------------------------------------------------------------------------
    groups: list[Group] = []
    grow = sheets["Groups"]
    gidx = _header_index(grow[0])
    seen = set()
    for rnum, row in enumerate(grow[1:], start=2):
        code = str(row[gidx.get("group_code", 0)] or "").strip()
        if not code:
            continue
        m = re.match(r"([A-Za-z]+)", code)
        prog = m.group(1) if m else ""
        if prog not in programs:
            issue("warning", f"Groups!{rnum}", f"group {code}: no curriculum sheet '{prog}' - group skipped")
            continue
        if code in seen:
            issue("warning", f"Groups!{rnum}", f"duplicate group {code} skipped")
            continue
        seen.add(code)
        year = int(_num(row[gidx["year"]])) if "year" in gidx else 1
        head = int(_num(row[gidx["headcount"]])) if "headcount" in gidx else 0
        if head <= 0:
            issue("fixed", f"Groups!{rnum}", f"group {code}: missing headcount, assumed 20")
            head = 20
        groups.append(Group(code, prog, year, head))

    # --- rooms -------------------------------------------------------------------------
    rooms: dict[str, Room] = {}
    rrow = sheets["Rooms"]
    ridx = _header_index(rrow[0])
    for rnum, row in enumerate(rrow[1:], start=2):
        raw = str(row[ridx.get("room_code", 0)] or "").strip()
        if not raw:
            continue
        code = raw.translate(CYRILLIC_LOOKALIKES)
        if code != raw:
            issue("fixed", f"Rooms!{rnum}", f"room code '{raw}' contained Cyrillic letters -> '{code}'")
        if not _bool(row[ridx["available"]] if "available" in ridx else True):
            issue("info", f"Rooms!{rnum}", f"room {code} marked unavailable - skipped")
            continue
        cap = int(_num(row[ridx.get("capacity", 1)]))
        rtype = str(row[ridx.get("room_type", 2)] or "P").strip().upper()
        floor = int(_num(row[ridx["floor"]])) if "floor" in ridx else 0
        if code in rooms:
            keep = rooms[code] if rooms[code].capacity >= cap else Room(code, cap, rtype, floor)
            issue("fixed", f"Rooms!{rnum}", f"duplicate room {code}: kept capacity {keep.capacity}")
            rooms[code] = keep
            continue
        rooms[code] = Room(code, cap, rtype, floor)
    min_group = min((g.headcount for g in groups), default=0)
    for r in rooms.values():
        if r.capacity < min_group:
            issue("warning", "Rooms", f"room {r.code} (capacity {r.capacity}) is smaller than any group")

    # --- timeslots ---------------------------------------------------------------------
    labels: dict[int, str] = {}
    trow = sheets["Timeslots"]
    tidx = _header_index(trow[0])
    for row in trow[1:]:
        if not row or row[0] is None:
            continue
        k = int(_num(row[tidx.get("slot_index", 2)])) - 1
        h = k % 12
        if h in labels:
            continue
        labels[h] = f"{_fmt_time(row[tidx.get('start', 3)])}-{_fmt_time(row[tidx.get('end', 4)])}"
    if not labels:
        labels = {h: f"{8 + h:02d}:00-{8 + h:02d}:50" for h in range(12)}

    return Dataset(courses, groups, sorted(rooms.values(), key=lambda r: (-r.capacity, r.code)), labels, issues)


def _fmt_time(v) -> str:
    if isinstance(v, dtime):
        return v.strftime("%H:%M")
    frac = _num(v)
    minutes = round(frac * 24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
