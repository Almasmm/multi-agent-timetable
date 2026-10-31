"""Fast unit tests of the building blocks (no solver runs)."""
import pytest

from timetable.config import Settings
from timetable.core.agent import BaseAgent
from timetable.core.llm import OpenAILLM, resolve_provider, strict_schema
from timetable.core.messages import Message
from timetable.domain.constraints import ConstraintSet, parse_offline
from timetable.domain.demand import blocks, partition, trimester_of
from timetable.domain.loader import normalize, read_workbook
from timetable.domain.models import Session
from timetable.solver.evolve import evolve
from timetable.domain.constraints import ConstraintIndex


@pytest.fixture(scope="module")
def ds():
    st = Settings()
    return normalize(read_workbook(st.data_path), st.weeks_per_term)


def test_blocks_are_pairs():
    assert blocks(3) == [2, 1] and blocks(5) == [2, 2, 1] and blocks(2) == [2]


def test_trimester_mapping():
    assert trimester_of(1, 1) == 1 and trimester_of(2, 1) == 4 and trimester_of(2, 2) == 5


def test_loader_repairs_dirty_data(ds):
    issues = " ".join(i["issue"] for i in ds.issues)
    assert "onlline" in issues  # typo in delivery_mode fixed
    assert "Cyrillic" in issues  # С1.3.248 -> C1.3.248
    assert all(r.code.isascii() for r in ds.rooms)
    assert not any(g.program == "TS" for g in ds.groups)  # group without curriculum skipped


def test_offline_parser_ru_en():
    cs = parse_offline(["1 курс заканчивает до 17:00", "No classes on Saturday",
                        "Аудитория C1.3.361 недоступна в среду", "Желательно не ставить занятия до 9:00",
                        "не более 3 пар в день", "абракадабра"],
                       ["IT", "SE"], ["Physical Education"], ["C1.3.361"])
    kinds = [c.kind for c in cs.constraints]
    assert kinds == ["forbid_time", "forbid_time", "room_unavailable", "forbid_time", "max_daily_hours"]
    assert cs.constraints[0].scope.years == [1] and cs.constraints[0].start_hour == 17
    assert cs.constraints[1].days == ["Sat"]
    assert cs.constraints[2].days == ["Wed"] and cs.constraints[2].scope.rooms == ["C1.3.361"]
    assert cs.constraints[3].hard is False and cs.constraints[3].end_hour == 9
    assert cs.constraints[4].value == 6
    assert cs.unparsed == ["абракадабра"]


def test_strict_schema_for_structured_outputs():
    sch = strict_schema(ConstraintSet)
    assert sch["additionalProperties"] is False
    item = sch["properties"]["constraints"]["items"]
    assert "$ref" not in str(sch) and item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_message_reply_roundtrip():
    m = Message(sender="orchestrator", recipient="data", intent="task", task="load_data")
    r = m.reply({"sessions": 3})
    assert r.intent == "result" and r.correlation_id == m.id and r.recipient == "orchestrator"
    e = m.reply(error="boom")
    assert e.intent == "error" and not e.ok
    assert Message.model_validate_json(e.model_dump_json()) == e


def test_max_five_tools():
    class Greedy(BaseAgent):
        def register_tools(self):
            for i in range(6):
                self.add_tool(f"t{i}", lambda: None, "x")

    with pytest.raises(ValueError):
        Greedy.__new__(Greedy).__init__(ctx=None)


def test_partition_respects_target(ds):
    from timetable.domain.demand import build_sessions

    sessions, _ = build_sessions(ds, 1, 2, 50)
    chunks = partition(sessions, 60)
    unit_groups = {}
    for s in sessions:
        unit_groups.setdefault(s.unit, set()).update(s.groups)
    for c in chunks:
        assert len(c) == 1 or sum(len(unit_groups[u]) for u in c) <= 60
    assert sorted(u for c in chunks for u in c) == sorted(unit_groups)


class _FakeCompletions:
    """Records the request and returns a canned OpenAI-style response."""

    def __init__(self, content, finish_reason="stop", refusal=None):
        self.content, self.finish_reason, self.refusal, self.calls = content, finish_reason, refusal, []

    def create(self, **kwargs):
        from types import SimpleNamespace as NS

        self.calls.append(kwargs)
        msg = NS(content=self.content, refusal=self.refusal)
        return NS(choices=[NS(message=msg, finish_reason=self.finish_reason)],
                  usage=NS(prompt_tokens=120, completion_tokens=30))


