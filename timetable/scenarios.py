"""Test scenarios with expected results (Assignment 4 / exam). Each scenario runs the full
multi-agent pipeline and checks the outcome. Used by pytest and by
``python -m timetable.scenarios`` which writes docs/scenario_results.md."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .app import run
from .config import ROOT, Settings
from .core.runlog import load_from_jsonl


@dataclass
class Scenario:
    id: str
    title: str
    expected: str
    requirements: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    check: Callable[[dict, list[dict]], list[str]] = lambda res, rows: []


def _rows(res: dict) -> list[dict]:
    p = Path(res.get("out_dir", "")) / "timetable.csv"
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def _events(res: dict) -> list[dict]:
    p = Path(res["out_dir"]) / "events.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def _ok(res: dict) -> list[str]:
    errs = []
    if res["status"] != "ok":
        errs.append(f"status={res['status']} ({res.get('reason', res.get('hard_violations'))})")
    if res.get("hard_violations"):
        errs.append(f"hard violations: {res['hard_violations']}")
    return errs


def _messages(res: dict, task: str) -> list[dict]:
    return [e for e in _events(res) if e["kind"] == "message" and e.get("task") == task and e.get("intent")]


def chk_baseline(res, rows):
    errs = _ok(res)
    if not rows:
        errs.append("no timetable rows exported")
    return errs


def chk_year1_until_17(res, rows):
    bad = [r for r in rows if r["year"] == "1" and int(r["hour_index"]) >= 9]
    return _ok(res) + ([f"{len(bad)} year-1 classes at/after 17:00"] if bad else [])


def chk_group_friday_free(res, rows):
    bad = [r for r in rows if r["group"] == "IT-2301" and r["day"] == "Fri"]
    return _ok(res) + ([f"IT-2301 has {len(bad)} classes on Friday"] if bad else [])


def chk_room_monday(res, rows):
    bad = [r for r in rows if r["classroom"] == "C1.1.334L" and r["day"] == "Mon"]
    return _ok(res) + ([f"C1.1.334L used {len(bad)} times on Monday"] if bad else [])


def chk_max_daily(res, rows):
    per = defaultdict(int)
    for r in rows:
        per[(r["group"], r["day"])] += 1
    worst = max(per.values(), default=0)
    return _ok(res) + ([f"a group has {worst} h in one day (> 6)"] if worst > 6 else [])


def chk_soft_constraint(res, rows):
    errs = _ok(res)
    specs = _messages(res, "parse_constraints")
    text = json.dumps([e["payload"] for e in specs], ensure_ascii=False)
    if "'hard': False" not in text and '"hard": false' not in text:
        errs.append("the 'желательно' requirement was not parsed as soft")
    return errs


def chk_negotiation(res, rows):
    errs = _ok(res)
    actions = [e["payload"].get("action") for e in _messages(res, "resolve") if e.get("intent") == "result"]
    if not actions:
        errs.append("negotiator was never asked to resolve the infeasible chunk")
    return errs + ([] if errs else [f"(concessions: {', '.join(a for a in actions if a)})"][:0])


def chk_unknown_entity(res, rows):
    errs = _ok(res)
    w = [e["payload"].get("warnings") for e in _messages(res, "parse_constraints") if e.get("intent") == "result"]
    if not any("X999" in json.dumps(x, ensure_ascii=False) for x in w):
        errs.append("unknown room X999 was not reported")
    return errs


def chk_unparsed(res, rows):
    errs = _ok(res)
    w = json.dumps([e["payload"] for e in _messages(res, "parse_constraints")], ensure_ascii=False)
    if "not understood" not in w:
        errs.append("nonsense requirement was not reported as not understood")
    return errs


def chk_missing_file(res, rows):
    if res["status"] != "failed" or "загрузить данные" not in res.get("reason", ""):
        return [f"expected a clean failure, got {res['status']}: {res.get('reason')}"]
    return []


def chk_step_limit(res, rows):
    if res["status"] != "stopped" or "step limit" not in res.get("reason", ""):
        return [f"expected loop guard stop, got {res['status']}: {res.get('reason')}"]
    return []


def chk_load_balance(res, rows):
    shares = load_from_jsonl(Path(res["out_dir"]) / "events.jsonl")
    worst = max(shares.values())
    return _ok(res) + ([f"agent load {shares} exceeds 40%"] if worst > 0.40 else [])


def chk_llm_fallback(res, rows):
    errs = _ok(res)
    ev = _events(res)
    if not any(e["kind"] == "llm_call" and e.get("status") == "failed" for e in ev):
        errs.append("no failed LLM call recorded")
    if not any(e["kind"] == "llm_call" and e.get("status") == "fallback" for e in ev):
        errs.append("no offline fallback recorded")
    return errs


def chk_data_cleaning(res, rows):
    errs = _ok(res)
    text = (Path(res["out_dir"]) / "report.md").read_text(encoding="utf-8")
    issues = json.dumps([e["payload"] for e in _messages(res, "load_data")], ensure_ascii=False)
    if "fixed" not in issues:
        errs.append("data agent reported no auto-fixes")
    if "Проблемы входных данных" not in text:
        errs.append("report misses the data-issue section")
    return errs


def chk_exports(res, rows):
    errs = _ok(res)
    out = Path(res["out_dir"])
    for f in ("timetable.xlsx", "timetable.csv", "timetable.html", "report.md", "events.jsonl", "state.sqlite"):
        if not (out / f).exists():
            errs.append(f"missing {f}")
    html = (out / "timetable.html").read_text(encoding="utf-8") if (out / "timetable.html").exists() else ""
    if "IT-2301" not in html:
        errs.append("HTML viewer has no group data")
    return errs


def chk_term2(res, rows):
    errs = _ok(res)
    if not any("Operating Systems" in r["discipline"] for r in rows):
        errs.append("term 2 (trimester 5) courses missing")
    return errs


IT = {"programs": ("IT",)}
SCENARIOS: list[Scenario] = [
    Scenario("S01", "Базовый прогон, программа IT (22 группы)", "0 жёстких нарушений, расписание экспортировано",
             settings=IT, check=chk_baseline),
    Scenario("S02", "«1 курс заканчивает до 17:00»", "у групп 1 курса нет пар с 17:00",
             ["1 курс заканчивает до 17:00"], IT, check=chk_year1_until_17),
    Scenario("S03", "Свободный день группы", "у IT-2301 нет пар в пятницу",
             ["У группы IT-2301 свободна пятница"], IT, check=chk_group_friday_free),
    Scenario("S04", "Аудитория на ремонте", "C1.1.334L не используется в понедельник",
             ["Аудитория C1.1.334L недоступна в понедельник"], IT, check=chk_room_monday),
    Scenario("S05", "Лимит часов в день", "не более 6 ак. часов в день у каждой группы",
             ["Не более 6 часов в день"], IT, check=chk_max_daily),
    Scenario("S06", "Мягкое пожелание", "«желательно» распознано как soft, расписание без нарушений",
             ["Желательно не ставить занятия до 9:00"], IT, check=chk_soft_constraint),
    Scenario("S07", "Нехватка ресурса → переговоры", "Negotiator выбирает уступки, итог без жёстких нарушений",
             ["Physical Education только утром"], {"programs": ("MT", "ITM"), "gym_capacity": 1},
             check=chk_negotiation),
    Scenario("S08", "Неизвестная аудитория в требовании", "ограничение отброшено с предупреждением, прогон ok",
             ["Аудитория X999 недоступна в среду"], IT, check=chk_unknown_entity),
    Scenario("S09", "Непонятное требование", "фраза помечена как непонятая, прогон ok",
             ["Сделайте расписание красивым и душевным"], IT, check=chk_unparsed),
    Scenario("S10", "Нет входного файла", "понятное сообщение об ошибке, без падения",
             settings={**IT, "data_path": ROOT / "data" / "missing.xlsx"}, check=chk_missing_file),
    Scenario("S11", "Защита от зацикливания", "остановка по лимиту шагов с объяснением",
             settings={**IT, "max_steps": 5}, check=chk_step_limit),
    Scenario("S12", "Баланс нагрузки агентов", "ни один агент не делает > 40% вызовов (по логам)",
             settings=IT, check=chk_load_balance),
    Scenario("S13", "Сбой LLM-провайдера", "ошибка LLM залогирована, офлайн-фолбэк, прогон ok",
             ["1 курс заканчивает до 18:00"], {**IT, "llm_provider": "claude", "llm_timeout": 3.0},
             env={"ANTHROPIC_API_KEY": "invalid-test-key", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"},
             check=chk_llm_fallback),
    Scenario("S14", "Грязные данные", "опечатки/кириллица исправлены, отражены в отчёте",
             settings=IT, check=chk_data_cleaning),
    Scenario("S15", "Экспорт результатов", "xlsx/csv/html/report/лог/SQLite созданы", settings=IT,
             check=chk_exports),
    Scenario("S16", "Второй триместр учебного года", "курсы триместра 5 (OS и др.) в расписании",
             settings={**IT, "term": 2}, check=chk_term2),
]


def run_scenario(sc: Scenario, out_root: Path, seconds: float = 10.0) -> tuple[bool, list[str], dict]:
    base = {"llm_provider": "offline", "solver_time_limit": seconds, "out_root": out_root}
    st = Settings().with_(**{**base, **sc.settings})
    old = {k: os.environ.get(k) for k in sc.env}
    os.environ.update(sc.env)
    try:
        res = run(st, sc.requirements, run_name=sc.id, echo=False)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    errs = sc.check(res, _rows(res))
    return not errs, errs, res


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out_root = ROOT / "runs" / f"scenarios-{time.strftime('%Y%m%d-%H%M%S')}"
    lines = ["# Результаты тестовых сценариев", "", f"Запуск: {time.strftime('%Y-%m-%d %H:%M')}", "",
             "| ID | Сценарий | Ожидаемый результат | Итог | Время, с | Детали |", "|---|---|---|---|---|---|"]
    passed = 0
    for sc in SCENARIOS:
        t0 = time.perf_counter()
        ok, errs, res = run_scenario(sc, out_root)
        passed += ok
        soft = res.get("soft") or {}
        detail = "; ".join(errs) if errs else (f"окна {soft.get('gap_hours_per_group')} ч/гр, "
                                                f"шагов {res.get('steps')}" if soft else res.get("reason", ""))
        lines.append(f"| {sc.id} | {sc.title} | {sc.expected} | {'✅' if ok else '❌'} | "
                     f"{time.perf_counter() - t0:.1f} | {detail} |")
        print(f"{sc.id} {'PASS' if ok else 'FAIL'} {sc.title} {errs}", flush=True)
    lines += ["", f"**Пройдено: {passed}/{len(SCENARIOS)}**"]
    out = ROOT / "docs" / "scenario_results.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed}/{len(SCENARIOS)} passed -> {out}")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
