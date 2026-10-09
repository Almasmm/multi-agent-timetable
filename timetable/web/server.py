"""FastAPI application: JSON API for the single-page UI in ``static/``.

Start with ``python -m timetable web`` (defaults to http://127.0.0.1:8000)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..app import agent_table
from . import runs as R
from .agents_ru import localized_agent_table

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 20 * 1024 * 1024


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
    llm: str = Field("auto", pattern="^(auto|claude|offline)$")
    name: str = Field("run", max_length=40)


def create_app() -> FastAPI:
    app = FastAPI(title="Multi-agent timetable", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    manager = R.RunManager()

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad_request(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def _conflict(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

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
        return localized_agent_table(agent_table())

    # --- runs ---
    @app.get("/api/runs")
    def list_runs():
        return manager.list()

    @app.post("/api/runs", status_code=201)
    def start_run(req: RunRequest):
        h = manager.start(req.model_dump())
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

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


app = create_app()


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("timetable.web.server:app", host=host, port=port, reload=reload, log_level="info")
