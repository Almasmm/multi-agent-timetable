"""Wires the agents together and runs one timetable generation."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .agents.constraint_agent import ConstraintAgent
from .agents.data_agent import DataAgent
from .agents.genetic_agent import GeneticAgent
from .agents.negotiator_agent import NegotiatorAgent
from .agents.orchestrator import Orchestrator
from .agents.reporter_agent import ReporterAgent
from .agents.room_agent import RoomAgent
from .agents.scheduler_agent import SchedulerAgent
from .agents.validator_agent import ValidatorAgent
from .config import Settings
from .core.agent import RunContext
from .core.errors import LimitExceeded
from .core.llm import LLMRouter
from .core.runlog import RunLogger
from .core.state import Blackboard

AGENT_CLASSES = [DataAgent, ConstraintAgent, GeneticAgent, SchedulerAgent, RoomAgent, ValidatorAgent,
                 NegotiatorAgent, ReporterAgent]


def build(settings: Settings, run_name: str | None = None, echo: bool = True,
          cancel: threading.Event | None = None) -> tuple[Orchestrator, RunContext]:
    run_id = run_name or time.strftime("%Y%m%d-%H%M%S")
    out = settings.out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out / "events.jsonl", run_id, echo=echo)
    llm = LLMRouter(settings.llm_provider, settings.llm_timeout, logger, claude_model=settings.llm_model,
                    openai_model=settings.openai_model)
    if llm.disabled_reason:
        logger.log("info", "system", msg=llm.disabled_reason)
    ctx = RunContext(settings=settings, board=Blackboard(out / "state.sqlite"), logger=logger, llm=llm, out_dir=out,
                     cancel=cancel or threading.Event())
    agents = {cls.name: cls(ctx) for cls in AGENT_CLASSES}
    return Orchestrator(ctx, agents), ctx


def run(settings: Settings, requirements: list[str], run_name: str | None = None, echo: bool = True,
        cancel: threading.Event | None = None) -> dict:
    orch, ctx = build(settings, run_name, echo, cancel)
    t0 = time.perf_counter()
    try:
        result = orch.run(requirements)
    except LimitExceeded as exc:
        ctx.logger.log("error", "orchestrator", msg=str(exc))
        result = {"status": "stopped", "reason": str(exc), "steps": orch.steps, "load": ctx.logger.load_report()}
    result["wall_s"] = round(time.perf_counter() - t0, 1)
    result["out_dir"] = str(ctx.out_dir)
    result["requirements"] = requirements
    result["settings"] = {"data_path": str(settings.data_path), "term": settings.term, "programs": list(settings.programs),
                          "allow_saturday": settings.allow_saturday, "solver_time_limit": settings.solver_time_limit,
                          "llm_provider": settings.llm_provider, "chunk_target_groups": settings.chunk_target_groups,
                          "ga_enabled": settings.ga_enabled, "ga_seconds": settings.ga_seconds, "seed": settings.seed,
                          "warm_start": str(settings.warm_start) if settings.warm_start else None}
    (Path(ctx.out_dir) / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                                                   encoding="utf-8")
    ctx.logger.close()
    ctx.board.close()
    return result


def agent_table() -> list[dict]:
    """Agent specification (role, tools, completion criterion) generated from the code itself."""
    from .core.agent import RunContext as _RC  # noqa: F401

    class _Dummy:
        settings = Settings()

    rows = [{"agent": Orchestrator.name, "role": Orchestrator.role, "done_when": Orchestrator.done_when,
             "tools": {"(none)": "routes typed messages; no domain tools"}}]
    for cls in AGENT_CLASSES:
        a = cls.__new__(cls)
        a.tools = {}
        a.ctx = _Dummy()
        cls.register_tools(a)
        rows.append(a.describe())
    return rows
