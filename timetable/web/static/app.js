/* Timetable Agents - single-page UI (vanilla JS, no build step).
   Routes: #/ overview, #/schedule[/group|room/<sel>] published timetable, #/requests[/<id>|/new] staff requests,
   #/autopilot 24/7 service, #/runs history, #/run/<id>/<tab> live + results, #/new start a run, #/agents services.
   Visual system «Аспан»: DESIGN.md (tokens live in styles.css). Floating layers: context panel, command palette, preferences. */
"use strict";

// ------------------------------------------------------------------ constants
// Agent list mirrors app.AGENT_CLASSES; the colour is a stable marker used in tags, cards and the log.
const AGENTS = [
  { name: "orchestrator", label: "Orchestrator", ru: "Оркестратор", short: "OR", color: "var(--a-orchestrator)", hint: "план и маршрутизация" },
  { name: "data", label: "Data", ru: "Данные", short: "DA", color: "var(--a-data)", hint: "Excel → спрос на занятия" },
  { name: "constraints", label: "Constraints", ru: "Ограничения", short: "CO", color: "var(--a-constraints)", hint: "текст → JSON-правила" },
  { name: "genetic", label: "Genetic", ru: "Генетик", short: "GA", color: "var(--a-genetic)", hint: "первое поколение расписания" },
  { name: "scheduler", label: "Scheduler", ru: "Планировщик", short: "SC", color: "var(--a-scheduler)", hint: "CP-SAT по кускам" },
  { name: "rooms", label: "Rooms", ru: "Аудитории", short: "RO", color: "var(--a-rooms)", hint: "квоты и назначение аудиторий" },
  { name: "validator", label: "Validator", ru: "Критик", short: "VA", color: "var(--a-validator)", hint: "независимая проверка" },
  { name: "negotiator", label: "Negotiator", ru: "Переговорщик", short: "NE", color: "var(--a-negotiator)", hint: "уступки и ремонт" },
  { name: "reporter", label: "Reporter", ru: "Отчётность", short: "RE", color: "var(--a-reporter)", hint: "xlsx / html / отчёт" },
];
// The 24/7 service (timetable/service): its orchestrator and three agents around the generation pipeline.
const SERVICE_AGENTS = [
  { name: "autopilot", label: "Autopilot", ru: "Автопилот", short: "AP", color: "var(--a-autopilot)", hint: "оркестратор службы 24/7" },
  { name: "intake", label: "Intake", ru: "Приёмная", short: "IN", color: "var(--a-intake)", hint: "заявки сотрудников → правила" },
  { name: "watcher", label: "Watcher", ru: "Наблюдатель", short: "WA", color: "var(--a-watcher)", hint: "данные, заявки, улучшения" },
  { name: "publisher", label: "Publisher", ru: "Публикатор", short: "PU", color: "var(--a-publisher)", hint: "проверка и выпуск версий" },
];
const AGENT_BY = Object.fromEntries([...AGENTS, ...SERVICE_AGENTS].map(a => [a.name, a]));
const SYSTEM_AGENT = { name: "system", label: "system", ru: "система", short: "SY", color: "var(--a-system)" };
const DAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];
const DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
const DAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const KIND_RU = { lecture: "лекция", practice: "практика", lab: "лаб." };
const TASK_RU = {
  load_data: "загрузка данных", parse_constraints: "разбор требований", plan_chunks: "декомпозиция",
  init_capacity: "аудиторный фонд", allocate_quota: "квота аудиторий", evolve: "ГА: первое поколение", solve_chunk: "решение CP-SAT",
  validate_chunk: "проверка куска", resolve: "переговоры", assign_rooms: "назначение аудиторий",
  validate_all: "итоговая проверка", repair: "локальный ремонт", export: "экспорт",
};
// Pipeline stages in orchestrator order; `tasks` lists the message tasks that belong to the stage.
const STAGES = [
  { key: "load_data", label: "Данные", agent: "data", tasks: ["load_data"] },
  { key: "parse_constraints", label: "Правила", agent: "constraints", tasks: ["parse_constraints"] },
  { key: "plan_chunks", label: "Куски", agent: "scheduler", tasks: ["plan_chunks"] },
  { key: "init_capacity", label: "Фонд аудиторий", agent: "rooms", tasks: ["init_capacity"] },
  { key: "chunks", label: "ГА + CP-SAT по кускам", agent: "scheduler", tasks: ["allocate_quota", "evolve", "solve_chunk", "validate_chunk", "resolve"] },
  { key: "assign_rooms", label: "Аудитории", agent: "rooms", tasks: ["assign_rooms"] },
  { key: "validate_all", label: "Итоговая проверка", agent: "validator", tasks: ["validate_all", "repair"] },
  { key: "export", label: "Экспорт", agent: "reporter", tasks: ["export"] },
];
const STAGE_OF = Object.fromEntries(STAGES.flatMap(s => s.tasks.map(t => [t, s.key])));
const ACTION_RU = {
  extend_time: "больше времени решателю", full_quota: "весь остаток аудиторного фонда",
  allow_saturday: "открыть субботу", relax_user_constraints: "ослабить пользовательские запреты", give_up: "сдаться",
};
const EXAMPLE_REQS = ["1 курс заканчивает до 17:00", "Physical Education только утром", "Не более 3 пар в день",
  "Аудитория C1.3.361 недоступна в среду", "Желательно не ставить занятия до 9:00", "У группы IT-2301 свободна пятница",
  "No classes on Saturday", "SE 2 курс не раньше 10:00"];
const PRESETS = [
  { id: "demo", title: "Демо: программа IT", sub: "22 группы · около 10 секунд", programs: ["IT"], seconds: 8 },
  { id: "two", title: "Две программы: SE + CS", sub: "172 группы · около минуты", programs: ["SE", "CS"], seconds: 15 },
  { id: "full", title: "Полный прогон", sub: "270 групп, 13 программ · около 2,5 минут", programs: [], seconds: 25 },
];
const FILES = [["timetable.xlsx", "Excel: расписание, аудитории, проблемы данных, нагрузка, качество"], ["timetable.csv", "CSV в длинном формате"],
  ["timetable.html", "Автономный HTML-просмотрщик"], ["report.md", "Отчёт о прогоне (Markdown)"], ["events.jsonl", "Журнал всех сообщений, инструментов и LLM"],
  ["result.json", "Машиночитаемый итог"], ["state.sqlite", "Blackboard: состояние и сообщения"]];
// Outline icons in the Lucide style (24px grid, 2px round stroke).
const svg = (body, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${body}</svg>`;
const I = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12-7.5z"/></svg>',
  plus: svg('<path d="M5 12h14M12 5v14"/>'),
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  trash: svg('<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/>'),
  down: svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>'),
  print: svg('<path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8" rx="1"/>'),
  check: svg('<path d="M20 6 9 17l-5-5"/>', 'stroke-width="2.5"'),
  checkCircle: svg('<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'),
  alert: svg('<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3zM12 9v4M12 17h.01"/>'),
  alertCircle: svg('<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>'),
  xCircle: svg('<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>'),
  stopCircle: svg('<circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6" rx="1"/>'),
  info: svg('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'),
  cal: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>'),
  calendar: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>'),
  search: svg('<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'),
  arrow: svg('<path d="M5 12h14M12 5l7 7-7 7"/>'),
  bolt: svg('<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>'),
  file: svg('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M16 13H8M16 17H8M10 9H8"/>'),
  db: svg('<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>'),
  moon: svg('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>'),
  chev: svg('<path d="m9 18 6-6-6-6"/>'),
  chevL: svg('<path d="m15 18-6-6 6-6"/>'),
  tool: svg('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>'),
  brain: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>'),
  msg: svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
  clock: svg('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  users: svg('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'),
  layers: svg('<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65M22 12.65l-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'),
  dashboard: svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
  home: svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
  inbox: svg('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'),
  bot: svg('<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>'),
  pulse: svg('<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
  network: svg('<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>'),
  history: svg('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>'),
  playCircle: svg('<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4z"/>'),
  squarePen: svg('<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.38 2.62a1 1 0 0 1 3 3l-9.01 9.02a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.86z"/>'),
  pen: svg('<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.38 2.62a1 1 0 0 1 3 3l-9.01 9.02a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.86z"/>'),
  refresh: svg('<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'),
  send: svg('<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>'),
  calPlus: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M12 14v6M9 17h6"/>'),
  globe: svg('<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>'),
  door: svg('<path d="M13 4h3a2 2 0 0 1 2 2v14M2 20h3M13 20h9M10 12v.01"/><path d="M13 4.56v16.16a1 1 0 0 1-1.24.97L5 20V5.56a2 2 0 0 1 1.52-1.94l4-1A2 2 0 0 1 13 4.56z"/>'),
  shield: svg('<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>'),
  sparkle: svg('<path d="M12 3 13.9 8.1 19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15v4M17 17h4"/>'),
  undo: svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>'),
  table: svg('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>'),
  group: svg('<path d="M3 5h.01M3 12h.01M3 19h.01M8 5h13M8 12h13M8 19h13"/>'),
  copy: svg('<rect x="8" y="8" width="14" height="14" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'),
  sliders: svg('<path d="M20 7h-9M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>'),
  panel: svg('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M16 15l-3-3 3-3"/>'),
  menu: svg('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  x: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
};
// Route -> top bar (section, title); mirrors the sidebar sections.
const ROUTES = {
  home: ["Расписание", "Центр управления"], schedule: ["Расписание", "Опубликованное расписание"],
  requests: ["Расписание", "Заявки сотрудников"], autopilot: ["Агенты 24/7", "Автопилот"],
  runs: ["Агенты 24/7", "Прогоны"], run: ["Прогоны", "Прогон"], agents: ["Агенты 24/7", "Агенты"],
  new: ["Агенты 24/7", "Новый прогон"],
};
const REQ_STATUS = {
  new: { text: "новая", cls: "accent", c: "var(--primary)" }, review: { text: "у диспетчера", cls: "warn", c: "var(--warn)" },
  accepted: { text: "принята", cls: "info", c: "var(--info)" }, applied: { text: "в расписании", cls: "ok", c: "var(--ok)" },
  rejected: { text: "отклонена", cls: "bad", c: "var(--bad)" }, withdrawn: { text: "отозвана", cls: "", c: "var(--text-4)" },
};
const JOB_RU = { generate: "первая генерация", regenerate: "перегенерация", improve: "поиск улучшения" };
const JOB_STATUS = { queued: ["в очереди", "accent"], running: ["идёт", "accent"], published: ["опубликовано", "ok"], kept: ["не лучше — оставлено", ""], failed: ["ошибка", "bad"] };
const REQ_EXAMPLES = ["Аудитория C1.3.361 недоступна в среду", "У группы IT-2301 свободна пятница", "1 курс заканчивает до 17:00",
  "Physical Education только утром", "Не более 6 часов в день", "SE 2 курс не раньше 10:00"];

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDur = s => { s = Math.round(s || 0); return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`; };
const fmtDate = ts => new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
// mail-list style: time for today, "23 окт." for older dates
const fmtWhen = ts => { const d = new Date(ts * 1000), now = new Date(); return d.toDateString() === now.toDateString() ? d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }); };
const fmtIn = s => s <= 0 ? "сейчас" : s < 60 ? `${Math.round(s)} с` : `${Math.round(s / 60)} мин`;
const initials = name => (String(name || "?").trim().split(/\s+/).map(w => w[0]).join("").slice(0, 2) || "?").toUpperCase();
const pct = x => `${Math.round((x || 0) * 100)}%`;
const sum = arr => arr.reduce((a, b) => a + b, 0);
const num = (x, d = 2) => x == null ? "—" : typeof x === "number" ? (Number.isInteger(x) ? x : x.toFixed(d)) : x;
const plural = (n, one, few, many) => { const m = n % 10, h = n % 100; return `${n} ${h >= 11 && h <= 14 ? many : m === 1 ? one : m >= 2 && m <= 4 ? few : many}`; };
const agentOf = name => AGENT_BY[name] || { ...SYSTEM_AGENT, label: name, ru: name };
const agentTag = name => { const a = agentOf(name); return `<span class="agent-tag" style="--c:${a.color}">${esc(a.ru)}</span>`; };
const kpi = (v, l, cls = "", sub = "") => `<div class="kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div>${sub ? `<div class="d">${sub}</div>` : ""}</div>`;
const skeleton = (n = 3) => `<div class="skeleton-wrap">${Array.from({ length: n }, () => '<div class="skeleton"></div>').join("")}</div>`;
const hvColor = hv => hv === 0 ? "var(--ok)" : hv > 0 ? "var(--bad)" : "inherit";

// Status vocabulary: text + icon + colour; a user cancel is a calm neutral state, not an error.
function statusInfo(st, cancelled = false) {
  switch (st) {
    case "ok": return { cls: "ok", icon: I.checkCircle, text: "успешно" };
    case "partial": return { cls: "warn", icon: I.alertCircle, text: "частично" };
    case "failed": return { cls: "bad", icon: I.xCircle, text: "ошибка" };
    case "stopped": return cancelled ? { cls: "", icon: I.stopCircle, text: "остановлен" } : { cls: "warn", icon: I.alertCircle, text: "остановлен: лимит" };
    case "running": return { cls: "accent", icon: '<span class="spinner"></span>', text: "выполняется" };
    case "cancelling": return { cls: "accent", icon: '<span class="spinner"></span>', text: "останавливается" };
    case "incomplete": return { cls: "bad", icon: I.xCircle, text: "прерван" };
    default: return { cls: "", icon: I.info, text: st || "?" };
  }
}
const statusBadge = (st, cancelled = false, lg = false) => { const s = statusInfo(st, cancelled); return `<span class="badge ${s.cls} ${lg ? "lg" : ""}">${s.icon}${s.text}</span>`; };
const runStatusBadge = (r, lg = false) => statusBadge(r.status, !!r.cancelled, lg);

function toast(msg, bad = false) {
  const t = document.createElement("div"); t.textContent = msg; if (bad) t.className = "bad";
  $("#toast").appendChild(t); setTimeout(() => t.remove(), 4000);
}
async function api(path, opts = {}) {
  let res;
  try { res = await fetch(path, opts); } catch (e) { setOnline(false); throw new Error("сервер недоступен"); }
  setOnline(true);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.error || (j.detail && (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail))) || msg; } catch (_) { }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function setOnline(ok) { $("#srv .live-dot").classList.toggle("off", !ok); $("#srv-text").textContent = ok ? "онлайн" : "нет связи"; }
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const confirmDelete = id => confirm(`Удалить прогон ${id} со всеми файлами?`);

// minimal markdown (headings, bullets, tables, bold, inline code) for report.md and LLM texts
function markdown(src) {
  const out = []; let list = false, table = [];
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter(r => !/^\|?\s*-{2,}/.test(r)).map(r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
    out.push("<table><tr>" + rows[0].map(c => `<th>${inline(c)}</th>`).join("") + "</tr>" +
      rows.slice(1).map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>");
    table = [];
  };
  for (const raw of (src || "").split("\n")) {
    const line = raw.trimEnd();
    if (/^\|/.test(line)) { table.push(line); continue; } else flushTable();
    if (/^\s*[-*] /.test(line)) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(line.replace(/^\s*[-*] /, ""))}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    const m = /^(#{1,3}) (.*)/.exec(line);
    if (m) out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>"); flushTable();
  return out.join("\n");
}

// human description of a constraint spec (JSON DSL -> Russian sentence)
function scopeText(sc) {
  if (!sc) return "";
  const parts = [];
  if (sc.programs && sc.programs.length) parts.push(`программы ${sc.programs.join(", ")}`);
  if (sc.years && sc.years.length) parts.push(`${sc.years.join(", ")} курс`);
  if (sc.groups && sc.groups.length) parts.push(`группы ${sc.groups.join(", ")}`);
  if (sc.courses && sc.courses.length) parts.push(`дисциплина «${sc.courses.join("», «")}»`);
  return parts.join(", ");
}
function describeSpec(s) {
  const days = (s.days || []).map(d => DAY_SHORT[DAY_EN.indexOf(d)]).join(", ");
  const hours = s.start_hour === 8 && s.end_hour === 20 ? "весь день" : `${s.start_hour}:00–${s.end_hour}:00`;
  const scope = scopeText(s.scope) || "все группы";
  if (s.kind === "max_daily_hours") return `Не более ${s.value} академ. часов в день — ${scope}`;
  if (s.kind === "room_unavailable") return `Аудитория ${(s.scope.rooms || []).join(", ")} закрыта: ${days || "все дни"}, ${hours}`;
  return `Нет занятий: ${scope}; ${days || "все дни"}, ${hours}`;
}

// ------------------------------------------------------------------ view preferences: theme, row density, text size
const THEMES = { light: "Светлая", dark: "Тёмная", auto: "Системная" };
const LEGACY_THEME = { aspan: "light", astana: "light", night: "dark" };
const pref = (k, d) => { try { return localStorage.getItem(k) || d; } catch (_) { return d; } };
const setPref = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { } };
const prefsBox = $("#prefs");
const darkMq = matchMedia("(prefers-color-scheme: dark)");
function applyPrefs() {
  const root = document.documentElement;
  let t = pref("theme", "light"); t = LEGACY_THEME[t] || (THEMES[t] ? t : "light");
  root.dataset.theme = t === "auto" ? (darkMq.matches ? "dark" : "light") : t;
  root.dataset.density = pref("density", "normal"); root.dataset.scale = pref("scale", "m");
  $("#theme-text").textContent = `Вид: ${THEMES[t].toLowerCase()} тема`;
  [["#pf-theme", t], ["#pf-density", root.dataset.density], ["#pf-scale", root.dataset.scale]]
    .forEach(([sel, v]) => $$(`${sel} button`).forEach(b => b.classList.toggle("on", b.dataset.v === v)));
}
darkMq.addEventListener("change", applyPrefs);
{ const q = new URLSearchParams(location.search).get("theme"); if (q && (THEMES[q] || LEGACY_THEME[q])) setPref("theme", q); }
[["#pf-theme", "theme"], ["#pf-density", "density"], ["#pf-scale", "scale"]].forEach(([sel, key]) => $$(`${sel} button`).forEach(b => b.onclick = () => {
  setPref(key, b.dataset.v); applyPrefs();
  if (key !== "theme" && currentPage === "schedule") route(); // the data grid measures row height once per render
}));
$("#prefs-btn").onclick = e => { e.stopPropagation(); prefsBox.classList.toggle("on"); };
$("#prefs-close").onclick = () => prefsBox.classList.remove("on");
document.addEventListener("click", e => { if (prefsBox.classList.contains("on") && !e.target.closest("#prefs, #prefs-btn")) prefsBox.classList.remove("on"); });
$$("[data-ic]").forEach(el => { el.innerHTML = I[el.dataset.ic] || ""; });
applyPrefs();

// sidebar: mobile drawer, collapsed rail (remembered per browser)
const closeNav = () => { document.body.classList.remove("nav-open"); $("#nav-toggle").setAttribute("aria-expanded", "false"); };
$("#nav-toggle").onclick = () => { const open = document.body.classList.toggle("nav-open"); $("#nav-toggle").setAttribute("aria-expanded", String(open)); };
$("#side-scrim").onclick = closeNav;
$$("#nav a, .compose, #pilot-card").forEach(a => a.addEventListener("click", closeNav));
const setMini = on => { document.body.classList.toggle("side-mini", on); setPref("side-mini", on ? "1" : ""); };
if (pref("side-mini", "")) document.body.classList.add("side-mini");
$("#side-collapse").onclick = () => setMini(!document.body.classList.contains("side-mini"));

// ------------------------------------------------------------------ context panel (glass, right): details of a row, cell, agent or version
// Non-modal: the workspace stays usable while it is open; Esc or × closes it.
const ctxOpen = () => $("#ctx").classList.contains("on");
function openCtx(kicker, title, body, foot = "") {
  $("#ctx-kicker").textContent = kicker; $("#ctx-title").innerHTML = title; $("#ctx-body").innerHTML = body; $("#ctx-foot").innerHTML = foot;
  $("#ctx").classList.add("on"); $("#ctx-body").scrollTop = 0;
}
function closeCtx() { $("#ctx").classList.remove("on"); $$(".cell.on, .svc-row.on").forEach(x => x.classList.remove("on")); }
$("#ctx-close").onclick = closeCtx;

// ------------------------------------------------------------------ command palette (Ctrl+K or /): commands + groups, rooms, runs
const COMMANDS = [
  ["Центр управления", "#/", "dashboard"], ["Расписание: таблица всех занятий", "#/schedule", "table"],
  ["Расписание: неделя группы", "#/schedule/group", "calendar"], ["Расписание: неделя аудитории", "#/schedule/room", "door"],
  ["Новая заявка", "#/requests/new", "squarePen"], ["Заявки", "#/requests", "inbox"],
  ["Автопилот", "#/autopilot", "bot"], ["Агенты", "#/agents", "network"],
  ["Прогоны", "#/runs", "history"], ["Новый прогон", "#/new", "playCircle"],
  ["Агенты: проверить сейчас", () => $("#tick-btn").click(), "refresh"],
  ["Автопилот: включить или выключить", () => setPilot(!(pilotBrief && pilotBrief.enabled)), "bot"],
  ["Вид: тема, плотность, размер текста", () => prefsBox.classList.add("on"), "sliders"],
];
const FOUND_TYPE = { group: ["группа", "users"], room: ["аудитория", "door"], run: ["прогон", "history"] };
const pal = { items: [], hl: 0, seq: 0 };
const palOpen = () => $("#pal").classList.contains("on");
function openPalette() { $("#pal").classList.add("on"); $("#pal-scrim").classList.add("on"); const i = $("#pal-input"); i.value = ""; drawPalette(""); setTimeout(() => i.focus(), 30); }
function closePalette() { $("#pal").classList.remove("on"); $("#pal-scrim").classList.remove("on"); }
async function drawPalette(q) {
  const my = ++pal.seq, ql = q.trim().toLowerCase();
  const cmds = COMMANDS.filter(c => !ql || c[0].toLowerCase().includes(ql)).map(([label, go, ic]) => ({ label, go, ic, sec: "Команды" }));
  let found = [];
  if (ql) {
    try {
      found = (await api(`/api/search?q=${encodeURIComponent(q.trim())}`)).map(it => ({ label: it.label, sec: "Найдено", mono: true, ty: FOUND_TYPE[it.type][0], ic: FOUND_TYPE[it.type][1],
        go: it.type === "run" ? `#/run/${encodeURIComponent(it.label)}` : `#/schedule/${it.type}/${encodeURIComponent(it.label)}` }));
    } catch (_) { }
  }
  if (my !== pal.seq) return;
  pal.items = [...found, ...cmds].slice(0, 30); pal.hl = 0; renderPalette();
}
function renderPalette() {
  let sec = "";
  $("#pal-list").innerHTML = pal.items.length ? pal.items.map((it, i) => `${it.sec !== sec ? `<div class="pal-sec">${(sec = it.sec)}</div>` : ""}<a href="${typeof it.go === "string" ? it.go : "#"}" data-i="${i}" class="${i === pal.hl ? "hl" : ""}" role="option" aria-selected="${i === pal.hl}"><span class="ico">${I[it.ic]}</span><span class="${it.mono ? "mono" : ""}">${esc(it.label)}</span>${it.ty ? `<span class="ty">${it.ty}</span>` : ""}</a>`).join("")
    : `<div class="empty">Ничего не найдено. Ищите по коду группы (IT-2301), аудитории (C1.3.361), имени прогона или названию команды.</div>`;
  const hl = $("#pal-list a.hl"); if (hl) hl.scrollIntoView({ block: "nearest" });
}
function runPalette(it) { if (!it) return; closePalette(); if (typeof it.go === "function") it.go(); else location.hash = it.go; }
$("#search-btn").onclick = openPalette;
$("#pal-scrim").onclick = closePalette;
$("#pal-input").addEventListener("input", debounce(() => drawPalette($("#pal-input").value), 140));
$("#pal-input").addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (pal.items.length) { pal.hl = (pal.hl + (e.key === "ArrowDown" ? 1 : -1) + pal.items.length) % pal.items.length; renderPalette(); } }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(pal.items[pal.hl]); }
});
$("#pal-list").addEventListener("mousedown", e => { const a = e.target.closest("a"); if (a) { e.preventDefault(); runPalette(pal.items[+a.dataset.i]); } });

