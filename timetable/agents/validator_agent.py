from __future__ import annotations

import json

from ..core.agent import BaseAgent
from ..solver.checks import check
from .common import H, chunk_sessions, constraint_index, days


class ValidatorAgent(BaseAgent):
    name = "validator"
    role = ("Critic: independently re-checks every chunk and the final timetable against all hard "
            "constraints and scores soft quality; only validated chunks are committed.")
    done_when = "a verdict (hard violations list + soft metrics) exists for the checked artefact"

    def register_tools(self) -> None:
        self.add_tool("check", lambda **kw: check(**kw), "independent hard/soft constraint checker",
                      retryable=False)

    def on_validate_chunk(self, chunk: int) -> dict:
        sessions = chunk_sessions(self.ctx, chunk)
        starts = self.require(f"solution:{chunk}")
        rep = self.call_tool("check", sessions=sessions, starts=starts, rooms_of=None,
                             rooms=self.require("dataset").rooms, cindex=constraint_index(self.ctx),
                             days=days(self.ctx, chunk), H=H, quotas=self.require(f"quota:{chunk}"), caps=self.require("caps"))
        if rep["hard_violations"] == 0:
            self.put(f"validated:{chunk}", True)
            merged = dict(self.get("starts", {}))
            merged.update(starts)
            self.put("starts", merged)
        return {"hard_violations": rep["hard_violations"], "by_type": rep["by_type"],
                "violations": rep["violations"][:20], "soft": rep["soft"]}

    def on_validate_all(self, final: bool = False) -> dict:
        rep = self.call_tool("check", sessions=self.require("sessions"), starts=self.get("starts", {}),
                             rooms_of=self.get("rooms_of", {}), rooms=self.require("dataset").rooms,
                             cindex=constraint_index(self.ctx), days=days(self.ctx), H=H)
        self.put("validation", rep)
        if final:
            critique = self.ctx.llm.text(
                self.name, "critique",
                system="You are a strict timetable quality reviewer. Given validation metrics, write 3-5 bullet "
                       "points in Russian: verdict (accept/reject), main weaknesses, one concrete improvement.",
                user=json.dumps({"hard_violations": rep["by_type"], "soft": rep["soft"]}, ensure_ascii=False),
                offline=lambda: _offline_critique(rep))
            self.put("critique", critique)
        return {"hard_violations": rep["hard_violations"], "by_type": rep["by_type"],
                "violations": rep["violations"][:50], "soft": rep["soft"]}


def _offline_critique(rep: dict) -> str:
    s = rep["soft"]
    verdict = "ПРИНЯТО" if rep["hard_violations"] == 0 else f"ОТКЛОНЕНО ({rep['hard_violations']} жёстких нарушений)"
    return "\n".join([
        f"- Вердикт: {verdict}.",
        f"- Окна: {s['gap_hours_total']} ч всего, {s['gap_hours_per_group']} ч на группу в неделю.",
        f"- Поздние пары (18:00+): {s['late_hours_18_plus']} ч; суббота: {s['saturday_hours']} ч.",
        f"- Нарушений порядка «лекция раньше практики»: {s['precedence_violations']}; "
        f"лекции в лекционных залах: {s['lectures_in_lecture_halls']}.",
    ])
