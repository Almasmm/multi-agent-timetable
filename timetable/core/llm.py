"""LLM access with graceful degradation.

* ``OpenAILLM`` - OpenAI models via the official SDK (chat completions, strict JSON schema).
* ``ClaudeLLM`` - Claude via the official Anthropic SDK, structured JSON outputs.
* ``OfflineLLM`` - deterministic rule-based fallback, so the system (and all tests) run
  without an API key or network.

Agents call :meth:`LLMRouter.structured` / :meth:`LLMRouter.text`, always passing an
``offline`` callable. If the primary provider is unavailable or fails after retries the
router uses the offline result and logs the degradation - the run never crashes because of
the LLM. API keys are read by the SDKs from the environment (``OPENAI_API_KEY`` /
``ANTHROPIC_API_KEY``, usually from ``.env``); they are never stored in code.
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
    additionalProperties=false, every property required). Accepted by both OpenAI and
    Anthropic strict modes."""
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


class OpenAILLM:
    """OpenAI chat completions with ``response_format=json_schema`` (strict)."""

    name = "openai"
    REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")  # models that accept reasoning_effort

    def __init__(self, model: str, timeout: float, client: Any = None):
        self.model = model
        if client is None:
            import openai  # imported lazily: offline mode works without the package

            client = openai.OpenAI(timeout=timeout, max_retries=2)
        self.client = client

    def _create(self, system: str, user: str, effort: str, fmt: dict | None) -> tuple[str, dict]:
        kwargs: dict[str, Any] = {
            "model": self.model, "max_completion_tokens": 8000,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.model.startswith(self.REASONING_PREFIXES):
            kwargs["reasoning_effort"] = effort
        if fmt:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "result", "schema": fmt, "strict": True}}
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise RuntimeError(f"model refused the request: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise RuntimeError("response truncated (max_completion_tokens)")
        usage = {"tokens_in": resp.usage.prompt_tokens, "tokens_out": resp.usage.completion_tokens}
        return choice.message.content or "", usage

    def structured(self, system: str, user: str, schema: type[T], effort: str) -> tuple[T, dict]:
        text, usage = self._create(system, user, effort, strict_schema(schema))
        return schema.model_validate(json.loads(text)), usage

    def text(self, system: str, user: str, effort: str) -> tuple[str, dict]:
        return self._create(system, user, effort, None)


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


def _has_openai_credentials() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _has_claude_credentials() -> bool:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"):
        return True
    home = os.path.expanduser("~/.config/anthropic")
    return os.path.isdir(home)


def resolve_provider(provider: str) -> tuple[str, str]:
    """``auto`` -> the first provider with credentials (OpenAI, then Claude, else offline).
    Returns (resolved provider, reason when it is not the requested one)."""
    if provider == "auto":
        if _has_openai_credentials():
            return "openai", ""
        if _has_claude_credentials():
            return "claude", ""
        return "offline", "no OPENAI_API_KEY / Anthropic credentials found - using offline rules"
    if provider == "openai" and not _has_openai_credentials():
        return "offline", "OPENAI_API_KEY is not set - using offline rules"
    if provider in ("openai", "claude"):
        return provider, ""
    return "offline", "offline mode requested"


class LLMRouter:
    """Single entry point used by agents. Logs every LLM call against the calling agent."""

    def __init__(self, provider: str, timeout: float, logger: RunLogger, claude_model: str = "claude-opus-5-5",
                 openai_model: str = "gpt-5-mini"):
        self.logger = logger
        self.primary: OpenAILLM | ClaudeLLM | None = None
        resolved, self.disabled_reason = resolve_provider(provider)
        try:
            if resolved == "openai":
                self.primary = OpenAILLM(openai_model, timeout)
            elif resolved == "claude":
                self.primary = ClaudeLLM(claude_model, timeout)
        except Exception as exc:  # package missing, bad config ...
            self.disabled_reason = f"{resolved} client unavailable: {exc}"

    @property
    def provider(self) -> str:
        return f"{self.primary.name}/{self.primary.model}" if self.primary else OfflineLLM.name

    def _run(self, agent: str, name: str, fn: Callable[[], tuple[Any, dict]], offline: Callable[[], Any]) -> Any:
        if self.primary is not None:
            t0 = time.perf_counter()
            try:
                result, usage = fn()
                self.logger.log("llm_call", agent, name=name, provider=self.primary.name, model=self.primary.model,
                                status="ok", duration_ms=round((time.perf_counter() - t0) * 1000), **usage)
                return result
            except Exception as exc:  # degrade, never crash the run
                self.logger.log("llm_call", agent, name=name, provider=self.primary.name, model=self.primary.model,
                                status="failed", duration_ms=round((time.perf_counter() - t0) * 1000),
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