// hotkeys: Ctrl+K or "/" palette, "N" new request, Esc closes the top floating layer
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && ["k", "K", "л", "Л"].includes(e.key)) { e.preventDefault(); palOpen() ? closePalette() : openPalette(); return; }
  if (e.key === "Escape") { if (palOpen()) closePalette(); else if (prefsBox.classList.contains("on")) prefsBox.classList.remove("on"); else if (ctxOpen()) closeCtx(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (e.key === "/") { e.preventDefault(); openPalette(); }
  else if (["n", "N", "т", "Т"].includes(e.key)) { e.preventDefault(); location.hash = "#/requests/new"; }
});

// 24/7 service status in the sidebar (polled; cheap /brief endpoint)
let pilotBrief = null;
function pilotLine(b) {
  if (!b) return "нет связи";
  if (b.job) return `${JOB_RU[b.job.kind] || b.job.kind}: идёт прогон`;
  if (!b.enabled) return "выключен";
  const r = b.last_result;
  if (r && r.state === "error") return "ошибка последнего такта";
  return b.next_tick ? `проверка через ${fmtIn(b.next_tick - Date.now() / 1000)}` : "работает";
}
async function pollPilot() {
  try {
    const b = await api("/api/autopilot/brief"); pilotBrief = b;
    $("#pilot-text").textContent = pilotLine(b);
    const sw = $("#pilot-sw"); sw.setAttribute("aria-checked", String(!!b.enabled));
    const dot = $("#nb-pilot"); dot.hidden = false; dot.className = `live-dot ${b.enabled ? "beat" : "idle"}`;
    const nr = $("#nb-requests"); nr.hidden = !b.open_requests; nr.textContent = b.open_requests;
    const nv = $("#nb-version"); nv.hidden = !b.current; if (b.current) nv.textContent = `v${b.current.id}`;
    const ru = $("#nb-runs"); ru.hidden = !b.active_run; ru.textContent = "1";
  } catch (_) { $("#pilot-text").textContent = "нет связи"; }
}
async function setPilot(on) {
  try { await post("/api/autopilot/switch", { on }); toast(on ? "Автопилот включён: агенты работают 24/7" : "Автопилот выключен"); } catch (e) { toast(e.message, true); }
  await pollPilot(); if (currentPage === "autopilot" || currentPage === "home") route();
}
$("#pilot-sw").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); setPilot(!(pilotBrief && pilotBrief.enabled)); });
$("#pilot-sw").addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); e.stopPropagation(); setPilot(!(pilotBrief && pilotBrief.enabled)); } });
$("#tick-btn").onclick = async () => {
  const b = $("#tick-btn"); b.classList.add("spin"); b.disabled = true;
  try { const r = await post("/api/autopilot/tick", {}); toast(tickText(r)); } catch (e) { toast(e.message, true); }
  b.classList.remove("spin"); b.disabled = false; await pollPilot(); if (["autopilot", "requests", "home", "schedule"].includes(currentPage)) route();
};
function tickText(r) {
  const s = { idle: "Агенты проверили всё: изменений нет", busy: "Идёт прогон — агенты ждут результат", started: "Агенты запустили генерацию", reviewed: "Публикатор проверил прогон", deferred: "Ждём окончания ручного прогона", error: "Ошибка такта" }[r.state] || r.state;
  return `${s}${r.reason ? ` · ${r.reason}` : ""}`;
}
pollPilot(); setInterval(pollPilot, 5000);
// ------------------------------------------------------------------ router
let currentView = null, currentPage = "home", pageTimer = null;
function route() {
  const hash = location.hash || "#/";
  const same = currentView instanceof RunView && hash.startsWith(`#/run/${currentView.id}/`) && !currentView.dead;
  if (!same) { if (currentView && currentView.destroy) currentView.destroy(); currentView = null; }
  clearTimeout(pageTimer);
  const m = /^#\/run\/([A-Za-z0-9_.\-]+)(?:\/([a-z]+))?(?:\/([a-z]+))?(?:\/(.+))?/.exec(hash);
  const sm = /^#\/schedule(?:\/(group|room|table)(?:\/(.+))?)?/.exec(hash);
  const rq = /^#\/requests(?:\/(new|\d+))?/.exec(hash);
  let name = "home";
  if (hash.startsWith("#/new")) name = "new"; else if (hash.startsWith("#/runs")) name = "runs";
  else if (hash.startsWith("#/agents")) name = "agents"; else if (m) name = "run";
  else if (sm) name = "schedule"; else if (rq) name = "requests"; else if (hash.startsWith("#/autopilot")) name = "autopilot";
  currentPage = name;
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.route === (name === "run" ? "runs" : name)));
  const [eyebrow, title] = ROUTES[name];
  $("#top-eyebrow").textContent = eyebrow; $("#top-title").textContent = name === "run" ? m[1] : title;
  closeCtx(); closePalette();
  const main = $("#main"); if (!same) { main.innerHTML = `<div class="container page">${skeleton(4)}</div>`; $("#work").scrollTo(0, 0); }
  const pages = {
    home: renderHome, new: renderNew, runs: renderRuns, agents: renderAgents, autopilot: renderAutopilot,
    schedule: () => renderSchedule(sm[1], sm[2] && decodeURIComponent(sm[2])), requests: () => renderRequests(rq[1]),
    run: () => renderRun(m[1], m[2], m[3], m[4] && decodeURIComponent(m[4])),
  };
  pages[name]().catch(e => {
    main.innerHTML = `<div class="container page"><div class="card empty-state">${I.alertCircle}<h2>Не удалось открыть страницу</h2><p>${esc(e.message)}</p><div class="actions"><a class="btn" href="#/">В центр управления</a></div></div></div>`;
  });
}
window.addEventListener("hashchange", route);

const crumbs = items => `<div class="crumbs"><a href="#/">Центр управления</a>${items.map(([l, h]) => ` <span>›</span> ${h ? `<a href="${h}">${l}</a>` : `<span>${l}</span>`}`).join("")}</div>`;
function pageHead(title, sub, actions = "", crumbItems = null, meta = "") {
  return `<header class="page-head"><div>${crumbItems ? crumbs(crumbItems) : ""}<h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}${meta ? `<div class="meta">${meta}</div>` : ""}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</header>`;
}
const vtag = id => `<span class="vtag">v${esc(id)}</span>`;
const page = (head, body) => `<div class="container page">${head}${body}</div>`;
const sectionHead = (title, extra = "", text = "") => `<div class="section-head"><div><h2>${title}</h2>${text ? `<p>${text}</p>` : ""}</div>${extra ? `<div class="actions">${extra}</div>` : ""}</div>`;

// Agent cards: name, role and (optionally) live state. `state(a)` returns {cls, off, meta, right} or null.
function agentGrid(state = () => null, link = true) {
  return `<div class="agent-grid">${AGENTS.map(a => {
    const s = state(a) || {};
    const tag = link ? "a" : "div";
    return `<${tag} class="agent-card ${s.cls || ""}" style="--c:${a.color}" ${link ? `href="#/agents#agent-${a.name}"` : ""}><div class="ic ${s.off ? "off" : ""}">${a.short}</div><div><b>${a.ru}</b><div class="meta">${s.meta || a.hint}</div></div>${s.right != null ? `<div class="right">${s.right}</div>` : ""}</${tag}>`;
  }).join("")}</div>`;
}
// Stage flow: 8 pipeline stages with their owner agent; `cls(key, i)` returns done/active/stopped/failed/"".
function flow(cls = () => "", extra = () => "") {
  return `<div class="flow">${STAGES.map((s, i) => { const c = cls(s.key, i); const a = AGENT_BY[s.agent]; return `<div class="st ${c}"><i>${c === "done" ? I.check : c === "failed" ? "!" : i + 1}</i><div class="t"><span>${s.label}${extra(s.key) || ""}</span><small>${a.ru}</small></div></div>`; }).join("")}</div>`;
}

// ------------------------------------------------------------------ published versions (cached per id: a version never changes)
const pubCache = {};
async function getPublished(id) {
  if (id && pubCache[id]) return pubCache[id];
  const p = await api(`/api/published${id ? `?version=${id}` : ""}`);
  if (p.version) pubCache[p.version.id] = p;
  return p;
}
const rowKey = r => `${r.group}|${r.day}|${r.hour}|${r.course}|${r.kind}|${r.room}`;
// Rows of a version that did not exist in the previous one (moved in time or changed room).
async function changedSet(pub, prevId) {
  const before = new Set((await getPublished(prevId)).rows.map(rowKey));
  return new Set(pub.rows.filter(r => !before.has(rowKey(r))).map(rowKey));
}
// Control-center metrics computed from the published rows: room fund = rooms of the dataset (or more if the
// version uses more), slots = 12 per teaching day (Saturday only if it is used).
function pubMetrics(pub, ds, profile) {
  const rooms = new Set(), occ = new Set();
  for (const r of pub.rows) if (r.room !== "online" && r.room !== "UNASSIGNED") { rooms.add(r.room); occ.add(`${r.room}|${r.day}|${r.hour}`); }
  const days = pub.rows.some(r => r.day === 5) ? 6 : 5, fund = Math.max(rooms.size, ds ? ds.rooms : 0);
  const progs = ds ? ds.programs.filter(p => !profile.programs.length || profile.programs.includes(p.code)) : [];
  const total = progs.length ? sum(progs.map(p => p.groups)) : pub.groups.length;
  return { util: fund ? occ.size / (fund * days * 12) : 0, roomHours: occ.size, fund, days, groups: pub.groups.length, total };
}

// Operational state of the 24/7 service agents and of the generation pipeline (one row each).
function serviceStates(ap, active) {
  const lr = ap.last_result || {}, load = (ap.load || {}).agents || {}, open = (ap.requests.new || 0) + (ap.requests.review || 0);
  const off = ["stopped", "Остановлен"];
  const calls = a => load[a] ? `${plural(load[a].calls, "вызов", "вызова", "вызовов")} за 2 дня` : "вызовов пока не было";
  return [
    { name: "autopilot", st: !ap.enabled ? off : lr.state === "error" ? ["error", "Ошибка такта"] : ap.job ? ["running", "Ведёт задание"] : ["healthy", "Работает"],
      d: ap.enabled ? `такт каждые ${ap.tick_s} с${ap.next_tick ? ` · следующий через ${fmtIn(ap.next_tick - Date.now() / 1000)}` : ""}` : "агенты не следят за заявками и данными" },
    { name: "intake", st: !ap.enabled ? off : ap.requests.new ? ["waiting", `Новых: ${ap.requests.new}`] : ["healthy", "Исправен"],
      d: open ? `${plural(open, "заявка ждёт", "заявки ждут", "заявок ждут")} решения` : `входящие разобраны · ${calls("intake")}` },
    { name: "watcher", st: !ap.enabled ? off : lr.state === "error" ? ["error", "Ошибка"] : ["healthy", "Исправен"], d: lr.reason || calls("watcher") },
    { name: "publisher", st: !ap.enabled ? off : ap.job ? ["waiting", "Ждёт прогон"] : ["healthy", "Исправен"],
      d: ap.current ? `действующая версия v${ap.current.id} · штраф ${num(ap.current.score, 1)}` : "опубликованных версий пока нет" },
    { name: "scheduler", label: "Конвейер генерации", short: "9×", st: active ? ["running", "Генерирует"] : ["idle", "Готов"],
      d: active ? `прогон ${active.id}` : "данные → ГА → CP-SAT → аудитории → критик → переговоры" },
  ];
}
const svcRow = s => { const a = agentOf(s.name); return `<a class="svc" href="#/agents#agent-${s.name}" style="--c:${a.color}"><span class="ic">${s.short || a.short}</span><span style="min-width:0"><b>${esc(s.label || a.ru)}</b><span class="d">${esc(s.d)}</span></span><span class="state ${s.st[0]}">${esc(s.st[1])}</span></a>`; };

// ------------------------------------------------------------------ control center (home)
async function renderHome() {
  const [runs, datasets, ap, reqs] = await Promise.all([api("/api/runs"), api("/api/datasets"), api("/api/autopilot"), api("/api/requests")]);
  const active = runs.find(r => r.status === "running" || r.status === "cancelling");
  const ds = datasets.find(d => d.valid && d.id === ap.profile.dataset) || datasets.find(d => d.valid);
  const cur = ap.current;
  const pub = cur ? await getPublished(cur.id) : null;
  const mt = pub && pub.version ? pubMetrics(pub, ds, ap.profile) : null;
  const soft = (cur && cur.soft) || {};
  const metric = (label, value, sub, icon, bar = null, cls = "") => `<div class="kpi metric ${cls}"><div class="l">${label}</div><span class="ico">${I[icon]}</span><div class="v">${value}</div>${bar != null ? `<div class="bar ${cls}"><i style="width:${Math.round(bar * 100)}%"></i></div>` : ""}<div class="d">${sub}</div></div>`;
  // recent activity: versions, service jobs, staff requests and manual runs, newest first
  const feed = [
    ...ap.versions.map(x => ({ ts: x.ts, c: agentOf("publisher").color, href: "#/schedule", t: `Опубликована версия v${x.id}`, s: x.diff.first ? `первая версия · ${x.diff.total_hours} ак. ч в неделю` : `перенесено ${x.diff.hours_moved} ч · групп ${x.diff.groups_affected} · штраф ${num(x.score, 1)}` })),
    ...ap.jobs.filter(j => j.status !== "published").map(j => ({ ts: j.finished || j.ts, c: agentOf("watcher").color, href: j.run_id ? `#/run/${encodeURIComponent(j.run_id)}` : "#/autopilot",
      t: `Задание №${j.id}: ${JOB_RU[j.kind] || j.kind} — ${(JOB_STATUS[j.status] || [j.status])[0]}`, s: (j.outcome && j.outcome.reason) || j.reason })),
    ...reqs.map(r => ({ ts: r.ts, c: agentOf("intake").color, href: `#/requests/${r.id}`, t: `Заявка №${r.id} от «${r.author}» — ${(REQ_STATUS[r.status] || REQ_STATUS.new).text}`, s: r.text })),
    ...runs.filter(r => !/-auto-/.test(r.id)).map(r => ({ ts: r.started, c: "var(--a-orchestrator)", href: `#/run/${encodeURIComponent(r.id)}`, t: `Прогон ${r.id.replace(/^\d{8}-\d{6}-/, "")}: ${statusInfo(r.status, !!r.cancelled).text}`, s: r.programs && r.programs.length ? r.programs.join(", ") : "все программы" })),
  ].sort((a, b) => b.ts - a.ts).slice(0, 8);
  const lr = ap.last_result || {};
  $("#main").innerHTML = page("", `
    <header class="dash-head">
      <div><h1>Расписание университета</h1>
        <div class="ver">${esc(ap.profile.university)} · ${cur ? `опубликована версия ${vtag(cur.id)} <span class="muted">${fmtDate(cur.ts)} · триместр ${cur.term ?? "?"}</span>` : '<span class="badge warn">опубликованной версии нет</span>'}</div></div>
      <div class="actions">
        <a class="btn" href="#/new">${I.playCircle} Сгенерировать</a>
        <a class="btn" href="#/autopilot" title="${esc(lr.reason || "")}"><span class="live-dot ${ap.enabled ? "beat" : "idle"}"></span>Автопилот: ${ap.enabled ? "работает" : "выключен"}</a>
        <a class="btn primary" href="#/requests/new">${I.squarePen} Новая заявка</a>
      </div>
    </header>
    ${active ? `<div class="active-strip"><div class="actions">${runStatusBadge(active)}<a href="#/run/${esc(active.id)}" class="mono"><b>${esc(active.id)}</b></a><span class="muted small">${fmtDur(active.elapsed_s)} · ${active.programs.length ? esc(active.programs.join(", ")) : "все программы"}</span></div><a class="btn primary sm" href="#/run/${esc(active.id)}/live">Ход выполнения ${I.arrow}</a></div>` : ""}
    <div class="kpi-row c4">
      ${mt ? metric("Конфликты", 0, "жёстких нарушений · проверено Критиком и Публикатором", "shield", null, "ok")
        + metric("Окна", `${num(soft.gap_hours_per_group, 1)}<small>ч</small>`, `на группу в неделю · всего ${num(soft.gap_hours_total, 0)} ч`, "clock")
        + metric("Покрытие групп", `${pct(mt.groups / (mt.total || 1))}`, `${mt.groups} из ${mt.total} групп профиля в расписании`, "users", mt.groups / (mt.total || 1))
        + metric("Загрузка аудиторий", pct(mt.util), `${mt.roomHours} из ${mt.fund * mt.days * 12} ауд.-часов · ${mt.fund} ауд., ${mt.days === 6 ? "Пн–Сб" : "Пн–Пт"} 08–20`, "door", mt.util)
        : `<div class="kpi" style="grid-column:1/-1"><div class="empty-state small">${I.cal}<p>Метрики появятся после первой опубликованной версии. ${ap.enabled ? "Автопилот уже работает над ней." : "Включите автопилот — агенты сгенерируют, проверят и опубликуют расписание сами."}</p>${ap.enabled ? "" : `<div class="actions"><button class="btn primary sm" id="h-on">${I.bot} Включить автопилот</button></div>`}</div></div>`}
    </div>
    <div class="grid c3">
      <section class="card">
        <div class="card-head"><h2>Последние события</h2><a class="btn sm ghost" href="#/autopilot">Журнал ${I.arrow}</a></div>
        ${feed.length ? `<div class="feed">${feed.map(f => `<a class="it" href="${f.href}"><span class="tm">${fmtWhen(f.ts)}</span><span class="dt" style="--c:${f.c}"></span><span class="tx">${esc(f.t)}<small>${esc(f.s || "")}</small></span></a>`).join("")}</div>`
          : `<div class="empty-state small">${I.clock}<p>Событий пока нет.</p></div>`}
      </section>
      <section class="card mini-runs">
        <div class="card-head"><h2>Последние прогоны</h2><a class="btn sm ghost" href="#/runs">Все ${I.arrow}</a></div>
        ${runs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Прогон</th><th>Статус</th><th class="num" title="Жёстких нарушений">Наруш.</th><th class="num">Когда</th></tr></thead><tbody>
          ${runs.slice(0, 7).map(r => `<tr class="link" data-id="${esc(r.id)}"><td class="id"><a href="#/run/${esc(r.id)}">${esc(r.id.replace(/^\d{8}-\d{6}-/, ""))}</a></td><td>${runStatusBadge(r)}</td><td class="num" style="color:${hvColor(r.hard_violations)}">${r.hard_violations ?? "—"}</td><td class="num muted mono">${fmtWhen(r.started)}</td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty-state small">${I.history}<p>Прогонов пока нет.</p></div>`}
      </section>
      <section class="card">
        <div class="card-head"><h2>Состояние агентов</h2><a class="btn sm ghost" href="#/agents">Все агенты ${I.arrow}</a></div>
        <div class="svc-list">${serviceStates(ap, active).map(svcRow).join("")}</div>
      </section>
    </div>`);
  bindRunRows($("#main"));
  const on = $("#h-on"); if (on) on.onclick = () => setPilot(true);
}
// The 24/7 loop: request -> watcher -> generation pipeline -> publisher -> students. `on` highlights a node.
function serviceLoop(on = "") {
  const nodes = [["intake", "Заявка и Приёмная", "Сотрудник пишет словами; Приёмная проверяет, что фраза понята и группа или аудитория существуют.", I.inbox],
    ["watcher", "Наблюдатель", "Видит новую заявку, правку Excel или простой и предлагает задание: перегенерация или поиск улучшения.", I.globe],
    ["scheduler", "Генерация: 9 агентов", "Данные → правила → ГА → CP-SAT → аудитории → критик → переговоры; тёплый старт от опубликованной версии.", I.layers],
    ["publisher", "Публикатор", "Пропускает только 0 нарушений; улучшение — если штраф меньше на 2 %, а в семестре ещё и не больше 10 % переносов.", I.shield],
    ["reporter", "Студенты и сотрудники", "Новая версия со штемпелем, объявление «что изменилось», Excel и календарь .ics для каждой группы.", I.calPlus]];
  return `<div class="loop">${nodes.map(([a, t, d, ic]) => `<div class="node ${on === a ? "on" : ""}" style="--c:${agentOf(a).color}"><div class="ic">${ic}</div><b>${t}</b><p>${d}</p></div>`).join("")}</div>`;
}
// mail-list rows
function reqItem(r, selected) {
  const s = REQ_STATUS[r.status] || REQ_STATUS.new;
  return `<a class="mi ${r.status === "new" || r.status === "review" ? "unread" : ""} ${selected === r.id ? "on" : ""}" href="#/requests/${r.id}">
    <span class="av">${esc(initials(r.author))}</span>
    <span style="min-width:0"><span class="who"><b>${esc(r.author)}</b><span class="st-dot" style="--c:${s.c}" title="${s.text}"></span><span class="tiny muted">${s.text}</span></span><span class="txt">${esc(r.text)}</span></span>
    <span class="tm">${fmtWhen(r.ts)}</span></a>`;
}
async function startPreset(id, btn) {
  const p = PRESETS.find(x => x.id === id);
  const old = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Запуск…'; }
  try {
    const r = await post("/api/runs", { dataset: "input.xlsx", term: 1, programs: p.programs, requirements: id === "demo" ? ["1 курс заканчивает до 17:00"] : [], solver_seconds: p.seconds, llm: "auto", name: id });
    location.hash = `#/run/${r.id}/live`;
  } catch (e) { toast(e.message, true); if (btn) { btn.disabled = false; btn.innerHTML = old; } }
}
function kpiTiles(r) {
  const s = r.soft || {}; const hv = r.hard_violations;
  return kpi(hv ?? "—", "жёстких нарушений", hv === 0 ? "ok" : hv > 0 ? "bad" : "") + kpi(num(s.gap_hours_per_group), "окон, ч на группу", "", "в неделю") +
    kpi(num(s.late_hours_18_plus), "часов после 18:00") + kpi(num(s.saturday_hours), "часов в субботу") +
    kpi(num(s.lectures_in_lecture_halls), "лекций в лекц. залах") + kpi(r.max_share != null ? pct(r.max_share) : "—", "макс. доля агента", r.max_share > 0.4 ? "bad" : "", "норма ≤ 40 %");
}
function runsTable(runs, withActions = true, compare = null) {
  return `<div class="tbl-wrap"><table class="tbl runs">
    <thead><tr>${compare ? '<th><span class="sr-only">Сравнить</span></th>' : ""}<th>Прогон</th><th>Статус</th><th>Данные</th><th class="num">Групп</th><th class="num">Нарушений</th><th class="num">Окон, ч/группу</th><th class="num">После 18:00, ч</th><th class="num">Время</th><th>LLM</th>${withActions ? "<th></th>" : ""}</tr></thead>
    <tbody>${runs.map(r => `<tr class="link" data-id="${esc(r.id)}">
      ${compare ? `<td><input type="checkbox" class="cmp" data-id="${esc(r.id)}" aria-label="сравнить ${esc(r.id)}" ${compare.includes(r.id) ? "checked" : ""} ${r.soft ? "" : "disabled"}></td>` : ""}
      <td class="id"><a href="#/run/${esc(r.id)}">${esc(r.id)}</a><div class="small">${fmtDate(r.started)}</div></td>
      <td>${runStatusBadge(r)}</td>
      <td><div>${r.programs && r.programs.length ? esc(r.programs.join(", ")) : "все программы"}</div><div class="small muted">триместр ${r.term ?? "?"}</div></td>
      <td class="num">${r.groups ?? "—"}</td>
      <td class="num" style="color:${hvColor(r.hard_violations)};font-weight:500">${r.hard_violations ?? "—"}</td>
      <td class="num">${r.soft ? num(r.soft.gap_hours_per_group) : "—"}</td>
      <td class="num">${r.soft ? num(r.soft.late_hours_18_plus) : "—"}</td>
      <td class="num">${fmtDur(r.elapsed_s)}</td>
      <td class="small muted">${esc(r.llm || "—")}</td>
      ${withActions ? `<td class="row-actions"><button class="btn sm icon subtle del" data-id="${esc(r.id)}" title="Удалить прогон" aria-label="Удалить ${esc(r.id)}" ${r.status === "running" || r.status === "cancelling" ? "disabled" : ""}>${I.trash}</button></td>` : ""}
    </tr>`).join("")}</tbody></table></div>`;
}
function bindRunRows(root, onDelete) {
  $$("tr.link", root).forEach(el => el.onclick = e => { if (e.target.closest("button, input, a")) return; location.hash = `#/run/${el.dataset.id}`; });
  $$("button.del", root).forEach(b => b.onclick = async () => {
    if (!confirmDelete(b.dataset.id)) return;
    try { await api(`/api/runs/${b.dataset.id}`, { method: "DELETE" }); toast("Прогон удалён"); if (onDelete) onDelete(); } catch (e) { toast(e.message, true); }
  });
}

