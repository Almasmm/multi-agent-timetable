"""Chat with every agent: the dispatcher gives instructions to one agent at a time.

Each agent keeps its single role from the pipeline / service and gets at most 5 *chat tools* -
small functions over the live platform: the published timetable and the draft editor, the
university rules (hard / soft), the request inbox and the autopilot. The model (Claude by default)
decides which tool to call; :meth:`AgentChat.send` runs the tool-use loop, feeds the results back
and stops after ``MAX_ROUNDS`` model calls (loop guard). Changes of the timetable go to the draft
(``editor.py``) and are published only through the Publisher, so the 0-violation guarantee holds.

Without an LLM (no key, network error) every agent answers with deterministic offline rules:
simple commands ("перенеси IT-2301 Пн 08:00 на Ср 11:00", "проверь", a rule sentence) still work.
Every LLM and tool call is logged to ``runs/_service/chat-YYYYMMDD.jsonl``.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from collections import defaultdict
from typing import Any, Callable

from ..config import Settings
from ..core.agent import MAX_TOOLS_PER_AGENT
from ..core.llm import ClaudeLLM, resolve_provider
from ..core.runlog import RunLogger
from ..domain.constraints import split_mode, with_mode
from ..domain.preview import describe_rules
from . import datasets
from .agents import describe_spec, find_dataset
from .editor import DAY_RU, KIND_RU, ScheduleEditor
from .store import REQUEST_ACTIVE, REQUEST_OPEN, ServiceStore

MAX_ROUNDS = 8           # model calls per user message (loop guard; the last one must answer)
HISTORY_TURNS = 16       # previous messages replayed to the model (text only)
RESULT_CHARS = 14000     # tool result size limit sent back to the model
CHAT_EFFORT = os.getenv("TT_CHAT_EFFORT", "low")

DAY_KEYS = [("пн", "пон", "mon"), ("вт", "вто", "tue"), ("ср", "сре", "wed"), ("чт", "чет", "thu"),
            ("пт", "пят", "fri"), ("сб", "суб", "sat")]


def parse_day(x: Any) -> int:
    if isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit()):
        d = int(x)
        if 0 <= d <= 5:
            return d
    t = str(x).strip().lower()
    for i, keys in enumerate(DAY_KEYS):
        if any(t.startswith(k) for k in keys):
            return i
    raise ValueError(f"не понял день недели «{x}» (пример: Пн, Wed)")


def parse_time(x: Any, first_hour: int = 8) -> int:
    """"09:00" / "9" / 9 (clock hour) -> hour index of the day."""
    m = re.match(r"^\s*(\d{1,2})(?::\d{2})?\s*$", str(x))
    if not m or not first_hour <= int(m.group(1)) < first_hour + 12:
        raise ValueError(f"не понял время «{x}» (пример: 09:00, от 08:00 до 19:00)")
    return int(m.group(1)) - first_hour


# ------------------------------------------------------------------------------------------
# Tools over the live platform
# ------------------------------------------------------------------------------------------
class ChatTools:
    """Every chat tool: name -> (function, JSON schema of the input, description)."""

    def __init__(self, store: ServiceStore, editor: ScheduleEditor, pilot: Any = None):
        self.store, self.editor, self.pilot = store, editor, pilot
        S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
        group, day = {"type": "string", "description": "group code, e.g. IT-2301"}, {"type": "string", "description": "day: Пн..Сб or Mon..Sat"}
        tm = {"type": "string", "description": "start time HH:MM, e.g. 09:00"}
        self.catalog: dict[str, tuple[Callable[..., Any], dict, str]] = {
            "overview": (self.overview, S({}), "Current published version, draft, autopilot, inbox and rule counts."),
            "data_summary": (self.data_summary, S({}), "Programmes, groups, rooms and data-quality issues of the dataset."),
            "group_week": (self.group_week, S({"group": group}, ["group"]),
                           "Week of one group (draft applied): every class with day, time, room, stream; gaps."),
            "room_week": (self.room_week, S({"room": {"type": "string"}}, ["room"]), "Week of one room and its load."),
            "free_rooms": (self.free_rooms, S({"day": day, "time": tm, "hours": {"type": "integer"},
                                               "min_capacity": {"type": "integer"},
                                               "kind": {"type": "string", "enum": ["lecture", "practice", "lab"]}},
                                              ["day", "time"]), "Rooms free at a day/time that fit a class."),
            "move_options": (self.move_options, S({"group": group, "day": day, "time": tm}, ["group", "day", "time"]),
                             "For the class a group has at day/time: every feasible new slot with the room and the "
                             "change of the penalty score (negative = better), plus why the best slots are blocked."),
            "suggest_moves": (self.suggest_moves, S({"group": group, "day": {"type": "string", "description": "optional: only classes of this day"}}, ["group"]),
                              "Best single moves of the group's classes that lower the penalty score (fewer gaps, "
                              "no late hours), checked against every hard rule. Use it to improve a group's week."),
            "move_session": (self.move_session, S({"group": group, "day": day, "time": tm, "to_day": day, "to_time": tm,
                                                   "room": {"type": "string", "description": "optional room code"}},
                                                  ["group", "day", "time", "to_day", "to_time"]),
                             "Move the class (whole stream) to a new slot in the DRAFT. Checked by the local rules and "
                             "the independent validator; refused with the reason if it breaks a hard rule."),
            "change_room": (self.change_room, S({"group": group, "day": day, "time": tm, "room": {"type": "string"}},
                                                ["group", "day", "time", "room"]), "Give a class another room (draft)."),
            "undo_move": (self.undo_move, S({}), "Undo the last change of the draft."),
            "draft_status": (self.draft_status, S({}), "Changes collected in the draft and their check result."),
            "publish_draft": (self.publish_draft, S({"note": {"type": "string"}}),
                              "Publish the draft as a new version (only with 0 hard violations)."),
            "discard_draft": (self.discard_draft, S({}), "Throw the draft away."),
            "validate": (self.validate, S({}), "Independent check of the timetable (draft applied): hard violations, soft metrics."),
            "quality": (self.quality, S({}), "Soft quality: gaps, late hours, Saturday; the groups with most gaps."),
            "list_rules": (self.list_rules, S({}), "Numbered university rules (hard/soft, weight, how they are understood) "
                                                   "and accepted staff requests."),
            "preview_rule": (self.preview_rule, S({"text": {"type": "string"}}, ["text"]),
                             "How a rule sentence would be understood (without saving)."),
            "add_rule": (self.add_rule, S({"text": {"type": "string", "description": "the rule as a Russian/English sentence"},
                                           "mode": {"type": "string", "enum": ["auto", "hard", "soft"]},
                                           "weight": {"type": "integer", "description": "soft penalty per hour 1..20"}},
                                          ["text"]),
                         "Add a standing rule; the Watcher re-generates the timetable with a warm start."),
            "update_rule": (self.update_rule, S({"number": {"type": "integer"}, "mode": {"type": "string", "enum": ["auto", "hard", "soft"]},
                                                 "weight": {"type": "integer"}}, ["number", "mode"]),
                            "Make rule N hard or soft (soft works for time windows) and set its weight."),
            "remove_rule": (self.remove_rule, S({"number": {"type": "integer"}}, ["number"]), "Delete rule N."),
            "regenerate": (self.regenerate, S({"kind": {"type": "string", "enum": ["regenerate", "improve"]},
                                               "reason": {"type": "string"}}, ["kind"]),
                           "Ask the Watcher for a new generation now (warm start) or an improvement search."),
            "jobs_history": (self.jobs_history, S({}), "Recent generation jobs and published versions."),
            "submit_request": (self.submit_request, S({"text": {"type": "string"}}, ["text"]),
                               "File a change request in the inbox; Intake triages it."),
            "requests_inbox": (self.requests_inbox, S({}), "Open and accepted staff requests."),
            "set_autopilot": (self.set_autopilot, S({"on": {"type": "boolean"}}, ["on"]), "Switch the 24/7 autopilot on or off."),
        }

    # --- helpers ---
    def ws(self):
        return self.editor.workspace()[0]

    def _slot(self, day: Any, time_: Any) -> tuple[int, int]:
        return parse_day(day), parse_time(time_, self.editor.settings.first_hour)

    def _group(self, group: str) -> str:
        g = str(group).strip().upper()
        if g not in self.ws().group_at:
            raise ValueError(f"группы {group} нет в опубликованном расписании")
        return g

    def _session_line(self, ws, sid: str) -> dict:
        d = ws.describe(sid)
        end = ws.labels.get(d["hour"] + d["duration"] - 1, "").split("-")[-1]
        return {"day": DAY_RU[d["day"]], "time": f"{d['time']}-{end}", "course": d["course"],
                "kind": KIND_RU.get(d["kind"], d["kind"]), "room": d["room"], "groups": d["groups"]}

    def _tick_soon(self) -> None:
        if self.pilot is not None:
            threading.Thread(target=self.pilot.tick, name="chat-tick", daemon=True).start()

    # --- read tools ---
    def overview(self) -> dict:
        cur = self.store.current_version()
        st = self.editor.state() if cur else {"count": 0}
        m = (cur or {}).get("metrics") or {}
        job = self.store.active_job()
        return {"university": self.store.profile()["university"],
                "version": {"id": cur["id"], "published": time.strftime("%d.%m %H:%M", time.localtime(cur["ts"])),
                            "kind": m.get("kind"), "score": cur["score"], "gap_hours_per_group": m.get("gap_hours_per_group"),
                            "late_hours_18_plus": m.get("late_hours_18_plus"), "term": m.get("term")} if cur else None,
                "draft": {"changes": st["count"], "hard": st.get("hard")},
                "autopilot": {"enabled": bool(self.store.get("enabled", False)),
                              "job": {k: job[k] for k in ("id", "kind", "reason", "status")} if job else None},
                "requests_open": len(self.store.requests(REQUEST_OPEN)), "rules": len(self.store.profile()["requirements"])}

    def data_summary(self) -> dict:
        ws = self.ws()
        by_prog: dict[str, set] = defaultdict(set)
        for s in ws.sessions:
            by_prog[s.program].update(s.groups)
        types = defaultdict(int)
        for r in ws.rooms:
            types[{"L": "лекционные", "P": "практические", "K": "компьютерные"}.get(r.rtype, r.rtype)] += 1
        levels = defaultdict(int)
        for i in ws.issues:
            levels[i["level"]] += 1
        return {"dataset": self.store.profile()["dataset"], "programs": {p: len(g) for p, g in sorted(by_prog.items())},
                "groups": sum(len(g) for g in by_prog.values()), "rooms": dict(types), "sessions": len(ws.sessions),
                "academic_hours": sum(s.duration * len(s.groups) for s in ws.sessions),
                "issues": dict(levels), "issue_examples": [f"[{i['level']}] {i['where']}: {i['issue']}" for i in ws.issues[:6]]}

    def group_week(self, group: str) -> dict:
        ws, g = self.ws(), self._group(group)
        sids = sorted(set(ws.group_at[g].values()), key=lambda s: ws.starts[s])
        days = defaultdict(set)
        for u in ws.group_at[g]:
            days[u // ws.H].add(u % ws.H)
        return {"group": g, "classes": [self._session_line(ws, s) for s in sids],
                "hours": len(ws.group_at[g]), "gaps": sum(ws._gaps(h) for h in days.values())}

    def room_week(self, room: str) -> dict:
        ws = self.ws()
        code = next((r.code for r in ws.rooms if r.code.lower() == str(room).strip().lower()), None)
        if code is None:
            raise ValueError(f"аудитории {room} нет в данных")
        sids = sorted(set(ws.room_at[code].values()), key=lambda s: ws.starts[s])
        r = ws.room_by[code]
        return {"room": code, "capacity": r.capacity, "type": r.rtype, "classes": [self._session_line(ws, s) for s in sids],
                "booked_hours": len(ws.room_at[code]), "load": round(len(ws.room_at[code]) / (len(ws.days) * ws.H), 2)}

    def free_rooms(self, day: Any, time: Any, hours: int = 1, min_capacity: int = 0, kind: str | None = None) -> dict:
        ws = self.ws()
        d, h = self._slot(day, time)
        span = range(d * ws.H + h, d * ws.H + h + max(1, int(hours or 1)))
        want = {"lecture": "L", "practice": "P", "lab": "K"}.get(kind or "")
        free = [r for r in ws.rooms if r.capacity >= int(min_capacity or 0) and (not want or r.rtype == want)
                and not any(u in ws.room_at[r.code] for u in span) and not set(span) & ws.blocked.get(r.code, set())]
        free.sort(key=lambda r: r.capacity)
        return {"day": DAY_RU[d], "time": ws.time(h), "free": len(free),
                "rooms": [{"room": r.code, "capacity": r.capacity, "type": r.rtype} for r in free[:20]]}

    def move_options(self, group: str, day: Any, time: Any) -> dict:
        ws, g = self.ws(), self._group(group)
        d, h = self._slot(day, time)
        sid = ws.locate(g, None, d, h)
        opts = ws.options(sid)
        ok = sorted((o for o in opts if o["ok"] and o["start"] != ws.starts[sid]), key=lambda o: (o["delta"], o["start"]))
        reasons = defaultdict(int)
        for o in opts:
            if not o["ok"]:
                reasons[o["reason"]] += 1
        return {"session": self._session_line(ws, sid), "feasible": len(ok),
                "best": [{"day": DAY_RU[o["day"]], "time": ws.time(o["hour"]), "room": o["room"], "delta": o["delta"]}
                         for o in ok[:10]],
                "blocked_by": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:6])}

    def suggest_moves(self, group: str, day: Any = None) -> dict:
        ws, g = self.ws(), self._group(group)
        only = parse_day(day) if day not in (None, "") else None
        best = []
        for sid in set(ws.group_at[g].values()):
            if only is not None and ws.starts[sid] // ws.H != only:
                continue
            for o in ws.options(sid):
                if o["ok"] and o["start"] != ws.starts[sid] and o["delta"] < 0:
                    cur = ws.describe(sid)
                    best.append({"course": cur["course"], "kind": KIND_RU.get(cur["kind"], cur["kind"]), "groups": cur["groups"],
                                 "from_day": DAY_RU[cur["day"]], "from_time": cur["time"], "to_day": DAY_RU[o["day"]],
                                 "to_time": ws.time(o["hour"]), "room": o["room"], "delta": o["delta"]})
        best.sort(key=lambda x: (x["delta"], len(x["groups"])))
        return {"group": g, "improving_moves": len(best), "best": best[:10]}

    # --- draft tools ---
    def move_session(self, group: str, day: Any, time: Any, to_day: Any, to_time: Any, room: str | None = None) -> dict:
        g = self._group(group)
        d, h = self._slot(day, time)
        td, th = self._slot(to_day, to_time)
        res = self.editor.move(g, None, d, h, td, th, room or None)
        return {"moved": res["move"]["label"], "delta": res["move"]["delta"], "draft_changes": res["count"],
                "hard_violations": res["hard"], "note": "правка в черновике; опубликует Публикатор"}

    def change_room(self, group: str, day: Any, time: Any, room: str) -> dict:
        return self.move_session(group, day, time, day, time, room)

    def undo_move(self) -> dict:
        res = self.editor.undo()
        return {"undone": res["undone"]["label"], "draft_changes": res["count"]}

    def draft_status(self) -> dict:
        st = self.editor.state()
        return {"base_version": st["base"], "stale": st["stale"], "changes": [m["label"] for m in st["moves"]],
                "hard_violations": st["hard"], "score": st["score"]}

    def publish_draft(self, note: str = "") -> dict:
        res = self.editor.publish(note, author="чат")
        return {"version": res["version"], "score": res["score"], "groups_affected": res["diff"]["groups_affected"],
                "announcement": res["announcement"]}

    def discard_draft(self) -> dict:
        self.editor.discard()
        return {"discarded": True}

    def validate(self) -> dict:
        val = self.ws().validate()
        return {"hard_violations": val["hard_violations"], "by_type": val["by_type"],
                "examples": [v["detail"] for v in val["violations"][:6]], "soft": val["soft"]}

    def quality(self) -> dict:
        ws = self.ws()
        val = ws.validate()
        per = {}
        for g, slots in ws.group_at.items():
            days = defaultdict(set)
            for u in slots:
                days[u // ws.H].add(u % ws.H)
            per[g] = sum(ws._gaps(h) for h in days.values())
        worst = sorted(per.items(), key=lambda kv: -kv[1])[:10]
        return {"soft": val["soft"], "groups_with_most_gaps": [{"group": g, "gap_hours": n} for g, n in worst if n]}

    # --- rules ---
    def _rules(self) -> list[str]:
        return list(self.store.profile()["requirements"])

    def _save_rules(self, rules: list[str]) -> None:
        self.store.set("profile", {**self.store.profile(), "requirements": rules})

    def list_rules(self) -> dict:
        path = find_dataset(self.store.profile()["dataset"])
        rules = [{"number": i + 1, "text": r["clean"], "mode": r["mode"] or (
                  "soft" if r["spec"] and not r["spec"]["hard"] else "hard" if r["spec"] else "auto"),
                  "weight": r["weight"] or (r["spec"] or {}).get("weight"),
                  "understood": describe_spec(r["spec"]) if r["spec"] else "офлайн-правила не распознали (разберёт LLM при генерации)",
                  "unknown": r["unknown"]}
                 for i, r in enumerate(describe_rules(path, self._rules()))]
        accepted = [{"request": r["id"], "text": r["text"], "status": r["status"]} for r in self.store.requests(REQUEST_ACTIVE)]
        return {"rules": rules, "accepted_requests": accepted,
                "builtin_hard": ["одна пара у группы за раз", "аудитория не занята дважды", "вместимость и тип аудитории",
                                 "не более 8 ак. ч в день", "блоки одной дисциплины в разные дни"],
                "builtin_soft": ["окна в дне группы", "часы после 18:00", "суббота", "лекция раньше практики"]}

    def preview_rule(self, text: str) -> dict:
        r = describe_rules(find_dataset(self.store.profile()["dataset"]), [text])[0]
        return {"understood": describe_spec(r["spec"]) if r["spec"] else None, "hard": (r["spec"] or {}).get("hard"),
                "unknown": r["unknown"], "softable": r["softable"]}

    def add_rule(self, text: str, mode: str = "auto", weight: int | None = None) -> dict:
        clean = split_mode(text)[0]
        if len(clean) < 3:
            raise ValueError("пустое правило")
        r = self.preview_rule(clean)
        if r["unknown"]:
            raise ValueError(f"в данных вуза нет: {', '.join(r['unknown'])}")
        rules = self._rules()
        if any(split_mode(x)[0].lower() == clean.lower() for x in rules):
            raise ValueError("такое правило уже есть")
        rules.append(with_mode(clean, None if mode in (None, "auto") else mode == "hard", weight))
        self._save_rules(rules)
        return {"number": len(rules), "rule": rules[-1], "understood": r["understood"] or "разберёт LLM при генерации",
                "note": "Наблюдатель перегенерирует расписание с тёплым стартом на ближайшем такте автопилота"}

    def _rule_index(self, number: int) -> tuple[list[str], int]:
        rules = self._rules()
        i = int(number) - 1
        if not 0 <= i < len(rules):
            raise ValueError(f"правила №{number} нет (всего {len(rules)})")
        return rules, i

    def update_rule(self, number: int, mode: str, weight: int | None = None) -> dict:
        rules, i = self._rule_index(number)
        rules[i] = with_mode(rules[i], None if mode == "auto" else mode == "hard", weight)
        self._save_rules(rules)
        return {"number": number, "rule": rules[i]}

    def remove_rule(self, number: int) -> dict:
        rules, i = self._rule_index(number)
        removed = rules.pop(i)
        self._save_rules(rules)
        return {"removed": split_mode(removed)[0], "left": len(rules)}

    # --- service ---
    def regenerate(self, kind: str = "regenerate", reason: str = "") -> dict:
        if kind not in ("regenerate", "improve"):
            raise ValueError("kind: regenerate или improve")
        if self.store.active_job():
            return {"queued": False, "note": "уже идёт задание - дождитесь его окончания"}
        self.store.set("force_job", {"kind": kind, "reason": reason or "без комментария"})
        self._tick_soon()
        if self.editor.settings.data_gate:  # the Watcher keeps the job until the university data is clean
            try:
                ready = datasets.readiness(find_dataset(self.store.profile()["dataset"]))
            except FileNotFoundError:
                ready = {"ready": False, "problems": 1}
            if not ready["ready"]:
                return {"queued": True, "kind": kind, "waits_for_data": ready["problems"],
                        "note": f"Задание в очереди, но ждёт данные: проблем — {ready['problems']}. Исправьте их в «Данные вуза» "
                                "— тогда Наблюдатель запустит перегенерацию."}
        return {"queued": True, "kind": kind, "note": "Наблюдатель предложит задание, Автопилот запустит конвейер"}

    def jobs_history(self) -> dict:
        jobs = [{"id": j["id"], "kind": j["kind"], "status": j["status"], "reason": j["reason"],
                 "when": time.strftime("%d.%m %H:%M", time.localtime(j["ts"]))} for j in self.store.jobs(6)]
        vers = [{"id": v["id"], "kind": (v["metrics"] or {}).get("kind"), "score": v["score"],
                 "when": time.strftime("%d.%m %H:%M", time.localtime(v["ts"])),
                 "moved_hours": (v["diff"] or {}).get("hours_moved")} for v in self.store.versions(5)]
        return {"jobs": jobs, "versions": vers}

    def submit_request(self, text: str) -> dict:
        rid = self.store.add_request(text.strip(), "чат")
        self._tick_soon()
        return {"request": rid, "status": "new", "note": "Приёмная разберёт заявку на ближайшем такте"}

    def requests_inbox(self) -> dict:
        return {"requests": [{"id": r["id"], "text": r["text"], "status": r["status"],
                              "verdict": (r["verdict"] or {}).get("reason")} for r in self.store.requests()[:15]]}

    def set_autopilot(self, on: bool) -> dict:
        if self.pilot is None:
            raise ValueError("автопилот недоступен в этом процессе")
        (self.pilot.start if on else self.pilot.stop)()
        return {"enabled": bool(on)}


# ------------------------------------------------------------------------------------------
# Agents of the chat
# ------------------------------------------------------------------------------------------
CHAT_AGENTS: dict[str, dict] = {
    "orchestrator": {"title": "Оркестратор", "tools": ["overview", "jobs_history", "regenerate", "submit_request"],
                     "role": "plans and routes the work of the other agents, starts generations; does no domain work itself",
                     "hello": "Я планирую и маршрутизирую работу агентов. Скажите, что нужно, — подскажу, кто это сделает, или запущу генерацию."},
    "data": {"title": "Данные", "tools": ["data_summary", "group_week", "room_week", "overview"],
             "role": "knows the university workbook: programmes, groups, rooms, data-quality issues",
             "hello": "Я отвечаю за данные вуза: программы, группы, аудитории и ошибки в Excel."},
    "constraints": {"title": "Ограничения", "tools": ["list_rules", "preview_rule", "add_rule", "update_rule", "remove_rule"],
                    "role": "turns natural-language requirements into hard and soft rules and manages the rule list",
                    "hello": "Сформулируйте правило обычными словами — например «1 курс заканчивает до 17:00» — я добавлю его как жёсткое или мягкое."},
    "genetic": {"title": "Генетик", "tools": ["quality", "suggest_moves", "regenerate", "overview"],
                "role": "evolves the first generation of the timetable (genetic algorithm) and searches improvements",
                "hello": "Я ищу улучшения расписания генетическим алгоритмом. Могу показать качество и запустить поиск улучшения."},
    "scheduler": {"title": "Планировщик", "tools": ["group_week", "suggest_moves", "move_options", "move_session", "undo_move"],
                  "role": "places classes in time (CP-SAT); in the chat moves single classes on request",
                  "hello": "Я расставляю занятия по времени. Попросите перенести пару — найду допустимые слоты и проверю правила."},
    "rooms": {"title": "Аудитории", "tools": ["room_week", "free_rooms", "change_room", "undo_move"],
              "role": "allocates the room fund and assigns concrete rooms",
              "hello": "Я назначаю аудитории. Могу найти свободную аудиторию или пересадить занятие."},
    "validator": {"title": "Критик", "tools": ["validate", "quality", "move_options", "group_week"],
                  "role": "independent checker: finds hard violations and measures soft quality",
                  "hello": "Я независимо проверяю расписание: жёсткие нарушения и мягкие критерии качества."},
    "negotiator": {"title": "Переговорщик", "tools": ["list_rules", "update_rule", "move_options", "move_session", "validate"],
                   "role": "resolves conflicts: relaxes rules (hard -> soft) or moves classes when requirements collide",
                   "hello": "Я разрешаю конфликты: ослабляю правила или переношу занятия, когда требования мешают друг другу."},
    "reporter": {"title": "Отчётность", "tools": ["overview", "quality", "jobs_history", "draft_status"],
                 "role": "reports what happened: versions, quality numbers, changes",
                 "hello": "Я готовлю сводки: что опубликовано, какое качество, что поменялось."},
    "autopilot": {"title": "Автопилот", "tools": ["overview", "jobs_history", "regenerate", "set_autopilot"],
                  "role": "24/7 service orchestrator: keeps the published timetable up to date",
                  "hello": "Я слежу за расписанием круглосуточно. Могу включить или выключить службу и запустить перегенерацию."},
    "intake": {"title": "Приёмная", "tools": ["submit_request", "requests_inbox", "preview_rule"],
               "role": "receives change requests from staff and triages them",
               "hello": "Передайте заявку сотрудника — например «Аудитория C1.3.361 недоступна в среду» — я зарегистрирую и разберу её."},
    "watcher": {"title": "Наблюдатель", "tools": ["overview", "jobs_history", "regenerate", "data_summary"],
                "role": "watches the data file and the requirements and proposes the next job",
                "hello": "Я наблюдаю за данными и заявками и решаю, когда перегенерировать или улучшать расписание."},
    "publisher": {"title": "Публикатор", "tools": ["draft_status", "publish_draft", "discard_draft", "validate", "jobs_history"],
                  "role": "release gate: publishes checked timetables (including the dispatcher's draft) with a notice",
                  "hello": "Я выпускаю версии. Покажу черновик ручных правок и опубликую его, если нет нарушений."},
}
for _name, _a in CHAT_AGENTS.items():  # the same limit as the pipeline agents (ТЗ: at most 5 tools)
    assert len(_a["tools"]) <= MAX_TOOLS_PER_AGENT, f"{_name}: too many chat tools"

SYSTEM = """You are the agent «{title}» of a multi-agent university timetabling platform ({university}).
Your single role: {role}.
You talk with the timetable dispatcher, who gives you instructions. Rules:
- Answer in Russian, briefly (1-6 sentences or a short list). Plain text with simple markdown (**bold**, lists).
- Use your tools for every fact and every change. Never invent numbers, groups, rooms or results.
- Times are academic hours 08:00-19:50 (50 min, pairs = 2 hours); days Пн..Сб. Pass times as HH:MM.
- Changes of classes go to a draft. Say so and remind that the Publisher (agent «Публикатор» or the
  «Опубликовать» button) releases it as a new version only with 0 hard violations.
