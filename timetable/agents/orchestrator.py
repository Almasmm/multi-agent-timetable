"""Orchestrator: only plans and routes. It never touches timetable data itself - it sends
typed task messages, reads the status fields of the replies and decides the next step."""
from __future__ import annotations

import time
from typing import Any

from ..core.agent import RunContext
from ..core.errors import LimitExceeded
from ..core.messages import Message


class Orchestrator:
    name = "orchestrator"
    role = "Plans the pipeline, dispatches tasks to agents, enforces step/time/loop limits."
    done_when = "every plan step finished or the run was stopped with a clear reason"

    def __init__(self, ctx: RunContext, agents: dict[str, Any]):
        self.ctx = ctx
        self.agents = agents
        self.steps = 0
        self.timeline: list[dict] = []

    # --- messaging ---------------------------------------------------------------------
    def send(self, recipient: str, task: str, **payload: Any) -> Message:
        self.steps += 1
        if self.steps > self.ctx.settings.max_steps:
            raise LimitExceeded(f"step limit {self.ctx.settings.max_steps} reached - possible loop, run stopped")
        self.ctx.check_deadline()
        msg = Message(sender=self.name, recipient=recipient, intent="task", task=task, payload=payload)
        self.ctx.board.record(msg)
        self.ctx.logger.log("message", self.name, msg_id=msg.id, to=recipient, task=task,
                            payload={k: v for k, v in payload.items() if k not in ("load", "timeline")})
        t0 = time.perf_counter()
        reply = self.agents[recipient].handle(msg)
        self.ctx.board.record(reply)
        self.ctx.logger.log("message", recipient, msg_id=reply.id, to=self.name, task=task, intent=reply.intent,
                            reply_to=msg.id, payload=_brief_payload(reply.payload))
        self.timeline.append({"step": self.steps, "agent": recipient, "task": task,
                              "status": "ok" if reply.ok else "error", "brief": _brief(reply.payload),
                              "ms": round((time.perf_counter() - t0) * 1000), "payload": reply.payload})
        return reply

    def plan(self) -> list[str]:
        return ["data.load_data", "constraints.parse_constraints", "scheduler.plan_chunks", "rooms.init_capacity",
                "for each chunk: rooms.allocate_quota -> genetic.evolve (first attempt) -> scheduler.solve_chunk -> "
                "validator.validate_chunk (negotiator.resolve on failure, max N rounds)", "rooms.assign_rooms",
                "validator.validate_all <-> negotiator.repair (max N rounds)", "reporter.export"]

    # --- the run -----------------------------------------------------------------------
    def run(self, requirements: list[str]) -> dict:
        st = self.ctx.settings
        self.ctx.board.put("plan", self.plan(), writer=self.name)
        self.ctx.logger.log("info", self.name, msg=f"plan: {len(self.plan())} stages, LLM={self.ctx.llm.provider}")

        r = self.send("data", "load_data")
        if not r.ok:
            return self._abort(f"Не удалось загрузить данные: {r.payload['error']}")
        r = self.send("constraints", "parse_constraints", texts=requirements)
        if not r.ok:
            return self._abort(f"Не удалось разобрать ограничения: {r.payload['error']}")
        r = self.send("scheduler", "plan_chunks")
        if not r.ok:
            return self._abort(f"Не удалось разбить задачу: {r.payload['error']}")
        n_chunks = r.payload["chunks"]
        r = self.send("rooms", "init_capacity")
        if not r.ok:
            return self._abort(f"Не удалось посчитать аудиторный фонд: {r.payload['error']}")

        failed_chunks = []
        for i in range(n_chunks):
            for attempt in range(st.max_negotiation_rounds + 1):
                self.send("rooms", "allocate_quota", chunk=i)
                if attempt == 0 and st.ga_enabled:
                    self.send("genetic", "evolve", chunk=i)  # first generation; CP-SAT refines it
                res = self.send("scheduler", "solve_chunk", chunk=i)
                if res.ok and res.payload["ok"]:
                    val = self.send("validator", "validate_chunk", chunk=i)
                    if val.ok and val.payload["hard_violations"] == 0:
                        break
                    status = "VALIDATION_FAILED"
                    diag = [v["detail"] for v in val.payload.get("violations", [])][:10] or [val.payload.get("error", "")]
                else:
                    status = res.payload.get("status", "ERROR")
                    diag = res.payload.get("diagnostics") or [res.payload.get("error", "unknown error")]
                if attempt == st.max_negotiation_rounds:
                    failed_chunks.append(i)
                    break
                dec = self.send("negotiator", "resolve", chunk=i, status=status, diagnostics=diag)
                if not dec.ok or dec.payload["action"] == "give_up":
                    failed_chunks.append(i)
                    break

        self.send("rooms", "assign_rooms")
        for _ in range(st.max_negotiation_rounds):
            val = self.send("validator", "validate_all")
            if not val.ok or val.payload["hard_violations"] == 0:
                break
            rep = self.send("negotiator", "repair", violations=val.payload["violations"])
            if not rep.ok or rep.payload["moved"] == 0:
                break
        final = self.send("validator", "validate_all", final=True)

        load = self.ctx.logger.load_report()
        timeline = [{k: v for k, v in e.items() if k != "payload"} for e in self.timeline]
        exp = self.send("reporter", "export", out=str(self.ctx.out_dir), load=load, timeline=timeline)
        return {
            "status": "ok" if final.ok and final.payload["hard_violations"] == 0 and not failed_chunks else "partial",
            "hard_violations": final.payload.get("by_type") if final.ok else None,
            "soft": final.payload.get("soft") if final.ok else None,
            "failed_chunks": failed_chunks, "steps": self.steps, "load": load,
            "files": exp.payload.get("files", []) if exp.ok else [], "llm": self.ctx.llm.provider,
        }

    def _abort(self, reason: str) -> dict:
        self.ctx.logger.log("error", self.name, msg=reason)
        return {"status": "failed", "reason": reason, "steps": self.steps, "load": self.ctx.logger.load_report()}


def _brief(payload: dict) -> str:
    if "error" in payload:
        return f"ERROR: {str(payload['error'])[:120]}"
    keys = ["status", "action", "hard_violations", "chunks", "sessions", "constraints", "mode", "moved",
            "assigned", "wall_s", "generations", "hard"]
    return ", ".join(f"{k}={payload[k]}" for k in keys if k in payload)[:160]


def _brief_payload(payload: dict) -> dict:
    return {k: (v if len(str(v)) < 400 else str(v)[:400] + "...") for k, v in payload.items()}
