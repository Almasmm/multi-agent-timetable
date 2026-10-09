"""Turn curricula + groups into weekly sessions (the scheduling demand) and split the
problem into independent-ish chunks for decomposition."""
from __future__ import annotations

from collections import defaultdict

from .models import LAB, LECTURE, Dataset, Session


def blocks(hours: int) -> list[int]:
    """3 h/week -> a pair + a single (2+1), 5 -> 2+2+1: pairs like the real AITU timetable."""
    return [2] * (hours // 2) + ([1] if hours % 2 else [])


def trimester_of(year: int, term: int) -> int:
    return (year - 1) * 3 + term


def build_sessions(ds: Dataset, term: int, stream_size: int, online_stream_size: int,
                   programs: tuple[str, ...] = (), online_scope: str = "lecture") -> tuple[list[Session], dict]:
    """``online_scope='lecture'``: an online course has online lectures and on-campus
    practice (as in the sample IT-2208 timetable); ``'all'`` puts every class online."""
    units: dict[tuple[str, int], list] = defaultdict(list)
    for g in ds.groups:
        if programs and g.program not in programs:
            continue
        units[(g.program, g.year)].append(g)
    by_prog_tri = defaultdict(list)
    for c in ds.courses:
        by_prog_tri[(c.program, c.trimester)].append(c)

    sessions: list[Session] = []
    summary = {}
    for (prog, year), groups in sorted(units.items()):
        groups = sorted(groups, key=lambda g: g.code)
        courses = by_prog_tri.get((prog, trimester_of(year, term)), [])
        summary[f"{prog}-{year}"] = {"groups": len(groups), "trimester": trimester_of(year, term),
                                     "courses": [c.name for c in courses]}
        for c in courses:
            for kind, hours in c.hours.items():
                online = c.online and (kind == LECTURE or online_scope == "all")
                if kind == LECTURE:
                    size = online_stream_size if online else stream_size
                    streams = [groups[i:i + max(size, 1)] for i in range(0, len(groups), max(size, 1))]
                else:
                    streams = [[g] for g in groups]
                for stream in streams:
                    codes = [g.code for g in stream]
                    comp = f"{prog}-{year}|{c.name}|{kind}|{'+'.join(codes) if len(codes) <= 3 else codes[0] + f'+{len(codes) - 1}'}"
                    for b, dur in enumerate(blocks(hours)):
                        sessions.append(Session(
                            id=f"{comp}#{b}", program=prog, year=year, course=c.name, kind=kind, groups=codes,
                            size=sum(g.headcount for g in stream), duration=dur, online=online,
                            venue=c.venue if kind != LAB else "", component=comp, lecture_first=c.lecture_first,
                        ))
    return sessions, summary


def partition(sessions: list[Session], target_groups: int) -> list[list[str]]:
    """Pack programme-year units into chunks of ~target groups (first-fit decreasing).
    Lecture streams never cross units, so units are the natural atoms of decomposition."""
    unit_groups: dict[str, set] = defaultdict(set)
    for s in sessions:
        unit_groups[s.unit].update(s.groups)
    chunks: list[list[str]] = []
    loads: list[int] = []
    for unit, gs in sorted(unit_groups.items(), key=lambda kv: -len(kv[1])):
        for i, load in enumerate(loads):
            if load + len(gs) <= target_groups:
                chunks[i].append(unit)
                loads[i] += len(gs)
                break
        else:
            chunks.append([unit])
            loads.append(len(gs))
    return chunks
