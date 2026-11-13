"""University data desk: workbook <-> four categories, cell checks, automatic fixes, per-category import (Excel or CSV,
Russian or English headers), saving into a working copy with history, and the web API. Uploads go to a temporary
folder (the last entry of DATASET_DIRS is swapped), so data/ is never touched."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from timetable.config import DATASET_DIRS, ROOT
from timetable.domain.loader import EXTRA_SHEETS
from timetable.domain.loader import normalize, read_workbook
from timetable.service import datasets as D

INPUT = ROOT / "data" / "input.xlsx"


@pytest.fixture()
def uploads(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    real = DATASET_DIRS[-1]
    DATASET_DIRS[-1] = d
    yield d
    DATASET_DIRS[-1] = real


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_real_workbook_becomes_tables_and_loader_issues_point_to_rows():
    tables = D.read_tables(INPUT)
    assert len(tables["rooms"]) == 105 and len(tables["timeslots"]) == 12
    assert tables["timeslots"][0] == {"pair": 1, "start": "08:00", "end": "08:50"} and tables["timeslots"][-1]["end"] == "19:50"
    assert len({r["program"] for r in tables["courses"]}) == 13
    res = D.check(INPUT, tables)
    s = res["summary"]
    assert s["groups"] == 270 and s["programs"] == 13 and s["rooms"] == 104  # what the Data agent itself loads
    assert s["load"]["IT"]["1"]["groups"] > 0 and s["load"]["IT"]["1"]["hours"][0] > 0 and s["day_span"] == ["08:00", "19:50"]
    # dirty source: 'onlline' typos are cell errors, the loader's own repairs are mapped back to their rows
    typo = next(c for c in res["cells"] if c["c"] == "delivery_mode")
    assert typo["level"] == "error" and tables["courses"][typo["r"]]["delivery_mode"] == "onlline"
    ts = next(i for i in res["issues"] if "TS-2101" in i["text"])
    assert ts["t"] == "groups" and tables["groups"][ts["r"]]["group_code"] == "TS-2101" and "учебного плана" in ts["text"]


def test_cell_checks_types_ranges_enums_duplicates_and_times():
    rooms = [{"room_code": "A1", "capacity": "30", "room_type": "p", "available": "да"},
             {"room_code": "a1", "capacity": "много", "room_type": "X", "available": "может"},
             {"room_code": "", "capacity": 0, "room_type": "L"}]
    clean, errs = D._clean_rows("rooms", rooms)
    assert clean[0] == {"room_code": "A1", "capacity": 30, "room_type": "P", "available": True, "floor": None, "building": None, "features": None}
    by = {(e["r"], e["c"]): e["text"] for e in errs}
    assert by[(1, "capacity")] == "нужно целое число" and by[(1, "room_type")].startswith("допустимо")
    assert by[(1, "available")] == "да или нет" and "повторяется" in by[(1, "room_code")]
    assert by[(2, "room_code")] == "обязательное поле" and by[(2, "capacity")] == "от 1 до 2000"
    _, errs = D._clean_rows("groups", [{"group_code": "ИТ 23", "year": 9, "headcount": 20}])
    assert {e["c"] for e in errs} == {"group_code", "year"}
    pairs = [{"pair": h + 1, "start": f"{8 + h}.00", "end": f"{8 + h:02d}:50"} for h in range(12)]
    pairs[3]["end"], pairs[5]["start"] = "10:30", "25:00"
    clean, errs = D._clean_rows("timeslots", pairs)
    assert clean[0]["start"] == "08:00"
    assert {(e["r"], e["c"]) for e in errs} == {(3, "end"), (5, "start")}  # end before start, not a time


def test_autofix_repairs_what_the_loader_would_guess():
    tables = D.read_tables(INPUT)
    fixed = D.autofix(tables)
    kinds = {(c["c"], c.get("removed", False)) for c in fixed["changes"]}
    assert ("delivery_mode", False) in kinds and ("room_code", True) in kinds  # typo + duplicate room C1.3.248
    res = D.check(INPUT, fixed["tables"])
    assert res["errors"] == 0 and res["summary"]["rooms"] == 104 and res["summary"]["groups"] == 270


def test_import_one_category_from_excel_or_csv_with_russian_headers():
    rooms = _xlsx({"Лист1": [["Аудитория", "Вместимость", "Тип", "Примечание"], ["B2.100", 40, "лекционная", "новая"], ["B2.101", "25", "P", ""]]})
    r = D.import_file("rooms.xlsx", rooms, "rooms")
    assert r["tables"]["rooms"] == [{"room_code": "B2.100", "capacity": 40, "room_type": "L"}, {"room_code": "B2.101", "capacity": 25, "room_type": "P"}] \
        or r["tables"]["rooms"][0]["room_code"] == "B2.100"
    assert r["found"]["rooms"]["ignored"] == ["Примечание"] and set(r["found"]["rooms"]["columns"]) == {"room_code", "capacity", "room_type"}
    csv_text = "Группа;Курс;Студентов\nIT-2599;1;25\nSE-2599;1;\n".encode("utf-8-sig")
    g = D.import_file("groups.csv", csv_text, "groups")["tables"]["groups"]
    assert g[0] == {"group_code": "IT-2599", "year": 1, "headcount": 25} and g[1]["headcount"] is None
    plan = _xlsx({"IT": [["Дисциплина", "Триместр", "Лекции", "Практики"], ["Algorithms", 4, 30, 20]],
                  "Notes": [["что-то"], ["другое"]]})
    c = D.import_file("plan.xlsx", plan, "courses")
    assert c["tables"]["courses"][0]["program"] == "IT" and c["found"]["courses"]["sheets"] == ["IT"]
    with pytest.raises(ValueError, match="Аудитории"):
        D.import_file("x.xlsx", plan, "rooms")
    everything = D.import_file("template.xlsx", D.template())  # the single template fills every category
    assert set(everything["tables"]) == {"rooms", "groups", "courses", "timeslots"} and not everything["missing"]
    assert len(everything["tables"]["timeslots"]) == 12


def test_working_copy_save_history_and_restore(uploads):
    with pytest.raises(ValueError):
        D.save(INPUT, D.read_tables(INPUT), None)  # originals are read-only
    copy = D.copy(INPUT, "university")
    assert copy.parent == uploads and copy.name == "university.xlsx" and D.editable(copy)
    tables = D.autofix(D.read_tables(copy))["tables"]
    with pytest.raises(ValueError, match="ошиб"):
        D.save(copy, D.read_tables(copy), None)  # the raw copy still has the 'onlline' typos
    tables["rooms"][0]["capacity"] = 999
    tables["groups"].append({"group_code": "IT-2599", "year": 1, "headcount": 25})
    tables["courses"] = [r for r in tables["courses"] if r["program"] != "MT"]  # a whole programme removed
    tables["timeslots"][0]["start"] = "08:30"
    mtime = copy.stat().st_mtime
    res = D.save(copy, tables, mtime, "тест")
    assert res["errors"] == 0
    with pytest.raises(RuntimeError):
        D.save(copy, tables, mtime)  # stale editor: the file changed since it was opened
    ds = normalize(read_workbook(copy), 10)  # what the Data agent reads now
    assert "MT" not in read_workbook(copy) and any(g.code == "IT-2599" for g in ds.groups)
    assert max(r.capacity for r in ds.rooms) == 999 and ds.slot_labels[0] == "08:30-08:50" and len(read_workbook(copy)["Timeslots"]) == 73
    hist = D.history(copy)
    assert len(hist) == 1 and hist[0]["note"] == "тест"
    D.restore(copy, hist[0]["stamp"])
    assert "MT" in read_workbook(copy) and len(D.history(copy)) == 2  # the restore itself is undoable


def test_upload_checks_the_workbook_and_never_shadows_an_original(uploads):
    with pytest.raises(ValueError, match="листов"):
        D.store_upload("empty.xlsx", _xlsx({"Sheet": [["a"]]}))
    with pytest.raises(ValueError, match="Excel"):
        D.store_upload("notes.xlsx", b"not a workbook")
    assert not list(uploads.glob("*.xlsx")) and not list(uploads.glob(".incoming*"))
    p = D.store_upload("input.xlsx", INPUT.read_bytes())
    assert p.parent == uploads and p.name != "input.xlsx"
    tpl = uploads / "tpl.xlsx"
    tpl.write_bytes(D.template())
    ds = normalize(read_workbook(tpl), 10)  # the template is a valid, tiny dataset
    assert len(ds.groups) == 2 and len(ds.rooms) == 3 and len(ds.courses) == 5 and len(ds.slot_labels) == 12


def test_data_api(uploads, tmp_path, monkeypatch):
    monkeypatch.setenv("TT_OUT", str(tmp_path / "runs"))
    from timetable.web.server import create_app
    with TestClient(create_app(autopilot=False)) as c:
        w = c.get("/api/data/working").json()
        assert w["dataset"]["id"] == "input.xlsx" and w["dataset"]["active"] and not w["dataset"]["editable"]
        assert w["spec"]["timeslots"]["fixed"] and len(w["tables"]["timeslots"]) == 12 and w["check"]["summary"]["groups"] == 270
        assert c.get("/api/data/..%2F.env").status_code == 404 and c.get("/api/data/nope.xlsx").status_code == 404
        tables = {k: w["tables"][k] for k in ("rooms", "groups", "courses")}
        fixed = c.post("/api/data/input.xlsx/autofix", json={"tables": tables}).json()["tables"]
        assert "timeslots" not in fixed
        fixed["rooms"][1]["capacity"] = "abc"
        chk = c.post("/api/data/input.xlsx/check", json={"tables": fixed}).json()
        assert chk["errors"] == 1 and chk["cells"][0]["c"] == "capacity"
        assert c.put("/api/data/input.xlsx", json={"tables": fixed}).status_code == 400  # errors block saving
        fixed["rooms"][1]["capacity"] = 40
        # the first save of the original creates the working copy and the agents switch to it
        saved = c.put("/api/data/input.xlsx", json={"tables": fixed, "base_mtime": w["dataset"]["mtime"]}).json()
        assert saved["errors"] == 0 and saved["dataset"]["id"] == "university.xlsx" and saved["dataset"]["active"]
        assert c.get("/api/autopilot").json()["profile"]["dataset"] == "university.xlsx"
        assert INPUT.exists() and (uploads / "university.xlsx").exists()
        w2 = c.get("/api/data/working").json()
        assert w2["dataset"]["editable"] and w2["tables"]["rooms"][1]["capacity"] == 40 and len(w2["history"]) == 1
        # per-category import: preview only, nothing saved
        imp = c.post("/api/data/university.xlsx/import", params={"table": "groups"},
                     files={"file": ("g.csv", "group_code,year,headcount\nIT-2599,1,25\n".encode())}).json()
        assert imp["tables"]["groups"] == [{"group_code": "IT-2599", "year": 1, "headcount": 25}]
        bad = c.post("/api/data/university.xlsx/import", params={"table": "rooms"}, files={"file": ("g.csv", b"a,b\n1,2\n")})
        assert bad.status_code == 400 and "Аудитории" in bad.json()["error"]
        tpl = c.get("/api/data/template")
        assert tpl.status_code == 200 and tpl.content[:2] == b"PK"
        allcats = c.post("/api/data/university.xlsx/import", files={"file": ("t.xlsx", tpl.content)}).json()
        assert set(allcats["tables"]) == {"rooms", "groups", "courses", "timeslots"}
        assert c.delete("/api/data/university.xlsx").status_code == 400  # the agents' working set
        assert c.get("/api/data/input.xlsx/download").content == INPUT.read_bytes()


def test_readiness_lists_what_blocks_generation(uploads):
    dirty = D.readiness(INPUT)
    assert not dirty["ready"] and dirty["errors"] == 11 and dirty["warnings"] >= 2 and dirty["fixed"] >= 1
    clean = uploads / "clean.xlsx"
    clean.write_bytes(D.template())
    assert D.readiness(clean)["ready"] and D.readiness(clean)["problems"] == 0


def test_the_api_refuses_to_generate_on_dirty_data(uploads, tmp_path, monkeypatch):
    monkeypatch.setenv("TT_OUT", str(tmp_path / "runs"))
    from timetable.web.server import create_app
    with TestClient(create_app(autopilot=False)) as c:
        g = c.get("/api/generate").json()
        assert g["data"]["ready"] is False and g["data"]["gate"] is True and g["data"]["items"]
        r = c.post("/api/generate")
        assert r.status_code == 409 and "данные вуза не готовы" in r.json()["error"]
        assert c.post("/api/runs", json={"dataset": "input.xlsx", "programs": ["IT"]}).status_code == 409


def test_dataset_folders_unchanged():
    """Guard: the real uploads folder is restored after the swapped tests."""
    assert DATASET_DIRS[-1] == ROOT / "data" / "uploads" and INPUT.exists()


def test_working_dataset_falls_back_when_its_file_is_gone(uploads, tmp_path, monkeypatch):
    monkeypatch.setenv("TT_OUT", str(tmp_path / "runs"))
    from timetable.web.server import create_app
    with TestClient(create_app(autopilot=False)) as c:
        prof = c.get("/api/autopilot").json()["profile"]
        (uploads / "gone.xlsx").write_bytes(INPUT.read_bytes())
        c.put("/api/autopilot/profile", json={**prof, "dataset": "gone.xlsx"})
        (uploads / "gone.xlsx").unlink()
        assert c.get("/api/data/working").json()["dataset"]["id"] == "input.xlsx"
        assert c.get("/api/autopilot").json()["profile"]["dataset"] == "input.xlsx"


def _plan_xlsx() -> bytes:
    """A small official curriculum (УП) laid out like AITU's: title block, two-level header, disciplines."""
    rows = [["УЧЕБНЫЙ ПЛАН"], ["на 2025-2028 учебные годы"], ['для Модульной образовательной программы 6В06101 "Компьютерные науки"'],
            ["по направлению подготовки 6B061 - Информационно-коммуникационные технологии"], ["Год поступления: 2025"],
            ["Шифр модуля", "Цикл дисциплины", "Компонент дисциплины", "Код дисциплины", "Наименование дисциплины", None, None,
             "Академические кредиты", "Триместр изучения", "форма контроля", "Количество часов"],
            [None] * 10 + ["Всего", "Аудиторная работа"],
            [None] * 11 + ["Лекции", "Лабораторные", "Практические", "СРОП", "СРО"],
            [None] * 4 + ["на английском", "на русском", "на казахском"],
            [1, "БД", "ВК", "MATH 1115", "Calculus 1", "Матанализ 1", "", 5, 1, "экз", 150, 30, "", 20, 10, 90],
            [2, "БД", "ВК", "CODE404", "Introduction to Programming 1", "", "", 5, 1, "экз", 150, 20, 30, "", 10, 90],
            [3, "БД", "ВК", "UP 1211", "Educational Practice", "", "", 2, 3, "отчет", 60, "", "", 60],
            [4, "Итоговая аттестация", None, "", "Diploma", "", "", 8, "", "", 240],
            ["Итого по учебному плану", None, None, None, None, None, None, 240]]
    return _xlsx({"УП": rows})


