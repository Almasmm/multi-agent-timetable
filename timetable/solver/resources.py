"""Room capacity is modelled as counted resources per time slot so the time model never
needs individual room variables (that is what keeps a 270-group problem small):

* ``ROOMS``  - every regular room big enough for a group,
* ``K``      - computer labs (lab sessions need one),
* ``CAP<n>`` - rooms with capacity >= n (for lecture streams bigger than a practice room),
* ``GYM``    - parallel places in the sports hall.

For nested capacity classes these counts satisfy Hall's condition, so a per-slot feasible
time assignment can be turned into a concrete room assignment afterwards.
"""
from __future__ import annotations

from ..domain.models import GYM, LAB, Room, Session


def thresholds(sessions: list[Session], rooms: list[Room]) -> list[int]:
    base = min((s.size for s in sessions if not s.online), default=0)
    usable = [r for r in rooms if r.capacity >= base]
    sizes = sorted({s.size for s in sessions if not s.online and s.venue != GYM and s.kind != LAB})
    return [c for c in sizes if sum(r.capacity >= c for r in usable) < len(usable)]


def needs(s: Session, caps: list[int]) -> list[str]:
    if s.online:
        return []
    if s.venue == GYM:
        return [GYM]
    if s.kind == LAB:
        return ["ROOMS", "K"]
    return ["ROOMS"] + [f"CAP{c}" for c in caps if s.size >= c]


def capacity(rooms: list[Room], caps: list[int], min_size: int, gym_capacity: int,
             blocked: dict[str, set[int]], n_slots: int) -> dict[str, list[int]]:
    """Free units of every resource in every global slot."""
    res = {"ROOMS": [0] * n_slots, "K": [0] * n_slots, GYM: [gym_capacity] * n_slots}
    res.update({f"CAP{c}": [0] * n_slots for c in caps})
    for r in rooms:
        if r.capacity < min_size:
            continue
        keys = ["ROOMS"] + (["K"] if r.rtype == "K" else []) + [f"CAP{c}" for c in caps if r.capacity >= c]
        for t in range(n_slots):
            if t in blocked.get(r.code, ()):
                continue
            for k in keys:
                res[k][t] += 1
    return res


def usage(sessions: list[Session], starts: dict[str, int], caps: list[int], n_slots: int) -> dict[str, list[int]]:
    use: dict[str, list[int]] = {}
    for s in sessions:
        if s.id not in starts:
            continue
        for k in needs(s, caps):
            row = use.setdefault(k, [0] * n_slots)
            for t in range(starts[s.id], starts[s.id] + s.duration):
                row[t] += 1
    return use
