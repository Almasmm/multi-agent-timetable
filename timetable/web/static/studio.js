/* Operator studio: home (what is happening with the timetable), one-button generation with the live board of the
   run, the real-time feed of the agents and the operator's proposals discussed by the agents.
   Loaded before app.js: app.js helpers ($, I, api, page, agentOf, STAGES...) are used only inside functions. */
"use strict";

const OPERATOR = { name: "operator", ru: "Оператор", label: "operator", short: "ОП", color: "var(--primary)" };
const whoIs = n => n === "operator" ? OPERATOR : n === "system" || n === "proposals" ? { name: "system", ru: "Система", label: "system", short: "SY", color: "var(--a-system)" } : agentOf(n);
const LIVE_TASK = { scan: "осмотр данных и заявок", propose: "предложить задание", triage: "разбор заявки", review: "проверка прогона",
  generate: "запуск генерации", proposal: "предложение оператора", publish: "публикация" };
const taskRu = t => TASK_RU[t] || LIVE_TASK[t] || t || "";
const fmtClock = ts => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const PROP_STATUS = { discussing: ["обсуждают", "accent"], ready: ["ждёт решения", "warn"], needs_input: ["нужен ответ", "warn"],
  accepted: ["принято", "ok"], rejected: ["отклонено", ""], failed: ["ошибка", "bad"] };
const propBadge = s => { const [t, c] = PROP_STATUS[s] || [s, ""]; return `<span class="badge ${c}">${s === "discussing" ? '<span class="spinner"></span>' : ""}${t}</span>`; };

