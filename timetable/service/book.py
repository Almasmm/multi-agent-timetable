"""The university workbook as people fill it in: one Excel file with an instruction sheet, a programme reference,
rooms, groups, the time grid, one curriculum sheet per programme and the extra data a real timetable needs
(teachers, their load and availability, subgroups, electives, room closures, calendar, settings).

Headers carry comments, coded columns have drop-down lists, the first row and column are frozen, tab colours show
what the agents use now (blue) and what is collected for the next model versions (orange). Used for the platform's
single template and the master dataset in ``master-dataset/``. The loader skips ``INFO_SHEETS``."""
from __future__ import annotations

import io

from ..domain.loader import INFO_SHEETS  # noqa: F401 - re-exported for callers
from ..domain.plan import PROGRAM_COLS

BLUE, SOFT, ORANGE, ORANGE_SOFT, GREY, WARN = "1F5FD1", "E8F0FD", "C2410C", "FDE7D6", "64748B", "FDF1E1"
DAYS = "Mon,Tue,Wed,Thu,Fri,Sat"

# column, required, what to write, example; a "≈" in front of the description = collected, not used by the agents yet
COLUMN_DOCS: dict[str, list[tuple[str, str, str, str]]] = {
    "Rooms": [("room_code", "да", "Код аудитории латиницей", "C1.2.240"),
              ("capacity", "да", "Число мест, целое", "30"),
              ("room_type", "да", "L — лекционная, P — практическая, K — компьютерный класс (для лабораторных)", "P"),
              ("available", "нет", "TRUE — используется, FALSE — закрыта (по умолчанию TRUE)", "TRUE"),
              ("floor", "нет", "Этаж", "2"),
              ("building", "нет", "≈ Корпус", "C1"),
              ("features", "нет", "≈ Оснащение через «;»: computers; electronics; projector; sports", "projector")],
    "Groups": [("group_code", "да", "Буквы программы (= имя листа учебного плана), дефис, номер", "IT-2301"),
               ("department", "нет", "Код кафедры", "1"),
               ("year", "да", "Курс: 1, 2, 3…", "1"),
               ("headcount", "да", "Реальное число студентов — от него зависит выбор аудитории", "24"),
               ("admission_year", "нет", "≈ Год набора: по нему выбирается версия учебного плана", "2025"),
               ("study_language", "нет", "≈ Язык обучения: kz, ru, en", "en")],
    "<программа>": [("course_name", "да", "Название дисциплины (как в УП, на английском)", "Calculus 1"),
                    ("trimester", "да", "Сквозной триместр: 1–3 — 1 курс, 4–6 — 2 курс, 7–9 — 3 курс", "1"),
                    ("credits", "нет", "Академические кредиты", "5"),
                    ("lecture_slots", "нет*", "Часов лекций за триместр (столбец «Лекции» УП)", "30"),
                    ("practice_slots", "нет*", "Часов практик за триместр (столбец «Практические» УП)", "20"),
                    ("lab_slots", "нет*", "Часов лабораторных за триместр (столбец «Лабораторные» УП)", ""),
                    ("delivery_mode", "нет", "offline — очно, online — дистанционно (по умолчанию offline)", "offline"),
                    ("lecture_precedes_practice", "нет", "TRUE — лекция недели раньше практики (по умолчанию TRUE)", "TRUE"),
                    ("course_code", "нет", "≈ Код дисциплины из УП", "MATH 1115"),
                    ("component", "нет", "≈ ОК — обязательный, ВК — вузовский, КВ — по выбору", "ВК"),
                    ("subgroups", "нет", "≈ На сколько подгрупп делится группа на практиках и лабораторных", "1"),
                    ("room_feature", "нет", "≈ Нужное оснащение аудитории: computers, electronics…", "")],
    "Timeslots": [("slot_id", "да", "Код слота", "MON-01"), ("day", "да", "Mon, Tue, Wed, Thu, Fri, Sat", "Mon"),
                  ("slot_index", "да", "Номер слота 1–72 по порядку (6 дней × 12 пар)", "1"),
                  ("start", "да", "Начало пары, ЧЧ:ММ", "08:00"), ("end", "да", "Конец пары, ЧЧ:ММ", "08:50")],
    "Programs": [("program", "да", "Код программы = имя листа учебного плана", "IT"),
                 ("edu_code", "нет", "Шифр образовательной программы", "6B06101"),
                 ("name_ru", "нет", "Название программы", "Компьютерные науки"),
                 ("direction / gop / degree / form", "нет", "Направление, ГОП, степень, форма обучения — из шапки УП", ""),
                 ("years / admission_year", "нет", "Годы обучения и год поступления", "2022-2025 / 2022")],
}