// ------------------------------------------------------------------ runs list
const runsState = { filter: "all", q: "", compare: [] };
async function renderRuns() {
  const runs = await api("/api/runs");
  const main = $("#main");
  const draw = () => {
    const f = runsState.filter, q = runsState.q.toLowerCase();
    const isBad = r => ["failed", "incomplete"].includes(r.status) || (r.status === "stopped" && !r.cancelled);
    const shown = runs.filter(r => (f === "all" || (f === "ok" ? r.status === "ok" : f === "partial" ? r.status === "partial" : f === "bad" ? isBad(r) : f === "stopped" ? r.cancelled : ["running", "cancelling"].includes(r.status)))
      && (!q || r.id.toLowerCase().includes(q) || (r.programs || []).join(",").toLowerCase().includes(q)));
    const counts = { all: runs.length, ok: runs.filter(r => r.status === "ok").length, partial: runs.filter(r => r.status === "partial").length,
      bad: runs.filter(isBad).length, stopped: runs.filter(r => r.cancelled).length, live: runs.filter(r => ["running", "cancelling"].includes(r.status)).length };
    const cmp = runs.filter(r => runsState.compare.includes(r.id));
    main.innerHTML = page(pageHead("Прогоны", `${plural(runs.length, "прогон", "прогона", "прогонов")} в папке runs/. Отметьте до трёх прогонов, чтобы сравнить качество.`, `<a class="btn primary" href="#/new">${I.plus} Новый прогон</a>`), `
      <section class="card">
        <div class="actions" style="justify-content:space-between;margin-bottom:12px">
          <div class="chips" id="f" role="group" aria-label="Фильтр по статусу">${[["all", "все"], ["ok", "успешные"], ["partial", "частичные"], ["stopped", "остановленные"], ["bad", "с ошибкой"], ["live", "идут сейчас"]].map(([k, l]) => `<button type="button" class="chip ${f === k ? "on" : ""}" data-f="${k}" aria-pressed="${f === k}">${l}<span class="n">${counts[k]}</span></button>`).join("")}</div>
          <div class="search">${I.search}<input type="text" id="q" placeholder="Поиск по имени или программе" aria-label="Поиск" value="${esc(runsState.q)}"></div></div>
        ${shown.length ? runsTable(shown, true, runsState.compare) : `<div class="empty-state">${I.search}<h3>${runs.length ? "Ничего не найдено" : "Прогонов пока нет"}</h3><p>${runs.length ? "Измените фильтр или строку поиска." : "Запустите первый прогон — демо на программе IT занимает около десяти секунд."}</p><div class="actions"><a class="btn primary" href="#/new">${I.plus} Новый прогон</a></div></div>`}
        ${runsState.compare.length === 1 ? '<p class="hint" style="margin-top:8px">Отметьте ещё хотя бы один прогон, чтобы увидеть сравнение.</p>' : ""}
      </section>
      ${cmp.length >= 2 ? `<section class="card"><div class="card-head"><h2>Сравнение ${plural(cmp.length, "прогона", "прогонов", "прогонов")}</h2><button class="btn sm subtle" id="cmp-clear">Сбросить</button></div>${compareTable(cmp)}</section>` : ""}`);
    $$("#f .chip").forEach(c => c.onclick = () => { runsState.filter = c.dataset.f; draw(); });
    const qi = $("#q"); qi.oninput = debounce(() => { runsState.q = qi.value; draw(); const el = $("#q"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }, 250);
    $$("input.cmp").forEach(cb => cb.onchange = () => {
      if (cb.checked) { if (runsState.compare.length >= 3) { cb.checked = false; toast("Сравнить можно не более трёх прогонов"); return; } runsState.compare.push(cb.dataset.id); }
      else runsState.compare = runsState.compare.filter(x => x !== cb.dataset.id);
      draw();
    });
    const cc = $("#cmp-clear"); if (cc) cc.onclick = () => { runsState.compare = []; draw(); };
    bindRunRows(main, () => renderRuns());
  };
  draw();
}
function compareTable(rs) {
  const rows = [["Статус", r => runStatusBadge(r)], ["Программы", r => r.programs.length ? r.programs.join(", ") : "все"], ["Групп", r => r.groups ?? "—"],
    ["Жёстких нарушений", r => `<b style="color:${hvColor(r.hard_violations)}">${r.hard_violations ?? "—"}</b>`], ["Окон, ч на группу в неделю", r => num(r.soft.gap_hours_per_group)],
    ["Окон всего, ч", r => num(r.soft.gap_hours_total)], ["Часов после 18:00", r => num(r.soft.late_hours_18_plus)], ["Часов в субботу", r => num(r.soft.saturday_hours)],
    ["Практика раньше лекции", r => num(r.soft.precedence_violations)], ["Лекций в лекционных залах", r => num(r.soft.lectures_in_lecture_halls)],
    ["Шагов оркестратора", r => r.steps ?? "—"], ["Макс. доля агента", r => r.max_share != null ? pct(r.max_share) : "—"], ["Время", r => fmtDur(r.elapsed_s)], ["LLM", r => r.llm || "—"]];
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Метрика</th>${rs.map(r => `<th class="num"><a href="#/run/${esc(r.id)}/summary">${esc(r.id)}</a></th>`).join("")}</tr></thead>
    <tbody>${rows.map(([l, f]) => `<tr><td class="muted">${l}</td>${rs.map(r => `<td class="num">${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ agents (operational services; the full spec opens in the context panel)
async function renderAgents() {
  const [spec, ap, runs] = await Promise.all([api("/api/agents"), api("/api/autopilot"), api("/api/runs")]);
  if (Array.isArray(spec)) throw new Error("Сервер запущен со старым кодом. Остановите его (Ctrl+C) и запустите заново: python -m timetable web");
  const active = runs.find(r => r.status === "running" || r.status === "cancelling");
  const last = runs.find(r => r.status === "ok" || r.status === "partial");
  const lastRun = last ? await api(`/api/runs/${encodeURIComponent(last.id)}`).catch(() => null) : null;
  const runLoad = ((((lastRun || {}).result || {}).load) || {});
  const svcLoad = (ap.load || {}).agents || {};
  const svc = Object.fromEntries(serviceStates(ap, active).map(s => [s.name, s]));
  const all = [...(spec.service || []), ...spec.agents];
  const row = (a, isSvc) => {
    const m = agentOf(a.name), ld = isSvc ? svcLoad[a.name] : (runLoad.agents || {})[a.name];
    const st = isSvc ? svc[a.name] || { st: ["idle", "—"], d: "" } : { st: active ? ["running", "В прогоне"] : ["idle", "Готов"], d: active ? active.id : last ? `последний прогон: ${fmtWhen(last.started)}` : "прогонов ещё не было" };
    const llm = a.llm && !/не использует/.test(a.llm);
    return `<tr class="svc-row" data-a="${a.name}" tabindex="0">
      <td><div class="an" style="--c:${m.color}"><span class="ic">${m.short}</span><span><b>${esc(a.title)}</b><small>${esc(m.label)}</small></span></div></td>
      <td class="small" style="max-width:420px">${esc(a.tagline)}</td>
      <td><span class="state ${st.st[0]}">${esc(st.st[1])}</span><div class="tiny muted" style="max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(st.d || "")}</div></td>
      <td class="num mono">${a.tools.length}/5</td>
      <td>${llm ? `<span class="badge accent">${I.brain}LLM</span>` : '<span class="badge">правила</span>'}</td>
      <td>${ld ? `<div class="share"><div class="bar ${ld.share > 0.4 && !isSvc ? "bad" : ""}"><i style="width:${Math.round(ld.share * 100)}%;${ld.share > 0.4 && !isSvc ? "" : `background:${m.color}`}"></i></div><span>${pct(ld.share)}</span></div><div class="tiny muted">${plural(ld.calls, "вызов", "вызова", "вызовов")}</div>` : '<span class="muted small">—</span>'}</td></tr>`;
  };
  const head = "<tr><th>Агент</th><th>Роль</th><th>Статус</th><th class=\"num\">Инструменты</th><th>Решения</th><th>Доля вызовов</th></tr>";
  $("#main").innerHTML = page(pageHead("Агенты", `${plural(all.length, "агент", "агента", "агентов")}: ${(spec.service || []).length} в службе 24/7 и ${spec.agents.length} в конвейере генерации. У каждого одна роль и не более пяти инструментов; нажмите на строку, чтобы открыть спецификацию.`,
    `<a class="btn" href="#/autopilot">${I.bot} Автопилот</a><button class="btn primary" id="ag-tick">${I.refresh} Проверить сейчас</button>`), `
    <section class="card pad0 ops">
      <div class="tbl-wrap" style="border:0;border-radius:0"><table class="tbl"><thead>${head}</thead><tbody>
        <tr class="grp"><td colspan="6">Служба 24/7 · нагрузка за 2 дня из runs/_service · норма «≤ 40 %» проверяется на прогоне генерации</td></tr>
        ${(spec.service || []).map(a => row(a, true)).join("")}
        <tr class="grp"><td colspan="6">Конвейер генерации · ${last ? `нагрузка прогона ${esc(last.id)}: макс. доля ${pct(runLoad.max_share)} при норме ≤ 40 %` : "прогонов ещё не было"}</td></tr>
        ${spec.agents.map(a => row(a, false)).join("")}
      </tbody></table></div>
    </section>
    <details class="fold"><summary>${I.chev} Как агенты работают вместе<span class="hint">цикл службы и этапы прогона</span></summary><div class="in">
      <div class="l-cap">Цикл службы 24/7: Автопилот раз в минуту маршрутизирует сообщения</div>${serviceLoop()}
      <div class="l-cap" style="margin-top:20px">Этапы прогона генерации и ответственные агенты</div>${flow(() => "done")}
      <div class="grid c3" style="margin-top:16px">
        <div class="fact-card"><b>Оркестратор + blackboard</b><p>Оркестратор отправляет типизированные сообщения, агенты кладут большие объекты (датасет, решения) в SQLite и возвращают только статусы и ключи.</p></div>
        <div class="fact-card"><b>Решатель ≠ критик</b><p>Scheduler строит расписание в CP-SAT, Validator перепроверяет его другим кодом. Только проверенный кусок попадает в общее расписание.</p></div>
        <div class="fact-card"><b>Переговоры вместо падения</b><p>Если кусок невыполним, Negotiator по диагнозу выбирает самую дешёвую уступку: время, аудитории, суббота или мягкие правила.</p></div></div></div></details>
    <details class="fold" id="protocol"><summary>${I.chev} Протокол сообщений<span class="hint">core/messages.py</span></summary><div class="in">
      <div class="grid c2"><div><div class="l-cap">Поручение оркестратора</div><pre class="code">${esc(JSON.stringify(spec.protocol.message, null, 2))}</pre></div>
        <div><div class="l-cap">Ответ агента</div><pre class="code">${esc(JSON.stringify(spec.protocol.reply, null, 2))}</pre></div></div>
      <ul class="nice" style="margin-top:12px">${spec.protocol.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div></details>
    <details class="fold" id="tz"><summary>${I.chev} Требования ТЗ и где они выполняются<span class="hint">${spec.requirements.length} требований</span></summary><div class="in">
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Требование</th><th>Как выполнено</th><th>Где в коде</th></tr></thead>
        <tbody>${spec.requirements.map(r => `<tr><td><span class="ok-mark">${I.check}</span>${esc(r.req)}</td><td>${esc(r.how)}</td><td><code class="key">${esc(r.where)}</code></td></tr>`).join("")}</tbody></table></div></div></details>`);
  $("#ag-tick").onclick = () => $("#tick-btn").click();
  const open = name => {
    const a = all.find(x => x.name === name); if (!a) return;
    $$(".svc-row").forEach(r => r.classList.toggle("on", r.dataset.a === name));
    agentPanel(a);
  };
  $$(".svc-row").forEach(tr => {
    tr.onclick = () => open(tr.dataset.a);
    tr.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(tr.dataset.a); } };
  });
  const m = /#agent-([a-z]+)$/.exec(location.hash); if (m) open(m[1]);
}
function agentPanel(a) {
  const m = agentOf(a.name), list = xs => `<ul class="nice">${(xs || []).map(d => `<li>${esc(d)}</li>`).join("")}</ul>`;
  openCtx(`${m.label} · ${a.tools.length ? plural(a.tools.length, "инструмент", "инструмента", "инструментов") : "без инструментов"}`, esc(a.title), `
    <div class="agent-head" style="--c:${m.color}"><span class="ic">${m.short}</span><span class="small muted" style="flex:1;min-width:0">${esc(a.tagline)}</span></div>
    <p class="lead" style="margin:0">${esc(a.role)}</p>
    ${[["Что делает", a.does], ["Входы", a.inputs], ["Выходы", a.outputs]].filter(([, xs]) => (xs || []).length).map(([h, xs]) => `<div><div class="h-sm">${h}</div>${list(xs)}</div>`).join("")}
    ${(a.blackboard || []).length ? `<div><div class="h-sm">Ключи blackboard</div><div class="chips tight">${a.blackboard.map(k => `<code class="key">${esc(k)}</code>`).join("")}</div></div>` : ""}
    ${a.tools.length ? `<div><div class="h-sm">Инструменты</div><div class="tbl-wrap"><table class="tbl"><tbody>${a.tools.map(t => `<tr><td><code class="key">${esc(t.name)}</code><div class="small" style="margin-top:4px">${esc(t.ru || t.en)}</div></td></tr>`).join("")}</tbody></table></div></div>` : ""}
    <dl><dt>LLM</dt><dd>${esc(a.llm)}</dd><dt>Готово, когда</dt><dd>${esc(a.done)}</dd><dt>Надёжность</dt><dd>${esc(a.reliability)}</dd></dl>`);
}
// ------------------------------------------------------------------ new run
const draft = { dataset: null, term: 1, programs: [], requirements: "", allow_saturday: false, solver_seconds: 25, chunk_groups: 60, stream: 2, llm: "auto", name: "run", warm_start: false };
async function renderNew() {
  const [datasets, brief] = await Promise.all([api("/api/datasets"), api("/api/autopilot/brief").catch(() => ({}))]);
  if (!brief.current) draft.warm_start = false;
  if (!draft.dataset || !datasets.find(d => d.id === draft.dataset && d.valid)) {
    const def = datasets.find(d => d.valid && d.id === "input.xlsx") || datasets.find(d => d.valid);
    draft.dataset = def ? def.id : null;
  }
  const main = $("#main");
  let previewSeq = 0;
  const preview = async () => {
    const box = $("#preview"); if (!box) return;
    const lines = draft.requirements.split("\n").filter(l => l.trim());
    if (!lines.length || !draft.dataset) { box.innerHTML = ""; return; }
    const seq = ++previewSeq;
    try {
      const r = await post("/api/constraints/preview", { dataset: draft.dataset, texts: lines });
      if (seq !== previewSeq) return;
      const bySource = Object.fromEntries(r.parsed.map(p => [p.source, p]));
      box.innerHTML = `<div class="l-cap">Как система понимает требования <span class="muted">(офлайн-правила; с LLM разбор точнее)</span></div>` + lines.map(l => {
        const p = bySource[l.trim()];
        if (!p) return `<div class="prev-row"><span class="badge bad">${I.xCircle}не понято</span><span>${esc(l)}</span><span class="muted small">попадёт в предупреждения отчёта</span></div>`;
        if (p.unknown.length) return `<div class="prev-row"><span class="badge warn">${I.alertCircle}неизвестно: ${esc(p.unknown.join(", "))}</span><span>${esc(l)}</span><span class="muted small">правило будет пропущено</span></div>`;
        return `<div class="prev-row"><span class="badge ${p.hard ? "accent" : "info"}">${p.hard ? "жёсткое" : "мягкое"}</span><span>${esc(l)}</span><span class="muted small">${esc(describeSpec(p))}</span></div>`;
      }).join("");
    } catch (e) { box.innerHTML = `<div class="hint">предпросмотр недоступен: ${esc(e.message)}</div>`; }
  };
  const render = () => {
    const ds = datasets.find(d => d.id === draft.dataset);
    const groups = ds ? sum(ds.programs.filter(p => !draft.programs.length || draft.programs.includes(p.code)).map(p => p.groups)) : 0;
    const chunks = Math.max(1, Math.ceil(groups / (draft.chunk_groups || 60)));
    const est = chunks * (draft.solver_seconds || 25) + 15;
    const nReq = draft.requirements.split("\n").filter(l => l.trim()).length;
    const valid = datasets.filter(d => d.valid), invalid = datasets.filter(d => !d.valid);
    main.innerHTML = page(pageHead("Новый прогон", "Выберите данные и программы, опишите требования — агенты сделают остальное.", "", [["Новый прогон"]]), `
      <div class="presets">${PRESETS.map(p => `<button type="button" class="preset" data-p="${p.id}"><span class="pi">${I.bolt}</span><span><b>${p.title}</b><span class="sub">${p.sub}</span></span></button>`).join("")}</div>
      <div class="new-layout">
        <div class="new-main">
          <section class="card"><div class="card-head"><h2><span class="num-badge">1</span>Данные вуза</h2><span class="hint">${I.db} Excel с листами программ, Groups, Rooms и Timeslots. Файлы проверяются тем же загрузчиком, что и Data-агент.</span></div>
            <div class="grid c3" id="ds-grid">${valid.map(d => `<button type="button" class="ds-card ${d.id === draft.dataset ? "on" : ""}" data-id="${esc(d.id)}" title="${esc(d.path)}" aria-pressed="${d.id === draft.dataset}">
              <div class="actions" style="justify-content:space-between"><b>${I.db}${esc(d.name)}</b>${d.id === draft.dataset ? `<span class="ok-mark">${I.check}</span>` : ""}</div><div class="small muted">${esc(d.dir)}/ · ${d.size_kb} КБ${d.issues.warning ? ` · <span style="color:var(--warn)">${d.issues.warning} предупр.</span>` : ""}</div>
              <div class="stats"><span>${d.programs.length} прогр.</span><span>${d.groups} групп</span><span>${d.rooms} ауд.</span><span>${d.courses} курсов</span><span>${d.students} студ.</span></div></button>`).join("") || `<div class="empty-state" style="grid-column:1/-1">${I.db}<h3>Нет пригодных файлов</h3><p>Загрузите Excel вуза ниже.</p></div>`}</div>
            ${invalid.length ? `<details style="margin-top:10px"><summary class="hint" style="cursor:pointer">Ещё ${plural(invalid.length, "файл", "файла", "файлов")} не подходят (сырые учебные планы без листов Groups / Rooms / Timeslots)</summary><div class="hint" style="margin-top:6px">${invalid.map(d => `<div>${esc(d.dir)}/${esc(d.id)} — ${esc(d.error || "")}</div>`).join("")}</div></details>` : ""}
            <div class="dropzone" id="drop" style="margin-top:12px" tabindex="0" role="button">${I.down}Перетащите .xlsx сюда или нажмите, чтобы загрузить свой файл</div>
            <input type="file" id="file" accept=".xlsx" style="display:none"></section>
          <section class="card"><div class="card-head"><h2><span class="num-badge">2</span>Параметры расписания</h2></div>
            <div class="form-row" style="margin-bottom:14px">
              <div class="field"><label>Триместр учебного года</label><div class="seg" id="term" role="group">${[1, 2, 3].map(t => `<button type="button" class="${draft.term === t ? "on" : ""}" data-t="${t}" aria-pressed="${draft.term === t}">${t}</button>`).join("")}</div><div class="hint">1-й курс учится в ${draft.term}-м триместре, 2-й — в ${draft.term + 3}-м, 3-й — в ${draft.term + 6}-м</div></div>
              <div class="field"><label for="secs">Время решателя на кусок, с</label><input type="number" id="secs" min="1" max="600" value="${draft.solver_seconds}"><div class="hint">больше времени → меньше окон, дольше прогон</div></div>
              <div class="field"><label>Суббота</label><label class="switch"><input type="checkbox" id="sat" ${draft.allow_saturday ? "checked" : ""}><span class="track"></span><span>разрешить занятия с самого начала</span></label><div class="hint">иначе суббота открывается только как уступка</div></div>
            </div>
            <label class="switch" style="margin-bottom:14px" title="${brief.current ? "" : "нет опубликованной версии"}"><input type="checkbox" id="warm" ${draft.warm_start ? "checked" : ""} ${brief.current ? "" : "disabled"}><span class="track"></span><span><b>Тёплый старт от опубликованной версии${brief.current ? ` v${brief.current.id}` : ""}</b> — агенты двигают занятия, только если правило требует; студентам меньше изменений</span></label>
            <div class="field"><label>Программы</label>
              <div class="chips" id="progs" role="group" aria-label="Программы"><button type="button" class="chip ${draft.programs.length ? "" : "on"}" data-p="" aria-pressed="${!draft.programs.length}">Все программы</button>${ds ? ds.programs.map(p => `<button type="button" class="chip ${draft.programs.includes(p.code) ? "on" : ""}" data-p="${esc(p.code)}" aria-pressed="${draft.programs.includes(p.code)}">${esc(p.code)}<span class="n">${p.groups}</span></button>`).join("") : ""}</div></div>
            <div class="hint" style="margin-top:10px">Выбрано <b>${plural(groups, "группа", "группы", "групп")}</b> → ${plural(chunks, "кусок", "куска", "кусков")} по ~${draft.chunk_groups} групп, ожидаемое время ≈ <b>${fmtDur(est)}</b>.</div></section>
          <section class="card"><div class="card-head"><h2><span class="num-badge">3</span>Требования на естественном языке</h2><span class="muted small">${nReq ? plural(nReq, "строка", "строки", "строк") : "необязательно"}</span></div>
            <textarea id="reqs" aria-label="Требования" placeholder="Одно требование в строке, по-русски или по-английски…">${esc(draft.requirements)}</textarea>
            <div class="hint" style="margin-top:6px">Жёсткие правила («не ставить», «недоступна») решатель соблюдает всегда; мягкие («желательно») штрафуются. Если правило делает кусок невыполнимым, Negotiator ослабит его и объяснит почему.</div>
            <div class="chips" style="margin-top:10px" id="examples">${EXAMPLE_REQS.map(e => `<button type="button" class="chip" data-e="${esc(e)}">+ ${esc(e)}</button>`).join("")}</div>
            <div id="preview" class="preview"></div></section>
          <details class="card adv"><summary><span class="num-badge">4</span>Дополнительные настройки</summary>
            <div class="form-row" style="margin-top:14px">
              <div class="field"><label for="chunkg">Групп в куске</label><input type="number" id="chunkg" min="5" max="300" value="${draft.chunk_groups}"><div class="hint">размер подзадачи CP-SAT</div></div>
              <div class="field"><label for="stream">Групп в офлайн-потоке лекции</label><input type="number" id="stream" min="1" max="50" value="${draft.stream}"></div>
              <div class="field"><label for="llm">LLM</label><select id="llm"><option value="auto" ${draft.llm === "auto" ? "selected" : ""}>auto — OpenAI или Claude по ключу в .env</option><option value="openai" ${draft.llm === "openai" ? "selected" : ""}>openai — только OpenAI</option><option value="claude" ${draft.llm === "claude" ? "selected" : ""}>claude — только Claude</option><option value="offline" ${draft.llm === "offline" ? "selected" : ""}>offline — правила без LLM</option></select></div>
              <div class="field"><label for="name">Имя прогона</label><input type="text" id="name" maxlength="40" value="${esc(draft.name)}"></div>
            </div></details>
        </div>
        <aside class="new-side">
          <div class="card"><div class="card-head"><h2>Итог</h2></div>
            <table class="tbl kv"><tr><td>Данные</td><td>${ds ? esc(ds.name) : "—"}</td></tr><tr><td>Триместр</td><td>${draft.term}</td></tr>
              <tr><td>Программы</td><td>${draft.programs.length ? esc(draft.programs.join(", ")) : "все"}</td></tr><tr><td>Групп</td><td>${groups}</td></tr>
              <tr><td>Требований</td><td>${nReq}</td></tr><tr><td>Тёплый старт</td><td>${draft.warm_start ? "да" : "нет"}</td></tr><tr><td>LLM</td><td>${draft.llm}</td></tr><tr><td>Оценка времени</td><td>${fmtDur(est)}</td></tr></table>
            <button class="btn primary lg block" id="go" style="margin-top:16px" ${ds ? "" : "disabled"}>${I.play} Сгенерировать расписание</button>
            <div class="hint" style="margin-top:10px">Прогон идёт в фоне, страницу можно закрывать. Одновременно выполняется один прогон.</div></div>
          <div class="card soft"><div class="l-cap">Что произойдёт</div><ol class="timeline-list">${STAGES.map(s => `<li><span class="agent-dot" style="--c:${AGENT_BY[s.agent].color}"></span>${s.label} <span class="muted small">· ${AGENT_BY[s.agent].ru}</span></li>`).join("")}</ol></div>
        </aside></div>`);
    $$(".preset").forEach(b => b.onclick = () => { const p = PRESETS.find(x => x.id === b.dataset.p); draft.programs = [...p.programs]; draft.solver_seconds = p.seconds; draft.name = p.id; render(); toast(`Применён набор «${p.title}»`); });
    $$("#ds-grid .ds-card").forEach(el => el.onclick = () => { const d = datasets.find(x => x.id === el.dataset.id); if (d && d.valid) { draft.dataset = d.id; draft.programs = []; render(); } });
    $$("#term button").forEach(b => b.onclick = () => { draft.term = +b.dataset.t; render(); });
    $$("#progs .chip").forEach(c => c.onclick = () => { const p = c.dataset.p; if (!p) draft.programs = []; else draft.programs = draft.programs.includes(p) ? draft.programs.filter(x => x !== p) : [...draft.programs, p]; render(); });
    $$("#examples .chip").forEach(c => c.onclick = () => { draft.requirements = (draft.requirements.trim() ? draft.requirements.trim() + "\n" : "") + c.dataset.e; render(); });
    const reqs = $("#reqs"); const dp = debounce(preview, 400);
    reqs.oninput = () => { draft.requirements = reqs.value; dp(); };
    $("#secs").onchange = e => { draft.solver_seconds = +e.target.value || 25; render(); };
    $("#chunkg").onchange = e => { draft.chunk_groups = +e.target.value || 60; render(); };
    $("#stream").onchange = e => draft.stream = +e.target.value || 2;
    $("#llm").onchange = e => { draft.llm = e.target.value; render(); };
    $("#name").oninput = e => draft.name = e.target.value;
    $("#sat").onchange = e => draft.allow_saturday = e.target.checked;
    $("#warm").onchange = e => { draft.warm_start = e.target.checked; render(); };
    const drop = $("#drop"), file = $("#file");
    drop.onclick = () => file.click(); drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } };
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); }; drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
    file.onchange = () => file.files[0] && upload(file.files[0]);
    async function upload(f) {
      const fd = new FormData(); fd.append("file", f);
      drop.innerHTML = '<span class="spinner"></span> Загрузка и проверка файла…';
      try {
        const d = await api("/api/datasets/upload", { method: "POST", body: fd }); toast(d.valid ? `Файл ${d.name} загружен` : `Файл загружен, но не подходит: ${d.error}`, !d.valid);
        const i = datasets.findIndex(x => x.id === d.id); if (i >= 0) datasets[i] = d; else datasets.push(d); if (d.valid) { draft.dataset = d.id; draft.programs = []; } render();
      } catch (e) { toast(e.message, true); render(); }
    }
    $("#go").onclick = async () => {
      const btn = $("#go"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Запуск…';
      try {
        const r = await post("/api/runs", { dataset: draft.dataset, term: draft.term, programs: draft.programs, requirements: draft.requirements.split("\n"),
          allow_saturday: draft.allow_saturday, solver_seconds: draft.solver_seconds, chunk_groups: draft.chunk_groups, stream: draft.stream, llm: draft.llm, name: draft.name || "run", warm_start: draft.warm_start });
        location.hash = `#/run/${r.id}/live`;
      } catch (e) { toast(e.message, true); btn.disabled = false; btn.innerHTML = `${I.play} Сгенерировать расписание`; }
    };
    preview();
  };
  render();
}

