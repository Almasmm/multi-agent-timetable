# Спецификация агентов

Таблицу можно сгенерировать из кода: `python -m timetable agents`. Развёрнутое описание на русском (что делает, входы/выходы, ключи blackboard, надёжность) показывает страница «Агенты» веб-платформы; его источник — `timetable/web/agents_ru.py`, при изменении агентов обновляй оба. Все сообщения имеют тип
`Message` (`timetable/core/messages.py`): `{id, ts, sender, recipient, intent: task|result|error, task,
payload: JSON, correlation_id}`.

| Агент | Роль (одна) | Входы (task → payload) | Выходы (result payload / blackboard) | Инструменты (≤5) | Критерий завершения |
|---|---|---|---|---|---|
| **Orchestrator** | Планирует пайплайн, маршрутизирует задачи, следит за лимитами. Предметную работу не делает | требования пользователя, Settings | итог прогона, `result.json`, timeline | нет (только отправка сообщений) | все шаги плана выполнены, или прогон остановлен с причиной |
| **Data** | Читает Excel, чистит и проверяет данные, строит недельный спрос | `load_data {}` | `{programs, courses, groups, rooms, sessions, issues}`; BB: `dataset`, `sessions`, `data_issues_text`, `warm_start`, `warm_rooms` | `read_workbook`, `normalize`, `build_demand`, `load_previous` (тёплый старт: `starts.json` + `rooms.json` опубликованной версии) (+ LLM: объяснение проблем данных) | датасет и занятия на BB, все проблемы данных перечислены |
| **Constraints** | Переводит требования на естественном языке в JSON-ограничения (hard/soft) | `parse_constraints {texts[]}` | `{constraints, hard, warnings, specs}`; BB: `constraint_specs` | `parse_text` (LLM, structured output; офлайн-правила), `ground` | specs на BB, неизвестные сущности и непонятые фразы отражены в warnings |
| **Genetic** | Строит первое поколение расписания куска генетическим алгоритмом (жадные + случайные особи, турнир, кроссовер, меметическая мутация) и отдаёт лучшую особь как подсказку CP-SAT | `evolve {chunk}` | `{ok, stats, hard, generations}`; BB: `seed:i` | `evolve` | посевное расписание со статистикой на BB или диагноз «нет допустимого слота» |
| **Scheduler** | Декомпозирует задачу и назначает время занятиям через CP-SAT в пределах квоты | `plan_chunks {}`, `solve_chunk {chunk}` | `{chunks, units}` / `{status, ok, diagnostics, stats}`; BB: `chunks`, `solution:i` | `partition`, `cp_sat_solve` | у куска статус OPTIMAL/FEASIBLE или диагноз узкого места |
| **Rooms** | Владеет аудиторным фондом: считает свободную ёмкость, выдаёт квоты, назначает аудитории | `init_capacity`, `allocate_quota {chunk}`, `assign_rooms` | `{capacity_classes}` / `{mode, quota_hours}` / `{assigned, unassigned}`; BB: `caps`, `capacity`, `quota:i`, `rooms_of` | `capacity`, `quota`, `assign` (при тёплом старте сначала оставляет прежнюю аудиторию, если время не изменилось) | у каждого оффлайн-занятия есть аудитория или оно в списке unassigned |
| **Validator** (критик) | Независимо перепроверяет куски и итог по всем жёстким правилам, считает качество | `validate_chunk {chunk}`, `validate_all {final}` | `{hard_violations, by_type, violations, soft}`; BB: `validated:i`, `starts`, `validation`, `critique` | `check` (+ LLM: рецензия) | вердикт с полным списком нарушений и метриками |
| **Negotiator** | Решает конфликты: выбирает уступку для невыполнимого куска, локально чинит остаточные конфликты | `resolve {chunk, status, diagnostics}`, `repair {violations}` | `{action, rationale}` / `{moved, failed}`; BB: `run_params`, `repairs` | `decide` (LLM + политика-фолбэк), `apply`, `local_repair` | уступка применена (или give_up), либо все чинимые нарушения перенесены |
| **Reporter** | Публикует результат | `export {out, load, timeline}` | `{files[]}` | `export_tables`, `export_html`, `write_report` (+ LLM: резюме) | все файлы созданы, report.md содержит качество, проблемы и нагрузку |

