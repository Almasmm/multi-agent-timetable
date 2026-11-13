"""Quick check of natural-language requirements against a workbook (offline parser + entity
grounding). Used by the web form preview and by the Intake agent that triages staff requests;
the run itself parses requirements again in the Constraint agent (LLM when available)."""
from __future__ import annotations

from pathlib import Path

from ..config import Settings
from .constraints import apply_modes, parse_offline, split_mode
from .loader import normalize, read_workbook

_entity_cache: dict[str, tuple[float, dict]] = {}


def dataset_entities(path: Path) -> dict:
    """Programme / room / course / group names of a workbook (cached by file mtime)."""
    mtime = path.stat().st_mtime
    hit = _entity_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    ds = normalize(read_workbook(path), Settings().weeks_per_term)
    ent = {"programs": sorted({g.program for g in ds.groups}), "rooms": [r.code for r in ds.rooms],
           "courses": sorted({c.name for c in ds.courses}), "groups": [g.code for g in ds.groups]}
    _entity_cache[str(path)] = (mtime, ent)
    return ent


def preview_texts(path: Path, texts: list[str]) -> dict:
    """{parsed: [spec + unknown entities], unparsed: [lines the offline parser did not understand]}."""
    ent = dataset_entities(path)
    tagged = [split_mode(t) for t in texts if t.strip()]
    cset = apply_modes(parse_offline([t[0] for t in tagged], ent["programs"], ent["courses"], ent["rooms"]), tagged)
    programs, rooms, groups = set(ent["programs"]), set(ent["rooms"]), set(ent["groups"])
    parsed = []
    for spec in cset.constraints:
        sc = spec.scope
        unknown = ([p for p in sc.programs if p not in programs] + [r for r in sc.rooms if r not in rooms]
                   + [g for g in sc.groups if g not in groups])
        parsed.append({**spec.model_dump(), "unknown": unknown})
    return {"parsed": parsed, "unparsed": cset.unparsed}


def describe_rules(path: Path, texts: list[str]) -> list[dict]:
    """One entry per requirement line for the rules editor: the sentence, its explicit mode tag
    and how the offline parser understood it (spec or None) plus entities missing in the data."""
    out = []
    for raw in texts:
        clean, hard, weight = split_mode(raw)
        res = preview_texts(path, [raw]) if clean else {"parsed": [], "unparsed": []}
        spec = res["parsed"][0] if res["parsed"] else None
        out.append({"text": raw, "clean": clean, "mode": None if hard is None else ("hard" if hard else "soft"),
                    "weight": weight, "spec": spec, "unknown": spec["unknown"] if spec else [],
                    "softable": bool(spec and spec["kind"] == "forbid_time")})
    return out
