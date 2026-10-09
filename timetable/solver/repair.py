"""Local repair: move a conflicting session to another start where its groups are free, the
component day rule holds, the daily cap is respected and a suitable room is free."""
from __future__ import annotations

from collections import defaultdict

from ..domain.constraints import ConstraintIndex
from ..domain.models import GYM, LAB, Room, Session
from .rooms import _pref
from .time_model import slot_cost


def repair(sessions: list[Session], starts: dict[str, int], rooms_of: dict[str, str], to_fix: list[str],
           rooms: list[Room], cindex: ConstraintIndex, days: list[int], H: int,
           gym_capacity: int) -> tuple[list[dict], list[str]]:
    by_id = {s.id: s for s in sessions}
    group_busy: dict[str, set[int]] = defaultdict(set)
    room_busy: dict[str, set[int]] = defaultdict(set, {k: set(v) for k, v in cindex.room_blocked().items()})
    comp_days: dict[str, set[int]] = defaultdict(set)
    day_hours: dict[tuple[str, int], int] = defaultdict(int)
    fixing = set(to_fix)
    for s in sessions:
        if s.id not in starts or s.id in fixing:
            continue
        span = range(starts[s.id], starts[s.id] + s.duration)
        for g in s.groups:
            group_busy[g].update(span)
            day_hours[(g, starts[s.id] // H)] += s.duration
        if s.id in rooms_of:
            room_busy[rooms_of[s.id]].update(span)
        comp_days[s.component].add(starts[s.id] // H)
    gyms = [Room(f"GYM-{i + 1}", 10 ** 6, "G", 1) for i in range(gym_capacity)]

    moves, failed = [], []
    for sid in to_fix:
        s = by_id[sid]
        forbidden = cindex.forbidden(s)
        soft = cindex.penalties(s)
        caps = {g: cindex.max_daily(g, s.program, s.year) for g in s.groups}
        pool = gyms if s.venue == GYM else [r for r in rooms if r.capacity >= s.size and (s.kind != LAB or r.rtype == "K")]
        best = None
        for d in days:
            if d in comp_days[s.component]:
                continue
            if any(day_hours[(g, d)] + s.duration > caps[g] for g in s.groups):
                continue
            for h in range(H - s.duration + 1):
                t = d * H + h
                span = range(t, t + s.duration)
                if any(u in forbidden for u in span) or any(u in group_busy[g] for g in s.groups for u in span):
                    continue
                room = None
                if not s.online:
                    free = [r for r in pool if not any(u in room_busy[r.code] for u in span)]
                    if not free:
                        continue
                    room = min(free, key=lambda r: _pref(s, r)).code
                cost = slot_cost(t, s.duration, H, len(s.groups)) + sum(soft.get(u, 0) for u in span)
                if best is None or cost < best[0]:
                    best = (cost, t, room)
        if best is None:
            failed.append(sid)
            continue
        _, t, room = best
        span = range(t, t + s.duration)
        for g in s.groups:
            group_busy[g].update(span)
            day_hours[(g, t // H)] += s.duration
        if room:
            room_busy[room].update(span)
        comp_days[s.component].add(t // H)
        moves.append({"session": sid, "from": starts.get(sid), "to": t, "room": room})
    return moves, failed