// ================================================================== home: what is happening with the timetable
async function renderHome() {
  const [ap, datasets, props, edit, gen] = await Promise.all([api("/api/autopilot"), api("/api/datasets"), api("/api/proposals").catch(() => []),
    api("/api/edit").catch(() => null), api("/api/generate").catch(() => null)]);
  if (currentPage !== "home") return;
  const cur = ap.current, pub = cur ? await getPublished(cur.id) : null;
  const ds = datasets.find(d => d.valid && d.id === ap.profile.dataset) || datasets.find(d => d.valid);
  const mt = pub && pub.version ? pubMetrics(pub, ds, ap.profile) : null, soft = (cur && cur.soft) || {};
  const job = ap.job, lastJob = ap.jobs[0];
  const waiting = props.filter(p => p.status === "ready" || p.status === "needs_input"), talking = props.filter(p => p.status === "discussing");
  const dirty = gen && gen.data && gen.data.gate && !gen.data.ready;
  const attention = [
    dirty && { cls: "bad", ic: "db", t: `Генерация остановлена: в данных ${plural(gen.data.problems, "проблема", "проблемы", "проблем")}`, s: "Исправьте их в «Данные вуза» — до этого агенты не строят расписание", href: "#/data/overview", cta: "Исправить" },
    !cur && !dirty && { cls: "warn", ic: "cal", t: "Расписания ещё нет", s: "Сгенерируйте первую версию", href: "#/generate", cta: "Сгенерировать" },
    job && { cls: "run", ic: "layers", t: `Идёт ${JOB_RU[job.kind] || job.kind}`, s: job.reason, href: "#/generate", cta: "Смотреть" },
    waiting.length && { cls: "warn", ic: "msg", t: plural(waiting.length, "предложение ждёт решения", "предложения ждут решения", "предложений ждут решения"), s: waiting[0].text, href: `#/proposals/${waiting[0].id}`, cta: "Открыть" },
    talking.length && { cls: "run", ic: "msg", t: `Агенты обсуждают ${plural(talking.length, "предложение", "предложения", "предложений")}`, s: talking[0].text, href: `#/proposals/${talking[0].id}`, cta: "Открыть" },
    edit && edit.count && { cls: edit.stale ? "bad" : "warn", ic: "pen", t: `В черновике ${plural(edit.count, "правка", "правки", "правок")}`, s: edit.stale ? "черновик устарел — сбросьте" : "не опубликованы", href: "#/schedule/group", cta: "Открыть" },
    lastJob && lastJob.status === "failed" && !job && { cls: "bad", ic: "alertCircle", t: "Последняя генерация не удалась", s: (lastJob.outcome && lastJob.outcome.reason) || lastJob.reason, href: "#/generate", cta: "Подробнее" },
  ].filter(Boolean);
  const v = ap.versions[0], d = (v && v.diff) || {};
  const metric = (label, value, sub, icon, bar = null, cls = "") => `<div class="kpi metric ${cls}"><div class="l">${label}</div><span class="ico">${I[icon]}</span><div class="v">${value}</div>${bar != null ? `<div class="bar ${cls}"><i style="width:${Math.round(bar * 100)}%"></i></div>` : ""}<div class="d">${sub}</div></div>`;
  const feed = [...ap.versions.map(x => ({ ts: x.ts, c: "var(--ok)", href: "#/schedule", t: `Версия v${x.id}`, s: x.diff.first ? "первая версия" : `перенесено ${x.diff.hours_moved} ч · групп ${x.diff.groups_affected}` })),
    ...props.map(p => ({ ts: p.updated || p.ts, c: "var(--primary)", href: `#/proposals/${p.id}`, t: `Предложение №${p.id} — ${(PROP_STATUS[p.status] || [p.status])[0]}`, s: p.text })),
    ...ap.jobs.filter(j => j.status === "failed").map(j => ({ ts: j.finished || j.ts, c: "var(--bad)", href: "#/generate", t: "Генерация не удалась", s: "" }))]
    .sort((a, b) => b.ts - a.ts).slice(0, 6);
  $("#main").innerHTML = page("", `
    <header class="dash-head">
      <div><h1>${esc(ap.profile.university)}</h1>
        <div class="ver">${cur ? `${vtag(cur.id)} <span class="muted">опубликована ${fmtDate(cur.ts)} · триместр ${cur.term ?? "?"}</span>` : '<span class="badge warn">версии ещё нет</span>'}
          <a class="pilot-chip" href="#/admin"><span class="live-dot ${ap.enabled ? "beat" : "idle"}"></span>агенты ${ap.enabled ? "следят 24/7" : "на паузе"}</a></div></div>
      <div class="actions"><a class="btn" href="#/generate">${I.playCircle} Сгенерировать</a><a class="btn primary" href="#/proposals/new">${I.msg} Предложить изменение</a></div>
    </header>
    ${attention.length ? `<div class="attn">${attention.map(a => `<a class="attn-it ${a.cls}" href="${a.href}"><span class="ai-ic">${a.cls === "run" ? '<span class="spinner"></span>' : I[a.ic]}</span><span class="ai-t"><b>${esc(a.t)}</b><small>${esc(a.s || "")}</small></span><span class="ai-go">${a.cta} ${I.arrow}</span></a>`).join("")}</div>`
      : `<div class="attn"><div class="attn-it ok"><span class="ai-ic">${I.checkCircle}</span><span class="ai-t"><b>Всё в порядке</b><small>Версия v${cur.id} актуальна · конфликтов нет · новых предложений нет</small></span></div></div>`}
    <div class="kpi-row c4">
      ${mt ? metric("Конфликты", 0, "жёстких нарушений", "shield", null, "ok")
        + metric("Окна", `${num(soft.gap_hours_per_group, 1)}<small>ч</small>`, "на группу в неделю", "clock")
        + metric("Группы", `${pct(mt.groups / (mt.total || 1))}`, `${mt.groups} из ${mt.total} в расписании`, "users", mt.groups / (mt.total || 1))
        + metric("Аудитории", pct(mt.util), `загрузка · ${mt.fund} аудиторий`, "door", mt.util)
        : `<div class="kpi" style="grid-column:1/-1"><div class="empty-state small">${I.cal}<p>Метрики появятся после первой версии</p><div class="actions"><a class="btn primary sm" href="#/generate">${I.playCircle} Сгенерировать</a></div></div></div>`}
    </div>
    <div class="grid home-2">
      <section class="card">
        <div class="card-head"><h2>${I.refresh} Последнее изменение</h2>${v ? `<a class="btn sm ghost" href="#/schedule">Расписание ${I.arrow}</a>` : ""}</div>
        ${v ? `<div class="chg-nums">${d.first ? `<div><b>${d.total_hours}</b><small>ак. ч в неделю</small></div><div><b>${(pub && pub.groups.length) || "—"}</b><small>групп</small></div>`
          : `<div><b>${d.hours_moved}</b><small>ч перенесено</small></div><div><b>${d.room_changes}</b><small>ч смена аудитории</small></div><div><b>${d.groups_affected}</b><small>групп затронуто</small></div>`}</div>
          ${!d.first && d.groups ? `<div class="chips tight" style="margin-top:12px">${Object.entries(d.groups).sort((a, b) => (b[1].moved + b[1].rooms) - (a[1].moved + a[1].rooms)).slice(0, 10).map(([g, x]) => `<a class="chip" href="#/schedule/group/${encodeURIComponent(g)}"><span class="mono">${esc(g)}</span><span class="n">${x.moved + x.rooms}</span></a>`).join("")}</div>` : ""}
          ${v.announcement ? `<p class="hint clamp2" style="margin:12px 0 0">${esc(v.announcement)}</p>` : ""}` : `<div class="empty-state small"><p>Версий ещё нет</p></div>`}
      </section>
      <section class="card">
        <div class="card-head"><h2>${I.clock} События</h2><a class="btn sm ghost" href="#/live">${I.pulse} Агенты</a></div>
        ${feed.length ? `<div class="feed">${feed.map(f => `<a class="it" href="${f.href}"><span class="tm">${fmtWhen(f.ts)}</span><span class="dt" style="--c:${f.c}"></span><span class="tx">${esc(f.t)}<small>${esc(f.s || "")}</small></span></a>`).join("")}</div>`
          : `<div class="empty-state small"><p>Событий пока нет</p></div>`}
      </section>
    </div>`);
  if (job) pageTimer = setTimeout(() => { if (currentPage === "home") renderHome().catch(() => { }); }, 6000);
}

// ================================================================== proposals: the operator suggests, agents discuss
const propUi = { filter: "open", prefill: null };
function proposeRows(rows) {
  propUi.prefill = { context: rows.slice(0, 40).map(r => ({ group: r.group, course: r.course, day: r.day, time: r.time, room: r.room, kind: r.kind })) };
  location.hash = "#/proposals/new";
}
const ctxChip = (c, i, removable = false) => `<span class="pctx" title="${esc(c.course)}"><b class="mono">${esc(c.group)}</b>${esc(DAY_SHORT[c.day] || "")} ${esc(String(c.time || "").slice(0, 5))}<span class="t">${esc(c.course)}</span>${removable ? `<button data-rm="${i}" aria-label="убрать">${I.x}</button>` : ""}</span>`;
const PROP_EXAMPLES = ["Перенесите Calculus 1 у IT-2301 с понедельника утра", "1 курс должен заканчивать до 17:00", "Освободите пятницу у группы SE-2301"];

