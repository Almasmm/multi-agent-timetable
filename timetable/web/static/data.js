/* University data desk (#/data[/<category>]): the workbook the agents use, as four categories - rooms, groups,
   curriculum, time grid. Each category has its own window: upload (Excel or CSV) with a preview, a spreadsheet
   editor, and a record card for create / read / update / delete. One template covers every category.
   Loaded before app.js: everything that uses app.js helpers ($, I, api, toast, page...) runs inside functions. */
"use strict";

const DATA_CATS = [
  ["overview", "Сводка", "dashboard", ""],
  ["rooms", "Аудитории", "door", "K — компьютерные классы для лабораторных"],
  ["groups", "Группы", "users", "IT-2301 → программа IT"],
  ["courses", "Учебный план", "layers", "Часы за триместр"],
  ["timeslots", "Сетка времени", "clock", "12 пар, Пн–Сб"],
];
const DATA_EDITABLE = ["rooms", "groups", "courses", "timeslots"];
// sheets collected for the full timetable (teachers, load...): kept in the workbook, used by the agents later
const EXTRA_RU = { Teachers: ["Преподаватели", "users"], TeacherLoad: ["Нагрузка", "layers"], TeacherUnavailability: ["Занятость преподавателей", "clock"],
  Subgroups: ["Подгруппы", "group"], Electives: ["Выбор студентов", "check"], RoomUnavailability: ["Закрытые аудитории", "door"],
  Calendar: ["Календарь", "calendar"], Holidays: ["Праздники", "cal"], Settings: ["Параметры вуза", "sliders"] };
const ROOM_TYPE_RU = { L: "Лекционные", P: "Практические", K: "Компьютерные классы" };
const dataUi = { tab: "overview", ed: null, term: 1, lastHash: location.hash, grid: null };
const encId = id => encodeURIComponent(id);
const sameVal = (a, b) => String(a ?? "") === String(b ?? "");
const lowKey = s => String(s ?? "").trim().toUpperCase();
const DATA_KEY = {
  rooms: r => lowKey(r.room_code), groups: r => lowKey(r.group_code), timeslots: r => String(r.pair),
  courses: r => `${lowKey(r.program)}|${lowKey(r.course_name)}|${r.trimester ?? ""}`,
};
const rowTitle = (t, r) => !r ? "" : t === "rooms" ? r.room_code : t === "groups" ? r.group_code : t === "timeslots" ? `${r.pair}-я пара` : r.course_name;

// ------------------------------------------------------------------ state
async function renderData(tab) {
  dataUi.tab = tab && DATA_CATS.some(c => c[0] === tab) ? tab : "overview";
  $("#top-title").textContent = DATA_CATS.find(c => c[0] === dataUi.tab)[1];
  if (!dataUi.ed) {
    const o = await api("/api/data/working");
    if (currentPage !== "data") return;
    dataLoad(o);
  }
  drawDataPage();
}

function dataLoad(o) {
  let n = 0;
  const rows = {}, base = {};
  for (const t of DATA_EDITABLE) {
    rows[t] = o.tables[t].map(r => ({ ...r, _id: ++n }));
    base[t] = new Map(rows[t].map(r => [r._id, { ...r }]));
  }
  dataUi.ed = { id: o.dataset.id, ds: o.dataset, spec: o.spec, rows, base, nextId: n, undo: [], redo: [], history: o.history,
    views: {}, fresh: new Set(), checkSeq: 0, checking: false, cells: new Map(), rowIssues: new Map(), check: null,
    extraCounts: o.extra || {}, extra: null };
  dataSetCheck(o.check, rows);
}

function dataSetCheck(chk, posted) {
  const ed = dataUi.ed, cells = new Map(), rowIssues = new Map();
  for (const c of chk.cells) {
    const r = (posted[c.t] || [])[c.r]; if (!r) continue;
    const k = `${c.t}|${r._id}|${c.c}`, prev = cells.get(k);
    if (!prev || prev.level !== "error") cells.set(k, c);
    c._id = r._id;
  }
  for (const i of chk.issues) {
    const r = i.t && i.r != null ? (posted[i.t] || [])[i.r] : null; if (!r) continue;
    i._id = r._id; const k = `${i.t}|${r._id}`;
    if (!rowIssues.has(k)) rowIssues.set(k, []);
    rowIssues.get(k).push(i);
  }
  Object.assign(ed, { check: chk, cells, rowIssues });
}

const dataPayload = rows => Object.fromEntries(DATA_EDITABLE.map(t => [t, rows[t].map(r => {
  const o = {}; for (const c of dataUi.ed.spec[t].cols) o[c.key] = r[c.key] ?? null; return o;
})]));

let dataCheckTimer = null;
const dataRecheck = () => { clearTimeout(dataCheckTimer); dataCheckTimer = setTimeout(dataRecheckNow, 350); };
async function dataRecheckNow() {
  clearTimeout(dataCheckTimer);
  const ed = dataUi.ed; if (!ed) return;
  const posted = Object.fromEntries(DATA_EDITABLE.map(t => [t, ed.rows[t].slice()]));
  const seq = ++ed.checkSeq; ed.checking = true; drawSaveBar();
  try {
    const chk = await post(`/api/data/${encId(ed.id)}/check`, { tables: dataPayload(posted) });
    if (seq !== ed.checkSeq || dataUi.ed !== ed) return;
    dataSetCheck(chk, posted);
  } catch (e) { toast(e.message, true); }
  if (seq !== ed.checkSeq || dataUi.ed !== ed) return;
  ed.checking = false; dataRefresh();
}

/** Every change goes through here: one undo step per table, then a server check. */
function dataMutate(t, fn) {
  const ed = dataUi.ed;
  ed.undo.push({ t, rows: ed.rows[t].map(r => ({ ...r })) });
  if (ed.undo.length > 100) ed.undo.shift();
  ed.redo = [];
  fn();
  dataRecheck(); dataRefresh();
}

function dataStats(t = null, ed = dataUi.ed) {
  const st = { cells: 0, added: 0, removed: 0 };
  if (!ed) return st;
  for (const tt of t ? [t] : DATA_EDITABLE) {
    const base = ed.base[tt]; let kept = 0;
    for (const r of ed.rows[tt]) {
      const b = base.get(r._id);
      if (!b) { st.added++; continue; }
      kept++;
      for (const c of ed.spec[tt].cols) if (!sameVal(b[c.key], r[c.key])) st.cells++;
    }
    st.removed += base.size - kept;
  }
  return st;
}
const dataDirty = () => { const s = dataStats(); return s.cells + s.added + s.removed > 0 || !!(dataUi.ed && dataUi.ed.extra); };
const tableIssues = t => {
  const ed = dataUi.ed, errs = ed.check.cells.filter(c => c.t === t && c.level === "error").length;
  const warns = ed.check.cells.filter(c => c.t === t && c.level !== "error").length + ed.check.issues.filter(i => i.t === t && i.level === "warning").length;
  return { errs, warns };
};

// ------------------------------------------------------------------ page
function drawDataPage() {
  const t = dataUi.tab, ov = t === "overview", cat = DATA_CATS.find(c => c[0] === t), spec = ov ? null : dataUi.ed.spec[t];
  const file = `<input type="file" id="d-file" accept=".xlsx,.csv" hidden>`;
  const head = ov
    ? pageHead("Данные вуза", "Всё, по чему агенты строят расписание",
      `<a class="btn" href="/api/data/template" download title="Одна книга Excel со всеми категориями">${I.down} Шаблон</a><button class="btn primary" id="d-upall">${I.upload} Загрузить всё</button>${file}`)
    : pageHead(cat[1], cat[3],
      `<button class="btn" id="d-up">${I.upload} Загрузить</button>${spec.fixed ? "" : `<button class="btn primary" id="dw-add">${I.plus} Добавить</button>`}${file}`,
      [["Данные вуза", "#/data/overview", true], [cat[1]]]);
  $("#main").innerHTML = page(head, `
    ${ov ? '<div class="d-src" id="d-src"></div>' : '<div class="kpi-row t-stats" id="t-stats"></div><div id="dw-note"></div>'}
    <div id="d-pane"></div>
    <div class="savebar" id="savebar"></div>`);
  const up = $("#d-upall") || $("#d-up"); up.onclick = () => dataPick(ov ? null : t);
  const add = $("#dw-add"); if (add) add.onclick = () => dataRecord(t, null);
  $("#d-file").onchange = e => { const f = e.target.files[0], tt = e.target.dataset.table || null; e.target.value = ""; if (f) dataImport(f, tt); };
  if (ov) drawSource(); else drawTableStats();
  drawPane(); drawSaveBar(); syncDataNav();
}

