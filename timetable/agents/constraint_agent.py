from __future__ import annotations

import json

from ..core.agent import BaseAgent
from ..domain.constraints import ConstraintSet, ConstraintSpec, parse_offline

SYSTEM = """You translate university timetabling requirements written in natural language (Russian or
English) into a strict JSON constraint list. Kinds:
- forbid_time: no classes for the scope inside a time window. days = list of Mon..Sat (empty = every day),
  start_hour/end_hour are clock hours 8..20 (end exclusive). "until 17:00" -> start_hour 17, end_hour 20.
  "no classes before 9" -> start_hour 8, end_hour 9. "only in the morning" -> start_hour 12, end_hour 20.
- max_daily_hours: value = max academic hours per day (1 pair = 2 hours).
- room_unavailable: scope.rooms + days/hours when the room cannot be used.
hard=false when the text says prefer / желательно / по возможности / if possible; else hard=true.
scope.programs uses programme codes, scope.years study years 1..3, scope.groups exact group codes,
scope.courses course name fragments. Use only codes from the provided entity lists. Put every sentence you
cannot express into `unparsed`. Use weight 5 unless the text implies priority. Copy the sentence into `source`."""


class ConstraintAgent(BaseAgent):
    name = "constraints"
    role = "Turns natural-language requirements into a validated JSON constraint set (hard / soft)."
    done_when = "constraint_specs on the blackboard; unknown entities and unparsed sentences reported"

    def register_tools(self) -> None:
        self.add_tool("parse_text", self._parse, "NL requirements -> ConstraintSet (LLM, offline rules fallback)")
        self.add_tool("ground", self._ground, "check entities exist, clamp windows, drop invalid", retryable=False)

    def _entities(self) -> dict:
        ds = self.require("dataset")
        return {"programs": sorted({g.program for g in ds.groups}), "rooms": [r.code for r in ds.rooms],
                "courses": sorted({c.name for c in ds.courses}), "groups": [g.code for g in ds.groups]}

    def _parse(self, texts: list[str]) -> ConstraintSet:
        ent = self._entities()
        user = json.dumps({"requirements": texts, "programs": ent["programs"], "rooms": ent["rooms"],
                           "example_groups": ent["groups"][:40], "courses": ent["courses"][:300]}, ensure_ascii=False)
        return self.ctx.llm.structured(
            self.name, "parse_constraints", SYSTEM, user, ConstraintSet,
            offline=lambda: parse_offline(texts, ent["programs"], ent["courses"], ent["rooms"]))

    def _ground(self, cset: ConstraintSet) -> tuple[list[dict], list[str]]:
        ent = self._entities()
        programs, rooms, groups = set(ent["programs"]), set(ent["rooms"]), set(ent["groups"])
        ok, warnings = [], []
        for spec in cset.constraints:
            sc = spec.scope
            unknown = ([p for p in sc.programs if p not in programs] + [r for r in sc.rooms if r not in rooms]
                       + [g for g in sc.groups if g not in groups])
            if unknown:
                warnings.append(f"'{spec.source}': unknown entities {unknown} - constraint ignored")
                continue
            spec.start_hour = max(8, min(spec.start_hour, 20))
            spec.end_hour = max(spec.start_hour, min(spec.end_hour, 20))
            if spec.kind == "max_daily_hours" and spec.value <= 0:
                warnings.append(f"'{spec.source}': max_daily_hours needs a positive value - ignored")
                continue
            if spec.kind == "room_unavailable" and not sc.rooms:
                warnings.append(f"'{spec.source}': no room given - ignored")
                continue
            ok.append(spec.model_dump())
        warnings += [f"not understood: '{u}'" for u in cset.unparsed]
        return ok, warnings

    def on_parse_constraints(self, texts: list[str]) -> dict:
        if not texts:
            self.put("constraint_specs", [])
            return {"constraints": 0, "warnings": []}
        cset = self.call_tool("parse_text", texts=texts)
        specs, warnings = self.call_tool("ground", cset=cset)
        self.put("constraint_specs", specs)
        self.put("constraint_warnings", warnings)
        return {"constraints": len(specs), "hard": sum(s["hard"] for s in specs), "warnings": warnings,
                "specs": [ConstraintSpec.model_validate(s).model_dump(exclude={"weight"}) for s in specs]}
