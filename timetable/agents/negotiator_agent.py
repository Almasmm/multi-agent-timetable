from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..core.agent import BaseAgent
from ..solver.repair import repair
from .common import H, chunk_sessions, constraint_index, days, run_params

Action = Literal["extend_time", "full_quota", "allow_saturday", "relax_user_constraints", "give_up"]
ACTIONS_HELP = {
    "extend_time": "solver hit the time limit without a solution: double the time budget for this chunk",
    "full_quota": "room quota is the bottleneck: let this chunk use all residual room capacity",
    "allow_saturday": "week is too short: open Saturday slots (penalised in the objective)",
    "relax_user_constraints": "turn hard user constraints that touch this chunk into soft ones",
    "give_up": "stop: nothing else can help, report the chunk as unscheduled",
}


class Decision(BaseModel):
    action: Action
    rationale: str = Field(description="one or two sentences in Russian")


class NegotiatorAgent(BaseAgent):
    name = "negotiator"
    role = ("Resolves conflicts between agents: decides which concession unblocks an infeasible chunk "
            "(more time, more rooms, Saturday, softer rules) and repairs residual clashes locally.")
    done_when = "a concession is applied (or give_up), or every repairable violation is moved"

    def register_tools(self) -> None:
        self.add_tool("decide", self._decide, "diagnostics -> concession (LLM, policy fallback)")
        self.add_tool("apply", self._apply, "apply a concession to the shared run parameters", retryable=False)
        self.add_tool("local_repair", lambda **kw: repair(**kw), "move clashing sessions to free slot+room",
                      retryable=False)

    # --- tools ---
    def _decide(self, chunk: int, status: str, diagnostics: list[str], tried: list[str]) -> Decision:
        allowed = [a for a in ACTIONS_HELP if a not in tried or a == "give_up"]
        if 5 in days(self.ctx, chunk) and "allow_saturday" in allowed:
            allowed.remove("allow_saturday")
        has_hard_user = any(s["hard"] and s["kind"] == "forbid_time" for s in self.get("constraint_specs", []))
        if not has_hard_user and "relax_user_constraints" in allowed:
            allowed.remove("relax_user_constraints")

        def policy() -> Decision:
            text = " ".join(diagnostics).lower()
            if status == "NO_SOLUTION_IN_TIME" and "extend_time" in allowed:
                return Decision(action="extend_time", rationale="Решатель не успел - даём больше времени.")
            if "allowed time windows" in text and "relax_user_constraints" in allowed:
                return Decision(action="relax_user_constraints",
                                rationale="Ресурса не хватает именно в разрешённых окнах - ослабляем запреты.")
            if ("quota" in text or "tight" in text) and "full_quota" in allowed:
                return Decision(action="full_quota", rationale="Узкое место - квота аудиторий; отдаём весь остаток.")
            if "relax_user_constraints" in allowed and "no admissible slot" in text:
                return Decision(action="relax_user_constraints", rationale="Пользовательские запреты не оставляют слотов.")
            if "allow_saturday" in allowed:
                return Decision(action="allow_saturday", rationale="Недели Пн-Пт не хватает - открываем субботу.")
            if "full_quota" in allowed:
                return Decision(action="full_quota", rationale="Пробуем весь остаток аудиторного фонда.")
            if "relax_user_constraints" in allowed:
                return Decision(action="relax_user_constraints", rationale="Ослабляем пользовательские запреты.")
            if "extend_time" in allowed:
                return Decision(action="extend_time", rationale="Последняя попытка - больше времени решателю.")
            return Decision(action="give_up", rationale="Все уступки исчерпаны.")

        decision = self.ctx.llm.structured(
            self.name, "choose_concession",
            system="You negotiate on behalf of a university timetable. A sub-problem (chunk) could not be solved. "
                   "Pick exactly one action from `allowed` that most likely fixes the diagnosed bottleneck with the "
                   "smallest loss of quality. Prefer actions in this order unless the diagnosis says otherwise: "
                   "extend_time (only for time-outs), full_quota (room quota bottleneck), allow_saturday, "
                   "relax_user_constraints, give_up.",
            user=json.dumps({"status": status, "diagnostics": diagnostics, "already_tried": tried,
                             "allowed": {a: ACTIONS_HELP[a] for a in allowed}}, ensure_ascii=False),
            schema=Decision, offline=policy)
        if decision.action not in allowed:  # never trust the LLM blindly
            decision = policy()
        return decision

    def _apply(self, chunk: int, action: str) -> dict:
        params = dict(run_params(self.ctx))
        key = str(chunk)
        params["tried"] = {**params["tried"], key: params["tried"].get(key, []) + [action]}
        if action == "extend_time":
            params["time_factor"] = {**params["time_factor"], key: params["time_factor"].get(key, 1) * 2}
        elif action == "full_quota":
            params["quota_mode"] = {**params["quota_mode"], key: "all"}
        elif action == "allow_saturday":
            params["saturday_chunks"] = params["saturday_chunks"] + [key]
        elif action == "relax_user_constraints":
            specs = self.get("constraint_specs", [])
            sessions = chunk_sessions(self.ctx, chunk)
            cidx = constraint_index(self.ctx)
            relaxed = []
            for spec_model, spec in zip(cidx.specs, specs):
                if spec["hard"] and spec["kind"] == "forbid_time" and any(cidx.matches(spec_model, s) for s in sessions):
                    spec["hard"] = False
                    spec["weight"] = max(spec.get("weight", 5), 10)
                    relaxed.append(spec["source"])
            self.put("constraint_specs", specs)
            params["relaxed"] = params.get("relaxed", []) + relaxed
        self.put("run_params", params)
        return params

    # --- tasks ---
    def on_resolve(self, chunk: int, status: str, diagnostics: list[str]) -> dict:
        tried = run_params(self.ctx)["tried"].get(str(chunk), [])
        decision = self.call_tool("decide", chunk=chunk, status=status, diagnostics=diagnostics, tried=tried)
        if decision.action != "give_up":
            self.call_tool("apply", chunk=chunk, action=decision.action)
        return {"action": decision.action, "rationale": decision.rationale}

    def on_repair(self, violations: list[dict]) -> dict:
        starts = dict(self.get("starts", {}))
        rooms_of = dict(self.get("rooms_of", {}))
        sids = {s.id: s for s in self.require("sessions")}
        to_fix = []
        for v in violations:
            sid = v.get("session")
            if sid and sid in sids and sid not in to_fix and v["type"] in (
                    "unscheduled", "no_room", "group_clash", "room_clash", "user_constraint", "room_unavailable"):
                to_fix.append(sid)
        if not to_fix:
            return {"moved": 0, "failed": [], "note": "no session-level violations to repair"}
        for sid in to_fix:
            rooms_of.pop(sid, None)
        moves, failed = self.call_tool(
            "local_repair", sessions=list(sids.values()), starts=starts, rooms_of=rooms_of, to_fix=to_fix,
            rooms=self.require("dataset").rooms, cindex=constraint_index(self.ctx), days=days(self.ctx), H=H,
            gym_capacity=run_params(self.ctx)["gym_capacity"])
        for mv in moves:
            starts[mv["session"]] = mv["to"]
            if mv["room"]:
                rooms_of[mv["session"]] = mv["room"]
        self.put("starts", starts)
        self.put("rooms_of", rooms_of)
        self.put("repairs", (self.get("repairs") or []) + moves)
        return {"moved": len(moves), "failed": failed[:30]}