// numbers of one category, recomputed from the rows in the editor (so unsaved edits count too)
function drawTableStats() {
  const el = $("#t-stats"), ed = dataUi.ed, t = dataUi.tab; if (!el || !ed || t === "overview") return;
  const rows = ed.rows[t], sumOf = k => rows.reduce((a, r) => a + (typeof r[k] === "number" ? r[k] : 0), 0);
  const { errs, warns } = tableIssues(t), st = dataStats(t), chg = st.cells + st.added + st.removed;
  const issues = kpi(errs || warns, errs ? "ошибок" : "предупреждений", errs ? "bad" : warns ? "warn" : "ok", chg ? `изменено: ${chg}` : "");
  let cards = "";
  if (t === "rooms") {
    const by = k => rows.filter(r => r.room_type === k).length, closed = rows.filter(r => r.available === false).length;
    cards = kpi(rows.length, "аудиторий", "", closed ? `${closed} закрыто` : "все доступны") + kpi(sumOf("capacity"), "мест", "", `до ${Math.max(0, ...rows.map(r => +r.capacity || 0))} в одной`)
      + kpi(by("L"), "лекционных") + kpi(by("P"), "практических") + kpi(by("K"), "компьютерных классов");
  } else if (t === "groups") {
    const progs = new Set(rows.map(r => (/^[A-Za-z]+/.exec(r.group_code || "") || [""])[0]).filter(Boolean));
    const years = [1, 2, 3].map(y => rows.filter(r => r.year === y).length), stud = sumOf("headcount");
    cards = kpi(rows.length, "групп", "", `${progs.size} программ`) + kpi(stud, "студентов", "", rows.length ? `в среднем ${Math.round(stud / rows.length)}` : "")
      + kpi(years[0], "1 курс") + kpi(years[1], "2 курс") + kpi(years[2], "3 курс");
  } else if (t === "courses") {
    const progs = new Set(rows.map(r => r.program).filter(Boolean)), online = rows.filter(r => r.delivery_mode === "online").length;
    const none = rows.filter(r => !["lecture_slots", "practice_slots", "lab_slots"].some(k => typeof r[k] === "number" && r[k] > 0)).length;
    cards = kpi(rows.length, "дисциплин", "", `${progs.size} программ`) + kpi(sumOf("lecture_slots"), "ч лекций", "", "за триместр, все программы")
      + kpi(sumOf("practice_slots"), "ч практик") + kpi(sumOf("lab_slots"), "ч лабораторных") + kpi(online, "онлайн", "", none ? `${none} без часов` : "");
  } else {
    const len = rows.map(r => (minutes(r.end) ?? 0) - (minutes(r.start) ?? 0)).filter(x => x > 0);
    cards = kpi(rows.length, "пар в день", "", "Пн–Сб") + kpi(esc((rows[0] || {}).start || "—"), "начало") + kpi(esc((rows[rows.length - 1] || {}).end || "—"), "конец")
      + kpi(len.length ? `${Math.min(...len)}–${Math.max(...len)}` : "—", "минут в паре");
  }
  el.innerHTML = cards + issues;
}

// sidebar group «Данные вуза»: active page + a dot when a category has errors (red) or unsaved changes (amber)
function syncDataNav() {
  const ed = dataUi.ed;
  $$('#nav a[data-route="data"]').forEach(a => a.classList.toggle("active", currentPage === "data" && a.dataset.tab === dataUi.tab));
  for (const t of DATA_EDITABLE) {
    const el = document.getElementById(`nd-${t}`); if (!el) continue;
    if (!ed || !ed.check) { el.hidden = true; continue; }
    const { errs } = tableIssues(t), st = dataStats(t), chg = st.cells + st.added + st.removed;
    el.hidden = !(errs || chg); el.className = `dot ${errs ? "bad" : "chg"}`; el.title = errs ? `ошибок: ${errs}` : `изменено: ${chg}`;
  }
}

function dataPick(table) {
  const f = $("#d-file"); if (!f) return;
  f.dataset.table = table || ""; f.accept = table ? ".xlsx,.csv" : ".xlsx"; f.click();
}

function drawSource() {
  const ed = dataUi.ed, d = ed.ds, el = $("#d-src"); if (!el) return;
  const n = ed.history.length;
  el.innerHTML = `<span class="ds-ic">${I.bot}</span>
    <span class="ds-t">Файл агентов: <b class="mono">${esc(d.id)}</b>${d.editable
      ? ` · сохранён ${fmtDate(d.mtime)}`
      : ' · <span class="muted">исходный файл, правки сохранятся в копию</span>'}</span>
    <span class="actions">${n ? `<button class="btn sm subtle" id="d-hist">${I.history} История · ${n}</button>` : ""}<a class="btn sm subtle" href="/api/data/${encId(d.id)}/download" download>${I.down} Скачать .xlsx</a></span>`;
  const h = $("#d-hist", el); if (h) h.onclick = dataHistory;
}

function dataTab(tab) {
  if (dataUi.grid && dataUi.grid.commit) dataUi.grid.commit();
  dataUi.tab = tab;
  history.replaceState(null, "", `#/data/${tab}`); dataUi.lastHash = location.hash;
  $("#top-title").textContent = (DATA_CATS.find(c => c[0] === tab) || [0, "Данные вуза"])[1];
  drawDataPage(); $("#work").scrollTo(0, 0);
}

function drawPane() {
  const host = $("#d-pane"); if (!host) return;
  dataUi.grid = null;
  if (dataUi.tab === "overview") return dataOverview(host);
  const t = dataUi.tab;
  host.innerHTML = `<section class="card dw"><div class="dw-grid" id="dw-grid"></div></section>`;
  drawWindowNote();
  gridMount($("#dw-grid", host), t);
}

function drawWindowNote() {
  const el = $("#dw-note"), t = dataUi.tab; if (!el || t === "overview") return;
  const { errs, warns } = tableIssues(t);
  el.innerHTML = errs || warns ? `<div class="dw-issue ${errs ? "bad" : "warn"}">${errs ? I.alertCircle : I.alert}
      <span><b>${errs ? plural(errs, "ошибка", "ошибки", "ошибок") : plural(warns, "предупреждение", "предупреждения", "предупреждений")}${errs && warns ? ` и ${plural(warns, "предупреждение", "предупреждения", "предупреждений")}` : ""}</b>
      ${errs ? "— сохранение заблокировано" : ""}</span>
      <span class="actions"><button class="btn sm" id="dn-only">Только проблемные</button>${errs ? `<button class="btn sm primary" id="dn-fix">${I.sparkle} Исправить автоматически</button>` : ""}</span></div>` : "";
  const o = $("#dn-only", el); if (o) o.onclick = () => { const v = dataUi.ed.views[t]; if (v) { v.onlyBad = true; v.r = v.ar = 0; } drawPane(); };
  const f = $("#dn-fix", el); if (f) f.onclick = () => dataAutofix();
}

function dataRefresh() {
  drawSaveBar(); drawWindowNote(); drawTableStats(); syncDataNav();
  if (dataUi.grid) dataUi.grid.redraw();
  else if (dataUi.tab === "overview") drawPane();
}

function drawSaveBar() {
  const ed = dataUi.ed, el = $("#savebar"); if (!el || !ed) return;
  const st = dataStats(), dirty = st.cells + st.added + st.removed > 0 || !!ed.extra, errs = ed.check ? ed.check.errors : 0;
  const parts = [st.cells && plural(st.cells, "ячейка изменена", "ячейки изменены", "ячеек изменено"),
    st.added && `+${plural(st.added, "строка", "строки", "строк")}`, st.removed && `−${plural(st.removed, "строка", "строки", "строк")}`, ed.extra && "доп. листы"].filter(Boolean);
  el.className = `savebar${dirty ? " on" : ""}`;
  el.innerHTML = `<span class="sb-state ${ed.checking ? "" : errs ? "bad" : "ok"}">${ed.checking ? '<span class="spinner"></span> проверка…' : errs ? `${I.alertCircle} ${plural(errs, "ошибка", "ошибки", "ошибок")}` : `${I.checkCircle} данные корректны`}</span>
    <span class="sb-what">${dirty ? parts.join(" · ") : "Всё сохранено"}</span>
    <span class="actions">${ed.undo.length ? `<button class="btn sm subtle" id="sb-undo" title="Отменить (Ctrl+Z)">${I.undo}</button>` : ""}
      ${dirty ? `<button class="btn sm" id="sb-reset">Отменить всё</button>` : ""}
      <button class="btn sm primary" id="sb-save" ${dirty && !errs ? "" : "disabled"}>${I.check} Сохранить<kbd>Ctrl S</kbd></button></span>`;
  const on = (s, fn) => { const b = $(s, el); if (b) b.onclick = fn; };
  on("#sb-save", dataSave); on("#sb-undo", () => dataUndo());
  on("#sb-reset", () => {
    if (!confirm("Отменить все несохранённые правки?")) return;
    for (const t of DATA_EDITABLE) ed.rows[t] = [...ed.base[t].values()].map(r => ({ ...r }));
    ed.undo = []; ed.redo = []; ed.fresh.clear(); ed.extra = null; dataRecheck(); dataRefresh();
  });
}

async function dataSave() {
  const ed = dataUi.ed; if (!ed) return;
  if (dataUi.grid) dataUi.grid.commit();
  const st = dataStats(); if (!(st.cells + st.added + st.removed) && !ed.extra) return;
  const btn = $("#sb-save"); if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Сохранение…'; }
  const note = [st.cells && `${st.cells} яч.`, st.added && `+${st.added} стр.`, st.removed && `−${st.removed} стр.`].filter(Boolean).join(", ");
  const posted = Object.fromEntries(DATA_EDITABLE.map(t => [t, ed.rows[t].slice()]));
  const wasId = ed.id;
  try {
    const r = await api(`/api/data/${encId(ed.id)}`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables: dataPayload(posted), extra: ed.extra || null, base_mtime: ed.ds.mtime, note: `правка: ${note || "доп. листы"}` }) });
    ed.ds = r.dataset; ed.id = r.dataset.id; ed.history = r.history; ed.extraCounts = r.extra || ed.extraCounts; ed.extra = null;
    for (const t of DATA_EDITABLE) ed.base[t] = new Map(ed.rows[t].map(x => [x._id, { ...x }]));
    ed.undo = []; ed.redo = []; ed.fresh.clear();
    dataSetCheck(r, posted);
    toast(wasId !== ed.id ? `Создана рабочая копия ${ed.id} — агенты переключились на неё и перегенерируют расписание`
      : r.dataset.active ? "Сохранено. Наблюдатель заметит новые данные и перегенерирует расписание" : `Сохранено в ${ed.id}`);
    drawSource(); dataRefresh();
  } catch (e) { toast(e.message, true); drawSaveBar(); }
}

function dataUndo(redo = false) {
  const ed = dataUi.ed, from = redo ? ed.redo : ed.undo, to = redo ? ed.undo : ed.redo;
  const u = from.pop(); if (!u) return;
  to.push({ t: u.t, rows: ed.rows[u.t].map(r => ({ ...r })) });
  ed.rows[u.t] = u.rows;
  if (u.t !== dataUi.tab) toast(`${redo ? "Повторено" : "Отменено"} в разделе «${ed.spec[u.t].title}»`);
  dataRecheck(); dataRefresh();
}

