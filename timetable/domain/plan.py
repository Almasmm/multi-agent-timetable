"""Official curriculum («Учебный план», УП) of AITU -> rows of a scheduling curriculum sheet.

Teachers and departments already have the УП workbook: a title block (years, programme code and name,
direction, degree, admission year) and one row per discipline with credits, trimester, form of control and
hours (lectures, labs, practicals, independent work). The platform accepts that file as it is: this module
finds the header, reads the disciplines and maps them onto ``CURRICULUM_COLS``.

Mapping: «Наименование дисциплины» (English column) -> course_name, «Триместр изучения» -> trimester,
«Академические кредиты» -> credits, «Лекции» / «Практические» / «Лабораторные» -> lecture_slots /
practice_slots / lab_slots (academic hours per trimester). Practices with the control form «отчет» and the final
attestation have no classroom hours, so they are not scheduled.
"""
from __future__ import annotations

import re

PROGRAM_COLS = ["program", "edu_code", "name_ru", "direction", "gop", "degree", "form", "years", "admission_year"]


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _txt(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def find_header(rows: list[list]) -> int | None:
    """Index of the УП header row («Наименование дисциплины» + «Триместр изучения»), or None."""
    for i, r in enumerate(rows[:40]):
        cells = [_txt(c).lower() for c in r or []]
        if any("наименование дисциплины" in c for c in cells) and any("триместр" in c for c in cells):
            return i
    return None


def is_plan(rows: list[list]) -> bool:
    return find_header(rows) is not None


def plan_meta(rows: list[list]) -> dict:
    meta: dict = {}
    head = find_header(rows) or 0
    for r in rows[:head]:
        t = _txt((r or [None])[0])
        if m := re.search(r"на (\d{4})\s*-\s*(\d{4})", t):
            meta["years"] = f"{m[1]}-{m[2]}"
        if m := re.search(r'программы\s+(\S+)\s+"(.+?)"', t):
            meta["edu_code"], meta["name_ru"] = m[1].replace("В", "B"), m[2]
        if t.lower().startswith("по направлению"):
            meta["direction"] = re.sub(r"(?i)^по направлению подготовки", "", t).strip()
        if t.startswith("по ГОП"):
            meta["gop"] = t.replace("по ГОП", "").strip()
        if t.startswith("Степень"):
            meta["degree"] = t.split(":", 1)[1].strip()
        if t.startswith("Форма обучения"):
            meta["form"] = t.split(":", 1)[1].strip()
        if t.startswith("Год поступления"):
            meta["admission_year"] = int(_num(t.split(":", 1)[1]))
    return meta


def _columns(rows: list[list], head: int) -> dict[str, int]:
    """Column positions from the two-level УП header (the sub-header sits one or two rows below)."""
    cols: dict[str, int] = {}
    block = rows[head:head + 5]
    for r in block:
        for j, c in enumerate(r or []):
            t = _txt(c).lower()
            if "наименование дисциплины" in t:
                cols.setdefault("name", j)
            elif t.startswith("академические кредиты") or t == "кредиты":
                cols.setdefault("credits", j)
            elif t.startswith("триместр изучения"):
                cols.setdefault("trimester", j)
            elif t.startswith("форма контроля"):
                cols.setdefault("control", j)
            elif t == "лекции":
                cols.setdefault("lec", j)
            elif t.startswith("лабораторн"):
                cols.setdefault("lab", j)
            elif t.startswith("практическ"):
                cols.setdefault("pr", j)
            elif t == "на английском":
                cols["name"] = j  # the English name column under «Наименование дисциплины»
    return cols


def plan_courses(rows: list[list], modes: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Curriculum rows (CURRICULUM_COLS keys) + notes about what was skipped."""
    head = find_header(rows)
    if head is None:
        return [], ["не найден заголовок учебного плана («Наименование дисциплины», «Триместр изучения»)"]
    cols = _columns(rows, head)
    need = {"name", "trimester", "lec", "pr"}
    if not need <= set(cols):
        return [], ["в учебном плане не найдены столбцы: " + ", ".join(sorted(need - set(cols)))]
    modes = {k.lower(): v for k, v in (modes or {}).items()}
    out, notes = [], []
    for r in rows[head + 1:]:
        r = list(r or [])
        get = lambda k: r[cols[k]] if k in cols and cols[k] < len(r) else None  # noqa: E731
        name, tri = _txt(get("name")), _num(get("trimester"))
        if not name or not tri or not isinstance(r[0] if r else None, (int, float)):
            continue  # title, sub-headers, totals and the final attestation (no trimester)
        lec, pr, lab = _num(get("lec")), _num(get("pr")), _num(get("lab"))
        if "отчет" in _txt(get("control")).lower():
            notes.append(f"{name} (триместр {int(tri)}): практика без аудиторных часов — не ставится в расписание")
            continue
        if not (lec or pr or lab):
            notes.append(f"{name} (триместр {int(tri)}): в учебном плане нет часов — уточните лекции и практики")
        out.append({"course_name": name, "trimester": int(tri), "credits": int(_num(get("credits"))) or None,
                    "lecture_slots": int(lec) or None, "practice_slots": int(pr) or None, "lab_slots": int(lab) or None,
                    "delivery_mode": modes.get(name.lower(), "offline"), "lecture_precedes_practice": True})
    return out, notes


def program_from_name(filename: str) -> str | None:
    """«CS 22-25.xlsx» -> CS: the programme code is the leading Latin letters of the file name."""
    m = re.match(r"\s*([A-Za-z]+)", filename or "")
    return m[1] if m else None
