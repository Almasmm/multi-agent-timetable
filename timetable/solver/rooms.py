"""Concrete room assignment once start times are fixed.

Sessions are processed in start-time order (interval scheduling: optimal for identical
rooms) taking the most constrained first; each picks the best-fitting free room with a
type preference: lectures -> L halls, practice -> P rooms, labs -> K labs only.
With a warm start, a first pass gives every session that kept its time slot its previous room
(if it is still free and fits), so a local change does not reshuffle rooms all over the campus.
"""
from __future__ import annotations

from collections import defaultdict

from ..domain.models import GYM, LAB, LECTURE, Room, Session


def _pref(s: Session, r: Room) -> tuple:
    if s.kind == LECTURE:
        return (r.rtype != "L", r.capacity)
    return (r.rtype != "P", r.rtype == "L", r.capacity)  # practice: P, then K, then L


def assign_rooms(sessions: list[Session], starts: dict[str, int], rooms: list[Room],
                 blocked: dict[str, set[int]], gym_capacity: int,
                 busy: dict[str, set[int]] | None = None,
                 prev_rooms: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    busy = defaultdict(set, {k: set(v) for k, v in (busy or {}).items()})
    for code, slots in blocked.items():
        busy[code] |= slots
    gyms = [Room(f"GYM-{i + 1}", 10 ** 6, "G", 1) for i in range(gym_capacity)]
    assignment: dict[str, str] = {}
    unassigned: list[str] = []
    todo = [s for s in sessions if not s.online and s.id in starts]
    todo.sort(key=lambda s: (starts[s.id], s.kind != LAB, -s.size, -s.duration))
    by_code = {r.code: r for r in rooms}
    for s in todo if prev_rooms else []:  # pass 1: keep the published room where possible
        r = by_code.get(prev_rooms.get(s.id, ""))
        span = range(starts[s.id], starts[s.id] + s.duration)
        if r and s.venue != GYM and r.capacity >= s.size and (s.kind != LAB or r.rtype == "K")                 and not any(u in busy[r.code] for u in span):
            assignment[s.id] = r.code
            busy[r.code].update(span)
    for s in (x for x in todo if x.id not in assignment):
        span = range(starts[s.id], starts[s.id] + s.duration)
        if s.venue == GYM:
            pool = gyms
        else:
            pool = [r for r in rooms if r.capacity >= s.size and (s.kind != LAB or r.rtype == "K")]
        free = [r for r in pool if not any(u in busy[r.code] for u in span)]
        if not free:
            unassigned.append(s.id)
            continue
        best = min(free, key=lambda r: _pref(s, r))
        assignment[s.id] = best.code
        busy[best.code].update(span)
    return assignment, unassigned
