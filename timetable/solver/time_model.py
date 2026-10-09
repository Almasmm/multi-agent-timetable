"""CP-SAT time-assignment model for one chunk of the timetable.

Variables: x[s, t] = 1 if session s starts in global slot t (t = day * H + hour).

Hard: every session exactly once; a group attends one class per slot; per-slot room
resources within the chunk's quota; blocks of one component on different days; daily hour
cap per group; user "forbid" windows (filtered out of the domain).

Soft (minimised): single and double gaps in a group's day, early/late hours, Saturday,
lecture-before-practice inside the week, user soft windows.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..domain.constraints import ConstraintIndex
from ..domain.models import LECTURE, Session
from .resources import needs

HOUR_COST = [2, 0, 0, 0, 0, 0, 0, 0, 0, 1, 3, 5]  # 08:00 slightly bad, 18:00+ bad
SATURDAY_COST = 6
W_GAP1, W_GAP2, W_PRECEDENCE = 4, 3, 6


@dataclass
class SolveResult:
    status: str
    starts: dict[str, int] = field(default_factory=dict)
    objective: float | None = None
    wall_s: float = 0.0
    diagnostics: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


def slot_cost(t: int, dur: int, H: int, n_groups: int) -> int:
    day, h = divmod(t, H)
    c = sum(HOUR_COST[min(h + k, len(HOUR_COST) - 1)] for k in range(dur))
    if day == 5:
        c += SATURDAY_COST * dur
    return c * max(1, min(n_groups, 3))


def solve_chunk(sessions: list[Session], cindex: ConstraintIndex, quotas: dict[str, list[int]], caps: list[int],
                days: list[int], H: int, time_limit: float, workers: int, seed: int,
                banned: dict[str, set[int]] | None = None, max_daily: dict[str, int] | None = None) -> SolveResult:
    t0 = time.perf_counter()
    banned = banned or {}
    m = cp_model.CpModel()
    x: dict[int, dict[int, cp_model.IntVar]] = {}
    cover: dict[int, dict[int, list]] = {}
    cost_terms = []
    diag: list[str] = []

    for i, s in enumerate(sessions):
        forbidden = cindex.forbidden(s) | banned.get(s.id, set())
        soft = cindex.penalties(s)
        res = needs(s, caps)
        x[i], cover[i] = {}, defaultdict(list)
        for d in days:
            for h in range(H - s.duration + 1):
                t = d * H + h
                span = range(t, t + s.duration)
                if any(u in forbidden for u in span):
                    continue
                if any(quotas.get(r, [0] * (6 * H))[u] <= 0 for r in res for u in span):
                    continue
                v = m.NewBoolVar(f"x{i}_{t}")
                x[i][t] = v
                for u in span:
                    cover[i][u].append(v)
                c = slot_cost(t, s.duration, H, len(s.groups)) + sum(soft.get(u, 0) for u in span)
                if c:
                    cost_terms.append(c * v)
        if not x[i]:
            diag.append(f"session '{s.id}' has no admissible slot (constraints/quotas exclude every slot)")
    if diag:
        return SolveResult("INFEASIBLE", diagnostics=diag, wall_s=time.perf_counter() - t0)

    for i in x:
        m.AddExactlyOne(x[i].values())

    # groups: one class at a time, daily cap, gaps
    by_group: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(sessions):
        for g in s.groups:
            by_group[g].append(i)
    n_gap = 0
    for g, idxs in by_group.items():
        occ: dict[int, list] = defaultdict(list)
        for i in idxs:
            for u, vs in cover[i].items():
                occ[u].extend(vs)
        for vs in occ.values():
            if len(vs) > 1:
                m.AddAtMostOne(vs)
        cap = (max_daily or {}).get(g)
        for d in days:
            day_terms = [sessions[i].duration * v for i in idxs for t, v in x[i].items() if t // H == d]
            if cap is not None and day_terms:
                m.Add(sum(day_terms) <= cap)
            o = [sum(occ.get(d * H + h, [])) for h in range(H)]
            for h in range(1, H - 1):
                if not occ.get(d * H + h):
                    continue
                gap = m.NewBoolVar("")
                m.Add(gap >= o[h - 1] + o[h + 1] - o[h] - 1)
                cost_terms.append(W_GAP1 * gap)
                n_gap += 1
                if h + 2 < H:
                    gap2 = m.NewBoolVar("")
                    m.Add(gap2 >= o[h - 1] + o[h + 2] - o[h] - o[h + 1] - 1)
                    cost_terms.append(W_GAP2 * gap2)

    # room resources within quota
    use: dict[tuple[str, int], list] = defaultdict(list)
    for i, s in enumerate(sessions):
        for r in needs(s, caps):
            for u, vs in cover[i].items():
                use[(r, u)].extend(vs)
    for (r, u), vs in use.items():
        q = quotas[r][u]
        if len(vs) > q:
            m.Add(sum(vs) <= q)

    # blocks of the same component on different days
    comps: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(sessions):
        comps[s.component].append(i)
    for idxs in comps.values():
        if len(idxs) > 1:
            for d in days:
                vs = [v for i in idxs for t, v in x[i].items() if t // H == d]
                if len(vs) > 1:
                    m.AddAtMostOne(vs)

    # soft: first lecture of a course in the week before its practice/lab
    lecture_comps: dict[tuple[str, str], list[int]] = defaultdict(list)  # (course-unit, group) -> lecture idxs
    for i, s in enumerate(sessions):
        if s.kind == LECTURE:
            for g in s.groups:
                lecture_comps[(s.unit + s.course, g)].append(i)
    min_day_cache: dict[tuple, cp_model.IntVar] = {}

    def day_of(i: int):
        return sum((t // H) * v for t, v in x[i].items())

    n_prec = 0
    for i, s in enumerate(sessions):
        if s.kind == LECTURE or not s.lecture_first:
            continue
        lidx = lecture_comps.get((s.unit + s.course, s.groups[0]))
        if not lidx:
            continue
        key = tuple(lidx)
        if key not in min_day_cache:
            dvars = []
            for j in lidx:
                dv = m.NewIntVar(0, 5, "")
                m.Add(dv == day_of(j))
                dvars.append(dv)
            mn = m.NewIntVar(0, 5, "")
            m.AddMinEquality(mn, dvars)
            min_day_cache[key] = mn
        slack = m.NewIntVar(0, 5, "")
        m.Add(slack >= min_day_cache[key] - day_of(i))
        cost_terms.append(W_PRECEDENCE * slack)
        n_prec += 1

    m.Minimize(sum(cost_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    solver.parameters.relative_gap_limit = 0.02
    status = solver.Solve(m)
    name = solver.StatusName(status)
    stats = {"sessions": len(sessions), "bool_vars": sum(len(v) for v in x.values()), "gap_vars": n_gap,
             "precedence_terms": n_prec, "objective": solver.ObjectiveValue() if name in ("OPTIMAL", "FEASIBLE") else None,
             "best_bound": solver.BestObjectiveBound(), "solver_wall_s": round(solver.WallTime(), 2)}
    if name not in ("OPTIMAL", "FEASIBLE"):
        diag = explain_infeasibility(sessions, quotas, caps, days, H, by_group, {i: set(cover[i]) for i in cover})
        return SolveResult("INFEASIBLE" if name == "INFEASIBLE" else "NO_SOLUTION_IN_TIME", diagnostics=diag,
                           wall_s=time.perf_counter() - t0, stats=stats)
    starts = {sessions[i].id: next(t for t, v in x[i].items() if solver.Value(v)) for i in x}
    return SolveResult(name, starts, solver.ObjectiveValue(), time.perf_counter() - t0, [], stats)


def explain_infeasibility(sessions, quotas, caps, days, H, by_group, admissible) -> list[str]:
    """Cheap necessary-condition checks that usually pinpoint the bottleneck. ``admissible``
    maps a session index to the slots it may cover after user constraints and quotas, so a
    window-induced shortage ("PE only in the morning" vs. gym places) is named explicitly."""
    out = []
    slots = [d * H + h for d in days for h in range(H)]
    n_slots = 6 * H
    demand: dict[str, int] = defaultdict(int)
    window: dict[str, set[int]] = defaultdict(set)
    for i, s in enumerate(sessions):
        for r in needs(s, caps):
            demand[r] += s.duration
            window[r] |= admissible.get(i, set())
    for r, need in demand.items():
        row = quotas.get(r, [0] * n_slots)
        have = sum(row[t] for t in slots)
        in_window = sum(row[t] for t in window[r])
        if need > have:
            out.append(f"resource {r}: demand {need} h > quota {have} h")
        elif need > in_window:
            out.append(f"resource {r}: demand {need} h > quota {in_window} h within the allowed time windows "
                       f"(user constraints shrink the usable slots)")
        elif need > 0.85 * have:
            out.append(f"resource {r} is tight: demand {need} h of quota {have} h ({need / max(have, 1):.0%})")
    for g, idxs in by_group.items():
        hours = sum(sessions[i].duration for i in idxs)
        usable = set().union(*(admissible.get(i, set()) for i in idxs))
        if hours > len(usable):
            out.append(f"group {g} needs {hours} h but only {len(usable)} admissible slots remain")
    return out or ["no single bottleneck found: combination of constraints is too tight"]