def test_official_curriculum_is_accepted_as_it_is():
    r = D.import_file("Компьютерные науки.xlsx", _plan_xlsx(), "courses", {"programs": {"6B06101": "IT"}, "modes": {"calculus 1": "online"}})
    rows = r["tables"]["courses"]
    assert [x["course_name"] for x in rows] == ["Calculus 1", "Introduction to Programming 1"]  # practice and attestation skipped
    assert rows[0] == {"course_name": "Calculus 1", "trimester": 1, "credits": 5, "lecture_slots": 30, "practice_slots": 20, "lab_slots": None,
                       "delivery_mode": "online", "lecture_precedes_practice": True, "program": "IT"}
    assert rows[1]["lab_slots"] == 30 and rows[1]["delivery_mode"] == "offline"
    f = r["found"]["courses"]
    assert f["plan"][0]["edu_code"] == "6B06101" and f["plan"][0]["years"] == "2025-2028" and "Educational Practice" in f["notes"][0]
    assert D.import_file("CS 25-28.xlsx", _plan_xlsx(), "courses")["tables"]["courses"][0]["program"] == "CS"  # code from the file name
    with pytest.raises(ValueError, match="код программы"):
        D.import_file("план.xlsx", _plan_xlsx(), "courses")


def test_template_is_a_valid_dataset_with_instruction_sheets(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_bytes(D.template())
    sheets = read_workbook(p)
    assert {"README", "Programs", "Rooms", "Groups", "Timeslots", "IT", *EXTRA_SHEETS} <= set(sheets)
    ds = normalize(sheets, 10)
    assert not ds.issues and len(ds.courses) == 5  # TeacherLoad / Electives have course_name too, but are not curricula
    assert sheets["Teachers"][0][:2] == ("teacher_id", "full_name") or list(sheets["Teachers"][0][:2]) == ["teacher_id", "full_name"]


def test_extra_sheets_survive_import_and_save(uploads):
    """Teachers, load, availability... are collected now: a full upload carries them, saving keeps them."""
    imp = D.import_file("aitu.xlsx", D.template())
    assert set(imp["extra"]) == set(EXTRA_SHEETS) and imp["extra"]["Teachers"][1][0] == "T001"
    assert {r["program"] for r in imp["tables"]["courses"]} == {"IT"}  # TeacherLoad rows are not courses
    copy = D.copy(INPUT, "university")
    tables = D.autofix(D.read_tables(copy))["tables"]
    tables["groups"][0]["admission_year"], tables["rooms"][0]["features"] = 2023, "projector"
    D.save(copy, tables, None, "", {"Teachers": imp["extra"]["Teachers"], "Calendar": imp["extra"]["Calendar"]})
    assert D.extra_counts(copy) == {"Teachers": 2, "Calendar": 1}
    back = D.read_tables(copy)
    assert back["groups"][0]["admission_year"] == 2023 and back["rooms"][0]["features"] == "projector"  # new columns kept
    D.save(copy, back, None)  # a later save without uploads keeps the extra sheets
    assert D.extra_counts(copy)["Teachers"] == 2 and normalize(read_workbook(copy), 10).groups
    with pytest.raises(ValueError, match="неизвестные листы"):
        D.save(copy, back, None, "", {"Secret": [["x"]]})
