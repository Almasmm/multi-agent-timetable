"""University data desk: the workbook the agents use, as four editable categories.

Categories: rooms (sheet ``Rooms``), groups (``Groups``), the curriculum with teaching hours (one sheet per
programme) and the time grid (``Timeslots``: 12 pairs, the same for the six days). The dispatcher uploads each
category separately (Excel or CSV, English or Russian headers) or all of them at once from the single template,
edits rows in the web UI, and saves. Every cell is validated, then the edited tables go through the Data agent's
own loader (``domain.loader.normalize``); safe automatic fixes repeat in the source what the loader guesses.

Original files in ``data/`` and ``master-dataset/`` are never changed: the first save writes a working copy to
``data/uploads/`` and the agents switch to it. Every later save first puts the previous file into ``.history/``
so any version can be restored. The Watcher fingerprints the active workbook, so a saved change makes the
agents regenerate the timetable on their own.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import time
from datetime import time as dtime
from pathlib import Path
from typing import Any

from ..config import DATASET_DIRS, DAYS, Settings
from ..core.errors import DataError
from ..domain.demand import trimester_of
from ..domain.loader import (CURRICULUM_COLS, CYRILLIC_LOOKALIKES, EXTRA_SHEETS, INFO_SHEETS, SERVICE_SHEETS, normalize,
                             read_workbook)
from ..domain.plan import is_plan, plan_courses, plan_meta, program_from_name

MAX_ROWS = 5000
MAX_TEXT = 300
HISTORY_KEEP = 30
SAFE_STEM = re.compile(r"[^A-Za-z0-9_.\- ]+")


def C(key: str, label: str, type: str = "text", **kw) -> dict:  # noqa: A002 - mirrors the JSON field name
    return {"key": key, "label": label, "type": type, **kw}


ROOM_TYPES = [["L", "лекционная"], ["P", "практическая"], ["K", "компьютерный класс"]]
MODES = [["offline", "очно"], ["online", "онлайн"]]
TABLES: dict[str, dict] = {
    "rooms": {"title": "Аудитории", "sheet": "Rooms", "cols": [
        C("room_code", "Аудитория", required=True, unique=True, max_len=40, width=150, mono=True,
          help="Код аудитории, например C1.2.240", aliases=["код", "код аудитории", "кабинет", "room"]),
        C("capacity", "Мест", "int", required=True, min=1, max=2000, width=90,
          aliases=["вместимость", "места", "количество мест", "seats"]),
        C("room_type", "Тип", "enum", required=True, options=ROOM_TYPES, width=180,
          help="L — лекционная, P — практическая, K — компьютерный класс (нужен для лабораторных)",
          aliases=["тип аудитории", "type"]),
        C("available", "Доступна", "bool", default=True, width=105,
          help="Недоступную аудиторию агенты не используют", aliases=["доступно", "доступность"]),
        C("floor", "Этаж", "int", min=-5, max=100, width=80),
        C("building", "Корпус", max_len=40, width=100, aliases=["здание"]),
        C("features", "Оснащение", max_len=200, width=210, aliases=["оборудование"],
          help="Через точку с запятой: computers; electronics; projector; sports")]},
    "groups": {"title": "Группы", "sheet": "Groups", "cols": [
        C("group_code", "Группа", required=True, unique=True, max_len=30, width=140, mono=True,
          pattern=r"^[A-Za-z]+[-A-Za-z0-9]*$", pattern_hint="латинские буквы программы, дефис, цифры: IT-2301",
          help="Первые буквы кода — программа (IT-2301 → IT)", aliases=["код группы", "group"]),
        C("department", "Кафедра", "int", min=0, max=999, width=95, help="Код кафедры (необязательно)"),
        C("year", "Курс", "int", required=True, min=1, max=6, width=80, aliases=["год обучения"]),
        C("headcount", "Студентов", "int", required=True, min=1, max=500, width=110,
          aliases=["численность", "количество студентов", "кол-во студентов", "студенты"]),
        C("admission_year", "Год набора", "int", min=2000, max=2100, width=110, aliases=["год поступления"],
          help="Год поступления: по нему выбирается версия учебного плана"),
        C("study_language", "Язык обучения", "enum", options=[["kz", "казахский"], ["ru", "русский"], ["en", "английский"]],
          width=150, aliases=["язык"])]},
    "courses": {"title": "Учебный план", "sheet": None, "cols": [
        C("program", "Программа", required=True, max_len=12, width=110, mono=True, pattern=r"^[A-Za-z]+$",
          pattern_hint="латинские буквы: IT, SE, CS", help="Код программы = имя листа учебного плана",
          aliases=["оп", "образовательная программа"]),
        C("course_name", "Дисциплина", required=True, max_len=200, width=330, flex=True,
          aliases=["предмет", "название", "название дисциплины", "course"]),
        C("trimester", "Триместр", "int", required=True, min=1, max=12, width=100,
          help="Номер триместра обучения: 1–3 — первый курс, 4–6 — второй и т. д."),
        C("credits", "Кредиты", "int", min=0, max=60, width=95, aliases=["кредитов"]),
        C("lecture_slots", "Лекции", "int", min=0, max=1000, width=95, hours=True,
          help="Академических часов лекций за триместр", aliases=["лекций", "лекции, ч"]),
        C("practice_slots", "Практики", "int", min=0, max=1000, width=100, hours=True,
          help="Академических часов практик за триместр", aliases=["практика", "практик", "практики, ч"]),
        C("lab_slots", "Лаб.", "int", min=0, max=1000, width=85, hours=True,
          help="Академических часов лабораторных за триместр (только в компьютерных классах)",
          aliases=["лаб", "лабораторные", "лабораторных"]),
        C("delivery_mode", "Формат", "enum", options=MODES, default="offline", width=110,
          aliases=["форма", "формат обучения", "mode"]),
        C("lecture_precedes_practice", "Лекция раньше практики", "bool", default=True, width=120),
        C("course_code", "Код дисциплины", max_len=60, width=150, mono=True, aliases=["код"]),
        C("component", "Компонент", "enum", options=[["ОК", "обязательный"], ["ВК", "вузовский"], ["КВ", "по выбору"]], width=150),
        C("subgroups", "Подгрупп", "int", min=1, max=6, width=100,
          help="На сколько подгрупп делится группа на практиках и лабораторных (языки — 2)"),
        C("room_feature", "Нужное оснащение", max_len=100, width=170, aliases=["оснащение"],
          help="Что нужно в аудитории для практик и лабораторных: computers, electronics…")]},
    "timeslots": {"title": "Сетка времени", "sheet": "Timeslots", "fixed": True, "cols": [
        C("pair", "Пара", "int", readonly=True, min=1, max=12, width=80, aliases=["№", "номер пары", "slot_index"]),
        C("start", "Начало", "time", required=True, width=110, mono=True, aliases=["начало пары"]),
        C("end", "Конец", "time", required=True, width=110, mono=True, aliases=["конец пары", "окончание"])]},
}
EDITABLE = ("rooms", "groups", "courses", "timeslots")
COURSE_SHEET_COLS = list(CURRICULUM_COLS) + ["course_code", "component", "subgroups", "room_feature"]
PAIRS = 12
SHEET_TABLE = {"Rooms": "rooms", "Groups": "groups"}


# ----------------------------------------------------------------------------------------- folders
def upload_dir() -> Path:
    return DATASET_DIRS[-1]


def _history_dir(path: Path) -> Path:
    return upload_dir() / ".history" / path.stem


def find(dataset_id: str) -> Path:
    """A dataset id is a bare file name inside one of the allowed folders."""
    if Path(dataset_id).name != dataset_id or not dataset_id.lower().endswith(".xlsx"):
        raise FileNotFoundError(f"dataset '{dataset_id}' not found")
    for d in DATASET_DIRS:
        p = d / dataset_id
        if p.is_file():
            return p
    raise FileNotFoundError(f"набор данных «{dataset_id}» не найден")


def editable(path: Path) -> bool:
    return path.parent.resolve() == upload_dir().resolve()


def _taken(name: str) -> bool:
    return any((d / name).exists() for d in DATASET_DIRS)


def unique_name(stem: str, suffix: str = "") -> str:
    stem = SAFE_STEM.sub("-", stem).strip(" -.")[:60] or "dataset"
    if not stem[0].isalnum():
        stem = "d" + stem
    name, n = f"{stem}{suffix}.xlsx", 2
    while _taken(name):
        name, n = f"{stem}{suffix}-{n}.xlsx", n + 1
    return name


# ----------------------------------------------------------------------------------------- cells
def coerce(col: dict, v: Any) -> tuple[Any, str | None]:
    """Value as it will be written to the workbook, or an error in Russian."""
    t = col["type"]
    if isinstance(v, str):
        v = v.strip()
        if len(v) > MAX_TEXT:
            return v[:MAX_TEXT], f"не длиннее {MAX_TEXT} символов"
    if v is None or v == "":
        if t == "bool":
            return col.get("default", True), None
        if col.get("required"):
            return None, "обязательное поле"
        return (col.get("default") if t == "enum" else None), None
    if t == "int":
        if isinstance(v, bool):
            return None, "нужно целое число"
        try:
            f = float(str(v).replace(",", ".").replace(" ", "")) if isinstance(v, str) else float(v)
        except ValueError:
            return v, "нужно целое число"
        if f != int(f):
            return v, "нужно целое число"
        n = int(f)
        lo, hi = col.get("min"), col.get("max")
        if (lo is not None and n < lo) or (hi is not None and n > hi):
            return n, f"от {lo} до {hi}"
        return n, None
    if t == "bool":
        if isinstance(v, bool):
            return v, None
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "да", "y", "+", "1.0"):
            return True, None
        if s in ("false", "0", "no", "нет", "n", "-", "0.0"):
            return False, None
        return v, "да или нет"
    if t == "time":
        if isinstance(v, dtime):
            return v.strftime("%H:%M"), None
        if isinstance(v, float) and 0 <= v < 1:
            m = round(v * 24 * 60)
            return f"{m // 60:02d}:{m % 60:02d}", None
        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?::\d{2})?", str(v).strip())
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            return str(v), "время ЧЧ:ММ"
        return f"{int(m[1]):02d}:{m[2]}", None
    s = str(v)
    if t == "enum":
        for value, _ in col["options"]:
            if s.lower() == value.lower():
                return value, None
        return s, "допустимо: " + ", ".join(f"{value} ({label})" for value, label in col["options"])
    if col.get("max_len") and len(s) > col["max_len"]:
        return s, f"не длиннее {col['max_len']} символов"
    if col.get("pattern") and not re.match(col["pattern"], s):
        return s, f"формат: {col.get('pattern_hint', col['pattern'])}"
    return s, None


def _clean_rows(table: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Coerced rows (keys of the table only, ``_id`` passed through) and the cell errors."""
    if len(rows) > MAX_ROWS:
        raise ValueError(f"слишком много строк в таблице {table} (максимум {MAX_ROWS})")
    cols, out, errors = TABLES[table]["cols"], [], []
    for i, row in enumerate(rows):
        clean: dict[str, Any] = {"_id": row["_id"]} if "_id" in row else {}
        for col in cols:
            val, err = coerce(col, row.get(col["key"]))
            clean[col["key"]] = val
            if err:
                errors.append({"t": table, "r": i, "c": col["key"], "level": "error", "text": err})
        out.append(clean)
    for col in cols:
        if not col.get("unique"):
            continue
        first: dict[str, int] = {}
        for i, row in enumerate(out):
            key = str(row[col["key"]] or "").upper()
            if not key:
                continue
            if key in first:
                errors.append({"t": table, "r": i, "c": col["key"], "level": "error",
                               "text": f"повторяется (строка {first[key] + 1})"})
            else:
                first[key] = i
    if table == "timeslots":
        if [r.get("pair") for r in out] != list(range(1, PAIRS + 1)):
            errors.append({"t": table, "r": 0, "c": "pair", "level": "error", "text": f"нужны пары 1–{PAIRS} по порядку"})
        prev_end = None
        for i, row in enumerate(out):
            a, b = row.get("start"), row.get("end")
            if not (_clock(a) and _clock(b)):
                continue
            if b <= a:
                errors.append({"t": table, "r": i, "c": "end", "level": "error", "text": "конец раньше начала"})
            if prev_end and a < prev_end:
                errors.append({"t": table, "r": i, "c": "start", "level": "warning", "text": f"пересекается с предыдущей парой (до {prev_end})"})
            prev_end = b
    if table == "courses":
        seen: dict[tuple, int] = {}
        for i, row in enumerate(out):
            key = (row["program"], str(row["course_name"] or "").lower(), row["trimester"])
            if key in seen and row["course_name"]:
                errors.append({"t": table, "r": i, "c": "course_name", "level": "warning",
                               "text": f"дисциплина уже есть в этом триместре (строка {seen[key] + 1})"})
            seen.setdefault(key, i)
    return out, errors


