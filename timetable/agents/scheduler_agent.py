from __future__ import annotations

from ..core.agent import BaseAgent
from ..domain.demand import partition
from ..solver.time_model import solve_chunk
from .common import H, chunk_sessions, constraint_index, days, run_params


class SchedulerAgent(BaseAgent):
    name = "scheduler"
    role = "Decomposes the problem into chunks and assigns start times with CP-SAT inside the room quota."
    done_when = "every chunk has a solution (status OPTIMAL/FEASIBLE) or a diagnosis of why not"

    def register_tools(self) -> None:
        self.add_tool("partition", lambda sessions, target: partition(sessions, target),
                      "programme-year units -> chunks of ~N groups", retryable=False)
        self.add_tool("cp_sat_solve", lambda **kw: solve_chunk(**kw), "OR-Tools CP-SAT time model for one chunk",
                      retryable=False)

    def on_plan_chunks(self) -> dict:
        sessions = self.require("sessions")
        chunks = self.call_tool("partition", sessions=sessions, target=self.ctx.settings.chunk_target_groups)
        self.put("chunks", chunks)
        sizes = [sum(1 for s in sessions if s.unit in set(c)) for c in chunks]
        return {"chunks": len(chunks), "units": chunks, "sessions_per_chunk": sizes}

    def on_solve_chunk(self, chunk: int) -> dict:
        st = self.ctx.settings
        params = run_params(self.ctx)
        sessions = chunk_sessions(self.ctx, chunk)
        cidx = constraint_index(self.ctx)
        max_daily = {g: cidx.max_daily(g, s.program, s.year) for s in sessions for g in s.groups}
        factor = params["time_factor"].get(str(chunk), 1)
        res = self.call_tool("cp_sat_solve", sessions=sessions, cindex=cidx, quotas=self.require(f"quota:{chunk}"),
                             caps=self.require("caps"), days=days(self.ctx, chunk), H=H,
                             time_limit=st.solver_time_limit * factor, workers=st.solver_workers,
                             seed=st.seed + chunk, max_daily=max_daily)
        if res.ok:
            self.put(f"solution:{chunk}", res.starts)
        return {"status": res.status, "ok": res.ok, "diagnostics": res.diagnostics, "stats": res.stats,
                "wall_s": round(res.wall_s, 2)}
