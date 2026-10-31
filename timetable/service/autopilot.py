"""Autopilot: the service orchestrator that keeps the timetable up to date 24/7.

Every ``tick`` seconds it routes typed messages, exactly like the pipeline orchestrator, and does
no domain work itself:

1. a job is running        -> wait for it; when it ends: publisher.review (publish or keep back);
2. watcher.scan            -> data fingerprint + ids of new staff requests;
3. intake.triage           -> a verdict for every new request;
4. watcher.propose         -> generate / regenerate / improve / wait;
5. orchestrator.generate   -> a normal multi-agent pipeline run (through the RunManager, so the
                              web UI shows it like any other run).

Logs: ``runs/_service/events-YYYYMMDD.jsonl`` (every record carries the tick number);
messages: ``runs/_service/state.sqlite``; durable state: ``runs/_service/service.sqlite``.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..config import Settings
from ..core.errors import LimitExceeded
from ..core.llm import LLMRouter
from ..core.messages import Message
from ..core.runlog import RunLogger
from ..core.state import Blackboard
from .agents import SERVICE_AGENT_CLASSES, ServiceContext
from .store import ServiceStore

KEEP_MESSAGES_S = 7 * 24 * 3600


class Autopilot:
    name = "autopilot"
    role = ("Service orchestrator: runs around the clock, routes typed messages between the Watcher, Intake, "
            "the generation pipeline and the Publisher; does no domain work itself.")
    done_when = "never - it runs until stopped; every tick ends with a recorded state (idle, busy, started, error)"

    def __init__(self, manager: Any, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.root: Path = self.settings.out_root / "_service"
        self.store = ServiceStore(self.root)
        self.manager = manager  # web.runs.RunManager (or a test double with start/status/active)
        self.tick_s = self.settings.autopilot_tick
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick: float | None = None
        self.last_result: dict | None = None

    # --- lifecycle --------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self.store.set("enabled", True)
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="autopilot", daemon=True)
        self._thread.start()

    def stop(self, persist: bool = True) -> None:
        if persist:
            self.store.set("enabled", False)
        self._stop.set()

    def resume_if_enabled(self) -> None:
        mode = self.settings.autopilot
        if mode == "on" or (mode == "auto" and self.store.get("enabled", False)):
            self.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.tick_s)

    # --- one cycle ----------------------------------------------------------------------------
    @contextmanager
    def _session(self) -> Iterator["_Session"]:
        """Fresh context per tick: today's log file, the service blackboard and the agents."""
        n = int(self.store.get("tick_no", 0)) + 1
        self.store.set("tick_no", n)
        day = time.strftime("%Y%m%d")
        logger = RunLogger(self.root / f"events-{day}.jsonl", "service", echo=False)
        logger.extra = {"tick": n}
        board = Blackboard(self.root / "state.sqlite")
        if self.store.get("pruned_day") != day:
            board.prune_messages(time.time() - KEEP_MESSAGES_S)
            self.store.set("pruned_day", day)
        prof = self.store.profile()
        st = self.settings.with_(run_timeout=float("inf"), llm_provider=prof["llm"])
        llm = LLMRouter(st.llm_provider, st.llm_timeout, logger, claude_model=st.llm_model, openai_model=st.openai_model)
        ctx = ServiceContext(settings=st, board=board, logger=logger, llm=llm, out_dir=self.root, store=self.store)
        s = _Session(self.name, ctx, {cls.name: cls(ctx) for cls in SERVICE_AGENT_CLASSES}, n, self.start_pipeline)
        try:
            yield s
        finally:
            logger.close()
            board.close()

    def tick(self) -> dict:
        """One routing cycle; safe to call from the loop, the CLI (--once) or the web button."""
        with self._lock:
            self.last_tick = time.time()
            with self._session() as s:
                try:
                    result = self._route(s)
                except LimitExceeded as exc:
                    result = {"state": "error", "reason": str(exc)}
                except Exception as exc:  # keep the service alive; the error is logged and shown in the UI
                    s.ctx.logger.log("error", self.name, msg=f"{type(exc).__name__}: {exc}",
                                     trace=traceback.format_exc(limit=4))
                    result = {"state": "error", "reason": f"{type(exc).__name__}: {exc}"}
                s.ctx.logger.log("info", self.name, msg=f"tick {s.n}: {result['state']}", outcome=result)
            self.last_result = {**result, "tick": s.n, "ts": self.last_tick}
            return self.last_result

    def _route(self, s: "_Session") -> dict:
        job = self.store.active_job()
        if job:
            try:
                status = self.manager.status(job["run_id"])["status"] if job["run_id"] else "missing"
            except (FileNotFoundError, ValueError):
                status = "missing"
            if status in ("running", "cancelling"):
                return {"state": "busy", "job": job["id"], "run_id": job["run_id"]}
            if status == "missing":
                self.store.update_job(job["id"], status="failed", finished=time.time(),
                                      outcome={"reason": "папка прогона удалена до проверки"})
                return {"state": "error", "reason": f"job {job['id']}: run folder is gone"}
            r = s.send("publisher", "review", job_id=job["id"], run_dir=str(self.settings.out_root / job["run_id"]))
            if not r.ok:
                self.store.update_job(job["id"], status="failed", finished=time.time(), outcome={"reason": r.payload["error"]})
            return {"state": "reviewed", "job": job["id"], **{k: r.payload.get(k) for k in ("status", "reason", "version")}}

        scan = s.send("watcher", "scan")
        if not scan.ok:
            return {"state": "error", "reason": scan.payload["error"]}
        for rid in scan.payload["new_requests"]:
            s.send("intake", "triage", request_id=rid)
        prop = s.send("watcher", "propose", fingerprint=scan.payload["fingerprint"])
        if not prop.ok:
            return {"state": "error", "reason": prop.payload["error"]}
        p = prop.payload
        if p["kind"] == "wait":
            return {"state": "idle", "reason": p["reason"]}
        if self.manager.active() is not None:
            return {"state": "deferred", "reason": "идёт ручной прогон - задание подождёт"}
        jid = self.store.add_job(p["kind"], p["reason"], p["params"])
        r = s.send("orchestrator", "generate", job=jid, **{k: v for k, v in p["params"].items() if k != "requirements"},
                   requirements=len(p["params"]["requirements"]))
        if not r.ok:
            self.store.update_job(jid, status="failed", finished=time.time(), outcome={"reason": r.payload["error"]})
            return {"state": "error", "reason": r.payload["error"]}
        self.store.update_job(jid, status="running", run_id=r.payload["run_id"])
        return {"state": "started", "job": jid, "kind": p["kind"], "reason": p["reason"], "run_id": r.payload["run_id"]}

    # --- the pipeline as a message recipient ---------------------------------------------------
    def start_pipeline(self, job: int, **_: Any) -> dict:
        params = dict(self.store.job(job)["params"])
        params["name"] = f"auto-{self.store.job(job)['kind']}-{job}"
        return {"run_id": self.manager.start(params).run_id}

    # --- read side for the UI -----------------------------------------------------------------
    def status(self) -> dict:
        cur = self.store.current_version()
        counts: dict[str, int] = {}
        for r in self.store.requests():
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"enabled": bool(self.store.get("enabled", False)), "running": self.running, "tick_s": self.tick_s,
                "mode": self.settings.autopilot, "last_tick": self.last_tick,
                "next_tick": (self.last_tick + self.tick_s) if self.running and self.last_tick else None,
                "last_result": self.last_result, "job": self.store.active_job(), "jobs": self.store.jobs(20),
                "current": _brief_version(cur), "versions": [_brief_version(v) for v in self.store.versions(12)],
                "requests": counts, "profile": self.store.profile(), "standing": self.store.standing_requirements(),
                "cycles": self.cycles(), "load": self.load()}

    def brief(self) -> dict:
        """Small status for the sidebar (polled every few seconds)."""
        job, cur = self.store.active_job(), self.store.current_version()
        active = self.manager.active()
        open_requests = len(self.store.requests(("new", "review")))
        return {"enabled": bool(self.store.get("enabled", False)), "running": self.running,
                "next_tick": (self.last_tick + self.tick_s) if self.running and self.last_tick else None,
                "last_result": self.last_result, "open_requests": open_requests,
                "job": {k: job[k] for k in ("id", "kind", "reason", "run_id", "status")} if job else None,
                "current": {"id": cur["id"], "ts": cur["ts"], "score": cur["score"]} if cur else None,
                "active_run": active.run_id if active else None, "dataset": self.store.profile()["dataset"]}

    def _log_lines(self, days: int = 2, tail: int = 4000) -> list[dict]:
        files = sorted(self.root.glob("events-*.jsonl"))[-days:]
        out = []
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines()[-tail:]:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def cycles(self, limit: int = 40) -> list[dict]:
        """Recent ticks, newest first. Quiet ticks (state idle/busy) are listed without their events."""
        ticks: dict[int, dict] = {}
        for e in self._log_lines():
            t = e.get("tick")
            if t is None:
                continue
            c = ticks.setdefault(t, {"tick": t, "ts": e["ts"], "events": []})
            c["events"].append(e)
            if e["kind"] == "info" and e["agent"] == self.name:
                c["outcome"] = e.get("outcome") or {}
        out = []
        for t in sorted(ticks, reverse=True)[:limit]:
            c = ticks[t]
            state = (c.get("outcome") or {}).get("state", "running")
            quiet = state in ("idle", "busy")
            out.append({"tick": t, "ts": c["ts"], "state": state, "outcome": c.get("outcome") or {},
                        "events": [] if quiet else c["events"][-60:], "n_events": len(c["events"])})
        return out

    def load(self) -> dict:
        """Tool + LLM calls per service agent over the last two days of logs."""
        calls: dict[str, int] = {}
        for e in self._log_lines():
            if e["kind"] in ("tool_call", "llm_call") and e.get("attempt", 1) == 1:
                calls[e["agent"]] = calls.get(e["agent"], 0) + 1
        total = sum(calls.values()) or 1
        return {"total_calls": sum(calls.values()), "agents": {a: {"calls": n, "share": round(n / total, 3)}
                                                               for a, n in sorted(calls.items(), key=lambda kv: -kv[1])}}

    def close(self) -> None:
        self.stop(persist=False)
        if self._thread:
            self._thread.join(timeout=5)
        self.store.close()