# extra sheets: title, purpose, columns (column, required, what, example), drop-down lists {column: values}, example rows
EXTRA: dict[str, dict] = {
    "Teachers": {"title": "Преподаватели", "why": "кто ведёт занятия и сколько может вести",
                 "cols": [("teacher_id", "да", "Уникальный код преподавателя", "T001"),
                          ("full_name", "да", "ФИО", "Иванов Иван Иванович"),
                          ("department", "нет", "Кафедра", "Computer Engineering"),
                          ("position", "нет", "Должность", "доцент"),
                          ("email", "нет", "Почта для рассылки расписания", "i.ivanov@astanait.edu.kz"),
                          ("max_hours_week", "нет", "Максимум аудиторных часов в неделю", "20"),
                          ("max_hours_day", "нет", "Максимум часов в день", "6"),
                          ("max_days_week", "нет", "Максимум рабочих дней в неделю", "5"),
                          ("languages", "нет", "Языки преподавания через «;»: en; ru; kz", "en; ru")],
                 "lists": {}, "example": [["T001", "Иванов Иван Иванович", "Computer Engineering", "доцент", "", 20, 6, 5, "en; ru"],
                                          ["T002", "Сериккызы Айгерим", "Mathematics", "ст. преподаватель", "", 18, 6, 5, "en; kz"]]},
    "TeacherLoad": {"title": "Нагрузка преподавателей", "why": "кто какую дисциплину ведёт у каких групп (распределение нагрузки)",
                    "cols": [("program", "да", "Код программы", "IT"),
                             ("course_name", "да", "Дисциплина — как в учебном плане", "Calculus 1"),
                             ("trimester", "да", "Триместр", "1"),
                             ("kind", "да", "lecture — лекция, practice — практика, lab — лабораторная", "lecture"),
                             ("groups", "да", "Группы через запятую или ALL — все группы курса", "IT-2501, IT-2502"),
                             ("teacher_id", "да", "Код из листа Teachers", "T001"),
                             ("stream", "нет", "Номер потока: строки с одним номером идут одним занятием", "1"),
                             ("subgroup", "нет", "Подгруппа, если группа делится (A, B, KZ, RU)", "")],
                    "lists": {"kind": "lecture,practice,lab"},
                    "example": [["IT", "Calculus 1", 1, "lecture", "IT-2501, IT-2502", "T002", 1, ""],
                                ["IT", "Calculus 1", 1, "practice", "IT-2501", "T002", "", ""],
                                ["IT", "Calculus 1", 1, "practice", "IT-2502", "T002", "", ""],
                                ["IT", "Introduction to Programming 1", 1, "lab", "IT-2501", "T001", "", "A"],
                                ["IT", "Introduction to Programming 1", 1, "lab", "IT-2501", "T001", "", "B"]]},
    "TeacherUnavailability": {"title": "Когда преподаватель не может", "why": "занятость и пожелания преподавателей",
                              "cols": [("teacher_id", "да", "Код из листа Teachers", "T001"),
                                       ("day", "да", "Mon, Tue, Wed, Thu, Fri, Sat", "Mon"),
                                       ("from", "да", "С, ЧЧ:ММ", "08:00"), ("to", "да", "До, ЧЧ:ММ", "12:00"),
                                       ("mode", "нет", "hard — нельзя, soft — нежелательно (по умолчанию hard)", "hard"),
                                       ("weeks", "нет", "Недели триместра: 1-5, 8; пусто — все", ""),
                                       ("reason", "нет", "Причина", "совмещение")],
                              "lists": {"day": DAYS, "mode": "hard,soft"},
                              "example": [["T001", "Mon", "08:00", "12:00", "hard", "", "совмещение"],
                                          ["T002", "Sat", "08:00", "19:50", "soft", "", "пожелание"]]},
    "Subgroups": {"title": "Подгруппы", "why": "деление группы: языки, лабораторные, физкультура",
                  "cols": [("group_code", "да", "Код группы", "IT-2501"),
                           ("course_name", "да", "Дисциплина; * — любая с этим началом: Kazakh Language*", "Russian Language 1 / Kazakh Language 1"),
                           ("subgroup", "да", "Название подгруппы", "KZ"),
                           ("headcount", "да", "Студентов в подгруппе", "12"),
                           ("kind", "нет", "lecture, practice, lab или all (по умолчанию practice)", "practice")],
                  "lists": {"kind": "lecture,practice,lab,all"},
                  "example": [["IT-2501", "Russian Language 1 / Kazakh Language 1", "KZ", 12, "practice"],
                              ["IT-2501", "Russian Language 1 / Kazakh Language 1", "RU", 12, "practice"]]},
    "Electives": {"title": "Выбор студентов", "why": "дисциплины по выбору: сколько студентов выбрали каждый вариант",
                  "cols": [("program", "да", "Код программы", "IT"), ("trimester", "да", "Триместр", "7"),
                           ("course_name", "да", "Дисциплина по выбору — как в учебном плане, с «/»", "Data Visualization / Computer Graphics Fundamentals"),
                           ("option", "да", "Выбранный вариант", "Data Visualization"),
                           ("group_code", "да", "Группа или ALL", "IT-2101"),
                           ("students", "да", "Сколько студентов выбрали", "14")],
                  "lists": {},
                  "example": [["IT", 1, "Technological Entrepreneurship / Entrepreneurship", "Technological Entrepreneurship", "IT-2501", 14],
                              ["IT", 1, "Technological Entrepreneurship / Entrepreneurship", "Entrepreneurship", "IT-2501", 10]]},
    "RoomUnavailability": {"title": "Когда аудитория закрыта", "why": "ремонт, мероприятия, бронь",
                           "cols": [("room_code", "да", "Код аудитории", "C1.1.141"),
                                    ("day", "да", "Mon, Tue, Wed, Thu, Fri, Sat", "Wed"),
                                    ("from", "да", "С, ЧЧ:ММ", "08:00"), ("to", "да", "До, ЧЧ:ММ", "19:50"),
                                    ("weeks", "нет", "Недели триместра; пусто — все", ""),
                                    ("reason", "нет", "Причина", "ремонт")],
                           "lists": {"day": DAYS}, "example": [["C1.1.141", "Wed", "08:00", "19:50", "", "ремонт"]]},
    "Calendar": {"title": "Учебный календарь", "why": "даты триместров для календаря .ics и объявлений",
                 "cols": [("academic_year", "да", "Учебный год", "2025-2026"), ("term", "да", "Триместр года: 1, 2, 3", "1"),
                          ("start_date", "да", "Первый день, ГГГГ-ММ-ДД", "2025-09-01"), ("end_date", "да", "Последний день", "2025-11-09"),
                          ("weeks", "да", "Учебных недель", "10"), ("exam_weeks", "нет", "Недель сессии", "2")],
                 "lists": {"term": "1,2,3"},
                 "example": [["2025-2026", 1, "2025-09-01", "2025-11-09", 10, 2]]},
    "Holidays": {"title": "Праздники и мероприятия", "why": "дни без занятий",
                 "cols": [("date", "да", "Дата, ГГГГ-ММ-ДД", "2025-12-16"), ("name", "да", "Название", "День Независимости"),
                          ("kind", "нет", "holiday — праздник, event — мероприятие", "holiday")],
                 "lists": {"kind": "holiday,event"}, "example": [["2025-12-16", "День Независимости", "holiday"]]},
    "Settings": {"title": "Параметры вуза", "why": "общие правила расписания",
                 "cols": [("key", "да", "Параметр", "max_hours_per_day"), ("value", "да", "Значение", "8"),
                          ("description", "нет", "Что означает", "")],
                 "lists": {},
                 "example": [["max_hours_per_day", 8, "Не больше часов у группы в день"],
                             ["lunch_break", "13:00-13:50", "Обед: без занятий"],
                             ["day_start", "08:00", "Первая пара"], ["day_end", "19:50", "Конец последней пары"],
                             ["saturday", "concession", "allowed — можно, concession — только если иначе не получается, never — нельзя"],
                             ["lecture_stream_groups", 2, "Сколько групп объединять в один лекционный поток"],
                             ["online_stream_groups", 50, "Сколько групп в одной онлайн-лекции"],
                             ["gym_parallel_groups", 8, "Сколько групп одновременно на физкультуре"],
                             ["weeks_per_term", 10, "Учебных недель в триместре"]]},
}