async function renderProposals(sel) {
  const list = await api("/api/proposals");
  if (currentPage !== "proposals") return;
  const cur = sel && sel !== "new" ? await api(`/api/proposals/${sel}`) : null;
  if (currentPage !== "proposals") return;
  const isOpen = p => ["discussing", "ready", "needs_input"].includes(p.status);
  const F = [["open", "открытые", isOpen], ["all", "все", () => true], ["closed", "закрытые", p => !isOpen(p)]];
  const shown = list.filter((F.find(f => f[0] === propUi.filter) || F[0])[2]);
  $("#main").innerHTML = `<div class="container wide page"><div class="prop">
    <aside class="prop-list">
      <div class="pl-head"><b>Предложения</b><a class="btn sm primary" href="#/proposals/new">${I.plus} Новое</a></div>
      <div class="pl-filter">${F.map(([k, l, f]) => `<button class="chip ${propUi.filter === k ? "on" : ""}" data-f="${k}">${l}<span class="n">${list.filter(f).length}</span></button>`).join("")}</div>
      <div class="pl-items">${shown.map(p => `<a class="pl-it ${cur && cur.id === p.id ? "on" : ""}" href="#/proposals/${p.id}">
          <span class="pl-top"><span class="mono muted">№${p.id}</span>${propBadge(p.status)}<span class="tm">${fmtWhen(p.updated || p.ts)}</span></span>
          <span class="pl-t">${esc(p.text)}</span>
          ${p.last ? `<span class="pl-last"><i style="--c:${whoIs(p.last.agent).color}"></i>${esc(whoIs(p.last.agent).ru)}: ${esc(plainText(p.last.text).slice(0, 90))}</span>` : ""}</a>`).join("")
        || `<div class="empty-state small">${I.msg}<p>${list.length ? "Здесь пусто" : "Предложений пока нет"}</p></div>`}</div>
    </aside>
    <section class="prop-main" id="prop-main"></section></div></div>`;
  $$(".pl-filter [data-f]").forEach(b => b.onclick = () => { propUi.filter = b.dataset.f; renderProposals(sel); });
  const main = $("#prop-main");
  if (sel === "new") return propCompose(main);
  if (!cur) {
    main.innerHTML = `<div class="prop-empty">${I.msg}<h2>Предложите изменение</h2><p>Агенты обсудят его, подготовят вариант и проверят. Решение — за вами.</p>
      <div class="chips" style="justify-content:center">${PROP_EXAMPLES.map(e => `<button class="chip" data-ex="${esc(e)}">${esc(e)}</button>`).join("")}</div>
      <a class="btn primary" href="#/proposals/new">${I.plus} Новое предложение</a></div>`;
    $$("[data-ex]", main).forEach(b => b.onclick = () => { propUi.prefill = { text: b.dataset.ex, context: [] }; location.hash = "#/proposals/new"; });
    return;
  }
  propThread(main, cur);
}

function propCompose(main) {
  const pre = propUi.prefill || { context: [] }; propUi.prefill = null;
  const ctx = [...(pre.context || [])];
  main.innerHTML = `<section class="card pt-new">
    <h2>${I.msg} Новое предложение</h2>
    <div class="pctx-list" id="pn-ctx"></div>
    <textarea id="pn-text" maxlength="1500" placeholder="${ctx.length ? "Что сделать с выбранными занятиями?" : "Что изменить в расписании?"}">${esc(pre.text || "")}</textarea>
    ${ctx.length ? "" : `<div class="chips tight">${PROP_EXAMPLES.map(e => `<button class="chip" data-ex="${esc(e)}">${esc(e)}</button>`).join("")}</div>`}
    <div class="pt-flow">${[["intake", "Приёмная определит тип"], ["scheduler", "Планировщик подготовит вариант"], ["validator", "Критик проверит"], ["operator", "Вы примете решение"]].map(([a, t]) => `<span><i style="--c:${whoIs(a).color}">${whoIs(a).short}</i>${t}</span>`).join(`<b>${I.arrow}</b>`)}</div>
    <div class="actions"><button class="btn primary lg" id="pn-send">${I.send} Обсудить с агентами</button><a class="btn lg subtle" href="#/proposals">Отмена</a></div></section>`;
  const drawCtx = () => {
    const el = $("#pn-ctx"); el.innerHTML = ctx.map((c, i) => ctxChip(c, i, true)).join("");
    $$("[data-rm]", el).forEach(b => b.onclick = () => { ctx.splice(+b.dataset.rm, 1); drawCtx(); });
  };
  drawCtx();
  const ta = $("#pn-text"); ta.focus();
  $$("[data-ex]", main).forEach(b => b.onclick = () => { ta.value = b.dataset.ex; ta.focus(); });
  ta.onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#pn-send").click(); };
  $("#pn-send").onclick = async e => {
    const text = ta.value.trim(); if (text.length < 3) { toast("Опишите предложение", true); ta.focus(); return; }
    e.currentTarget.disabled = true; e.currentTarget.innerHTML = '<span class="spinner"></span> Отправляем…';
    try { const p = await post("/api/proposals", { text, context: ctx }); location.hash = `#/proposals/${p.id}`; }
    catch (err) { toast(err.message, true); e.currentTarget.disabled = false; e.currentTarget.innerHTML = `${I.send} Обсудить с агентами`; }
  };
}