# ----------------------------------------------------------------------------------------- workbook <-> tables
def _time(v) -> str:
    if isinstance(v, dtime):
        return v.strftime("%H:%M")
    if isinstance(v, (int, float)):
        m = round(float(v) * 24 * 60)
        return f"{m // 60:02d}:{m % 60:02d}"
    return str(v or "")


def _sheet_rows(rows: list[list], table: str) -> list[dict]:
    if not rows:
        return []
    idx = {str(c).strip().lower(): i for i, c in enumerate(rows[0]) if c is not None and str(c).strip()}
    out = []
    for raw in rows[1:]:
        if raw is None or all(v in (None, "") for v in raw):
            continue
        rec = {}
        for col in TABLES[table]["cols"]:
            j = idx.get(col["key"])
            v = raw[j] if j is not None and j < len(raw) else None
            val, err = coerce(col, v)
            rec[col["key"]] = _kept(v, val, err)
        out.append(rec)
    return out


def _kept(v, val, err):
    """The clean value, or what the file had (stripped) so the editor can show the wrong cell."""
    if not err:
        return val
    return (v.strip() or None) if isinstance(v, str) else v


def tables_from_sheets(sheets: dict[str, list[list]]) -> dict[str, list[dict]]:
    courses = []
    for name, rows in sheets.items():
        if name in SERVICE_SHEETS or name in INFO_SHEETS or not rows:
            continue
        head = {str(c).strip().lower() for c in rows[0] if c is not None}
        if not {"course_name", "trimester"} <= head:
            continue
        courses += [{**r, "program": name} for r in _sheet_rows(rows, "courses")]
    return {"rooms": _sheet_rows(sheets["Rooms"], "rooms"), "groups": _sheet_rows(sheets["Groups"], "groups"),
            "courses": courses, "timeslots": _pairs(sheets["Timeslots"])}