function dataHistory() {
  const ed = dataUi.ed, d = ed.ds;
  openCtx("История", d.id, `<p class="hint">Последние 30 версий</p>
    <ul class="ds-hist">${ed.history.map(h => `<li><span class="hh-ic">${I.history}</span><span class="hh-t"><b>Как было до ${fmtDate(h.saved)}</b><small>затем — ${esc(h.note)} · ${h.size_kb} КБ</small></span>
      <button class="btn sm" data-stamp="${esc(h.stamp)}" title="Вернуть файл к этому состоянию">${I.undo} Вернуть</button></li>`).join("")}</ul>`);
  $$("#ctx-body [data-stamp]").forEach(b => b.onclick = async () => {
    if (dataDirty() && !confirm("Несохранённые правки пропадут. Продолжить?")) return;
    if (!confirm("Вернуть файл к этой версии? Текущее состояние тоже попадёт в историю.")) return;
    try { await post(`/api/data/${encId(d.id)}/restore`, { stamp: b.dataset.stamp }); closeCtx(); dataUi.ed = null; toast("Файл восстановлен"); await renderData(dataUi.tab); }
    catch (err) { toast(err.message, true); }
  });
}

// ------------------------------------------------------------------ overview window
function dataOverview(host) {
  const ed = dataUi.ed, s = ed.check.summary, errs = ed.check.errors;
  const warns = ed.check.issues.filter(i => i.level === "warning").length + ed.check.cells.filter(c => c.level !== "error").length;
  const progs = Object.keys(s.load).sort(), years = [...new Set(progs.flatMap(p => Object.keys(s.load[p]).map(Number)))].sort((a, b) => a - b);
  const term = dataUi.term, maxH = Math.max(1, ...progs.flatMap(p => Object.values(s.load[p]).map(c => c.hours[term - 1])));
  const types = Object.entries(s.rooms_by_type).sort((a, b) => b[1].seats - a[1].seats), maxSeats = Math.max(1, ...types.map(([, x]) => x.seats));
  host.innerHTML = `
    <div class="kpi-row d-kpis">${kpi(s.programs, "программ")}${kpi(s.groups, "групп", "", plural(s.students, "студент", "студента", "студентов"))}
      ${kpi(s.rooms, "аудиторий", "", `${s.seats} мест${s.unavailable_rooms ? ` · ${s.unavailable_rooms} закрыто` : ""}`)}${kpi(s.courses, "дисциплин", "", `${s.weeks} недель в триместре`)}
      ${kpi(errs || warns, errs ? "ошибок" : "предупреждений", errs ? "bad" : warns ? "warn" : "ok", s.issues.fixed ? `${s.issues.fixed} исправит загрузчик` : "")}</div>
    <div class="grid d-ov">
      <section class="card"><div class="card-head"><h2>${I.layers} Нагрузка по программам</h2>
        <div class="seg" id="ov-term">${[1, 2, 3].map(k => `<button data-v="${k}" class="${term === k ? "on" : ""}">${k}-й триместр года</button>`).join("")}</div>
        <p class="hint">ч/нед на группу · нажмите — учебный план</p></div>
        <div class="load-wrap"><table class="load"><thead><tr><th>Программа</th>${years.map(y => `<th>${y} курс</th>`).join("")}</tr></thead><tbody>
        ${progs.map(p => `<tr><th class="mono">${esc(p)}</th>${years.map(y => {
          const c = s.load[p][y]; if (!c) return '<td class="none">—</td>';
          const h = c.hours[term - 1], tri = (y - 1) * 3 + term;
          return `<td class="${h ? "" : "zero"}" style="--h:${(h / maxH).toFixed(2)}" data-p="${esc(p)}" data-tri="${tri}" title="${esc(p)}, ${y} курс, триместр ${tri}: ${h} ч/нед · ${c.groups} гр · ${c.students} студ."><b>${h || "нет"}</b>${h ? "<small>ч/нед</small>" : ""}<span>${c.groups} гр · ${c.students} ст</span></td>`;
        }).join("")}</tr>`).join("")}</tbody></table></div></section>
      <section class="card"><div class="card-head"><h2>${I.door} Фонд аудиторий</h2></div>
        <div class="rt-list">${types.map(([k, x]) => `<button class="rt" data-rt="${esc(k)}"><span class="rt-k k-${esc(k)}">${esc(k)}</span><span class="rt-t"><b>${ROOM_TYPE_RU[k] || esc(k)}</b><small>${plural(x.rooms, "аудитория", "аудитории", "аудиторий")} · до ${x.max} мест</small></span><span class="rt-n">${x.seats}<small>мест</small></span><span class="rt-bar"><i style="width:${(x.seats / maxSeats * 100).toFixed(1)}%"></i></span></button>`).join("")}</div>
        <p class="hint" style="margin-top:12px">Крупнейшая группа — ${s.biggest_group} студ.</p></section>
    </div>
    <div id="ov-issues"></div>
    ${dataExtraCard()}
    <section class="card d-sheets"><div class="card-head"><h2>${I.table} Единый шаблон</h2><a class="btn sm" href="/api/data/template" download>${I.down} Скачать шаблон</a></div>
      <div class="sheets">${[["Rooms", "Аудитории", "room_code, capacity, room_type (L / P / K), available, floor"], ["Groups", "Группы", "group_code (IT-2301: буквы — программа), department, year, headcount"],
        ["IT, SE, CS…", "Учебный план — лист на программу", "course_name, trimester, credits, lecture_slots, practice_slots, lab_slots (часов за триместр), delivery_mode, lecture_precedes_practice"],
        ["Timeslots", "Сетка времени", "slot_index (или pair), start, end — 12 пар, одинаковых для Пн–Сб"],
        ["Teachers, TeacherLoad…", "Для полного расписания", "преподаватели, нагрузка и занятость, подгруппы, выбор студентов, календарь, параметры"]].map(([n, t, c]) => `<div class="sheet"><span class="mono">${n}</span><b>${t}</b><small>${c}</small></div>`).join("")}</div></section>`;
  bindSeg($("#ov-term"), String(term), v => { dataUi.term = +v; dataOverview(host); });
  $$(".load td[data-p]", host).forEach(td => td.onclick = () => { dataView("courses", { program: td.dataset.p, trimester: td.dataset.tri }); dataTab("courses"); });
  $$(".rt[data-rt]", host).forEach(b => b.onclick = () => { dataView("rooms", { room_type: b.dataset.rt }); dataTab("rooms"); });
  dataIssues($("#ov-issues", host));
}

function dataExtraCard() {
  const ed = dataUi.ed, counts = ed.extra ? Object.fromEntries(Object.entries(ed.extra).map(([k, v]) => [k, Math.max(0, v.length - 1)])) : ed.extraCounts;
  const filled = Object.values(counts).filter(n => n > 0).length;
  return `<section class="card"><div class="card-head"><h2>${I.layers} Для полного расписания</h2><span class="badge ${filled ? "accent" : ""}">${filled} из ${Object.keys(EXTRA_RU).length}</span>
      <p class="hint">Заполняются в шаблоне Excel · агенты начнут учитывать после обновления модели</p></div>
    <div class="ex-grid">${Object.entries(EXTRA_RU).map(([k, [t, ic]]) => `<div class="ex ${counts[k] ? "on" : ""}"><span class="ex-ic">${I[ic]}</span><span><b>${t}</b><small>${counts[k] ? plural(counts[k], "строка", "строки", "строк") : "не заполнено"}</small></span></div>`).join("")}</div></section>`;
}

function dataView(t, f) {
  const ed = dataUi.ed;
  ed.views[t] = { ...(ed.views[t] || {}), q: "", f, onlyBad: false, onlyChg: false, r: 0, c: 0, ar: 0, ac: 0, top: 0 };
}

function dataIssues(host) {
  const ed = dataUi.ed, chk = ed.check;
  const rowNo = (t, id) => ed.rows[t] ? ed.rows[t].findIndex(x => x._id === id) + 1 : 0;
  const colLabel = (t, k) => { const c = ed.spec[t] && ed.spec[t].cols.find(x => x.key === k); return c ? c.label : k; };
  const item = (x, kind) => {
    const t = x.t, id = x._id, r = t && id ? ed.rows[t].find(z => z._id === id) : null;
    const where = t ? `${ed.spec[t].title}${id ? `, строка ${rowNo(t, id)}` : ""}` : x.where;
    return `<li class="iss ${kind}"><span class="iss-dot"></span><span class="iss-t"><b>${esc(x.text)}</b><small>${esc(where)}${x.c ? ` · ${esc(colLabel(t, x.c))}` : ""}${r ? ` · <span class="mono">${esc(rowTitle(t, r) || "")}</span>` : ""}</small></span>
      ${t && id ? `<button class="btn sm" data-go="${t}|${id}|${x.c || ""}">Показать</button>` : t ? `<button class="btn sm" data-go="${t}||">Открыть</button>` : ""}</li>`;
  };
  const errs = chk.cells.filter(c => c.level === "error"), warns = [...chk.cells.filter(c => c.level !== "error"), ...chk.issues.filter(i => i.level === "warning")];
  const fixed = chk.issues.filter(i => i.level === "fixed");
  const sec = (title, list, kind, hint) => list.length ? `<section class="card"><div class="card-head"><h2>${title} <span class="count-n">${list.length}</span></h2>${hint ? `<p class="hint">${hint}</p>` : ""}</div><ul class="iss-list">${list.slice(0, 200).map(x => item(x, kind)).join("")}</ul>${list.length > 200 ? `<p class="hint">и ещё ${list.length - 200}</p>` : ""}</section>` : "";
  host.innerHTML = (errs.length ? `<div class="dw-issue bad">${I.sparkle}<span><b>Часть ошибок исправляется автоматически</b></span><span class="actions"><button class="btn sm primary" id="iss-fix">${I.sparkle} Исправить автоматически</button></span></div>` : "")
    + sec(`${I.xCircle} Ошибки`, errs, "error", "")
    + sec(`${I.alert} Предупреждения`, warns, "warning", "")
    + sec(`${I.sparkle} Загрузчик исправит при чтении`, fixed, "fixed", "");
  $$("[data-go]", host).forEach(b => b.onclick = () => { const [t, id, c] = b.dataset.go.split("|"); dataJump(t, id ? +id : null, c); });
  const fx = $("#iss-fix", host); if (fx) fx.onclick = () => dataAutofix();
}