function propThread(main, p) {
  const o = p.outcome || {}, open = ["ready", "needs_input"].includes(p.status), talking = p.status === "discussing";
  const lastAgent = [...p.messages].reverse().find(m => m.role !== "user");
  const next = !lastAgent ? "intake" : lastAgent.agent === "intake" ? (p.kind === "rule" ? "constraints" : "scheduler") : lastAgent.agent === "scheduler" ? "validator" : lastAgent.agent;
  const typing = talking ? `<div class="msg ai typing" style="--c:${whoIs(next).color}"><span class="av">${whoIs(next).short}</span><div class="body"><div class="bubble"><span class="dots"><i></i><i></i><i></i></span> ${esc(whoIs(next).ru)} работает…</div></div></div>` : "";
  let decide = "";
  if (p.status === "ready" && p.kind === "rule") {
    decide = `<div class="pt-decide"><span class="pd-t">${I.shield}<span><b>Правило</b><small>${esc(o.understood || p.text)}</small></span></span>
      ${modeSeg("pd-mode", o.mode || "auto")}<span class="sp"></span>
      <button class="btn" data-act="reject">Отклонить</button><button class="btn primary" data-act="accept">${I.check} Принять правило</button></div>`;
  } else if (p.status === "ready") {
    decide = `<div class="pt-decide"><span class="pd-t">${I.pen}<span><b>${plural((o.moves || []).length, "перенос", "переноса", "переносов")} в черновике · ${o.hard ? `${o.hard} нарушений` : "0 нарушений"}</b><small>${esc((o.moves || []).map(m => m.label).join(" · "))}</small></span></span>
      <span class="sp"></span><button class="btn subtle" data-act="look">${I.calendar} Посмотреть</button>
      <button class="btn" data-act="reject">Отклонить</button><button class="btn primary" data-act="accept">${I.check} Принять и опубликовать</button></div>`;
  } else if (p.status === "needs_input") {
    decide = `<div class="pt-decide soft"><span class="pd-t">${I.info}<span><b>Агентам нужно уточнение</b><small>Ответьте ниже или отклоните</small></span></span><span class="sp"></span><button class="btn" data-act="reject">Отклонить</button></div>`;
  } else if (!talking) {
    decide = `<div class="pt-decide done ${p.status}"><span class="pd-t">${p.status === "accepted" ? I.checkCircle : I.xCircle}<span><b>${p.status === "accepted" ? (o.version ? `Принято · опубликована v${o.version}` : "Принято · правило добавлено") : "Отклонено"}</b></span></span>
      ${o.version ? `<span class="sp"></span><a class="btn" href="#/schedule">${I.table} Расписание</a>` : o.rule_number ? `<span class="sp"></span><a class="btn" href="#/rules">${I.shield} Правила</a>` : ""}</div>`;
  }
  main.innerHTML = `<section class="card pad0 pt">
    <header class="pt-head"><div class="pt-tags"><span class="mono muted">№${p.id}</span>${propBadge(p.status)}${p.kind ? `<span class="badge">${p.kind === "rule" ? "правило" : "изменение занятий"}</span>` : ""}<span class="tm">${fmtDate(p.ts)}</span></div>
      <h2>${esc(p.text)}</h2>${(p.context || []).length ? `<div class="pctx-list">${p.context.map((c, i) => ctxChip(c, i)).join("")}</div>` : ""}</header>
    <div class="ch-log pt-log" id="pt-log">${p.messages.map(m => chatMsg({ ...m, role: m.role === "user" ? "user" : "assistant" }, whoIs(m.agent))).join("")}${typing}</div>
    ${decide}
    ${open ? `<form class="ch-compose" id="pt-form"><textarea id="pt-in" rows="1" maxlength="1500" placeholder="Ответить агентам…"></textarea><button class="btn primary icon" type="submit" aria-label="Отправить">${I.send}</button></form>` : ""}
  </section>`;
  const log = $("#pt-log"); log.scrollTop = log.scrollHeight;
  bindChatLinks(log);
  if (p.status === "ready" && p.kind === "rule") bindSeg($("#pd-mode"), o.mode || "auto", () => { });
  $$("[data-act]", main).forEach(b => b.onclick = async () => {
    const act = b.dataset.act;
    if (act === "look") { schedState.edit = true; schedState.version = null; const g = (p.context[0] || {}).group; location.hash = g ? `#/schedule/group/${encodeURIComponent(g)}` : "#/schedule/group"; return; }
    b.disabled = true;
    try {
      const mode = act === "accept" && p.kind === "rule" ? ($("#pd-mode .on") || {}).dataset.v : undefined;
      await post(`/api/proposals/${p.id}/${act}`, act === "accept" ? { mode: mode || null } : {});
      toast(act === "accept" ? (p.kind === "rule" ? "Правило добавлено — агенты перегенерируют расписание" : "Опубликовано") : "Отклонено");
      pollPilot(); renderProposals(String(p.id));
    } catch (e) { toast(e.message, true); b.disabled = false; }
  });
  const form = $("#pt-form");
  if (form) {
    const inp = $("#pt-in");
    inp.oninput = () => { inp.style.height = "auto"; inp.style.height = Math.min(inp.scrollHeight, 160) + "px"; };
    inp.onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } };
    form.onsubmit = async e => {
      e.preventDefault(); const text = inp.value.trim(); if (!text) return;
      inp.disabled = true;
      try { await post(`/api/proposals/${p.id}/reply`, { text }); renderProposals(String(p.id)); } catch (err) { toast(err.message, true); inp.disabled = false; }
    };
  }
  if (talking) pageTimer = setTimeout(() => { if (currentPage === "proposals" && location.hash === `#/proposals/${p.id}`) renderProposals(String(p.id)).catch(() => { }); }, 1500);
}