def _pairs(rows: list[list]) -> list[dict]:
    """72 workbook slots -> 12 pairs (the loader also takes the first time it meets for each pair)."""
    idx = {_norm(c): i for i, c in enumerate(rows[0] if rows else []) if c is not None}
    jn = next((idx[k] for k in ("slotindex", "pair", "пара", "№", "номерпары") if k in idx), None)
    js, je = idx.get("start", idx.get("начало")), idx.get("end", idx.get("конец"))
    found: dict[int, dict] = {}
    for raw in rows[1:]:
        if not raw or js is None or je is None:
            continue
        try:
            k = int(float(raw[jn])) - 1 if jn is not None and raw[jn] not in (None, "") else len(found)
        except (TypeError, ValueError):
            continue
        h = k % PAIRS
        if h not in found:
            found[h] = {"pair": h + 1, "start": _time(raw[js]), "end": _time(raw[je])}
    return [found.get(h, {"pair": h + 1, "start": f"{8 + h:02d}:00", "end": f"{8 + h:02d}:50"}) for h in range(PAIRS)]


def _norm(s) -> str:
    return re.sub(r"[\s_\-.,]+", "", str(s or "").strip().lower())


def read_tables(path: Path) -> dict[str, list[dict]]:
    try:
        return tables_from_sheets(read_workbook(path))
    except DataError as exc:  # e.g. a raw curriculum file without the Groups / Rooms sheets
        raise ValueError(ru_issue(str(exc))) from exc


def _excel(v):
    return "" if v is None else v


