from __future__ import annotations

import math

from ..core.agent import BaseAgent
from ..solver import resources as R
from ..solver.rooms import assign_rooms
from .common import chunk_sessions, constraint_index, run_params

N_SLOTS = 72


class RoomAgent(BaseAgent):
    name = "rooms"
    role = ("Owns the shared room fund: computes free capacity, gives every chunk a fair room quota "
            "(more on request of the negotiator) and assigns concrete rooms at the end.")
    done_when = "every offline session has a room or is reported as unassigned"

    def register_tools(self) -> None:
        self.add_tool("capacity", self._capacity, "rooms x slots -> free units per resource class", retryable=False)
        self.add_tool("quota", self._quota, "fair share of residual capacity for one chunk", retryable=False)
        self.add_tool("assign", self._assign, "concrete room per session (best fit, type preference)",
                      retryable=False)

    # --- tools ---
    def _capacity(self) -> dict:
        ds = self.require("dataset")
        sessions = self.require("sessions")
        caps = R.thresholds(sessions, ds.rooms)
        offline = [s.size for s in sessions if not s.online]
        min_size = min(offline) if offline else 0
        blocked = constraint_index(self.ctx).room_blocked()
        cap = R.capacity(ds.rooms, caps, min_size, run_params(self.ctx)["gym_capacity"], blocked, N_SLOTS)
        return {"caps": caps, "capacity": cap}

    def _quota(self, chunk: int, mode: str) -> dict[str, list[int]]:
        cap = self.require("capacity")
        caps = self.require("caps")
        chunks = self.require("chunks")
        sessions = self.require("sessions")
        validated = [i for i in range(len(chunks)) if self.get(f"validated:{i}") and i != chunk]
        used = {r: [0] * N_SLOTS for r in cap}
        for i in validated:
            for r, row in R.usage(chunk_sessions(self.ctx, i), self.require(f"solution:{i}"), caps, N_SLOTS).items():
                for t, n in enumerate(row):
                    used[r][t] += n
        pending = [i for i in range(len(chunks)) if i not in validated]
        mine = chunk_sessions(self.ctx, chunk)
        others = [s for i in pending if i != chunk for s in chunk_sessions(self.ctx, i)]
        quota = {}
        for r in cap:
            d_me = sum(s.duration for s in mine if r in R.needs(s, caps))
            d_all = d_me + sum(s.duration for s in others if r in R.needs(s, caps))
            share = 1.0 if mode == "all" or not d_all else min(1.0, d_me / d_all * self.ctx.settings.quota_slack)
            quota[r] = [max(0, min(cap[r][t] - used[r][t], math.ceil((cap[r][t] - used[r][t]) * share)))
                        for t in range(N_SLOTS)]
        return quota

    def _assign(self, starts: dict[str, int]):
        ds = self.require("dataset")
        return assign_rooms(self.require("sessions"), starts, ds.rooms, constraint_index(self.ctx).room_blocked(),
                            run_params(self.ctx)["gym_capacity"])

    # --- tasks ---
    def on_init_capacity(self) -> dict:
        out = self.call_tool("capacity")
        self.put("caps", out["caps"])
        self.put("capacity", out["capacity"])
        return {"capacity_classes": {k: max(v) for k, v in out["capacity"].items()}}

    def on_allocate_quota(self, chunk: int) -> dict:
        mode = run_params(self.ctx)["quota_mode"].get(str(chunk), "fair")
        quota = self.call_tool("quota", chunk=chunk, mode=mode)
        self.put(f"quota:{chunk}", quota)
        return {"mode": mode, "quota_hours": {r: sum(row[:60]) for r, row in quota.items()}}

    def on_assign_rooms(self) -> dict:
        starts = self.require("starts")
        rooms_of, unassigned = self.call_tool("assign", starts=starts)
        self.put("rooms_of", rooms_of)
        return {"assigned": len(rooms_of), "unassigned": unassigned}
