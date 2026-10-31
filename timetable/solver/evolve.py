"""Genetic algorithm that builds the *first generation* of a chunk timetable.

The chromosome is a vector ``gene[i] = start slot of session i`` drawn from the session's
admissible slots (days, duration, user forbid-windows, positive room quota - the same
domain the CP-SAT model uses). Fitness = 1000 * hard violations + soft penalty, where

* hard: a group in two classes at once, resource use above the quota, two blocks of one
  component on the same day, daily hour cap exceeded;
* soft: early/late/Saturday slot costs, gaps inside a group's day, user soft windows and -
  with a warm start - ``w_stable`` for every session moved away from the published version.

Population = greedy constructive individuals (random session order) + random ones;
tournament selection, uniform crossover, random mutation and - the memetic step - one
local-search pass over every gene of the child (each session is moved to the best of a few
admissible slots). Fitness is maintained *incrementally* (``State.move``), so a whole pass
over ~800 sessions costs well under 0.1 s instead of hundreds of full re-evaluations.
The best individual is handed to CP-SAT as a solution hint, so the exact solver starts
from an already good timetable instead of from scratch - this mirrors the "GA builds,
agents refine" idea of the master's thesis.
"""
from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.constraints import ConstraintIndex
from ..domain.models import Session
from .resources import needs
from .time_model import W_GAP1, slot_cost

HARD_W = 1000


@dataclass
class EvolveResult:
    starts: dict[str, int] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.starts)


class _Problem:
    """Pre-computed per-session data shared by all individuals."""

    def __init__(self, sessions, cindex: ConstraintIndex, quotas, caps, days, H, max_daily,
                 prev: dict[str, int] | None = None, w_stable: int = 0):
        self.sessions = sessions
        self.H = H
        self.n_slots = 6 * H
        self.quotas = quotas
        self.caps_of: dict[str, int] = dict(max_daily or {})
        self.allowed: list[list[int]] = []
        self.res: list[list[str]] = []
        self.base_cost: list[dict[int, int]] = []
        for s in sessions:
            forbidden = cindex.forbidden(s)
            soft = cindex.penalties(s)
            res = needs(s, caps)
            self.res.append(res)
            opts, cost = [], {}
            for d in days:
                for h in range(H - s.duration + 1):
                    t = d * H + h
                    span = range(t, t + s.duration)
                    if any(u in forbidden for u in span):
                        continue
                    if any(quotas.get(r, [0] * self.n_slots)[u] <= 0 for r in res for u in span):
                        continue
                    opts.append(t)
                    cost[t] = slot_cost(t, s.duration, H, len(s.groups)) + sum(soft.get(u, 0) for u in span)
            if prev and prev.get(s.id) in cost:  # stability: staying in the published slot is free
                for t in cost:
                    cost[t] += w_stable if t != prev[s.id] else 0
            self.allowed.append(opts)
            self.base_cost.append(cost)


class State:
    """Counters of one individual with incrementally maintained hard/soft totals."""

    __slots__ = ("p", "genes", "group_slot", "use", "comp_day", "day_hours", "occ", "hard", "soft")

    def __init__(self, p: _Problem, genes: list[int]):
        self.p = p
        self.genes = genes
        self.group_slot: dict = defaultdict(int)
        self.use: dict = defaultdict(int)
        self.comp_day: dict = defaultdict(int)
        self.day_hours: dict = defaultdict(int)
        self.occ: dict = defaultdict(lambda: defaultdict(int))  # (g, d) -> {hour: count}
        self.hard = 0
        self.soft = 0
        for i, t in enumerate(genes):
            self._add(i, t, +1)

    @property
    def fitness(self) -> int:
        return HARD_W * self.hard + self.soft

    @staticmethod
    def _gap(hours: dict) -> int:
        keys = [h for h, n in hours.items() if n > 0]
        return (max(keys) - min(keys) + 1) - len(keys) if keys else 0

    def _add(self, i: int, t: int, sign: int) -> None:
        p, s, H = self.p, self.p.sessions[i], self.p.H
        d = t // H
        self.soft += sign * p.base_cost[i][t]
        # component blocks on different days
        n = self.comp_day[(s.component, d)]
        self.comp_day[(s.component, d)] = n + sign
        self.hard += (max(0, n + sign - 1) - max(0, n - 1))
        for g in s.groups:
            # daily cap
            cap = p.caps_of.get(g)
            hrs = self.day_hours[(g, d)]
            self.day_hours[(g, d)] = hrs + sign * s.duration
            if cap is not None:
                self.hard += max(0, hrs + sign * s.duration - cap) - max(0, hrs - cap)
            # gaps of this group-day
            occ = self.occ[(g, d)]
            self.soft -= W_GAP1 * self._gap(occ)
            for u in range(t, t + s.duration):
                occ[u % H] += sign
                n = self.group_slot[(g, u)]
                self.group_slot[(g, u)] = n + sign
                self.hard += max(0, n + sign - 1) - max(0, n - 1)
            self.soft += W_GAP1 * self._gap(occ)
        for r in p.res[i]:
            for u in range(t, t + s.duration):
                q = p.quotas[r][u]
                n = self.use[(r, u)]
                self.use[(r, u)] = n + sign
                self.hard += max(0, n + sign - q) - max(0, n - q)

    def move(self, i: int, t_new: int) -> None:
        t_old = self.genes[i]
        if t_old == t_new:
            return
        self._add(i, t_old, -1)
        self._add(i, t_new, +1)
        self.genes[i] = t_new

    def conflicting(self) -> list[int]:
        """Indices of sessions that take part in a hard conflict."""
        p, H = self.p, self.p.H
        out = []
        for i, s in enumerate(p.sessions):
            t = self.genes[i]
            d = t // H
            bad = self.comp_day[(s.component, d)] > 1
            if not bad:
                for g in s.groups:
                    cap = p.caps_of.get(g)
                    if (cap is not None and self.day_hours[(g, d)] > cap) or any(
                            self.group_slot[(g, u)] > 1 for u in range(t, t + s.duration)):
                        bad = True
                        break
            if not bad:
                bad = any(self.use[(r, u)] > p.quotas[r][u] for r in p.res[i] for u in range(t, t + s.duration))
            if bad:
                out.append(i)
        return out

    def improve_gene(self, i: int, rng: random.Random, candidates: int = 6) -> None:
        """Memetic step: move session i to the best of a few random admissible slots."""
        best_t, best_f = self.genes[i], self.fitness
        for t in rng.sample(self.p.allowed[i], min(candidates, len(self.p.allowed[i]))):
            self.move(i, t)
            if self.fitness < best_f:
                best_t, best_f = t, self.fitness
        self.move(i, best_t)

    def local_search(self, rng: random.Random, candidates: int = 6) -> None:
        """One pass over all sessions in random order (repairs conflicts, closes gaps)."""
        order = list(range(len(self.genes)))
        rng.shuffle(order)
        for i in order:
            self.improve_gene(i, rng, candidates)


