"""FastAPI application: JSON API for the single-page UI in ``static/``.

Start with ``python -m timetable web`` (defaults to http://127.0.0.1:8000). The same process hosts
the 24/7 autopilot (``timetable.service``) unless TT_AUTOPILOT=off."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..app import agent_table
from ..config import Settings
from ..service.autopilot import Autopilot
from ..service.versions import read_rows, to_ics
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


# which statuses a dispatcher decision may change (the Intake agent sets new -> accepted / rejected / review)
DECISIONS = {"approve": ({"new", "review", "rejected"}, "accepted"),
             "reject": ({"new", "review", "accepted"}, "rejected"),
             "withdraw": ({"new", "review", "accepted", "applied"}, "withdrawn")}


def create_app(autopilot: bool | None = None) -> FastAPI:
    """``autopilot=None`` follows TT_AUTOPILOT (auto = resume the saved on/off state); tests pass False."""
    manager = R.RunManager()
    pilot = Autopilot(manager, Settings())

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
            raise HTTPException(413, "file too large (max 20 MB)")
        return R.save_upload(file.filename or "upload.xlsx", content)

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