// ------------------------------------------------------------------ combobox (group / room picker)
function bindCombo(root, opts, current, onPick) {
  const input = $("input", root), list = $(".list", root);
  const fill = q => {
    const items = opts.filter(o => o.toLowerCase().includes(q.toLowerCase())).slice(0, 80);
    list.innerHTML = items.map(o => `<div data-v="${esc(o)}" class="${o === current ? "hl" : ""}">${esc(o)}</div>`).join("") || '<div class="muted">нет совпадений</div>';
    $$("div[data-v]", list).forEach(d => d.onmousedown = () => onPick(d.dataset.v));
  };
  input.onfocus = () => { root.classList.add("open"); fill(""); input.select(); };
  input.onblur = () => setTimeout(() => root.classList.remove("open"), 120);
  input.oninput = () => fill(input.value);
  input.onkeydown = e => { if (e.key === "Enter") { const m = opts.find(o => o.toLowerCase() === input.value.toLowerCase()) || opts.find(o => o.toLowerCase().includes(input.value.toLowerCase())); if (m) onPick(m); } };
}

// ------------------------------------------------------------------ published timetable: data grid of all sessions + week grid of one group / room
const schedState = { version: null };
let reqPrefill = ""; // text handed from the timetable to the request form
async function renderSchedule(mode, sel) {
  mode = mode === "group" || mode === "room" ? mode : "table";
  const ap = await api("/api/autopilot");
  const pub = await getPublished(schedState.version || (ap.current && ap.current.id));
  const main = $("#main");
  if (!pub.version) {
    main.innerHTML = page("", `<section class="card"><div class="empty-state">${I.cal}<h2>Опубликованной версии пока нет</h2>
      <p>Расписание публикует агент «Публикатор» после проверки прогона. Включите автопилот — агенты сгенерируют первую версию по профилю вуза — или запустите прогон вручную.</p>
      <div class="actions">${ap.enabled ? `<button class="btn primary" id="s-tick">${I.refresh} Проверить сейчас</button>` : `<button class="btn primary" id="s-on">${I.bot} Включить автопилот</button>`}<a class="btn" href="#/new">${I.playCircle} Новый прогон</a></div></div></section>`);
    const on = $("#s-on"); if (on) on.onclick = () => setPilot(true);
    const tk = $("#s-tick"); if (tk) tk.onclick = () => $("#tick-btn").click();
    return;
  }
  const v = pub.version, m = v.metrics || {}, d = v.diff || {};
  const latest = ap.current && ap.current.id === v.id;
  const prev = ap.versions.filter(x => x.id < v.id).sort((a, b) => b.id - a.id)[0];
  const changed = prev && !d.first ? await changedSet(pub, prev.id) : null;
  if (currentPage !== "schedule") return;
  main.innerHTML = `<div class="container wide page">
    <div class="sched-bar">
      <h1>Расписание</h1>
      <button class="chip on" id="s-ver" title="Версия, изменения и правила">${vtag(v.id)} ${latest ? "действующая" : "архивная"} · ${fmtDate(v.ts)}</button>
      <div class="meta"><span>триместр ${m.term ?? "?"}</span><span>${plural(pub.groups.length, "группа", "группы", "групп")}</span><span>${pub.rows.length} ак. ч в неделю</span><span style="color:var(--ok)">0 нарушений</span>${d.first ? "" : `<span>перенесено ${d.hours_moved} ч у ${plural(d.groups_affected || 0, "группы", "групп", "групп")}</span>`}</div>
      <div class="actions right">
        <div class="seg" id="s-view" role="tablist" aria-label="Вид">${[["table", "Таблица", "table"], ["group", "Неделя группы", "calendar"], ["room", "Неделя аудитории", "door"]].map(([k, l, ic]) => `<button type="button" role="tab" aria-selected="${mode === k}" class="${mode === k ? "on" : ""}" data-m="${k}">${I[ic]}${l}</button>`).join("")}</div>
        <a class="btn sm" href="/api/published/files/timetable.xlsx?version=${v.id}">${I.down} Excel</a>
        ${latest ? "" : `<button class="btn sm primary" id="s-latest">К действующей версии</button>`}
      </div>
    </div>
    <section class="card pad0 print-host" id="s-body"></section></div>`;
  $$("#s-view button").forEach(b => b.onclick = () => { location.hash = b.dataset.m === "table" ? "#/schedule" : `#/schedule/${b.dataset.m}`; });
  $("#s-ver").onclick = () => versionPanel(pub, ap, mode, sel);
  const lt = $("#s-latest"); if (lt) lt.onclick = () => { schedState.version = null; renderSchedule(mode, sel); };
  if (mode === "table") scheduleGrid($("#s-body"), pub, changed);
  else weekView($("#s-body"), pub, ap, mode, sel, changed);
}

// Version card in the context panel: announcement, what changed, other versions, rules.
function versionPanel(pub, ap, mode, sel) {
  const v = pub.version, m = v.metrics || {}, d = v.diff || {};
  const groupsTop = Object.entries(d.groups || {}).sort((a, b) => (b[1].moved + b[1].rooms) - (a[1].moved + a[1].rooms)).slice(0, 24);
  openCtx(`Версия · опубликована ${fmtDate(v.ts)}`, `Версия ${vtag(v.id)}`, `
    ${v.announcement ? `<p class="lead" style="margin:0">${esc(v.announcement)}</p>` : ""}
    ${d.first ? `<div class="callout info">${I.info}<div>Первая версия расписания: ${d.total_hours} академических часов в неделю.</div></div>`
      : `<div class="kpi-row c3 inner">${kpi(d.hours_moved, "ч перенесено")}${kpi(d.room_changes, "ч смена ауд.")}${kpi(d.groups_affected, "групп")}</div>
      ${groupsTop.length ? `<div><div class="h-sm">Кого коснулось</div><div class="chips tight">${groupsTop.map(([g, x]) => `<a class="chip" href="#/schedule/group/${encodeURIComponent(g)}" title="перенесено ${x.moved} ч, смена аудитории ${x.rooms} ч"><span class="mono">${esc(g)}</span><span class="n">${x.moved + x.rooms}</span></a>`).join("")}</div></div>` : ""}`}
    <div><div class="h-sm">Версии</div>${ap.versions.map(x => `<a class="ver ${x.id === v.id ? "on" : ""}" href="#" data-v="${x.id}">${vtag(x.id)}<span style="min-width:0"><b class="small">${esc(JOB_RU[x.kind] || x.kind || "версия")}</b><div class="tiny muted">${x.diff.first ? "первая версия" : `перенесено ${x.diff.hours_moved} ч · штраф ${num(x.score, 1)}`}</div></span><span class="tiny mono muted">${fmtWhen(x.ts)}</span></a>`).join("")}</div>
    <div><div class="h-sm">Правила этой версии</div>${(m.requirements || []).length ? `<ul class="nice">${m.requirements.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : '<div class="hint">Только встроенные правила вуза: одна пара у группы за раз, вместимость и тип аудиторий, не более 8 часов в день, обед.</div>'}</div>`,
    `<a class="btn sm" href="/api/published/files/timetable.xlsx?version=${v.id}">${I.down} Excel</a><a class="btn sm primary" href="#/requests/new">${I.squarePen} Предложить изменение</a>`);
  $$("#ctx .ver[data-v]").forEach(a => a.onclick = e => {
    e.preventDefault(); const id = +a.dataset.v;
    schedState.version = ap.current && id === ap.current.id ? null : id; closeCtx(); renderSchedule(mode, sel);
  });
}

// One session (row of the published table) in the context panel.
function sessionPanel(r, pub, changed) {
  const k = r.room === "online" ? "online" : r.kind;
  const mates = [...new Set(pub.rows.filter(x => x.day === r.day && x.hour === r.hour && x.course === r.course && x.kind === r.kind && x.room === r.room).map(x => x.group))];
  const room = r.room === "online" ? "онлайн" : r.room;
  openCtx(`${DAY_RU[r.day]} · ${r.time}`, esc(r.course), `
    <div class="actions"><span class="kind ${k}">${k === "online" ? "онлайн" : esc(KIND_RU[r.kind] || r.kind)}</span>${changed && changed.has(rowKey(r)) ? '<span class="badge gold">изменено в этой версии</span>' : ""}</div>
    <dl><dt>Группа</dt><dd class="mono">${esc(r.group)}</dd><dt>Программа</dt><dd>${esc(r.program)}, ${r.year} курс</dd>
      <dt>Аудитория</dt><dd class="mono">${esc(room)}</dd><dt>Время</dt><dd>${DAY_RU[r.day]}, <span class="mono">${esc(r.time)}</span></dd>
      <dt>Поток</dt><dd>${mates.length > 1 ? `${plural(mates.length, "группа", "группы", "групп")}: <span class="mono">${mates.map(esc).join(", ")}</span>` : "одна группа"}</dd></dl>
    <div class="sub-card"><div class="h-sm">Как изменить занятие</div><p class="small muted" style="margin:0">Расписание правят агенты: заявку проверит Приёмная, Наблюдатель перестроит расписание с тёплым стартом (минимум переносов), Публикатор выпустит версию только без нарушений.</p></div>`,
    `<a class="btn sm" href="#/schedule/group/${encodeURIComponent(r.group)}">${I.calendar} Неделя группы</a>${r.room !== "online" ? `<a class="btn sm" href="#/schedule/room/${encodeURIComponent(r.room)}">${I.door} Аудитория</a>` : ""}<button class="btn sm primary" id="ctx-req">${I.squarePen} Предложить изменение</button>`);
  $("#ctx-req").onclick = () => { reqPrefill = `Группа ${r.group}: «${r.course}», ${DAY_SHORT[r.day]} ${r.time.slice(0, 5)}, ${room} — `; closeCtx(); location.hash = "#/requests/new"; };
}

// Week grid of one group / room: sticky day header, frozen time column, click a session for details.
function weekView(host, pub, ap, mode, sel, changed) {
  const index = indexRows(pub.rows);
  const opts = mode === "group" ? pub.groups : pub.rooms;
  if (!opts.includes(sel)) sel = opts[0] || "";
  const what = mode === "group" ? "Группа" : "Аудитория";
  const mine = mode === "group" ? (index.byGroup[sel] || []) : (index.byRoom[sel] || []);
  const ch = changed ? new Set(mine.filter(r => changed.has(rowKey(r))).map(r => `${r.day}|${r.hour}`)) : null;
  host.innerHTML = `<div class="tt-controls">
      <div class="combo inline" id="s-combo"><label for="s-sel" class="l-cap" style="margin:0">${what}</label><input type="text" id="s-sel" value="${esc(sel)}" autocomplete="off"><div class="list"></div></div>
      <div class="actions"><button class="btn sm icon" id="s-prev" aria-label="предыдущая" title="предыдущая">${I.chevL}</button><button class="btn sm icon" id="s-next" aria-label="следующая" title="следующая">${I.chev}</button></div>
      <div class="legend"><span><i class="lecture"></i>лекция</span><span><i class="practice"></i>практика</span><span><i class="lab"></i>лаборатория</span><span><i class="online"></i>онлайн</span>${ch ? `<span><i class="changed"></i>изменилось в v${pub.version.id}${ch.size ? ` (${ch.size} ч)` : ": ничего"}</span>` : ""}</div>
      <div class="actions" style="margin-left:auto">
        ${mode === "group" ? `<a class="btn sm" href="/api/published/ics?group=${encodeURIComponent(sel)}&version=${pub.version.id}" title="Повторяющиеся события на триместр для Google / Outlook / iPhone">${I.calPlus} В календарь</a>` : ""}
        <button class="btn sm subtle" id="s-print">${I.print} Печать</button></div></div>
    <div class="print-area"><div class="print-title">${what} ${esc(sel)} · ${esc(ap.profile.university)} · версия v${pub.version.id}</div>${weekGridHtml(pub, index, mode, sel, ch)}</div>`;
  const go = s => { location.hash = `#/schedule/${mode}/${encodeURIComponent(s)}`; };
  bindCombo($("#s-combo"), opts, sel, go);
  const step = k => go(opts[(opts.indexOf(sel) + k + opts.length) % opts.length]);
  $("#s-prev").onclick = () => step(-1); $("#s-next").onclick = () => step(1); $("#s-print").onclick = () => window.print();
  $$(".cell[data-d]", host).forEach(c => c.onclick = () => {
    const r = mine.find(x => x.day === +c.dataset.d && x.hour === +c.dataset.h); if (!r) return;
    $$(".cell.on", host).forEach(x => x.classList.remove("on")); c.classList.add("on");
    sessionPanel(r, pub, changed);
  });
}

// ---- data grid: virtual rows, frozen columns, sort, filter, group, resize, keyboard, bulk actions
const DG_COLS = [
  { k: "sel", t: "", w: 44, fz: true, fixed: true },
  { k: "group", t: "Группа", w: 124, fz: true, code: true },
  { k: "program", t: "Программа", w: 112 },
  { k: "year", t: "Курс", w: 72, num: true },
  { k: "day", t: "День", w: 84, val: r => r.day, txt: r => DAY_SHORT[r.day], g: r => DAY_RU[r.day] },
  { k: "hour", t: "Время", w: 118, code: true, val: r => r.hour, txt: r => r.time },
  { k: "course", t: "Дисциплина", w: 360, flex: true },
  { k: "kind", t: "Вид", w: 118, val: r => r.room === "online" ? "онлайн" : KIND_RU[r.kind] || r.kind },
  { k: "room", t: "Аудитория", w: 124, code: true },
  { k: "stream", t: "Поток", w: 78, num: true },
  { k: "chg", t: "Изм.", w: 66 },
];
const DG_GROUP = [["", "без группировки"], ["group", "по группе"], ["program", "по программе"], ["day", "по дню"], ["room", "по аудитории"], ["course", "по дисциплине"]];
const dg = { version: null, q: "", program: "", day: "", kind: "", onlyChanged: false, sort: null, groupBy: "", collapsed: new Set(), sel: new Set(), anchor: null, active: 0, col: 1, top: 0 };
const dgCmp = (a, b) => typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "ru", { numeric: true });