USED_NOW = ["Rooms", "Groups", "<программа>", "Timeslots"]


def readme_rows() -> list[tuple]:
    rows = [("title", "Данные вуза для генерации расписания"),
            ("text", "Одна книга Excel — все данные вуза. Заполните листы и загрузите книгу на платформе: «Данные вуза» → "
                     "«Загрузить всё». Каждую категорию можно загрузить и отдельно — кнопкой в её окне."),
            ("h", "Агенты учитывают сейчас  (синие вкладки)"),
            ("row", "Rooms", "аудитории"), ("row", "Groups", "группы"),
            ("row", "IT, SE, CS…", "учебный план: лист на программу, имя листа = код программы"),
            ("row", "Timeslots", "сетка времени: 6 дней × 12 пар"),
            ("h", "Собираем для полного расписания  (оранжевые вкладки)"),
            ("text", "Эти данные платформа сохраняет уже сейчас; агенты начнут их учитывать после обновления модели. "
                     "Столбцы с пометкой ≈ в описании — тоже из этой части.")]
    rows += [("row", name, f"{x['title']} — {x['why']}") for name, x in EXTRA.items()]
    rows += [("h", "Справочные листы  (серые вкладки)"),
             ("row", "Programs", "справочник программ: шифр, название, годы"), ("row", "README, Check", "инструкция и замечания"),
             ("h", "Учебный план из официального УП"),
             ("text", "Официальный файл «Учебный план» загружается без переделки: окно «Учебный план» → «Загрузить». "
                      "Код программы берётся по шифру из Programs или из имени файла: «CS 22-25.xlsx» → CS."),
             ("row", "Наименование дисциплины (англ.)", "course_name"), ("row", "Триместр изучения", "trimester"),
             ("row", "Академические кредиты", "credits"),
             ("row", "Лекции / Практические / Лабораторные", "lecture_slots / practice_slots / lab_slots"),
             ("row", "Практики с формой контроля «отчет»", "не переносятся: аудиторных часов нет"),
             ("h", "Правила"),
             ("bullet", "Коды — латиницей: C1.2.240, IT-2301 (кириллица исправляется автоматически)."),
             ("bullet", "* У дисциплины нужен хотя бы один вид часов, иначе она не попадёт в расписание."),
             ("bullet", "Часы — академические за триместр; агенты делят их на число недель (10)."),
             ("bullet", "Буквы в коде группы = имя листа её программы."),
             ("bullet", "Численность групп — реальная: от неё зависит, какие аудитории подходят."),
             ("bullet", "Дни недели — Mon … Sat, время — ЧЧ:ММ, даты — ГГГГ-ММ-ДД."),
             ("bullet", "Лишние столбцы не мешают: платформа берёт только известные.")]
    return rows


