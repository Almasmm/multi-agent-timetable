"""Independent checker used by the Validator (critic) agent. It deliberately shares no code
with the CP-SAT model, so a modelling bug cannot hide its own violations."""
from __future__ import annotations

from collections import Counter, defaultdict

from ..domain.constraints import ConstraintIndex
from ..domain.models import GYM, LAB, LECTURE, Room, Session


def check(sessions: list[Session], starts: dict[str, int], rooms_of: dict[str, str] | None, rooms: list[Room],
          cindex: ConstraintIndex, days: list[int], H: int, quotas: dict[str, list[int]] | None = None,
          caps: list[int] | None = None) -> dict:
    hard: list[dict] = []

    def bad(kind: str, detail: str, sid: str | None = None) -> None:
        hard.append({"type": kind, "detail": detail, "session": sid})

    room_by_code = {r.code: r for r in rooms}
    blocked = cindex.room_blocked()
    group_slots: dict[tuple[str, int], list[str]] = defaultdict(list)
    room_slots: dict[tuple[str, int], list[str]] = defaultdict(list)
    comp_days: dict[str, Counter] = defaultdict(Counter)
    day_hours: dict[tuple[str, int], int] = defaultdict(int)
    late = sat = 0
    for s in sessions:
        if s.id not in starts:
            bad("unscheduled", f"{s.course} ({s.kind}) for {','.join(s.groups)} has no slot", s.id)
            continue
        t = starts[s.id]
        d, h = divmod(t, H)
        if d not in days or h + s.duration > H:
            bad("bad_slot", f"slot {t} outside the teaching week/day", s.id)
        span = range(t, t + s.duration)
        if set(span) & cindex.forbidden(s):
            bad("user_constraint", f"{s.course} for {','.join(s.groups)} placed in a forbidden window", s.id)
        for g in s.groups:
            day_hours[(g, d)] += s.duration
            for u in span:
                group_slots[(g, u)].append(s.id)
        comp_days[s.component][d] += 1
        late += sum(1 for u in span if u % H >= 10)
        sat += s.duration if d == 5 else 0
        if rooms_of is not None and not s.online:
            code = rooms_of.get(s.id)
            if code is None:
                bad("no_room", f"{s.course} ({s.kind}) for {','.join(s.groups)} has no room", s.id)
                continue
            if s.venue == GYM:
                continue
            r = room_by_code.get(code)
            if r is None:
                bad("unknown_room", f"room {code} does not exist", s.id)
                continue
            if r.capacity < s.size:
                bad("capacity", f"{code} ({r.capacity}) too small for {s.size} students", s.id)
            if s.kind == LAB and r.rtype != "K":
                bad("room_type", f"lab in non-lab room {code}", s.id)
            if set(span) & blocked.get(code, set()):
                bad("room_unavailable", f"{code} used while unavailable", s.id)
            for u in span:
                room_slots[(code, u)].append(s.id)
    for (g, u), ids in group_slots.items():
        if len(ids) > 1:
            bad("group_clash", f"group {g} has {len(ids)} classes in slot {u}", ids[0])
    for (code, u), ids in room_slots.items():
        if len(ids) > 1:
            bad("room_clash", f"room {code} double-booked in slot {u}", ids[0])
    for comp, cnt in comp_days.items():
        if any(v > 1 for v in cnt.values()):
            bad("same_day_blocks", f"{comp}: two blocks on one day")
    by_group_prog = {g: (s.program, s.year) for s in sessions for g in s.groups}
    for (g, d), hrs in day_hours.items():
        cap = cindex.max_daily(g, *by_group_prog[g])
        if hrs > cap:
            bad("daily_cap", f"group {g}: {hrs} h on day {d} (cap {cap})")
    if quotas and caps is not None:
        from .resources import usage

        for r, row in usage(sessions, starts, caps, len(next(iter(quotas.values())))).items():
            for u, n in enumerate(row):
                if n > quotas[r][u]:
                    bad("quota", f"resource {r} slot {u}: {n} > quota {quotas[r][u]}")

    # ---- soft quality metrics ----
    gaps = 0
    groups_days = defaultdict(set)
    for (g, u) in group_slots:
        groups_days[(g, u // H)].add(u % H)
    for hours in groups_days.values():
        lo, hi = min(hours), max(hours)
        gaps += (hi - lo + 1) - len(hours)
    groups = {g for s in sessions for g in s.groups}
    precedence = 0
    first_lecture: dict[tuple[str, str], int] = {}
    for s in sessions:
        if s.kind == LECTURE and s.id in starts:
            for g in s.groups:
                k = (s.course, g)
                first_lecture[k] = min(first_lecture.get(k, 99), starts[s.id] // H)
    for s in sessions:
        if s.kind != LECTURE and s.lecture_first and s.id in starts:
            fl = first_lecture.get((s.course, s.groups[0]))
            if fl is not None and starts[s.id] // H < fl:
                precedence += 1
    in_lecture_hall = sum(1 for s in sessions if s.kind == LECTURE and rooms_of and rooms_of.get(s.id, "").startswith("C")
                          and room_by_code.get(rooms_of[s.id], Room("", 0, "", 0)).rtype == "L")
    offline_lectures = sum(1 for s in sessions if s.kind == LECTURE and not s.online)
    soft = {
        "gap_hours_total": gaps,
        "gap_hours_per_group": round(gaps / max(len(groups), 1), 2),
        "late_hours_18_plus": late,
        "saturday_hours": sat,
        "precedence_violations": precedence,
        "lectures_in_lecture_halls": f"{in_lecture_hall}/{offline_lectures}",
    }
    by_type = Counter(v["type"] for v in hard)
    return {"hard_violations": len(hard), "by_type": dict(by_type), "violations": hard[:200], "soft": soft}