function scheduleGrid(host, pub, changed) {
  const rows = pub.rows, isChg = r => !!changed && changed.has(rowKey(r));
  if (dg.version !== pub.version.id) Object.assign(dg, { version: pub.version.id, sel: new Set(), collapsed: new Set(), active: 0, top: 0, anchor: null });
  if (!changed) dg.onlyChanged = false;
  let widths = {}; try { widths = JSON.parse(pref("dg-widths", "{}")); } catch (_) { }
  const W = c => widths[c.k] || c.w;
  const val = (c, r) => c.k === "chg" ? (isChg(r) ? 0 : 1) : c.val ? c.val(r) : r[c.k];
  const text = (c, r) => c.txt ? c.txt(r) : String(r[c.k] ?? "");
  const lastFz = DG_COLS.filter(c => c.fz).length - 1;
  const fzLeft = []; DG_COLS.reduce((x, c, j) => { if (c.fz) fzLeft[j] = x; return c.fz ? x + W(c) : x; }, 0);
  const opt = (v, l, cur) => `<option value="${esc(v)}" ${String(cur) === String(v) ? "selected" : ""}>${esc(l)}</option>`;
  host.innerHTML = `
    <div class="dg-toolbar">
      <div class="search">${I.search}<input type="text" id="dg-q" placeholder="Группа, дисциплина, аудитория" aria-label="Поиск по таблице" value="${esc(dg.q)}"></div>
      <select id="dg-prog" aria-label="Программа">${opt("", "Все программы", dg.program)}${pub.programs.map(p => opt(p, p, dg.program)).join("")}</select>
      <select id="dg-day" aria-label="День">${opt("", "Все дни", dg.day)}${DAY_RU.slice(0, rows.some(r => r.day === 5) ? 6 : 5).map((dn, i) => opt(i, dn, dg.day)).join("")}</select>
      <select id="dg-kind" aria-label="Вид занятия">${opt("", "Все виды", dg.kind)}${[["lecture", "лекции"], ["practice", "практики"], ["lab", "лабораторные"], ["online", "онлайн"]].map(([k, l]) => opt(k, l, dg.kind)).join("")}</select>
      ${changed ? `<label class="switch"><input type="checkbox" id="dg-chg" ${dg.onlyChanged ? "checked" : ""}><span class="track"></span><span class="small">только изменённые</span></label>` : ""}
      <span class="sep"></span>
      <label class="inl">${I.group}<select id="dg-gb" aria-label="Группировка">${DG_GROUP.map(([k, l]) => opt(k, l, dg.groupBy)).join("")}</select></label>
      <span style="margin-left:auto" class="actions"><button class="btn sm subtle" id="dg-reset">Сбросить</button><button class="btn sm" id="dg-csv" title="Выгрузить отфильтрованные строки">${I.down} CSV</button></span>
    </div>
    <div class="dg-bulk" id="dg-bulk" hidden></div>
    <div class="dg-scroll" id="dg" tabindex="0" role="grid" aria-label="Все занятия опубликованной версии" aria-rowcount="${rows.length}">
      <div class="dg-head" id="dg-head" role="row"></div><div class="dg-body" id="dg-body"></div></div>
    <div class="dg-foot"><span id="dg-count"></span><span><kbd>↑</kbd><kbd>↓</kbd> строки · <kbd>←</kbd><kbd>→</kbd> столбцы · <kbd>Пробел</kbd> выбрать · <kbd>Shift</kbd>+клик диапазон · <kbd>Enter</kbd> подробнее · <kbd>Ctrl</kbd>+<kbd>A</kbd> все</span></div>`;
  const scroller = $("#dg", host), body = $("#dg-body", host), head = $("#dg-head", host);
  const rowH = parseFloat(getComputedStyle(scroller).getPropertyValue("--grid-row")) || 40, HEAD = 38;
  const setCols = () => {
    scroller.style.setProperty("--cols", DG_COLS.map(c => c.flex ? `minmax(${W(c)}px, 1fr)` : `${W(c)}px`).join(" "));
    scroller.style.setProperty("--total", `${sum(DG_COLS.map(W))}px`);
  };
  let view = [], filtered = [];
  const recompute = () => {
    const q = dg.q.trim().toLowerCase();
    filtered = [];
    rows.forEach((r, i) => {
      if (dg.program && r.program !== dg.program) return;
      if (dg.day !== "" && r.day !== +dg.day) return;
      if (dg.kind && (dg.kind === "online" ? r.room !== "online" : r.kind !== dg.kind || r.room === "online")) return;
      if (dg.onlyChanged && !isChg(r)) return;
      if (q && !`${r.group} ${r.course} ${r.room} ${r.program} ${r.time}`.toLowerCase().includes(q)) return;
      filtered.push(i);
    });
    if (dg.sort) { const c = DG_COLS.find(x => x.k === dg.sort[0]), s = dg.sort[1]; filtered.sort((a, b) => dgCmp(val(c, rows[a]), val(c, rows[b])) * s || a - b); }
    view = [];
    if (dg.groupBy) {
      const c = DG_COLS.find(x => x.k === dg.groupBy), label = r => (c.g || c.txt || (x => String(x[c.k])))(r), buckets = new Map();
      for (const i of filtered) { const g = label(rows[i]); if (!buckets.has(g)) buckets.set(g, []); buckets.get(g).push(i); }
      const keys = [...buckets.keys()].sort((a, b) => dgCmp(val(c, rows[buckets.get(a)[0]]), val(c, rows[buckets.get(b)[0]])));
      for (const g of keys) { const list = buckets.get(g); view.push({ g, n: list.length }); if (!dg.collapsed.has(g)) list.forEach(i => view.push({ i })); }
    } else view = filtered.map(i => ({ i }));
    dg.active = Math.min(dg.active, Math.max(0, view.length - 1));
  };
  const drawHead = () => {
    const vis = view.filter(it => it.i != null), all = vis.length && vis.every(it => dg.sel.has(it.i));
    head.innerHTML = DG_COLS.map((c, j) => {
      const fz = c.fz ? ` fz${j === lastFz ? " last" : ""}" style="left:${fzLeft[j]}px` : "";
      if (c.k === "sel") return `<div class="dg-hc${fz}"><input type="checkbox" class="dg-check" id="dg-all" aria-label="выбрать все видимые строки" ${all ? "checked" : ""}></div>`;
      const s = dg.sort && dg.sort[0] === c.k ? dg.sort[1] : 0;
      return `<div class="dg-hc${fz}${c.num ? " num" : ""}" data-k="${c.k}" role="columnheader" aria-sort="${s > 0 ? "ascending" : s < 0 ? "descending" : "none"}" title="Сортировать">${c.t}${s ? `<span class="ar">${s > 0 ? "↑" : "↓"}</span>` : ""}${c.fixed ? "" : `<span class="rz" data-k="${c.k}" title="Ширина столбца"></span>`}</div>`;
    }).join("");
  };
  const cell = (c, j, r, i, p) => {
    const cls = `dg-cell${c.fz ? ` fz${j === lastFz ? " last" : ""}` : ""}${c.code ? " code" : ""}${c.num ? " num" : ""}${p === dg.active && j === dg.col ? " ac" : ""}`;
    const st = c.fz ? ` style="left:${fzLeft[j]}px"` : "";
    let inner;
    if (c.k === "sel") inner = `<input type="checkbox" class="dg-check" data-i="${i}" ${dg.sel.has(i) ? "checked" : ""} tabindex="-1" aria-label="выбрать строку">`;
    else if (c.k === "kind") { const k = r.room === "online" ? "online" : r.kind; inner = `<span class="kind ${k}">${k === "online" ? "онлайн" : esc(KIND_RU[r.kind] || r.kind)}</span>`; }
    else if (c.k === "chg") inner = isChg(r) ? '<span class="chg" title="изменилось в этой версии"></span>' : "";
    else if (c.k === "course") inner = `<span class="t" title="${esc(r.course)}">${esc(r.course)}</span>`;
    else if (c.k === "room" && r.room === "online") inner = '<span class="muted">онлайн</span>';
    else inner = esc(text(c, r));
    return `<div class="${cls}"${st} role="gridcell">${inner}</div>`;
  };
  const draw = () => {
    const top = scroller.scrollTop - HEAD, vh = scroller.clientHeight;
    body.style.height = `${view.length * rowH}px`;
    const from = Math.max(0, Math.floor(top / rowH) - 6), to = Math.min(view.length, Math.ceil((top + vh) / rowH) + 6);
    let html = "";
    for (let p = from; p < to; p++) {
      const it = view[p], y = p * rowH;
      if (it.i == null) { html += `<div class="dg-row grp ${dg.collapsed.has(it.g) ? "" : "open"} ${p === dg.active ? "act" : ""}" style="top:${y}px" data-p="${p}"><span class="gl">${I.chev}${esc(it.g)} <span class="muted">· ${it.n}</span></span></div>`; continue; }
      const r = rows[it.i];
      html += `<div class="dg-row ${dg.sel.has(it.i) ? "sel" : ""} ${p === dg.active ? "act" : ""}" style="top:${y}px" data-p="${p}" role="row" aria-selected="${dg.sel.has(it.i)}">${DG_COLS.map((c, j) => cell(c, j, r, it.i, p)).join("")}</div>`;
    }
    body.innerHTML = html || `<div class="dg-empty">Нет занятий, подходящих под фильтры. <a href="#" id="dg-clear">Сбросить фильтры</a></div>`;
    const cl = $("#dg-clear", body); if (cl) cl.onclick = e => { e.preventDefault(); reset(); };
  };
  const bulk = () => {
    const n = dg.sel.size, box = $("#dg-bulk", host);
    $("#dg-count", host).textContent = `Показано ${filtered.length} из ${rows.length} занятий${n ? ` · выбрано ${n}` : ""}`;
    box.hidden = !n; if (!n) return;
    const groups = [...new Set([...dg.sel].map(i => rows[i].group))];
    box.innerHTML = `<span>Выбрано: ${n}</span><button class="btn sm" data-b="csv">${I.down} CSV</button><button class="btn sm" data-b="copy">${I.copy} Копировать</button>
      ${groups.length === 1 ? `<a class="btn sm" href="#/schedule/group/${encodeURIComponent(groups[0])}">${I.calendar} Неделя ${esc(groups[0])}</a>` : ""}
      <button class="btn sm primary" data-b="req">${I.squarePen} Предложить изменение</button><button class="btn sm ghost" data-b="clear">Снять выбор</button>`;
  };
  const refresh = (keepScroll = true) => { recompute(); drawHead(); if (!keepScroll) scroller.scrollTop = 0; draw(); bulk(); };
  const reset = () => { Object.assign(dg, { q: "", program: "", day: "", kind: "", onlyChanged: false, sort: null, groupBy: "", collapsed: new Set() }); scheduleGrid(host, pub, changed); };
  const selected = () => [...dg.sel].sort((a, b) => a - b).map(i => rows[i]);
  const csv = list => {
    const cols = ["group", "program", "year", "day", "time", "course", "kind", "room", "stream"];
    const q = s => /[",\n;]/.test(s) ? `"${String(s).replace(/"/g, '""')}"` : s;
    const lines = [cols.join(","), ...list.map(r => cols.map(k => q(k === "day" ? DAY_RU[r.day] : String(r[k] ?? ""))).join(","))];
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" }));
    a.download = `timetable-v${pub.version.id}-${list.length}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const ensureVisible = () => {
    const y = dg.active * rowH, top = scroller.scrollTop, vh = scroller.clientHeight - HEAD;
    if (y < top) scroller.scrollTop = y; else if (y + rowH > top + vh) scroller.scrollTop = y + rowH - vh;
  };
  const openActive = () => { const it = view[dg.active]; if (!it) return; if (it.i == null) toggleGroup(it.g); else sessionPanel(rows[it.i], pub, changed); };
  const toggleGroup = g => { dg.collapsed.has(g) ? dg.collapsed.delete(g) : dg.collapsed.add(g); refresh(); };
  const toggleSel = (p, range = false) => {
    const it = view[p]; if (!it || it.i == null) return;
    if (range && dg.anchor != null) {
      const [a, b] = [Math.min(dg.anchor, p), Math.max(dg.anchor, p)];
      for (let x = a; x <= b; x++) if (view[x] && view[x].i != null) dg.sel.add(view[x].i);
    } else { dg.sel.has(it.i) ? dg.sel.delete(it.i) : dg.sel.add(it.i); dg.anchor = p; }
    drawHead(); draw(); bulk();
  };

  setCols(); refresh();
  scroller.scrollTop = dg.top;
  scroller.addEventListener("scroll", () => { dg.top = scroller.scrollTop; requestAnimationFrame(draw); });
  // toolbar
  const qi = $("#dg-q", host); qi.oninput = debounce(() => { dg.q = qi.value; refresh(false); }, 160);
  $("#dg-prog", host).onchange = e => { dg.program = e.target.value; refresh(false); };
  $("#dg-day", host).onchange = e => { dg.day = e.target.value; refresh(false); };
  $("#dg-kind", host).onchange = e => { dg.kind = e.target.value; refresh(false); };
  $("#dg-gb", host).onchange = e => { dg.groupBy = e.target.value; dg.collapsed = new Set(); refresh(false); };
  const chg = $("#dg-chg", host); if (chg) chg.onchange = () => { dg.onlyChanged = chg.checked; refresh(false); };
  $("#dg-reset", host).onclick = reset;
  $("#dg-csv", host).onclick = () => csv(filtered.map(i => rows[i]));
  // header: sort (asc -> desc -> off), resize by dragging the right edge, select all
  head.addEventListener("click", e => {
    if (e.target.closest(".rz")) return;
    if (e.target.id === "dg-all") { const vis = view.filter(it => it.i != null); const on = e.target.checked; vis.forEach(it => on ? dg.sel.add(it.i) : dg.sel.delete(it.i)); drawHead(); draw(); bulk(); return; }
    const hc = e.target.closest(".dg-hc[data-k]"); if (!hc) return;
    const k = hc.dataset.k, s = dg.sort && dg.sort[0] === k ? dg.sort[1] : 0;
    dg.sort = s === 0 ? [k, 1] : s === 1 ? [k, -1] : null; refresh();
  });
  head.addEventListener("pointerdown", e => {
    const rz = e.target.closest(".rz"); if (!rz) return;
    e.preventDefault(); rz.classList.add("on");
    const c = DG_COLS.find(x => x.k === rz.dataset.k), x0 = e.clientX, w0 = W(c);
    const move = ev => { widths[c.k] = Math.max(56, Math.round(w0 + ev.clientX - x0)); setCols(); };
    const up = () => { rz.classList.remove("on"); window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); setPref("dg-widths", JSON.stringify(widths)); scheduleGrid(host, pub, changed); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  });
  // rows: click = make active (and follow in the panel if it is open), checkbox = select, double click = details
  body.addEventListener("click", e => {
    const row = e.target.closest(".dg-row"); if (!row) return;
    const p = +row.dataset.p, it = view[p];
    if (it.i == null) { dg.active = p; toggleGroup(it.g); return; }
    if (e.target.classList.contains("dg-check")) { toggleSel(p, e.shiftKey); return; }
    if (e.shiftKey) { toggleSel(p, true); return; }
    dg.active = p; const cellEl = e.target.closest(".dg-cell"); if (cellEl) dg.col = Math.max(1, [...row.children].indexOf(cellEl));
    draw(); scroller.focus({ preventScroll: true });
    if (ctxOpen()) sessionPanel(rows[it.i], pub, changed);
  });
  body.addEventListener("dblclick", e => { const row = e.target.closest(".dg-row"); if (row && !e.target.classList.contains("dg-check")) { dg.active = +row.dataset.p; openActive(); } });
  scroller.addEventListener("keydown", e => {
    const pg = Math.max(1, Math.floor((scroller.clientHeight - HEAD) / rowH) - 1), last = view.length - 1;
    const move = to => { const from = dg.active; dg.active = Math.max(0, Math.min(last, to)); if (e.shiftKey && view[dg.active] && view[dg.active].i != null) { if (dg.anchor == null) dg.anchor = from; toggleSel(dg.active, true); } ensureVisible(); draw(); if (ctxOpen() && view[dg.active] && view[dg.active].i != null) sessionPanel(rows[view[dg.active].i], pub, changed); };
    const keys = { ArrowDown: () => move(dg.active + 1), ArrowUp: () => move(dg.active - 1), PageDown: () => move(dg.active + pg), PageUp: () => move(dg.active - pg),
      Home: () => move(0), End: () => move(last), ArrowRight: () => { dg.col = Math.min(DG_COLS.length - 1, dg.col + 1); draw(); }, ArrowLeft: () => { dg.col = Math.max(1, dg.col - 1); draw(); },
      " ": () => toggleSel(dg.active), Enter: openActive };
    if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "ф")) { e.preventDefault(); filtered.forEach(i => dg.sel.add(i)); drawHead(); draw(); bulk(); return; }
    if (e.key === "Escape" && !ctxOpen() && dg.sel.size) { e.stopPropagation(); dg.sel.clear(); dg.anchor = null; drawHead(); draw(); bulk(); return; }
    if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
  });
  // bulk actions
  $("#dg-bulk", host).addEventListener("click", async e => {
    const b = e.target.closest("[data-b]"); if (!b) return;
    const list = selected();
    if (b.dataset.b === "csv") csv(list);
    else if (b.dataset.b === "copy") {
      const tsv = list.map(r => [r.group, DAY_SHORT[r.day], r.time, r.course, KIND_RU[r.kind] || r.kind, r.room].join("\t")).join("\n");
      try { await navigator.clipboard.writeText(tsv); toast(`Скопировано строк: ${list.length}`); } catch (_) { toast("Браузер не дал доступ к буферу обмена", true); }
    } else if (b.dataset.b === "req") {
      const parts = list.slice(0, 6).map(r => `${r.group} ${DAY_SHORT[r.day]} ${r.time.slice(0, 5)} «${r.course}»`);
      reqPrefill = `Перенести занятия: ${parts.join("; ")}${list.length > 6 ? ` и ещё ${list.length - 6}` : ""} — `;
      location.hash = "#/requests/new";
    } else if (b.dataset.b === "clear") { dg.sel.clear(); dg.anchor = null; drawHead(); draw(); bulk(); }
  });
}
// ------------------------------------------------------------------ staff requests (mail-style inbox)
const reqState = { filter: "all" };
const REQ_FILTERS = [["all", "все", () => true], ["open", "ждут решения", r => r.status === "new" || r.status === "review"],
  ["accepted", "приняты", r => r.status === "accepted" || r.status === "applied"], ["rejected", "отклонены", r => r.status === "rejected"], ["withdrawn", "отозваны", r => r.status === "withdrawn"]];
async function renderRequests(sel) {
  const [reqs, brief] = await Promise.all([api("/api/requests"), api("/api/autopilot/brief")]);
  const filt = REQ_FILTERS.find(f => f[0] === reqState.filter) || REQ_FILTERS[0];
  const shown = reqs.filter(filt[2]);
  const selId = sel && sel !== "new" ? +sel : null;
  const r = selId ? reqs.find(x => x.id === selId) : null;
  const main = $("#main");
  main.innerHTML = page("", `<div class="mail">
    <aside class="mail-list">
      <div class="ml-head"><b>Входящие заявки</b><span class="n">${reqs.length}</span><span style="margin-left:auto" class="actions"><a class="btn sm primary" href="#/requests/new">${I.pen} Новая</a><button class="btn sm icon subtle" id="rq-refresh" title="Обновить" aria-label="Обновить">${I.refresh}</button></span></div>
      <div class="ml-filter">${REQ_FILTERS.map(([k, l, f]) => `<button class="chip ${reqState.filter === k ? "on" : ""}" data-f="${k}">${l}<span class="n">${reqs.filter(f).length}</span></button>`).join("")}</div>
      <div class="ml-items">${shown.map(x => reqItem(x, selId)).join("") || `<div class="empty-state small">${I.inbox}<p>${reqs.length ? "В этой папке пусто." : "Заявок пока нет."}</p></div>`}</div>
      <div class="ml-foot">${shown.length} / ${reqs.length}</div>
    </aside>
    <div class="reader" id="reader"></div></div>`);
  $$(".ml-filter .chip").forEach(c => c.onclick = () => { reqState.filter = c.dataset.f; renderRequests(sel); });
  $("#rq-refresh").onclick = () => renderRequests(sel);
  const reader = $("#reader");
  if (sel === "new") return composeRequest(reader, brief);
  if (!r) {
    reader.innerHTML = `<div class="reader-empty"><div>${I.inbox}<h3>Выберите заявку</h3><p>Выберите заявку из списка — она откроется здесь вместе с вердиктом агента «Приёмная». Новую заявку можно написать кнопкой «Новая» или клавишей N.</p></div></div>`;
  } else {
    const s = REQ_STATUS[r.status] || REQ_STATUS.new, vd = r.verdict || {};
    const can = { approve: ["new", "review", "rejected"].includes(r.status), reject: ["new", "review", "accepted"].includes(r.status), withdraw: ["new", "review", "accepted", "applied"].includes(r.status) };
    reader.innerHTML = `<section class="card">
      <div class="actions" style="justify-content:space-between">
        <div class="actions"><span class="mi" style="padding:0;border:0;grid-template-columns:38px auto"><span class="av">${esc(initials(r.author))}</span><span><span class="who"><b>${esc(r.author)}</b></span><span class="tiny muted mono">заявка №${r.id} · ${fmtDate(r.ts)}</span></span></span></div>
        <span class="badge ${s.cls} lg">${s.text}</span></div>
      <div class="letter">${esc(r.text)}</div>
      ${r.status === "new" ? `<div class="callout info">${brief.enabled ? '<span class="spinner"></span>' : I.info}<div>${brief.enabled ? "Приёмная разберёт заявку на ближайшем такте автопилота (раз в минуту)." : "Автопилот выключен: заявку разберут после его включения или по кнопке «Проверить сейчас»."}</div></div>`
      : `<div class="verdict" style="--c:${agentOf("intake").color}"><div class="i">IN</div><div><div class="actions" style="margin-bottom:4px">${agentTag("intake")}<span class="small muted">вердикт</span></div>
          ${vd.understood ? `<div style="font-weight:500">${esc(vd.understood)}${vd.hard === false ? ' <span class="badge info">мягкое</span>' : vd.hard ? ' <span class="badge accent">жёсткое</span>' : ""}</div>` : ""}
          <div class="small" style="color:var(--text-2);margin-top:4px">${esc(vd.reason || "")}</div>
          ${vd.summary && vd.summary !== vd.understood ? `<div class="small muted" style="margin-top:4px">${esc(vd.summary)}</div>` : ""}
          ${vd.dispatcher ? `<div class="small" style="margin-top:6px"><b>Диспетчер:</b> ${esc(vd.dispatcher)}</div>` : ""}</div></div>`}
      <h3 class="h-sm" style="margin-top:18px">Путь заявки</h3>
      <ul class="timeline-mini">
        <li style="--c:var(--primary)">Получена от «${esc(r.author)}» <span class="tm">${fmtDate(r.ts)}</span></li>
        ${r.status !== "new" ? `<li style="--c:${agentOf("intake").color}">Приёмная ${r.status === "rejected" ? "отклонила" : r.status === "review" ? "передала диспетчеру" : vd.understood ? "приняла" : "разобрала"}${vd.reason ? ` — ${esc(vd.reason)}` : ""}</li>` : ""}
        ${r.status === "accepted" ? `<li style="--c:${agentOf("watcher").color}">Наблюдатель запустит перегенерацию с тёплым стартом на ближайшем такте</li>` : ""}
        ${r.status === "applied" ? `<li style="--c:${agentOf("publisher").color}">Учтена в опубликованном расписании (задание №${r.job_id}) <a href="#/schedule">открыть ${I.arrow}</a></li>` : ""}
        ${r.status === "withdrawn" ? `<li style="--c:var(--text-4)">Отозвана — правило уберут при следующей перегенерации</li>` : ""}</ul>
      <div class="actions" style="margin-top:8px">
        ${can.approve ? `<button class="btn ok" data-act="approve">${I.check} Принять</button>` : ""}
        ${can.reject ? `<button class="btn danger" data-act="reject">${I.xCircle} Отклонить</button>` : ""}
        ${can.withdraw ? `<button class="btn subtle" data-act="withdraw">${I.undo} Отозвать${r.status === "applied" ? " (правило больше не нужно)" : ""}</button>` : ""}
      </div></section>`;
    $$("[data-act]", reader).forEach(b => b.onclick = async () => {
      const note = b.dataset.act === "reject" ? prompt("Причина отказа (увидит автор заявки):", "") : "";
      if (note === null) return;
      try { await post(`/api/requests/${r.id}/decision`, { action: b.dataset.act, note: note || "" }); toast("Решение сохранено — агенты учтут его на ближайшем такте"); renderRequests(sel); pollPilot(); } catch (e) { toast(e.message, true); }
    });
  }
  if (reqs.some(x => x.status === "new") && brief.enabled) pageTimer = setTimeout(() => { if (currentPage === "requests" && location.hash === `#/requests${sel ? `/${sel}` : ""}`) renderRequests(sel).catch(() => { }); }, 5000);
}
function composeRequest(reader, brief) {
  let author = ""; try { author = localStorage.getItem("req-author") || ""; } catch (_) { }
  reader.innerHTML = `<section class="card compose-card">
    <div class="card-head"><h2>${I.pen} Новая заявка</h2><span class="hint">Пишите так же, как диспетчеру: «Аудитория C1.3.361 недоступна в среду», «У группы IT-2301 свободна пятница». Агент «Приёмная» проверит фразу и объекты, Наблюдатель запустит перегенерацию, Публикатор выпустит новую версию с минимумом переносов.</span></div>
    <div class="form-row c2" style="margin-bottom:12px"><div class="field"><label for="rq-author">От кого</label><input type="text" id="rq-author" maxlength="60" placeholder="Деканат FIT, диспетчер, преподаватель…" value="${esc(author)}"></div>
      <div class="field"><span class="lbl">Кому</span><div class="chips tight" style="height:40px;align-items:center">${agentTag("intake")}${agentTag("watcher")}${agentTag("publisher")}</div></div></div>
    <div class="field"><label for="rq-text">Текст заявки</label><textarea id="rq-text" maxlength="300" placeholder="Одно требование, по-русски или по-английски"></textarea></div>
    <div class="chips" style="margin-top:10px" id="rq-ex">${REQ_EXAMPLES.map(e => `<button type="button" class="chip" data-e="${esc(e)}">+ ${esc(e)}</button>`).join("")}</div>
    <div id="rq-prev" class="preview"></div>
    <div class="actions" style="margin-top:16px"><button class="btn primary lg" id="rq-send">${I.send} Отправить агентам</button><a class="btn lg subtle" href="#/requests">Отмена</a></div>
  </section>`;
  const ta = $("#rq-text"), box = $("#rq-prev"); let seq = 0;
  const preview = debounce(async () => {
    const t = ta.value.trim(), my = ++seq; if (!t || !brief.dataset) { box.innerHTML = ""; return; }
    try {
      const r = await post("/api/constraints/preview", { dataset: brief.dataset, texts: [t] }); if (my !== seq) return;
      const p = r.parsed[0];
      box.innerHTML = `<div class="l-cap">Как это поймёт Приёмная</div>` + (!p ? `<div class="prev-row"><span class="badge warn">${I.alertCircle}не распознано</span><span>${esc(t)}</span><span class="muted small">решение примет диспетчер; с LLM фразу разберёт агент «Ограничения»</span></div>`
        : p.unknown.length ? `<div class="prev-row"><span class="badge bad">${I.xCircle}нет в данных: ${esc(p.unknown.join(", "))}</span><span>${esc(t)}</span><span class="muted small">заявка будет отклонена — проверьте код группы или аудитории</span></div>`
        : `<div class="prev-row"><span class="badge ${p.hard ? "accent" : "info"}">${p.hard ? "жёсткое правило" : "мягкое пожелание"}</span><span>${esc(t)}</span><span class="muted small">${esc(describeSpec(p))}</span></div>`);
    } catch (_) { box.innerHTML = ""; }
  }, 350);
  ta.oninput = preview; ta.focus();
  if (reqPrefill) { ta.value = reqPrefill; reqPrefill = ""; ta.setSelectionRange(ta.value.length, ta.value.length); preview(); }
  $$("#rq-ex .chip").forEach(c => c.onclick = () => { ta.value = c.dataset.e; preview(); ta.focus(); });
  $("#rq-send").onclick = async () => {
    const text = ta.value.trim(), who = $("#rq-author").value.trim() || "диспетчер";
    if (text.length < 3) { toast("Напишите текст заявки", true); ta.focus(); return; }
    const b = $("#rq-send"); b.disabled = true; b.innerHTML = '<span class="spinner"></span> Отправка…';
    try {
      try { localStorage.setItem("req-author", who); } catch (_) { }
      const r = await post("/api/requests", { text, author: who });
      if (brief.enabled) { try { await post("/api/autopilot/tick", {}); } catch (_) { } }
      toast(brief.enabled ? "Заявка отправлена — агенты уже разбирают её" : "Заявка сохранена; автопилот выключен");
      location.hash = `#/requests/${r.id}`; pollPilot();
    } catch (e) { toast(e.message, true); b.disabled = false; b.innerHTML = `${I.send} Отправить агентам`; }
  };
}

// ------------------------------------------------------------------ autopilot (24/7 service console)
const pilotUi = { open: new Set() };
async function renderAutopilot() {
  const [ap, datasets] = await Promise.all([api("/api/autopilot"), api("/api/datasets")]);
  const main = $("#main");
  if (currentPage !== "autopilot") return;
  const editing = document.activeElement && document.activeElement.closest && document.activeElement.closest("#profile");
  if (editing) { pageTimer = setTimeout(() => renderAutopilot().catch(() => { }), 5000); return; } // do not wipe a form the dispatcher is typing in
  const lr = ap.last_result || {}, job = ap.job, prof = ap.profile;
  const now = Date.now() / 1000;
  const stateText = job ? `Идёт ${JOB_RU[job.kind] || job.kind}: ${job.reason}` : !ap.enabled ? "Выключен: агенты не следят за заявками и данными" : lr.state === "error" ? `Ошибка такта: ${lr.reason}` : lr.reason ? `Всё спокойно: ${lr.reason}` : "Ждёт первого такта";
  const hot = job ? "scheduler" : lr.state === "reviewed" ? "publisher" : lr.state === "started" ? "watcher" : "";
  const ds = datasets.find(d => d.id === prof.dataset);
  const load = Object.entries((ap.load || {}).agents || {});
  main.innerHTML = page("", `
    <section class="card">
      <div class="pilot-hero">
        <div class="pilot-orb ${ap.enabled ? "on" : "off"}">${I.bot}</div>
        <div><h1>Автопилот 24/7 <span class="badge ${ap.enabled ? (lr.state === "error" ? "bad" : "ok") : ""} lg">${ap.enabled ? '<i class="live-dot beat"></i> работает' : "выключен"}</span></h1>
          <p>${esc(stateText)}</p>
          <div class="actions" style="margin-top:8px;font-size:13px;color:var(--text-3);gap:6px 16px"><span>такт каждые ${ap.tick_s} с</span>${ap.last_tick ? `<span>последний: ${fmtWhen(ap.last_tick)}</span>` : ""}${ap.next_tick ? `<span>следующий через ${fmtIn(ap.next_tick - now)}</span>` : ""}<span>режим запуска: ${esc(ap.mode)}</span></div></div>
        <div class="actions" style="justify-content:flex-end">
          <button class="big-switch ${ap.enabled ? "on" : ""}" id="ap-sw" aria-pressed="${ap.enabled}">${ap.enabled ? "Включён" : "Выключен"}<span class="track"></span></button>
          <button class="btn" id="ap-tick">${I.refresh} Проверить сейчас</button></div>
      </div>
    </section>
    ${job ? `<div class="active-strip"><div class="actions"><span class="badge accent"><span class="spinner"></span>${esc(JOB_RU[job.kind] || job.kind)}</span><b>Задание №${job.id}</b><span class="muted">${esc(job.reason)}</span></div>${job.run_id ? `<a class="btn primary sm" href="#/run/${esc(job.run_id)}/live">Смотреть агентов ${I.arrow}</a>` : ""}</div>` : ""}
    <div class="kpi-row">${kpi(ap.current ? `v${ap.current.id}` : "—", "действующая версия", "accent")}${kpi(ap.jobs.length, "заданий всего")}${kpi(ap.jobs.filter(j => j.status === "published").length, "опубликовано")}${kpi((ap.requests.new || 0) + (ap.requests.review || 0), "заявок ждут решения", (ap.requests.review || 0) ? "warn" : "")}${kpi(ap.standing.length, "постоянных правил")}${kpi((ap.load || {}).total_calls ?? 0, "вызовов агентов службы", "", "за 2 дня")}</div>
    <section class="card"><div class="card-head"><h2>${I.refresh} Цикл службы</h2><span class="hint">Подсвечен шаг, на котором служба сейчас.</span></div>${serviceLoop(hot)}</section>
    <div class="two-col">
      <div class="col">
        <section class="card">
          <div class="card-head"><h2>${I.layers} Задания агентов</h2><span class="hint">Каждое задание — обычный многоагентный прогон, запущенный автопилотом. Публикатор решает, выпускать ли его.</span></div>
          ${ap.jobs.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>№</th><th>Вид</th><th>Почему</th><th>Статус</th><th>Итог</th></tr></thead><tbody>${ap.jobs.map(j => {
      const st = JOB_STATUS[j.status] || [j.status, ""], o = j.outcome || {};
      return `<tr><td class="mono">${j.id}</td><td class="nowrap">${esc(JOB_RU[j.kind] || j.kind)}<div class="tiny muted">${fmtWhen(j.ts)}</div></td><td class="small">${esc(j.reason)}</td>
              <td><span class="badge ${st[1]}">${j.status === "running" ? '<span class="spinner"></span>' : ""}${st[0]}</span>${j.run_id ? `<div class="tiny"><a class="mono" href="#/run/${esc(j.run_id)}">прогон ${I.arrow}</a></div>` : ""}</td>
              <td class="small">${o.version ? `<a href="#/schedule"><b>v${o.version}</b></a> · ` : ""}${esc(o.reason || "")}${o.diff && !o.diff.first ? `<div class="tiny muted">перенесено ${o.diff.hours_moved} ч, групп ${o.diff.groups_affected}</div>` : ""}</td></tr>`;
    }).join("")}</tbody></table></div>` : `<div class="empty-state small">${I.layers}<p>Заданий пока не было. ${ap.enabled ? "Первое появится на ближайшем такте." : "Включите автопилот."}</p></div>`}
        </section>
        <section class="card">
          <div class="card-head"><h2>${I.msg} Журнал службы</h2><span class="hint">Такты автопилота: тихие проверки свёрнуты, такты с действиями раскрываются в сообщения и вызовы агентов. Источник: runs/_service/events-ГГГГММДД.jsonl.</span></div>
          ${ap.cycles.length ? ap.cycles.map(c => {
      const o = c.outcome || {}, quiet = !c.events.length;
      const badge = { idle: ["", "тихо"], busy: ["accent", "ждёт прогон"], started: ["accent", "запуск"], reviewed: ["ok", "проверка"], deferred: ["warn", "отложено"], error: ["bad", "ошибка"] }[c.state] || ["", c.state];
      return `<details class="cycle ${quiet ? "quiet" : ""}" data-t="${c.tick}" ${pilotUi.open.has(c.tick) ? "open" : ""}><summary><span class="tk">#${c.tick}</span><span><span class="badge ${badge[0]}">${badge[1]}</span></span><span class="small" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.reason || (o.status ? `${o.status}${o.version ? ` → v${o.version}` : ""}` : o.run_id || ""))}</span><span class="tk">${fmtWhen(c.ts)}</span></summary>
              ${quiet ? "" : `<div class="ev">${c.events.filter(e => e.kind !== "info").map(e => `<div>${agentTag(e.agent)}<span>${e.kind === "message" ? `${e.to ? `→ ${esc(agentOf(e.to).ru)} · ` : ""}${esc(e.task || "")}${e.intent ? ` <span class="muted">(${e.intent === "error" ? "ошибка" : "ответ"})</span>` : ""}` : e.kind === "tool_call" ? `инструмент ${esc(e.name)} <span class="muted">${e.status} · ${e.duration_ms} мс</span>` : e.kind === "llm_call" ? `LLM ${esc(e.name)} <span class="muted">${esc(e.provider || "")} · ${e.status}</span>` : esc(e.msg || e.kind)}</span></div>`).join("")}</div>`}</details>`;
    }).join("") : `<div class="empty-state small"><p>Тактов ещё не было.</p></div>`}
        </section>
      </div>
      <div class="col">
        <section class="card">
          <div class="card-head"><h2>${I.cal} Версии</h2><a class="btn sm ghost" href="#/schedule">Расписание ${I.arrow}</a></div>
          ${ap.versions.length ? ap.versions.map(x => `<a class="ver" href="#/schedule">${vtag(x.id)}<span style="min-width:0"><b class="small" style="font-weight:600">${esc(JOB_RU[x.kind] || x.kind || "")}</b><div class="tiny muted">${x.diff.first ? `${x.diff.total_hours} ч в неделю` : `перенесено ${x.diff.hours_moved} ч · групп ${x.diff.groups_affected}`} · штраф ${num(x.score, 1)}</div></span><span class="tiny mono muted">${fmtWhen(x.ts)}</span></a>`).join("") : '<div class="hint">Версий пока нет.</div>'}
        </section>
        <section class="card">
          <div class="card-head"><h2>${I.users} Нагрузка агентов службы</h2></div>
          ${load.length ? `<div class="hbars">${load.map(([a, v]) => `<div class="row"><span>${agentTag(a)}</span><div class="bar"><i style="width:${v.share * 100}%;background:${agentOf(a).color}"></i></div><span class="v">${v.calls} · ${pct(v.share)}</span></div>`).join("")}</div>
          <p class="hint" style="margin:10px 0 0">Вызовы инструментов и LLM за два дня. В простое больше всего работает Наблюдатель — он осматривает данные каждый такт; правило «≤ 40 %» проверяется на прогоне генерации (сценарий S12).</p>` : '<div class="hint">Вызовов ещё не было.</div>'}
        </section>
        <section class="card" id="profile">
          <div class="card-head"><h2>${I.shield} Профиль вуза</h2><span class="hint">По этому профилю агенты генерируют и поддерживают расписание. Изменение профиля — тоже повод для перегенерации.</span></div>
          <div class="field"><label for="pf-uni">Университет</label><input type="text" id="pf-uni" value="${esc(prof.university)}"></div>
          <div class="form-row c2" style="margin-top:12px">
            <div class="field"><label for="pf-ds">Данные</label><select id="pf-ds">${datasets.filter(d => d.valid).map(d => `<option value="${esc(d.id)}" ${d.id === prof.dataset ? "selected" : ""}>${esc(d.name)} · ${d.groups} групп</option>`).join("")}</select></div>
            <div class="field"><span class="lbl">Триместр</span><div class="seg" id="pf-term">${[1, 2, 3].map(t => `<button type="button" class="${prof.term === t ? "on" : ""}" data-t="${t}">${t}</button>`).join("")}</div></div></div>
          <div class="field" style="margin-top:12px"><span class="lbl">Программы</span><div class="chips tight" id="pf-progs"><button type="button" class="chip ${prof.programs.length ? "" : "on"}" data-p="">все</button>${ds ? ds.programs.map(p => `<button type="button" class="chip ${prof.programs.includes(p.code) ? "on" : ""}" data-p="${esc(p.code)}">${esc(p.code)}<span class="n">${p.groups}</span></button>`).join("") : ""}</div></div>
          <div class="field" style="margin-top:12px"><label for="pf-reqs">Постоянные правила вуза</label><textarea id="pf-reqs" style="min-height:90px" placeholder="Одно правило в строке">${esc(prof.requirements.join("\n"))}</textarea><div class="hint">Плюс принятые заявки: ${ap.standing.length - prof.requirements.length}.</div></div>
          <div class="form-row c2" style="margin-top:12px">
            <div class="field"><label for="pf-secs">Решатель, с на кусок</label><input type="number" id="pf-secs" min="1" max="600" value="${prof.solver_seconds}"></div>
            <div class="field"><label for="pf-llm">LLM</label><select id="pf-llm">${["auto", "openai", "claude", "offline"].map(x => `<option ${x === prof.llm ? "selected" : ""}>${x}</option>`).join("")}</select></div>
            <div class="field"><label for="pf-every">Искать улучшение раз в, мин</label><input type="number" id="pf-every" min="0" max="10080" value="${prof.improve_every_min}"></div>
            <div class="field"><label for="pf-att">Попыток на версию</label><input type="number" id="pf-att" min="0" max="50" value="${prof.max_improve_attempts}"></div>
            <div class="field"><label for="pf-stable">Семестр начинается</label><input type="date" id="pf-stable" value="${esc(prof.stable_after || "")}"><div class="hint">после этой даты изменения должны быть малыми</div></div>
            <div class="field"><label for="pf-moved">Макс. переносов в семестре, %</label><input type="number" id="pf-moved" min="0" max="100" value="${Math.round(prof.max_moved_share * 100)}"></div></div>
          <label class="switch" style="margin-top:12px"><input type="checkbox" id="pf-warm" ${prof.warm_start ? "checked" : ""}><span class="track"></span><span>Тёплый старт от опубликованной версии</span></label>
          <div class="actions" style="margin-top:14px"><button class="btn primary" id="pf-save">${I.check} Сохранить профиль</button></div>
        </section>
      </div>
    </div>`);
  $("#ap-sw").onclick = () => setPilot(!ap.enabled);
  $("#ap-tick").onclick = () => $("#tick-btn").click();
  $$("details.cycle").forEach(dt => dt.addEventListener("toggle", () => { const t = +dt.dataset.t; if (dt.open) pilotUi.open.add(t); else pilotUi.open.delete(t); }));
  let term = prof.term, progs = [...prof.programs];
  $$("#pf-term button").forEach(b => b.onclick = () => { term = +b.dataset.t; $$("#pf-term button").forEach(x => x.classList.toggle("on", x === b)); });
  $$("#pf-progs .chip").forEach(c => c.onclick = () => {
    const p = c.dataset.p; progs = !p ? [] : progs.includes(p) ? progs.filter(x => x !== p) : [...progs, p];
    $$("#pf-progs .chip").forEach(x => x.classList.toggle("on", x.dataset.p ? progs.includes(x.dataset.p) : !progs.length));
  });
  $("#pf-save").onclick = async () => {
    const body = { university: $("#pf-uni").value.trim() || "Astana IT University", dataset: $("#pf-ds").value, term, programs: progs,
      requirements: $("#pf-reqs").value.split("\n").map(x => x.trim()).filter(Boolean), solver_seconds: +$("#pf-secs").value || 25, llm: $("#pf-llm").value,
      warm_start: $("#pf-warm").checked, improve_every_min: +$("#pf-every").value || 0, max_improve_attempts: +$("#pf-att").value || 0,
      stable_after: $("#pf-stable").value || "", max_moved_share: Math.min(1, Math.max(0, (+$("#pf-moved").value || 0) / 100)) };
    try { await api("/api/autopilot/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); toast("Профиль сохранён — Наблюдатель учтёт его на ближайшем такте"); document.activeElement.blur(); renderAutopilot(); }
    catch (e) { toast(e.message, true); }
  };
  pageTimer = setTimeout(() => { if (currentPage === "autopilot") renderAutopilot().catch(() => { }); }, ap.job ? 3000 : 6000);
}

// ------------------------------------------------------------------ run page (live + results)
async function renderRun(id, tab, mode, sel) {
  if (currentView instanceof RunView && currentView.id === id && !currentView.dead) { currentView.applyRoute(tab, mode, sel); return; }
  const view = new RunView(id);
  view.applyRoute(tab, mode, sel);
  currentView = view;
  await view.init();
}
const TABS = [["summary", "Сводка"], ["live", "Журнал агентов"], ["timetable", "Расписание"], ["conflicts", "Конфликты и переговоры"], ["quality", "Качество"], ["data", "Данные и ограничения"], ["report", "Отчёт и файлы"]];

class RunView {
  constructor(id) {
    this.id = id; this.events = []; this.offset = 0; this.tab = null; this.timer = null; this.dead = false;
    this.calls = {}; this.active = null; this.stage = ""; this.stageTask = ""; this.chunksTotal = null; this.chunksDone = 0; this.negotiations = 0; this.errors = 0; this.repairs = 0; this.msgs = 0;
    this.doneStages = new Set(); this.touched = new Set(); this.agentErrors = new Set();
    this.logFilter = { agent: "", kind: "" }; this.results = null; this.tt = null; this.details = null; this.report = null;
    this.ttMode = "group"; this.ttSel = "";
  }
  destroy() { this.dead = true; clearTimeout(this.timer); }
  applyRoute(tab, mode, sel) {
    if (tab && TABS.some(t => t[0] === tab)) this.tab = tab;
    if (mode && ["group", "room", "heat"].includes(mode)) this.ttMode = mode;
    if (sel) this.ttSel = sel;
    if (this.status) this.renderTabs();
  }
  syncHash() {
    const parts = [`#/run/${this.id}`, this.tab];
    if (this.tab === "timetable") parts.push(this.ttMode, encodeURIComponent(this.ttSel));
    const h = parts.join("/"); if (location.hash !== h) history.replaceState(null, "", h);
  }
  get running() { return this.status.status === "running" || this.status.status === "cancelling"; }
  get cancelled() { return !!this.status.cancelled; }
  get files() { return this.status.files || []; }
  has(f) { return this.files.includes(f); }

  async init() {
    this.status = await api(`/api/runs/${this.id}`);
    if (!this.tab) this.tab = this.running ? "live" : "summary";
    this.renderShell();
    await this.pump();
  }
  async pump() {
    if (this.dead) return;
    try {
      let more = true;
      while (more) { const r = await api(`/api/runs/${this.id}/events?offset=${this.offset}`); r.events.forEach(e => this.ingest(e)); this.offset = r.offset; more = r.more; }
      this.status = await api(`/api/runs/${this.id}`);
      this.renderHeader(); this.renderPipeline(); this.appendLog();
      if (!this.running && !this.results) await this.loadResults();
    } catch (e) { toast(e.message, true); }
    if (this.running) this.timer = setTimeout(() => this.pump(), 1000);
    else if (!this.results) this.timer = setTimeout(() => this.pump(), 2500);
  }
  ingest(e) {
    this.events.push(e);
    if ((e.kind === "tool_call" || e.kind === "llm_call") && (e.attempt || 1) === 1) this.calls[e.agent] = (this.calls[e.agent] || 0) + 1;
    if (e.kind === "llm_call") { if (e.status === "failed") { this.llmFailed = (this.llmFailed || 0) + 1; this.llmError = e.error; } else if (e.status === "ok" && e.provider !== "offline-rules") this.llmOk = (this.llmOk || 0) + 1; }
    if (e.kind === "error") { this.errors++; if (AGENT_BY[e.agent]) this.agentErrors.add(e.agent); }
    if (e.kind === "message") {
      this.msgs++;
      if (e.agent === "orchestrator") {
        this.active = e.to; this.stageTask = e.task; this.stage = STAGE_OF[e.task] || e.task; this.stageChunk = e.payload && e.payload.chunk; this.touched.add(e.to);
        if (e.task === "assign_rooms") this.doneStages.add("chunks"); // every chunk was accepted before rooms are assigned
        if (e.task === "export") this.doneStages.add("validate_all");
      } else if (e.to === "orchestrator") {
        this.active = null; const p = e.payload || {};
        if (e.intent !== "error" && STAGE_OF[e.task] && !["chunks", "validate_all"].includes(STAGE_OF[e.task])) this.doneStages.add(STAGE_OF[e.task]);
        if (e.task === "plan_chunks" && typeof p.chunks === "number") this.chunksTotal = p.chunks;
        if (e.task === "validate_chunk" && p.hard_violations === 0) this.chunksDone++;
        if (e.task === "resolve") this.negotiations++;
        if (e.task === "repair" && typeof p.moved === "number") this.repairs += p.moved;
        if (e.task === "validate_all" && p.hard_violations === 0) this.doneStages.add("validate_all");
      }
    }
  }
  // Stage classes derived from real events: finished stages are "done", the stage the run was in is
  // "active" (running), "stopped" (cancelled / limit) or "failed" (crash); later stages stay neutral.
  stageState(key) {
    if (this.doneStages.has(key)) return "done";
    if (key !== this.stage) return "";
    const st = this.status.status;
    if (this.running) return "active";
    if (st === "failed" || st === "incomplete") return "failed";
    if (st === "stopped") return "stopped";
    return st === "ok" || st === "partial" ? "done" : "";
  }
  progress() {
    const done = STAGES.filter(s => this.stageState(s.key) === "done").length;
    let partial = 0;
    if (this.stage === "chunks" && this.chunksTotal && !this.doneStages.has("chunks")) partial = this.chunksDone / this.chunksTotal;
    else if (this.running && this.stage && !this.doneStages.has(this.stage)) partial = 0.3;
    return { done, f: Math.min(1, (done + partial) / STAGES.length) };
  }

  renderShell() {
    $("#main").innerHTML = `<div class="container page">
      <div id="hdr"></div>
      <div id="run-state"></div>
      <div id="run-kpi" hidden></div>
      <div class="card" id="run-pipe"><div id="flow"></div><div class="progress-row"><div class="bar" id="prog"><i style="width:0"></i></div><div class="small muted" id="prog-text"></div></div>
        <details id="agents-box" style="margin-top:14px"><summary class="hint" style="cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:6px">${I.users} Агенты и их нагрузка <span id="agents-sum"></span></summary><div id="pipe" style="margin-top:12px"></div></details></div>
      <div class="tabs" id="tabs" role="tablist"></div>
      <div id="tab-body"></div></div>`;
    this.renderTabs();
  }
  renderHeader() {
    const s = this.status, p = s.params || {};
    const canCancel = this.running && s.status === "running";
    const meta = [`<b>триместр</b> ${p.term ?? "?"}`, `<b>программы</b> ${p.programs && p.programs.length ? esc(p.programs.join(", ")) : "все"}`,
      `<b>LLM</b> ${esc((s.result && s.result.llm) || p.llm || "auto")}`, `<b>требований</b> ${(p.requirements || []).length}`,
      p.solver_seconds ? `<b>решатель</b> ${p.solver_seconds} с на кусок` : null, `<b>запущен</b> ${fmtDate(s.started)}`, `<b>длительность</b> ${fmtDur(s.elapsed_s)}`].filter(Boolean).map(x => `<span>${x}</span>`).join("");
    $("#hdr").innerHTML = pageHead(`${esc(this.id)} ${runStatusBadge(s, true)}`, "",
      `${canCancel ? `<button class="btn danger" id="cancel">${I.stop} Остановить</button>` : ""}
       ${this.has("timetable.xlsx") ? `<a class="btn" href="/api/runs/${esc(this.id)}/files/timetable.xlsx">${I.down} Excel</a>` : ""}
       ${!this.running ? `<button class="btn icon subtle" id="del" title="Удалить прогон" aria-label="Удалить прогон">${I.trash}</button>` : ""}
       <a class="btn primary" href="#/new">${I.plus} Новый прогон</a>`, [["Прогоны", "#/runs"], [esc(this.id)]], meta);
    const c = $("#cancel"); if (c) c.onclick = async () => { c.disabled = true; try { await post(`/api/runs/${this.id}/cancel`, {}); toast("Останавливаем после текущего шага"); } catch (e) { toast(e.message, true); } };
    const d = $("#del"); if (d) d.onclick = async () => { if (!confirmDelete(this.id)) return; try { await api(`/api/runs/${this.id}`, { method: "DELETE" }); toast("Прогон удалён"); location.hash = "#/runs"; } catch (e) { toast(e.message, true); } };
    this.renderState();
  }
  // The state card is the first thing on the page: what happened, in plain words, with the next action.
  renderState() {
    const s = this.status, res = s.result || {}, st = s.status;
    const { done } = this.progress();
    const stageName = STAGES.find(x => x.key === this.stage), where = stageName ? `на этапе «${stageName.label}»` : "";
    let cls = "", icon = I.info, title = "", text = "", actions = "";
    if (this.running) {
      cls = "running"; icon = '<span class="spinner big"></span>';
      title = st === "cancelling" ? "Останавливаем прогон" : "Агенты работают";
      text = st === "cancelling" ? "Текущий шаг доводится до конца, после этого прогон остановится." : `${stageName ? `Сейчас: ${stageName.label.toLowerCase()}` : "Подготовка"}${this.chunksTotal ? ` · куски ${this.chunksDone}/${this.chunksTotal}` : ""} · ${fmtDur(s.elapsed_s)}`;
      actions = `<button class="btn sm" data-go="live">Журнал ${I.arrow}</button>`;
    } else if (st === "ok") {
      cls = "ok"; icon = I.checkCircle; title = "Расписание построено"; text = `Все группы получили недельное расписание без жёстких нарушений за ${fmtDur(res.wall_s)}.`;
      actions = `<button class="btn primary sm" data-go="timetable">${I.cal} Открыть расписание</button>`;
    } else if (st === "partial") {
      cls = "warn"; icon = I.alertCircle; title = "Расписание построено частично";
      text = `Остались жёсткие нарушения: ${esc(Object.entries(res.hard_violations || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "см. проверку")}.${res.failed_chunks && res.failed_chunks.length ? ` Куски ${res.failed_chunks.join(", ")} не решены.` : ""}`;
      actions = `<button class="btn sm" data-go="conflicts">Конфликты ${I.arrow}</button><button class="btn sm" data-go="timetable">${I.cal} Расписание</button>`;
    } else if (st === "stopped" && this.cancelled) {
      cls = ""; icon = I.stopCircle; title = "Прогон остановлен пользователем";
      text = `Остановлен ${where} через ${fmtDur(res.wall_s)}: выполнено ${done} из ${STAGES.length} этапов, расписание не построено. Журнал агентов и созданные файлы доступны ниже.`;
      actions = `<button class="btn sm" data-go="live">Журнал ${I.arrow}</button><a class="btn primary sm" href="#/new">${I.play} Запустить заново</a>`;
    } else if (st === "stopped") {
      cls = "warn"; icon = I.alertCircle; title = "Прогон остановлен по лимиту"; text = `${esc(res.reason || "превышен лимит шагов или времени")}. Выполнено ${done} из ${STAGES.length} этапов.`;
      actions = `<button class="btn sm" data-go="live">Журнал ${I.arrow}</button>`;
    } else if (st === "failed") {
      cls = "bad"; icon = I.xCircle; title = "Прогон завершился с ошибкой"; text = esc(res.reason || "см. журнал агентов");
      actions = `<button class="btn sm" data-go="live">Журнал ${I.arrow}</button>`;
    } else {
      cls = "bad"; icon = I.xCircle; title = "Прогон прерван без результата"; text = "Процесс завершился до записи результата (например, сервер был остановлен). Доступен только журнал.";
      actions = `<button class="btn sm" data-go="live">Журнал ${I.arrow}</button>`;
    }
    $("#run-state").innerHTML = `<div class="state-card ${cls}" id="state"><div class="vi">${icon}</div><div><h2>${title}</h2><p>${text}</p></div><div class="actions">${actions}</div></div>`;
    $$("#run-state [data-go]").forEach(b => b.onclick = () => this.goto(b.dataset.go));
  }
  renderPipeline() {
    const st = this.status.status;
    $("#flow").innerHTML = flow(k => this.stageState(k), k => k === "chunks" && this.chunksTotal ? ` ${this.chunksDone}/${this.chunksTotal}` : "");
    const { done, f } = this.progress();
    let text;
    if (this.running) text = `${this.stageTask ? TASK_RU[this.stageTask] || this.stageTask : "подготовка"}${this.stageChunk != null && this.stage === "chunks" ? ` #${this.stageChunk}` : ""} · этап ${Math.min(done + 1, STAGES.length)} из ${STAGES.length}`;
    else if (st === "ok" || st === "partial") text = `${plural(this.status.result.steps ?? 0, "шаг", "шага", "шагов")} · ${plural(this.negotiations, "уступка", "уступки", "уступок")} · ${plural(this.repairs, "перенос", "переноса", "переносов")}`;
    else text = `выполнено ${done} из ${STAGES.length} этапов`;
    const bar = $("#prog"); bar.className = "bar " + (this.running ? "" : st === "ok" ? "ok" : st === "partial" ? "warn" : st === "failed" || st === "incomplete" ? "bad" : "neutral");
    bar.firstElementChild.style.width = `${Math.round(f * 100)}%`; $("#prog-text").textContent = text;
    const total = sum(Object.values(this.calls));
    $("#agents-sum").textContent = `· ${plural(total, "вызов", "вызова", "вызовов")}${this.active && this.running ? ` · сейчас: ${agentOf(this.active).ru}` : ""}`;
    $("#pipe").innerHTML = agentGrid(a => {
      const n = this.calls[a.name] || 0, isActive = this.running && this.active === a.name, msgs = a.name === "orchestrator" ? this.events.filter(e => e.kind === "message" && e.agent === "orchestrator").length : 0;
      return {
        cls: isActive ? "active" : this.agentErrors.has(a.name) ? "error" : "", off: !isActive && !n && !msgs && !this.touched.has(a.name),
        meta: isActive ? `${TASK_RU[this.stageTask] || this.stageTask}${this.stageChunk != null && this.stage === "chunks" ? ` #${this.stageChunk}` : ""}` : a.hint,
        right: a.name === "orchestrator" ? plural(msgs, "поручение", "поручения", "поручений") : n ? plural(n, "вызов", "вызова", "вызовов") : "—",
      };
    }, false);
    if (this.running) $("#agents-box").open = true;
  }
  renderTabs() {
    const avail = k => this.tt && this.tt.rows.length ? true : !["timetable", "quality"].includes(k);
    $("#tabs").innerHTML = TABS.map(([k, l]) => `<button role="tab" aria-selected="${this.tab === k}" class="${this.tab === k ? "on" : ""}" data-t="${k}" ${this.tt && !avail(k) ? 'title="недоступно: расписание не построено"' : ""}>${k === "live" && this.running ? "Ход выполнения" : l}${k === "conflicts" && this.negotiations ? `<span class="cnt">${this.negotiations}</span>` : ""}${k === "live" && this.running ? '<span class="cnt live pulse">●</span>' : ""}</button>`).join("");
    $$("#tabs button").forEach(b => b.onclick = () => { this.tab = b.dataset.t; this.renderTabs(); });
    this.renderTab();
  }
  renderTab() {
    const body = $("#tab-body"); this.syncHash();
    if (this.tab === "live") { body.innerHTML = this.logShell(); this.logEl = $("#log"); this.logRendered = 0; this.appendLog(true); return; }
    if (!this.results) {
      body.innerHTML = `<div class="card empty-state">${this.running ? `<span class="spinner big"></span><h3>Агенты работают</h3><p>Результаты появятся после завершения прогона. Пока можно следить за ходом выполнения.</p><div class="actions"><button class="btn" id="to-live">Открыть журнал</button></div>` : `<span class="spinner big"></span><h3>Загружаем результаты…</h3>`}</div>`;
      const b = $("#to-live"); if (b) b.onclick = () => this.goto("live"); return;
    }
    const noTT = !this.tt.rows.length;
    if (noTT && (this.tab === "timetable" || this.tab === "quality")) {
      body.innerHTML = `<div class="card empty-state">${I.cal}<h3>Расписание не построено</h3><p>${this.cancelled ? "Прогон был остановлен до экспорта расписания." : "Прогон завершился без расписания."} Посмотрите журнал агентов или запустите новый прогон.</p><div class="actions"><button class="btn" data-go="live">Журнал</button><a class="btn primary" href="#/new">${I.play} Новый прогон</a></div></div>`;
      $$("[data-go]", body).forEach(b => b.onclick = () => this.goto(b.dataset.go)); return;
    }
    ({ summary: () => this.renderSummary(body), timetable: () => this.renderTimetable(body), conflicts: () => this.renderConflicts(body), quality: () => this.renderQuality(body), data: () => this.renderData(body), report: () => this.renderReport(body) })[this.tab]();
  }

  // ---- log tab
  logShell() {
    return `<div class="card"><div class="log-toolbar">
      <div class="chips" id="log-agents" role="group" aria-label="Фильтр по агенту"></div>
      <div class="seg" id="log-kinds" role="group" aria-label="Тип события">${[["", "всё"], ["message", "сообщения"], ["tool_call", "инструменты"], ["llm_call", "LLM"], ["error", "ошибки"]].map(([k, l]) => `<button type="button" class="${this.logFilter.kind === k ? "on" : ""}" data-k="${k}">${l}</button>`).join("")}</div></div>
      <div class="log" id="log"></div>
      <div class="hint" style="margin-top:8px" id="log-foot"></div></div>`;
  }
  logFoot() {
    const f = $("#log-foot"); if (f) f.textContent = `${plural(this.events.length, "событие", "события", "событий")} · ${plural(sum(Object.values(this.calls)), "вызов", "вызова", "вызовов")} инструментов и LLM · ${plural(this.errors, "ошибка", "ошибки", "ошибок")} · нажмите на строку, чтобы раскрыть подробности · источник: events.jsonl`;
    const chips = $("#log-agents"); const seen = [...new Set(this.events.map(e => e.agent))];
    if (chips && chips.querySelectorAll(".chip").length !== seen.length + 1) {
      chips.innerHTML = `<button type="button" class="chip ${this.logFilter.agent ? "" : "on"}" data-a="">все агенты</button>` + seen.map(a => `<button type="button" class="chip ${this.logFilter.agent === a ? "on" : ""}" data-a="${esc(a)}"><span class="agent-dot" style="--c:${agentOf(a).color}"></span>${esc(agentOf(a).ru)}</button>`).join("");
      this.bindLogFilters();
    }
  }
  appendLog(full = false) {
    if (this.tab !== "live" || !this.logEl) return;
    if (full) { this.logEl.innerHTML = ""; this.logRendered = 0; this.bindLogFilters(); }
    const fresh = this.events.slice(this.logRendered); const base = this.logRendered; this.logRendered = this.events.length;
    this.logFoot();
    const t0 = this.events.length ? this.events[0].ts : 0;
    const keep = fresh.map((e, i) => [e, base + i]).filter(([e]) => (!this.logFilter.agent || e.agent === this.logFilter.agent) && (!this.logFilter.kind || e.kind === this.logFilter.kind));
    if (!keep.length) { if (full && !this.logEl.children.length) this.logEl.innerHTML = `<div class="empty-state small">${this.running ? '<span class="spinner"></span><p>событий пока нет</p>' : "<p>нет событий, подходящих под фильтр</p>"}</div>`; return; }
    const atBottom = this.logEl.scrollHeight - this.logEl.scrollTop - this.logEl.clientHeight < 40;
    if (this.logEl.querySelector(".empty-state")) this.logEl.innerHTML = "";
    this.logEl.insertAdjacentHTML("beforeend", keep.map(([e, i]) => this.logLine(e, i, t0)).join(""));
    while (this.logEl.children.length > 1500) this.logEl.firstElementChild.remove();
    if (atBottom || full) this.logEl.scrollTop = this.logEl.scrollHeight;
  }
  bindLogFilters() {
    $$("#log-agents .chip").forEach(c => c.onclick = () => { this.logFilter.agent = c.dataset.a; this.renderTab(); });
    $$("#log-kinds button").forEach(b => b.onclick = () => { this.logFilter.kind = b.dataset.k; this.renderTab(); });
    const log = this.logEl; if (log && !log.dataset.bound) {
      log.dataset.bound = "1";
      log.addEventListener("click", ev => { const ln = ev.target.closest(".ln"); if (!ln) return; const open = ln.classList.toggle("open"); const pre = ln.querySelector("pre"); if (open && pre && !pre.textContent) pre.textContent = JSON.stringify(this.events[+ln.dataset.i], null, 2); });
      log.addEventListener("keydown", ev => { if ((ev.key === "Enter" || ev.key === " ") && ev.target.classList.contains("ln")) { ev.preventDefault(); ev.target.click(); } });
    }
  }
  logLine(e, idx, t0) {
    const t = `+${(e.ts - t0).toFixed(1)}s`;
    let text = "", kind = "";
    if (e.kind === "message") {
      kind = `${I.msg} сообщение`;
      if (e.agent === "orchestrator") text = `→ <b>${esc(agentOf(e.to).ru)}</b> · ${esc(TASK_RU[e.task] || e.task)}${e.payload && Object.keys(e.payload).length ? ` <span class="muted">${esc(JSON.stringify(e.payload)).slice(0, 140)}</span>` : ""}`;
      else text = `← оркестратору · ${esc(TASK_RU[e.task] || e.task)} <span class="st-${e.intent === "error" ? "error" : "ok"}">${e.intent === "error" ? "ошибка" : "результат"}</span> <span class="muted">${esc(brief(e.payload))}</span>`;
    } else if (e.kind === "tool_call") { kind = `${I.tool} инструмент`; text = `${esc(e.name)} <span class="st-${e.status}">${e.status}</span>${e.attempt > 1 ? ` (попытка ${e.attempt})` : ""}${e.duration_ms != null ? ` <span class="muted">${e.duration_ms} мс</span>` : ""}${e.error ? ` <span class="st-error">${esc(e.error)}</span>` : ""}`; }
    else if (e.kind === "llm_call") { kind = `${I.brain} LLM`; text = `${esc(e.name)} <span class="st-${e.status}">${e.status}</span> <span class="muted">${esc(e.provider)}${e.tokens_in ? ` · ${e.tokens_in}→${e.tokens_out} ток.` : ""}${e.duration_ms != null ? ` · ${e.duration_ms} мс` : ""}</span>${e.error ? ` <span class="st-error">${esc(e.error)}</span>` : ""}`; }
    else { kind = `${e.kind === "error" ? I.alertCircle : I.info} ${esc(e.kind)}`; text = esc(e.msg || JSON.stringify(e)); }
    return `<div class="ln ${e.kind}" data-i="${idx}" tabindex="0"><span class="ts">${t}</span><span class="agent">${agentTag(e.agent)}</span><span><span class="kind">${kind}</span>${text}</span>${I.chev.replace("<svg", '<svg class="chev"')}<div class="det"><pre></pre></div></div>`;
  }

  // ---- results
  async loadResults() {
    const [tt, details, report] = await Promise.all([api(`/api/runs/${this.id}/timetable`), api(`/api/runs/${this.id}/details`), this.has("report.md") ? api(`/api/runs/${this.id}/report`).catch(() => "") : ""]);
    this.tt = tt; this.details = details; this.report = report; this.results = this.status.result || {};
    this.index = indexRows(tt.rows);
    if (!this.ttSel) this.ttSel = tt.groups[0] || "";
    if (this.tab === "live" && tt.rows.length) this.tab = "summary";
    const k = $("#run-kpi"); if (tt.rows.length) { k.hidden = false; k.innerHTML = `<div class="kpi-row">${this.kpis()}</div>`; }
    this.renderState(); this.renderTabs();
  }
  kpis() {
    const res = this.results, soft = res.soft || {}; const hv = sum(Object.values(res.hard_violations || {}));
    return kpi(hv, "жёстких нарушений", hv ? "bad" : "ok") + kpi(num(soft.gap_hours_per_group), "окон, ч на группу", "", `всего ${num(soft.gap_hours_total)} ч в неделю`) + kpi(num(soft.late_hours_18_plus), "часов после 18:00") + kpi(num(soft.saturday_hours), "часов в субботу") + kpi(num(soft.precedence_violations), "практика раньше лекции") + kpi(num(soft.lectures_in_lecture_halls), "лекций в лекц. залах");
  }
  goto(tab) { this.tab = tab; this.renderTabs(); const el = $("#tabs"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }

  renderSummary(body) {
    const res = this.results, d = this.details, s = this.status, val = d.validation || {};
    const rp = d.run_params || {}, warn = d.constraint_warnings || [], issuesW = (d.data_issues || []).filter(i => i.level === "warning").length;
    const noTT = !this.tt.rows.length;
    body.innerHTML = `
      <div class="grid c3" style="margin-top:16px">
        <div class="card"><div class="card-head"><h2>Что сделали агенты</h2><button class="btn sm ghost" data-go="live">Журнал ${I.arrow}</button></div>
          <ul class="facts-list">
            <li><b>${this.msgs}</b> сообщений за ${plural(res.steps ?? 0, "шаг", "шага", "шагов")} оркестратора</li>
            <li><b>${sum(Object.values(this.calls))}</b> вызовов инструментов и LLM, макс. доля одного агента <b>${pct((res.load || {}).max_share)}</b></li>
            <li><b>${this.chunksTotal ?? "—"}</b> кусков задачи: первое поколение ГА → доработка CP-SAT, всего <b>${fmtDur(res.wall_s)}</b></li>
            <li><b>${this.negotiations}</b> уступок Negotiator${(rp.relaxed || []).length ? `, ослаблено правил: <b>${rp.relaxed.length}</b>` : ""}</li>
            <li><b>${(d.repairs || []).length}</b> локальных переносов после назначения аудиторий</li>
            <li>LLM: <b>${esc(res.llm || s.params.llm || "—")}</b>${this.llmOk ? ` · <span style="color:var(--ok)">${plural(this.llmOk, "успешный вызов", "успешных вызова", "успешных вызовов")}</span>` : ""}${this.llmFailed ? ` · <span style="color:var(--warn)">${plural(this.llmFailed, "сбой", "сбоя", "сбоев")}, ответы заменены офлайн-правилами</span><div class="hint" style="margin-top:4px">${esc(this.llmError || "")}</div>` : ""}</li></ul>
          ${noTT ? "" : `<button class="btn sm" data-go="conflicts" style="margin-top:10px">Конфликты и переговоры ${I.arrow}</button>`}</div>
        <div class="card"><div class="card-head"><h2>Рецензия критика</h2>${agentTag("validator")}</div>${d.critique ? `<div class="markdown">${markdown(d.critique)}</div>` : `<div class="empty-state small"><p>Критик не успел дать рецензию: ${this.cancelled ? "прогон остановлен раньше итоговой проверки" : "итоговая проверка не выполнялась"}.</p></div>`}</div>
        <div class="card"><div class="card-head"><h2>Требует внимания</h2></div>
          ${!warn.length && !issuesW && !(rp.relaxed || []).length && !(val.violations || []).length ? (s.status === "ok" || s.status === "partial" ? `<div class="empty-state small">${I.checkCircle}<p>Замечаний нет: все требования поняты, данные в порядке.</p></div>` : `<div class="empty-state small"><p>Проверки не проводились: прогон завершился раньше.</p></div>`) : `<ul class="nice">
            ${(rp.relaxed || []).map(r => `<li><span class="badge warn">ослаблено</span> ${esc(r)}</li>`).join("")}
            ${warn.map(w => `<li><span class="badge warn">требование</span> ${esc(w)}</li>`).join("")}
            ${issuesW ? `<li><span class="badge warn">данные</span> ${plural(issuesW, "замечание", "замечания", "замечаний")} к входным данным требуют ручной правки</li>` : ""}
            ${(val.violations || []).slice(0, 5).map(v => `<li><span class="badge bad">${esc(v.type)}</span> ${esc(v.detail)}</li>`).join("")}</ul>`}
          <button class="btn sm ghost" data-go="data" style="margin-top:10px">Данные и ограничения ${I.arrow}</button></div></div>`;
    $$("[data-go]", body).forEach(b => b.onclick = () => this.goto(b.dataset.go));
  }

  renderTimetable(body) {
    const tt = this.tt;
    const opts = this.ttMode === "group" ? tt.groups : this.ttMode === "room" ? tt.rooms : tt.programs;
    if (!opts.includes(this.ttSel)) this.ttSel = opts[0] || "";
    const what = this.ttMode === "group" ? "Группа" : this.ttMode === "room" ? "Аудитория" : "Программа";
    body.innerHTML = `<div class="card" style="margin-top:16px">
      <div class="tt-controls">
        <div class="seg" id="tt-mode" role="group" aria-label="Режим">${[["group", "По группе"], ["room", "По аудитории"], ["heat", "Карта программы"]].map(([k, l]) => `<button type="button" class="${this.ttMode === k ? "on" : ""}" data-m="${k}">${l}</button>`).join("")}</div>
        <div class="field combo" id="combo"><label for="sel">${what}</label><input type="text" id="sel" value="${esc(this.ttSel)}" autocomplete="off"><div class="list" id="sel-list"></div></div>
        <div class="actions"><button class="btn sm icon" id="prev" title="предыдущая" aria-label="предыдущая">←</button><button class="btn sm icon" id="next" title="следующая" aria-label="следующая">→</button><button class="btn sm subtle" id="print">${I.print} Печать</button></div>
        <div class="legend" style="margin-left:auto">${this.ttMode === "heat" ? `<span><i class="h-lecture"></i>лекция</span><span><i class="h-practice"></i>практика</span><span><i class="h-lab"></i>лаб.</span><span><i class="h-online"></i>онлайн</span><span><i class="h-gap"></i>окно</span>` : `<span><i class="lecture"></i>лекция</span><span><i class="practice"></i>практика</span><span><i class="lab"></i>лаборатория</span><span><i class="online"></i>онлайн</span>`}</div>
      </div>
      <div id="tt-view" class="print-area"></div></div>`;
    $$("#tt-mode button").forEach(b => b.onclick = () => { this.ttMode = b.dataset.m; this.ttSel = ""; this.renderTimetable(body); });
    const input = $("#sel"), list = $("#sel-list"), combo = $("#combo");
    const fill = q => { const items = opts.filter(o => o.toLowerCase().includes(q.toLowerCase())).slice(0, 60); list.innerHTML = items.map(o => `<div data-v="${esc(o)}" class="${o === this.ttSel ? "hl" : ""}">${esc(o)}</div>`).join("") || '<div class="muted">нет совпадений</div>'; $$("div[data-v]", list).forEach(d => d.onmousedown = () => { this.ttSel = d.dataset.v; this.renderTimetable(body); }); };
    input.onfocus = () => { combo.classList.add("open"); fill(""); input.select(); }; input.onblur = () => setTimeout(() => combo.classList.remove("open"), 120);
    input.oninput = () => fill(input.value); input.onkeydown = e => { if (e.key === "Enter") { const m = opts.find(o => o.toLowerCase() === input.value.toLowerCase()) || opts.find(o => o.toLowerCase().includes(input.value.toLowerCase())); if (m) { this.ttSel = m; this.renderTimetable(body); } } };
    const step = d => { const i = opts.indexOf(this.ttSel); this.ttSel = opts[(i + d + opts.length) % opts.length]; this.renderTimetable(body); };
    $("#prev").onclick = () => step(-1); $("#next").onclick = () => step(1); $("#print").onclick = () => window.print();
    const view = $("#tt-view");
    view.innerHTML = `<div class="print-title">${what} ${esc(this.ttSel)} · триместр ${(this.status.params || {}).term ?? ""} · ${esc(this.id)}</div>` +
      (this.ttMode === "heat" ? this.heatmap(this.ttSel) : this.weekGrid(this.ttMode, this.ttSel));
    this.syncHash();
  }
  weekGrid(mode, sel) { return weekGridHtml(this.tt, this.index, mode, sel); }
  heatmap(prog) {
    const tt = this.tt, H = 12, useSat = tt.rows.some(r => r.day === 5), days = useSat ? 6 : 5;
    const groups = tt.groups.filter(g => (this.index.progOf[g] || "") === prog);
    let html = `<div class="heat"><table><thead><tr><th></th>${Array.from({ length: days }, (_, d) => `<th colspan="${H}" class="${d ? "day-sep" : ""}">${DAY_SHORT[d]}</th>`).join("")}</tr></thead><tbody>`;
    let totalGaps = 0;
    for (const g of groups) {
      const rows = this.index.byGroup[g] || []; const occ = {};
      rows.forEach(r => occ[r.day * H + r.hour] = r.room === "online" ? "online" : r.kind);
      html += `<tr><td class="g">${esc(g)}</td>`;
      for (let d = 0; d < days; d++) {
        const hrs = rows.filter(r => r.day === d).map(r => r.hour); const lo = Math.min(...hrs), hi = Math.max(...hrs);
        for (let h = 0; h < H; h++) {
          const k = occ[d * H + h]; const gap = !k && hrs.length && h > lo && h < hi; if (gap) totalGaps++;
          html += `<td title="${esc(g)} · ${DAY_SHORT[d]} ${esc(tt.times[h])}"><div class="sq ${k || (gap ? "gap" : "")} ${h === 0 && d ? "day-sep" : ""}"></div></td>`;
        }
      }
      html += "</tr>";
    }
    html += "</tbody></table></div>";
    html += `<div class="hint" style="margin-top:10px">${plural(groups.length, "группа", "группы", "групп")} · окон всего: <b>${totalGaps}</b> ч (${groups.length ? (totalGaps / groups.length).toFixed(1) : 0} ч на группу)</div>`;
    return html;
  }

  renderConflicts(body) {
    const d = this.details, conv = d.conversation || [], chunks = d.chunks || [];
    const byChunk = {};
    for (const c of conv) { const idx = c.request && c.request.chunk != null ? c.request.chunk : null; if (idx == null) continue; (byChunk[idx] = byChunk[idx] || []).push(c); }
    const stepHtml = c => {
      const a = agentOf(c.agent); const r = c.reply || {};
      let text = "", quote = "";
      if (c.task === "allocate_quota") text = `<b>${a.ru}</b> выдал квоту аудиторий (${r.mode === "all" ? "весь остаток фонда" : "справедливая доля"})`;
      else if (c.task === "evolve") { const st = r.stats || {}; text = `<b>${a.ru}</b> · первое поколение: <span class="badge ${r.hard === 0 ? "ok" : "warn"}">${st.generations ?? "?"} поколений, конфликтов ${r.hard ?? "?"}</span> за ${st.wall_s ?? "?"} с`; if (st.first_fitness != null) quote = `фитнес ${st.first_fitness} → ${st.best_fitness} (жёстких ${st.first_hard} → ${st.best_hard}); лучшая особь передана CP-SAT как подсказка`; }
      else if (c.task === "solve_chunk") { text = `<b>${a.ru}</b> · CP-SAT: <span class="badge ${r.ok ? "ok" : "bad"}">${esc(r.status)}</span> за ${r.wall_s ?? "?"} с`; if (r.diagnostics && r.diagnostics.length) quote = r.diagnostics.slice(0, 4).map(esc).join("<br>"); }
      else if (c.task === "validate_chunk") { text = `<b>${a.ru}</b> проверил кусок: <span class="badge ${r.hard_violations === 0 ? "ok" : "bad"}">${r.hard_violations === 0 ? "нарушений нет, решение принято" : `${r.hard_violations} нарушений`}</span>`; if (r.hard_violations) quote = Object.entries(r.by_type || {}).map(([k, v]) => `${esc(k)}: ${v}`).join(", "); }
      else if (c.task === "resolve") { text = `<b>${a.ru}</b> выбрал уступку: <span class="badge warn">${esc(ACTION_RU[r.action] || r.action)}</span>`; quote = esc(r.rationale || ""); }
      else if (c.intent === "error") text = `<b>${esc(a.ru)}</b> · ошибка: ${esc(r.error)}`;
      return `<div class="step" style="--c:${a.color}"><div class="i">${a.short}</div><div class="t">${text}${quote ? `<div class="quote">${quote}</div>` : ""}</div></div>`;
    };
    const chunkCards = Object.keys(byChunk).sort((a, b) => a - b).map(i => {
      const steps = byChunk[i]; const neg = steps.filter(s => s.task === "resolve").length; const ok = steps.some(s => s.task === "validate_chunk" && s.reply.hard_violations === 0);
      return `<div class="chunk-card"><header><div><b>Кусок #${i}</b> <span class="muted small">${esc((chunks[i] || []).join(", "))}</span></div><div class="actions">${neg ? `<span class="badge warn">${plural(neg, "раунд", "раунда", "раундов")} переговоров</span>` : '<span class="badge ok">с первой попытки</span>'}${ok ? `<span class="badge ok">${I.check}принят</span>` : '<span class="badge bad">не решён</span>'}</div></header><div class="steps">${steps.map(stepHtml).join("")}</div></div>`;
    }).join("");
    const finals = conv.filter(c => c.task === "validate_all" || c.task === "repair");
    const val = d.validation || {}; const rp = d.run_params || {};
    body.innerHTML = `
      <div class="kpi-row c4" style="margin:16px 0">
        ${kpi(val.hard_violations ?? "—", "жёстких нарушений в итоге", val.hard_violations == null ? "" : sum(Object.values(val.by_type || {})) ? "bad" : "ok")}
        ${kpi(conv.filter(c => c.task === "resolve").length, "уступок Negotiator", "", `${plural(Object.keys(rp.tried || {}).length, "кусок", "куска", "кусков")} потребовали переговоров`)}
        ${kpi((d.repairs || []).length, "локальных переносов", "", "после назначения аудиторий")}
        ${kpi((rp.relaxed || []).length, "ослабленных правил", (rp.relaxed || []).length ? "warn" : "")}</div>
      <div class="card" style="margin-bottom:16px"><div class="card-head"><h2>Переговоры по кускам задачи</h2><span class="hint">Каждый кусок (несколько программ-курсов) решается отдельно внутри квоты аудиторий. Если решатель не справился или критик нашёл нарушения, Negotiator выбирает уступку и кусок решается заново.</span></div>
        <div class="grid c2">${chunkCards || `<div class="empty-state small" style="grid-column:1/-1"><p>${this.cancelled ? "Прогон остановлен до решения кусков." : "Нет данных о кусках."}</p></div>`}</div></div>
      <div class="grid c2" style="margin-bottom:16px">
        <div class="card"><div class="card-head"><h2>Финальная проверка и ремонт</h2></div><div class="steps">${finals.map(c => { const r = c.reply || {}; const a = agentOf(c.agent); return `<div class="step" style="--c:${a.color}"><div class="i">${a.short}</div><div class="t">${c.task === "validate_all" ? `<b>${a.ru}</b> · итог${c.request.final ? " (финал)" : ""}: <span class="badge ${r.hard_violations ? "bad" : "ok"}">${r.hard_violations} нарушений</span> ${r.by_type && Object.keys(r.by_type).length ? `<span class="muted small">${esc(JSON.stringify(r.by_type))}</span>` : ""}` : `<b>${a.ru}</b> · ремонт: перенесено ${r.moved}, не удалось ${(r.failed || []).length}`}</div></div>`; }).join("") || '<div class="muted small">итоговая проверка не выполнялась</div>'}</div>
          ${(d.repairs || []).length ? `<h3 class="h-sm" style="margin-top:14px">Переносы</h3><div style="max-height:260px;overflow:auto">${(d.repairs || []).slice(0, 200).map(m => `<div class="violation"><span class="mono">${slotLabel(m.from)} → ${slotLabel(m.to)}</span><span>${esc(m.session)} ${m.room ? `<span class="muted">· ${esc(m.room)}</span>` : ""}</span></div>`).join("")}</div>` : ""}</div>
        <div class="card"><div class="card-head"><h2>Ослабленные правила и остаточные нарушения</h2></div>
          ${(rp.relaxed || []).length ? `<div class="hint">Negotiator перевёл эти жёсткие требования в мягкие (штраф вместо запрета):</div><ul class="nice">${rp.relaxed.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : '<div class="hint">Пользовательские правила не ослаблялись.</div>'}
          ${(rp.saturday_chunks || []).length ? `<div class="small" style="margin-top:6px">Суббота открыта для кусков: ${rp.saturday_chunks.join(", ")}</div>` : ""}
          ${(val.violations || []).length ? `<h3 class="h-sm" style="margin-top:14px">Нарушения в итоговом расписании</h3><div style="max-height:300px;overflow:auto">${val.violations.map(v => `<div class="violation"><span class="badge bad">${esc(v.type)}</span><span>${esc(v.detail)}</span></div>`).join("")}</div>` : val.hard_violations === 0 ? `<div style="margin-top:14px"><span class="badge ok">${I.check} Итоговое расписание без жёстких нарушений</span></div>` : ""}</div></div>
      ${d.critique ? `<div class="card"><div class="card-head"><h2>Рецензия критика</h2>${agentTag("validator")}</div><div class="markdown">${markdown(d.critique)}</div></div>` : ""}`;
  }

  renderQuality(body) {
    const res = this.results, load = (res.load || {}).agents || {}, tt = this.tt;
    const dayHours = Array(6).fill(0), hourHist = Array(12).fill(0);
    tt.rows.forEach(r => { dayHours[r.day]++; hourHist[r.hour]++; });
    const roomUse = {}; tt.rows.forEach(r => { if (r.room !== "online" && r.room !== "UNASSIGNED") roomUse[`${r.room}|${r.day}|${r.hour}`] = 1; });
    const roomHours = {}; Object.keys(roomUse).forEach(k => { const room = k.split("|")[0]; roomHours[room] = (roomHours[room] || 0) + 1; });
    const topRooms = Object.entries(roomHours).sort((a, b) => b[1] - a[1]).slice(0, 10);
    const progGaps = {}; for (const [g, rows] of Object.entries(this.index.byGroup)) { const p = this.index.progOf[g]; (progGaps[p] = progGaps[p] || []).push(gapsOf(rows)); }
    const progRows = Object.entries(progGaps).map(([p, arr]) => [p, arr.length, (sum(arr) / arr.length).toFixed(1)]).sort((a, b) => b[2] - a[2]);
    const maxDay = Math.max(...dayHours, 1), maxHour = Math.max(...hourHist, 1);
    body.innerHTML = `
      <div class="grid c3" style="margin-top:16px">
        <div class="card"><div class="card-head"><h2>Нагрузка агентов</h2><span class="hint">Доля вызовов инструментов и LLM. Требование ТЗ: ни один агент не делает больше 40 %.</span></div>
          <div class="hbars">${Object.entries(load).map(([a, v]) => `<div class="row"><span>${agentTag(a)}</span><div class="bar ${v.share > 0.4 ? "bad" : ""}"><i style="width:${v.share * 100}%;${v.share > 0.4 ? "" : `background:${agentOf(a).color}`}"></i></div><span class="v">${v.calls} · ${pct(v.share)}</span></div>`).join("")}</div>
          <div class="hint" style="margin-top:10px">макс. доля <b>${pct((res.load || {}).max_share)}</b> · всего вызовов ${(res.load || {}).total_calls ?? "—"} · шагов оркестратора ${res.steps ?? "—"} · ${fmtDur(res.wall_s)}</div></div>
        <div class="card"><div class="card-head"><h2>Часы по дням</h2></div><div class="hbars">${dayHours.slice(0, 6).map((n, d) => n || d < 5 ? `<div class="row"><span>${DAY_RU[d]}</span><div class="bar"><i style="width:${n / maxDay * 100}%"></i></div><span class="v">${n}</span></div>` : "").join("")}</div>
          <h3 class="h-sm" style="margin:16px 0 8px">Часы по времени дня</h3><div class="hbars">${hourHist.map((n, h) => `<div class="row"><span class="small">${esc(tt.times[h])}</span><div class="bar ${h >= 10 ? "warn" : ""}"><i style="width:${n / maxHour * 100}%"></i></div><span class="v">${n}</span></div>`).join("")}</div></div>
        <div class="card"><div class="card-head"><h2>Окна по программам</h2></div><table class="tbl"><tr><th>Программа</th><th class="num">Групп</th><th class="num">Окон, ч/группу</th></tr>${progRows.map(r => `<tr><td>${esc(r[0])}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td></tr>`).join("")}</table>
          <h3 class="h-sm" style="margin:16px 0 8px">Самые загруженные аудитории</h3><table class="tbl">${topRooms.map(([r, n]) => `<tr><td>${esc(r)}</td><td class="num">${n} ч · ${pct(n / 60)}</td></tr>`).join("")}</table></div></div>`;
  }

  renderData(body) {
    const d = this.details, p = this.status.params || {}, ds = d.demand_summary || {}, specs = d.constraint_specs || [], warn = d.constraint_warnings || [];
    const reqs = p.requirements || [], relaxed = (d.run_params || {}).relaxed || [];
    body.innerHTML = `
      <div class="grid c2" style="margin:16px 0">
        <div class="card"><div class="card-head"><h2>Требования → JSON-правила</h2>${agentTag("constraints")}</div>
          ${reqs.length ? `<ul class="nice">${reqs.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : '<div class="hint">Требований не задано — действуют только встроенные правила вуза.</div>'}
          ${specs.length ? `<div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th>Тип</th><th>Жёсткое</th><th>Как понято</th><th>Источник</th></tr></thead><tbody>${specs.map(s => `<tr><td><code class="key">${esc(s.kind)}</code></td><td>${s.hard ? "да" : `нет, вес ${s.weight}`}${relaxed.includes(s.source) ? ' <span class="badge warn">ослаблено</span>' : ""}</td><td>${esc(describeSpec(s))}</td><td class="small muted">${esc(s.source)}</td></tr>`).join("")}</tbody></table></div>` : reqs.length && !specs.length ? '<div class="hint" style="margin-top:8px">Правила ещё не разобраны: прогон остановлен до этапа «Правила».</div>' : ""}
          ${warn.length ? `<div style="margin-top:10px">${warn.map(w => `<div class="callout warn" style="margin-top:6px">${I.alertCircle}<div>${esc(w)}</div></div>`).join("")}</div>` : ""}</div>
        <div class="card"><div class="card-head"><h2>Качество входных данных</h2>${agentTag("data")}</div>${d.data_issues_text ? `<div class="markdown" style="margin-bottom:10px">${markdown(d.data_issues_text)}</div>` : '<div class="hint">Данные ещё не загружались.</div>'}
          ${(d.data_issues || []).length ? `<div class="tbl-wrap" style="max-height:320px;overflow:auto"><table class="tbl"><thead><tr><th>Уровень</th><th>Где</th><th>Что</th></tr></thead><tbody>${d.data_issues.map(i => `<tr><td><span class="badge ${i.level === "fixed" ? "ok" : "warn"}">${i.level === "fixed" ? "исправлено" : "внимание"}</span></td><td class="small">${esc(i.where)}</td><td class="small">${esc(i.issue)}</td></tr>`).join("")}</tbody></table></div>` : ""}</div></div>
      <div class="card"><div class="card-head"><h2>Спрос: единицы декомпозиции (программа-курс)</h2>${agentTag("scheduler")}<span class="hint">Источник: ${esc(p.dataset_path || "")}</span></div>
        ${Object.keys(ds).length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Единица</th><th class="num">Групп</th><th class="num">Триместр</th><th>Курсы</th><th class="num">Кусок</th></tr></thead><tbody>${Object.entries(ds).map(([u, v]) => `<tr><td><b>${esc(u)}</b></td><td class="num">${v.groups}</td><td class="num">${v.trimester}</td><td class="small">${v.courses.map(esc).join(" · ")}</td><td class="num">#${(d.chunks || []).findIndex(c => c.includes(u))}</td></tr>`).join("")}</tbody></table></div>` : '<div class="hint">Спрос не рассчитан.</div>'}</div>`;
  }

  renderReport(body) {
    body.innerHTML = `<div class="two-col" style="margin-top:16px"><div class="card"><div class="card-head"><h2>report.md</h2>${agentTag("reporter")}</div>${this.report ? `<div class="markdown">${markdown(this.report)}</div>` : `<div class="empty-state small"><p>Отчёт не написан: ${this.cancelled ? "прогон остановлен до этапа «Экспорт»" : "прогон завершился до экспорта"}.</p></div>`}</div>
      <div class="card"><div class="card-head"><h2>Файлы прогона</h2><span class="hint">${plural(this.files.length, "файл", "файла", "файлов")} в runs/${esc(this.id)}/</span></div><div class="file-list">${FILES.map(([f, d]) => { const ok = this.has(f); return `<a class="${ok ? "" : "missing"}" ${ok ? `href="/api/runs/${esc(this.id)}/files/${f}"` : 'aria-disabled="true" title="файл не создан"'}>${I.file}<span><b>${f}</b><br><span class="small muted">${ok ? d : "не создан"}</span></span>${I.down}</a>`; }).join("")}</div></div></div>`;
  }
}

// ------------------------------------------------------------------ data helpers
function indexRows(rows) {
  const byGroup = {}, byRoom = {}, progOf = {};
  for (const r of rows) {
    (byGroup[r.group] = byGroup[r.group] || []).push(r); progOf[r.group] = r.program;
    if (r.room !== "online" && r.room !== "UNASSIGNED") (byRoom[r.room] = byRoom[r.room] || []).push(r);
  }
  return { byGroup, byRoom, progOf };
}
// Week grid of one group or room; `changed` = Set of "day|hour" keys to outline (what moved vs the previous version).
function weekGridHtml(tt, index, mode, sel, changed = null) {
  const H = 12;
  const rows = mode === "group" ? (index.byGroup[sel] || []) : (index.byRoom[sel] || []);
  const useSat = tt.rows.some(r => r.day === 5); const days = useSat ? 6 : 5;
  const cell = Array.from({ length: days }, () => Array(H).fill(null));
  for (const r of rows) {
    if (r.day >= days) continue;
    const c = cell[r.day][r.hour];
    if (c && c.course === r.course && c.kind === r.kind) { if (!c.groups.includes(r.group)) c.groups.push(r.group); continue; }
    if (c) { c.clash = true; continue; }
    cell[r.day][r.hour] = { course: r.course, kind: r.kind, room: r.room, groups: [r.group], online: r.room === "online", stream: r.stream };
  }
  const same = (a, b) => a && b && a.course === b.course && a.kind === b.kind && a.room === b.room && a.groups.join() === b.groups.join();
  const skip = Array.from({ length: days }, () => Array(H).fill(false));
  let html = `<div class="tt-wrap"><table class="tt"><thead><tr><th></th>${Array.from({ length: days }, (_, d) => `<th>${DAY_RU[d]}</th>`).join("")}</tr></thead><tbody>`;
  for (let h = 0; h < H; h++) {
    html += `<tr><td class="time">${esc(tt.times[h])}</td>`;
    for (let d = 0; d < days; d++) {
      if (skip[d][h]) continue;
      const c = cell[d][h];
      if (!c) { html += `<td class="${h === 5 ? "lunch" : ""}"></td>`; continue; }
      let span = 1; while (h + span < H && same(c, cell[d][h + span]) && !cell[d][h + span].clash) { skip[d][h + span] = true; span++; }
      const cls = c.online ? "online" : c.kind;
      const who = mode === "group" ? (c.online ? "онлайн" : esc(c.room)) : esc(c.groups.join(", "));
      html += `<td rowspan="${span}"><div class="cell ${cls} ${c.clash ? "clash" : ""} ${changed && changed.has(`${d}|${h}`) ? "changed" : ""}" title="${esc(c.course)}" data-d="${d}" data-h="${h}"><b>${esc(c.course)}</b><div class="r"><span>${who}</span><span>${KIND_RU[c.kind] || c.kind}${c.stream > 1 && mode === "group" ? ` · поток ${c.stream}` : ""}</span></div></div></td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table></div>";
  const hours = rows.length, byDay = new Set(rows.map(r => r.day)).size;
  const gaps = mode === "group" ? gapsOf(rows) : null;
  html += `<div class="hint grid-note">${plural(hours, "академический час", "академических часа", "академических часов")} в неделю · ${plural(byDay, "учебный день", "учебных дня", "учебных дней")}${gaps != null ? ` · окон: <b>${gaps}</b> ч` : ""}${mode === "room" ? ` · загрузка ${pct(hours / (days * H))}` : ""}</div>`;
  return html;
}
function gapsOf(rows) {
  const byDay = {}; rows.forEach(r => (byDay[r.day] = byDay[r.day] || new Set()).add(r.hour));
  let g = 0; for (const hs of Object.values(byDay)) { const a = [...hs]; g += (Math.max(...a) - Math.min(...a) + 1) - a.length; } return g;
}
function slotLabel(t) { if (t == null) return "—"; const d = Math.floor(t / 12), h = t % 12; return `${DAY_SHORT[d] || d} ${String(8 + h).padStart(2, "0")}:00`; }
function brief(p) {
  if (!p) return ""; if (p.error) return `ОШИБКА: ${p.error}`;
  const keys = ["status", "action", "hard_violations", "chunks", "sessions", "constraints", "mode", "moved", "assigned", "wall_s", "rationale"];
  return keys.filter(k => k in p).map(k => `${k}=${typeof p[k] === "object" ? JSON.stringify(p[k]) : p[k]}`).join(", ").slice(0, 220);
}

route();
