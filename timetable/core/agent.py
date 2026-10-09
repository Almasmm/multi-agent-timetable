"""Agent base class: one role, at most 5 tools, logged + retried + time-boxed tool calls."""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from .errors import LimitExceeded, TimetableError, ToolError
from .llm import LLMRouter
from .messages import Message
from .runlog import RunLogger
from .state import Blackboard

MAX_TOOLS_PER_AGENT = 5


@dataclass
class RunContext:
    settings: Settings
    board: Blackboard
    logger: RunLogger
    llm: LLMRouter
    out_dir: Any
    started: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)  # set by the UI to stop a run

    def check_deadline(self) -> None:
        if self.cancel.is_set():
            raise LimitExceeded("run cancelled by the user")
        if time.monotonic() - self.started > self.settings.run_timeout:
            raise LimitExceeded(f"run exceeded the time limit of {self.settings.run_timeout:.0f} s")


@dataclass
class Tool:
    name: str
    fn: Callable[..., Any]
    description: str
    retryable: bool = True


class BaseAgent:
    name = "agent"
    role = ""
    done_when = ""  # completion criterion (documented in the agent table)

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.tools: dict[str, Tool] = {}
        self.register_tools()

    # --- tools -------------------------------------------------------------------------
    def register_tools(self) -> None:  # overridden by subclasses
        pass

    def add_tool(self, name: str, fn: Callable[..., Any], description: str, retryable: bool = True) -> None:
        if len(self.tools) >= MAX_TOOLS_PER_AGENT:
            raise ValueError(f"{self.name}: an agent may own at most {MAX_TOOLS_PER_AGENT} tools")
        self.tools[name] = Tool(name, fn, description, retryable)

    def call_tool(self, name: str, **kwargs: Any) -> Any:
        """Run a tool with timeout + retries (exponential backoff); every attempt is logged."""
        tool = self.tools[name]
        attempts = 1 + (self.ctx.settings.tool_retries if tool.retryable else 0)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            self.ctx.check_deadline()
            t0 = time.perf_counter()
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                result = pool.submit(tool.fn, **kwargs).result(timeout=self.ctx.settings.tool_timeout)
                self.ctx.logger.log("tool_call", self.name, name=name, status="ok", attempt=attempt,
                                    duration_ms=round((time.perf_counter() - t0) * 1000))
                return result
            except FutureTimeout:
                last_exc = ToolError(f"tool '{name}' timed out after {self.ctx.settings.tool_timeout:.0f} s")
            except (TimetableError, KeyError, ValueError) as exc:  # deterministic errors: no point retrying
                last_exc = exc
                attempts = attempt
            except Exception as exc:  # transient / unknown -> retry
                last_exc = exc
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            self.ctx.logger.log("tool_call", self.name, name=name, status="error", attempt=attempt,
                                duration_ms=round((time.perf_counter() - t0) * 1000),
                                error=f"{type(last_exc).__name__}: {last_exc}")
            if attempt < attempts:
                time.sleep(min(0.5 * 2 ** (attempt - 1), 4))
        raise ToolError(f"{self.name}.{name} failed after {attempts} attempt(s): {last_exc}") from last_exc

    # --- messaging ---------------------------------------------------------------------
    def handle(self, msg: Message) -> Message:
        handler = getattr(self, f"on_{msg.task}", None)
        if handler is None:
            return msg.reply(error=f"{self.name} cannot handle task '{msg.task}'")
        try:
            return msg.reply(handler(**msg.payload))
        except LimitExceeded:
            raise
        except Exception as exc:
            self.ctx.logger.log("error", self.name, msg=f"{msg.task}: {exc}", trace=traceback.format_exc(limit=4))
            return msg.reply(error=f"{type(exc).__name__}: {exc}")

    # --- helpers -----------------------------------------------------------------------
    def put(self, key: str, value: Any) -> None:
        self.ctx.board.put(key, value, writer=self.name)

    def get(self, key: str, default: Any = None) -> Any:
        return self.ctx.board.get(key, default)

    def require(self, key: str) -> Any:
        return self.ctx.board.require(key)

    def describe(self) -> dict[str, Any]:
        return {"agent": self.name, "role": self.role, "done_when": self.done_when,
                "tools": {t.name: t.description for t in self.tools.values()}}