// ================================================================== generation: one button, live board
const genUi = { run: null, off: 0, events: [], group: null, reviewed: null, status: null, seen: new Set() };
async function renderGenerate() {
  const st = await api("/api/generate");
  if (currentPage !== "generate") return;
  const p = st.profile;
  genUi.data = st.data;
  if (genUi.run !== (st.active || st.last)) Object.assign(genUi, { run: st.active || st.last, off: 0, events: [], group: null, reviewed: null, status: null, seen: new Set() });
  $("#main").innerHTML = page(pageHead("Генерация", `${esc(p.dataset)} · триместр ${p.term} · ${p.programs.length ? esc(p.programs.join(", ")) : "все программы"} · ${plural(p.requirements.length, "правило", "правила", "правил")}`,
    `<a class="btn" href="#/live">${I.pulse} Работа агентов</a>`), `
    <section class="card gen-hero" id="gen-hero"></section>
    <div class="gen-stages" id="gen-stages"></div>
    <div class="kpi-row gen-kpis" id="gen-kpis"></div>
    <div class="gen-board">
      <section class="card gen-week-card"><div class="card-head"><h2>${I.calendar} Неделя</h2><span class="hint" id="gen-heat-note"></span></div>
        <div id="gen-heat"></div><div class="gen-legend"><span><i class="lg-solid"></i>размещено</span><span><i class="lg-ghost"></i>черновик ГА</span></div>
        <div class="card-head" style="margin-top:18px"><h2>${I.layers} Куски задачи</h2><span class="hint">ГА → CP-SAT → Критик</span></div><div id="gen-chunks" class="gen-chunks"></div></section>
      <div class="gen-side">
        <section class="card"><div class="card-head"><h2>${I.users} Группа</h2><select id="gen-group" aria-label="Группа"></select></div><div id="gen-week"></div></section>
        <section class="card"><div class="card-head"><h2>${I.layers} Программы</h2></div><div id="gen-progs" class="gen-progs"></div></section>
        <section class="card"><div class="card-head"><h2>${I.msg} Агенты сейчас</h2><a class="btn sm ghost" href="#/live">Все ${I.arrow}</a></div><div id="gen-feed" class="gen-feed"></div></section>
      </div>
    </div>`);
  $("#gen-group").onchange = e => { genUi.group = e.target.value; genTick(); };
  genTick();
}

async function genStart(btn) {
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Запуск…';
  try {
    const r = await post("/api/generate", {});
    if (r.run_id) { Object.assign(genUi, { run: r.run_id, off: 0, events: [], group: null, reviewed: null, seen: new Set() }); toast(r.state === "busy" ? "Генерация уже идёт" : "Агенты начали генерацию"); }
    else toast(r.reason || "Агенты не запустили генерацию", true);
  } catch (e) { toast(e.message, true); }
  pollPilot(); renderGenerate();
}

