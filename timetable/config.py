"""Run settings. Everything is overridable from environment variables / .env so the
same code adapts to another university, term or hardware budget without edits."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

try:  # optional: python-dotenv
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
DATASET_DIRS = [ROOT / "data", ROOT / "dataset-archive", ROOT / "data" / "uploads"]  # where workbooks are looked up


def _env(name: str, default, cast=str):
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    if cast is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return cast(raw)


@dataclass(frozen=True)
class Settings:
    # --- data / problem ---
    data_path: Path = field(default_factory=lambda: Path(_env("TT_DATA", str(ROOT / "data" / "input.xlsx"))))
    term: int = field(default_factory=lambda: _env("TT_TERM", 1, int))  # 1..3 inside the academic year
    weeks_per_term: int = field(default_factory=lambda: _env("TT_WEEKS", 10, int))
    programs: tuple[str, ...] = field(
        default_factory=lambda: tuple(p for p in _env("TT_PROGRAMS", "").split(",") if p.strip())
    )  # empty = all programs
    slots_per_day: int = 12  # 08:00 .. 19:50, 50-minute academic hours
    first_hour: int = 8
    allow_saturday: bool = field(default_factory=lambda: _env("TT_ALLOW_SATURDAY", False, bool))
    lecture_stream_size: int = field(default_factory=lambda: _env("TT_STREAM", 2, int))
    online_stream_size: int = field(default_factory=lambda: _env("TT_ONLINE_STREAM", 50, int))
    online_scope: str = field(default_factory=lambda: _env("TT_ONLINE_SCOPE", "lecture"))  # lecture | all
    gym_capacity: int = field(default_factory=lambda: _env("TT_GYM_CAPACITY", 8, int))
    max_hours_per_day: int = field(default_factory=lambda: _env("TT_MAX_HOURS_DAY", 8, int))
    # --- decomposition / solver ---
    chunk_target_groups: int = field(default_factory=lambda: _env("TT_CHUNK_GROUPS", 60, int))
    solver_time_limit: float = field(default_factory=lambda: _env("TT_SOLVER_SECONDS", 25.0, float))
    quota_slack: float = field(default_factory=lambda: _env("TT_QUOTA_SLACK", 1.15, float))
    solver_workers: int = field(default_factory=lambda: _env("TT_SOLVER_WORKERS", 8, int))
    ga_enabled: bool = field(default_factory=lambda: _env("TT_GA", True, bool))  # GA first generation -> CP-SAT hint
    ga_seconds: float = field(default_factory=lambda: _env("TT_GA_SECONDS", 4.0, float))
    ga_population: int = field(default_factory=lambda: _env("TT_GA_POPULATION", 12, int))
    seed: int = field(default_factory=lambda: _env("TT_SEED", 7, int))
    # warm start: starts.json of the published version; moving a session away from it costs stability_weight
    warm_start: Path | None = field(default_factory=lambda: Path(_env("TT_WARM_START", "")) if _env("TT_WARM_START", "") else None)
    stability_weight: int = field(default_factory=lambda: _env("TT_STABILITY_WEIGHT", 6, int))
    # --- reliability limits ---
    max_steps: int = field(default_factory=lambda: _env("TT_MAX_STEPS", 150, int))
    run_timeout: float = field(default_factory=lambda: _env("TT_RUN_TIMEOUT", 1800.0, float))
    tool_timeout: float = field(default_factory=lambda: _env("TT_TOOL_TIMEOUT", 300.0, float))
    tool_retries: int = field(default_factory=lambda: _env("TT_TOOL_RETRIES", 2, int))
    max_negotiation_rounds: int = field(default_factory=lambda: _env("TT_MAX_NEGOTIATION", 4, int))
    # --- LLM ---
    llm_provider: str = field(default_factory=lambda: _env("TT_LLM", "auto"))  # auto | openai | claude | offline
    llm_model: str = field(default_factory=lambda: _env("TT_LLM_MODEL", "claude-opus-5-5"))  # Claude model
    openai_model: str = field(default_factory=lambda: _env("TT_OPENAI_MODEL", "gpt-5-mini"))
    llm_timeout: float = field(default_factory=lambda: _env("TT_LLM_TIMEOUT", 90.0, float))
    # --- output ---
    out_root: Path = field(default_factory=lambda: Path(_env("TT_OUT", str(ROOT / "runs"))))
    # --- 24/7 service (autopilot) ---
    autopilot: str = field(default_factory=lambda: _env("TT_AUTOPILOT", "auto"))  # auto (resume saved state) | on | off
    autopilot_tick: float = field(default_factory=lambda: _env("TT_AUTOPILOT_TICK", 60.0, float))
    term_start: str = field(default_factory=lambda: _env("TT_TERM_START", "2026-09-07"))  # first Monday, for .ics export

    @property
    def days(self) -> list[str]:
        return DAYS if self.allow_saturday else DAYS[:5]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)
