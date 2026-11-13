"""Verify published evidence bytes and independently re-check its timetable.

Run from the repository root with its dependencies installed:
    python scripts/verify_readme_evidence.py docs/evidence/2026-11-13/published-v3
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timetable.domain.constraints import ConstraintIndex, ConstraintSpec  # noqa: E402
from timetable.domain.models import Room, Session  # noqa: E402
from timetable.service.versions import read_rows  # noqa: E402
from timetable.solver.checks import check  # noqa: E402


def verify(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"SHA-256 mismatch: {name}")
    with gzip.open(folder / "checker-input.json.gz", "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    index = ConstraintIndex([ConstraintSpec.model_validate(x) for x in data["specs"]],
                            data["hours_per_day"], data["first_hour"], data["max_daily_hours"])
    rooms = [Room(**x) for x in data["rooms"]]
    validation = check([Session(**x) for x in data["sessions"]], data["starts"], data["rooms_of"],
                       rooms, index, data["days"], data["hours_per_day"])
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    if validation != metrics["independent_validation"]:
        raise ValueError("Independent validation differs from the published metrics")
    counts: Counter = Counter()
    for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["kind"] in ("tool_call", "llm_call") and rec.get("attempt", 1) == 1:
            counts[rec["agent"]] += 1
    if dict(counts) != metrics["load"]["calls"]:
        raise ValueError("Agent call counts differ from the complete event log")
    rows = read_rows(folder / "timetable.csv")
    if len(rows) != metrics["group_academic_hours"] or len({r["group"] for r in rows}) != metrics["groups"]:
        raise ValueError("CSV coverage differs from the published metrics")
    occupied = {(r["room"], r["day"], r["hour"]) for r in rows if r["room"] not in {"online", "UNASSIGNED"}}
    physical = {r.code for r in rooms}
    if (len(occupied) != metrics["room_usage"]["occupied_resource_hours"]
            or sum(r in physical for r, _, _ in occupied) != metrics["room_usage"]["occupied_physical_room_hours"]):
        raise ValueError("CSV room usage differs from the published metrics")
    return {"files_verified": len(manifest["files"]), "groups": metrics["groups"],
            "group_academic_hours": len(rows), "hard_violations": validation["hard_violations"],
            "gap_hours_per_group": validation["soft"]["gap_hours_per_group"],
            "max_agent_share": max(counts.values()) / sum(counts.values())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    print(json.dumps(verify(parser.parse_args().folder), ensure_ascii=False, indent=2))
