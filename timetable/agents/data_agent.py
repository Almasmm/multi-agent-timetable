from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..core.agent import BaseAgent
from ..domain.demand import build_sessions
from ..domain.loader import normalize, read_workbook


class DataAgent(BaseAgent):
    name = "data"
    role = "Reads the university workbook, repairs/flags dirty data and builds the weekly demand (sessions)."
    done_when = "dataset + sessions are on the blackboard and every data issue is listed"

    def register_tools(self) -> None:
        self.add_tool("read_workbook", lambda path: read_workbook(path), "Excel file -> raw sheet rows")
        self.add_tool("normalize", lambda sheets, weeks: normalize(sheets, weeks),
                      "validate + repair rows, return Dataset and issues", retryable=False)
        self.add_tool("build_demand", self._build_demand, "curricula x groups -> weekly sessions", retryable=False)
        self.add_tool("load_previous", _load_previous, "starts + rooms of the published version (warm start, JSON)")

    def _build_demand(self, ds):
        st = self.ctx.settings
        return build_sessions(ds, st.term, st.lecture_stream_size, st.online_stream_size, st.programs, st.online_scope)

    def on_load_data(self) -> dict:
        st = self.ctx.settings
        sheets = self.call_tool("read_workbook", path=st.data_path)
        ds = self.call_tool("normalize", sheets=sheets, weeks=st.weeks_per_term)
        sessions, summary = self.call_tool("build_demand", ds=ds)
        if not sessions:
            raise ValueError("no sessions to schedule: check TT_PROGRAMS / TT_TERM and the curriculum sheets")
        self.put("dataset", ds)
        self.put("sessions", sessions)
        self.put("demand_summary", summary)
        stats = {
            "programs": len({c.program for c in ds.courses}), "courses": len(ds.courses), "groups": len(ds.groups),
            "rooms": len(ds.rooms), "sessions": len(sessions),
            "offline_hours": sum(s.duration for s in sessions if not s.online),
            "online_hours": sum(s.duration for s in sessions if s.online),
            "issues": dict(Counter(i["level"] for i in ds.issues)),
        }
        explanation = self.ctx.llm.text(
            self.name, "explain_data_issues",
            system="You are a data steward at a university registrar office. In 3-5 short bullet points (Russian), "
                   "explain which input-data problems were found, what was auto-fixed and what a human must fix.",
            user=json.dumps({"stats": stats, "issues": ds.issues[:60]}, ensure_ascii=False),
            offline=lambda: _offline_issue_summary(ds.issues),
        )
        self.put("data_issues_text", explanation)
        if st.warm_start:  # stable re-generation: keep the published timetable where nothing forces a move
            starts, rooms = self.call_tool("load_previous", path=st.warm_start)
            ids = {s.id for s in sessions}
            self.put("warm_start", {k: v for k, v in starts.items() if k in ids})
            self.put("warm_rooms", {k: v for k, v in rooms.items() if k in ids})
            stats["warm_start"] = sum(1 for k in starts if k in ids)
        return stats


def _load_previous(path: Path) -> tuple[dict, dict]:
    """starts.json of a published version and, next to it, rooms.json (if present)."""
    rooms = path.with_name("rooms.json")
    return (json.loads(path.read_text(encoding="utf-8")),
            json.loads(rooms.read_text(encoding="utf-8")) if rooms.exists() else {})


def _offline_issue_summary(issues: list[dict]) -> str:
    if not issues:
        return "- Проблем во входных данных не найдено."
    by_level = Counter(i["level"] for i in issues)
    lines = [f"- Найдено замечаний: {len(issues)} (исправлено автоматически: {by_level.get('fixed', 0)}, "
             f"требуют внимания: {by_level.get('warning', 0)})."]
    for i in issues[:6]:
        lines.append(f"- [{i['level']}] {i['where']}: {i['issue']}")
    return "\n".join(lines)
