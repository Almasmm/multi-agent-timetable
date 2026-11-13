"""Manual editing of the published timetable (drag & drop in the web UI, or a chat agent).

A published version is never changed in place. Edits collect in a *draft* = base version + an
ordered list of moves (stored in ``service.sqlite``). Every move is checked twice:

1. a fast local check (:meth:`Workspace.check_target`) with the solver's hard rules: all groups
   of the stream are free, no forbidden window of a university rule, daily hour cap, blocks of
   one course on different days, a free room that fits (capacity, labs in computer labs, closed
   rooms). The UI calls it for every slot while a card is dragged, to colour the drop targets;
2. on commit the whole timetable goes through the Validator's independent checker
   (``solver/checks.check``): the move is kept only if the number of hard violations does not grow.

Publishing the draft creates a new immutable version (kind ``manual``) with the usual diff and
notice. Its starts.json / rooms.json make the next automatic re-generation a warm start, so
the dispatcher's changes survive later runs wherever nothing forces a move.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from collections import defaultdict
from typing import Any

from ..config import DAYS, Settings
from ..domain.constraints import ConstraintIndex, ConstraintSpec
from ..domain.demand import build_sessions
from ..domain.loader import normalize, read_workbook
from ..domain.models import GYM, LAB, Room, Session
from ..export.writers import rows as export_rows
from ..export.writers import write_csv, write_excel, write_html
from ..solver.checks import check
from ..solver.rooms import _pref
from .store import ServiceStore
from .versions import diff_rows, quality_score, read_json, read_rows

DAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
KIND_RU = {"lecture": "лекция", "practice": "практика", "lab": "лабораторная"}


class Workspace:
    """Sessions of one version with their start slots and rooms, plus occupancy indexes."""

    def __init__(self, sessions: list[Session], starts: dict[str, int], rooms_of: dict[str, str], rooms: list[Room],
                 specs: list[dict], labels: dict[int, str], settings: Settings, issues: list[dict] | None = None):
        self.sessions = [s for s in sessions if s.id in starts]
        self.by_id = {s.id: s for s in self.sessions}
        self.starts = {k: int(v) for k, v in starts.items() if k in self.by_id}
        self.rooms_of = {k: v for k, v in rooms_of.items() if k in self.by_id}
        self.rooms, self.room_by = rooms, {r.code: r for r in rooms}
        self.specs = [ConstraintSpec.model_validate(x) for x in specs]
        self.H, self.first_hour = settings.slots_per_day, settings.first_hour
        self.cindex = ConstraintIndex(self.specs, self.H, self.first_hour, settings.max_hours_per_day)
        self.blocked = self.cindex.room_blocked()
        self.labels, self.issues = labels, issues or []
        used_days = {t // self.H for t in self.starts.values()}
        self.days = sorted({DAYS.index(d) for d in settings.days} | used_days)
        self.gym_capacity = settings.gym_capacity
        self.components: dict[str, list[str]] = defaultdict(list)
        for s in self.sessions:
            self.components[s.component].append(s.id)
        self.group_at: dict[str, dict[int, str]] = defaultdict(dict)
        self.room_at: dict[str, dict[int, str]] = defaultdict(dict)
        self.gym_at: dict[int, set[str]] = defaultdict(set)
        for s in self.sessions:
            self._occupy(s.id)
        self.base_hard = self.validate()["hard_violations"]

    # --- occupancy -----------------------------------------------------------------------------
    def span(self, sid: str, start: int | None = None) -> range:
        t = self.starts[sid] if start is None else start
        return range(t, t + self.by_id[sid].duration)

    def _occupy(self, sid: str) -> None:
        s = self.by_id[sid]
        for u in self.span(sid):
            for g in s.groups:
                self.group_at[g][u] = sid
            if s.venue == GYM:
                self.gym_at[u].add(sid)
            elif not s.online and self.rooms_of.get(sid):
                self.room_at[self.rooms_of[sid]][u] = sid

    def _vacate(self, sid: str) -> None:
        s = self.by_id[sid]
        for u in self.span(sid):
            for g in s.groups:
                if self.group_at[g].get(u) == sid:
                    del self.group_at[g][u]
            self.gym_at[u].discard(sid)
            code = self.rooms_of.get(sid)
            if code and self.room_at[code].get(u) == sid:
                del self.room_at[code][u]

    def locate(self, group: str | None = None, room: str | None = None, day: int = 0, hour: int = 0) -> str:
        t = day * self.H + hour
        sid = self.group_at.get(group or "", {}).get(t) if group else self.room_at.get(room or "", {}).get(t)
        if not sid:
            raise KeyError(f"в {DAY_RU[day]} {self.time(hour)} у {'группы ' + group if group else 'аудитории ' + str(room)} нет занятия")
        return sid

    def time(self, hour: int) -> str:
        return self.labels.get(hour, f"{self.first_hour + hour:02d}:00").split("-")[0]

    def describe(self, sid: str) -> dict:
        s, t = self.by_id[sid], self.starts[sid]
        d, h = divmod(t, self.H)
        return {"id": sid, "course": s.course, "kind": s.kind, "groups": s.groups, "size": s.size, "duration": s.duration,
                "online": s.online, "gym": s.venue == GYM, "day": d, "hour": h, "time": self.time(h),
                "room": "online" if s.online else self.rooms_of.get(sid, "UNASSIGNED")}

    def label(self, sid: str, start: int | None = None) -> str:
        s = self.by_id[sid]
        d, h = divmod(self.starts[sid] if start is None else start, self.H)
        return f"«{s.course}» ({KIND_RU.get(s.kind, s.kind)}, {', '.join(s.groups)}) {DAY_RU[d]} {self.time(h)}"

    # --- local checks ----------------------------------------------------------------------------
    def _fits(self, s: Session, r: Room, span: range, sid: str) -> bool:
        if r.capacity < s.size or (s.kind == LAB and r.rtype != "K"):
            return False
        if set(span) & self.blocked.get(r.code, set()):
            return False
        return all(self.room_at[r.code].get(u) in (None, sid) for u in span)

    def free_rooms(self, sid: str, start: int | None = None) -> list[str]:
        """Rooms that fit the session and are free over its span (best fit first)."""
        s = self.by_id[sid]
        if s.online or s.venue == GYM:
            return []
        span = self.span(sid, start)
        return [r.code for r in sorted((r for r in self.rooms if self._fits(s, r, span, sid)), key=lambda r: _pref(s, r))]

    def _day_hours(self, g: str, d: int, without: str, add: range | None = None) -> set[int]:
        hrs = {u % self.H for u, x in self.group_at[g].items() if u // self.H == d and x != without}
        if add is not None:
            hrs |= {u % self.H for u in add if u // self.H == d}
        return hrs

    @staticmethod
    def _gaps(hours: set[int]) -> int:
        return (max(hours) - min(hours) + 1 - len(hours)) if hours else 0

    def check_target(self, sid: str, start: int, room: str | None = None) -> dict:
        """Can session ``sid`` start at global slot ``start`` (optionally in ``room``)? Returns
        ok + reason, the room it would get and the soft-quality change (penalty hours, as in the
        Publisher's score: gaps + 2 x after 18:00 + 3 x Saturday)."""
        s = self.by_id[sid]
        d, h = divmod(start, self.H)
        out: dict[str, Any] = {"day": d, "hour": h, "start": start, "ok": False, "room": None, "reason": ""}
        if d not in self.days:
            return {**out, "reason": "не учебный день"}
        if h + s.duration > self.H:
            return {**out, "reason": "занятие не помещается до конца дня"}
        span = range(start, start + s.duration)
        for g in s.groups:
            for u in span:
                other = self.group_at[g].get(u)
                if other and other != sid:
                    return {**out, "reason": f"у группы {g} в это время «{self.by_id[other].course}»"}
        for spec in self.specs:
            if spec.kind == "forbid_time" and spec.hard and self.cindex.matches(spec, s) and \
                    set(span) & self.cindex._window(spec):
                return {**out, "reason": f"запрещено правилом: {spec.source or 'окно времени'}"}
        for other in self.components[s.component]:
            if other != sid and self.starts[other] // self.H == d:
                return {**out, "reason": f"в этот день уже есть другой блок «{s.course}» ({KIND_RU.get(s.kind, s.kind)})"}
        for g in s.groups:
            n = len(self._day_hours(g, d, sid, span))
            cap = self.cindex.max_daily(g, s.program, s.year)
            if n > cap:
                return {**out, "reason": f"у группы {g} будет {n} ч в день (лимит {cap})"}
        if s.online:
            out["room"] = "online"
        elif s.venue == GYM:
            busy = {x for u in span for x in self.gym_at[u] if x != sid}
            if len(busy) >= self.gym_capacity:
                return {**out, "reason": "спортзалы заняты"}
            out["room"] = self.rooms_of.get(sid)
        else:
            current = self.rooms_of.get(sid)
            if room:
                r = self.room_by.get(room)
                if r is None:
                    return {**out, "reason": f"аудитории {room} нет в данных"}
                if not self._fits(s, r, span, sid):
                    why = ("мала" if r.capacity < s.size else "не компьютерный класс" if s.kind == LAB and r.rtype != "K"
                           else "закрыта правилом" if set(span) & self.blocked.get(r.code, set()) else "занята")
                    return {**out, "reason": f"аудитория {room} {why}"}
                out["room"] = room
            elif current in self.room_by and self._fits(s, self.room_by[current], span, sid):
                out["room"] = current
            else:
                free = self.free_rooms(sid, start)
                if not free:
                    return {**out, "reason": "нет свободной подходящей аудитории"}
                out["room"] = free[0]
            out["room_change"] = out["room"] != current
        # soft change for students
        old = self.span(sid)
        delta = 0.0
        for g in s.groups:
            for day in {old.start // self.H, d}:
                before = self._gaps(self._day_hours(g, day, "", None))
                after = self._gaps(self._day_hours(g, day, sid, span))
                delta += after - before
        late = lambda rg: sum(1 for u in rg if u % self.H >= 10)  # noqa: E731
        sat = lambda rg: sum(1 for u in rg if u // self.H == 5)  # noqa: E731
        delta += 2 * (late(span) - late(old)) * len(s.groups) + 3 * (sat(span) - sat(old)) * len(s.groups)
        pen = self.cindex.penalties(s)
        soft_rule = sum(pen.get(u, 0) for u in span) - sum(pen.get(u, 0) for u in old)
        return {**out, "ok": True, "delta": round(delta, 1), "soft_rule": soft_rule}

    def options(self, sid: str) -> list[dict]:
        s = self.by_id[sid]
        return [self.check_target(sid, d * self.H + h) for d in self.days for h in range(self.H - s.duration + 1)]

    # --- commit -----------------------------------------------------------------------------------
    def validate(self) -> dict:
        """The Validator's independent checker over the whole timetable."""
        return check(self.sessions, self.starts, self.rooms_of, self.rooms, self.cindex, self.days, self.H)

    def apply(self, sid: str, start: int, room: str | None) -> None:
        self._vacate(sid)
        self.starts[sid] = start
        if room and room != "online":
            self.rooms_of[sid] = room
        self._occupy(sid)

    def move(self, sid: str, start: int, room: str | None = None) -> dict:
        res = self.check_target(sid, start, room)
        if not res["ok"]:
            raise ValueError(res["reason"])
        before = {"start": self.starts[sid], "room": self.rooms_of.get(sid)}
        label_from = self.label(sid)
        self.apply(sid, start, res["room"])
        val = self.validate()
        if val["hard_violations"] > self.base_hard:
            self.apply(sid, before["start"], before["room"])
            first = val["violations"][0]["detail"] if val["violations"] else "нарушение"
            raise ValueError(f"независимая проверка отклонила перенос: {first}")
        return {"sid": sid, "from": before["start"], "to": start, "room_from": before["room"],
                "room_to": self.rooms_of.get(sid), "label": f"{label_from} → {DAY_RU[start // self.H]} "
                f"{self.time(start % self.H)}{'' if res['room'] in (None, 'online', before['room']) else ', ауд. ' + res['room']}",
                "delta": res.get("delta", 0), "ts": time.time()}

    def export(self) -> list[dict]:
        return export_rows(self.sessions, self.starts, self.rooms_of, self.labels, self.H)


def web_rows(data: list[dict]) -> list[dict]:
    """writers.rows() format -> the row format of the web API (same as versions.read_rows)."""
    return [{"group": r["group"], "program": r["program"], "year": int(r["year"]), "day": r["day_index"],
             "hour": r["hour_index"], "time": r["time"], "course": r["discipline"], "kind": r["type"],
             "room": r["classroom"], "stream": r["stream_size"]} for r in data]


class ScheduleEditor:
    """Draft of manual changes on top of a published version (one draft per service)."""

    def __init__(self, store: ServiceStore, settings: Settings | None = None):
        self.store = store
        self.settings = settings or Settings()
        self._lock = threading.RLock()
        self._ws: Workspace | None = None
        self._key: tuple | None = None

    # --- loading ---------------------------------------------------------------------------------
    def _params(self, v: dict) -> dict:
        m = v.get("metrics") or {}
        if m.get("params"):
            return m["params"]
        job = self.store.job(v["job_id"]) if v.get("job_id") else None
        prof = self.store.profile()
        p = dict(job["params"]) if job else {}
        return {"dataset": p.get("dataset") or prof["dataset"], "term": p.get("term") or m.get("term") or prof["term"],
                "programs": p.get("programs") if job else m.get("programs") or prof["programs"],
                "stream": p.get("stream"), "allow_saturday": bool(p.get("allow_saturday"))}

    def _load(self, v: dict) -> Workspace:
        from .agents import find_dataset  # local import: agents imports this module's siblings

        p = self._params(v)
        st = self.settings.with_(data_path=find_dataset(p["dataset"]), term=int(p["term"]),
                                 programs=tuple(p.get("programs") or ()), allow_saturday=bool(p.get("allow_saturday")),
                                 **({"lecture_stream_size": int(p["stream"])} if p.get("stream") else {}))
        ds = normalize(read_workbook(st.data_path), st.weeks_per_term)
        sessions, _ = build_sessions(ds, st.term, st.lecture_stream_size, st.online_stream_size, st.programs,
                                     st.online_scope)
        vdir = self.store.version_dir(v["id"])
        specs = json.loads((vdir / "specs.json").read_text(encoding="utf-8")) if (vdir / "specs.json").exists() \
            else self._run_specs(v)
        return Workspace(sessions, read_json(vdir / "starts.json"), read_json(vdir / "rooms.json"), ds.rooms, specs,
                         ds.slot_labels, st, ds.issues)

    def _run_specs(self, v: dict) -> list[dict]:
        """Versions published before specs.json existed: constraints from the run's blackboard."""
        import sqlite3

        db = self.settings.out_root / str(v.get("run_id")) / "state.sqlite"
        if not db.exists():
            return []
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM kv WHERE key='constraint_specs'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else []

    def draft(self) -> dict:
        return self.store.get("draft") or {"base": None, "moves": []}

    def workspace(self) -> tuple[Workspace, dict]:
        """Workspace of the draft (base version + moves), cached until the draft changes."""
        with self._lock:
            draft = self.draft()
            cur = self.store.current_version()
            if cur is None:
                raise FileNotFoundError("опубликованной версии ещё нет - редактировать нечего")
            base = self.store.version(draft["base"]) if draft["base"] else cur
            if base is None:
                draft, base = {"base": None, "moves": []}, cur
            key = (base["id"], len(draft["moves"]), draft["moves"][-1]["ts"] if draft["moves"] else 0)
            if self._ws is None or self._key != key:  # rebuild: base version + replay every move
                ws = self._load(base)
                for m in draft["moves"]:
                    if m["sid"] in ws.by_id:
                        ws.apply(m["sid"], m["to"], m.get("room_to"))
                self._ws, self._key = ws, key
            return self._ws, {**draft, "base": base["id"]}

    # --- API ---------------------------------------------------------------------------------------
    def state(self, with_rows: bool = False) -> dict:
        ws, draft = self.workspace()
        cur = self.store.current_version()
        val = ws.validate() if draft["moves"] else None
        out = {"base": draft["base"], "current": cur["id"] if cur else None, "stale": bool(cur and draft["base"] != cur["id"]),
               "moves": draft["moves"], "count": len(draft["moves"]),
               "hard": val["hard_violations"] if val else ws.base_hard,
               "score": quality_score(val["soft"]) if val else None, "soft": val["soft"] if val else None}
        if with_rows:
            out["rows"] = web_rows(ws.export())
            out["moved"] = sorted({g for m in draft["moves"] if m["sid"] in ws.by_id for g in ws.by_id[m["sid"]].groups})
        return out

    def options(self, group: str | None, room: str | None, day: int, hour: int) -> dict:
        ws, _ = self.workspace()
        sid = ws.locate(group, room, day, hour)
        return {"session": ws.describe(sid), "slots": ws.options(sid), "rooms": ws.free_rooms(sid)}

    def move(self, group: str | None, room: str | None, day: int, hour: int, to_day: int, to_hour: int,
             to_room: str | None = None) -> dict:
        with self._lock:
            ws, draft = self.workspace()
            sid = ws.locate(group, room, day, hour)
            rec = ws.move(sid, to_day * ws.H + to_hour, to_room)
            moves = [*draft["moves"], rec]
            self.store.set("draft", {"base": draft["base"], "moves": moves})
            self._key = (draft["base"], len(moves), rec["ts"])
            return {"move": rec, "session": ws.describe(sid), **self.state()}

    def undo(self) -> dict:
        with self._lock:
            ws, draft = self.workspace()
            if not draft["moves"]:
                raise ValueError("в черновике нет правок")
            last = draft["moves"][-1]
            if last["sid"] in ws.by_id:
                ws.apply(last["sid"], last["from"], last.get("room_from"))
            moves = draft["moves"][:-1]
            self.store.set("draft", {"base": draft["base"], "moves": moves})
            self._key = (draft["base"], len(moves), moves[-1]["ts"] if moves else 0)
            return {"undone": last, **self.state()}

    def discard(self) -> dict:
        with self._lock:
            self.store.set("draft", None)
            self._ws = self._key = None
            return self.state()

    def publish(self, note: str = "", author: str = "диспетчер") -> dict:
        """Draft -> new immutable version (kind 'manual'), only with 0 new hard violations."""
        with self._lock:
            ws, draft = self.workspace()
            if not draft["moves"]:
                raise ValueError("в черновике нет правок")
            cur = self.store.current_version()
            if cur and cur["id"] != draft["base"]:
                raise RuntimeError(f"агенты уже опубликовали v{cur['id']}, черновик сделан от v{draft['base']} - сбросьте его")
            val = ws.validate()
            if val["hard_violations"] > ws.base_hard:
                raise ValueError(f"черновик не прошёл проверку: {val['hard_violations']} жёстких нарушений")
            base = self.store.version(draft["base"])
            bdir = self.store.version_dir(base["id"])
            data = ws.export()
            new_rows = web_rows(data)
            diff = diff_rows(read_rows(bdir / "timetable.csv"), new_rows)
            metrics = {**(base.get("metrics") or {}), **val["soft"], "kind": "manual", "params": self._params(base),
                       "base_version": base["id"], "manual_moves": len(draft["moves"]), "author": author}
            score = quality_score(val["soft"])
            text = (f"Диспетчер ({author}) внёс ручные правки в v{base['id']}: {len(draft['moves'])} перенос(а), "
                    f"затронуто групп: {diff['groups_affected']}. Перед публикацией расписание проверено независимым "
                    f"критиком: жёстких нарушений {val['hard_violations']}.{(' ' + note.strip()) if note.strip() else ''}")
            vid = self.store.add_version(f"manual-v{base['id']}", None, score, metrics, diff, text)
            vdir = self.store.version_dir(vid)
            vdir.mkdir(parents=True, exist_ok=True)
            write_csv(vdir / "timetable.csv", data)
            write_excel(vdir / "timetable.xlsx", data, ws.issues, {"agents": {}}, {**val["soft"], "hard_violations": val["by_type"]})
            write_html(vdir / "timetable.html", data, ws.labels, f"Расписание — версия v{vid} (ручные правки)",
                       {"hard_violations": val["hard_violations"], **val["soft"]})
            (vdir / "starts.json").write_text(json.dumps(ws.starts, ensure_ascii=False), encoding="utf-8")
            (vdir / "rooms.json").write_text(json.dumps(ws.rooms_of, ensure_ascii=False), encoding="utf-8")
            (vdir / "specs.json").write_text(json.dumps([s.model_dump() for s in ws.specs], ensure_ascii=False),
                                             encoding="utf-8")
            res = read_json(bdir / "result.json")
            (vdir / "result.json").write_text(json.dumps({**res, "soft": val["soft"], "hard_violations": val["by_type"],
                                                         "manual_moves": draft["moves"]}, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
            report = (bdir / "report.md").read_text(encoding="utf-8") if (bdir / "report.md").exists() else "# Отчёт"
            lines = [report, "", f"## Ручные правки (версия v{vid})", "", *[f"- {m['label']}" for m in draft["moves"]]]
            (vdir / "report.md").write_text("\n".join(lines), encoding="utf-8")
            if (bdir / "timetable.xlsx").exists() and not (vdir / "timetable.xlsx").exists():
                shutil.copy2(bdir / "timetable.xlsx", vdir / "timetable.xlsx")
            self.store.set("draft", None)
            self._ws = self._key = None
            return {"version": vid, "diff": diff, "score": score, "announcement": text}