- Rules: hard = must never be broken, soft = penalty, the solver avoids it when it can. Soft works for time windows.
- If a request belongs to another agent, say which one in one sentence ({others}).
- If a tool refuses (an error), explain the reason in plain words and offer the closest alternative."""


class AgentChat:
    """Conversation store + tool-use loop for every chat agent."""

    def __init__(self, store: ServiceStore, editor: ScheduleEditor, pilot: Any = None, settings: Settings | None = None):
        self.store, self.settings = store, settings or editor.settings
        self.tools = ChatTools(store, editor, pilot)
        self._llm: tuple[str, Any] | None = None

    # --- provider ---
    def provider(self) -> dict:
        want = self.store.profile().get("llm") or self.settings.llm_provider
        resolved, reason = resolve_provider(want)
        if resolved == "claude":
            return {"name": "claude", "model": self.settings.llm_model, "label": f"Claude · {self.settings.llm_model}"}
        why = reason or ("чат с инструментами работает на Claude" if resolved == "openai" else "")
        return {"name": "offline", "model": None, "label": "офлайн-правила", "reason": why}

    def _claude(self) -> ClaudeLLM:
        if self._llm is None or self._llm[0] != self.settings.llm_model:
            self._llm = (self.settings.llm_model, ClaudeLLM(self.settings.llm_model, self.settings.llm_timeout))
        return self._llm[1]

    def _logger(self) -> RunLogger:
        root = self.store.root
        return RunLogger(root / f"chat-{time.strftime('%Y%m%d')}.jsonl", "chat", echo=False)

    # --- public API ---
    def agents(self) -> list[dict]:
        last = self.store.chat_last()
        return [{"name": n, "title": a["title"], "role": a["role"], "hello": a["hello"], "tools": a["tools"],
                 "last": last.get(n)} for n, a in CHAT_AGENTS.items()]

    def history(self, agent: str) -> list[dict]:
        self._spec(agent)
        return self.store.chat_messages(agent)

    def clear(self, agent: str) -> None:
        self._spec(agent)
        self.store.clear_chat(agent)

    def _spec(self, agent: str) -> dict:
        if agent not in CHAT_AGENTS:
            raise FileNotFoundError(f"агента «{agent}» нет")
        return CHAT_AGENTS[agent]

    def send(self, agent: str, text: str) -> dict:
        self._spec(agent)
        text = text.strip()
        if not text:
            raise ValueError("пустое сообщение")
        history = self.store.chat_messages(agent)[-HISTORY_TURNS:]
        self.store.add_chat(agent, "user", text)
        reply, actions, prov = self.run_agent(agent, text, history)
        msg = self.store.add_chat(agent, "assistant", reply, actions, prov["label"])
        return {"message": msg, "provider": prov}

    def run_agent(self, agent: str, text: str, history: list[dict] | None = None, tools: list[str] | None = None,
                  offline=None) -> tuple[str, list[dict], dict]:
        """One turn of an agent with its tools (Claude, or the offline rules): reply, tool actions, provider.
        ``tools`` narrows the agent's tool set; ``offline`` replaces the offline rules (used by proposals)."""
        spec = dict(self._spec(agent))
        if tools is not None:
            spec["tools"] = [t for t in spec["tools"] if t in tools]
        logger = self._logger()
        prov = self.provider()
        actions: list[dict] = []
        fallback = offline or (lambda: self._offline(agent, spec, text, actions, logger))
        try:
            if prov["name"] == "claude":
                try:
                    reply = self._claude_loop(agent, spec, history or [], text, actions, logger)
                except Exception as exc:  # degrade, never break the conversation
                    logger.log("llm_call", agent, name="chat", provider="claude", status="failed",
                               error=f"{type(exc).__name__}: {exc}")
                    reply = fallback()
                    reply += f"\n\n_Claude недоступен ({type(exc).__name__}) — ответил по офлайн-правилам._"
                    prov = {**prov, "name": "offline", "label": "офлайн-правила (сбой Claude)"}
            else:
                reply = fallback()
        finally:
            logger.close()
        return reply, actions, prov

    # --- tool execution ---
    def _call(self, agent: str, name: str, args: dict, logger: RunLogger) -> tuple[Any, bool]:
        fn = self.tools.catalog[name][0]
        t0 = time.perf_counter()
        try:
            res, ok = fn(**args), True
        except (ValueError, KeyError, RuntimeError, FileNotFoundError, TypeError) as exc:
            res, ok = {"error": str(exc).strip("'\"")}, False
        except Exception as exc:  # unexpected: logged with a trace, reported to the model as an error
            logger.log("error", agent, msg=f"{name}: {exc}", trace=traceback.format_exc(limit=4))
            res, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
        logger.log("tool_call", agent, name=name, status="ok" if ok else "error", attempt=1,
                   duration_ms=round((time.perf_counter() - t0) * 1000))
        return res, ok

    def _record(self, actions: list[dict], name: str, args: dict, res: Any, ok: bool) -> None:
        actions.append({"tool": name, "input": args, "ok": ok, "summary": summarize(name, res, ok)})

    # --- Claude tool-use loop ---
    def _claude_loop(self, agent: str, spec: dict, history: list[dict], text: str, actions: list, logger: RunLogger) -> str:
        others = "; ".join(f"«{a['title']}» - {a['role']}" for n, a in CHAT_AGENTS.items() if n != agent)
        system = SYSTEM.format(title=spec["title"], role=spec["role"], university=self.store.profile()["university"],
                               others=others)
        tools = [{"name": n, "description": self.tools.catalog[n][2], "input_schema": self.tools.catalog[n][1]}
                 for n in spec["tools"]]
        messages: list[dict] = [{"role": m["role"], "content": m["text"]} for m in history if m["text"]]
        messages.append({"role": "user", "content": text})
        llm = self._claude()
        reply = ""
        for rnd in range(MAX_ROUNDS):
            t0 = time.perf_counter()
            resp = llm.turn(system, messages, tools, CHAT_EFFORT, final=rnd == MAX_ROUNDS - 1)
            logger.log("llm_call", agent, name="chat", provider="claude", model=llm.model, status="ok",
                       duration_ms=round((time.perf_counter() - t0) * 1000), tokens_in=resp.usage.input_tokens,
                       tokens_out=resp.usage.output_tokens)
            messages.append({"role": "assistant", "content": resp.content})
            reply = "\n".join(b.text for b in resp.content if b.type == "text").strip() or reply
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                return reply or "Готово."
            results = []
            for b in uses:
                args = dict(b.input) if isinstance(b.input, dict) else {}
                res, ok = self._call(agent, b.name, args, logger) if b.name in spec["tools"] else (
                    {"error": f"инструмент {b.name} не принадлежит этому агенту"}, False)
                self._record(actions, b.name, args, res, ok)
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": not ok,
                                "content": json.dumps(res, ensure_ascii=False, default=str)[:RESULT_CHARS]})
            messages.append({"role": "user", "content": results})
        return (reply + "\n\n" if reply else "") + "_Остановился по лимиту шагов — уточните задачу._"

    # --- offline rules ---
    def _offline(self, agent: str, spec: dict, text: str, actions: list, logger: RunLogger) -> str:
        t = text.lower()
        has = lambda tool: tool in spec["tools"]  # noqa: E731
        groups = re.findall(r"\b([A-Za-zА-Яа-я]{2,4}-\d{4})\b", text)
        slots = re.findall(r"(пн|вт|ср|чт|пт|сб|mon|tue|wed|thu|fri|sat)\w*\.?,?\s+(\d{1,2}:\d{2})", t)
        room = re.search(r"\b((?:C|С)1[.\-][\w.]+|IEC[.\-][\w.]+)", text)
        plan: tuple[str, dict] | None = None
        if has("move_session") and groups and len(slots) >= 2 and re.search(r"перен|сдвин|move", t):
            plan = ("move_session", {"group": groups[0], "day": slots[0][0], "time": slots[0][1],
                                     "to_day": slots[1][0], "to_time": slots[1][1], **({"room": room.group(1)} if room else {})})
        elif has("change_room") and groups and slots and room:
            plan = ("change_room", {"group": groups[0], "day": slots[0][0], "time": slots[0][1], "room": room.group(1)})
        elif has("suggest_moves") and groups and re.search(r"улучш|окн|лучше|предлож", t):
            plan = ("suggest_moves", {"group": groups[0]})
        elif has("move_options") and groups and slots:
            plan = ("move_options", {"group": groups[0], "day": slots[0][0], "time": slots[0][1]})
        elif has("undo_move") and re.search(r"отмен|верни|undo", t):
            plan = ("undo_move", {})
        elif has("publish_draft") and re.search(r"опублик|publish|выпуст", t):
            plan = ("publish_draft", {})
        elif has("discard_draft") and re.search(r"сбрось|удали черновик|discard", t):
            plan = ("discard_draft", {})
        elif has("set_autopilot") and re.search(r"выключ|включ|останов", t):
            plan = ("set_autopilot", {"on": not re.search(r"выключ|останов", t)})
        elif has("regenerate") and re.search(r"перегенер|сгенер|улучш|запусти", t):
            plan = ("regenerate", {"kind": "improve" if "улучш" in t else "regenerate", "reason": text[:120]})
        elif has("validate") and re.search(r"провер|нарушен|valid", t):
            plan = ("validate", {})
        elif has("quality") and re.search(r"качеств|окн|quality", t):
            plan = ("quality", {})
        elif has("remove_rule") and (m := re.search(r"удали\w*\s+(?:правило\s*)?№?\s*(\d+)", t)):
            plan = ("remove_rule", {"number": int(m.group(1))})
        elif has("update_rule") and (m := re.search(r"правило\s*№?\s*(\d+).*?(мягк|жёстк|жестк|soft|hard)", t)):
            plan = ("update_rule", {"number": int(m.group(1)), "mode": "soft" if m.group(2) in ("мягк", "soft") else "hard"})
        elif has("list_rules") and re.search(r"правил|rules|список", t) and not re.search(r"добав", t):
            plan = ("list_rules", {})
        elif has("requests_inbox") and re.search(r"заявк", t) and not re.search(r"аудитори|групп|курс", t):
            plan = ("requests_inbox", {})
        elif has("jobs_history") and re.search(r"истори|задани|верси", t):
            plan = ("jobs_history", {})
        elif has("group_week") and groups:
            plan = ("group_week", {"group": groups[0]})
        elif has("room_week") and room:
            plan = ("room_week", {"room": room.group(1)})
        elif has("add_rule") or has("submit_request"):
            tool = "add_rule" if has("add_rule") else "submit_request"
            body = re.sub(r"^(добавь(те)?|создай(те)?|правило|заявка)[:\s]+", "", text.strip(), flags=re.I)
            mode = "soft" if re.search(r"мягк", t) else "hard" if re.search(r"жёстк|жестк", t) else "auto"  # else: wording
            plan = (tool, {"text": body, **({"mode": mode} if tool == "add_rule" else {})})
        elif has("data_summary"):
            plan = ("data_summary", {})
        elif has("overview"):
            plan = ("overview", {})
        if plan is None:
            return f"{spec['hello']}\n\nБез ключа Claude я понимаю простые команды. Мои инструменты: {', '.join(spec['tools'])}."
        res, ok = self._call(agent, plan[0], plan[1], logger)
        self._record(actions, plan[0], plan[1], res, ok)
        logger.log("llm_call", agent, name="chat", provider="offline-rules", status="ok")
        return summarize(plan[0], res, ok, detailed=True)


