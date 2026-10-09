/* Timetable Agents - single-page UI (vanilla JS, no build step).
   Routes: #/ overview, #/new start a run, #/runs history, #/run/<id>/<tab> live + results, #/agents spec. */
"use strict";

// ------------------------------------------------------------------ constants
const AGENTS = [
  { name: "orchestrator", label: "Orchestrator", ru: "Оркестратор", short: "OR", color: "var(--a-orchestrator)", hint: "план и маршрутизация" },
  { name: "data", label: "Data", ru: "Данные", short: "DA", color: "var(--a-data)", hint: "Excel → спрос" },
  { name: "constraints", label: "Constraints", ru: "Ограничения", short: "CO", color: "var(--a-constraints)", hint: "текст → правила" },
  { name: "scheduler", label: "Scheduler", ru: "Планировщик", short: "SC", color: "var(--a-scheduler)", hint: "CP-SAT по кускам" },
  { name: "rooms", label: "Rooms", ru: "Аудитории", short: "RO", color: "var(--a-rooms)", hint: "квоты и аудитории" },
  { name: "validator", label: "Validator", ru: "Критик", short: "VA", color: "var(--a-validator)", hint: "независимая проверка" },
  { name: "negotiator", label: "Negotiator", ru: "Переговорщик", short: "NE", color: "var(--a-negotiator)", hint: "уступки и ремонт" },
  { name: "reporter", label: "Reporter", ru: "Отчётность", short: "RE", color: "var(--a-reporter)", hint: "xlsx / html / отчёт" },
];
const AGENT_BY = Object.fromEntries(AGENTS.map(a => [a.name, a]));
const DAY_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];
const DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
const DAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const KIND_RU = { lecture: "лекция", practice: "практика", lab: "лаб." };
const TASK_RU = {
  load_data: "загрузка данных", parse_constraints: "разбор требований", plan_chunks: "декомпозиция",
  init_capacity: "аудиторный фонд", allocate_quota: "квота аудиторий", solve_chunk: "решение CP-SAT",
  validate_chunk: "проверка куска", resolve: "переговоры", assign_rooms: "назначение аудиторий",
  validate_all: "итоговая проверка", repair: "локальный ремонт", export: "экспорт",
};
const STAGES = [
  { key: "load_data", label: "Данные", agent: "data" }, { key: "parse_constraints", label: "Правила", agent: "constraints" },
  { key: "plan_chunks", label: "Куски", agent: "scheduler" }, { key: "init_capacity", label: "Фонд", agent: "rooms" },
  { key: "chunks", label: "Решение кусков", agent: "scheduler" }, { key: "assign_rooms", label: "Аудитории", agent: "rooms" },
  { key: "validate_all", label: "Проверка", agent: "validator" }, { key: "export", label: "Экспорт", agent: "reporter" },
];
const ACTION_RU = {
  extend_time: "больше времени решателю", full_quota: "весь остаток аудиторного фонда",
  allow_saturday: "открыть субботу", relax_user_constraints: "ослабить пользовательские запреты", give_up: "сдаться",
};
const STATUS_RU = { ok: "успешно", partial: "частично", failed: "ошибка", stopped: "остановлен", running: "выполняется",
  cancelling: "отмена…", incomplete: "прерван", unknown: "?" };
const STATUS_CLS = { ok: "ok", partial: "warn", failed: "bad", stopped: "bad", running: "accent", cancelling: "warn", incomplete: "bad" };
const EXAMPLE_REQS = ["1 курс заканчивает до 17:00", "Physical Education только утром", "Не более 3 пар в день",
  "Аудитория C1.3.361 недоступна в среду", "Желательно не ставить занятия до 9:00", "У группы IT-2301 свободна пятница",
  "No classes on Saturday", "SE 2 курс не раньше 10:00"];
const PRESETS = [
  { id: "demo", title: "Демо: программа IT", sub: "22 группы · ~10 секунд", programs: ["IT"], seconds: 8 },
  { id: "two", title: "Две программы SE + CS", sub: "172 группы · ~1 минута", programs: ["SE", "CS"], seconds: 15 },
  { id: "full", title: "Полный прогон", sub: "270 групп, 13 программ · ~2.5 минуты", programs: [], seconds: 25 },
];
const I = {
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11 12 3l9 8M5 10v10h14V10"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l12-7.5z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M6 10l6 6 6-6M4 20h16"/></svg>',
  print: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 8V3h10v5M5 8h14a2 2 0 0 1 2 2v6h-4v4H7v-4H3v-6a2 2 0 0 1 2-2z"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5L20 7"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20L12 3zM12 10v4M12 17.5v.5"/></svg>',
  cal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h8l5 5v13H6zM14 3v5h5M9 13h6M9 17h6"/></svg>',
  db: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
  auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
};

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDur = s => { s = Math.round(s || 0); return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`; };
const fmtDate = ts => new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const pct = x => `${Math.round((x || 0) * 100)}%`;
const sum = arr => arr.reduce((a, b) => a + b, 0);
const plural = (n, one, few, many) => { const m = n % 10, h = n % 100; return `${n} ${h >= 11 && h <= 14 ? many : m === 1 ? one : m >= 2 && m <= 4 ? few : many}`; };
const agentTag = name => { const a = AGENT_BY[name] || { color: "var(--a-system)", label: name }; return `<span class="agent-tag" style="--c:${a.color}">${esc(a.label)}</span>`; };
const statusBadge = st => `<span class="badge ${STATUS_CLS[st] || ""}">${st === "running" || st === "cancelling" ? '<i class="pulse"></i>' : ""}${esc(STATUS_RU[st] || st)}</span>`;
const kpi = (v, l, cls = "", sub = "") => `<div class="kpi ${cls}"><div class="v">${v}</div><div class="l">${l}</div>${sub ? `<div class="d">${sub}</div>` : ""}</div>`;
const skeleton = (n = 3) => `<div class="skeleton-wrap">${Array.from({ length: n }, () => '<div class="skeleton"></div>').join("")}</div>`;
const hvColor = hv => hv === 0 ? "var(--ok)" : hv > 0 ? "var(--bad)" : "inherit";

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
function setOnline(ok) { $("#srv-dot").classList.toggle("off", !ok); $("#srv-text").textContent = ok ? "сервер онлайн" : "нет связи с сервером"; }
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

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

// ------------------------------------------------------------------ theme
const THEMES = ["auto", "light", "dark"], THEME_RU = { auto: "авто", light: "светлая", dark: "тёмная" };
const getTheme = () => { try { return localStorage.getItem("theme") || "auto"; } catch (_) { return "auto"; } };
function applyTheme() {
  const t = getTheme();
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
  $("#theme-btn").innerHTML = `${I[t === "light" ? "sun" : t === "dark" ? "moon" : "auto"]} Тема: ${THEME_RU[t]}`;
}
$("#theme-btn").onclick = () => { const next = THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length]; try { localStorage.setItem("theme", next); } catch (_) { } applyTheme(); };
{ const q = new URLSearchParams(location.search).get("theme"); if (q && THEMES.includes(q)) { try { localStorage.setItem("theme", q); } catch (_) { } } }
applyTheme();

// ------------------------------------------------------------------ router
let currentView = null;
function route() {
  const hash = location.hash || "#/";
  const same = currentView instanceof RunView && hash.startsWith(`#/run/${currentView.id}/`) && !currentView.dead;
  if (!same) { if (currentView && currentView.destroy) currentView.destroy(); currentView = null; }
  const m = /^#\/run\/([A-Za-z0-9_.\-]+)(?:\/([a-z]+))?(?:\/([a-z]+))?(?:\/(.+))?/.exec(hash);
  let name = "home";
  if (hash.startsWith("#/new")) name = "new"; else if (hash.startsWith("#/runs")) name = "runs";
  else if (hash.startsWith("#/agents")) name = "agents"; else if (m) name = "run";
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.route === (name === "run" ? "runs" : name)));
  const main = $("#main"); if (!same) { main.innerHTML = skeleton(4); window.scrollTo(0, 0); }
  ({ home: renderHome, new: renderNew, runs: renderRuns, agents: renderAgents, run: () => renderRun(m[1], m[2], m[3], m[4] && decodeURIComponent(m[4])) })[name]().catch(e => {
    main.innerHTML = `<div class="card empty-state">${I.alert}<h2>Не удалось открыть страницу</h2><p>${esc(e.message)}</p><a class="btn" href="#/">На обзор</a></div>`;
  });
}
window.addEventListener("hashchange", route);

function topbar(title, sub, actions = "", crumbs = "") {
  return `<div class="topbar"><div>${crumbs ? `<div class="crumbs">${crumbs}</div>` : ""}<h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div><div class="actions">${actions}</div></div>`;
}
const pipelineStrip = (cls = "done", extra = () => "") => `<div class="pipeline">${AGENTS.map(a => `<a class="agent-node ${cls}" style="--c:${a.color}" href="#/agents" data-agent="${a.name}"><div class="ic">${a.short}</div><b>${a.ru}</b><div class="meta">${extra(a) || a.hint}</div></a>`).join("")}</div>`;