async function genTick() {
  clearTimeout(pageTimer);
  const id = genUi.run, hero = $("#gen-hero"); if (!hero) return;
  const blocked = genUi.data && genUi.data.gate && !genUi.data.ready;
  const btn = running => `<button class="btn primary lg gen-go" id="gen-go" ${running || blocked ? "disabled" : ""} title="${blocked ? "сначала исправьте данные вуза" : ""}">${I.playCircle} Сгенерировать расписание</button>`;
  const gateHtml = () => {
    const d = genUi.data;
    return `<div class="gate bad gen-gate"><span class="gate-ic">${I.alertCircle}</span><span class="gate-t"><b>Генерация остановлена: в данных вуза ${plural(d.problems, "проблема", "проблемы", "проблем")}</b>
      <small>Агенты не строят расписание, пока загрузчик Data-агента находит ошибки. Исправьте их — кнопка станет активной.</small>
      <ul>${(d.items || []).slice(0, 5).map(i => `<li class="${i.level}">${esc(i.text)}</li>`).join("")}</ul></span>
      <a class="btn sm primary" href="#/data/overview">${I.db} Исправить данные</a></div>`;
  };
  const gateEl = $("#gen-gate-slot") || (() => { hero.insertAdjacentHTML("beforebegin", '<div id="gen-gate-slot"></div>'); return $("#gen-gate-slot"); })();
  gateEl.innerHTML = blocked ? gateHtml() : "";
  if (!id) {
    hero.innerHTML = blocked ? `<div class="gh-state"><span class="gh-ic">${I.db}</span><div><h2>Ждём исправления данных</h2><p>Как только проблем не останется, кнопка станет активной</p></div></div>${btn(false)}`
      : `<div class="gh-state"><span class="gh-ic">${I.playCircle}</span><div><h2>Готово к генерации</h2><p>Агенты построят расписание по данным вуза и правилам и проверят его</p></div></div>${btn(false)}`;
    $("#gen-go").onclick = e => genStart(e.currentTarget);
    ["#gen-stages", "#gen-kpis"].forEach(s => { $(s).innerHTML = ""; });
    $("#gen-heat").innerHTML = '<div class="empty-state small"><p>Неделя заполнится во время генерации</p></div>';
    return;
  }
  let st, board, ev;
  try {
    [st, board, ev] = await Promise.all([api(`/api/runs/${encodeURIComponent(id)}`), api(`/api/runs/${encodeURIComponent(id)}/board${genUi.group ? `?group=${encodeURIComponent(genUi.group)}` : ""}`),
      api(`/api/runs/${encodeURIComponent(id)}/events?offset=${genUi.off}`)]);
  } catch (e) { hero.innerHTML = `<div class="gh-state bad"><span class="gh-ic">${I.alertCircle}</span><div><h2>Прогон недоступен</h2><p>${esc(e.message)}</p></div></div>${btn(false)}`; $("#gen-go").onclick = x => genStart(x.currentTarget); return; }
  if (currentPage !== "generate" || genUi.run !== id) return;
  genUi.off = ev.offset; genUi.events.push(...ev.events);
  const running = st.status === "running" || st.status === "cancelling", res = st.result || {};
  // publication: the Publisher reviews a finished service job on the next tick
  let pubLine = "";
  if (!running && genUi.reviewed === null && /-auto-/.test(id)) {
    genUi.reviewed = "pending";
    try {
      const br = await api("/api/autopilot/brief");
      if (br.job && br.job.run_id === id) genUi.reviewed = await post("/api/autopilot/tick", {});  // the Publisher reviews it now
      else {
        const ap = await api("/api/autopilot"), j = ap.jobs.find(x => x.run_id === id), o = (j && j.outcome) || {};
        genUi.reviewed = j ? { state: "reviewed", status: j.status, version: o.version, reason: o.reason } : { state: "none" };
      }
      pollPilot();
    } catch (_) { genUi.reviewed = { state: "error" }; }
  }
  const rv = genUi.reviewed;
  if (rv && typeof rv === "object" && rv.state === "reviewed")
    pubLine = rv.status === "published" ? `Опубликована версия v${rv.version}` : `${rv.status === "kept" ? "Публикатор оставил прежнюю версию" : "Публикатор не опубликовал"}${rv.reason ? `: ${rv.reason}` : ""}`;
  else if (!running && /-auto-/.test(id)) pubLine = "Публикатор проверяет…";
  // hero
  const tasks = genUi.events.filter(e => e.kind === "message" && e.task).map(e => e.task);
  const stageIdx = tasks.reduce((m, t) => Math.max(m, STAGES.findIndex(s => s.tasks.includes(t))), -1);
  const hv = res.hard_violations ? sum(Object.values(res.hard_violations)) : null;
  const okRun = st.status === "ok";
  hero.innerHTML = running
    ? `<div class="gh-state run"><span class="gh-ic"><span class="spinner big"></span></span><div><h2>Идёт генерация · ${esc(stageIdx >= 0 ? STAGES[stageIdx].label : "старт")}</h2><p class="mono">${fmtDur(st.elapsed_s)} · ${esc(id)}</p></div></div>
       <div class="actions"><button class="btn" id="gen-stop">${I.stop} Остановить</button>${btn(true)}</div>`
    : `<div class="gh-state ${okRun ? "ok" : "bad"}"><span class="gh-ic">${okRun ? I.checkCircle : I.alertCircle}</span><div><h2>${okRun ? `Готово за ${fmtDur(st.elapsed_s)} · ${hv ? `${hv} нарушений` : "0 нарушений"}` : `Прогон: ${statusInfo(st.status, st.cancelled).text}`}</h2>
       <p>${esc(pubLine || res.reason || "")}</p><div class="actions" style="margin-top:8px"><a class="btn sm" href="#/schedule">${I.table} Расписание</a><a class="btn sm ghost" href="#/run/${encodeURIComponent(id)}">Подробности прогона ${I.arrow}</a></div></div></div>${btn(false)}`;
  const go = $("#gen-go"); if (go && !running) go.onclick = e => genStart(e.currentTarget);
  const stop = $("#gen-stop"); if (stop) stop.onclick = async () => { stop.disabled = true; try { await post(`/api/runs/${encodeURIComponent(id)}/cancel`, {}); toast("Останавливаем после текущего шага"); } catch (e) { toast(e.message, true); } };
  // stages
  $("#gen-stages").innerHTML = STAGES.map((s, i) => { const state = !running && okRun ? "done" : i < stageIdx ? "done" : i === stageIdx ? (running ? "on" : "done") : "";
    return `<div class="gs ${state}" style="--c:${AGENT_BY[s.agent].color}"><span class="gs-dot">${state === "done" ? I.check : i + 1}</span><span class="gs-t">${esc(s.label)}<small>${esc(AGENT_BY[s.agent].ru)}</small></span></div>`; }).join("");
  // kpis
  const calls = genUi.events.filter(e => e.kind === "tool_call").length, llm = genUi.events.filter(e => e.kind === "llm_call").length;
  const b = board.ready ? board : null, chunks = b ? b.chunks : [];
  const solved = chunks.filter(c => c.state === "validated").length;
  const hvLive = b && b.validation ? b.validation.hard_violations : hv;
  $("#gen-kpis").innerHTML = kpi(fmtDur(st.elapsed_s), "время") + kpi(b ? `${solved}<small>/${chunks.length}</small>` : "—", "кусков готово")
    + kpi(b ? pct(b.placed / (b.total || 1)) : "—", "занятий размещено", "", b ? `${b.placed} из ${b.total}` : "")
    + kpi(b ? b.hours_total : "—", "ак. часов в неделю") + kpi(calls, "вызовов инструментов") + kpi(llm, "вызовов LLM")
    + kpi(hvLive ?? "—", "жёстких нарушений", hvLive === 0 ? "ok" : hvLive > 0 ? "bad" : "");
  const again = b ? genBoard(b) : false;
  if (!b) $("#gen-heat").innerHTML = `<div class="empty-state small"><span class="spinner"></span><p>Data-агент загружает данные…</p></div>`;
  // feed
  const msgs = genUi.events.filter(e => e.kind === "message" && e.task).slice(-7).reverse();
  $("#gen-feed").innerHTML = msgs.map(e => { const a = whoIs(e.agent), to = whoIs(e.to); const reply = !!e.intent;
    return `<div class="gf"><span class="av" style="--c:${a.color}">${a.short}</span><span class="gf-t"><b>${esc(a.ru)}</b> ${reply ? "ответил" : "→"} ${reply ? "" : `<b>${esc(to.ru)}</b>`}<small>${esc(taskRu(e.task))}${e.intent === "error" ? " · ошибка" : ""}</small></span><span class="tm">${fmtClock(e.ts)}</span></div>`; }).join("") || '<p class="hint">Пока тихо</p>';
  if (again || running || genUi.reviewed === "pending")
    pageTimer = setTimeout(() => { if (currentPage === "generate" && genUi.run === id) genTick(); }, again ? 30 : 1500);
}