def _clock(s) -> dtime | None:
    m = re.fullmatch(r"(\d\d):(\d\d)", str(s or ""))
    return dtime(int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None


def timeslot_sheet(pairs: list[dict]) -> list[list]:
    """12 pairs -> the 72-row Timeslots sheet (Mon-Sat x 12) the loader expects."""
    rows: list[list] = [["slot_id", "day", "slot_index", "start", "end"]]
    for d, day in enumerate(DAYS):
        for h, p in enumerate(pairs[:PAIRS]):
            k = d * PAIRS + h + 1
            rows.append([f"{day.upper()}-{k:02d}", day, k, _clock(p.get("start")), _clock(p.get("end"))])
    return rows


def to_sheets(tables: dict[str, list[dict]]) -> tuple[dict[str, list[list]], dict]:
    """Workbook sheets as lists of rows + where each programme sheet row came from (for issue mapping)."""
    sheets: dict[str, list[list]] = {}
    origin: dict[str, list[int]] = {}
    for i, row in enumerate(tables["courses"]):
        prog = str(row.get("program") or "").strip() or "UNKNOWN"
        if prog not in sheets:
            sheets[prog], origin[prog] = [list(COURSE_SHEET_COLS)], []
        sheets[prog].append([_excel(row.get(k)) for k in COURSE_SHEET_COLS])
        origin[prog].append(i)
    for table in ("groups", "rooms"):
        keys = [c["key"] for c in TABLES[table]["cols"]]
        sheets[TABLES[table]["sheet"]] = [keys] + [[_excel(r.get(k)) for k in keys] for r in tables[table]]
    sheets["Timeslots"] = timeslot_sheet(tables["timeslots"])
    return sheets, origin


# ----------------------------------------------------------------------------------------- loader issues in Russian
_COL_RU = {"lecture_slots": "лекции", "practice_slots": "практики", "lab_slots": "лабораторные"}
_ISSUE_RU = [
    (r"delivery_mode '(.*)' -> '(.*)'", lambda m: f"формат «{m[1]}» будет прочитан как «{m[2]}»"),
    (r"(.*): (\w+)=([\d.]+) is not a multiple of (\d+) weeks, rounded to (\d+) h/week",
     lambda m: f"{m[1]}: {_COL_RU.get(m[2], m[2])} {m[3]} ч не делится на {m[4]} недель — округлено до {m[5]} ч/нед"),
    (r"(.*): no lecture/practice/lab slots - course skipped",
     lambda m: f"{m[1]}: нет часов лекций, практик и лабораторных — дисциплина пропущена"),
    (r"sheet skipped: no course_name/trimester header", lambda m: "лист пропущен: нет столбцов course_name и trimester"),
    (r"group (.*): no curriculum sheet '(.*)' - group skipped",
     lambda m: f"группа {m[1]}: нет учебного плана программы «{m[2]}» — группа пропущена"),
    (r"duplicate group (.*) skipped", lambda m: f"группа {m[1]} повторяется — дубликат пропущен"),
    (r"group (.*): missing headcount, assumed 20", lambda m: f"группа {m[1]}: не указано число студентов — принято 20"),
    (r"room code '(.*)' contained Cyrillic letters -> '(.*)'",
     lambda m: f"в коде аудитории «{m[1]}» кириллица — будет прочитан как «{m[2]}»"),
    (r"room (.*) marked unavailable - skipped", lambda m: f"аудитория {m[1]} отмечена недоступной — агенты её не используют"),
    (r"duplicate room (.*): kept capacity (\d+)", lambda m: f"аудитория {m[1]} повторяется — оставлена вместимость {m[2]}"),
    (r"room (.*) \(capacity (\d+)\) is smaller than any group", lambda m: f"аудитория {m[1]} ({m[2]} мест) меньше любой группы"),
    (r"workbook is missing required sheet\(s\): (.*)", lambda m: f"в книге нет обязательных листов: {m[1]}"),
    (r"cannot open workbook (.*?): (.*)", lambda m: f"не удалось открыть файл {m[1]}: это не книга Excel .xlsx"),
    (r"input workbook not found: (.*)", lambda m: "файл не найден"),
]


def ru_issue(text: str) -> str:
    for pat, fmt in _ISSUE_RU:
        m = re.fullmatch(pat, text)
        if m:
            return fmt(m)
    return text


def _map_issue(where: str, origin: dict) -> tuple[str | None, int | None]:
    sheet, _, row = where.partition("!")
    if not row.isdigit():
        return SHEET_TABLE.get(sheet), None
    n = int(row) - 2
    if sheet in SHEET_TABLE:
        return SHEET_TABLE[sheet], n
    rows = origin.get(sheet) or []
    return ("courses", rows[n]) if 0 <= n < len(rows) else (None, None)


# ----------------------------------------------------------------------------------------- validation
def validate(tables: dict[str, list[dict]], weeks: int | None = None) -> dict:
    weeks = weeks or Settings().weeks_per_term
    clean, cells = {}, []
    for t in EDITABLE:
        clean[t], errs = _clean_rows(t, tables.get(t) or [])
        cells += errs
    sheets, origin = to_sheets(clean)
    ds = normalize(sheets, weeks)
    issues = []
    for i in ds.issues:
        t, r = _map_issue(i["where"], origin)
        issues.append({"level": i["level"], "where": i["where"], "text": ru_issue(i["issue"]), "t": t, "r": r})
    for p in sorted({g.program for g in ds.groups}):
        for y in sorted({g.year for g in ds.groups if g.program == p}):
            if not any(c.program == p and c.trimester in [trimester_of(y, k) for k in (1, 2, 3)] for c in ds.courses):
                issues.append({"level": "warning", "where": f"{p}-{y}", "t": "courses", "r": None,
                               "text": f"у {y} курса программы {p} нет дисциплин ни в одном триместре года"})
    return {"cells": cells, "issues": issues, "errors": sum(c["level"] == "error" for c in cells),
            "summary": summarize(ds, clean), "clean": clean}


def summarize(ds, clean: dict) -> dict:
    """Numbers for the overview: programmes x years with weekly hours per term, rooms by type."""
    load: dict[str, dict] = {}
    for g in ds.groups:
        cell = load.setdefault(g.program, {}).setdefault(str(g.year), {"groups": 0, "students": 0, "hours": [0, 0, 0]})
        cell["groups"] += 1
        cell["students"] += g.headcount
    for prog, years in load.items():
        for y, cell in years.items():
            for k in (1, 2, 3):
                tri = trimester_of(int(y), k)
                cell["hours"][k - 1] = sum(sum(c.hours.values()) for c in ds.courses if c.program == prog and c.trimester == tri)
    by_type = {}
    for r in ds.rooms:
        x = by_type.setdefault(r.rtype, {"rooms": 0, "seats": 0, "max": 0})
        x["rooms"] += 1
        x["seats"] += r.capacity
        x["max"] = max(x["max"], r.capacity)
    biggest = max((g.headcount for g in ds.groups), default=0)
    return {"programs": len(load), "groups": len(ds.groups), "students": sum(g.headcount for g in ds.groups),
            "rooms": len(ds.rooms), "seats": sum(r.capacity for r in ds.rooms), "courses": len(ds.courses),
            "unavailable_rooms": sum(1 for r in clean["rooms"] if r.get("available") is False),
            "rooms_by_type": by_type, "load": load, "biggest_group": biggest,
            "trimesters": sorted({c.trimester for c in ds.courses}), "weeks": Settings().weeks_per_term,
            "day_span": [ds.slot_labels.get(0, "")[:5], ds.slot_labels.get(PAIRS - 1, "")[-5:]],
            "issues": {"fixed": sum(i["level"] == "fixed" for i in ds.issues),
                       "warning": sum(i["level"] == "warning" for i in ds.issues)}}


_ready_cache: dict[str, tuple[float, dict]] = {}


def readiness(path: Path) -> dict:
    """Is the workbook ready for generation? Every problem the Data agent's loader reports (cell errors, skipped
    rows, values it would have to guess) must be fixed by the operator first; notes of level "info" do not block."""
    mtime = path.stat().st_mtime
    hit = _ready_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        tables = read_tables(path)
        res = check(path, tables)
    except ValueError as exc:  # not a university workbook at all
        out = {"ready": False, "problems": 1, "errors": 1, "warnings": 0, "fixed": 0,
               "items": [{"level": "error", "text": str(exc), "t": None}], "dataset": path.name}
        _ready_cache[str(path)] = (mtime, out)
        return out
    cells = res["cells"]
    issues = [i for i in res["issues"] if i["level"] != "info"]
    errors = sum(c["level"] == "error" for c in cells)
    warnings = sum(c["level"] != "error" for c in cells) + sum(i["level"] == "warning" for i in issues)
    fixed = sum(i["level"] == "fixed" for i in issues)
    items = [{"level": c["level"], "text": _cell_where(tables, c), "t": c["t"]} for c in cells if c["level"] == "error"][:4] + \
        [{"level": i["level"], "text": _issue_where(i), "t": i["t"]}
         for i in sorted(issues, key=lambda i: i["level"] != "warning")][:6]
    out = {"ready": not (errors or warnings or fixed), "problems": errors + warnings + fixed, "errors": errors,
           "warnings": warnings, "fixed": fixed, "items": items, "dataset": path.name}
    _ready_cache[str(path)] = (mtime, out)
    return out


def _cell_where(tables: dict, c: dict) -> str:
    """'Учебный план, строка 12, Формат «onlline»: допустимо …' - where the operator has to look."""
    col = next((x for x in TABLES[c["t"]]["cols"] if x["key"] == c["c"]), {"label": c["c"]})
    rows = tables.get(c["t"]) or []
    value = rows[c["r"]].get(c["c"]) if 0 <= c["r"] < len(rows) else None
    shown = f" «{value}»" if value not in (None, "") else ""
    return f"{TABLES[c['t']]['title']}, строка {c['r'] + 1}, {col['label']}{shown}: {c['text']}"


def _issue_where(i: dict) -> str:
    return f"{TABLES[i['t']]['title']}, строка {i['r'] + 1}: {i['text']}" if i["t"] and i["r"] is not None else i["text"]


def not_ready_reason(r: dict) -> str:
    return (f"данные вуза не готовы ({r['dataset']}): проблем — {r['problems']}; исправьте их на странице «Данные вуза», "
            "после этого агенты сгенерируют расписание")


def _complete(path: Path, tables: dict) -> dict:
    """Tables missing from a request are taken from the file as it is."""
    missing = [t for t in EDITABLE if t not in tables]
    return {**read_tables(path), **tables} if missing else tables


def check(path: Path, tables: dict) -> dict:
    res = validate(_complete(path, tables))
    res.pop("clean")
    return res


# ----------------------------------------------------------------------------------------- automatic fixes
def autofix(tables: dict[str, list[dict]]) -> dict:
    """Safe repairs the loader would do anyway, made visible in the source: typos in the delivery mode,
    Cyrillic look-alikes in codes, text booleans, letter case, and duplicate rooms (largest kept)."""
    out, changes = {}, []

    def note(t, i, k, old, new):
        changes.append({"t": t, "r": i, "c": k, "old": old, "new": new})

    for t in [t for t in EDITABLE if t in tables]:
        rows = [dict(r) for r in tables[t]]
        for i, row in enumerate(rows):
            for col in TABLES[t]["cols"]:
                k, v = col["key"], row.get(col["key"])
                new = v
                if isinstance(v, str):
                    new = v.strip()
                    if col.get("mono"):
                        new = new.translate(CYRILLIC_LOOKALIKES)
                    if k == "delivery_mode" and new.lower() not in ("online", "offline"):
                        low = new.lower()
                        new = "online" if low.startswith(("onl", "онл", "дист")) else "offline" if low else new
                if new not in (None, ""):
                    val, err = coerce(col, new)
                    if not err:
                        new = val
                if new != v and not (v is None and new == ""):
                    row[k] = new
                    note(t, i, k, v, new)
        if t == "rooms":
            best: dict[str, int] = {}
            for i, row in enumerate(rows):
                code = str(row.get("room_code") or "").upper()
                cap = row.get("capacity") if isinstance(row.get("capacity"), int) else 0
                if not code:
                    continue
                j = best.get(code)
                if j is None or cap > (rows[j].get("capacity") if isinstance(rows[j].get("capacity"), int) else 0):
                    best[code] = i
            keep = set(best.values()) | {i for i, r in enumerate(rows) if not str(r.get("room_code") or "")}
            for i, row in enumerate(rows):
                if i not in keep:
                    changes.append({"t": t, "r": i, "c": "room_code", "old": row.get("room_code"), "new": None,
                                    "removed": True})
            rows = [r for i, r in enumerate(rows) if i in keep]
        out[t] = rows
    return {"tables": out, "changes": changes}


# ----------------------------------------------------------------------------------------- history
def _history_index(path: Path) -> list[dict]:
    p = _history_dir(path) / "history.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def history(path: Path) -> list[dict]:
    return [h for h in _history_index(path) if (_history_dir(path) / h["file"]).exists()][::-1]


def _backup(path: Path, note: str) -> None:
    """Keep the file as it was before this change."""
    if not path.exists():
        return
    d = _history_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    shutil.copy2(path, d / f"{stamp}.xlsx")
    items = _history_index(path) + [{"file": f"{stamp}.xlsx", "stamp": stamp, "ts": path.stat().st_mtime,
                                     "saved": time.time(), "note": note, "size_kb": round(path.stat().st_size / 1024)}]
    for old in items[:-HISTORY_KEEP]:
        (d / old["file"]).unlink(missing_ok=True)
    (d / "history.json").write_text(json.dumps(items[-HISTORY_KEEP:], ensure_ascii=False, indent=1), encoding="utf-8")


def restore(path: Path, stamp: str) -> None:
    if not editable(path):
        raise ValueError("исходный файл защищён: восстанавливать можно только рабочую копию")
    item = next((h for h in history(path) if h["stamp"] == stamp), None)
    if item is None:
        raise FileNotFoundError("такой версии нет в истории")
    _backup(path, f"восстановлена версия {stamp}")
    shutil.copy2(_history_dir(path) / item["file"], path)
    os.utime(path)


# ----------------------------------------------------------------------------------------- writing
def write_workbook(src: Path, target: Path, clean: dict[str, list[dict]], extra: dict | None = None) -> None:
    """Replace the Rooms, Groups, Timeslots and programme sheets of ``src`` and the extra sheets that came with an
    upload (teachers, load...); every other sheet is kept."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    wb = load_workbook(src)
    sheets, _ = to_sheets(clean)
    for ws in list(wb.worksheets):
        if ws.title in SERVICE_SHEETS or ws.title in INFO_SHEETS or ws.title in sheets:
            continue
        head = {str(c.value).strip().lower() for c in ws[1] if c.value is not None} if ws.max_row else set()
        if {"course_name", "trimester"} <= head:  # a programme that no longer has courses
            wb.remove(ws)
    for name, rows in sheets.items():
        ws = wb[name] if name in wb.sheetnames else wb.create_sheet(name)
        ws.delete_rows(1, ws.max_row + 1)
        for row in rows:
            ws.append([None if v == "" else v for v in row])
        if name == "Timeslots":
            for cells in ws.iter_rows(min_row=2, min_col=4, max_col=5):
                for c in cells:
                    c.number_format = "hh:mm"
    for name, rows in (extra or {}).items():
        ws = wb[name] if name in wb.sheetnames else wb.create_sheet(name)
        ws.delete_rows(1, ws.max_row + 1)
        for row in rows:
            ws.append([None if v == "" else v for v in row])
        for c in ws[1]:
            c.font = Font(bold=True)
    tmp = target.with_name(f".saving-{target.name}")
    wb.save(tmp)
    wb.close()
    os.replace(tmp, target)


def same_version(path: Path, base_mtime: float | None) -> None:
    if base_mtime is not None and abs(path.stat().st_mtime - base_mtime) > 1e-3:
        raise RuntimeError("файл изменился с момента открытия (загружен заново или восстановлен) — обновите страницу")


def _jsonable(v):
    if isinstance(v, dtime):
        return v.strftime("%H:%M")
    if hasattr(v, "isoformat"):  # date / datetime
        return v.isoformat()[:10]
    return v


def read_extra(path: Path) -> dict[str, list[list]]:
    """The extra sheets of a workbook, as JSON-friendly rows (trailing empty rows dropped)."""
    sheets = read_workbook(path)
    out = {}
    for name in EXTRA_SHEETS:
        rows = [[_jsonable(v) for v in r] for r in sheets.get(name) or []]
        while rows and all(v in (None, "") for v in rows[-1]):
            rows.pop()
        if rows:
            out[name] = rows
    return out


def extra_counts(path: Path) -> dict[str, int]:
    return {k: max(0, len(v) - 1) for k, v in read_extra(path).items()}


def check_extra(extra: dict | None) -> dict:
    extra = extra or {}
    bad = [k for k in extra if k not in EXTRA_SHEETS]
    if bad:
        raise ValueError(f"неизвестные листы: {', '.join(bad)}")
    if any(len(v) > MAX_ROWS for v in extra.values()):
        raise ValueError(f"слишком много строк (максимум {MAX_ROWS})")
    return extra


def save(path: Path, tables: dict, base_mtime: float | None, note: str = "", extra: dict | None = None) -> dict:
    if not editable(path):
        raise ValueError("исходный файл защищён — правки сохраняются в рабочую копию")
    same_version(path, base_mtime)
    res = validate(_complete(path, tables))
    if res["errors"]:
        raise ValueError(f"в таблицах {res['errors']} ошибок — исправьте подсвеченные ячейки")
    clean = {t: [{k: v for k, v in r.items() if k != "_id"} for r in rows] for t, rows in res.pop("clean").items()}
    _backup(path, note or "правка в редакторе")
    write_workbook(path, path, clean, check_extra(extra))
    return {**res, "mtime": path.stat().st_mtime}


def copy(path: Path, stem: str | None = None) -> Path:
    upload_dir().mkdir(parents=True, exist_ok=True)
    target = upload_dir() / (unique_name(stem) if stem else unique_name(path.stem, "-edit"))
    shutil.copy2(path, target)
    os.utime(target)
    return target


def store_upload(filename: str, content: bytes) -> Path:
    """Check an uploaded workbook with the Data agent's loader before it joins the datasets."""
    name = Path(filename or "upload.xlsx").name
    if not name.lower().endswith(".xlsx"):
        raise ValueError("нужен файл Excel .xlsx")
    upload_dir().mkdir(parents=True, exist_ok=True)
    tmp = upload_dir() / f".incoming-{int(time.time() * 1000)}.xlsx"
    tmp.write_bytes(content)
    try:
        normalize(read_workbook(tmp), Settings().weeks_per_term)
    except DataError as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(ru_issue(str(exc).replace(tmp.name, name))) from exc
    except Exception as exc:  # not a workbook at all, or a broken one
        tmp.unlink(missing_ok=True)
        raise ValueError(f"файл не читается как книга Excel: {type(exc).__name__}") from exc
    stem = SAFE_STEM.sub("-", Path(name).stem).strip(" -.")[:60] or "upload"
    target = upload_dir() / f"{stem}.xlsx"
    if target.exists():  # a new version of an uploaded file: keep the old one in the history
        _backup(target, "загружена новая версия файла")
    elif _taken(target.name):  # same name as an original: never shadow it
        target = upload_dir() / unique_name(stem)
    os.replace(tmp, target)
    return target


def delete(path: Path) -> None:
    if not editable(path):
        raise ValueError("исходные файлы не удаляются из платформы")
    path.unlink()
    shutil.rmtree(_history_dir(path), ignore_errors=True)


# ----------------------------------------------------------------------------------------- template
EXAMPLE = {
    "rooms": [{"room_code": "C1.1.100", "capacity": 120, "room_type": "L", "available": True, "floor": 1, "building": "C1", "features": "projector"},
              {"room_code": "C1.2.201", "capacity": 30, "room_type": "P", "available": True, "floor": 2, "building": "C1", "features": "projector"},
              {"room_code": "C1.3.301", "capacity": 28, "room_type": "K", "available": True, "floor": 3, "building": "C1", "features": "computers; projector"}],
    "groups": [{"group_code": "IT-2501", "department": 1, "year": 1, "headcount": 24, "admission_year": 2025, "study_language": "en"},
               {"group_code": "IT-2502", "department": 1, "year": 1, "headcount": 22, "admission_year": 2025, "study_language": "en"}],
    "courses": [{"program": "IT", "course_name": "Calculus 1", "trimester": 1, "credits": 5, "lecture_slots": 30, "practice_slots": 20,
                 "delivery_mode": "offline", "lecture_precedes_practice": True, "course_code": "MATH 1115", "component": "ВК", "subgroups": 1},
                {"program": "IT", "course_name": "Introduction to Programming 1", "trimester": 1, "credits": 5, "lecture_slots": 20,
                 "lab_slots": 30, "delivery_mode": "offline", "lecture_precedes_practice": True, "course_code": "CODE404", "component": "ВК",
                 "subgroups": 2, "room_feature": "computers"},
                {"program": "IT", "course_name": "Russian Language 1 / Kazakh Language 1", "trimester": 1, "credits": 5, "practice_slots": 50,
                 "delivery_mode": "offline", "lecture_precedes_practice": False, "course_code": "RL2202 / KL2202", "component": "ОК", "subgroups": 2},
                {"program": "IT", "course_name": "Technological Entrepreneurship / Entrepreneurship", "trimester": 1, "credits": 5,
                 "lecture_slots": 20, "practice_slots": 30, "delivery_mode": "online", "lecture_precedes_practice": True,
                 "course_code": "TP 3113 / Pred 2116", "component": "КВ", "subgroups": 1},
                {"program": "IT", "course_name": "Physical Education", "trimester": 1, "credits": 2, "practice_slots": 20,
                 "delivery_mode": "offline", "lecture_precedes_practice": False, "course_code": "Fiz 1112", "component": "ОК", "room_feature": "sports"}],
    "programs": [{"program": "IT", "edu_code": "6B06101", "name_ru": "Компьютерные науки", "direction": "6B061 - Информационно-коммуникационные технологии",
                  "degree": "Бакалавр", "form": "Очное (бакалавр 3 года)", "years": "2025-2028", "admission_year": 2025}],
}


def template() -> bytes:
    """The single template: instruction sheet, programme reference and every category with example rows."""
    from .book import build_book
    pairs = [{"pair": h + 1, "start": f"{8 + h:02d}:00", "end": f"{8 + h:02d}:50"} for h in range(PAIRS)]
    return build_book(EXAMPLE, EXAMPLE["programs"], None, timeslot_sheet(pairs))


def import_context(path: Path) -> dict:
    """What an official curriculum (УП) needs from the working workbook: programme codes by edu code
    (sheet Programs) and the delivery mode already used for a course name."""
    sheets = read_workbook(path)
    programs = {}
    rows = sheets.get("Programs") or []
    if rows:
        idx = {str(c).strip().lower(): i for i, c in enumerate(rows[0]) if c}
        if "program" in idx and "edu_code" in idx:
            for r in rows[1:]:
                if r and r[idx["edu_code"]] and r[idx["program"]]:
                    programs[str(r[idx["edu_code"]]).strip().replace("В", "B")] = str(r[idx["program"]]).strip()
    modes: dict[str, str] = {}
    for c in tables_from_sheets(sheets)["courses"]:
        if c.get("course_name") and c.get("delivery_mode") in ("online", "offline"):
            modes.setdefault(str(c["course_name"]).lower(), c["delivery_mode"])
    return {"programs": programs, "modes": modes}


# ----------------------------------------------------------------------------------------- import of one category
def _match_cols(table: str, header: list) -> dict[int, str]:
    """Header cell index -> column key, by key, Russian label or alias (case, spaces, underscores ignored)."""
    names = {}
    for col in TABLES[table]["cols"]:
        for n in [col["key"], col["label"], *col.get("aliases", [])]:
            names.setdefault(_norm(n), col["key"])
    out = {}
    for i, h in enumerate(header):
        k = names.get(_norm(h))
        if k and k not in out.values():
            out[i] = k
    return out


NEED = {"rooms": {"room_code", "capacity"}, "groups": {"group_code"}, "courses": {"course_name"},
        "timeslots": {"start", "end"}}


def _rows_of(table: str, rows: list[list], cols: dict[int, str], program: str | None = None) -> list[dict]:
    by_key = {c["key"]: c for c in TABLES[table]["cols"]}
    out = []
    for raw in rows[1:]:
        if raw is None or all(v in (None, "") for v in raw):
            continue
        rec = {}
        for j, k in cols.items():
            v = raw[j] if j < len(raw) else None
            val, err = coerce(by_key[k], v)
            rec[k] = _kept(v, val, err)
        if table == "courses" and "program" not in rec and program:
            rec["program"] = program
        out.append(rec)
    if len(out) > MAX_ROWS:
        raise ValueError(f"в файле больше {MAX_ROWS} строк")
    return out


def _read_any(filename: str, content: bytes) -> dict[str, list[list]]:
    """Sheets of an .xlsx file, or the one table of a .csv file."""
    name = filename.lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        return {Path(filename).stem: [list(r) for r in csv.reader(io.StringIO(text), dialect)]}
    if not name.endswith(".xlsx"):
        raise ValueError("нужен файл Excel .xlsx или таблица .csv")
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheets = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        wb.close()
    except Exception as exc:
        raise ValueError(f"файл не читается как книга Excel: {type(exc).__name__}") from exc
    return sheets


def import_file(filename: str, content: bytes, table: str | None = None, context: dict | None = None) -> dict:
    """Rows found in an uploaded file, per category: nothing is saved here - the editor shows a preview,
    the dispatcher chooses replace or update, reviews the rows and saves. ``table=None`` reads every
    category it can find (the single template), otherwise only that one (any sheet with fitting headers).
    An official curriculum (УП) is accepted as it is for the curriculum category (``domain/plan.py``)."""
    if table is not None and table not in EDITABLE:
        raise ValueError(f"неизвестная категория {table}")
    sheets = _read_any(filename, content)
    wanted = [table] if table else list(EDITABLE)
    tables, found = {}, {}
    ctx = context or {}
    if "courses" in wanted:
        for name, rows in sheets.items():
            if name in INFO_SHEETS or not is_plan(rows):
                continue
            meta = plan_meta(rows)
            prog = ((ctx.get("programs") or {}).get(meta.get("edu_code", ""))
                    or (name if re.fullmatch(r"[A-Za-z]+", name) else None) or program_from_name(filename))
            if not prog:
                raise ValueError("не удалось определить код программы: назовите файл по коду, например «IT.xlsx»")
            found_rows, notes = plan_courses(rows, ctx.get("modes"))
            tables.setdefault("courses", []).extend({**r, "program": prog} for r in found_rows)
            f = found.setdefault("courses", {"sheets": [], "columns": [], "ignored": [], "notes": [], "plan": []})
            f["sheets"].append(name)
            f["columns"] = list(CURRICULUM_COLS) + ["program"]
            f["notes"] += notes
            f["plan"].append({"program": prog, **meta})
    for t in wanted:
        if t in tables:
            continue
        preferred = TABLES[t]["sheet"]
        order = sorted((n for n in sheets if n not in INFO_SHEETS), key=lambda n: n != preferred)
        for name in order:
            rows = sheets[name]
            head = rows[0] if rows else []
            cols = _match_cols(t, head)
            if not NEED[t] <= set(cols.values()):
                continue
            if t == "timeslots":
                tables[t] = _pairs(rows)
                found[t] = {"sheets": [name], "columns": ["pair", "start", "end"], "ignored": []}
                break
            if t == "courses":  # every sheet with a curriculum header, the sheet name is the programme
                prog = name if re.fullmatch(r"[A-Za-z]+", name) else None
                tables.setdefault(t, []).extend(_rows_of(t, rows, cols, prog))
                f = found.setdefault(t, {"sheets": [], "columns": [], "ignored": []})
                f["sheets"].append(name)
                f["columns"] = sorted(set(f["columns"]) | set(cols.values()) | ({"program"} if prog else set()))
                f["ignored"] = sorted(set(f["ignored"]) | {str(h) for i, h in enumerate(head) if h not in (None, "") and i not in cols})
                continue
            tables[t] = _rows_of(t, rows, cols)
            found[t] = {"sheets": [name], "columns": list(cols.values()),
                        "ignored": [str(h) for i, h in enumerate(head) if h not in (None, "") and i not in cols]}
            break
    extra = {}
    if table is None:
        for name in EXTRA_SHEETS:
            rows = [[_jsonable(v) for v in r] for r in sheets.get(name) or []]
            while rows and all(v in (None, "") for v in rows[-1]):
                rows.pop()
            if rows:
                extra[name] = rows
    if not tables and not extra:
        if table:
            need = ", ".join(f"{k} ({next(c['label'] for c in TABLES[table]['cols'] if c['key'] == k)})" for k in sorted(NEED[table]))
            raise ValueError(f"в файле нет таблицы «{TABLES[table]['title']}»: нужны столбцы {need}")
        raise ValueError("в файле не найдено ни одной категории — возьмите единый шаблон")
    return {"tables": tables, "found": found, "missing": [t for t in wanted if t not in tables], "extra": extra}