# ------------------------------------------------------------------------------------------
def summarize(tool: str, res: Any, ok: bool, detailed: bool = False) -> str:
    """Short Russian text of a tool result: the action chip in the UI and the offline answer."""
    if not ok:
        return f"Не получилось: {res.get('error') if isinstance(res, dict) else res}"
    r = res if isinstance(res, dict) else {"value": res}
    if tool == "move_session" or tool == "change_room":
        d = r.get("delta", 0)
        q = "без изменения штрафа" if not d else f"штраф {'+' if d > 0 else ''}{d}"
        return f"В черновике: {r['moved']} ({q}). Правок в черновике: {r['draft_changes']}, жёстких нарушений: {r['hard_violations']}."
    if tool == "move_options":
        s = r["session"]
        head = f"«{s['course']}» {s['day']} {s['time']}: допустимых слотов {r['feasible']}."
        if detailed and r["best"]:
            head += "\nЛучшие варианты:\n" + "\n".join(
                f"- {o['day']} {o['time']}, ауд. {o['room']}, штраф {'+' if o['delta'] > 0 else ''}{o['delta']}" for o in r["best"][:6])
        return head
    if tool == "suggest_moves":
        head = f"{r['group']}: улучшающих переносов {r['improving_moves']}."
        if detailed and r["best"]:
            head += "\n" + "\n".join(f"- «{x['course']}» ({x['kind']}) {x['from_day']} {x['from_time']} → {x['to_day']} "
                                     f"{x['to_time']}, ауд. {x['room']}, штраф {x['delta']}" for x in r["best"][:6])
        return head
    if tool == "undo_move":
        return f"Отменено: {r['undone']}."
    if tool == "publish_draft":
        return f"Опубликована версия v{r['version']} (затронуто групп: {r['groups_affected']}). {r['announcement']}" if detailed \
            else f"Опубликована v{r['version']}"
    if tool == "discard_draft":
        return "Черновик сброшен."
    if tool == "draft_status":
        return f"В черновике правок: {len(r['changes'])}, жёстких нарушений: {r['hard_violations']}." + (
            "\n" + "\n".join(f"- {c}" for c in r["changes"]) if detailed and r["changes"] else "")
    if tool == "validate":
        soft = r["soft"]
        return (f"Жёстких нарушений: {r['hard_violations']}. Окна: {soft.get('gap_hours_per_group')} ч на группу, "
                f"после 18:00: {soft.get('late_hours_18_plus')} ч, суббота: {soft.get('saturday_hours')} ч.")
    if tool == "quality":
        soft = r["soft"]
        top = ", ".join(f"{x['group']} ({x['gap_hours']} ч)" for x in r["groups_with_most_gaps"][:5])
        return f"Окна: {soft.get('gap_hours_per_group')} ч на группу в неделю. Больше всего окон: {top or 'нет'}."
    if tool == "list_rules":
        if not r["rules"]:
            return "Своих правил вуза пока нет — действуют встроенные. Напишите правило обычной фразой."
        lines = [f"{x['number']}. {x['text']} — {'жёсткое' if x['mode'] == 'hard' else 'мягкое' if x['mode'] == 'soft' else 'авто'}"
                 f"{' · вес ' + str(x['weight']) if x['mode'] == 'soft' and x['weight'] else ''}" for x in r["rules"]]
        return f"Правил: {len(r['rules'])}" + ("\n" + "\n".join(lines) if detailed else "")
    if tool == "preview_rule":
        return f"Понял так: {r['understood']}" if r["understood"] else "Офлайн-правила не распознали фразу."
    if tool == "add_rule":
        return f"Правило №{r['number']} добавлено: {r['understood']}. {r['note']}."
    if tool == "update_rule":
        return f"Правило №{r['number']} теперь: {r['rule']}."
    if tool == "remove_rule":
        return f"Удалено правило: {r['removed']}."
    if tool == "regenerate":
        return f"Задание принято: {r['note']}." if r.get("queued") else r["note"]
    if tool == "submit_request":
        return f"Заявка №{r['request']} зарегистрирована. {r['note']}."
    if tool == "set_autopilot":
        return "Автопилот включён." if r["enabled"] else "Автопилот выключен."
    if tool == "group_week":
        out = f"{r['group']}: {r['hours']} ак. ч в неделю, окон {r['gaps']} ч."
        if detailed:
            out += "\n" + "\n".join(f"- {c['day']} {c['time']} — {c['course']} ({c['kind']}), {c['room']}" for c in r["classes"])
        return out
    if tool == "room_week":
        out = f"{r['room']} ({r['capacity']} мест): занято {r['booked_hours']} ч, загрузка {round(r['load'] * 100)} %."
        if detailed:
            out += "\n" + "\n".join(f"- {c['day']} {c['time']} — {c['course']}, {', '.join(c['groups'])}" for c in r["classes"][:20])
        return out
    if tool == "free_rooms":
        return f"{r['day']} {r['time']}: свободно {r['free']}: " + ", ".join(f"{x['room']} ({x['capacity']})" for x in r["rooms"][:8])
    if tool == "data_summary":
        return (f"Датасет {r['dataset']}: {len(r['programs'])} программ, {r['groups']} групп, {r['sessions']} занятий, "
                f"аудиторий: " + ", ".join(f"{k} {v}" for k, v in r["rooms"].items()) + f". Замечаний к данным: {sum(r['issues'].values())}.")
    if tool == "jobs_history":
        out = "Последние версии: " + ", ".join(f"v{v['id']} ({v['kind'] or 'авто'}, {v['when']})" for v in r["versions"][:4])
        if detailed and r["jobs"]:
            out += "\nЗадания:\n" + "\n".join(f"- №{j['id']} {j['kind']} — {j['status']} ({j['when']})" for j in r["jobs"])
        return out
    if tool == "requests_inbox":
        return f"Заявок: {len(r['requests'])}" + ("\n" + "\n".join(f"- №{x['id']} [{x['status']}] {x['text']}" for x in r["requests"][:8]) if detailed else "")
    if tool == "overview":
        v = r["version"]
        if not v:
            return "Опубликованной версии пока нет."
        return (f"Действует версия v{v['id']} от {v['published']}: окна {v['gap_hours_per_group']} ч на группу, штраф {v['score']}. "
                f"Черновик: {r['draft']['changes']} правок. Автопилот {'включён' if r['autopilot']['enabled'] else 'выключен'}, "
                f"открытых заявок: {r['requests_open']}.")
    return json.dumps(r, ensure_ascii=False)[:300]
