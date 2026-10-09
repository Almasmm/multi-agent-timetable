import pytest

from timetable.scenarios import SCENARIOS, run_scenario


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_scenario(sc, tmp_path):
    ok, errs, res = run_scenario(sc, tmp_path)
    assert ok, f"{sc.id} {sc.title}: {errs}"