## Служба 24/7 (`timetable/service/`)

Служба работает поверх того же конвейера: её агенты наследуют `BaseAgent` (лимит 5 инструментов,
ретраи, логирование), общаются тем же `Message`, а прогон генерации для них — получатель
`orchestrator` с задачей `generate`. Состояние: `runs/_service/service.sqlite` (профиль вуза, заявки,
задания, версии), сообщения: `runs/_service/state.sqlite`, журнал: `runs/_service/events-ГГГГММДД.jsonl`
(у каждой записи номер такта `tick`).

| Агент | Роль (одна) | Входы (task → payload) | Выходы | Инструменты (≤5) | Критерий завершения |
|---|---|---|---|---|---|
| **Autopilot** (сервисный оркестратор) | Раз в `TT_AUTOPILOT_TICK` секунд маршрутизирует сообщения между агентами службы и конвейером. Предметной работы не делает | такт по таймеру, кнопке или `--once` | состояние такта: `idle / busy / started / reviewed / deferred / error` | нет | не завершается; каждый такт заканчивается записанным состоянием |
| **Intake** (Приёмная) | Разбирает заявки сотрудников: понята ли фраза, существуют ли группа, аудитория, программа | `triage {request_id}` | `{status: accepted / rejected / review, reason}`; вердикт в заявке | `ground` (офлайн-разбор + сверка с Excel), `summarize` (LLM / шаблон), `record` | у каждой новой заявки есть вердикт |
| **Watcher** (Наблюдатель) | Следит за данными и требованиями, предлагает следующее задание | `scan {}`, `propose {fingerprint}` | `{fingerprint, new_requests}` / `{kind: generate / regenerate / improve / wait, reason, params}` | `fingerprint` (хэш Excel), `inbox`, `propose` | ровно одно предложение на такт |
| **Publisher** (Публикатор) | Выпускающий: публикует только прогон без жёстких нарушений, который нужен (изменения) или лучше (улучшение) | `review {job_id, run_dir}` | `{status: published / kept / failed, reason, version, diff}`; снимок `versions/v<N>/` | `evaluate`, `publish`, `announce` (LLM / шаблон) | прогон опубликован как версия или отложен с причиной |

Правила Наблюдателя: нет версии → `generate`; изменился хэш Excel или набор правил (профиль + принятые
заявки) → `regenerate` с тёплым стартом; иначе раз в `improve_every_min` минут → `improve` (другое зерно,
больше времени решателю), не более `max_improve_attempts` попыток на версию; три сбоя подряд → пауза 30 минут.

Правила Публикатора: статус `ok` и 0 жёстких нарушений обязательны; улучшение публикуется, только если
штраф (окна + 2 × после 18:00 + 3 × суббота + 0,5 × порядок) меньше хотя бы на 2 %, а после начала
семестра (`stable_after`) ещё и переносит не больше `max_moved_share` (10 %) академических часов.

## Внешние инструменты и источники данных

1. **Файл Excel** университета (openpyxl): учебные планы 13 программ, группы, аудитории, слоты.
2. **OR-Tools CP-SAT**: программный решатель ограничений (выполнение кода); стартует из решения **генетического алгоритма** (`solver/evolve.py`).
3. **LLM** — OpenAI (`gpt-5-mini`, strict JSON schema) или Claude (`claude-opus-5-5`, structured outputs), иначе офлайн-правила.
4. **SQLite**: blackboard-состояние и журнал сообщений.
5. **Файлы-результаты**: xlsx, csv, html, md, jsonl.

## Баланс нагрузки

Каждый вызов инструмента и LLM логируется с именем агента (`events.jsonl`, `kind=tool_call|llm_call`).
Доля агента — его вызовы с `attempt=1` (или без поля `attempt`), делённые на все такие вызовы прогона.
Требование ≤ 40% проверяет сценарий S12 (прогон генерации). Журнал службы 24/7 считается отдельно
(пульт «Автопилот» → «Нагрузка агентов службы»): в простое больше всех работает Наблюдатель, потому что
он осматривает данные каждый такт. В `report.md`, `result.json` и лист «Agent load» Excel
попадает снимок до вызова Reporter. Полное распределение, включая экспорт, нужно пересчитывать
по завершённому `events.jsonl`. Проверенные журналы и пересчёт: [evidence/README.md](evidence/README.md).