def _fake_client(fake):
    from types import SimpleNamespace as NS

    return NS(chat=NS(completions=fake))


def test_openai_structured_request_uses_strict_json_schema():
    fake = _FakeCompletions('{"constraints": [], "unparsed": ["x"]}')
    llm = OpenAILLM("gpt-5-mini", timeout=5, client=_fake_client(fake))
    result, usage = llm.structured("sys", "user", ConstraintSet, effort="low")
    assert result.unparsed == ["x"] and usage == {"tokens_in": 120, "tokens_out": 30}
    req = fake.calls[0]
    assert req["model"] == "gpt-5-mini" and req["reasoning_effort"] == "low"
    assert req["messages"][0] == {"role": "system", "content": "sys"}
    fmt = req["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_openai_text_and_failure_modes():
    fake = _FakeCompletions("- итог")
    llm = OpenAILLM("gpt-4.1", timeout=5, client=_fake_client(fake))
    assert llm.text("s", "u", "low")[0] == "- итог"
    assert "reasoning_effort" not in fake.calls[0] and "response_format" not in fake.calls[0]
    with pytest.raises(RuntimeError, match="refused"):
        OpenAILLM("gpt-5", 5, _fake_client(_FakeCompletions(None, refusal="no"))).text("s", "u", "low")
    with pytest.raises(RuntimeError, match="truncated"):
        OpenAILLM("gpt-5", 5, _fake_client(_FakeCompletions("{", finish_reason="length"))).text("s", "u", "low")


def test_provider_resolution_prefers_openai_then_claude_then_offline(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("timetable.core.llm._has_claude_credentials", lambda: False)
    assert resolve_provider("auto")[0] == "offline" and resolve_provider("openai")[0] == "offline"
    assert resolve_provider("offline") == ("offline", "offline mode requested")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve_provider("auto") == ("openai", "") and resolve_provider("openai") == ("openai", "")
    assert resolve_provider("claude") == ("claude", "")


def _toy_sessions():
    """Two groups, four sessions each (one 2-hour block), two share a lecture stream."""
    mk = lambda i, groups, dur, comp: Session(id=f"s{i}", program="IT", year=1, course=f"C{comp}", kind="practice",
                                               groups=groups, size=20, duration=dur, online=False, venue="",
                                               component=f"{comp}|{'+'.join(groups)}", lecture_first=False)
    return [mk(0, ["G1"], 2, "a"), mk(1, ["G1"], 1, "b"), mk(2, ["G1"], 2, "a"), mk(3, ["G2"], 2, "c"),
            mk(4, ["G2"], 1, "d"), mk(5, ["G1", "G2"], 2, "lec")]


def test_genetic_seed_is_conflict_free_on_a_toy_problem():
    sessions = _toy_sessions()
    cidx = ConstraintIndex([], 12, 8, 8)
    quotas = {"ROOMS": [2] * 72}
    res = evolve(sessions, cidx, quotas, caps=[], days=[0, 1, 2], H=12, time_limit=0.5, seed=1, population=10)
    assert res.ok and len(res.starts) == len(sessions)
    assert res.stats["best_hard"] == 0 and res.stats["generations"] >= 1
    assert res.stats["best_fitness"] <= res.stats["first_fitness"]
    # same component -> different days; a group never has two classes in one slot
    by_id = {s.id: s for s in sessions}
    assert res.starts["s0"] // 12 != res.starts["s2"] // 12
    busy = set()
    for sid, t in res.starts.items():
        for g in by_id[sid].groups:
            for u in range(t, t + by_id[sid].duration):
                assert (g, u) not in busy
                busy.add((g, u))


def test_genetic_reports_unseedable_sessions():
    sessions = _toy_sessions()
    cidx = ConstraintIndex([], 12, 8, 8)
    res = evolve(sessions, cidx, {"ROOMS": [0] * 72}, caps=[], days=[0], H=12, time_limit=0.2, seed=1)
    assert not res.ok and "no admissible slot" in res.diagnostics[0]