// ------------------------------------------------------------------ home
async function renderHome() {
  const [runs, datasets] = await Promise.all([api("/api/runs"), api("/api/datasets")]);
  const last = runs.find(r => r.status === "ok" || r.status === "partial");
  const active = runs.find(r => r.status === "running" || r.status === "cancelling");
  const ds = datasets.find(d => d.valid && d.id === "input.xlsx") || datasets.find(d => d.valid);
  const okRuns = runs.filter(r => r.status === "ok").length;
  const main = $("#main");
  main.innerHTML = `
    <section class="hero-card">
      <div class="hero-text">
        <div class="eyebrow">Astana IT University · многоагентная ИИ-система</div>
        <h1>Расписание университета, которое агенты собирают сами</h1>
        <p>Один прогон строит недельное расписание для ${ds ? ds.groups : 270} групп. Восемь агентов делят работу: читают данные вуза,
        переводят пожелания на человеческом языке в правила, решают задачу в OR-Tools CP-SAT, проверяют друг друга и договариваются об уступках,
        когда всё сразу выполнить невозможно.</p>
        <div class="actions">
          <a class="btn primary lg" href="#/new">${I.play} Запустить прогон</a>
          <button class="btn lg" id="demo-btn">${I.bolt} Демо на программе IT · 10 с</button>
          <a class="btn lg ghost" href="#/agents">Как устроены агенты ${I.arrow}</a>
        </div>
      </div>
      <div class="hero-stats">
        ${ds ? `${kpi(ds.groups, "учебных групп")}${kpi(ds.programs.length, "образовательных программ")}${kpi(ds.rooms, "аудиторий")}${kpi(ds.slots, "слотов в неделю")}` : '<div class="muted">нет пригодных данных</div>'}
      </div>
    </section>

    ${active ? `<div class="card accent-border" style="margin-bottom:16px"><div class="actions" style="justify-content:space-between"><div class="actions">${statusBadge(active.status)}<b class="mono">${esc(active.id)}</b><span class="muted">${fmtDur(active.elapsed_s)} · ${active.programs.length ? esc(active.programs.join(", ")) : "все программы"}</span></div><a class="btn primary" href="#/run/${esc(active.id)}">Смотреть ход выполнения ${I.arrow}</a></div></div>` : ""}

    <section class="grid c4 step-grid" style="margin-bottom:16px">
      ${[["1", "Данные вуза", "Excel с учебными планами, группами, аудиториями и слотами. Грязные данные чинятся и помечаются."],
      ["2", "Требования словами", "«1 курс заканчивает до 17:00», «аудитория закрыта в среду» — по-русски или по-английски."],
      ["3", "Агенты договариваются", "Решатель, критик и переговорщик обмениваются JSON-сообщениями, пока расписание не станет допустимым."],
      ["4", "Готовое расписание", "Сетка по группам и аудиториям, Excel, отчёт для руководства и полный журнал решений."]]
      .map(([n, t, d]) => `<div class="card step-card"><div class="step-n">${n}</div><h3>${t}</h3><p>${d}</p></div>`).join("")}
    </section>

    <section class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>Команда агентов</h2><a class="btn sm ghost" href="#/agents">Спецификация ${I.arrow}</a></div>
      ${pipelineStrip("done")}
      <p class="muted small" style="margin:12px 0 0">Оркестратор только планирует и маршрутизирует. Каждый агент владеет одной ролью и не более чем пятью инструментами; все вызовы логируются, и ни один агент не делает больше 40 % работы.</p>
    </section>

    ${last ? `<section class="card" style="margin-bottom:16px"><div class="card-head"><h2>Последний результат</h2><div class="actions">${statusBadge(last.status)}<a class="btn sm" href="#/run/${esc(last.id)}/summary">${esc(last.id)} ${I.arrow}</a></div></div>
      <div class="kpi-row">${kpiTiles(last)}</div></section>` : ""}

    <section class="card">
      <div class="card-head"><h2>Последние прогоны</h2><div class="actions"><span class="muted small">${plural(runs.length, "прогон", "прогона", "прогонов")}, успешных ${okRuns}</span><a class="btn sm ghost" href="#/runs">Все прогоны ${I.arrow}</a></div></div>
      ${runs.length ? runsTable(runs.slice(0, 6), false) : `<div class="empty-state">${I.cal}<h3>Прогонов пока нет</h3><p>Запустите первый — демо на программе IT занимает около десяти секунд.</p></div>`}
    </section>`;
  bindRunRows(main);
  $("#demo-btn").onclick = () => startPreset("demo", $("#demo-btn"));
}
async function startPreset(id, btn) {
  const p = PRESETS.find(x => x.id === id);
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Запуск…'; }
  try {
    const r = await post("/api/runs", { dataset: "input.xlsx", term: 1, programs: p.programs, requirements: id === "demo" ? ["1 курс заканчивает до 17:00"] : [], solver_seconds: p.seconds, llm: "auto", name: id });
    location.hash = `#/run/${r.id}/live`;
  } catch (e) { toast(e.message, true); if (btn) { btn.disabled = false; btn.innerHTML = `${I.bolt} Демо на программе IT · 10 с`; } }
}
function kpiTiles(r) {
  const s = r.soft || {}; const hv = r.hard_violations;
  return kpi(hv ?? "—", "жёстких нарушений", hv === 0 ? "ok" : hv > 0 ? "bad" : "") + kpi(s.gap_hours_per_group ?? "—", "окон, ч на группу в неделю") +
    kpi(s.late_hours_18_plus ?? "—", "часов после 18:00") + kpi(s.saturday_hours ?? "—", "часов в субботу") +
    kpi(s.lectures_in_lecture_halls ?? "—", "лекций в лекционных залах") + kpi(r.max_share != null ? pct(r.max_share) : "—", "макс. доля одного агента", r.max_share > 0.4 ? "bad" : "", "норма ≤ 40 %");
}
function runsTable(runs, withActions = true, compare = null) {
  return `<div class="tbl-wrap"><table class="tbl runs">
    <thead><tr>${compare ? "<th></th>" : ""}<th>Прогон</th><th>Статус</th><th>Данные</th><th class="num">Групп</th><th class="num">Нарушений</th><th class="num">Окон ч/гр</th><th class="num">После 18:00</th><th class="num">Время</th><th>LLM</th>${withActions ? "<th></th>" : ""}</tr></thead>
    <tbody>${runs.map(r => `<tr class="link" data-id="${esc(r.id)}">
      ${compare ? `<td><input type="checkbox" class="cmp" data-id="${esc(r.id)}" ${compare.includes(r.id) ? "checked" : ""} ${r.soft ? "" : "disabled"}></td>` : ""}
      <td><div class="mono" style="font-weight:600">${esc(r.id)}</div><div class="small muted">${fmtDate(r.started)}</div></td>
      <td>${statusBadge(r.status)}</td>
      <td><div>${r.programs && r.programs.length ? esc(r.programs.join(", ")) : "все программы"}</div><div class="small muted">триместр ${r.term ?? "?"}</div></td>
      <td class="num">${r.groups ?? "—"}</td>
      <td class="num" style="color:${hvColor(r.hard_violations)};font-weight:600">${r.hard_violations ?? "—"}</td>
      <td class="num">${r.soft ? r.soft.gap_hours_per_group : "—"}</td>
      <td class="num">${r.soft ? r.soft.late_hours_18_plus : "—"}</td>
      <td class="num">${fmtDur(r.elapsed_s)}</td>
      <td class="small muted">${esc(r.llm || "—")}</td>
      ${withActions ? `<td class="row-actions"><button class="btn sm ghost del" data-id="${esc(r.id)}" title="Удалить" ${r.status === "running" || r.status === "cancelling" ? "disabled" : ""}>${I.trash}</button></td>` : ""}
    </tr>`).join("")}</tbody></table></div>`;
}
function bindRunRows(root, onDelete) {
  $$("tr.link", root).forEach(el => el.onclick = e => { if (e.target.closest("button, input")) return; location.hash = `#/run/${el.dataset.id}`; });
  $$("button.del", root).forEach(b => b.onclick = async () => {
    if (!confirm(`Удалить прогон ${b.dataset.id} со всеми файлами?`)) return;
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
    const shown = runs.filter(r => (f === "all" || (f === "ok" ? r.status === "ok" : f === "partial" ? r.status === "partial" : f === "bad" ? ["failed", "stopped", "incomplete"].includes(r.status) : ["running", "cancelling"].includes(r.status)))
      && (!q || r.id.toLowerCase().includes(q) || (r.programs || []).join(",").toLowerCase().includes(q)));
    const counts = { all: runs.length, ok: runs.filter(r => r.status === "ok").length, partial: runs.filter(r => r.status === "partial").length,
      bad: runs.filter(r => ["failed", "stopped", "incomplete"].includes(r.status)).length, live: runs.filter(r => ["running", "cancelling"].includes(r.status)).length };
    const cmp = runs.filter(r => runsState.compare.includes(r.id));
    main.innerHTML = topbar("Прогоны", `${plural(runs.length, "прогон", "прогона", "прогонов")} в папке runs/. Отметьте до трёх прогонов, чтобы сравнить качество.`, `<a class="btn primary" href="#/new">${I.plus} Новый прогон</a>`) + `
      <div class="card" style="margin-bottom:16px">
        <div class="actions" style="justify-content:space-between;margin-bottom:12px">
          <div class="chips" id="f">${[["all", "все"], ["ok", "успешные"], ["partial", "частичные"], ["bad", "с ошибкой"], ["live", "идут сейчас"]].map(([k, l]) => `<span class="chip ${f === k ? "on" : ""}" data-f="${k}">${l}<span class="n">${counts[k]}</span></span>`).join("")}</div>
          <div class="search">${I.search}<input type="text" id="q" placeholder="поиск по имени или программе" value="${esc(runsState.q)}"></div></div>
        ${shown.length ? runsTable(shown, true, runsState.compare) : `<div class="empty-state">${I.search}<h3>Ничего не найдено</h3><p>Измените фильтр или запустите новый прогон.</p></div>`}</div>
      ${cmp.length >= 2 ? `<div class="card"><div class="card-head"><h2>Сравнение прогонов</h2><button class="btn sm ghost" id="cmp-clear">Сбросить</button></div>${compareTable(cmp)}</div>` : ""}`;
    $$("#f .chip").forEach(c => c.onclick = () => { runsState.filter = c.dataset.f; draw(); });
    const qi = $("#q"); qi.oninput = debounce(() => { runsState.q = qi.value; draw(); $("#q").focus(); }, 250);
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
  const rows = [["Статус", r => statusBadge(r.status)], ["Программы", r => r.programs.length ? r.programs.join(", ") : "все"], ["Групп", r => r.groups ?? "—"],
    ["Жёстких нарушений", r => `<b style="color:${hvColor(r.hard_violations)}">${r.hard_violations ?? "—"}</b>`], ["Окон, ч на группу", r => r.soft.gap_hours_per_group],
    ["Окон всего, ч", r => r.soft.gap_hours_total], ["Часов после 18:00", r => r.soft.late_hours_18_plus], ["Часов в субботу", r => r.soft.saturday_hours],
    ["Практика раньше лекции", r => r.soft.precedence_violations], ["Лекций в лекц. залах", r => r.soft.lectures_in_lecture_halls],
    ["Шагов оркестратора", r => r.steps ?? "—"], ["Макс. доля агента", r => r.max_share != null ? pct(r.max_share) : "—"], ["Время", r => fmtDur(r.elapsed_s)], ["LLM", r => r.llm || "—"]];
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Метрика</th>${rs.map(r => `<th><a href="#/run/${esc(r.id)}/summary" class="mono">${esc(r.id)}</a></th>`).join("")}</tr></thead>
    <tbody>${rows.map(([l, f]) => `<tr><td class="muted">${l}</td>${rs.map(r => `<td>${f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

// ------------------------------------------------------------------ agents
async function renderAgents() {
  const spec = await api("/api/agents");
  const main = $("#main");
  const section = a => {
    const m = AGENT_BY[a.name] || { color: "var(--a-system)", short: "??", label: a.name };
    return `<section class="card agent-section" id="agent-${a.name}" style="--c:${m.color}">
      <header class="agent-head"><div class="ic">${m.short}</div><div><h2>${esc(a.title)} <span class="muted" style="font-weight:500">· ${esc(m.label)}</span></h2><div class="muted">${esc(a.tagline)}</div></div>
        <div class="actions" style="margin-left:auto"><span class="badge">${a.tools.length ? plural(a.tools.length, "инструмент", "инструмента", "инструментов") : "без инструментов"}</span>${a.llm && !/не использует/.test(a.llm) ? '<span class="badge accent">использует LLM</span>' : '<span class="badge">без LLM</span>'}</div></header>
      <p class="lead">${esc(a.role)}</p>
      <div class="grid c2" style="gap:20px">
        <div><h3 class="h-sm">Что делает</h3><ul class="nice">${(a.does || []).map(d => `<li>${esc(d)}</li>`).join("")}</ul></div>
        <div>
          <h3 class="h-sm">Входы</h3><ul class="nice">${(a.inputs || []).map(d => `<li>${esc(d)}</li>`).join("")}</ul>
          <h3 class="h-sm" style="margin-top:12px">Выходы</h3><ul class="nice">${(a.outputs || []).map(d => `<li>${esc(d)}</li>`).join("")}</ul>
          ${(a.blackboard || []).length ? `<h3 class="h-sm" style="margin-top:12px">Ключи blackboard</h3><div class="chips">${a.blackboard.map(k => `<code class="key">${esc(k)}</code>`).join("")}</div>` : ""}
        </div></div>
      ${a.tools.length ? `<h3 class="h-sm" style="margin-top:16px">Инструменты</h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Инструмент</th><th>Назначение</th><th>В коде</th></tr></thead>
        <tbody>${a.tools.map(t => `<tr><td><code class="key">${esc(t.name)}</code></td><td>${esc(t.ru || t.en)}</td><td class="small muted">${esc(t.en)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <div class="grid c3 facts" style="margin-top:16px">
        <div><div class="l">LLM</div><div>${esc(a.llm)}</div></div>
        <div><div class="l">Готово, когда</div><div>${esc(a.done)}</div></div>
        <div><div class="l">Надёжность</div><div>${esc(a.reliability)}</div></div></div>
    </section>`;
  };
  main.innerHTML = topbar("Агенты", "Восемь агентов, одна роль у каждого. Спецификация собрана из кода и дополнена описанием на русском.", `<a class="btn primary" href="#/new">${I.plus} Новый прогон</a>`) + `
    <section class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>Схема: оркестратор + общая память</h2></div>
      ${pipelineStrip("done")}
      <div class="grid c3" style="margin-top:16px">
        <div class="fact-card"><b>Оркестратор + blackboard</b><p>Оркестратор отправляет типизированные сообщения, агенты кладут большие объекты (датасет, решения) в SQLite и возвращают только статусы и ключи.</p></div>
        <div class="fact-card"><b>Решатель ≠ критик</b><p>Scheduler строит расписание в CP-SAT, Validator перепроверяет его другим кодом. Только проверенный кусок попадает в общее расписание.</p></div>
        <div class="fact-card"><b>Переговоры вместо падения</b><p>Если кусок невыполним, Negotiator по диагнозу выбирает самую дешёвую уступку: время, аудитории, суббота или мягкие правила.</p></div></div>
    </section>
    <div class="agents-layout">
      <nav class="agents-index card flat"><div class="l">Агенты</div>${spec.agents.map(a => { const m = AGENT_BY[a.name]; return `<a href="#agent-${a.name}" data-target="agent-${a.name}" style="--c:${m.color}"><span class="agent-dot"></span>${esc(a.title)}<span class="muted small">${esc(m.label)}</span></a>`; }).join("")}
        <div class="l" style="margin-top:12px">Ещё</div><a href="#protocol" data-target="protocol">Протокол сообщений</a><a href="#tz" data-target="tz">Требования ТЗ</a></nav>
      <div class="agents-body">
        ${spec.agents.map(section).join("")}
        <section class="card" id="protocol"><div class="card-head"><h2>Протокол сообщений</h2><span class="badge">core/messages.py</span></div>
          <div class="grid c2"><div><div class="l-cap">Поручение оркестратора</div><pre class="code">${esc(JSON.stringify(spec.protocol.message, null, 2))}</pre></div>
            <div><div class="l-cap">Ответ агента</div><pre class="code">${esc(JSON.stringify(spec.protocol.reply, null, 2))}</pre></div></div>
          <ul class="nice" style="margin-top:12px">${spec.protocol.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></section>
        <section class="card" id="tz"><div class="card-head"><h2>Требования ТЗ и где они выполняются</h2></div>
          <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Требование</th><th>Как выполнено</th><th>Где в коде</th></tr></thead>
            <tbody>${spec.requirements.map(r => `<tr><td><span class="ok-mark">${I.check}</span>${esc(r.req)}</td><td>${esc(r.how)}</td><td><code class="key">${esc(r.where)}</code></td></tr>`).join("")}</tbody></table></div></section>
      </div></div>`;
  $$(".agents-index a, .pipeline a").forEach(a => a.onclick = e => { const id = a.dataset.target || `agent-${a.dataset.agent}`; const el = document.getElementById(id); if (el) { e.preventDefault(); el.scrollIntoView({ behavior: "smooth", block: "start" }); } });
}

// ------------------------------------------------------------------ new run
const draft = { dataset: null, term: 1, programs: [], requirements: "", allow_saturday: false, solver_seconds: 25, chunk_groups: 60, stream: 2, llm: "auto", name: "run" };
async function renderNew() {
  const datasets = await api("/api/datasets");
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
      box.innerHTML = `<div class="l-cap">Как система понимает требования <span class="muted" style="text-transform:none;letter-spacing:0">(офлайн-правила; с Claude разбор точнее)</span></div>` + lines.map(l => {
        const p = bySource[l.trim()];
        if (!p) return `<div class="prev-row"><span class="badge bad">не понято</span><span>${esc(l)}</span><span class="muted small">попадёт в предупреждения отчёта</span></div>`;
        if (p.unknown.length) return `<div class="prev-row"><span class="badge warn">неизвестно: ${esc(p.unknown.join(", "))}</span><span>${esc(l)}</span><span class="muted small">правило будет пропущено</span></div>`;
        return `<div class="prev-row"><span class="badge ${p.hard ? "accent" : "info"}">${p.hard ? "жёсткое" : "мягкое"}</span><span>${esc(l)}</span><span class="muted small">${esc(describeSpec(p))}</span></div>`;
      }).join("");
    } catch (e) { box.innerHTML = `<div class="small muted">предпросмотр недоступен: ${esc(e.message)}</div>`; }
  };
  const render = () => {
    const ds = datasets.find(d => d.id === draft.dataset);
    const groups = ds ? sum(ds.programs.filter(p => !draft.programs.length || draft.programs.includes(p.code)).map(p => p.groups)) : 0;
    const chunks = Math.max(1, Math.ceil(groups / (draft.chunk_groups || 60)));
    const est = chunks * (draft.solver_seconds || 25) + 15;
    const nReq = draft.requirements.split("\n").filter(l => l.trim()).length;
    main.innerHTML = topbar("Новый прогон", "Выберите данные и программы, опишите требования — агенты сделают остальное.") + `
      <div class="presets">${PRESETS.map(p => `<button class="preset" data-p="${p.id}">${I.bolt}<div><b>${p.title}</b><div class="small muted">${p.sub}</div></div></button>`).join("")}</div>
      <div class="new-layout">
        <div class="new-main">
          <div class="card"><div class="card-head"><h2><span class="num-badge">1</span>Данные вуза</h2></div>
            <div class="grid c3" id="ds-grid">${datasets.filter(d => d.valid).map(d => `<div class="ds-card ${d.id === draft.dataset ? "on" : ""}" data-id="${esc(d.id)}" title="${esc(d.path)}">
              <div class="actions" style="justify-content:space-between"><b>${I.db} ${esc(d.name)}</b>${d.id === draft.dataset ? `<span class="ok-mark">${I.check}</span>` : ""}</div><div class="small muted">${esc(d.dir)}/ · ${d.size_kb} КБ${d.issues.warning ? ` · <span style="color:var(--warn)">${d.issues.warning} предупр.</span>` : ""}</div>
              <div class="stats"><span>${d.programs.length} прогр.</span><span>${d.groups} групп</span><span>${d.rooms} ауд.</span><span>${d.courses} курсов</span><span>${d.students} студ.</span></div></div>`).join("") || '<div class="empty-state">Нет пригодных файлов</div>'}</div>
            ${datasets.some(d => !d.valid) ? `<details style="margin-top:10px"><summary class="small muted" style="cursor:pointer">Ещё ${datasets.filter(d => !d.valid).length} файлов не подходят (сырые учебные планы без листов Groups / Rooms / Timeslots)</summary><div class="small muted" style="margin-top:6px">${datasets.filter(d => !d.valid).map(d => `<div>${esc(d.dir)}/${esc(d.id)} — ${esc(d.error || "")}</div>`).join("")}</div></details>` : ""}
            <div class="dropzone" id="drop" style="margin-top:12px">${I.down} Перетащите .xlsx сюда или нажмите, чтобы загрузить свой файл (листы программ + Groups, Rooms, Timeslots)</div>
            <input type="file" id="file" accept=".xlsx" style="display:none"></div>
          <div class="card"><div class="card-head"><h2><span class="num-badge">2</span>Триместр и программы</h2></div>
            <div class="actions" style="margin-bottom:14px"><span class="muted">Триместр учебного года:</span><div class="seg" id="term">${[1, 2, 3].map(t => `<button class="${draft.term === t ? "on" : ""}" data-t="${t}">${t}</button>`).join("")}</div><span class="muted small">1-й курс учится в ${draft.term}-м триместре, 2-й — в ${draft.term + 3}-м, 3-й — в ${draft.term + 6}-м</span></div>
            <div class="chips" id="progs"><span class="chip ${draft.programs.length ? "" : "on"}" data-p="">Все программы</span>${ds ? ds.programs.map(p => `<span class="chip ${draft.programs.includes(p.code) ? "on" : ""}" data-p="${esc(p.code)}">${esc(p.code)}<span class="n">${p.groups}</span></span>`).join("") : ""}</div>
            <div class="small muted" style="margin-top:10px">Выбрано <b>${plural(groups, "группа", "группы", "групп")}</b> → ${plural(chunks, "кусок", "куска", "кусков")} по ~${draft.chunk_groups} групп, ожидаемое время ≈ <b>${fmtDur(est)}</b>.</div></div>
          <div class="card"><div class="card-head"><h2><span class="num-badge">3</span>Требования на естественном языке</h2><span class="muted small">${nReq ? plural(nReq, "строка", "строки", "строк") : "необязательно"}</span></div>
            <textarea id="reqs" placeholder="Одно требование в строке, по-русски или по-английски…">${esc(draft.requirements)}</textarea>
            <div class="hint" style="margin-top:6px">Жёсткие правила («не ставить», «недоступна») решатель соблюдает всегда; мягкие («желательно») штрафуются. Если правило делает кусок невыполнимым, Negotiator ослабит его и объяснит почему.</div>
            <div class="chips" style="margin-top:10px" id="examples">${EXAMPLE_REQS.map(e => `<span class="chip" data-e="${esc(e)}">+ ${esc(e)}</span>`).join("")}</div>
            <div id="preview" class="preview"></div></div>
          <details class="card adv"><summary><span class="num-badge">4</span>Дополнительные параметры</summary>
            <div class="grid c3" style="margin-top:14px">
              <div class="field"><label>Время решателя на кусок, с</label><input type="number" id="secs" min="1" max="600" value="${draft.solver_seconds}"><div class="hint">больше → меньше окон, дольше прогон</div></div>
              <div class="field"><label>Групп в куске</label><input type="number" id="chunkg" min="5" max="300" value="${draft.chunk_groups}"><div class="hint">размер подзадачи CP-SAT</div></div>
              <div class="field"><label>Групп в оффлайн-потоке лекции</label><input type="number" id="stream" min="1" max="50" value="${draft.stream}"></div>
              <div class="field"><label>LLM</label><select id="llm"><option value="auto" ${draft.llm === "auto" ? "selected" : ""}>auto — Claude, если есть ключ</option><option value="claude" ${draft.llm === "claude" ? "selected" : ""}>claude — только Claude</option><option value="offline" ${draft.llm === "offline" ? "selected" : ""}>offline — правила без LLM</option></select></div>
              <div class="field"><label>Имя прогона</label><input type="text" id="name" maxlength="40" value="${esc(draft.name)}"></div>
              <div class="field"><label>Суббота</label><label class="switch"><input type="checkbox" id="sat" ${draft.allow_saturday ? "checked" : ""}><span class="track"></span><span>разрешить с самого начала</span></label></div>
            </div></details>
        </div>
        <aside class="new-side">
          <div class="card"><div class="card-head"><h2>Итог</h2></div>
            <table class="tbl kv"><tr><td>Данные</td><td>${ds ? esc(ds.name) : "—"}</td></tr><tr><td>Триместр</td><td>${draft.term}</td></tr>
              <tr><td>Программы</td><td>${draft.programs.length ? esc(draft.programs.join(", ")) : "все"}</td></tr><tr><td>Групп</td><td>${groups}</td></tr>
              <tr><td>Требований</td><td>${nReq}</td></tr><tr><td>LLM</td><td>${draft.llm}</td></tr><tr><td>Оценка времени</td><td>${fmtDur(est)}</td></tr></table>
            <button class="btn primary lg" id="go" style="width:100%;justify-content:center;margin-top:16px" ${ds ? "" : "disabled"}>${I.play} Сгенерировать расписание</button>
            <div class="small muted" style="margin-top:10px">Прогон идёт в фоне, страницу можно закрывать. Одновременно выполняется один прогон.</div></div>
          <div class="card flat"><div class="l-cap">Что произойдёт</div><ol class="timeline-list">${STAGES.map(s => `<li><span class="agent-dot" style="--c:${AGENT_BY[s.agent].color}"></span>${s.label} <span class="muted small">· ${AGENT_BY[s.agent].ru}</span></li>`).join("")}</ol></div>
        </aside></div>`;
    $$(".preset").forEach(b => b.onclick = () => { const p = PRESETS.find(x => x.id === b.dataset.p); draft.programs = [...p.programs]; draft.solver_seconds = p.seconds; draft.name = p.id; render(); });
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
    const drop = $("#drop"), file = $("#file");
    drop.onclick = () => file.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); }; drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
    file.onchange = () => file.files[0] && upload(file.files[0]);
    async function upload(f) {
      const fd = new FormData(); fd.append("file", f);
      try {
        const d = await api("/api/datasets/upload", { method: "POST", body: fd }); toast(d.valid ? `Файл ${d.name} загружен` : `Файл загружен, но не подходит: ${d.error}`, !d.valid);
        const i = datasets.findIndex(x => x.id === d.id); if (i >= 0) datasets[i] = d; else datasets.push(d); if (d.valid) { draft.dataset = d.id; draft.programs = []; } render();
      } catch (e) { toast(e.message, true); }
    }
    $("#go").onclick = async () => {
      const btn = $("#go"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Запуск…';
      try {
        const r = await post("/api/runs", { dataset: draft.dataset, term: draft.term, programs: draft.programs, requirements: draft.requirements.split("\n"),
          allow_saturday: draft.allow_saturday, solver_seconds: draft.solver_seconds, chunk_groups: draft.chunk_groups, stream: draft.stream, llm: draft.llm, name: draft.name || "run" });
        location.hash = `#/run/${r.id}/live`;
      } catch (e) { toast(e.message, true); btn.disabled = false; btn.innerHTML = `${I.play} Сгенерировать расписание`; }
    };
    preview();
  };
  render();
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
    this.calls = {}; this.active = null; this.stage = ""; this.chunksTotal = null; this.chunksDone = 0; this.negotiations = 0; this.errors = 0; this.repairs = 0; this.msgs = 0;
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
    if (e.kind === "error") this.errors++;
    if (e.kind === "message") {
      this.msgs++;
      if (e.agent === "orchestrator") { this.active = e.to; this.stage = e.task; this.stageChunk = e.payload && e.payload.chunk; }
      else if (e.to === "orchestrator") {
        this.active = null; const p = e.payload || {};
        if (e.task === "plan_chunks" && typeof p.chunks === "number") this.chunksTotal = p.chunks;
        if (e.task === "validate_chunk" && p.hard_violations === 0) this.chunksDone++;
        if (e.task === "resolve") this.negotiations++;
        if (e.task === "repair" && typeof p.moved === "number") this.repairs += p.moved;
      }
    }
  }

  renderShell() {
    $("#main").innerHTML = `
      <div id="hdr"></div>
      <div class="card" style="margin-bottom:4px"><div id="pipe"></div>
        <div class="stepper" id="stepper"></div>
        <div style="display:flex;align-items:center;gap:14px;margin-top:12px"><div class="bar" style="flex:1" id="prog"><i style="width:0"></i></div><div class="small muted nowrap" id="prog-text"></div></div></div>
      <div class="tabs" id="tabs"></div>
      <div id="tab-body"></div>`;
    this.renderTabs();
  }
  renderHeader() {
    const s = this.status, p = s.params || {};
    const canCancel = this.running && s.status === "running";
    const chips = [`триместр ${p.term ?? "?"}`, p.programs && p.programs.length ? p.programs.join(", ") : "все программы",
      `LLM: ${(s.result && s.result.llm) || p.llm || "auto"}`, p.requirements && p.requirements.length ? plural(p.requirements.length, "требование", "требования", "требований") : "без требований",
      p.solver_seconds ? `${p.solver_seconds} с на кусок` : null, fmtDur(s.elapsed_s)].filter(Boolean);
    $("#hdr").innerHTML = topbar(`<span class="mono" style="font-size:20px">${esc(this.id)}</span> ${statusBadge(s.status)}`,
      `<div class="chips tight">${chips.map(c => `<span class="chip static">${esc(c)}</span>`).join("")}</div>${s.result && s.result.reason ? `<div style="color:var(--bad);margin-top:6px">${I.alert} ${esc(s.result.reason)}</div>` : ""}`,
      `${canCancel ? `<button class="btn danger" id="cancel">${I.stop} Остановить</button>` : ""}
       ${!this.running ? `<a class="btn" href="/api/runs/${esc(this.id)}/files/timetable.xlsx">${I.down} Excel</a><button class="btn ghost" id="del" title="Удалить прогон">${I.trash}</button>` : ""}
       <a class="btn primary" href="#/new">${I.plus} Новый</a>`,
      `<a href="#/runs">Прогоны</a> › ${fmtDate(s.started)}`);
    const c = $("#cancel"); if (c) c.onclick = async () => { c.disabled = true; try { await post(`/api/runs/${this.id}/cancel`, {}); toast("Останавливаем после текущего шага"); } catch (e) { toast(e.message, true); } };
    const d = $("#del"); if (d) d.onclick = async () => { if (!confirm(`Удалить прогон ${this.id} со всеми файлами?`)) return; try { await api(`/api/runs/${this.id}`, { method: "DELETE" }); toast("Прогон удалён"); location.hash = "#/runs"; } catch (e) { toast(e.message, true); } };
  }
  renderPipeline() {
    const res = this.status.result;
    $("#pipe").innerHTML = `<div class="pipeline">${AGENTS.map(a => {
      const n = this.calls[a.name] || 0, isActive = this.running && this.active === a.name;
      const cls = isActive ? "active" : (n || a.name === "orchestrator") ? "done" : "";
      const meta = isActive ? `<span style="color:${a.color};font-weight:600">${esc(TASK_RU[this.stage] || this.stage)}${this.stageChunk != null && /chunk|quota|resolve/.test(this.stage) ? ` #${this.stageChunk}` : ""}</span>`
        : a.name === "orchestrator" ? plural(this.events.filter(e => e.kind === "message" && e.agent === "orchestrator").length, "сообщение", "сообщения", "сообщений") : plural(n, "вызов", "вызова", "вызовов");
      return `<div class="agent-node ${cls}" style="--c:${a.color}"><i class="status"></i><div class="ic">${a.short}</div><b>${a.ru}</b><div class="meta">${meta}</div></div>`;
    }).join("")}</div>`;
    const order = STAGES.map(s => s.key); const cur = this.stage;
    let curIdx = ["allocate_quota", "solve_chunk", "validate_chunk", "resolve"].includes(cur) ? 4 : ["validate_all", "repair"].includes(cur) ? 6 : order.indexOf(cur);
    if (!this.running) curIdx = res ? 8 : curIdx;
    $("#stepper").innerHTML = STAGES.map((s, i) => `<div class="st ${i < curIdx ? "done" : i === curIdx && this.running ? "active" : ""}"><i>${i < curIdx ? I.check : i + 1}</i><span>${s.label}${i === 4 && this.chunksTotal ? ` ${this.chunksDone}/${this.chunksTotal}` : ""}</span></div>`).join("");
    let f = 0, text = "";
    if (this.chunksTotal == null) { f = Math.min(0.1, this.events.length / 200); text = this.running ? "подготовка данных и ограничений…" : ""; }
    else { f = 0.1 + 0.7 * this.chunksDone / this.chunksTotal; text = `куски: ${this.chunksDone}/${this.chunksTotal}`; }
    if (["assign_rooms", "validate_all", "repair"].includes(this.stage)) { f = 0.85; text += " · аудитории и финальная проверка"; }
    if (this.stage === "export") { f = 0.95; text += " · экспорт"; }
    if (!this.running) { f = 1; text = res ? `${plural(res.steps ?? 0, "шаг", "шага", "шагов")} · ${plural(this.negotiations, "уступка", "уступки", "уступок")} · ${plural(this.repairs, "перенос", "переноса", "переносов")}` : "нет результата"; }
    const bar = $("#prog"); bar.className = "bar " + (!this.running ? (STATUS_CLS[this.status.status] === "ok" ? "ok" : STATUS_CLS[this.status.status] === "warn" ? "warn" : "bad") : "");
    bar.firstElementChild.style.width = `${Math.round(f * 100)}%`; $("#prog-text").textContent = text;
  }
  renderTabs() {
    $("#tabs").innerHTML = TABS.map(([k, l]) => `<button class="${this.tab === k ? "on" : ""}" data-t="${k}">${k === "live" && this.running ? "Ход выполнения" : l}${k === "conflicts" && this.negotiations ? `<span class="cnt">${this.negotiations}</span>` : ""}${k === "live" && this.running ? '<span class="cnt live">●</span>' : ""}</button>`).join("");
    $$("#tabs button").forEach(b => b.onclick = () => { this.tab = b.dataset.t; this.renderTabs(); });
    this.renderTab();
  }
  renderTab() {
    const body = $("#tab-body"); this.syncHash();
    if (this.tab === "live") { body.innerHTML = this.logShell(); this.logEl = $("#log"); this.logRendered = 0; this.appendLog(true); return; }
    if (!this.results) { body.innerHTML = `<div class="card empty-state">${this.running ? '<span class="spinner big"></span><h3>Агенты работают</h3><p>Результаты появятся после завершения прогона. Пока можно следить за ходом выполнения.</p><button class="btn" id="to-live">Открыть журнал</button>' : `${I.alert}<h3>Результаты недоступны</h3><p>Прогон завершился без расписания.</p>`}</div>`; const b = $("#to-live"); if (b) b.onclick = () => { this.tab = "live"; this.renderTabs(); }; return; }
    ({ summary: () => this.renderSummary(body), timetable: () => this.renderTimetable(body), conflicts: () => this.renderConflicts(body), quality: () => this.renderQuality(body), data: () => this.renderData(body), report: () => this.renderReport(body) })[this.tab]();
  }

  // ---- log tab
  logShell() {
    return `<div class="card"><div class="actions" style="justify-content:space-between;margin-bottom:10px;flex-wrap:wrap">
      <div class="chips" id="log-agents"></div>
      <div class="seg" id="log-kinds">${[["", "всё"], ["message", "сообщения"], ["tool_call", "инструменты"], ["llm_call", "LLM"], ["error", "ошибки"]].map(([k, l]) => `<button class="${this.logFilter.kind === k ? "on" : ""}" data-k="${k}">${l}</button>`).join("")}</div></div>
      <div class="log" id="log"></div>
      <div class="small muted" style="margin-top:8px" id="log-foot"></div></div>`;
  }
  logFoot() {
    const f = $("#log-foot"); if (f) f.textContent = `${plural(this.events.length, "событие", "события", "событий")} · ${plural(sum(Object.values(this.calls)), "вызов", "вызова", "вызовов")} инструментов и LLM · ${plural(this.errors, "ошибка", "ошибки", "ошибок")} · источник: events.jsonl`;
    const chips = $("#log-agents"); const seen = [...new Set(this.events.map(e => e.agent))];
    if (chips && chips.querySelectorAll(".chip").length !== seen.length + 1) {
      chips.innerHTML = `<span class="chip ${this.logFilter.agent ? "" : "on"}" data-a="">все агенты</span>` + seen.map(a => `<span class="chip ${this.logFilter.agent === a ? "on" : ""}" data-a="${esc(a)}"><span class="agent-dot" style="--c:${(AGENT_BY[a] || { color: "var(--a-system)" }).color}"></span>${esc((AGENT_BY[a] || {}).ru || a)}</span>`).join("");
      this.bindLogFilters();
    }
  }
  appendLog(full = false) {
    if (this.tab !== "live" || !this.logEl) return;
    if (full) { this.logEl.innerHTML = ""; this.logRendered = 0; this.bindLogFilters(); }
    const fresh = this.events.slice(this.logRendered); this.logRendered = this.events.length;
    this.logFoot();
    const t0 = this.events.length ? this.events[0].ts : 0;
    const keep = fresh.filter(e => (!this.logFilter.agent || e.agent === this.logFilter.agent) && (!this.logFilter.kind || e.kind === this.logFilter.kind));
    if (!keep.length) { if (full && !this.logEl.children.length) this.logEl.innerHTML = '<div class="empty-state small">событий пока нет</div>'; return; }
    const atBottom = this.logEl.scrollHeight - this.logEl.scrollTop - this.logEl.clientHeight < 40;
    if (this.logEl.querySelector(".empty-state")) this.logEl.innerHTML = "";
    this.logEl.insertAdjacentHTML("beforeend", keep.map(e => this.logLine(e, t0)).join(""));
    while (this.logEl.children.length > 1500) this.logEl.firstElementChild.remove();
    if (atBottom || full) this.logEl.scrollTop = this.logEl.scrollHeight;
  }
  bindLogFilters() {
    $$("#log-agents .chip").forEach(c => c.onclick = () => { this.logFilter.agent = c.dataset.a; this.renderTab(); });
    $$("#log-kinds button").forEach(b => b.onclick = () => { this.logFilter.kind = b.dataset.k; this.renderTab(); });
  }
  logLine(e, t0) {
    const t = `+${(e.ts - t0).toFixed(1)}s`;
    let text = "";
    if (e.kind === "message") {
      if (e.agent === "orchestrator") text = `→ <b>${esc((AGENT_BY[e.to] || {}).ru || e.to)}</b> · ${esc(TASK_RU[e.task] || e.task)}${e.payload && Object.keys(e.payload).length ? ` <span class="muted">${esc(JSON.stringify(e.payload)).slice(0, 160)}</span>` : ""}`;
      else text = `← оркестратору · ${esc(TASK_RU[e.task] || e.task)} <span class="st-${e.intent === "error" ? "error" : "ok"}">${e.intent === "error" ? "ошибка" : "результат"}</span> <span class="muted">${esc(brief(e.payload))}</span>`;
    } else if (e.kind === "tool_call") text = `🔧 ${esc(e.name)} <span class="st-${e.status}">${e.status}</span>${e.attempt > 1 ? ` (попытка ${e.attempt})` : ""}${e.duration_ms != null ? ` <span class="muted">${e.duration_ms} мс</span>` : ""}${e.error ? ` <span class="st-error">${esc(e.error)}</span>` : ""}`;
    else if (e.kind === "llm_call") text = `🧠 ${esc(e.name)} <span class="st-${e.status}">${e.status}</span> <span class="muted">${esc(e.provider)}${e.tokens_in ? ` · ${e.tokens_in}→${e.tokens_out} ток.` : ""}${e.duration_ms != null ? ` · ${e.duration_ms} мс` : ""}</span>${e.error ? ` <span class="st-error">${esc(e.error)}</span>` : ""}`;
    else text = esc(e.msg || JSON.stringify(e));
    return `<div class="ln ${e.kind}"><span class="ts">${t}</span><span>${agentTag(e.agent)}</span><span class="kind">${e.kind}</span><span>${text}</span></div>`;
  }

  // ---- results
  async loadResults() {
    const [tt, details, report] = await Promise.all([api(`/api/runs/${this.id}/timetable`), api(`/api/runs/${this.id}/details`), api(`/api/runs/${this.id}/report`).catch(() => "")]);
    this.tt = tt; this.details = details; this.report = report; this.results = this.status.result || {};
    this.index = indexRows(tt.rows);
    if (!this.ttSel) this.ttSel = tt.groups[0] || "";
    if (this.tab === "live" && tt.rows.length) this.tab = "summary";
    this.renderTabs();
  }
  goto(tab) { this.tab = tab; this.renderTabs(); window.scrollTo({ top: 0, behavior: "smooth" }); }

  renderSummary(body) {
    const res = this.results, d = this.details, s = this.status, soft = res.soft || {}, val = d.validation || {};
    const hv = sum(Object.values(res.hard_violations || {}));
    const st = s.status, rp = d.run_params || {}, warn = d.constraint_warnings || [], issuesW = (d.data_issues || []).filter(i => i.level === "warning").length;
    const verdict = st === "ok" ? ["ok", "Расписание построено", `Все ${plural(this.tt.groups.length, "группа", "группы", "групп")} получили недельное расписание без жёстких нарушений.`]
      : st === "partial" ? ["warn", "Расписание построено частично", `Остались нарушения: ${esc(JSON.stringify(res.hard_violations))}. ${res.failed_chunks && res.failed_chunks.length ? `Куски ${res.failed_chunks.join(", ")} не решены.` : ""}`]
        : ["bad", "Прогон не завершился", esc(res.reason || "см. журнал")];
    body.innerHTML = `
      <div class="verdict ${verdict[0]}"><div class="vi">${verdict[0] === "ok" ? I.check : I.alert}</div><div><h2>${verdict[1]}</h2><p>${verdict[2]}</p></div>
        <div class="actions" style="margin-left:auto"><button class="btn" data-go="timetable">${I.cal} Открыть расписание</button><a class="btn" href="/api/runs/${esc(this.id)}/files/timetable.xlsx">${I.down} Excel</a></div></div>
      <div class="kpi-row card" style="margin:16px 0">${kpi(hv, "жёстких нарушений", hv ? "bad" : "ok")}${kpi(soft.gap_hours_per_group ?? "—", "окон, ч на группу в неделю", "", `всего ${soft.gap_hours_total ?? "—"} ч`)}${kpi(soft.late_hours_18_plus ?? "—", "часов после 18:00")}${kpi(soft.saturday_hours ?? "—", "часов в субботу")}${kpi(soft.precedence_violations ?? "—", "практика раньше лекции")}${kpi(soft.lectures_in_lecture_halls ?? "—", "лекций в лекц. залах")}</div>
      <div class="grid c3">
        <div class="card"><div class="card-head"><h2>Что сделали агенты</h2><button class="btn sm ghost" data-go="live">Журнал ${I.arrow}</button></div>
          <ul class="facts-list">
            <li><b>${this.msgs}</b> сообщений за ${plural(res.steps ?? 0, "шаг", "шага", "шагов")} оркестратора</li>
            <li><b>${sum(Object.values(this.calls))}</b> вызовов инструментов и LLM, макс. доля одного агента <b>${pct((res.load || {}).max_share)}</b></li>
            <li><b>${this.chunksTotal ?? "—"}</b> кусков задачи решено в CP-SAT за <b>${fmtDur(res.wall_s)}</b></li>
            <li><b>${this.negotiations}</b> уступок Negotiator${(rp.relaxed || []).length ? `, ослаблено правил: <b>${rp.relaxed.length}</b>` : ""}</li>
            <li><b>${(d.repairs || []).length}</b> локальных переносов после назначения аудиторий</li>
            <li>LLM: <b>${esc(res.llm || "—")}</b></li></ul>
          <button class="btn sm" data-go="conflicts" style="margin-top:8px">Конфликты и переговоры ${I.arrow}</button></div>
        <div class="card"><div class="card-head"><h2>Рецензия критика</h2>${agentTag("validator")}</div><div class="markdown">${markdown(d.critique || "—")}</div></div>
        <div class="card"><div class="card-head"><h2>Требует внимания</h2></div>
          ${!warn.length && !issuesW && !(rp.relaxed || []).length && !(val.violations || []).length ? `<div class="empty-state small">${I.check}<p>Замечаний нет: все требования поняты, данные в порядке.</p></div>` : `<ul class="nice">
            ${(rp.relaxed || []).map(r => `<li><span class="badge warn">ослаблено</span> ${esc(r)}</li>`).join("")}
            ${warn.map(w => `<li><span class="badge warn">требование</span> ${esc(w)}</li>`).join("")}
            ${issuesW ? `<li><span class="badge warn">данные</span> ${plural(issuesW, "замечание", "замечания", "замечаний")} к входным данным требуют ручной правки</li>` : ""}
            ${(val.violations || []).slice(0, 5).map(v => `<li><span class="badge bad">${esc(v.type)}</span> ${esc(v.detail)}</li>`).join("")}</ul>`}
          <button class="btn sm ghost" data-go="data" style="margin-top:8px">Данные и ограничения ${I.arrow}</button></div></div>`;
    $$("[data-go]", body).forEach(b => b.onclick = () => this.goto(b.dataset.go));
  }

  renderTimetable(body) {
    const tt = this.tt;
    if (!tt.rows.length) { body.innerHTML = `<div class="card empty-state">${I.alert}<h3>Расписание не экспортировано</h3><p>Прогон завершился без результата.</p></div>`; return; }
    const opts = this.ttMode === "group" ? tt.groups : this.ttMode === "room" ? tt.rooms : tt.programs;
    if (!opts.includes(this.ttSel)) this.ttSel = opts[0] || "";
    body.innerHTML = `<div class="card">
      <div class="tt-controls">
        <div class="seg" id="tt-mode">${[["group", "По группе"], ["room", "По аудитории"], ["heat", "Карта программы"]].map(([k, l]) => `<button class="${this.ttMode === k ? "on" : ""}" data-m="${k}">${l}</button>`).join("")}</div>
        <div class="field combo" id="combo"><label>${this.ttMode === "group" ? "Группа" : this.ttMode === "room" ? "Аудитория" : "Программа"}</label><input type="text" id="sel" value="${esc(this.ttSel)}" autocomplete="off"><div class="list" id="sel-list"></div></div>
        <div class="actions"><button class="btn sm" id="prev" title="предыдущая">←</button><button class="btn sm" id="next" title="следующая">→</button><button class="btn sm ghost" id="print">${I.print} Печать</button></div>
        <div class="legend" style="margin-left:auto"><span><i style="background:var(--lecture);border-left:3px solid #3b82f6"></i>лекция</span><span><i style="background:var(--practice);border-left:3px solid #64748b"></i>практика</span><span><i style="background:var(--lab);border-left:3px solid #22c55e"></i>лаборатория</span><span><i style="background:var(--online);border-left:3px dashed #f59e0b"></i>онлайн</span></div>
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
    view.innerHTML = `<div class="print-title">${this.ttMode === "group" ? "Группа" : this.ttMode === "room" ? "Аудитория" : "Программа"} ${esc(this.ttSel)} · триместр ${(this.status.params || {}).term ?? ""} · ${esc(this.id)}</div>` +
      (this.ttMode === "heat" ? this.heatmap(this.ttSel) : this.weekGrid(this.ttMode, this.ttSel));
    this.syncHash();
  }
  weekGrid(mode, sel) {
    const tt = this.tt, H = 12;
    const rows = mode === "group" ? (this.index.byGroup[sel] || []) : (this.index.byRoom[sel] || []);
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
        html += `<td rowspan="${span}"><div class="cell ${cls} ${c.clash ? "clash" : ""}" title="${esc(c.course)}"><b>${esc(c.course)}</b><div class="r"><span>${who}</span><span>${KIND_RU[c.kind] || c.kind}${c.stream > 1 && mode === "group" ? ` · поток ${c.stream}` : ""}</span></div></div></td>`;
      }
      html += "</tr>";
    }
    html += "</tbody></table></div>";
    const hours = rows.length, byDay = new Set(rows.map(r => r.day)).size;
    const gaps = mode === "group" ? gapsOf(rows) : null;
    html += `<div class="small muted" style="margin-top:10px">${plural(hours, "академический час", "академических часа", "академических часов")} в неделю · ${plural(byDay, "учебный день", "учебных дня", "учебных дней")}${gaps != null ? ` · окон: <b>${gaps}</b> ч` : ""}${mode === "room" ? ` · загрузка ${pct(hours / (days * H))}` : ""}</div>`;
    return html;
  }
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
    html += `<div class="legend" style="margin-top:10px"><span><i style="background:#3b82f6"></i>лекция</span><span><i style="background:#94a3b8"></i>практика</span><span><i style="background:#22c55e"></i>лаб.</span><span><i style="background:#f59e0b"></i>онлайн</span><span><i style="background:color-mix(in srgb, var(--bad) 28%, transparent);border:1px solid var(--bad)"></i>окно</span><span class="muted">· ${plural(groups.length, "группа", "группы", "групп")} · окон всего: <b>${totalGaps}</b> ч (${groups.length ? (totalGaps / groups.length).toFixed(1) : 0} ч на группу)</span></div>`;
    return html;
  }

  renderConflicts(body) {
    const d = this.details, conv = d.conversation || [], chunks = d.chunks || [];
    const byChunk = {};
    for (const c of conv) { const idx = c.request && c.request.chunk != null ? c.request.chunk : null; if (idx == null) continue; (byChunk[idx] = byChunk[idx] || []).push(c); }
    const stepHtml = c => {
      const a = AGENT_BY[c.agent] || { color: "var(--a-system)", short: "??", ru: c.agent }; const r = c.reply || {};
      let text = "", quote = "";
      if (c.task === "allocate_quota") text = `<b>${a.ru}</b> выдал квоту аудиторий (${r.mode === "all" ? "весь остаток фонда" : "справедливая доля"})`;
      else if (c.task === "solve_chunk") { text = `<b>${a.ru}</b> · CP-SAT: <span class="badge ${r.ok ? "ok" : "bad"}">${esc(r.status)}</span> за ${r.wall_s ?? "?"} с`; if (r.diagnostics && r.diagnostics.length) quote = r.diagnostics.slice(0, 4).map(esc).join("<br>"); }
      else if (c.task === "validate_chunk") { text = `<b>${a.ru}</b> проверил кусок: <span class="badge ${r.hard_violations === 0 ? "ok" : "bad"}">${r.hard_violations === 0 ? "нарушений нет, решение принято" : `${r.hard_violations} нарушений`}</span>`; if (r.hard_violations) quote = Object.entries(r.by_type || {}).map(([k, v]) => `${esc(k)}: ${v}`).join(", "); }
      else if (c.task === "resolve") { text = `<b>${a.ru}</b> выбрал уступку: <span class="badge warn">${esc(ACTION_RU[r.action] || r.action)}</span>`; quote = esc(r.rationale || ""); }
      else if (c.intent === "error") text = `<b>${esc(a.ru)}</b> · ошибка: ${esc(r.error)}`;
      return `<div class="step" style="--c:${a.color}"><div class="i">${a.short}</div><div class="t">${text}${quote ? `<div class="quote">${quote}</div>` : ""}</div></div>`;
    };
    const chunkCards = Object.keys(byChunk).sort((a, b) => a - b).map(i => {
      const steps = byChunk[i]; const neg = steps.filter(s => s.task === "resolve").length; const ok = steps.some(s => s.task === "validate_chunk" && s.reply.hard_violations === 0);
      return `<div class="chunk-card"><header><div><b>Кусок #${i}</b> <span class="muted small">${esc((chunks[i] || []).join(", "))}</span></div><div class="actions">${neg ? `<span class="badge warn">${plural(neg, "раунд", "раунда", "раундов")} переговоров</span>` : '<span class="badge ok">с первой попытки</span>'}${ok ? '<span class="badge ok">принят</span>' : '<span class="badge bad">не решён</span>'}</div></header><div class="steps">${steps.map(stepHtml).join("")}</div></div>`;
    }).join("");
    const finals = conv.filter(c => c.task === "validate_all" || c.task === "repair");
    const val = d.validation || {}; const rp = d.run_params || {};
    body.innerHTML = `
      <div class="kpi-row card" style="margin-bottom:16px">
        ${kpi(val.hard_violations ?? "—", "жёстких нарушений в итоге", sum(Object.values(val.by_type || {})) ? "bad" : "ok")}
        ${kpi(conv.filter(c => c.task === "resolve").length, "уступок Negotiator", "", `${plural(Object.keys(rp.tried || {}).length, "кусок", "куска", "кусков")} потребовали переговоров`)}
        ${kpi((d.repairs || []).length, "локальных переносов", "", "после назначения аудиторий")}
        ${kpi((rp.relaxed || []).length, "ослабленных правил", (rp.relaxed || []).length ? "warn" : "")}</div>
      <div class="card" style="margin-bottom:16px"><div class="card-head"><h2>Переговоры по кускам задачи</h2></div><div class="muted small" style="margin-bottom:12px">Каждый кусок (несколько программ-курсов) решается отдельно внутри квоты аудиторий. Если решатель не справился или критик нашёл нарушения, Negotiator выбирает уступку и кусок решается заново.</div>
        <div class="grid c2">${chunkCards || '<div class="empty-state small">Нет данных о кусках</div>'}</div></div>
      <div class="grid c2" style="margin-bottom:16px">
        <div class="card"><div class="card-head"><h2>Финальная проверка и ремонт</h2></div><div class="steps">${finals.map(c => { const r = c.reply || {}; const a = AGENT_BY[c.agent]; return `<div class="step" style="--c:${a.color}"><div class="i">${a.short}</div><div class="t">${c.task === "validate_all" ? `<b>${a.ru}</b> · итог${c.request.final ? " (финал)" : ""}: <span class="badge ${r.hard_violations ? "bad" : "ok"}">${r.hard_violations} нарушений</span> ${r.by_type && Object.keys(r.by_type).length ? `<span class="muted small">${esc(JSON.stringify(r.by_type))}</span>` : ""}` : `<b>${a.ru}</b> · ремонт: перенесено ${r.moved}, не удалось ${(r.failed || []).length}`}</div></div>`; }).join("") || '<div class="muted">—</div>'}</div>
          ${(d.repairs || []).length ? `<h3 class="h-sm" style="margin-top:14px">Переносы</h3><div style="max-height:260px;overflow:auto">${(d.repairs || []).slice(0, 200).map(m => `<div class="violation"><span class="mono">${slotLabel(m.from)} → ${slotLabel(m.to)}</span><span>${esc(m.session)} ${m.room ? `<span class="muted">· ${esc(m.room)}</span>` : ""}</span></div>`).join("")}</div>` : ""}</div>
        <div class="card"><div class="card-head"><h2>Ослабленные правила и остаточные нарушения</h2></div>
          ${(rp.relaxed || []).length ? `<div class="muted small">Negotiator перевёл эти жёсткие требования в мягкие (штраф вместо запрета):</div><ul class="nice">${rp.relaxed.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : '<div class="muted small">Пользовательские правила не ослаблялись.</div>'}
          ${(rp.saturday_chunks || []).length ? `<div class="small">Суббота открыта для кусков: ${rp.saturday_chunks.join(", ")}</div>` : ""}
          ${(val.violations || []).length ? `<h3 class="h-sm" style="margin-top:14px">Нарушения в итоговом расписании</h3><div style="max-height:300px;overflow:auto">${val.violations.map(v => `<div class="violation"><span class="badge bad">${esc(v.type)}</span><span>${esc(v.detail)}</span></div>`).join("")}</div>` : `<div style="margin-top:14px"><span class="badge ok">${I.check} Итоговое расписание без жёстких нарушений</span></div>`}</div></div>
      ${d.critique ? `<div class="card"><div class="card-head"><h2>Рецензия критика</h2>${agentTag("validator")}</div><div class="markdown">${markdown(d.critique)}</div></div>` : ""}`;
  }

  renderQuality(body) {
    const res = this.results, soft = res.soft || {}, load = (res.load || {}).agents || {}, tt = this.tt;
    const hv = sum(Object.values(res.hard_violations || {}));
    const dayHours = Array(6).fill(0), hourHist = Array(12).fill(0);
    tt.rows.forEach(r => { dayHours[r.day]++; hourHist[r.hour]++; });
    const roomUse = {}; tt.rows.forEach(r => { if (r.room !== "online" && r.room !== "UNASSIGNED") roomUse[`${r.room}|${r.day}|${r.hour}`] = 1; });
    const roomHours = {}; Object.keys(roomUse).forEach(k => { const room = k.split("|")[0]; roomHours[room] = (roomHours[room] || 0) + 1; });
    const topRooms = Object.entries(roomHours).sort((a, b) => b[1] - a[1]).slice(0, 10);
    const progGaps = {}; for (const [g, rows] of Object.entries(this.index.byGroup)) { const p = this.index.progOf[g]; (progGaps[p] = progGaps[p] || []).push(gapsOf(rows)); }
    const progRows = Object.entries(progGaps).map(([p, arr]) => [p, arr.length, (sum(arr) / arr.length).toFixed(1)]).sort((a, b) => b[2] - a[2]);
    const maxDay = Math.max(...dayHours, 1), maxHour = Math.max(...hourHist, 1);
    body.innerHTML = `
      <div class="kpi-row card" style="margin-bottom:16px">
        ${kpi(hv, "жёстких нарушений", hv ? "bad" : "ok")}${kpi(soft.gap_hours_per_group ?? "—", "окон, ч на группу в неделю", "", `всего ${soft.gap_hours_total ?? "—"} ч`)}${kpi(soft.late_hours_18_plus ?? "—", "часов после 18:00")}${kpi(soft.saturday_hours ?? "—", "часов в субботу")}${kpi(soft.precedence_violations ?? "—", "практика раньше лекции")}${kpi(soft.lectures_in_lecture_halls ?? "—", "лекций в лекц. залах")}</div>
      <div class="grid c3">
        <div class="card"><div class="card-head"><h2>Нагрузка агентов</h2></div><div class="muted small" style="margin-bottom:10px">Доля вызовов инструментов и LLM. Требование ТЗ: ни один агент не делает больше 40 %.</div>
          <div class="hbars">${Object.entries(load).map(([a, v]) => `<div class="row"><span>${agentTag(a)}</span><div class="bar ${v.share > 0.4 ? "bad" : ""}"><i style="width:${v.share * 100}%"></i></div><span class="v">${v.calls} · ${pct(v.share)}</span></div>`).join("")}</div>
          <div class="small" style="margin-top:10px">макс. доля <b>${pct((res.load || {}).max_share)}</b> · всего вызовов ${(res.load || {}).total_calls ?? "—"} · шагов оркестратора ${res.steps ?? "—"} · ${fmtDur(res.wall_s)}</div></div>
        <div class="card"><div class="card-head"><h2>Часы по дням</h2></div><div class="hbars">${dayHours.slice(0, 6).map((n, d) => n || d < 5 ? `<div class="row"><span>${DAY_RU[d]}</span><div class="bar"><i style="width:${n / maxDay * 100}%"></i></div><span class="v">${n}</span></div>` : "").join("")}</div>
          <h3 class="h-sm" style="margin:16px 0 8px">Часы по времени дня</h3><div class="hbars">${hourHist.map((n, h) => `<div class="row"><span class="small">${esc(tt.times[h])}</span><div class="bar ${h >= 10 ? "warn" : ""}"><i style="width:${n / maxHour * 100}%"></i></div><span class="v">${n}</span></div>`).join("")}</div></div>
        <div class="card"><div class="card-head"><h2>Окна по программам</h2></div><table class="tbl"><tr><th>программа</th><th class="num">групп</th><th class="num">окон ч/группу</th></tr>${progRows.map(r => `<tr><td>${esc(r[0])}</td><td class="num">${r[1]}</td><td class="num">${r[2]}</td></tr>`).join("")}</table>
          <h3 class="h-sm" style="margin:16px 0 8px">Самые загруженные аудитории</h3><table class="tbl">${topRooms.map(([r, n]) => `<tr><td>${esc(r)}</td><td class="num">${n} ч · ${pct(n / 60)}</td></tr>`).join("")}</table></div></div>`;
  }

  renderData(body) {
    const d = this.details, p = this.status.params || {}, ds = d.demand_summary || {}, specs = d.constraint_specs || [], warn = d.constraint_warnings || [];
    const reqs = p.requirements || [], relaxed = (d.run_params || {}).relaxed || [];
    body.innerHTML = `
      <div class="grid c2" style="margin-bottom:16px">
        <div class="card"><div class="card-head"><h2>Требования → JSON-правила</h2>${agentTag("constraints")}</div>
          ${reqs.length ? `<ul class="nice">${reqs.map(r => `<li>${esc(r)}</li>`).join("")}</ul>` : '<div class="muted small">Требований не задано — действуют только встроенные правила вуза.</div>'}
          ${specs.length ? `<div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th>Тип</th><th>Жёсткое</th><th>Как понято</th><th>Источник</th></tr></thead><tbody>${specs.map(s => `<tr><td><code class="key">${esc(s.kind)}</code></td><td>${s.hard ? "да" : `нет, вес ${s.weight}`}${relaxed.includes(s.source) ? ' <span class="badge warn">ослаблено</span>' : ""}</td><td>${esc(describeSpec(s))}</td><td class="small muted">${esc(s.source)}</td></tr>`).join("")}</tbody></table></div>` : ""}
          ${warn.length ? `<div style="margin-top:10px">${warn.map(w => `<div class="small" style="color:var(--warn)">${I.alert} ${esc(w)}</div>`).join("")}</div>` : ""}</div>
        <div class="card"><div class="card-head"><h2>Качество входных данных</h2>${agentTag("data")}</div><div class="markdown" style="margin-bottom:10px">${markdown(d.data_issues_text || "")}</div>
          ${(d.data_issues || []).length ? `<div class="tbl-wrap" style="max-height:320px;overflow:auto"><table class="tbl"><thead><tr><th>Уровень</th><th>Где</th><th>Что</th></tr></thead><tbody>${d.data_issues.map(i => `<tr><td><span class="badge ${i.level === "fixed" ? "ok" : "warn"}">${i.level === "fixed" ? "исправлено" : "внимание"}</span></td><td class="small">${esc(i.where)}</td><td class="small">${esc(i.issue)}</td></tr>`).join("")}</tbody></table></div>` : ""}</div></div>
      <div class="card"><div class="card-head"><h2>Спрос: единицы декомпозиции (программа-курс)</h2>${agentTag("scheduler")}</div><div class="muted small" style="margin-bottom:8px">Источник: ${esc(p.dataset_path || "")}</div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Единица</th><th class="num">Групп</th><th class="num">Триместр</th><th>Курсы</th><th class="num">Кусок</th></tr></thead><tbody>${Object.entries(ds).map(([u, v]) => `<tr><td><b>${esc(u)}</b></td><td class="num">${v.groups}</td><td class="num">${v.trimester}</td><td class="small">${v.courses.map(esc).join(" · ")}</td><td class="num">#${(d.chunks || []).findIndex(c => c.includes(u))}</td></tr>`).join("")}</tbody></table></div></div>`;
  }

  renderReport(body) {
    const files = [["timetable.xlsx", "Excel: расписание, аудитории, проблемы данных, нагрузка, качество"], ["timetable.csv", "CSV в длинном формате"], ["timetable.html", "Автономный HTML-просмотрщик"], ["report.md", "Отчёт о прогоне (Markdown)"], ["events.jsonl", "Лог всех сообщений, инструментов и LLM"], ["result.json", "Машиночитаемый итог"], ["state.sqlite", "Blackboard: состояние и сообщения"]];
    body.innerHTML = `<div class="cols" style="align-items:start"><div class="card" style="flex:2"><div class="card-head"><h2>report.md</h2>${agentTag("reporter")}</div><div class="markdown">${this.report ? markdown(this.report) : '<div class="muted">отчёт не найден</div>'}</div></div>
      <div class="card" style="flex:1"><div class="card-head"><h2>Файлы прогона</h2></div><div class="file-list">${files.map(([f, d]) => `<a href="/api/runs/${esc(this.id)}/files/${f}">${I.file}<span><b>${f}</b><br><span class="small muted">${d}</span></span>${I.down}</a>`).join("")}</div>
        <div class="small muted" style="margin-top:12px">runs/${esc(this.id)}/</div></div></div>`;
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
