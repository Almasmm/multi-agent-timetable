"""Web platform tests: API contract + one short end-to-end run through the server
(IT programme, offline LLM, ~10-20 s). Run with ``pytest tests/test_web.py``."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from timetable.web.server import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(autopilot=False)) as c:
        yield c


def test_health_endpoint_for_probes(client):
    """Docker HEALTHCHECK, the Jenkins smoke test and Kubernetes probes all call this endpoint."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"]
    assert body["data"] is True  # default workbook is present
    assert body["autopilot"] is False  # tests start the app with the autopilot disabled


def test_datasets_are_validated_with_the_real_loader(client):
    ds = client.get("/api/datasets").json()
    valid = [d for d in ds if d["valid"]]
    assert valid, "data/input.xlsx must be listed as a valid dataset"
    main = next(d for d in valid if d["id"] == "input.xlsx")
    assert main["groups"] == 270 and main["rooms"] > 100 and len(main["programs"]) == 13
    assert all("error" in d for d in ds if not d["valid"])  # raw curricula are explained, not hidden


def test_agent_table_matches_the_spec(client):
    spec = client.get("/api/agents").json()
    rows = spec["agents"]
    assert len(rows) == 9 and all(len(r["tools"]) <= 5 for r in rows)
    assert all(r["title"] and r["role"] and r["done"] for r in rows)  # Russian overlay covers every agent
    assert all(t["ru"] for r in rows for t in r["tools"])  # every tool has a Russian description
    assert len(spec["requirements"]) >= 10 and spec["protocol"]["message"]["intent"] == "task"
    service = spec["service"]  # 24/7 service: its orchestrator + three agents, same limits
    assert [a["name"] for a in service] == ["autopilot", "intake", "watcher", "publisher"]
    assert all(len(a["tools"]) <= 5 and a["role"] and a["done"] for a in service)
    assert all(t["en"] and t["ru"] for a in service for t in a["tools"])  # Russian spec matches the code


def test_constraint_preview_explains_each_line(client):
    r = client.post("/api/constraints/preview", json={"dataset": "input.xlsx", "texts": [
        "1 курс заканчивает до 17:00", "абракадабра", "У группы XX-0000 свободна пятница"]}).json()
    assert [p["kind"] for p in r["parsed"]] == ["forbid_time", "forbid_time"]
    assert r["parsed"][1]["unknown"] == ["XX-0000"] and r["unparsed"] == ["абракадабра"]


def test_bad_ids_are_rejected(client):
    assert client.get("/api/runs/..%2F..%2Fsecret").status_code in (400, 404)
    assert client.get("/api/runs/no-such-run").status_code == 404
    assert client.get("/api/runs/no-such-run/files/report.md").status_code == 404
    assert client.post("/api/runs", json={"dataset": "nope.xlsx"}).status_code == 404
    assert client.post("/api/runs", json={"dataset": "input.xlsx", "llm": "gpt"}).status_code == 422


def test_end_to_end_run_through_the_api(client):
    r = client.post("/api/runs", json={"dataset": "input.xlsx", "term": 1, "programs": ["IT"], "llm": "offline",
                                       "solver_seconds": 6, "name": "pytest-web",
                                       "requirements": ["1 курс заканчивает до 17:00"]})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    assert client.post("/api/runs", json={"dataset": "input.xlsx", "llm": "offline"}).status_code == 409  # one at a time

    deadline = time.time() + 180
    status, offset, seen_running = None, 0, False
    while time.time() < deadline:
        status = client.get(f"/api/runs/{run_id}").json()
        ev = client.get(f"/api/runs/{run_id}/events", params={"offset": offset}).json()
        offset = ev["offset"]
        if status["status"] == "running":
            seen_running = True
        if status["status"] not in ("running", "cancelling"):
            break
        time.sleep(0.5)
    assert status["status"] == "ok", status
    assert status["params"]["requirements"] == ["1 курс заканчивает до 17:00"]
    assert status["result"]["load"]["max_share"] <= 0.4
    assert seen_running or status["elapsed_s"] < 10  # a very fast machine may finish before the first poll

    ev = client.get(f"/api/runs/{run_id}/events", params={"offset": 0}).json()
    kinds = {e["kind"] for e in ev["events"]}
    assert {"message", "tool_call", "llm_call"} <= kinds and ev["more"] is False

    tt = client.get(f"/api/runs/{run_id}/timetable").json()
    assert tt["rows"] and all(r["program"] == "IT" for r in tt["rows"]) and len(tt["times"]) == 12
    assert tt["times"][0] == "08:00-08:50"

    det = client.get(f"/api/runs/{run_id}/details").json()
    assert det["validation"]["hard_violations"] == 0
    assert det["constraint_specs"] and det["constraint_specs"][0]["kind"] == "forbid_time"
    assert any(c["task"] == "validate_chunk" for c in det["conversation"])

    assert "# Отчёт о прогоне" in client.get(f"/api/runs/{run_id}/report").text
    assert client.get(f"/api/runs/{run_id}/files/timetable.xlsx").status_code == 200
    assert client.get(f"/api/runs/{run_id}/files/.env").status_code == 404
    assert any(x["id"] == run_id for x in client.get("/api/runs").json())

    assert client.delete(f"/api/runs/{run_id}").status_code == 200
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_status_of_a_cancelled_run_lists_only_existing_files(tmp_path, monkeypatch):
    """A run stopped by the user has no timetable yet: the UI must not offer Excel and must
    show a calm 'stopped by the user' state instead of an error."""
    import json
    monkeypatch.setenv("TT_OUT", str(tmp_path))
    d = tmp_path / "20261010-000000-stopped"
    d.mkdir()
    (d / "events.jsonl").write_text("", encoding="utf-8")
    (d / "result.json").write_text(json.dumps({"status": "stopped", "reason": "run cancelled by the user",
                                               "wall_s": 4.3, "steps": 2}), encoding="utf-8")
    with TestClient(create_app(autopilot=False)) as c:
        st = c.get("/api/runs/20261010-000000-stopped").json()
        assert st["status"] == "stopped" and st["cancelled"] is True
        assert st["files"] == ["events.jsonl", "result.json"]
        assert c.get("/api/runs/20261010-000000-stopped/files/timetable.xlsx").status_code == 404
        listed = c.get("/api/runs").json()
        assert [r["id"] for r in listed] == ["20261010-000000-stopped"] and listed[0]["cancelled"] is True
