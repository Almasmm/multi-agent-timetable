"""LLM access with graceful degradation.

* ``ClaudeLLM`` - Claude via the official Anthropic SDK, structured JSON outputs.
* ``OfflineLLM`` - deterministic rule-based fallback, so the system (and all tests) run
  without an API key or network.

Agents call :meth:`LLMRouter.structured` / :meth:`LLMRouter.text`, always passing an
``offline`` callable. If the primary provider is unavailable or fails after retries the
router uses the offline result and logs the degradation - the run never crashes because of
the LLM. The API key is read by the SDK from the environment (``ANTHROPIC_API_KEY`` or an
``ant auth login`` profile); it is never stored in code.
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from .runlog import RunLogger

T = TypeVar("T", bound=BaseModel)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> strict schema for structured outputs (refs inlined,
    additionalProperties=false, every property required)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


class ClaudeLLM:
    name = "claude"
    _BETAS = ["server-side-fallback-2026-07-01"]

    def __init__(self, model: str, timeout: float):
        import anthropic  # imported lazily: offline mode works without the package

        self._anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=2)

    def _create(self, system: str, user: str, effort: str, fmt: dict | None):
        output_config: dict[str, Any] = {"effort": effort}
        if fmt:
            output_config["format"] = fmt
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=self._BETAS,
            fallbacks="default",  # server-side fallback on a policy refusal
            output_config=output_config,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused the request")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("response truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        usage = {"tokens_in": resp.usage.input_tokens, "tokens_out": resp.usage.output_tokens}
        return text, usage

    def structured(self, system: str, user: str, schema: type[T], effort: str) -> tuple[T, dict]:
        text, usage = self._create(system, user, effort, {"type": "json_schema", "schema": strict_schema(schema)})
        return schema.model_validate(json.loads(text)), usage

    def text(self, system: str, user: str, effort: str) -> tuple[str, dict]:
        return self._create(system, user, effort, None)


class OfflineLLM:
    name = "offline-rules"


def _has_claude_credentials() -> bool:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"):
        return True
    home = os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(home)


class LLMRouter:
    """Single entry point used by agents. Logs every LLM call against the calling agent."""

    def __init__(self, provider: str, model: str, timeout: float, logger: RunLogger):
        self.logger = logger
        self.primary: ClaudeLLM | None = None
        self.disabled_reason = ""
        if provider in ("claude", "auto"):
            if provider == "auto" and not _has_claude_credentials():
                self.disabled_reason = "no Anthropic credentials found - using offline rules"
            else:
                try:
                    self.primary = ClaudeLLM(model, timeout)
                except Exception as exc:  # package missing, bad config ...
                    self.disabled_reason = f"Claude client unavailable: {exc}"
        else:
            self.disabled_reason = "offline mode requested"

    @property
    def provider(self) -> str:
        return self.primary.name if self.primary else OfflineLLM.name

    def _run(self, agent: str, name: str, fn: Callable[[], tuple[Any, dict]], offline: Callable[[], Any]) -> Any:
        if self.primary is not None:
            t0 = time.perf_counter()
            try:
                result, usage = fn()
                self.logger.log("llm_call", agent, name=name, provider=self.primary.name, status="ok",
                                duration_ms=round((time.perf_counter() - t0) * 1000), **usage)
                return result
            except Exception as exc:  # degrade, never crash the run
                self.logger.log("llm_call", agent, name=name, provider=self.primary.name, status="failed",
                                duration_ms=round((time.perf_counter() - t0) * 1000),
                                error=f"{type(exc).__name__}: {exc}")
                self.logger.log("info", agent, msg=f"LLM failed, falling back to offline rules for '{name}'",
                                attempt=2)
                result = offline()
                self.logger.log("llm_call", agent, name=name, provider=OfflineLLM.name, status="fallback", attempt=2)
                return result
        result = offline()
        self.logger.log("llm_call", agent, name=name, provider=OfflineLLM.name, status="ok")
        return result

    def structured(self, agent: str, name: str, system: str, user: str, schema: type[T],
                   offline: Callable[[], T], effort: str = "low") -> T:
        return self._run(agent, name, lambda: self.primary.structured(system, user, schema, effort), offline)

    def text(self, agent: str, name: str, system: str, user: str, offline: Callable[[], str],
             effort: str = "low") -> str:
        return self._run(agent, name, lambda: self.primary.text(system, user, effort), offline)
