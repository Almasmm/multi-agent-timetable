"""Typed messages exchanged between agents (serialised as JSON in the state store and logs)."""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["task", "result", "error"]


class Message(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = Field(default_factory=time.time)
    sender: str
    recipient: str
    intent: Intent
    task: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None  # id of the task message a result answers

    def reply(self, payload: dict[str, Any] | None = None, *, error: str | None = None) -> "Message":
        return Message(
            sender=self.recipient,
            recipient=self.sender,
            intent="error" if error else "result",
            task=self.task,
            payload={"error": error, **(payload or {})} if error else (payload or {}),
            correlation_id=self.id,
        )

    @property
    def ok(self) -> bool:
        return self.intent == "result"
