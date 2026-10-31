from __future__ import annotations

from ..core.agent import BaseAgent
from ..solver.evolve import evolve
from .common import H, chunk_sessions, constraint_index, days, run_params, warm_starts


class GeneticAgent(BaseAgent):
    name = "genetic"
    role = ("Builds the first generation of a chunk timetable with a genetic algorithm (greedy + evolution) "
            "and hands the best individual to the Scheduler as a CP-SAT solution hint.")
    done_when = "a seed timetable with its fitness statistics is on the blackboard, or the chunk is diagnosed as unseedable"

    def register_tools(self) -> None:
        self.add_tool("evolve", lambda **kw: evolve(**kw), "GA: population of timetables -> best individual + fitness",
                      retryable=False)

    def on_evolve(self, chunk: int) -> dict:
        st = self.ctx.settings
        sessions = chunk_sessions(self.ctx, chunk)
        cidx = constraint_index(self.ctx)
        max_daily = {g: cidx.max_daily(g, s.program, s.year) for s in sessions for g in s.groups}
        res = self.call_tool("evolve", sessions=sessions, cindex=cidx, quotas=self.require(f"quota:{chunk}"),
                             caps=self.require("caps"), days=days(self.ctx, chunk), H=H, time_limit=st.ga_seconds,
                             seed=st.seed + chunk, max_daily=max_daily, population=st.ga_population,
                             prev=warm_starts(self.ctx, sessions), w_stable=st.stability_weight)
        if res.ok:
            self.put(f"seed:{chunk}", res.starts)
        run_params(self.ctx)  # make sure shared params exist for later agents
        return {"ok": res.ok, "stats": res.stats, "diagnostics": res.diagnostics,
                "hard": res.stats.get("best_hard"), "generations": res.stats.get("generations")}