def greedy(p: _Problem, rng: random.Random) -> list[int]:
    """Place sessions one by one (most constrained first, random tie-break) into the slot
    with the smallest incremental cost - an admissible individual in most cases."""
    H = p.H
    order = sorted(range(len(p.sessions)), key=lambda i: (len(p.allowed[i]), rng.random()))
    genes = [p.allowed[i][0] for i in range(len(p.sessions))]
    st = State(p, [])  # empty state; we add sessions as we go
    st.genes = genes
    placed: list[int] = []
    for i in order:
        best_t, best_f = None, None
        for t in rng.sample(p.allowed[i], min(24, len(p.allowed[i]))):
            st._add(i, t, +1)
            f = st.fitness
            st._add(i, t, -1)
            if best_f is None or f < best_f:
                best_t, best_f = t, f
        genes[i] = best_t
        st._add(i, best_t, +1)
        placed.append(i)
    return genes


def evolve(sessions: list[Session], cindex: ConstraintIndex, quotas: dict[str, list[int]], caps: list[int],
           days: list[int], H: int, time_limit: float, seed: int, max_daily: dict[str, int] | None = None,
           population: int = 12, elite: int = 2, mutation: float = 0.02, prev: dict[str, int] | None = None,
           w_stable: int = 0) -> EvolveResult:
    """``prev`` (session id -> start of the published version) seeds one individual with the
    published timetable; with ``w_stable`` > 0 every moved session is penalised (warm start)."""
    t0 = time.perf_counter()
    rng = random.Random(seed)
    p = _Problem(sessions, cindex, quotas, caps, days, H, max_daily, prev, w_stable)
    empty = [sessions[i].id for i in range(len(sessions)) if not p.allowed[i]]
    if empty:
        return EvolveResult(diagnostics=[f"session '{sid}' has no admissible slot" for sid in empty[:5]])
    n = len(sessions)
    pop: list[State] = [State(p, greedy(p, rng)) for _ in range(max(1, population // 2))]
    kept = 0
    if prev:  # warm individual: the published slot where it is still admissible, greedy elsewhere
        genes = list(pop[0].genes)
        for i, s in enumerate(sessions):
            if prev.get(s.id) in p.base_cost[i]:
                genes[i] = prev[s.id]
                kept += 1
        pop.append(State(p, genes))
    while len(pop) < population:
        pop.append(State(p, [rng.choice(p.allowed[i]) for i in range(n)]))
    raw = min(pop, key=lambda s: s.fitness)
    greedy_fit, greedy_hard = raw.fitness, raw.hard  # best individual before the memetic pass
    for st in pop:
        st.local_search(rng)
    pop.sort(key=lambda s: s.fitness)
    first = (pop[0].fitness, pop[0].hard, pop[0].soft)
    generations = 0
    n_mut = max(1, math.ceil(mutation * n))

    def tournament() -> State:
        return min(rng.sample(pop, min(3, len(pop))), key=lambda s: s.fitness)

    while time.perf_counter() - t0 < time_limit:
        children = pop[:elite]
        while len(children) < population:
            a, b = tournament(), tournament()
            child = State(p, [a.genes[i] if rng.random() < 0.5 else b.genes[i] for i in range(n)])
            for i in rng.sample(range(n), n_mut):  # random mutation keeps diversity
                child.move(i, rng.choice(p.allowed[i]))
            child.local_search(rng)  # memetic step: repair conflicts, close gaps
            children.append(child)
        pop = sorted(children, key=lambda s: s.fitness)
        generations += 1
    best = pop[0]
    stats = {"sessions": n, "population": population, "generations": generations,
             "greedy_fitness": greedy_fit, "greedy_hard": greedy_hard, "first_fitness": first[0],
             "first_hard": first[1], "best_fitness": best.fitness, "best_hard": best.hard, "best_soft": best.soft,
             "wall_s": round(time.perf_counter() - t0, 2), "warm_kept": kept}
    return EvolveResult({sessions[i].id: best.genes[i] for i in range(n)}, stats,
                        [] if best.hard == 0 else [f"best individual still has {best.hard} hard conflicts - CP-SAT will repair them"])
