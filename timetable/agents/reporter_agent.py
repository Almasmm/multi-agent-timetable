from __future__ import annotations

import json
from pathlib import Path

from ..core.agent import BaseAgent
from ..export.writers import rows, write_csv, write_excel, write_html


class ReporterAgent(BaseAgent):
    name = "reporter"
    role = "Publishes the result: Excel/CSV/HTML timetables and a human-readable run report."
    done_when = "all output files exist and report.md summarises quality, issues and agent load"

    def register_tools(self) -> None:
        self.add_tool("export_tables", self._export_tables, "Excel + CSV with timetable, rooms, issues, load")
        self.add_tool("export_html", self._export_html, "self-contained HTML viewer (per group / per room)")
        self.add_tool("write_report", self._write_report, "markdown run report incl. LLM summary")

    def _data(self) -> list[dict]:
        return rows(self.require("sessions"), self.get("starts", {}), self.get("rooms_of", {}),
                    self.require("dataset").slot_labels)

    def _export_tables(self, out: Path, load: dict) -> list[str]:
        data = self._data()
        metrics = {**self.get("validation", {}).get("soft", {}),
                   "hard_violations": self.get("validation", {}).get("by_type", {})}
        xlsx = write_excel(out / "timetable.xlsx", data, self.require("dataset").issues, load, metrics)
        csv = write_csv(out / "timetable.csv", data)
        return [str(xlsx), str(csv)]

    def _export_html(self, out: Path) -> str:
        val = self.get("validation", {})
        st = self.ctx.settings
        summary = {"term": st.term, "hard_violations": val.get("hard_violations"), **val.get("soft", {})}
        return str(write_html(out / "timetable.html", self._data(), self.require("dataset").slot_labels,
                              f"Расписание — term {st.term}", summary))

    def _write_report(self, out: Path, load: dict, timeline: list[dict]) -> str:
        val = self.get("validation", {})
        facts = {"validation": {"hard": val.get("by_type"), "soft": val.get("soft")}, "load": load,
                 "negotiation": [e for e in timeline if e.get("task") == "resolve"],
                 "repairs": len(self.get("repairs") or []), "constraint_warnings": self.get("constraint_warnings", [])}
        summary = self.ctx.llm.text(
            self.name, "executive_summary",
            system="Write a concise executive summary (Russian, max 8 bullet points) of an automated university "
                   "timetable run for the vice-rector: was it successful, key quality numbers, what the agents "
                   "had to negotiate, what needs human attention.",
            user=json.dumps(facts, ensure_ascii=False, default=str),
            offline=lambda: _offline_summary(facts))
        lines = ["# Отчёт о прогоне", "", "## Итог", summary, "",
                 "## Проблемы входных данных", self.get("data_issues_text", ""), "",
                 "## Критик (Validator)", self.get("critique", ""), "",
                 "## Распределение нагрузки по агентам", "", "| агент | вызовов | доля |", "|---|---|---|"]
        lines += [f"| {a} | {v['calls']} | {v['share']:.0%} |" for a, v in load["agents"].items()]
        lines += ["", "## Ход выполнения", "", "| шаг | агент | задача | статус | кратко |", "|---|---|---|---|---|"]
        lines += [f"| {e['step']} | {e['agent']} | {e['task']} | {e['status']} | {e['brief']} |" for e in timeline]
        path = out / "report.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def on_export(self, out: str, load: dict, timeline: list[dict]) -> dict:
        out_dir = Path(out)
        files = self.call_tool("export_tables", out=out_dir, load=load)
        files.append(self.call_tool("export_html", out=out_dir))
        files.append(self.call_tool("write_report", out=out_dir, load=load, timeline=timeline))
        return {"files": files}


def _offline_summary(f: dict) -> str:
    hard = f["validation"]["hard"] or {}
    soft = f["validation"]["soft"] or {}
    ok = not hard
    lines = [f"- Статус: {'расписание построено без жёстких нарушений' if ok else f'есть жёсткие нарушения: {hard}'}.",
             f"- Окна: {soft.get('gap_hours_per_group')} ч/нед на группу; поздние часы (18:00+): "
             f"{soft.get('late_hours_18_plus')}; суббота: {soft.get('saturday_hours')} ч.",
             f"- Переговоры: {len(f['negotiation'])} уступок; локальных переносов: {f['repairs']}.",
             f"- Максимальная доля вызовов одного агента: {f['load']['max_share']:.0%} (порог 40%)."]
    if f["constraint_warnings"]:
        lines.append(f"- Требуют внимания ограничения: {'; '.join(f['constraint_warnings'][:3])}")
    return "\n".join(lines)
