"""User constraints: a small JSON DSL that the Constraint agent produces from natural
language (via the LLM or offline rules), plus grounding to slot masks for the solver."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from ..config import DAYS
from .models import Session

Day = Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class Scope(BaseModel):
    programs: list[str] = Field(default_factory=list, description="programme codes, e.g. IT, SE; empty = all")
    years: list[int] = Field(default_factory=list, description="study years 1..3; empty = all")
    groups: list[str] = Field(default_factory=list, description="group codes, e.g. IT-2208")
    courses: list[str] = Field(default_factory=list, description="course name fragments (case-insensitive)")
    rooms: list[str] = Field(default_factory=list, description="room codes (room_unavailable only)")


class ConstraintSpec(BaseModel):
    kind: Literal["forbid_time", "max_daily_hours", "room_unavailable"]
    hard: bool = True
    scope: Scope = Field(default_factory=Scope)
    days: list[Day] = Field(default_factory=list, description="empty = every day")
    start_hour: int = Field(8, description="window start, clock hour 8..20")
    end_hour: int = Field(20, description="window end (exclusive), clock hour 8..20")
    value: int = Field(0, description="hours limit for max_daily_hours, else 0")
    weight: int = Field(5, description="penalty per hour for soft constraints")
    source: str = Field("", description="the original sentence")


class ConstraintSet(BaseModel):
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list, description="sentences that could not be understood")


# ----------------------------------------------------------------------------------------
# Offline rule-based parser (RU / EN). Used when no LLM is configured or the LLM fails.
# ----------------------------------------------------------------------------------------
DAY_WORDS = {
    "Mon": ["monday", "понедельник"], "Tue": ["tuesday", "вторник"], "Wed": ["wednesday", "сред"],
    "Thu": ["thursday", "четверг"], "Fri": ["friday", "пятниц"], "Sat": ["saturday", "суббот"],
}
SOFT_WORDS = ["prefer", "желательно", "по возможности", "if possible", "try to", "лучше", "стараться", "preferably"]
COURSE_ALIASES = {"физкультур": "Physical Education", "physical education": "Physical Education",
                  "pe ": "Physical Education", "английск": "Foreign Language", "foreign language": "Foreign Language"}
YEAR_WORDS = {1: ["first", "1st", "первого", "первый", "первом"], 2: ["second", "2nd", "второго", "второй"],
              3: ["third", "3rd", "третьего", "третий"]}


def _days(text: str) -> list[str]:
    return [d for d, words in DAY_WORDS.items() if any(w in text for w in words)]


def _hour(text: str, keywords: list[str]) -> int | None:
    for kw in keywords:
        m = re.search(kw + r"\s*(\d{1,2})(?::(\d{2}))?", text)
        if m:
            h = int(m.group(1))
            return h + (1 if m.group(2) and int(m.group(2)) > 0 else 0)
    return None


def parse_offline(lines: list[str], programs: list[str], courses: list[str], rooms: list[str]) -> ConstraintSet:
    out = ConstraintSet()
    room_lookup = {r.lower(): r for r in rooms}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        t = line.lower()
        hard = not any(w in t for w in SOFT_WORDS)
        scope = Scope()
        scope.groups = [g.upper() for g in re.findall(r"\b([a-z]{2,4}-\d{4})\b", t)]
        scope.programs = [p for p in programs if re.search(rf"(?<![a-z\-]){p.lower()}(?![a-z\-\d])", t)
                          and not any(g.startswith(p.upper() + "-") for g in scope.groups)]
        for y, words in YEAR_WORDS.items():
            if re.search(rf"\b{y}\s*(курс|year|-?й курс)", t) or re.search(rf"year\s*{y}\b", t) or any(
                    re.search(rf"\b{w}\b.*(курс|year)", t) for w in words):
                scope.years.append(y)
        scope.courses = sorted({v for k, v in COURSE_ALIASES.items() if k in t + " "} |
                               {c for c in courses if len(c) > 5 and c.lower() in t})
        days = _days(t)
        spec = None
        m_room = [room_lookup[w] for w in re.findall(r"\b(?:c1|с1|iec)[\.\-][\w\.]+", t.replace("с1", "c1"))
                  if w in room_lookup]
        if m_room and any(w in t for w in ["unavailable", "недоступ", "закрыт", "ремонт", "busy", "занят"]):
            spec = ConstraintSpec(kind="room_unavailable", scope=Scope(rooms=m_room), days=days)
            start, end = _hour(t, ["from", "с"]), _hour(t, ["until", "till", "до"])
            spec.start_hour, spec.end_hour = start or 8, end or 20
        elif re.search(r"(max|не более|не больше|максимум|at most)\s*(\d+)", t):
            n = int(re.search(r"(max|не более|не больше|максимум|at most)\s*(\d+)", t).group(2))
            if "пар" in t or "pair" in t:
                n *= 2
            spec = ConstraintSpec(kind="max_daily_hours", value=n)
        elif any(w in t for w in ["only in the morning", "только утром", "утром", "morning only"]):
            spec = ConstraintSpec(kind="forbid_time", start_hour=12, end_hour=20)
        elif any(w in t for w in ["only in the afternoon", "только после обеда", "после обеда"]):
            spec = ConstraintSpec(kind="forbid_time", start_hour=8, end_hour=13)
        elif (m_before := re.search(r"(не ставить|не начинать|нет занятий|без занятий|no classes|не раньше)"
                                   r"[^0-9]*(?:до|before)\s*(\d{1,2})", t)):
            spec = ConstraintSpec(kind="forbid_time", start_hour=8, end_hour=int(m_before.group(2)))
        elif (h := _hour(t, ["until", "till", "end by", "до", "заканчива\\w* до", "not after", "не позже"])) and not days:
            spec = ConstraintSpec(kind="forbid_time", start_hour=h, end_hour=20)
        elif (h := _hour(t, ["before", "раньше", "не раньше", "start after", "начинать с", "начало с"])):
            spec = ConstraintSpec(kind="forbid_time", start_hour=8, end_hour=h)
        elif (h := _hour(t, ["after", "после"])):
            spec = ConstraintSpec(kind="forbid_time", start_hour=h, end_hour=20)
        elif days and any(w in t for w in ["no class", "free", "свобод", "выходн", "нет занятий", "без занятий",
                                            "off", "не ставить", "no lessons", "без"]):
            spec = ConstraintSpec(kind="forbid_time", days=days)
        if spec is None:
            out.unparsed.append(line)
            continue
        if spec.kind != "room_unavailable":
            spec.scope = scope
            if spec.kind == "forbid_time" and not spec.days:
                spec.days = days
        spec.hard, spec.source = hard, line
        out.constraints.append(spec)
    return out


# ----------------------------------------------------------------------------------------
# Grounding
# ----------------------------------------------------------------------------------------
class ConstraintIndex:
    """Answers the solver's questions: which slots are forbidden/penalised for a session,
    the daily hour cap per group and which slots each room is blocked."""

    def __init__(self, specs: list[ConstraintSpec], hours_per_day: int, first_hour: int, default_max_daily: int):
        self.specs = specs
        self.H = hours_per_day
        self.first_hour = first_hour
        self.default_max_daily = default_max_daily

    def _window(self, spec: ConstraintSpec) -> set[int]:
        days = [DAYS.index(d) for d in (spec.days or DAYS)]
        hours = [h for h in range(self.H) if spec.start_hour <= self.first_hour + h < spec.end_hour]
        return {d * self.H + h for d in days for h in hours}

    @staticmethod
    def matches(spec: ConstraintSpec, s: Session) -> bool:
        sc = spec.scope
        if sc.programs and s.program not in sc.programs:
            return False
        if sc.years and s.year not in sc.years:
            return False
        if sc.groups and not set(sc.groups) & set(s.groups):
            return False
        if sc.courses and not any(c.lower() in s.course.lower() for c in sc.courses):
            return False
        return True

    def forbidden(self, s: Session) -> set[int]:
        out: set[int] = set()
        for spec in self.specs:
            if spec.kind == "forbid_time" and spec.hard and self.matches(spec, s):
                out |= self._window(spec)
        return out

    def penalties(self, s: Session) -> dict[int, int]:
        pen: dict[int, int] = defaultdict(int)
        for spec in self.specs:
            if spec.kind == "forbid_time" and not spec.hard and self.matches(spec, s):
                for t in self._window(spec):
                    pen[t] += spec.weight
        return pen

    def max_daily(self, group: str, program: str, year: int) -> int:
        cap = self.default_max_daily
        for spec in self.specs:
            if spec.kind == "max_daily_hours" and spec.value > 0:
                sc = spec.scope
                if (not sc.groups or group in sc.groups) and (not sc.programs or program in sc.programs) and (
                        not sc.years or year in sc.years):
                    cap = min(cap, spec.value)
        return cap

    def room_blocked(self) -> dict[str, set[int]]:
        out: dict[str, set[int]] = defaultdict(set)
        for spec in self.specs:
            if spec.kind == "room_unavailable":
                for r in spec.scope.rooms:
                    out[r] |= self._window(spec)
        return out
