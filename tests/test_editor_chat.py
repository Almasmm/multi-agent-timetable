"""Manual editing (drag & drop draft), hard/soft rule tags and the agent chat - no solver, no network:
a toy week of two groups is loaded instead of the university workbook, Claude is replaced by a fake."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from timetable.config import Settings
from timetable.domain.constraints import apply_modes, parse_offline, split_mode, with_mode
from timetable.domain.models import Room, Session
from timetable.export.writers import write_csv
from timetable.service import chat as chat_mod
from timetable.service.chat import CHAT_AGENTS, AgentChat, ChatTools, parse_day, parse_time
from timetable.service.editor import ScheduleEditor, Workspace
from timetable.service.store import ServiceStore

G1, G2 = "IT-2301", "IT-2302"
ROOMS = [Room("C1.1.101P", 30, "P", 1), Room("C1.1.102P", 30, "P", 1), Room("C1.2.200L", 60, "L", 2), Room("C1.3.300K", 30, "K", 3)]
FRIDAY_OFF = {"kind": "forbid_time", "hard": True, "scope": {"groups": [G2]}, "days": ["Fri"], "start_hour": 8, "end_hour": 20,
              "source": f"У группы {G2} свободна пятница"}


def _sessions():
    mk = lambda sid, course, kind, groups, dur, comp: Session(  # noqa: E731
        id=sid, program="IT", year=1, course=course, kind=kind, groups=groups, size=20 * len(groups), duration=dur,
        online=False, venue="", component=comp, lecture_first=False)
    return [mk("calc#0", "Calculus 1", "practice", [G1], 2, "calc"), mk("calc#1", "Calculus 1", "practice", [G1], 2, "calc"),
            mk("phys#0", "Physics", "practice", [G2], 1, "phys"), mk("lec#0", "Programming", "lecture", [G1, G2], 2, "lec"),
            mk("lab#0", "Programming", "lab", [G2], 2, "lab")]


STARTS = {"calc#0": 0, "calc#1": 12, "phys#0": 0, "lec#0": 2, "lab#0": 24}  # Mon 08:00, Tue 08:00, Mon 08:00, Mon 10:00, Wed 08:00
ROOMS_OF = {"calc#0": "C1.1.101P", "calc#1": "C1.1.101P", "phys#0": "C1.1.102P", "lec#0": "C1.2.200L", "lab#0": "C1.3.300K"}


def _workspace() -> Workspace:
    return Workspace(_sessions(), STARTS, ROOMS_OF, ROOMS, [FRIDAY_OFF], {}, Settings())


# ------------------------------------------------------------------ rule tags
def test_mode_tags_round_trip_and_override_the_wording():
    assert split_mode("[soft:7] 1 курс заканчивает до 17:00") == ("1 курс заканчивает до 17:00", False, 7)
    assert split_mode("  [HARD]  text") == ("text", True, None) and split_mode("text") == ("text", None, None)
    assert with_mode("[hard] x", False, 30) == "[soft:20] x" and with_mode("[soft:3] x", None) == "x"
    texts = ["Желательно не ставить занятия до 9:00", "1 курс заканчивает до 17:00", "Не более 3 пар в день"]
    tagged = [(texts[0], True, None), (texts[1], False, 9), (texts[2], False, None)]
    cs = apply_modes(parse_offline(texts, ["IT"], [], []), tagged)
    soft_word, until, cap = cs.constraints
    assert soft_word.hard is True                       # "желательно" overridden by [hard]
    assert until.hard is False and until.weight == 9    # pinned soft with a weight
    assert cap.kind == "max_daily_hours" and cap.hard is True  # soft mode only applies to time windows


# ------------------------------------------------------------------ local checks of a move
def test_workspace_checks_every_hard_rule_of_a_move():
    ws = _workspace()
    assert ws.validate()["hard_violations"] == 0 and ws.base_hard == 0
    assert "другой блок" in ws.check_target("calc#0", 12 + 4)["reason"]          # Tue already has a Calculus block
    assert f"у группы {G1}" in ws.check_target("calc#0", 2)["reason"]             # the lecture stream is there
    assert "запрещено правилом" in ws.check_target("phys#0", 4 * 12)["reason"]   # Friday is off for G2
    assert "занята" in ws.check_target("phys#0", 1, room="C1.1.101P")["reason"]  # Calculus sits in that room
    assert "не компьютерный класс" in ws.check_target("lab#0", 36, room="C1.1.102P")["reason"]
    assert "не помещается" in ws.check_target("calc#0", 11)["reason"]
    ok = ws.check_target("calc#0", 24 + 4)
    assert ok["ok"] and ok["room"] == "C1.1.101P" and not ok["room_change"]
    assert len(ws.options("calc#0")) == 5 * 11  # 5 days x 11 two-hour starts


def test_move_keeps_zero_violations_and_reports_the_soft_change():
    ws = _workspace()
    rec = ws.move("phys#0", 1)  # Mon 09:00: G2 then has 09:00-11:50 without a gap
    assert rec["to"] == 1 and ws.validate()["hard_violations"] == 0
    assert "Physics" in rec["label"] and "Пн 09:00" in rec["label"]
    with pytest.raises(ValueError, match="у группы"):
        ws.move("phys#0", 2)


# ------------------------------------------------------------------ draft editor on top of a published version
@pytest.fixture()
def editor(tmp_path, monkeypatch):
    store = ServiceStore(tmp_path / "_service")
    store.set("profile", {"llm": "offline"})
    vid = store.add_version("r1", None, 10.0, {"kind": "generate", "term": 1, "programs": ["IT"], "config": "c", "fingerprint": "f"},
                            {"first": True, "total_hours": 0}, "v1")
    vdir = store.version_dir(vid)
    vdir.mkdir(parents=True)
    write_csv(vdir / "timetable.csv", _workspace().export())
    (vdir / "result.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    ed = ScheduleEditor(store, Settings().with_(out_root=tmp_path, llm_provider="offline"))
    monkeypatch.setattr(ed, "_load", lambda v: _workspace())
    yield ed
    store.close()


def test_draft_move_undo_and_publish_a_manual_version(editor):
    st = editor.state()
    assert st["base"] == 1 and st["count"] == 0 and st["hard"] == 0
    opts = editor.options(G2, None, 0, 0)
    assert opts["session"]["course"] == "Physics" and any(s["ok"] for s in opts["slots"])
    r = editor.move(G2, None, 0, 0, 0, 1)
    assert r["count"] == 1 and r["hard"] == 0
    rows = editor.state(with_rows=True)["rows"]
    assert {(x["day"], x["hour"]) for x in rows if x["group"] == G2 and x["course"] == "Physics"} == {(0, 1)}
    with pytest.raises(ValueError):
        editor.move(G1, None, 0, 0, 1, 0)  # second Calculus block on Tuesday
    assert editor.undo()["count"] == 0
    with pytest.raises(ValueError, match="нет правок"):
        editor.publish()
    editor.move(G2, None, 0, 0, 0, 1)
    editor._ws = None  # survives a restart: the draft is replayed from service.sqlite
    assert editor.state()["count"] == 1
    res = editor.publish("перенос по просьбе кафедры")
    store = editor.store
    v2 = store.current_version()
    assert res["version"] == v2["id"] == 2 and v2["metrics"]["kind"] == "manual" and v2["metrics"]["config"] == "c"
    assert v2["diff"]["hours_moved"] == 1 and "кафедры" in v2["announcement"]
    for name in ("timetable.csv", "timetable.xlsx", "starts.json", "rooms.json", "specs.json", "report.md"):
        assert (store.version_dir(2) / name).exists(), name
    assert json.loads((store.version_dir(2) / "starts.json").read_text(encoding="utf-8"))["phys#0"] == 1
    assert editor.state()["count"] == 0 and editor.state()["base"] == 2


def test_publish_refuses_a_stale_draft(editor):
    editor.move(G2, None, 0, 0, 0, 1)
    editor.store.add_version("r2", None, 9.0, {"kind": "improve"}, {}, "агенты опубликовали v2")
    assert editor.state()["stale"] is True
    with pytest.raises(RuntimeError, match="сбросьте"):
        editor.publish()


# ------------------------------------------------------------------ chat
def test_every_chat_agent_has_at_most_five_known_tools():
    tools = ChatTools.__new__(ChatTools)
    ChatTools.__init__(tools, None, None)
    for name, a in CHAT_AGENTS.items():
        assert 1 <= len(a["tools"]) <= 5, name
        assert all(t in tools.catalog for t in a["tools"]), name
    assert parse_day("ср") == 2 and parse_day("Friday") == 4 and parse_day(0) == 0 and parse_time("09:00") == 1
    with pytest.raises(ValueError):
        parse_time("21:00")


def test_offline_chat_commands(editor):
    chat = AgentChat(editor.store, editor, None, editor.settings)
    assert chat.provider()["name"] == "offline"
    r = chat.send("validator", "проверь расписание")
    assert "Жёстких нарушений: 0" in r["message"]["text"] and r["message"]["actions"][0]["tool"] == "validate"
    r = chat.send("scheduler", f"Перенеси {G2} Пн 08:00 на Пн 09:00")
    assert r["message"]["actions"][0]["ok"] and editor.state()["count"] == 1
    r = chat.send("scheduler", f"Перенеси {G1} Пн 08:00 на Вт 10:00")
    assert not r["message"]["actions"][0]["ok"] and "другой блок" in r["message"]["text"]
    r = chat.send("publisher", "опубликуй черновик")
    assert editor.store.current_version()["metrics"]["kind"] == "manual" and "v2" in r["message"]["text"]
    r = chat.send("constraints", "Желательно не ставить занятия после 18:00")
    assert editor.store.profile()["requirements"] == ["Желательно не ставить занятия после 18:00"]
    assert chat.send("constraints", "сделай правило 1 жёстким")["message"]["actions"][0]["tool"] == "update_rule"
    assert editor.store.profile()["requirements"] == ["[hard] Желательно не ставить занятия после 18:00"]
    assert [m["role"] for m in chat.history("validator")] == ["user", "assistant"]
    with pytest.raises(FileNotFoundError):
        chat.send("nobody", "hi")


class _FakeClaude:
    """Scripted stand-in for ClaudeLLM.turn: first a tool call, then (or on the last allowed round) text."""
    model = "fake-claude"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def turn(self, system, messages, tools, effort, final=False):
        self.calls.append({"tools": [t["name"] for t in tools], "final": final, "n": len(messages)})
        step = self.script.pop(0) if self.script else ("text", "Готово")
        if final or step[0] == "text":
            content = [SimpleNamespace(type="text", text=step[1] if step[0] == "text" else "итог по лимиту")]
        else:
            content = [SimpleNamespace(type="tool_use", id=f"t{len(self.calls)}", name=step[1], input=step[2])]
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_claude_tool_loop_runs_owned_tools_and_stops_at_the_limit(editor, monkeypatch):
    chat = AgentChat(editor.store, editor, None, editor.settings)
    monkeypatch.setattr(chat, "provider", lambda: {"name": "claude", "model": "fake", "label": "Claude · fake"})
    fake = _FakeClaude([("tool", "validate", {}), ("tool", "add_rule", {"text": "x"}), ("text", "Нарушений **нет**.")])
    monkeypatch.setattr(chat, "_claude", lambda: fake)
    r = chat.send("validator", "проверь")
    acts = r["message"]["actions"]
    assert [a["tool"] for a in acts] == ["validate", "add_rule"] and acts[0]["ok"] and not acts[1]["ok"]  # not its tool
    assert r["message"]["text"] == "Нарушений **нет**." and fake.calls[0]["tools"] == CHAT_AGENTS["validator"]["tools"]
    loop = _FakeClaude([("tool", "validate", {})] * 20)
    monkeypatch.setattr(chat, "_claude", lambda: loop)
    r = chat.send("validator", "ещё раз")
    assert len(loop.calls) == chat_mod.MAX_ROUNDS and loop.calls[-1]["final"] and r["message"]["text"] == "итог по лимиту"

    def broken():
        raise RuntimeError("network down")
    monkeypatch.setattr(chat, "_claude", broken)
    r = chat.send("validator", "проверь")  # provider failure -> offline rules, the chat keeps working
    assert "офлайн" in r["provider"]["label"] and "Жёстких нарушений" in r["message"]["text"]


# ------------------------------------------------------------------ web API
def test_rules_chat_and_edit_api(tmp_path, monkeypatch):
    monkeypatch.setenv("TT_OUT", str(tmp_path))
    monkeypatch.setenv("TT_LLM", "offline")
    from timetable.web.server import create_app
    with TestClient(create_app(autopilot=False)) as c:
        c.put("/api/autopilot/profile", json={"dataset": "input.xlsx", "llm": "offline"})
        assert c.get("/api/edit").json()["base"] is None
        assert c.post("/api/edit/move", json={"group": G1, "day": 0, "hour": 0, "to_day": 1, "to_hour": 0}).status_code == 404
        r = c.put("/api/rules", json={"rules": [{"text": "1 курс заканчивает до 17:00", "mode": "soft", "weight": 8},
                                                {"text": "Не более 3 пар в день", "mode": "auto"}]}).json()
        assert [x["mode"] for x in r["rules"]] == ["soft", None] and r["rules"][0]["weight"] == 8
        assert r["rules"][0]["spec"]["hard"] is False and r["rules"][1]["spec"]["kind"] == "max_daily_hours"
        assert c.get("/api/autopilot").json()["profile"]["requirements"][0] == "[soft:8] 1 курс заканчивает до 17:00"
        p = c.post("/api/rules/preview", json={"text": "У группы XX-9999 свободна пятница"}).json()
        assert p["unknown"] == ["XX-9999"]
        assert c.put("/api/rules", json={"rules": [{"text": "ок?", "mode": "medium"}]}).status_code == 422
        agents = c.get("/api/chat").json()
        assert agents["provider"]["name"] == "offline" and len(agents["agents"]) == len(CHAT_AGENTS)
        r = c.post("/api/chat/constraints", json={"text": "Покажи все правила"}).json()
        assert "Правил: 2" in r["message"]["text"]
        assert len(c.get("/api/chat/constraints").json()["messages"]) == 2
        assert c.delete("/api/chat/constraints").json() == {"cleared": "constraints"}
        assert c.post("/api/chat/nobody", json={"text": "hi"}).status_code == 404
