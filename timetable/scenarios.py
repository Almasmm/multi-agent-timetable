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
    runner: Callable[[Path, float], tuple[dict, list[str]]] | None = None  # multi-run scenarios (S18, S19)


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


def _busiest_monday_room(rows: list[dict]) -> tuple[str, int]:
    from collections import Counter
    c = Counter(r["room"] for r in rows if r["day"] == 0 and r["room"] not in ("online", "UNASSIGNED"))
    return c.most_common(1)[0]


def run_warm_start(out_root: Path, seconds: float) -> tuple[dict, list[str]]:
    """Publish IT, close its busiest Monday room, re-generate warm (from the published version) and
    cold (from scratch): the warm run must move far fewer classes and keep the closure."""
    from .service.versions import diff_rows, read_rows, snapshot
    base = Settings().with_(programs=("IT",), llm_provider="offline", solver_time_limit=seconds, out_root=out_root)
    r0 = run(base, [], "S18-published", echo=False)
    if r0["status"] != "ok":
        return r0, [f"baseline status {r0['status']}"]
    rows0 = read_rows(out_root / "S18-published" / "timetable.csv")
    room, n = _busiest_monday_room(rows0)
    snapshot(out_root / "S18-published", out_root / "S18-v1")
    req = [f"Аудитория {room} недоступна в понедельник"]
    out, errs = {}, []
    for name, kw in [("warm", {"warm_start": out_root / "S18-v1" / "starts.json"}), ("cold", {})]:
        r = run(base.with_(**kw), req, f"S18-{name}", echo=False)
        rows = read_rows(out_root / f"S18-{name}" / "timetable.csv")
        d = diff_rows(rows0, rows)
        used = sum(1 for x in rows if x["room"] == room and x["day"] == 0)
        errs += [f"{name}: {e}" for e in _ok(r)] + ([f"{name}: {room} still used {used} h on Monday"] if used else [])
        out[name] = {**d, "groups": None, "res": r}
    w, c = out["warm"], out["cold"]
    if w["hours_moved"] + w["room_changes"] >= c["hours_moved"] + c["room_changes"]:
        errs.append(f"warm start changed {w['hours_moved']}+{w['room_changes']} h, cold {c['hours_moved']}+{c['room_changes']} h")
    if w["hours_moved"] > 0.05 * w["total_hours"]:
        errs.append(f"warm start moved {w['hours_moved']} of {w['total_hours']} h (> 5 %)")
    res = {**w["res"], "detail": f"{room} закрыта в пн ({n} ч): тёплый старт — перенесено {w['hours_moved']} ч, смена ауд. "
                                  f"{w['room_changes']} ч, групп {w['groups_affected']}; с нуля — {c['hours_moved']} ч, "
                                  f"{c['room_changes']} ч, групп {c['groups_affected']} из {len({r['group'] for r in rows0})}"}
    return res, errs


def run_autopilot(out_root: Path, seconds: float) -> tuple[dict, list[str]]:
    """The 24/7 service with the real pipeline: first version -> staff request -> warm re-generation
    -> publication with a change notice; improvement attempts are disabled to keep the test short."""
    from .service.autopilot import Autopilot
    from .service.versions import read_rows
    from .web.runs import RunManager
    svc = out_root / "S19"
    old = os.environ.get("TT_OUT")
    os.environ["TT_OUT"] = str(svc)  # the run manager resolves run folders from TT_OUT
    ap = Autopilot(RunManager(), Settings().with_(out_root=svc))
    errs: list[str] = []
    try:
        ap.store.set("profile", {"programs": ["IT"], "solver_seconds": seconds, "llm": "offline", "max_improve_attempts": 0})

        def until_idle() -> list[dict]:
            seen, deadline = [], time.time() + 300
            while time.time() < deadline:
                r = ap.tick()
                seen.append(r)
                if r["state"] in ("idle", "error"):
                    break
                time.sleep(1 if r["state"] == "busy" else 0)
            return seen

        first = until_idle()
        v1 = ap.store.current_version()
        if not v1:
            return {"status": "failed"}, [f"no first version: {first[-1]}"]
        room, n = _busiest_monday_room(read_rows(ap.store.version_dir(v1["id"]) / "timetable.csv"))
        rid = ap.store.add_request(f"Аудитория {room} недоступна в понедельник", "Диспетчер")
        bad = ap.store.add_request("У группы XX-0000 свободна пятница", "Деканат")
        second = until_idle()
        v2 = ap.store.current_version()
        states = [r["state"] for r in first + second]
        if not v2 or v2["id"] != v1["id"] + 1:
            errs.append(f"no second version (states {states})")
        else:
            rows = read_rows(ap.store.version_dir(v2["id"]) / "timetable.csv")
            if any(x["room"] == room and x["day"] == 0 for x in rows):
                errs.append(f"{room} still used on Monday in v{v2['id']}")
            if v2["diff"]["hours_moved"] > 0.05 * v2["diff"]["total_hours"]:
                errs.append(f"v{v2['id']} moved {v2['diff']['hours_moved']} h")
            if not v2["announcement"]:
                errs.append("no change notice")
        if ap.store.request(rid)["status"] != "applied":
            errs.append(f"request status {ap.store.request(rid)['status']}")
        if ap.store.request(bad)["status"] != "rejected":
            errs.append("unknown group was not rejected")
        if states[-1] != "idle":
            errs.append(f"service did not settle: {states[-1]}")
        d = (v2 or {}).get("diff") or {}
        res = {"status": "ok" if not errs else "failed", "detail": f"такты: {len(states)}; v{v1['id']} → v{(v2 or {}).get('id')}: "
               f"перенесено {d.get('hours_moved')} ч, смена ауд. {d.get('room_changes')} ч; заявка «{room} в пн» применена, "
               f"неизвестная группа отклонена"}
        return res, errs
    finally:
        ap.close()
        if old is None:
            os.environ.pop("TT_OUT", None)
        else:
            os.environ["TT_OUT"] = old


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
    Scenario("S17", "Сбой OpenAI-провайдера", "ошибка OpenAI залогирована, офлайн-фолбэк, прогон ok",
             ["1 курс заканчивает до 18:00"], {**IT, "llm_provider": "openai", "llm_timeout": 3.0},
             env={"OPENAI_API_KEY": "invalid-test-key", "OPENAI_BASE_URL": "http://127.0.0.1:9"},
             check=chk_llm_fallback),
    Scenario("S18", "Тёплый старт: аудитория закрыта после публикации",
             "закрытая аудитория не используется; переносов намного меньше, чем при генерации с нуля (≤ 5 % часов)",
             runner=run_warm_start),
    Scenario("S19", "Служба 24/7: заявка → перегенерация → публикация",
             "автопилот публикует v1, принимает заявку, отклоняет неизвестную группу, публикует v2 с малым числом переносов",
             runner=run_autopilot),
]


def run_scenario(sc: Scenario, out_root: Path, seconds: float = 10.0) -> tuple[bool, list[str], dict]:
    if sc.runner is not None:
        res, errs = sc.runner(out_root, seconds)
        return not errs, errs, res
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
        detail = "; ".join(errs) if errs else res.get("detail") or (f"окна {soft.get('gap_hours_per_group')} ч/гр, "
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
