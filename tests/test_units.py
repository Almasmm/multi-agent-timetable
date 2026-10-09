"""Fast unit tests of the building blocks (no solver runs)."""
import pytest

from timetable.config import Settings
from timetable.core.agent import BaseAgent
from timetable.core.llm import strict_schema
from timetable.core.messages import Message
from timetable.domain.constraints import ConstraintSet, parse_offline
from timetable.domain.demand import blocks, partition, trimester_of
from timetable.domain.loader import normalize, read_workbook


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
