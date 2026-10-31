"""Tests of the 24/7 service (no solver): version helpers, warm start in the GA, and full autopilot
cycles with a fake generation pipeline, so every routing decision is checked in about a second."""
from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from timetable.config import Settings
from timetable.domain.constraints import ConstraintIndex
from timetable.domain.models import Session
from timetable.service.agents import config_hash, live_term
from timetable.service.autopilot import Autopilot
from timetable.service.versions import diff_rows, quality_score, to_ics
from timetable.solver.evolve import evolve

FIELDS = ["group", "program", "year", "day", "day_index", "hour_index", "time", "discipline", "type", "classroom", "stream_size"]


def _row(group, day, hour, course="Calculus 1", kind="lecture", room="C1.1.141"):
    return {"group": group, "program": "IT", "year": 1, "day": day, "hour": hour, "time": f"{8 + hour:02d}:00-{8 + hour:02d}:50",
            "course": course, "kind": kind, "room": room, "stream": 1}


# ------------------------------------------------------------------ pure helpers
def test_quality_score_weights():
    assert quality_score({"gap_hours_total": 10, "late_hours_18_plus": 2, "saturday_hours": 1, "precedence_violations": 4}) == 19.0
    assert quality_score({}) == 0


def test_diff_counts_moves_and_room_changes():
    old = [_row("G1", 0, 0), _row("G1", 0, 1), _row("G2", 1, 2)]
    new = [_row("G1", 0, 0), _row("G1", 2, 3), _row("G2", 1, 2, room="C1.2.200")]
    d = diff_rows(old, new)
    assert d["hours_moved"] == 1 and d["room_changes"] == 1 and d["groups_affected"] == 2
    assert d["groups"]["G1"] == {"moved": 1, "rooms": 0} and d["groups"]["G2"] == {"moved": 0, "rooms": 1}
    assert diff_rows([], new)["first"] is True


def test_ics_merges_consecutive_hours_into_weekly_events():
    rows = [_row("G1", 0, 2), _row("G1", 0, 3), _row("G1", 2, 0, course="Physics", room="online")]
    ics = to_ics(rows, "G1", "2026-09-09", weeks=10)  # a Wednesday: snapped to Monday 07.09
    assert ics.count("BEGIN:VEVENT") == 2 and "DTSTART:20260907T100000" in ics and "DTEND:20260907T115000" in ics
    assert "RRULE:FREQ=WEEKLY;COUNT=10" in ics and "LOCATION:онлайн" in ics and ics.endswith("END:VCALENDAR\r\n")
    assert to_ics(rows, "G1", "2026-09-09", 10).split("UID:")[1][:30] == ics.split("UID:")[1][:30]  # stable UIDs


def test_live_term_and_config_hash():
    assert live_term({"stable_after": "2026-09-07"}, "", time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1)))
    assert not live_term({"stable_after": ""}, "2026-09-07", time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, -1)))
    p = {"dataset": "input.xlsx", "term": 1, "programs": []}
    assert config_hash(p, ["a"]) != config_hash(p, ["a", "b"]) and config_hash(p, ["a"]) == config_hash(dict(p), ["a"])


def test_warm_start_keeps_the_published_slots():
    """With a stability weight the GA keeps an already conflict-free published timetable."""
    mk = lambda i, g, comp: Session(id=f"s{i}", program="IT", year=1, course=comp, kind="practice", groups=[g], size=20,  # noqa: E731
                                    duration=1, online=False, venue="", component=f"{comp}|{g}", lecture_first=False)
    sessions = [mk(0, "G1", "a"), mk(1, "G1", "b"), mk(2, "G2", "c"), mk(3, "G2", "d")]
    prev = {"s0": 14, "s1": 15, "s2": 26, "s3": 27}  # Tue 10:00-12:00 / Wed 10:00-12:00, no gaps
    res = evolve(sessions, ConstraintIndex([], 12, 8, 8), {"ROOMS": [2] * 72}, caps=[], days=[0, 1, 2, 3, 4], H=12,
                 time_limit=0.3, seed=3, prev=prev, w_stable=50)
    assert res.stats["warm_kept"] == 4 and res.starts == prev


# ------------------------------------------------------------------ autopilot with a fake pipeline
class _Handle:
    def __init__(self, run_id):
        self.run_id = run_id


class FakeManager:
    """Stands in for web.runs.RunManager: 'runs' finish instantly with a chosen quality."""

    def __init__(self, root: Path):
        self.root, self.started, self.next_soft, self.next_status, self.shift = root, [], {"gap_hours_total": 40}, "ok", 0

    def active(self):
        return None

    def status(self, run_id):
        return {"status": "ok"}

    def start(self, params):
        run_id = f"r{len(self.started) + 1}-{params['name']}"
        self.started.append({"run_id": run_id, **params})
        d = self.root / run_id
        d.mkdir(parents=True)
        res = {"status": self.next_status, "hard_violations": {} if self.next_status == "ok" else {"group_clash": 2},
               "soft": {"gap_hours_per_group": 2.0, **self.next_soft}}
        (d / "result.json").write_text(json.dumps(res), encoding="utf-8")
        with (d / "timetable.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, FIELDS)
            w.writeheader()
            for i, g in enumerate(["IT-2301", "IT-2302"]):
                h = (i + self.shift) % 10
                w.writerow({"group": g, "program": "IT", "year": 1, "day": "Mon", "day_index": 0, "hour_index": h,
                            "time": f"{8 + h:02d}:00-{8 + h:02d}:50", "discipline": "Calculus 1", "type": "lecture",
                            "classroom": "C1.1.141", "stream_size": 1})
        con = sqlite3.connect(d / "state.sqlite")
        con.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT, writer TEXT, updated REAL)")
        con.execute("INSERT INTO kv VALUES ('starts', '{\"IT-1|Calculus 1|lecture|IT-2301#0\": 0}', 'validator', 0)")
        con.commit()
        con.close()
        return _Handle(run_id)