function dataJump(t, id, colKey) {
  const ed = dataUi.ed; dataView(t, {});
  const v = ed.views[t];
  if (id != null) {
    const r = ed.rows[t].findIndex(x => x._id === id), c = Math.max(0, ed.spec[t].cols.findIndex(x => x.key === colKey));
    Object.assign(v, { r: Math.max(0, r), c, ar: Math.max(0, r), ac: c });
  }
  dataTab(t);
}

async function dataAutofix() {
  const ed = dataUi.ed;
  if (dataUi.grid) dataUi.grid.commit();
  try {
    const res = await post(`/api/data/${encId(ed.id)}/autofix`, { tables: Object.fromEntries(DATA_EDITABLE.map(t => [t, ed.rows[t].map(r => ({ ...r }))])) });
    if (!res.changes.length) { toast("Автоматически исправлять нечего — остальное нужно поправить вручную"); return; }
    for (const t of DATA_EDITABLE) {
      if (!res.changes.some(c => c.t === t)) continue;
      ed.undo.push({ t, rows: ed.rows[t].map(r => ({ ...r })) });
      ed.rows[t] = res.tables[t].map(r => ({ ...r }));
    }
    ed.redo = [];
    const removed = res.changes.filter(c => c.removed).length;
    toast(`Исправлено: ${plural(res.changes.length - removed, "ячейка", "ячейки", "ячеек")}${removed ? `, удалено повторов: ${removed}` : ""}. Отменить — Ctrl+Z`);
    dataRecheck(); dataRefresh();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ record card: create / read / update / delete
function newDataRow(t, from = null, f = {}) {
  const ed = dataUi.ed, r = { _id: ++ed.nextId };
  for (const c of ed.spec[t].cols) r[c.key] = c.default ?? null;
  if (t === "courses") Object.assign(r, { program: f.program || (from && from.program) || null, trimester: f.trimester ? +f.trimester : from ? from.trimester : null, delivery_mode: (from && from.delivery_mode) || "offline" });
  if (t === "groups" && from) Object.assign(r, { year: f.year ? +f.year : from.year, department: from.department });
  if (t === "rooms") Object.assign(r, { room_type: f.room_type || (from && from.room_type) || null, floor: from ? from.floor : null });
  ed.fresh.add(r._id);
  return r;
}

function dataRecord(t, id, preset = null) {
  const ed = dataUi.ed, spec = ed.spec[t], row = id != null ? ed.rows[t].find(r => r._id === id) : null;
  if (id != null && !row) return;
  const v = ed.views[t] || {}, at = dataUi.grid && dataUi.grid.activeRow ? dataUi.grid.activeRow() : null;
  const vals = row ? { ...row } : preset || newDataRow(t, at, v.f || {});
  if (!row && !preset) ed.fresh.delete(vals._id);
  const errOf = k => row ? ed.cells.get(`${t}|${row._id}|${k}`) : null;
  const ri = row ? ed.rowIssues.get(`${t}|${row._id}`) : null;
  const field = c => {
    const val = vals[c.key], e = errOf(c.key), id2 = `rf-${c.key}`;
    let input;
    if (c.readonly) input = `<div class="rf-static mono">${esc(val ?? "—")}</div>`;
    else if (c.type === "bool") input = `<label class="switch"><input type="checkbox" id="${id2}" ${val === true || val == null && c.default ? "checked" : ""}><span class="track"></span><span class="small">${val === false ? "нет" : "да"}</span></label>`;
    else if (c.type === "enum") input = `<select id="${id2}">${c.required ? "" : '<option value="">—</option>'}${val != null && !c.options.some(o => o[0] === val) ? `<option value="${esc(val)}" selected>${esc(val)} (неверно)</option>` : ""}${c.options.map(o => `<option value="${esc(o[0])}" ${o[0] === val ? "selected" : ""}>${o[0].length <= 2 ? `${esc(o[0])} — ` : ""}${esc(o[1])}</option>`).join("")}</select>`;
    else if (c.type === "time") input = `<input type="time" id="${id2}" value="${esc(/^\d\d:\d\d$/.test(val || "") ? val : "")}">`;
    else input = `<input type="text" id="${id2}" value="${esc(val ?? "")}" ${c.type === "int" ? 'inputmode="numeric"' : ""} autocomplete="off" spellcheck="false" ${c.mono ? 'class="mono"' : ""}>`;
    return `<div class="rf ${e ? (e.level === "error" ? "bad" : "warn") : ""}"><label for="${id2}">${esc(c.label)}${c.required ? '<i class="req">*</i>' : ""}</label>${input}
      ${e ? `<small class="rf-err">${esc(e.text)}</small>` : c.help ? `<small class="rf-help">${esc(c.help)}</small>` : ""}</div>`;
  };
  const title = row ? (rowTitle(t, row) || "Без названия") : t === "timeslots" ? "Пара" : "Новая запись";
  openCtx(row ? spec.title : `${spec.title} · добавление`, title,
    `${ri ? `<div class="rf-issues">${ri.map(x => `<p>${I.alert}<span>${esc(x.text)}</span></p>`).join("")}</div>` : ""}
     <form class="rec-form" id="rec-form" autocomplete="off">${spec.cols.map(field).join("")}<button type="submit" hidden></button></form>`,
    `${row && !spec.fixed ? `<button class="btn subtle" id="rec-del">${I.trash} Удалить</button><button class="btn subtle" id="rec-dup" title="Новая запись с теми же значениями">${I.copy} Дублировать</button>` : ""}
     <span style="flex:1"></span><button class="btn" id="rec-cancel">Отмена</button><button class="btn primary" id="rec-ok">${I.check} ${row ? "Сохранить" : "Добавить"}</button>`);
  const read = () => {
    const out = {};
    for (const c of spec.cols) {
      if (c.readonly) { out[c.key] = vals[c.key]; continue; }
      const el = $(`#rf-${c.key}`); if (!el) continue;
      out[c.key] = c.type === "bool" ? el.checked : c.type === "enum" ? (el.value === "" ? null : el.value) : parseTyped(c, el.value);
    }
    return out;
  };
  $$("#rec-form input[type=checkbox]").forEach(cb => cb.onchange = () => { cb.parentElement.querySelector(".small").textContent = cb.checked ? "да" : "нет"; });
  const submit = async () => {
    const data = read();
    let target = row;
    if (row) {
      if (spec.cols.every(c => sameVal(row[c.key], data[c.key]))) { closeCtx(); return; }
      dataMutate(t, () => Object.assign(row, data));
    } else {
      target = { ...vals, ...data, _id: vals._id };
      ed.fresh.add(target._id);
      dataMutate(t, () => { const i = at ? ed.rows[t].indexOf(at) + 1 : ed.rows[t].length; ed.rows[t].splice(i > 0 ? i : ed.rows[t].length, 0, target); });
    }
    if (dataUi.grid) dataUi.grid.focusRow(target._id);
    await dataRecheckNow();
    const bad = spec.cols.some(c => { const e = ed.cells.get(`${t}|${target._id}|${c.key}`); return e && e.level === "error"; });
    if (bad) { toast("В карточке есть ошибки — исправьте подсвеченные поля", true); dataRecord(t, target._id); return; }
    closeCtx();
    toast(row ? "Запись изменена. Нажмите «Сохранить», чтобы агенты её получили" : "Запись добавлена. Нажмите «Сохранить», чтобы агенты её получили");
  };
  $("#rec-form").onsubmit = e => { e.preventDefault(); submit(); };
  $("#rec-ok").onclick = submit;
  $("#rec-cancel").onclick = () => closeCtx();
  const del = $("#rec-del"); if (del) del.onclick = () => {
    if (!confirm(`Удалить «${title}»?`)) return;
    dataMutate(t, () => { ed.rows[t] = ed.rows[t].filter(r => r._id !== row._id); });
    closeCtx(); toast("Запись удалена. Вернуть — Ctrl+Z или «Отменить всё»");
  };
  const dup = $("#rec-dup"); if (dup) dup.onclick = () => { const copy = { ...read(), _id: ++ed.nextId }; dataRecord(t, null, copy); };
  const first = $("#rec-form input:not([type=checkbox]), #rec-form select"); if (first) setTimeout(() => first.focus(), 60);
}

// ------------------------------------------------------------------ spreadsheet editor
function parseTyped(col, s) {
  if (s == null) return null;
  s = String(s).trim(); if (s === "") return null;
  if (col.type === "int") return /^-?\d+([.,]0+)?$/.test(s.replace(/\s/g, "")) ? parseInt(s.replace(/\s/g, ""), 10) : s;
  if (col.type === "bool") { const l = s.toLowerCase(); return ["true", "1", "да", "yes", "+", "✓"].includes(l) ? true : ["false", "0", "нет", "no", "-"].includes(l) ? false : s; }
  if (col.type === "enum") { const l = s.toLowerCase(); const o = col.options.find(o => o[0].toLowerCase() === l || o[1].toLowerCase() === l); return o ? o[0] : s; }
  if (col.type === "time") { const m = /^(\d{1,2})[:.](\d{2})$/.exec(s); return m ? `${m[1].padStart(2, "0")}:${m[2]}` : s; }
  return s;
}
const showVal = (col, v) => v == null ? "" : col.type === "bool" ? (v === true ? "TRUE" : v === false ? "FALSE" : String(v)) : String(v);
function cellHtml(col, v) {
  if (v == null || v === "") return "";
  if (col.type === "bool") return v === true ? `<span class="xb on">${I.check}</span>` : v === false ? '<span class="xb"></span>' : esc(v);
  if (col.type === "enum") {
    const o = col.options.find(o => o[0] === v); if (!o) return esc(v);
    return o[0].length <= 2 ? `<span class="xe xe-${esc(o[0])}">${esc(o[0])}</span><span class="xl">${esc(o[1])}</span>` : `<span class="xe xm-${esc(o[0])}">${esc(o[1])}</span>`;
  }
  return esc(v);
}
const minutes = s => { const m = /^(\d\d):(\d\d)$/.exec(s || ""); return m ? +m[1] * 60 + +m[2] : null; };

function derivedCols(t) {
  const ed = dataUi.ed, weeks = ed.check.summary.weeks || 10;
  if (t === "groups") {
    const progs = new Set(ed.rows.courses.map(r => r.program));
    return [{ key: "_prog", label: "Программа", derived: true, width: 110, mono: true, help: "Определяется по буквам кода группы",
      get: r => (/^[A-Za-z]+/.exec(r.group_code || "") || [""])[0], bad: v => v && !progs.has(v) ? "нет учебного плана этой программы" : "" }];
  }
  if (t === "courses") {
    const w = x => typeof x === "number" && x > 0 ? Math.round(x / weeks) : 0;
    return [{ key: "_week", label: "В неделю", derived: true, width: 130, help: `Лекции · практики · лабораторные, ч/нед (часы за триместр ÷ ${weeks})`,
      get: r => { const a = [w(r.lecture_slots), w(r.practice_slots), w(r.lab_slots)]; return a.some(Boolean) ? a.map(x => x || "–").join(" · ") : ""; } }];
  }
  if (t === "timeslots") {
    return [{ key: "_len", label: "Длительность", derived: true, width: 130, help: "Конец минус начало",
      get: r => { const a = minutes(r.start), b = minutes(r.end); return a != null && b != null && b > a ? `${b - a} мин` : ""; } },
      { key: "_days", label: "Дни", derived: true, width: 120, get: () => "Пн–Сб" }];
  }
  return [];
}

const GRID_FILTERS = {
  rooms: [["room_type", "Все типы", ed => ed.spec.rooms.cols.find(c => c.key === "room_type").options.map(([k, l]) => [k, `${k} — ${l}`])]],
  groups: [["_prog", "Все программы", ed => [...new Set(ed.rows.groups.map(r => (/^[A-Za-z]+/.exec(r.group_code || "") || [""])[0]))].filter(Boolean).sort().map(p => [p, p])],
    ["year", "Все курсы", ed => [...new Set(ed.rows.groups.map(r => r.year))].filter(x => x != null).sort((a, b) => a - b).map(y => [y, `${y} курс`])]],
  courses: [["program", "Все программы", ed => [...new Set(ed.rows.courses.map(r => r.program))].filter(Boolean).sort().map(p => [p, p])],
    ["trimester", "Все триместры", ed => [...new Set(ed.rows.courses.map(r => r.trimester))].filter(x => x != null).sort((a, b) => a - b).map(x => [x, `триместр ${x}`])],
    ["delivery_mode", "Любой формат", () => [["offline", "очно"], ["online", "онлайн"]]]],
  timeslots: [],
};

function gridMount(host, t) {
  const ed = dataUi.ed, spec = ed.spec[t], fixed = !!spec.fixed;
  const cols = [...spec.cols, ...derivedCols(t)];
  const nfz = t === "courses" && innerWidth > 1100 ? 2 : 1, fzLeft = [56];  // frozen key columns, like Excel panes
  for (let j = 1; j < nfz; j++) fzLeft[j] = fzLeft[j - 1] + (cols[j - 1].width || 120);
  const fzAttr = j => j < nfz ? ` style="left:${fzLeft[j]}px"` : "", fzCls = j => j < nfz ? ` fz${j === nfz - 1 ? " fzl" : ""}` : "";
  const v = ed.views[t] = { q: "", f: {}, sort: null, onlyBad: false, onlyChg: false, r: 0, c: 0, ar: 0, ac: 0, top: 0, ...(ed.views[t] || {}) };
  const opt = (val, l, cur) => `<option value="${esc(val)}" ${String(cur ?? "") === String(val) ? "selected" : ""}>${esc(l)}</option>`;
  host.innerHTML = `
    <div class="xg-bar">
      <div class="search">${I.search}<input type="search" id="xg-q" placeholder="Поиск" value="${esc(v.q)}" aria-label="Поиск по таблице"></div>
      ${(GRID_FILTERS[t] || []).map(([k, l, fn]) => `<select data-f="${k}" aria-label="${esc(l)}">${opt("", l, v.f[k])}${fn(ed).map(([a, b]) => opt(a, b, v.f[k])).join("")}</select>`).join("")}
      ${!fixed ? `<label class="switch"><input type="checkbox" id="xg-bad" ${v.onlyBad ? "checked" : ""}><span class="track"></span><span class="small">с проблемами</span></label>` : ""}
      <label class="switch"><input type="checkbox" id="xg-chg" ${v.onlyChg ? "checked" : ""}><span class="track"></span><span class="small">изменённые</span></label>
      <span class="xg-acts"><button class="btn sm" id="xg-card" title="Открыть выделенную строку карточкой (двойной клик по номеру строки)">${I.file} Карточка</button>
        ${fixed ? "" : `<button class="btn sm" id="xg-del" title="Удалить выделенные строки (Ctrl+−)">${I.trash} Удалить</button>`}
        <span class="sep"></span><button class="btn sm subtle" id="xg-undo" title="Отменить (Ctrl+Z)">${I.undo}</button><button class="btn sm subtle" id="xg-redo" title="Повторить (Ctrl+Y)">${I.redo}</button>
        <button class="btn sm subtle" id="xg-csv" title="Выгрузить видимые строки в CSV">${I.down} CSV</button></span>
    </div>
    <div class="xg-wrap" id="xg" tabindex="0" data-keys role="grid" aria-label="${esc(spec.title)}">
      <table class="xg" style="width:${56 + sum(cols.map(c => c.width || 120))}px"><colgroup><col style="width:56px">${cols.map(c => `<col style="width:${c.width || 120}px">`).join("")}<col></colgroup>
        <thead><tr><th class="rn corner" title="Выделить всё (Ctrl+A)"></th>${cols.map((c, j) => `<th data-c="${j}" class="${c.derived || c.readonly ? "dv" : ""}${c.type === "int" ? " num" : ""}${fzCls(j)}"${fzAttr(j)} title="${esc(c.help || c.label)}${c.derived ? " · вычисляется" : ""}">${esc(c.label)}${c.required ? '<i class="req">*</i>' : ""}<span class="ar"></span></th>`).join("")}<th class="fill"></th></tr></thead>
        <tbody id="xg-body"></tbody></table>
      <div class="xg-empty" id="xg-empty" hidden>${I.search}<b>Ничего не найдено</b><span>Измените поиск или фильтры</span></div>
    </div>
    <div class="xg-foot"><span id="xg-count"></span><span class="keys"><kbd>Enter</kbd> правка · <kbd>Ctrl V</kbd> из Excel · <kbd>Ctrl Z</kbd> отмена · двойной клик по № — карточка</span></div>`;
  const wrap = $("#xg", host), body = $("#xg-body", host);
  let view = [], editing = null, dragging = false;

  const val = (c, r) => c.derived ? c.get(r) : r[c.key];
  const issueOf = (r, c) => c.derived ? (c.bad && c.bad(c.get(r)) ? { level: "warning", text: c.bad(c.get(r)) } : null) : ed.cells.get(`${t}|${r._id}|${c.key}`);
  const changed = (r, c) => { if (c.derived) return false; const b = ed.base[t].get(r._id); return !b || !sameVal(b[c.key], r[c.key]); };
  const rowBad = r => ed.rowIssues.has(`${t}|${r._id}`) || cols.some(c => issueOf(r, c));
  const rowChg = r => cols.some(c => changed(r, c));

  const compute = () => {
    const q = v.q.trim().toLowerCase();
    const index = new Map(ed.rows[t].map((r, i) => [r._id, i]));
    view = ed.rows[t].filter(r => {
      if (ed.fresh.has(r._id)) return true;
      for (const [k, want] of Object.entries(v.f)) {
        if (want === "" || want == null) continue;
        const c = cols.find(x => x.key === k), have = c ? val(c, r) : r[k];
        if (String(have ?? "") !== String(want)) return false;
      }
      if (v.onlyBad && !rowBad(r)) return false;
      if (v.onlyChg && !rowChg(r)) return false;
      if (q && !cols.some(c => String(val(c, r) ?? "").toLowerCase().includes(q))) return false;
      return true;
    });
    if (v.sort) {
      const c = cols[v.sort[0]], s = v.sort[1];
      view.sort((a, b) => { const x = val(c, a), y = val(c, b); return (typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), "ru", { numeric: true })) * s || index.get(a._id) - index.get(b._id); });
    }
    v.r = Math.min(v.r, Math.max(0, view.length - 1)); v.ar = Math.min(v.ar, Math.max(0, view.length - 1));
    return index;
  };

  const rowHtml = (r, i, index) => {
    const no = index.get(r._id) + 1, fresh = !ed.base[t].has(r._id), ri = ed.rowIssues.get(`${t}|${r._id}`);
    const rnCls = fresh ? " add" : ri ? (ri.some(x => x.level === "warning") ? " warn" : " info") : "";
    return `<tr data-r="${i}"><th class="rn${rnCls}" title="${esc(ri ? ri.map(x => x.text).join("\n") : fresh ? "новая строка" : "двойной клик — карточка записи")}">${fresh ? "+" : no}</th>${cols.map((c, j) => {
      const is = issueOf(r, c), cls = `${c.type === "int" ? "num" : ""}${c.mono ? " mono" : ""}${c.derived || c.readonly ? " dv" : ""}${is ? (is.level === "error" ? " x-err" : " x-warn") : ""}${changed(r, c) ? " x-chg" : ""}`;
      return `<td data-c="${j}" class="${cls}${fzCls(j)}"${fzAttr(j)}${is ? ` title="${esc(is.text)}"` : ""}>${cellHtml(c, val(c, r))}</td>`;
    }).join("")}<td class="fill"></td></tr>`;
  };

  const draw = () => {
    const index = compute();
    body.innerHTML = view.map((r, i) => rowHtml(r, i, index)).join("");
    $("#xg-empty", host).hidden = view.length > 0;
    $$("thead th[data-c]", host).forEach(th => { const j = +th.dataset.c, s = v.sort && v.sort[0] === j ? v.sort[1] : 0; th.querySelector(".ar").textContent = s > 0 ? "↑" : s < 0 ? "↓" : ""; th.setAttribute("aria-sort", s > 0 ? "ascending" : s < 0 ? "descending" : "none"); });
    const total = ed.rows[t].length;
    $("#xg-count", host).innerHTML = view.length === total ? plural(total, "строка", "строки", "строк") : `${view.length} из ${total} строк`;
    const u = $("#xg-undo", host), rd = $("#xg-redo", host); if (u) u.disabled = !ed.undo.length; if (rd) rd.disabled = !ed.redo.length;
    paint(false);
  };

  const td = (r, c) => { const tr = body.rows[r]; return tr ? tr.cells[c + 1] : null; };
  const rect = () => ({ r0: Math.min(v.r, v.ar), r1: Math.max(v.r, v.ar), c0: Math.min(v.c, v.ac), c1: Math.max(v.c, v.ac) });
  let painted = [];
  const paint = (scroll = true) => {
    painted.forEach(el => el.classList.remove("x-sel", "x-ac")); painted = [];
    if (!view.length) return;
    const R = rect(), big = (R.r1 - R.r0 + 1) * (R.c1 - R.c0 + 1) > 1;
    if (big) for (let i = R.r0; i <= R.r1; i++) for (let j = R.c0; j <= R.c1; j++) { const el = td(i, j); if (el) { el.classList.add("x-sel"); painted.push(el); } }
    const a = td(v.r, v.c); if (a) { a.classList.add("x-ac"); painted.push(a); if (scroll) keepVisible(a); }
  };
  const keepVisible = el => {
    const w = wrap.getBoundingClientRect(), e = el.getBoundingClientRect(), head = 40, fz = $("thead th.fzl", host);
    const left = el.classList.contains("fz") ? 0 : fz ? fz.getBoundingClientRect().right - w.left : 56;
    if (e.top < w.top + head) wrap.scrollTop -= w.top + head - e.top;
    else if (e.bottom > w.bottom - 12) wrap.scrollTop += e.bottom - w.bottom + 12;
    if (e.left < w.left + left) wrap.scrollLeft -= w.left + left - e.left;
    else if (e.right > w.right - 12) wrap.scrollLeft += e.right - w.right + 12;
  };
  const go = (r, c, extend = false) => {
    v.r = Math.max(0, Math.min(view.length - 1, r)); v.c = Math.max(0, Math.min(cols.length - 1, c));
    if (!extend) { v.ar = v.r; v.ac = v.c; }
    paint();
  };
  const mutate = fn => dataMutate(t, fn);

  const startEdit = initial => {
    const c = cols[v.c], r = view[v.r]; if (!c || !r) return;
    if (c.derived) return toast("Этот столбец вычисляется из других", true);
    if (c.readonly) return toast("Номер пары задан сеткой — меняйте время начала и конца", true);
    if (c.type === "bool" && initial == null) { mutate(() => { r[c.key] = !(r[c.key] === true); }); return; }
    const cell = td(v.r, v.c); if (!cell) return;
    cell.classList.add("x-edit");
    if (c.type === "enum") {
      const cur = r[c.key], known = c.options.some(o => o[0] === cur);
      cell.innerHTML = `<select class="xg-in">${!c.required ? '<option value="">—</option>' : ""}${!known && cur != null ? `<option value="${esc(cur)}" selected>${esc(cur)} (неверно)</option>` : ""}${c.options.map(o => `<option value="${esc(o[0])}" ${o[0] === cur ? "selected" : ""}>${o[0].length <= 2 ? `${esc(o[0])} — ` : ""}${esc(o[1])}</option>`).join("")}</select>`;
    } else cell.innerHTML = `<input class="xg-in" type="text" spellcheck="false" autocomplete="off">`;
    const inp = cell.firstChild;
    if (c.type !== "enum") inp.value = initial != null ? initial : showVal(c, r[c.key]);
    editing = { r, c, inp, cell };
    inp.focus();
    if (c.type !== "enum") { if (initial != null) inp.setSelectionRange(inp.value.length, inp.value.length); else inp.select(); }
    inp.addEventListener("keydown", e => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); commit(); go(v.r + (e.shiftKey ? -1 : 1), v.c); }
      else if (e.key === "Tab") { e.preventDefault(); commit(); go(v.r, v.c + (e.shiftKey ? -1 : 1)); }
      else if (e.key === "Escape") { e.preventDefault(); cancel(); }
      else if (c.type !== "enum" && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); commit(); go(v.r + (e.key === "ArrowUp" ? -1 : 1), v.c); }
    });
    if (c.type === "enum") inp.addEventListener("change", () => commit());
    inp.addEventListener("blur", () => { if (editing && editing.inp === inp) commit(false); });
  };
  const commit = (refocus = true) => {
    const e = editing; if (!e) return; editing = null;
    const nv = e.c.type === "enum" ? (e.inp.value === "" ? null : e.inp.value) : parseTyped(e.c, e.inp.value);
    if (!sameVal(nv, e.r[e.c.key]) || typeof nv !== typeof e.r[e.c.key]) mutate(() => { e.r[e.c.key] = nv; });
    else draw();
    if (refocus) wrap.focus({ preventScroll: true });
  };
  const cancel = () => { if (!editing) return; editing = null; draw(); wrap.focus({ preventScroll: true }); };

  const editableCols = R => cols.slice(R.c0, R.c1 + 1).filter(c => !c.derived && !c.readonly);
  const clearRange = () => {
    const R = rect(), cs = editableCols(R); if (!cs.length) return;
    mutate(() => { for (let i = R.r0; i <= R.r1; i++) for (const c of cs) view[i][c.key] = null; });
  };
  const fillDown = () => {
    const R = rect(); if (R.r1 === R.r0) return;
    mutate(() => { for (const c of editableCols(R)) for (let i = R.r0 + 1; i <= R.r1; i++) view[i][c.key] = view[R.r0][c.key]; });
  };
  const addRow = () => {
    if (fixed) return toast("В сетке ровно 12 пар — строки не добавляются", true);
    const at = view[v.r], r = newDataRow(t, at, v.f);
    mutate(() => { const i = at ? ed.rows[t].indexOf(at) + 1 : ed.rows[t].length; ed.rows[t].splice(i, 0, r); });
    go(view.indexOf(r), 0); startEdit();
  };
  const delRows = () => {
    if (fixed) return toast("В сетке ровно 12 пар — строки не удаляются", true);
    const R = rect(), list = view.slice(R.r0, R.r1 + 1); if (!list.length) return;
    if (list.length > 5 && !confirm(`Удалить ${plural(list.length, "строку", "строки", "строк")}?`)) return;
    const kill = new Set(list.map(r => r._id));
    mutate(() => { ed.rows[t] = ed.rows[t].filter(r => !kill.has(r._id)); });
    go(R.r0, v.c);
    toast(`Удалено: ${plural(list.length, "строка", "строки", "строк")}. Вернуть — Ctrl+Z`);
  };
  const openCard = () => { const r = view[v.r]; if (r) dataRecord(t, r._id); };

  const copyText = () => {
    const R = rect();
    return view.slice(R.r0, R.r1 + 1).map(r => cols.slice(R.c0, R.c1 + 1).map(c => showVal(c, val(c, r)).replace(/[\t\n]/g, " ")).join("\t")).join("\n");
  };
  const paste = text => {
    const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").map(l => l.split("\t"));
    if (!lines.length || (lines.length === 1 && lines[0].length === 1 && lines[0][0] === "")) return;
    const norm = s => String(s || "").trim().toLowerCase().replace(/[\s_\-.]+/g, "");
    const headMap = lines[0].map(h => spec.cols.findIndex(c => [c.key, c.label, ...(c.aliases || [])].some(n => norm(n) === norm(h))));
    const header = lines.length > 1 && headMap.filter(i => i >= 0).length >= Math.max(2, Math.ceil(lines[0].length / 2));
    const R = rect();
    let added = 0;
    mutate(() => {
      if (lines.length === 1 && lines[0].length === 1 && !header) {  // one value into the whole selection
        for (let i = R.r0; i <= R.r1; i++) for (const c of editableCols(R)) view[i][c.key] = parseTyped(c, lines[0][0]);
        return;
      }
      const data = header ? lines.slice(1) : lines;  // with a header row the lines are new records: append them
      const start = header && !fixed ? view.length : R.r0;
      data.forEach((cells, i) => {
        let row = view[start + i];
        if (!row) { if (fixed) return; row = newDataRow(t, view[view.length - 1], v.f); ed.rows[t].push(row); view.push(row); added++; }
        cells.forEach((s, j) => {
          const c = header ? spec.cols[headMap[j]] : cols[R.c0 + j];
          if (c && !c.derived && !c.readonly) row[c.key] = parseTyped(c, s);
        });
      });
      v.ar = start; v.r = Math.min(start + data.length - 1, view.length - 1); if (header) { v.ac = 0; v.c = cols.length - 1; }
      if (!header) { v.ac = R.c0; v.c = Math.min(cols.length - 1, R.c0 + Math.max(...data.map(x => x.length)) - 1); }
    });
    toast(`Вставлено: ${plural(lines.length - (header ? 1 : 0), "строка", "строки", "строк")}${header ? " по заголовкам столбцов" : ""}${added ? `, новых — ${added}` : ""}`);
  };

  // events -------------------------------------------------------------
  body.addEventListener("mousedown", e => {
    const cell = e.target.closest("td, th.rn"); if (!cell || cell.classList.contains("x-edit") || cell.classList.contains("fill")) return;
    if (editing) commit(false);
    const r = +cell.parentElement.dataset.r;
    e.preventDefault(); wrap.focus({ preventScroll: true });
    window.addEventListener("mouseup", () => { dragging = false; }, { once: true });
    if (cell.matches("th.rn")) { if (!e.shiftKey) { v.ar = r; } v.r = r; v.ac = 0; v.c = cols.length - 1; paint(false); dragging = "row"; return; }
    const c = +cell.dataset.c;
    if (e.target.closest(".xb") && !e.shiftKey && cols[c].type === "bool") { go(r, c); startEdit(); return; }
    go(r, c, e.shiftKey); dragging = "cell";
  });
  body.addEventListener("mouseover", e => {
    if (!dragging || !(e.buttons & 1)) { dragging = false; return; }
    const cell = e.target.closest("td, th.rn"); if (!cell || cell.classList.contains("fill")) return;
    v.r = +cell.parentElement.dataset.r; if (dragging === "cell" && cell.dataset.c != null) v.c = +cell.dataset.c;
    paint(false);
  });
  body.addEventListener("dblclick", e => {
    if (e.target.closest("th.rn")) { v.r = v.ar = +e.target.closest("tr").dataset.r; openCard(); return; }
    if (e.target.closest("td:not(.fill)") && !e.target.closest(".xb")) startEdit();
  });
  $("thead", host).addEventListener("click", e => {
    const th = e.target.closest("th"); if (!th || th.classList.contains("fill")) return;
    if (th.classList.contains("corner")) { v.ar = 0; v.ac = 0; v.r = view.length - 1; v.c = cols.length - 1; paint(false); wrap.focus(); return; }
    const j = +th.dataset.c; v.sort = !v.sort || v.sort[0] !== j ? [j, 1] : v.sort[1] === 1 ? [j, -1] : null; draw();
  });
  wrap.addEventListener("keydown", e => {
    if (editing) return;
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    const cmd = (keys, fn) => { if (keys.includes(k)) { e.preventDefault(); fn(); return true; } return false; };
    if (ctrl) {
      if (cmd(["z", "Z", "я", "Я"], () => e.shiftKey ? dataUndo(true) : dataUndo())) return;
      if (cmd(["y", "Y", "н", "Н"], () => dataUndo(true))) return;
      if (cmd(["a", "A", "ф", "Ф"], () => { v.ar = 0; v.ac = 0; v.r = view.length - 1; v.c = cols.length - 1; paint(false); })) return;
      if (cmd(["d", "D", "в", "В"], fillDown)) return;
      if (cmd(["Enter"], addRow)) return;
      if (cmd(["-", "_"], delRows)) return;
      if (cmd(["ArrowUp"], () => go(0, v.c, e.shiftKey)) || cmd(["ArrowDown"], () => go(view.length - 1, v.c, e.shiftKey))) return;
      if (cmd(["ArrowLeft"], () => go(v.r, 0, e.shiftKey)) || cmd(["ArrowRight"], () => go(v.r, cols.length - 1, e.shiftKey))) return;
      if (cmd(["Home"], () => go(0, 0, e.shiftKey)) || cmd(["End"], () => go(view.length - 1, cols.length - 1, e.shiftKey))) return;
      return;  // Ctrl+C / Ctrl+V / Ctrl+X arrive as copy / paste / cut events
    }
    if (cmd(["ArrowUp"], () => go(v.r - 1, v.c, e.shiftKey)) || cmd(["ArrowDown"], () => go(v.r + 1, v.c, e.shiftKey))
      || cmd(["ArrowLeft"], () => go(v.r, v.c - 1, e.shiftKey)) || cmd(["ArrowRight"], () => go(v.r, v.c + 1, e.shiftKey))
      || cmd(["PageUp"], () => go(v.r - 20, v.c, e.shiftKey)) || cmd(["PageDown"], () => go(v.r + 20, v.c, e.shiftKey))
      || cmd(["Home"], () => go(v.r, 0, e.shiftKey)) || cmd(["End"], () => go(v.r, cols.length - 1, e.shiftKey))
      || cmd(["Tab"], () => go(v.r, v.c + (e.shiftKey ? -1 : 1)))) return;
    if (cmd(["Enter", "F2"], () => e.shiftKey && k === "Enter" ? openCard() : startEdit())) return;
    if (cmd(["Delete", "Backspace"], clearRange)) return;
    if (cmd(["Escape"], () => { v.ar = v.r; v.ac = v.c; paint(false); })) return;
    if (k === " " && cols[v.c] && cols[v.c].type === "bool") { e.preventDefault(); startEdit(); return; }
    if (k.length === 1 && !e.altKey) { e.preventDefault(); startEdit(k); }
  });
  wrap.addEventListener("copy", e => { if (editing) return; e.preventDefault(); e.clipboardData.setData("text/plain", copyText()); const R = rect(); toast(`Скопировано: ${(R.r1 - R.r0 + 1) * (R.c1 - R.c0 + 1)} ячеек`); });
  wrap.addEventListener("cut", e => { if (editing) return; e.preventDefault(); e.clipboardData.setData("text/plain", copyText()); clearRange(); });
  wrap.addEventListener("paste", e => { if (editing) return; e.preventDefault(); paste(e.clipboardData.getData("text/plain")); });
  $("#xg-q", host).addEventListener("input", debounce(e => { v.q = e.target.value; v.r = v.ar = 0; draw(); }, 120));
  $$("[data-f]", host).forEach(s => s.onchange = () => { v.f[s.dataset.f] = s.value; v.r = v.ar = 0; ed.fresh.clear(); draw(); });
  const sw = (id, key) => { const el = $(id, host); if (el) el.onchange = () => { v[key] = el.checked; v.r = v.ar = 0; draw(); }; };
  sw("#xg-bad", "onlyBad"); sw("#xg-chg", "onlyChg");
  const on = (id, fn) => { const el = $(id, host); if (el) el.onclick = fn; };
  on("#xg-card", openCard); on("#xg-del", delRows);
  on("#xg-undo", () => dataUndo()); on("#xg-redo", () => dataUndo(true));
  on("#xg-csv", () => {
    const lines = [cols.map(c => c.label), ...view.map(r => cols.map(c => showVal(c, val(c, r))))].map(l => l.map(x => /[",;\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x).join(","));
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv" })); a.download = `${t}.csv`; a.click(); URL.revokeObjectURL(a.href);
  });
  wrap.addEventListener("scroll", () => { v.top = wrap.scrollTop; }, { passive: true });

  dataUi.grid = { t, redraw: draw, commit: () => commit(false), activeRow: () => view[v.r] || null,
    focusRow: id => { compute(); const i = view.findIndex(r => r._id === id); if (i >= 0) { v.r = v.ar = i; v.ac = v.c = 0; draw(); paint(true); } } };
  draw();
  wrap.scrollTop = v.top || 0;
  if (v.r || v.c) paint(true);
  wrap.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ import: one category or the whole template, preview, apply
function dataImport(file, table) {
  if (!/\.(xlsx|csv)$/i.test(file.name)) { toast("Нужен файл Excel .xlsx или таблица .csv", true); return; }
  if (!table && !/\.xlsx$/i.test(file.name)) { toast("Все категории сразу загружаются из книги Excel (единый шаблон)", true); return; }
  if (file.size > 20 * 1024 * 1024) { toast("Файл больше 20 МБ", true); return; }
  const ed = dataUi.ed, kb = file.size < 1048576 ? `${Math.max(1, Math.round(file.size / 1024))} КБ` : `${(file.size / 1048576).toFixed(1)} МБ`;
  const what = table ? `«${ed.spec[table].title}»` : "все категории";
  let box = $("#up-modal");
  if (!box) { box = document.createElement("div"); box.id = "up-modal"; document.body.appendChild(box); }
  box.className = "up-layer on";
  const close = () => { box.className = "up-layer"; box.innerHTML = ""; document.removeEventListener("keydown", esc_); };
  const esc_ = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", esc_);
  const steps = ["Загрузка файла", "Чтение листов и столбцов", "Предпросмотр изменений"];
  const frame = (step, pctDone, res = "", acts = "") => {
    box.innerHTML = `<div class="up-scrim"></div><div class="up-modal ${step >= 2 ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="Загрузка данных">
      <div class="up-file"><span class="up-ic">${I.table}</span><span><b>${esc(file.name)}</b><small>${kb} · в ${what}</small></span></div>
      <ol class="up-steps">${steps.map((s, i) => `<li class="${i < step ? "done" : i === step ? "on" : ""}"><span class="st-dot">${i < step ? I.check : i + 1}</span>${s}${i === 0 && step === 0 ? `<span class="pc">${pctDone}%</span>` : ""}</li>`).join("")}</ol>
      ${step < 2 ? `<div class="up-bar ${step === 1 ? "busy" : ""}"><i style="width:${step === 0 ? pctDone : 100}%"></i></div>` : ""}
      <div class="up-res">${res}</div><div class="actions">${acts}</div></div>`;
    $(".up-scrim", box).onclick = () => { if (step >= 2 || res) close(); };
  };
  frame(0, 0);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/data/${encId(ed.id)}/import${table ? `?table=${table}` : ""}`);
  xhr.upload.onprogress = e => { if (e.lengthComputable) frame(0, Math.round(e.loaded / e.total * 100)); };
  xhr.upload.onload = () => frame(1, 100);
  const fail = msg => {
    frame(1, 100, `<div class="up-err">${I.xCircle}<span><b>Файл не подошёл</b>${esc(msg)}</span></div><p class="hint">Столбцы — как в шаблоне или по-русски</p>`,
      `<a class="btn" href="/api/data/template" download>${I.down} Единый шаблон</a><button class="btn" id="up-close">Закрыть</button><button class="btn primary" id="up-again">${I.upload} Другой файл</button>`);
    const on = $(".up-steps li.on", box); if (on) on.classList.add("bad");
    $("#up-close", box).onclick = close; $("#up-again", box).onclick = () => { close(); dataPick(table); };
  };
  xhr.onerror = () => fail("сервер недоступен");
  xhr.onload = () => {
    let d = null; try { d = JSON.parse(xhr.responseText); } catch (_) { }
    if (xhr.status !== 200 || !d) { fail((d && d.error) || `ошибка ${xhr.status}`); return; }
    importPreview(d, table, frame, close);
  };
  const fd = new FormData(); fd.append("file", file);
  xhr.send(fd);
}

const IMPORT_MODES = {
  rooms: [["merge", "Обновить и добавить", "остальные останутся"], ["replace", "Заменить целиком", "как в файле"]],
  groups: [["merge", "Обновить и добавить", "остальные останутся"], ["replace", "Заменить целиком", "как в файле"]],
  courses: [["programs", "Заменить программы из файла", "другие программы останутся"], ["merge", "Обновить и добавить", "по дисциплине и триместру"], ["replace", "Заменить весь план", "как в файле"]],
  timeslots: [["replace", "Заменить время пар", "как в файле"]],
};

function importDiff(t, rows, cols, mode) {
  const ed = dataUi.ed, key = DATA_KEY[t], cur = new Map(ed.rows[t].map(r => [key(r), r]));
  const fileKeys = new Set(), progs = new Set(rows.map(r => lowKey(r.program)));
  let added = 0, changed = 0, same = 0;
  for (const r of rows) {
    const k = key(r); fileKeys.add(k);
    const old = cur.get(k);
    if (!old) added++; else if (cols.some(c => !sameVal(old[c], r[c] ?? null))) changed++; else same++;
  }
  const removed = mode === "merge" ? 0 : ed.rows[t].filter(r => !fileKeys.has(key(r)) && (mode !== "programs" || progs.has(lowKey(r.program)))).length;
  return { added, changed, same, removed };
}

function importPreview(d, table, frame, close) {
  const ed = dataUi.ed, tables = Object.keys(d.tables), sel = {}, mode = {}, extra = Object.keys(d.extra || {});
  sel.__extra = extra.length > 0;
  for (const t of tables) { sel[t] = true; mode[t] = table ? IMPORT_MODES[t][0][0] : t === "courses" ? "replace" : "replace"; }
  const block = t => {
    const f = d.found[t], rows = d.tables[t], df = importDiff(t, rows, f.columns, mode[t]);
    const colsRu = f.columns.map(k => (ed.spec[t].cols.find(c => c.key === k) || { label: k }).label);
    return `<div class="imp ${sel[t] ? "" : "off"}" data-imp="${t}">
      <div class="imp-head">${tables.length > 1 ? `<label class="switch"><input type="checkbox" data-sel="${t}" ${sel[t] ? "checked" : ""}><span class="track"></span></label>` : ""}
        <span class="imp-ic">${I[DATA_CATS.find(c => c[0] === t)[2]]}</span><b>${ed.spec[t].title}</b>
        <span class="hint">${f.sheets.length > 1 ? `листы: ${f.sheets.map(esc).join(", ")}` : `лист «${esc(f.sheets[0])}»`} · ${plural(rows.length, "строка", "строки", "строк")}</span></div>
      ${f.plan ? `<div class="imp-cols"><span>Учебный план:</span>${f.plan.map(p => `<span class="chip-s">${esc(p.program)} · ${esc(p.name_ru || "")}${p.years ? ` · ${esc(p.years)}` : ""}</span>`).join("")}</div>` : ""}
      ${f.notes && f.notes.length ? `<details class="imp-notes"><summary>Не попадут в расписание: ${f.notes.length}</summary><ul>${f.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></details>` : ""}
      <div class="imp-cols"><span>Столбцы:</span>${colsRu.map(c => `<span class="chip-s ok">${esc(c)}</span>`).join("")}${f.ignored.length ? `<span class="muted">пропущены: ${f.ignored.slice(0, 6).map(esc).join(", ")}${f.ignored.length > 6 ? "…" : ""}</span>` : ""}</div>
      <div class="imp-diff"><span class="dd add"><b>${df.added}</b>новых</span><span class="dd ch"><b>${df.changed}</b>изменится</span><span class="dd same"><b>${df.same}</b>без изменений</span><span class="dd del"><b>${df.removed}</b>${mode[t] === "merge" ? "удалится" : "будет удалено"}</span></div>
      ${IMPORT_MODES[t].length > 1 ? `<div class="imp-modes">${IMPORT_MODES[t].map(([k, l, h]) => `<label class="imp-mode ${mode[t] === k ? "on" : ""}"><input type="radio" name="m-${t}" value="${k}" data-mode="${t}" ${mode[t] === k ? "checked" : ""}><span><b>${l}</b><small>${h}</small></span></label>`).join("")}</div>` : ""}
    </div>`;
  };
  const draw = () => {
    const anyOn = tables.some(t => sel[t]) || sel.__extra;
    const extraBlock = extra.length ? `<div class="imp ${sel.__extra ? "" : "off"}"><div class="imp-head"><label class="switch"><input type="checkbox" data-sel="__extra" ${sel.__extra ? "checked" : ""}><span class="track"></span></label>
        <span class="imp-ic">${I.layers}</span><b>Для полного расписания</b><span class="hint">сохранятся в книге</span></div>
        <div class="imp-cols">${extra.map(k => `<span class="chip-s ${d.extra[k].length > 1 ? "ok" : ""}">${esc((EXTRA_RU[k] || [k])[0])} · ${Math.max(0, d.extra[k].length - 1)}</span>`).join("")}</div></div>` : "";
    frame(2, 100, `${d.missing.length && !table && tables.length ? `<p class="hint">В файле нет: ${d.missing.map(t => ed.spec[t].title).join(", ")} — эти категории не изменятся.</p>` : ""}${tables.map(block).join("")}${extraBlock}
      <p class="hint">Изменения появятся в таблице — затем «Сохранить»</p>`,
      `<button class="btn" id="up-close">Отмена</button><button class="btn primary" id="up-apply" ${anyOn ? "" : "disabled"}>${I.check} Применить</button>`);
    $("#up-close").onclick = close;
    $$("[data-sel]").forEach(cb => cb.onchange = () => { sel[cb.dataset.sel] = cb.checked; draw(); });
    $$("[data-mode]").forEach(rb => rb.onchange = () => { mode[rb.dataset.mode] = rb.value; draw(); });
    $("#up-apply").onclick = () => {
      const done = [];
      for (const t of tables) if (sel[t]) { applyImport(t, d.tables[t], d.found[t].columns, mode[t]); done.push(ed.spec[t].title); }
      if (sel.__extra) { ed.extra = d.extra; done.push("доп. листы"); dataRefresh(); }
      close();
      const first = tables.find(t => sel[t]);
      if (first) { dataView(first, {}); ed.views[first].onlyChg = false; dataTab(table || (done.length > 1 ? "overview" : first)); }
      else dataTab("overview");
      toast(`Загружено: ${done.join(", ")}. Проверьте подсвеченные строки и нажмите «Сохранить»`);
    };
  };
  draw();
}

function applyImport(t, rows, cols, mode) {
  const ed = dataUi.ed, key = DATA_KEY[t], cur = ed.rows[t];
  const byKey = new Map(cur.map(r => [key(r), r])), used = new Set();
  const progs = new Set(rows.map(r => lowKey(r.program)));
  dataMutate(t, () => {
    const merged = rows.map(src => {
      const old = byKey.get(key(src));
      const row = old && !used.has(old._id) ? { ...old } : newDataRow(t);
      if (old) used.add(old._id);
      for (const c of cols) row[c] = src[c] ?? null;
      return row;
    });
    if (mode === "replace") ed.rows[t] = t === "timeslots" ? cur.map(r => merged.find(m => m._id === r._id) || r) : merged;
    else if (mode === "programs") {
      const kept = cur.filter(r => !progs.has(lowKey(r.program)));
      ed.rows[t] = [...kept, ...merged];
    } else {
      const upd = new Map(merged.filter(r => used.has(r._id)).map(r => [r._id, r]));
      ed.rows[t] = [...cur.map(r => upd.get(r._id) || r), ...merged.filter(r => !upd.has(r._id))];
    }
  });
}

(function dataGlobalHooks() {
  let depth = 0;
  const isFile = e => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  const overlay = on => {
    let ov = document.getElementById("drop-ov");
    if (!ov) { ov = document.createElement("div"); ov.id = "drop-ov"; ov.className = "drop-ov"; ov.innerHTML = '<div class="drop-box"><span class="drop-ic"></span><b></b><small></small></div>'; document.body.appendChild(ov); }
    if (on) {
      const t = dataUi.tab !== "overview" && dataUi.ed ? dataUi.ed.spec[dataUi.tab].title : null;
      ov.querySelector(".drop-ic").innerHTML = I.upload;
      ov.querySelector("b").textContent = t ? `Отпустите, чтобы загрузить «${t}»` : "Отпустите, чтобы загрузить все категории";
      ov.querySelector("small").textContent = t ? "Excel или CSV" : "Единый шаблон Excel";
    }
    ov.classList.toggle("on", on);
  };
  window.addEventListener("dragenter", e => { if (currentPage !== "data" || !isFile(e) || !dataUi.ed) return; depth++; overlay(true); });
  window.addEventListener("dragover", e => { if (currentPage === "data" && isFile(e)) e.preventDefault(); });
  window.addEventListener("dragleave", e => { if (currentPage !== "data" || !isFile(e)) return; depth = Math.max(0, depth - 1); if (!depth) overlay(false); });
  window.addEventListener("drop", e => {
    if (currentPage !== "data" || !isFile(e)) return;
    e.preventDefault(); depth = 0; overlay(false);
    const f = e.dataTransfer.files[0]; if (f && dataUi.ed) dataImport(f, dataUi.tab === "overview" ? null : dataUi.tab);
  });
  // unsaved edits: ask before leaving the page (registered before app.js's router, so it can stop it)
  window.addEventListener("hashchange", e => {
    if (dataUi.ed && dataDirty() && !location.hash.startsWith("#/data")) {
      if (!confirm("В данных вуза есть несохранённые правки. Уйти без сохранения?")) { e.stopImmediatePropagation(); history.replaceState(null, "", dataUi.lastHash); return; }
      dataUi.ed = null;
    }
    if (!location.hash.startsWith("#/data") && dataUi.ed && !dataDirty()) dataUi.ed = null;  // reload fresh next time
    dataUi.lastHash = location.hash;
  });
  window.addEventListener("beforeunload", e => { if (dataUi.ed && dataDirty()) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("keydown", e => {
    if (currentPage === "data" && (e.ctrlKey || e.metaKey) && ["s", "S", "ы", "Ы"].includes(e.key)) { e.preventDefault(); dataSave(); }
  });
})();
