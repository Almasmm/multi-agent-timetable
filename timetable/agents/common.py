"""Small read-only helpers over the blackboard shared by several agents."""
from __future__ import annotations

from ..core.agent import RunContext
from ..domain.constraints import ConstraintIndex, ConstraintSpec

H = 12


def run_params(ctx: RunContext) -> dict:
    params = ctx.board.get("run_params")
    if params is None:
        params = {"allow_saturday": ctx.settings.allow_saturday, "gym_capacity": ctx.settings.gym_capacity,
                  "quota_mode": {}, "time_factor": {}, "tried": {}, "saturday_chunks": []}
        ctx.board.put("run_params", params, writer="system")
    return params


def days(ctx: RunContext, chunk: int | None = None) -> list[int]:
    """Teaching days. Saturday is opened globally by settings, or per chunk by the negotiator
    (a concession for one sub-problem must not spill over to the others)."""
    params = run_params(ctx)
    sat = params["saturday_chunks"]
    if params["allow_saturday"] or (sat if chunk is None else str(chunk) in sat):
        return list(range(6))
    return list(range(5))


def constraint_index(ctx: RunContext) -> ConstraintIndex:
    specs = [ConstraintSpec.model_validate(s) for s in ctx.board.get("constraint_specs", [])]
    return ConstraintIndex(specs, H, ctx.settings.first_hour, ctx.settings.max_hours_per_day)


def chunk_sessions(ctx: RunContext, i: int):
    units = set(ctx.board.require("chunks")[i])
    return [s for s in ctx.board.require("sessions") if s.unit in units]


def slot_label(ctx: RunContext, t: int) -> tuple[str, str]:
    from ..config import DAYS

    labels = ctx.board.require("dataset").slot_labels
    d, h = divmod(t, H)
    return DAYS[d], labels.get(h, f"{8 + h:02d}:00")