function genBoard(b) {
  const max = Math.max(1, ...b.grid.flat()), times = pubTimes && pubTimes.length ? pubTimes : Array.from({ length: 12 }, (_, h) => `${String(8 + h).padStart(2, "0")}:00`);
  const prev = genUi.prevGrid || [];
  $("#gen-heat").innerHTML = `<table class="gheat"><thead><tr><th></th>${DAY_SHORT.map(d => `<th>${d}</th>`).join("")}</tr></thead><tbody>${times.map((t, h) => `<tr><th class="mono">${esc(t)}</th>${b.grid.map((col, d) => {
    const n = col[h], g = b.ghost[d][h], was = prev[d] ? prev[d][h] : 0;
    return `<td class="${n > was ? "flash" : ""}" style="--h:${(n / max).toFixed(2)}" title="${DAY_RU[d]} ${t}: ${n} занятий${g ? ` + ${g} в черновике ГА` : ""}">${n ? `<b>${n}</b>` : ""}${g ? `<i>+${g}</i>` : ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
  genUi.prevGrid = b.grid;
  $("#gen-heat-note").textContent = b.final ? "итоговое расписание" : `${b.placed} из ${b.total} занятий`;
  const ST = { waiting: ["ждёт", ""], quota: ["квота аудиторий", "run"], ga: ["ГА: первое поколение", "run"], solved: ["CP-SAT решил", "run"], validated: ["проверено", "ok"], retry: ["переговоры", "warn"] };
  $("#gen-chunks").innerHTML = b.chunks.map(c => { const [t, cls] = ST[c.state] || [c.state, ""];
    return `<div class="gc ${cls} ${b.chunk_recent === c.k ? "recent" : ""}"><div class="gc-top"><b>Кусок ${c.k + 1}</b><span class="gc-st">${cls === "run" ? '<span class="spinner"></span>' : cls === "ok" ? I.check : ""}${t}</span></div>
      <div class="gc-units">${c.units.slice(0, 6).map(u => `<span class="mono">${esc(u)}</span>`).join("")}${c.units.length > 6 ? `<span>+${c.units.length - 6}</span>` : ""}</div>
      <div class="gc-n">${c.groups} гр · ${c.sessions} занятий</div></div>`; }).join("");
  const sel = $("#gen-group");
  if (sel && sel.options.length !== b.groups.length) {
    if (!genUi.group) { const ch = b.chunks.find(c => c.k === b.chunk_recent) || b.chunks[0]; genUi.group = b.groups.find(g => ch && ch.units.some(u => g.startsWith(u.split("-")[0] + "-"))) || b.groups[0]; }
    sel.innerHTML = b.groups.map(g => `<option ${g === genUi.group ? "selected" : ""}>${esc(g)}</option>`).join("");
  }
  const again = !b.group && !!genUi.group;  // the group was just chosen: fetch its week
  const cells = Array.from({ length: 6 }, () => Array(12).fill(null));
  for (const w of b.week || []) for (let k = 0; k < w.duration; k++) if (w.hour + k < 12) cells[w.day][w.hour + k] = { ...w, cont: k > 0 };
  $("#gen-week").innerHTML = `<table class="mini-week"><thead><tr><th></th>${DAY_SHORT.map(d => `<th>${d}</th>`).join("")}</tr></thead><tbody>${Array.from({ length: 12 }, (_, h) => `<tr><th>${8 + h}</th>${cells.map(col => {
    const c = col[h]; if (!c) return "<td></td>";
    return `<td class="mw ${c.online ? "online" : c.kind} ${c.tentative ? "tent" : ""}" title="${esc(c.course)}${c.tentative ? " (черновик ГА)" : ""}">${c.cont ? "" : esc(c.course.split(/[ /]/)[0].slice(0, 6))}</td>`; }).join("")}</tr>`).join("")}</tbody></table>
    <p class="hint" style="margin:8px 0 0">${(b.week || []).length ? `${plural((b.week || []).length, "занятие", "занятия", "занятий")} размещено` : "Кусок этой группы ещё не решён"}</p>`;
  $("#gen-progs").innerHTML = Object.entries(b.programs).sort((a, c) => a[0].localeCompare(c[0])).map(([p, [n, t]]) => `<div class="gp"><span class="mono">${esc(p)}</span><span class="bar"><i style="width:${(n / (t || 1) * 100).toFixed(1)}%"></i></span><span class="gp-n">${n}/${t}</span></div>`).join("");
  return again;
}

// ================================================================== real-time feed of the agents
const lvUi = { cursors: {}, run: null, src: "all", tools: false, paused: false, stats: {}, last: null, n: 0 };
async function renderLive() {
  Object.assign(lvUi, { cursors: {}, run: null, stats: {}, last: null, n: 0 });
  $("#main").innerHTML = `<div class="container wide page live-page">
    ${pageHead("Работа агентов", "Что агенты делают и пишут друг другу — в реальном времени",
      `<div class="seg" id="lv-src">${[["all", "Все"], ["run", "Генерация"], ["svc", "Служба 24/7"], ["chat", "Чат и предложения"]].map(([k, l]) => `<button data-v="${k}" class="${lvUi.src === k ? "on" : ""}">${l}</button>`).join("")}</div>
       <label class="switch"><input type="checkbox" id="lv-tools" ${lvUi.tools ? "checked" : ""}><span class="track"></span><span class="small">инструменты</span></label>
       <button class="btn sm" id="lv-pause">${lvUi.paused ? I.play : I.stop} ${lvUi.paused ? "Продолжить" : "Пауза"}</button>`)}
    <div class="live"><aside class="card lv-agents" id="lv-agents"></aside>
      <section class="card pad0 lv-feed"><div class="lv-top" id="lv-top"></div><div class="lv-log" id="lv-log" data-src="${lvUi.src}" data-tools="${lvUi.tools}"></div></section></div></div>`;
  bindSeg($("#lv-src"), lvUi.src, v => { lvUi.src = v; $("#lv-log").dataset.src = v; });
  $("#lv-tools").onchange = e => { lvUi.tools = e.target.checked; $("#lv-log").dataset.tools = lvUi.tools; };
  $("#lv-pause").onclick = () => { lvUi.paused = !lvUi.paused; $("#lv-pause").innerHTML = `${lvUi.paused ? I.play : I.stop} ${lvUi.paused ? "Продолжить" : "Пауза"}`; if (!lvUi.paused) liveTick(); };
  liveTick();
}

function lvItem(e) {
  const a = whoIs(e.agent), src = e.src, tm = `<span class="lv-tm">${fmtClock(e.ts)}</span>`;
  if (e.kind === "message") {
    const to = whoIs(e.to), reply = !!e.intent, bad = e.intent === "error";
    const prose = !!(e.payload && e.payload.text), body = prose ? esc(plainText(e.payload.text)) : esc(brief(e.payload || {}));
    return `<div class="lv-msg ${reply ? "reply" : ""} ${bad ? "bad" : ""}" data-s="${src}"><span class="av" style="--c:${a.color}">${a.short}</span>
      <div class="lv-b"><div class="lv-h"><b>${esc(a.ru)}</b><span class="lv-arrow">${I.arrow}</span><span class="lv-to" style="--c:${to.color}">${esc(to.ru)}</span><span class="lv-task">${reply ? (bad ? "ошибка" : "ответ") : "поручение"} · ${esc(taskRu(e.task))}</span>${tm}</div>
      ${body ? `<div class="lv-p ${prose ? "prose" : ""}">${body}</div>` : ""}</div></div>`;
  }
  if (e.kind === "tool_call") return `<div class="lv-row tool ${e.status !== "ok" ? "bad" : ""}" data-s="${src}"><span class="lv-ic">${I.tool}</span><b style="color:${a.color}">${esc(a.ru)}</b><span class="mono">${esc(e.name)}</span><span class="muted">${e.duration_ms != null ? `${e.duration_ms} мс` : ""}</span>${tm}</div>`;
  if (e.kind === "llm_call") return `<div class="lv-row llm ${e.status !== "ok" ? "bad" : ""}" data-s="${src}"><span class="lv-ic">${I.brain}</span><b style="color:${a.color}">${esc(a.ru)}</b><span>спросил ${esc(e.provider === "claude" ? "Claude" : e.provider === "openai" ? "OpenAI" : "офлайн-правила")}</span><span class="mono">${esc(e.name || "")}</span><span class="muted">${e.duration_ms ? `${(e.duration_ms / 1000).toFixed(1)} с` : ""}${e.tokens_in ? ` · ${e.tokens_in}→${e.tokens_out} ток.` : ""}${e.status !== "ok" ? ` · ${esc(e.error || e.status)}` : ""}</span>${tm}</div>`;
  if (e.kind === "error") return `<div class="lv-row bad" data-s="${src}"><span class="lv-ic">${I.alertCircle}</span><b>${esc(a.ru)}</b><span>${esc(e.msg || "")}</span>${tm}</div>`;
  return `<div class="lv-info" data-s="${src}"><span>${esc(a.ru)}: ${esc(e.msg || e.kind)}</span>${tm}</div>`;
}

async function liveTick() {
  clearTimeout(pageTimer);
  if (currentPage !== "live" || lvUi.paused) return;
  const q = new URLSearchParams({ svc: lvUi.cursors.svc || "", chat: lvUi.cursors.chat || "", run_c: lvUi.cursors.run || "" });
  if (lvUi.run) q.set("run", lvUi.run);
  let r;
  try { r = await api(`/api/live?${q}`); } catch (_) { pageTimer = setTimeout(liveTick, 3000); return; }
  if (currentPage !== "live") return;
  lvUi.cursors = { ...r.cursors };
  lvUi.run = r.run;
  if (r.active && r.active !== r.run) { lvUi.run = r.active; lvUi.cursors.run = ""; }  // a new run started: follow it
  const log = $("#lv-log"); if (!log) return;
  const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  const now = Date.now() / 1000;
  for (const e of r.events) {
    const s = lvUi.stats[e.agent] || (lvUi.stats[e.agent] = { msg: 0, tool: 0, llm: 0, last: 0 });
    if (e.kind === "message") s.msg++; else if (e.kind === "tool_call") s.tool++; else if (e.kind === "llm_call") s.llm++;
    s.last = Math.max(s.last, e.ts || 0);
  }
  if (r.events.length) {
    log.insertAdjacentHTML("beforeend", r.events.map(lvItem).join(""));
    lvUi.n += r.events.length;
    while (log.children.length > 900) log.firstElementChild.remove();
    if (atEnd) log.scrollTop = log.scrollHeight;
  }
  if (!log.children.length) log.innerHTML = '<div class="empty-state small lv-empty"><p>Агенты пока молчат — запустите генерацию или отправьте предложение</p></div>';
  else { const em = $(".lv-empty", log); if (em) em.remove(); }
  $("#lv-top").innerHTML = `<span class="live-dot ${r.active ? "beat" : "idle"}"></span><b>${r.active ? "Идёт генерация" : "Генерация не идёт"}</b>${lvUi.run ? `<a class="mono" href="#/run/${encodeURIComponent(lvUi.run)}">${esc(lvUi.run)}</a>` : ""}<span class="sp"></span><span class="muted">${plural(lvUi.n, "событие", "события", "событий")}</span>`;
  const row = (a, title) => { const s = lvUi.stats[a.name] || { msg: 0, tool: 0, llm: 0, last: 0 }, hot = now - s.last < 6;
    return `<div class="lv-ag ${hot ? "hot" : ""}" style="--c:${a.color}"><span class="av">${a.short}</span><span class="t"><b>${esc(title || a.ru)}</b><small>${s.last ? `${s.msg} сообщ. · ${s.tool} инстр.${s.llm ? ` · ${s.llm} LLM` : ""}` : "тихо"}</small></span>${hot ? '<span class="live-dot beat"></span>' : ""}</div>`; };
  $("#lv-agents").innerHTML = `<div class="lv-sec">Служба 24/7</div>${SERVICE_AGENTS.map(a => row(agentOf(a.name))).join("")}
    <div class="lv-sec">Генерация</div>${AGENTS.map(a => row(agentOf(a.name))).join("")}<div class="lv-sec">Люди</div>${row(OPERATOR)}`;
  pageTimer = setTimeout(liveTick, 1200);
}
