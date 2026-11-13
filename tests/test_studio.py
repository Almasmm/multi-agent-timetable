"""Operator proposals discussed by the agents, the real-time feed, the live board of a run and the one-button
generation - offline, on the toy week of tests/test_editor_chat.py and a hand-made run folder."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from tests.test_editor_chat import G1, G2, editor  # noqa: F401 - the fixture is reused
from tests.test_service import FakeManager
from timetable.config import Settings
from timetable.service.autopilot import Autopilot
from timetable.service.chat import AgentChat
from timetable.service.proposals import ProposalDesk
from timetable.web.live import live_feed, run_board

PHYS = {"group": G2, "course": "Physics", "day": 0, "time": "08:00-08:50", "room": "C1.1.102P", "kind": "practice"}


@pytest.fixture()
def desk(editor):  # noqa: F811
    return ProposalDesk(editor.store, AgentChat(editor.store, editor, None, editor.settings))


def test_change_proposal_is_prepared_checked_and_published(desk):
    p = desk.create("Перенесите физику IT-2302 с утра понедельника", [PHYS], wait=True)
    roles = [(m["agent"], m["role"]) for m in p["messages"]]
    assert roles[:2] == [("operator", "user"), ("intake", "agent")] and ("scheduler", "agent") in roles and ("validator", "agent") in roles
    assert p["kind"] == "move" and p["status"] == "ready" and len(p["outcome"]["moves"]) == 1 and p["outcome"]["hard"] == 0
    assert desk.tools.editor.state()["count"] == 1
    desk.store.update_proposal(p["id"], status="discussing")
    with pytest.raises(RuntimeError):
        desk.reply(p["id"], "ок")  # no answer while the agents are still discussing
    desk.store.update_proposal(p["id"], status="ready")
    done = desk.accept(p["id"])
    assert done["status"] == "accepted" and done["outcome"]["version"] == 2
    assert desk.store.current_version()["metrics"]["kind"] == "manual" and done["messages"][-1]["agent"] == "publisher"


def test_rejected_proposal_returns_the_draft(desk):
    p = desk.create("Поставьте физику IT-2302 в другое время", [PHYS], wait=True)
    assert p["status"] == "ready" and desk.tools.editor.state()["count"] == 1
    r = desk.reject(p["id"])
    assert r["status"] == "rejected" and desk.tools.editor.state()["count"] == 0
    with pytest.raises(RuntimeError):
        desk.accept(p["id"])


def test_rule_proposal_is_evaluated_and_added_on_accept(desk):
    p = desk.create("Желательно не ставить занятия после 18:00", wait=True)
    assert p["kind"] == "rule" and p["status"] == "ready" and p["outcome"]["mode"] == "soft"
    assert [m["agent"] for m in p["messages"]][1:3] == ["intake", "constraints"] and desk.store.profile()["requirements"] == []
    r = desk.accept(p["id"], "hard")
    assert r["status"] == "accepted" and desk.store.profile()["requirements"] == ["[hard] Желательно не ставить занятия после 18:00"]
    assert desk.store.get("force_job")["kind"] == "regenerate"  # the Watcher will propose the regeneration


def test_accepted_rule_says_the_regeneration_waits_for_clean_data(desk):
    """data/input.xlsx has loader problems: the rule is added and queued, but the operator is told what blocks it."""
    p = desk.create("Желательно не ставить занятия после 18:00", wait=True)
    r = desk.accept(p["id"])
    last = r["messages"][-1]
    assert last["agent"] == "watcher" and "ждёт" in last["text"] and "Данные вуза" in last["text"]
    assert desk.store.get("force_job")["kind"] == "regenerate"  # kept: it starts once the data is fixed
    desk.tools.editor.settings = desk.tools.editor.settings.with_(data_gate=False)
    assert desk.tools.regenerate()["note"].startswith("Наблюдатель предложит")


def test_without_classes_the_agents_ask_for_details(desk):
    p = desk.create("Сделайте расписание IT-2301 удобнее", wait=True)
    assert p["status"] == "needs_input" and "занятия" in p["messages"][-1]["text"]
    p = desk.reply(p["id"], f"перенеси {G2} Пн 08:00 на Пн 09:00", wait=True)
    assert len(p["messages"]) >= 5


def _run_dir(tmp_path):
    d = tmp_path / "r1"
    d.mkdir()
    sessions = [{"id": "a#0", "program": "IT", "year": 1, "course": "Calc", "kind": "lecture", "groups": ["IT-1", "IT-2"], "duration": 2, "online": False},
                {"id": "b#0", "program": "IT", "year": 1, "course": "Phys", "kind": "practice", "groups": ["IT-1"], "duration": 1, "online": False},
                {"id": "c#0", "program": "SE", "year": 1, "course": "Algo", "kind": "lab", "groups": ["SE-1"], "duration": 2, "online": False}]
    con = sqlite3.connect(d / "state.sqlite")
    con.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT, writer TEXT, updated REAL)")
    now = time.time()
    for i, (k, v) in enumerate({"sessions": sessions, "chunks": [["IT-1"], ["SE-1"]], "quota:0": {}, "seed:0": {"a#0": 0, "b#0": 14},
                                "solution:0": {"a#0": 1, "b#0": 14}, "validated:0": True, "quota:1": {}, "seed:1": {"c#0": 26}}.items()):
        con.execute("INSERT INTO kv VALUES (?,?,?,?)", (k, json.dumps(v), "x", now + i))
    con.commit()
    con.close()
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": now, "kind": "message", "agent": "orchestrator", "to": "scheduler", "task": "solve_chunk", "payload": {"chunk": 0}},
        {"ts": now + 1, "kind": "tool_call", "agent": "scheduler", "name": "cp_sat", "status": "ok"}]) + "\n", encoding="utf-8")
    return d


def test_run_board_shows_chunks_heatmap_and_a_group_week(tmp_path):
    b = run_board(_run_dir(tmp_path), "IT-1")
    assert [c["state"] for c in b["chunks"]] == ["validated", "ga"] and b["placed"] == 2 and b["tentative"] == 1
    assert b["grid"][0][1] == 1 and b["grid"][0][2] == 1 and b["grid"][1][2] == 1 and b["ghost"][2][2] == 1
    assert b["programs"] == {"IT": [2, 2], "SE": [0, 1]} and b["groups"] == ["IT-1", "IT-2", "SE-1"]
    assert {(w["day"], w["hour"], w["course"]) for w in b["week"]} == {(0, 1, "Calc"), (1, 2, "Phys")}


def test_live_feed_merges_sources_and_reads_incrementally(tmp_path):
    d = _run_dir(tmp_path)
    svc = tmp_path / "_service"
    svc.mkdir()
    (svc / f"events-{time.strftime('%Y%m%d')}.jsonl").write_text(json.dumps({"ts": time.time() - 5, "kind": "info", "agent": "autopilot", "msg": "tick"}) + "\n", encoding="utf-8")
    f = live_feed(tmp_path, d.name, {})
    assert [e["src"] for e in f["events"]] == ["svc", "run", "run"]
    with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time() + 9, "kind": "llm_call", "agent": "critic", "name": "review"}) + "\n")
        fh.write('{"ts": 1, "kind": "half')  # a line still being written is not read yet
    g = live_feed(tmp_path, d.name, f["cursors"])
    assert [e["kind"] for e in g["events"]] == ["llm_call"]


def test_one_button_generation_starts_a_service_job(tmp_path):
    mgr = FakeManager(tmp_path)
    ap = Autopilot(mgr, Settings().with_(out_root=tmp_path, llm_provider="offline", data_gate=False))
    ap.store.set("profile", {"llm": "offline", "improve_every_min": 999, "stable_after": "2000-01-01"})
    try:
        ap.tick(), ap.tick()  # v1
        assert ap.tick()["state"] == "idle"
        ap.store.set("force_job", {"kind": "regenerate", "reason": "кнопка «Сгенерировать»"})
        r = ap.tick()
        assert r["state"] == "started" and r["run_id"] and "кнопка" in r["reason"]
        assert ap.tick()["status"] in ("published", "kept")
    finally:
        ap.close()