def build_book(tables: dict[str, list[dict]], programs: list[dict] | None = None, checks: list[dict] | None = None,
               timeslot_rows: list[list] | None = None, extra: dict[str, list[list]] | None = None,
               course_cols: list[str] | None = None) -> bytes:
    """``tables``: rooms / groups / courses (with ``program``) in the editor's format; ``timeslot_rows``: the
    72-row Timeslots sheet with header; ``extra``: rows of the extra sheets without header (None = examples)."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    head_font = Font(bold=True, color="FFFFFF")
    thin = Border(bottom=Side(style="thin", color="CDD6E4"))
    docs = {s: {c: (req, what) for c, req, what, _ in rows} for s, rows in COLUMN_DOCS.items()}
    docs.update({name: {c: (req, what) for c, req, what, _ in x["cols"]} for name, x in EXTRA.items()})

    wb = Workbook()
    readme = wb.active
    readme.title = "README"
    readme.sheet_properties.tabColor = GREY
    _readme(readme, Font, PatternFill, Alignment)

    def sheet(name, header, rows, doc_key, color=BLUE, widths=None):
        ws = wb.create_sheet(name)
        ws.sheet_properties.tabColor = color
        ws.append(header)
        for r in rows:
            ws.append([None if v == "" else v for v in r])
        fill = PatternFill("solid", fgColor=color)
        for j, cell in enumerate(ws[1]):
            req, what = docs.get(doc_key, {}).get(header[j], ("", ""))
            later = what.startswith("≈")
            cell.font, cell.fill = head_font, PatternFill("solid", fgColor=ORANGE) if later else fill
            cell.alignment = Alignment(vertical="center")
            if what:
                text = what.lstrip("≈ ") + (" · обязательно" if req.startswith("да") else "") + (" · пока не учитывается агентами" if later else "")
                cell.comment = Comment(text, "Timetable Agents", width=280, height=100)
            ws.column_dimensions[cell.column_letter].width = (widths or {}).get(header[j], max(12, len(header[j]) + 4))
        ws.freeze_panes = "B2"
        ws.row_dimensions[1].height = 22
        center = Alignment(horizontal="center")
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.border = thin
                if isinstance(c.value, (int, float, bool)):
                    c.alignment = center
        return ws

    def validate(ws, header, col, values, title):
        if col not in header:
            return
        letter = ws.cell(1, header.index(col) + 1).column_letter
        dv = DataValidation(type="list", formula1=f'"{values}"', allow_blank=True, showErrorMessage=True,
                            errorTitle=title, error=f"Допустимо: {values}")
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}5000")

    if programs is not None:
        sheet("Programs", PROGRAM_COLS, [[p.get(k, "") for k in PROGRAM_COLS] for p in programs], "Programs", GREY,
              {"name_ru": 38, "direction": 46, "gop": 40, "form": 26})
    keys = [c for c, *_ in COLUMN_DOCS["Rooms"]]
    ws = sheet("Rooms", keys, [[r.get(k, "") for k in keys] for r in tables["rooms"]], "Rooms", widths={"features": 30})
    validate(ws, keys, "room_type", "L,P,K", "Тип аудитории")
    validate(ws, keys, "available", "TRUE,FALSE", "Доступна")
    keys = [c for c, *_ in COLUMN_DOCS["Groups"]]
    ws = sheet("Groups", keys, [[r.get(k, "") for k in keys] for r in tables["groups"]], "Groups")
    validate(ws, keys, "study_language", "kz,ru,en", "Язык обучения")
    ws = sheet("Timeslots", timeslot_rows[0], timeslot_rows[1:], "Timeslots")
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=5):
        for c in row:
            c.number_format = "hh:mm"
    keys = course_cols or [c for c, *_ in COLUMN_DOCS["<программа>"]]
    order: list[str] = []
    for r in tables["courses"]:
        if r.get("program") not in order:
            order.append(r.get("program"))
    for prog in order:
        rows = [[r.get(k, "") for k in keys] for r in tables["courses"] if r.get("program") == prog]
        ws = sheet(prog, keys, rows, "<программа>", widths={"course_name": 52, "lecture_precedes_practice": 26, "course_code": 22})
        validate(ws, keys, "delivery_mode", "offline,online", "Формат")
        validate(ws, keys, "lecture_precedes_practice", "TRUE,FALSE", "Лекция раньше практики")
        validate(ws, keys, "component", "ОК,ВК,КВ", "Компонент")
    for name, x in EXTRA.items():
        header = [c for c, *_ in x["cols"]]
        rows = (extra or {}).get(name, x["example"]) if extra is not None else x["example"]
        ws = sheet(name, header, rows, name, ORANGE, {"full_name": 32, "course_name": 46, "groups": 26, "description": 70,
                                                       "email": 30, "option": 34, "name": 30, "reason": 24, "key": 26})
        for col, values in x["lists"].items():
            validate(ws, header, col, values, x["title"])
    if checks:
        ws = sheet("Check", ["sheet", "row", "item", "what to do"],
                   [[c.get("sheet", ""), c.get("row", ""), c.get("item", ""), c.get("todo", "")] for c in checks],
                   "", GREY, {"item": 80, "what to do": 60, "sheet": 14})
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.fill = PatternFill("solid", fgColor=WARN)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _readme(ws, Font, PatternFill, Alignment) -> None:  # noqa: N803 - openpyxl classes passed in
    for col, w in zip("ABCD", (36, 6, 78, 26)):
        ws.column_dimensions[col].width = w
    ws.sheet_view.showGridLines = False
    wrap = Alignment(wrap_text=True, vertical="top")
    r = 1
    for kind, *parts in readme_rows():
        if kind == "title":
            ws.cell(r, 1, parts[0]).font = Font(bold=True, size=16, color=BLUE)
            r += 2
            continue
        if kind == "h":
            r += 1
            ws.cell(r, 1, parts[0]).font = Font(bold=True, size=12, color=ORANGE if "Собираем" in parts[0] else "0F1A2E")
        elif kind == "text":
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            ws.cell(r, 1, parts[0]).alignment = wrap
            ws.row_dimensions[r].height = 34
        elif kind == "row":
            ws.cell(r, 1, parts[0]).font = Font(bold=True)
            ws.cell(r, 2, "→")
            ws.cell(r, 3, parts[1])
        elif kind == "bullet":
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            ws.cell(r, 1, "• " + parts[0]).alignment = wrap
        r += 1
    r += 1
    ws.cell(r, 1, "Столбцы").font = Font(bold=True, size=12)
    r += 1
    sections = [(s, f"Лист {s}", COLUMN_DOCS[s]) for s in USED_NOW] + [(n, f"Лист {n} — {x['title']}", x["cols"]) for n, x in EXTRA.items()] \
        + [("Programs", "Лист Programs", COLUMN_DOCS["Programs"])]
    for key, title, cols in sections:
        r += 1
        ws.cell(r, 1, title).font = Font(bold=True, color=ORANGE if key in EXTRA else BLUE)
        r += 1
        for j, h in enumerate(["столбец", "обяз.", "что писать", "пример"], start=1):
            c = ws.cell(r, j, h)
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor=ORANGE_SOFT if key in EXTRA else SOFT)
        for col, req, what, ex in cols:
            r += 1
            ws.cell(r, 1, col).font = Font(name="Consolas")
            ws.cell(r, 2, req)
            c = ws.cell(r, 3, what)
            c.alignment = wrap
            if what.startswith("≈"):
                c.font = Font(color=ORANGE)
            ws.cell(r, 4, ex)
        r += 1
