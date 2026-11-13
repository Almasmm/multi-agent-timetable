"""FastAPI application: JSON API for the single-page UI in ``static/``.

Start with ``python -m timetable web`` (defaults to http://127.0.0.1:8000). The same process hosts
the 24/7 autopilot (``timetable.service``) unless TT_AUTOPILOT=off."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..app import agent_table
from ..config import Settings
from ..domain.constraints import with_mode
from ..domain.preview import describe_rules
from ..service import datasets as D
from ..service.agents import describe_spec
from ..service.autopilot import Autopilot
from ..service.chat import AgentChat
from ..service.editor import ScheduleEditor
from ..service.proposals import ProposalDesk
from ..service.versions import read_rows, to_ics
from . import live as L
from . import runs as R
from .agents_ru import localized_agent_table, service_agent_table

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024
PUBLISHED_FILES = ("timetable.xlsx", "timetable.csv", "timetable.html", "report.md")


class PreviewRequest(BaseModel):
    dataset: str
    texts: list[str] = Field(default_factory=list, max_length=100)


class RunRequest(BaseModel):
    dataset: str = Field(description="dataset id from GET /api/datasets")
    term: int = Field(1, ge=1, le=3)
    programs: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    allow_saturday: bool = False
    solver_seconds: float | None = Field(None, ge=1, le=600)
    chunk_groups: int | None = Field(None, ge=5, le=300)
    stream: int | None = Field(None, ge=1, le=50)
    llm: str = Field("auto", pattern="^(auto|openai|claude|offline)$")
    name: str = Field("run", max_length=40)
    warm_start: bool = Field(False, description="start from the published version (few moves for students)")


class RequestIn(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    author: str = Field("диспетчер", min_length=1, max_length=60)


class DecisionIn(BaseModel):
    action: Literal["approve", "reject", "withdraw"]
    note: str = Field("", max_length=300)


class SwitchIn(BaseModel):
    on: bool


class ProfileIn(BaseModel):
    university: str = Field("Astana IT University", min_length=2, max_length=80)
    dataset: str
    term: int = Field(1, ge=1, le=3)
    programs: list[str] = Field(default_factory=list, max_length=50)
    requirements: list[str] = Field(default_factory=list, max_length=100)
    solver_seconds: float = Field(25, ge=1, le=600)
    llm: str = Field("auto", pattern="^(auto|openai|claude|offline)$")
    warm_start: bool = True
    improve_every_min: float = Field(60, ge=0, le=10080)
    max_improve_attempts: int = Field(4, ge=0, le=50)
    stable_after: str = Field("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    max_moved_share: float = Field(0.1, ge=0, le=1)


class RuleIn(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    mode: Literal["auto", "hard", "soft"] = "auto"
    weight: int | None = Field(None, ge=1, le=20)


class RulesIn(BaseModel):
    rules: list[RuleIn] = Field(default_factory=list, max_length=100)


class RuleText(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class CellIn(BaseModel):
    group: str | None = Field(None, max_length=40)
    room: str | None = Field(None, max_length=40)
    day: int = Field(ge=0, le=5)
    hour: int = Field(ge=0, le=11)


class MoveIn(CellIn):
    to_day: int = Field(ge=0, le=5)
    to_hour: int = Field(ge=0, le=11)
    to_room: str | None = Field(None, max_length=40)


class PublishIn(BaseModel):
    note: str = Field("", max_length=300)
    author: str = Field("диспетчер", min_length=1, max_length=60)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


Cell = str | int | float | bool | None


class TablesIn(BaseModel):
    tables: dict[Literal["rooms", "groups", "courses", "timeslots"], list[dict[str, Cell]]]
    extra: dict[str, list[list[Cell]]] | None = None  # extra sheets of a full upload (teachers, load...)
    base_mtime: float | None = None
    note: str = Field("", max_length=200)


class ContextItem(BaseModel):
    group: str = Field(max_length=40)
    course: str = Field("", max_length=200)
    day: int = Field(0, ge=0, le=5)
    time: str = Field("", max_length=20)
    room: str = Field("", max_length=40)
    kind: str = Field("", max_length=20)


class ProposalIn(BaseModel):
    text: str = Field(min_length=3, max_length=1500)
    context: list[ContextItem] = Field(default_factory=list, max_length=40)


class ProposalReply(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


class AcceptIn(BaseModel):
    mode: Literal["auto", "hard", "soft"] | None = None


class StampIn(BaseModel):
    stamp: str = Field(pattern=r"^[0-9-]{8,30}$")


# which statuses a dispatcher decision may change (the Intake agent sets new -> accepted / rejected / review)
DECISIONS = {"approve": ({"new", "review", "rejected"}, "accepted"),
             "reject": ({"new", "review", "accepted"}, "rejected"),
             "withdraw": ({"new", "review", "accepted", "applied"}, "withdrawn")}


def create_app(autopilot: bool | None = None) -> FastAPI:
    """``autopilot=None`` follows TT_AUTOPILOT (auto = resume the saved on/off state); tests pass False."""
    manager = R.RunManager()
    pilot = Autopilot(manager, Settings())
    editor = ScheduleEditor(pilot.store, pilot.settings)
    chat = AgentChat(pilot.store, editor, pilot, pilot.settings)
    desk = ProposalDesk(pilot.store, chat)

    @asynccontextmanager
    async def lifespan(_app):
        if autopilot is True:
            pilot.start()
        elif autopilot is None:
            pilot.resume_if_enabled()
        yield
        pilot.close()

    app = FastAPI(title="Multi-agent timetable", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  lifespan=lifespan)

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad_request(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _missing(_, exc):
        return JSONResponse({"error": str(exc).strip("'\"")}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def _conflict(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    # --- health (Docker HEALTHCHECK, CI smoke test, Kubernetes probes) ---
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": os.environ.get("APP_VERSION", "dev"),
                "data": Settings().data_path.is_file(), "autopilot": pilot.running}

    # --- datasets ---
    @app.get("/api/datasets")
    def datasets():
        return R.list_datasets()

    @app.post("/api/datasets/upload")
    async def upload(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "файл больше 20 МБ")
        return data_item(R.dataset_info(D.store_upload(file.filename or "upload.xlsx", content)))

    # --- operator proposals discussed by the agents ---
    @app.get("/api/proposals")
    def proposals_list():
        return desk.list()

    @app.post("/api/proposals", status_code=201)
    def proposals_create(req: ProposalIn):
        return desk.create(req.text, [c.model_dump() for c in req.context])

    @app.get("/api/proposals/{pid}")
    def proposals_get(pid: int):
        return desk.get(pid)

    @app.post("/api/proposals/{pid}/reply")
    def proposals_reply(pid: int, req: ProposalReply):
        return desk.reply(pid, req.text)

    @app.post("/api/proposals/{pid}/accept")
    def proposals_accept(pid: int, req: AcceptIn):
        return desk.accept(pid, req.mode)

    @app.post("/api/proposals/{pid}/reject")
    def proposals_reject(pid: int):
        return desk.reject(pid)

    # --- real time: what the agents do, how a run builds the timetable, the one-button generation ---
    def current_run() -> str | None:
        h = manager.active()
        if h is not None:
            return h.run_id
        job = pilot.store.active_job()
        return job["run_id"] if job and job.get("run_id") else None

    def last_run() -> str | None:
        cur = current_run()
        if cur:
            return cur
        runs = manager.list()
        return runs[0]["id"] if runs else None

    @app.get("/api/live")
    def live(run: str | None = None, svc: str = "", chat_: str = Query("", alias="chat"), run_c: str = ""):
        rid = run or last_run()
        if rid:
            R.run_dir(rid)  # validates the id
        return {**L.live_feed(R.runs_root(), rid, {"svc": svc, "chat": chat_, "run": run_c}), "active": current_run()}

    @app.get("/api/runs/{run_id}/board")
    def run_board(run_id: str, group: str | None = None):
        return {**L.run_board(R.run_dir(run_id), group), "status": manager.status(run_id)}

    @app.get("/api/generate")
    def generate_state():
        prof = pilot.store.profile()
        return {"active": current_run(), "last": last_run(), "brief": pilot.brief(),
                "profile": {k: prof[k] for k in ("dataset", "term", "programs", "requirements", "solver_seconds", "llm")}}

    @app.post("/api/generate")
    def generate_now():
        rid = current_run()
        if rid:
            return {"state": "busy", "run_id": rid}
        cur = pilot.store.current_version()
        pilot.store.set("force_job", {"kind": "regenerate", "reason": "кнопка «Сгенерировать»"})
        res = pilot.tick()
        if res.get("state") != "started":
            pilot.store.set("force_job", None)
        return {**res, "first": cur is None}

    # --- university data desk: the workbook as editable tables ---
    def data_item(d: dict) -> dict:
        p = Path(d["path"])
        return {**d, "editable": D.editable(p), "active": d["id"] == pilot.store.profile()["dataset"],
                "mtime": p.stat().st_mtime if p.exists() else None}

    @app.get("/api/data")
    def data_list():
        return [data_item(d) for d in R.list_datasets()]

    @app.get("/api/data/template")
    def data_template():
        return Response(D.template(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="timetable-data-template.xlsx"'})

    @app.get("/api/data/working")
    def data_working():
        """The workbook the agents use: what the «Данные вуза» page edits. If that file is gone (deleted by
        hand), the agents are pointed back to the default workbook so neither they nor the page break."""
        name = pilot.store.profile()["dataset"]
        try:
            D.find(name)
        except FileNotFoundError:
            name = Settings().data_path.name
            pilot.store.set("profile", {**pilot.store.profile(), "dataset": name})
        return data_open(name)

    @app.get("/api/data/{dataset_id}")
    def data_open(dataset_id: str):
        p = D.find(dataset_id)
        tables = D.read_tables(p)
        return {"dataset": data_item(R.dataset_info(p)), "tables": tables, "spec": D.TABLES,
                "check": D.check(p, tables), "history": D.history(p), "extra": D.extra_counts(p)}

    @app.post("/api/data/{dataset_id}/check")
    def data_check(dataset_id: str, req: TablesIn):
        return D.check(D.find(dataset_id), req.tables)

    @app.post("/api/data/{dataset_id}/autofix")
    def data_autofix(dataset_id: str, req: TablesIn):
        D.find(dataset_id)
        return D.autofix(req.tables)

    @app.put("/api/data/{dataset_id}")
    def data_save(dataset_id: str, req: TablesIn):
        p, base = D.find(dataset_id), req.base_mtime
        if not D.editable(p):  # an original: the first save becomes the working copy the agents switch to
            D.same_version(p, base)
            copy = D.copy(p, "university")
            try:
                res = D.save(copy, req.tables, None, req.note or "исходный файл до первой правки", req.extra)
            except Exception:
                D.delete(copy)
                raise
            if dataset_id == pilot.store.profile()["dataset"]:
                pilot.store.set("profile", {**pilot.store.profile(), "dataset": copy.name})
            return {**res, "dataset": data_item(R.dataset_info(copy)), "history": D.history(copy), "extra": D.extra_counts(copy)}
        res = D.save(p, req.tables, base, req.note, req.extra)
        return {**res, "dataset": data_item(R.dataset_info(p)), "history": D.history(p), "extra": D.extra_counts(p)}

    @app.post("/api/data/{dataset_id}/import")
    async def data_import(dataset_id: str, file: UploadFile = File(...), table: str | None = None):
        p = D.find(dataset_id)
        content = await file.read()
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "файл больше 20 МБ")
        return D.import_file(file.filename or "upload.xlsx", content, table or None, D.import_context(p))

    @app.post("/api/data/{dataset_id}/copy", status_code=201)
    def data_copy(dataset_id: str):
        return data_item(R.dataset_info(D.copy(D.find(dataset_id))))

    @app.post("/api/data/{dataset_id}/activate")
    def data_activate(dataset_id: str):
        info = R.dataset_info(D.find(dataset_id))
        if not info["valid"]:
            raise ValueError("этот файл не проходит проверку загрузчика — агенты не смогут по нему работать")
        pilot.store.set("profile", {**pilot.store.profile(), "dataset": dataset_id})
        return data_item(info)

    @app.get("/api/data/{dataset_id}/download")
    def data_download(dataset_id: str):
        return FileResponse(D.find(dataset_id), filename=dataset_id)

    @app.post("/api/data/{dataset_id}/restore")
    def data_restore(dataset_id: str, req: StampIn):
        p = D.find(dataset_id)
        D.restore(p, req.stamp)
        return {"dataset": data_item(R.dataset_info(p)), "history": D.history(p)}

    @app.delete("/api/data/{dataset_id}")
    def data_delete(dataset_id: str):
        if dataset_id == pilot.store.profile()["dataset"]:
            raise ValueError("это рабочий набор агентов — сначала сделайте рабочим другой")
        D.delete(D.find(dataset_id))
        return {"deleted": dataset_id}

    @app.post("/api/constraints/preview")
    def preview(req: PreviewRequest):
        return R.preview_constraints(req.dataset, req.texts)

    # --- agents ---
    @app.get("/api/agents")
    def agents():
        return {**localized_agent_table(agent_table()), "service": service_agent_table()}

    # --- runs ---
    @app.get("/api/runs")
    def list_runs():
        return manager.list()

    @app.post("/api/runs", status_code=201)
    def start_run(req: RunRequest):
        params = req.model_dump()
        if params.pop("warm_start"):
            cur = pilot.store.current_version()
            starts = pilot.store.version_dir(cur["id"]) / "starts.json" if cur else None
            if not starts or not starts.exists():
                raise ValueError("нет опубликованной версии для тёплого старта")
            params["warm_start_path"] = str(starts)
        h = manager.start(params)
        return {"id": h.run_id}

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str):
        return manager.status(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        R.run_dir(run_id)
        return {"cancelled": manager.cancel(run_id)}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str):
        manager.delete(run_id)
        return {"deleted": run_id}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, offset: int = 0):
        events, new_offset, more = R.read_events(R.run_dir(run_id), max(0, offset))
        return {"events": events, "offset": new_offset, "more": more}

    @app.get("/api/runs/{run_id}/timetable")
    def run_timetable(run_id: str):
        return R.read_timetable(R.run_dir(run_id))

    @app.get("/api/runs/{run_id}/details")
    def run_details(run_id: str):
        return R.read_details(R.run_dir(run_id))

    @app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
    def run_report(run_id: str):
        p = R.run_dir(run_id) / "report.md"
        if not p.exists():
            raise HTTPException(404, "report.md not written yet")
        return p.read_text(encoding="utf-8")

    @app.get("/api/runs/{run_id}/files/{name}")
    def run_file(run_id: str, name: str):
        if name not in R.DOWNLOADABLE:
            raise HTTPException(404, "unknown file")
        p = R.run_dir(run_id) / name
        if not p.exists():
            raise HTTPException(404, f"{name} not found")
        return FileResponse(p, filename=f"{run_id}-{name}")

    # --- 24/7 service: autopilot, staff requests, published versions ---
    @app.get("/api/autopilot")
    def autopilot_status():
        return pilot.status()

    @app.get("/api/autopilot/brief")
    def autopilot_brief():
        return pilot.brief()

    @app.post("/api/autopilot/switch")
    def autopilot_switch(req: SwitchIn):
        if req.on:
            pilot.start()
        else:
            pilot.stop()
        return {"enabled": req.on}

    @app.post("/api/autopilot/tick")
    def autopilot_tick():
        return pilot.tick()

    @app.put("/api/autopilot/profile")
    def autopilot_profile(req: ProfileIn):
        R.resolve_dataset(req.dataset)
        prof = req.model_dump()
        prof["requirements"] = [x.strip() for x in prof["requirements"] if x.strip()]
        pilot.store.set("profile", prof)
        return pilot.store.profile()

    @app.get("/api/requests")
    def list_requests():
        return pilot.store.requests()

    @app.post("/api/requests", status_code=201)
    def add_request(req: RequestIn):
        rid = pilot.store.add_request(req.text.strip(), req.author.strip())
        return pilot.store.request(rid)

    @app.post("/api/requests/{rid}/decision")
    def decide_request(rid: int, req: DecisionIn):
        r = pilot.store.request(rid)
        if r is None:
            raise FileNotFoundError(f"request {rid} not found")
        allowed, target = DECISIONS[req.action]
        if r["status"] not in allowed:
            raise ValueError(f"нельзя применить «{req.action}» к заявке в статусе {r['status']}")
        verdict = {**(r["verdict"] or {}), "dispatcher": req.note or req.action}
        pilot.store.update_request(rid, status=target, verdict=verdict)
        return pilot.store.request(rid)

    def _version(vid: int | None) -> dict:
        v = pilot.store.version(vid) if vid else pilot.store.current_version()
        if v is None:
            raise FileNotFoundError(f"version {vid} not found" if vid else "опубликованной версии ещё нет")
        return v

    def _version_rows(v: dict) -> list[dict]:
        return read_rows(pilot.store.version_dir(v["id"]) / "timetable.csv")

    @app.get("/api/published")
    def published(version: int | None = None):
        if version is None and pilot.store.current_version() is None:
            return {"version": None}
        v = _version(version)
        return {"version": v, **R.timetable_payload(_version_rows(v))}

    @app.get("/api/published/ics")
    def published_ics(group: str, version: int | None = None):
        v = _version(version)
        rows = _version_rows(v)
        if not any(r["group"] == group for r in rows):
            raise FileNotFoundError(f"group '{group}' is not in version v{v['id']}")
        st = Settings()
        safe = "".join(c for c in group if c.isalnum() or c in "-_") or "group"
        return Response(to_ics(rows, group, st.term_start, st.weeks_per_term), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{safe}-v{v["id"]}.ics"'})

    @app.get("/api/published/files/{name}")
    def published_file(name: str, version: int | None = None):
        if name not in PUBLISHED_FILES:
            raise HTTPException(404, "unknown file")
        v = _version(version)
        p = pilot.store.version_dir(v["id"]) / name
        if not p.exists():
            raise HTTPException(404, f"{name} not found")
        return FileResponse(p, filename=f"v{v['id']}-{name}")

    # --- university rules: hard / soft (stored as profile requirements, "[soft:7] ..." tags) ---
    def _rule_rows(texts: list[str]) -> list[dict]:
        out = describe_rules(R.resolve_dataset(pilot.store.profile()["dataset"]), texts)
        for r in out:
            r["understood"] = describe_spec(r["spec"]) if r["spec"] else None
        return out

    @app.get("/api/rules")
    def rules():
        prof = pilot.store.profile()
        accepted = [{"id": r["id"], "text": r["text"], "status": r["status"], "author": r["author"],
                     "understood": (r["verdict"] or {}).get("understood")} for r in pilot.store.requests(("accepted", "applied"))]
        return {"rules": _rule_rows(prof["requirements"]), "requests": accepted,
                "version": (pilot.store.current_version() or {}).get("id")}

    @app.put("/api/rules")
    def save_rules(req: RulesIn):
        texts = [with_mode(r.text, None if r.mode == "auto" else r.mode == "hard", r.weight) for r in req.rules]
        pilot.store.set("profile", {**pilot.store.profile(), "requirements": texts})
        return rules()

    @app.post("/api/rules/preview")
    def preview_rule(req: RuleText):
        return _rule_rows([req.text])[0]

    # --- manual editing: draft on top of the published version (drag & drop) ---
    @app.get("/api/edit")
    def edit_state(rows: bool = False):
        if pilot.store.current_version() is None:
            return {"base": None, "count": 0, "moves": []}
        st = editor.state(with_rows=rows)
        if rows:
            st.update({k: v for k, v in R.timetable_payload(st.pop("rows")).items()})
        return st

    @app.post("/api/edit/options")
    def edit_options(req: CellIn):
        return editor.options(req.group, req.room, req.day, req.hour)

    @app.post("/api/edit/move")
    def edit_move(req: MoveIn):
        return editor.move(req.group, req.room, req.day, req.hour, req.to_day, req.to_hour, req.to_room)

    @app.post("/api/edit/undo")
    def edit_undo():
        return editor.undo()

    @app.post("/api/edit/discard")
    def edit_discard():
        return editor.discard()

    @app.post("/api/edit/publish")
    def edit_publish(req: PublishIn):
        return editor.publish(req.note, req.author)

    # --- chat with each agent ---
    @app.get("/api/chat")
    def chat_agents():
        return {"agents": chat.agents(), "provider": chat.provider()}

    @app.get("/api/chat/{agent}")
    def chat_history(agent: str):
        return {"messages": chat.history(agent)}

    @app.post("/api/chat/{agent}")
    def chat_send(agent: str, req: ChatIn):
        return chat.send(agent, req.text)

    @app.delete("/api/chat/{agent}")
    def chat_clear(agent: str):
        chat.clear(agent)
        return {"cleared": agent}

    @app.get("/api/search")
    def search(q: str = ""):
        q = q.strip().lower()
        if not q:
            return []
        out = []
        cur = pilot.store.current_version()
        if cur:
            tt = R.timetable_payload(_version_rows(cur))
            out += [{"type": "group", "label": g} for g in tt["groups"] if q in g.lower()][:8]
            out += [{"type": "room", "label": r} for r in tt["rooms"] if q in r.lower()][:6]
        out += [{"type": "run", "label": r["id"]} for r in manager.list() if q in r["id"].lower()][:6]
        return out

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()  # uvicorn entry point


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("timetable.web.server:app", host=host, port=port, reload=reload, log_level="info")