@pytest.fixture()
def pilot(tmp_path):
    mgr = FakeManager(tmp_path)
    ap = Autopilot(mgr, Settings().with_(out_root=tmp_path, llm_provider="offline"))
    ap.store.set("profile", {"llm": "offline", "improve_every_min": 0, "max_improve_attempts": 2, "stable_after": "2000-01-01"})
    yield ap, mgr
    ap.close()


def test_autopilot_publishes_first_version_then_regenerates_on_a_request(pilot):
    ap, mgr = pilot
    r = ap.tick()
    assert r["state"] == "started" and r["kind"] == "generate" and mgr.started[0]["warm_start_path"] is None
    r = ap.tick()
    assert r["state"] == "reviewed" and r["status"] == "published" and r["version"] == 1
    v1 = ap.store.current_version()
    assert (ap.store.version_dir(1) / "starts.json").exists() and v1["announcement"].startswith("Опубликована версия v1")

    good = ap.store.add_request("Аудитория C1.1.141 недоступна в понедельник", "Деканат")
    bad = ap.store.add_request("У группы XX-9999 свободна пятница", "Деканат")
    vague = ap.store.add_request("абракадабра", "кто-то")
    mgr.shift = 1  # the new run moves both groups by one hour
    r = ap.tick()
    assert r["state"] == "started" and r["kind"] == "regenerate"
    assert ap.store.request(good)["status"] == "accepted" and ap.store.request(bad)["status"] == "rejected"
    assert "XX-9999" in ap.store.request(bad)["verdict"]["reason"] and ap.store.request(vague)["status"] == "review"
    job = mgr.started[-1]
    assert job["warm_start_path"].endswith("starts.json") and "Аудитория C1.1.141 недоступна в понедельник" in job["requirements"]
    r = ap.tick()
    assert r["status"] == "published" and r["version"] == 2
    assert ap.store.request(good)["status"] == "applied"
    v2 = ap.store.current_version()
    assert v2["diff"]["hours_moved"] == 2 and v2["diff"]["groups_affected"] == 2


def test_autopilot_improvement_gate(pilot):
    ap, mgr = pilot
    ap.tick(), ap.tick()  # v1 with penalty 40
    mgr.next_soft = {"gap_hours_total": 39.5}  # not 2 % better
    assert ap.tick()["kind"] == "improve"
    assert ap.tick()["status"] == "kept"
    mgr.next_soft, mgr.shift = {"gap_hours_total": 20}, 3  # much better but moves every hour in a live term
    ap.tick()
    r = ap.tick()
    assert r["status"] == "kept" and "идёт семестр" in r["reason"]
    assert ap.tick()["state"] == "idle"  # attempts exhausted -> wait
    ap.store.set("profile", {**ap.store.profile(), "stable_after": "2999-01-01", "max_improve_attempts": 3})  # draft period
    ap.tick()
    assert ap.tick()["status"] == "published" and ap.store.current_version()["score"] == 20


def test_autopilot_rejects_failed_runs_and_pauses_after_three_failures(pilot):
    ap, mgr = pilot
    mgr.next_status = "partial"
    for _ in range(3):
        assert ap.tick()["state"] == "started"
        assert ap.tick()["status"] == "failed"
    r = ap.tick()
    assert r["state"] == "idle" and "три задания подряд" in r["reason"]
    assert ap.store.current_version() is None
    cyc = ap.cycles()
    assert cyc[0]["state"] == "idle" and cyc[1]["state"] == "reviewed" and cyc[1]["events"]


# ------------------------------------------------------------------ web API of the service
def test_service_api(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_OUT", str(tmp_path))
    from timetable.web.server import create_app
    with TestClient(create_app(autopilot=False)) as c:
        assert c.get("/api/published").json() == {"version": None}
        assert c.get("/api/published/ics", params={"group": "IT-2301"}).status_code == 404
        r = c.post("/api/requests", json={"text": "1 курс заканчивает до 17:00", "author": "Деканат"})
        assert r.status_code == 201 and r.json()["status"] == "new"
        rid = r.json()["id"]
        assert c.post(f"/api/requests/{rid}/decision", json={"action": "approve"}).json()["status"] == "accepted"
        assert c.post(f"/api/requests/{rid}/decision", json={"action": "approve"}).status_code == 400  # already accepted
        assert c.post(f"/api/requests/{rid}/decision", json={"action": "withdraw", "note": "ремонт закончен"}).json()["status"] == "withdrawn"
        assert c.post("/api/requests", json={"text": "x"}).status_code == 422
        assert c.put("/api/autopilot/profile", json={"dataset": "nope.xlsx"}).status_code == 404
        prof = c.put("/api/autopilot/profile", json={"dataset": "input.xlsx", "programs": ["IT"], "requirements": [" a ", ""]}).json()
        assert prof["programs"] == ["IT"] and prof["requirements"] == ["a"]
        st = c.get("/api/autopilot").json()
        assert st["enabled"] is False and st["profile"]["programs"] == ["IT"] and st["standing"] == ["a"]
        assert c.get("/api/autopilot/brief").json()["open_requests"] == 0
        assert c.post("/api/runs", json={"dataset": "input.xlsx", "warm_start": True}).status_code == 400  # nothing published
        assert c.get("/api/published/files/..%2F.env").status_code == 404