class _Session:
    """Message routing inside one tick (mirrors Orchestrator.send): record, log, deliver, record reply."""

    def __init__(self, sender: str, ctx: ServiceContext, agents: dict, n: int, pipeline: Any):
        self.sender, self.ctx, self.agents, self.n, self.pipeline = sender, ctx, agents, n, pipeline

    def send(self, recipient: str, task: str, **payload: Any) -> Message:
        msg = Message(sender=self.sender, recipient=recipient, intent="task", task=task, payload=payload)
        self.ctx.board.record(msg)
        self.ctx.logger.log("message", self.sender, msg_id=msg.id, to=recipient, task=task, payload=payload)
        if recipient == "orchestrator":  # the generation pipeline (started through the run manager)
            try:
                reply = msg.reply(self.pipeline(**payload))
            except Exception as exc:  # e.g. 409: a manual run started in between
                reply = msg.reply(error=f"{type(exc).__name__}: {exc}")
        else:
            reply = self.agents[recipient].handle(msg)
        self.ctx.board.record(reply)
        self.ctx.logger.log("message", recipient, msg_id=reply.id, to=self.sender, task=task, intent=reply.intent,
                            reply_to=msg.id, payload={k: (v if len(str(v)) < 400 else str(v)[:400] + "...")
                                                      for k, v in reply.payload.items()})
        return reply


def _brief_version(v: dict | None) -> dict | None:
    if not v:
        return None
    m = v.get("metrics") or {}
    d = v.get("diff") or {}
    return {"id": v["id"], "ts": v["ts"], "run_id": v["run_id"], "job_id": v["job_id"], "score": v["score"],
            "announcement": v["announcement"], "kind": m.get("kind"), "term": m.get("term"),
            "programs": m.get("programs") or [], "requirements": m.get("requirements") or [],
            "soft": {k: m.get(k) for k in ("gap_hours_per_group", "gap_hours_total", "late_hours_18_plus",
                                           "saturday_hours", "precedence_violations", "lectures_in_lecture_halls")},
            "diff": {k: d.get(k) for k in ("first", "hours_moved", "room_changes", "groups_affected", "total_hours")}}
