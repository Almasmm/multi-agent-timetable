# Спецификация агентов

Таблицу можно сгенерировать из кода: `python -m timetable agents`. Развёрнутое описание на русском (что делает, входы/выходы, ключи blackboard, надёжность) показывает страница «Агенты» веб-платформы; его источник — `timetable/web/agents_ru.py`, при изменении агентов обновляй оба. Все сообщения имеют тип
`Message` (`timetable/core/messages.py`): `{id, ts, sender, recipient, intent: task|result|error, task,
payload: JSON, correlation_id}`.

| Агент | Роль (одна) | Входы (task → payload) | Выходы (result payload / blackboard) | Инструменты (≤5) | Критерий завершения |
|---|---|---|---|---|---|
| **Orchestrator** | Планирует пайплайн, маршрутизирует задачи, следит за лимитами. Предметную работу не делает | требования пользователя, Settings | итог прогона, `result.json`, timeline | нет (только отправка сообщений) | все шаги плана выполнены, или прогон остановлен с причиной |
| **Data** | Читает Excel, чистит и проверяет данные, строит недельный спрос | `load_data {}` | `{programs, courses, groups, rooms, sessions, issues}`; BB: `dataset`, `sessions`, `data_issues_text` | `read_workbook`, `normalize`, `build_demand` (+ LLM: объяснение проблем данных) | датасет и занятия на BB, все проблемы данных перечислены |
| **Constraints** | Переводит требования на естественном языке в JSON-ограничения (hard/soft) | `parse_constraints {texts[]}` | `{constraints, hard, warnings, specs}`; BB: `constraint_specs` | `parse_text` (LLM, structured output; офлайн-правила), `ground` | specs на BB, неизвестные сущности и непонятые фразы отражены в warnings |
| **Scheduler** | Декомпозирует задачу и назначает время занятиям через CP-SAT в пределах квоты | `plan_chunks {}`, `solve_chunk {chunk}` | `{chunks, units}` / `{status, ok, diagnostics, stats}`; BB: `chunks`, `solution:i` | `partition`, `cp_sat_solve` | у куска статус OPTIMAL/FEASIBLE или диагноз узкого места |
| **Rooms** | Владеет аудиторным фондом: считает свободную ёмкость, выдаёт квоты, назначает аудитории | `init_capacity`, `allocate_quota {chunk}`, `assign_rooms` | `{capacity_classes}` / `{mode, quota_hours}` / `{assigned, unassigned}`; BB: `caps`, `capacity`, `quota:i`, `rooms_of` | `capacity`, `quota`, `assign` | у каждого оффлайн-занятия есть аудитория или оно в списке unassigned |
| **Validator** (критик) | Независимо перепроверяет куски и итог по всем жёстким правилам, считает качество | `validate_chunk {chunk}`, `validate_all {final}` | `{hard_violations, by_type, violations, soft}`; BB: `validated:i`, `starts`, `validation`, `critique` | `check` (+ LLM: рецензия) | вердикт с полным списком нарушений и метриками |
| **Negotiator** | Решает конфликты: выбирает уступку для невыполнимого куска, локально чинит остаточные конфликты | `resolve {chunk, status, diagnostics}`, `repair {violations}` | `{action, rationale}` / `{moved, failed}`; BB: `run_params`, `repairs` | `decide` (LLM + политика-фолбэк), `apply`, `local_repair` | уступка применена (или give_up), либо все чинимые нарушения перенесены |
| **Reporter** | Публикует результат | `export {out, load, timeline}` | `{files[]}` | `export_tables`, `export_html`, `write_report` (+ LLM: резюме) | все файлы созданы, report.md содержит качество, проблемы и нагрузку |

## Внешние инструменты и источники данных

1. **Файл Excel** университета (openpyxl): учебные планы 13 программ, группы, аудитории, слоты.
2. **OR-Tools CP-SAT**: программный решатель ограничений (выполнение кода).
3. **LLM Claude** (`claude-opus-5-5`, structured outputs) или офлайн-правила.
4. **SQLite**: blackboard-состояние и журнал сообщений.
5. **Файлы-результаты**: xlsx, csv, html, md, jsonl.

## Баланс нагрузки

Каждый вызов инструмента и LLM логируется с именем агента (`events.jsonl`, `kind=tool_call|llm_call`).
Доля агента — его вызовы с `attempt=1` (или без поля `attempt`), делённые на все такие вызовы прогона.
Требование ≤ 40% проверяет сценарий S12. В `report.md`, `result.json` и лист «Agent load» Excel
попадает снимок до вызова Reporter. Полное распределение, включая экспорт, нужно пересчитывать
по завершённому `events.jsonl`. Проверенные журналы и пересчёт: [evidence/README.md](evidence/README.md).
