"""HTTP API + static web frontend.

Run with:  uvicorn --factory splat_explorer.api:create_app --host 127.0.0.1 --port 8000
(or simply `scripts/start.sh`, which also starts the worker).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import PROJECT_ROOT, QUALITY_PRESETS, Settings, get_settings
from .db import JobStore
from .pipeline import probe as probe_stage
from .pipeline.tools import tool_exists

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v"}
CHUNK = 8 * 1024 * 1024
WEB_DIR = PROJECT_ROOT / "web"


class StartBody(BaseModel):
    quality: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = JobStore(settings.db_path)
    app = FastAPI(title="Splat Explorer", version=__version__)
    app.state.settings = settings
    app.state.store = store

    def job_or_404(job_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{12}", job_id):
            raise HTTPException(404, "Job not found")
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    def public(job: dict[str, Any], request: Request) -> dict[str, Any]:
        out = dict(job)
        base = str(request.base_url).rstrip("/")
        scene = settings.job_dir(job["id"]) / "scene"
        out["has_video"] = any(settings.job_dir(job["id"]).glob("input.*"))
        out["scene_ready"] = job["status"] == "done" and (scene / "scene.ply").exists()
        out["preview_url"] = (f"/api/scenes/{job['id']}/preview.jpg"
                              if (scene / "preview.jpg").exists() else None)
        out["viewer_link"] = (f"splatexplorer://open?scene={job['id']}&server={base}"
                              if out["scene_ready"] else None)
        return out

    # -- health ------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        tools = {
            "ffmpeg": tool_exists(settings.ffmpeg_bin),
            "ffprobe": tool_exists(settings.ffprobe_bin),
            "colmap": tool_exists(settings.colmap_bin),
            "brush": tool_exists(settings.brush_bin),
        }
        return {"ok": all(tools.values()), "version": __version__, "tools": tools,
                "data_dir": str(settings.data_dir), "qualities": list(QUALITY_PRESETS),
                "default_quality": settings.default_quality}

    # -- jobs --------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs(request: Request, status: str | None = None) -> list[dict[str, Any]]:
        return [public(j, request) for j in store.list(status)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> dict[str, Any]:
        return public(job_or_404(job_id), request)

    @app.post("/api/jobs", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...), name: str = Form(""),
                     quality: str = Form(""), auto_start: bool = Form(False)) -> dict[str, Any]:
        ext = Path(file.filename or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, "Upload an MP4 or MOV video.")
        quality = quality if quality in QUALITY_PRESETS else settings.default_quality
        display_name = (name or Path(file.filename or "scene").stem).strip()[:80] or "scene"
        job = store.create(display_name, quality, video_filename=file.filename)
        job_dir = settings.job_dir(job["id"])
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / f"input{ext}"
        limit = settings.max_upload_mb * 1024 * 1024
        written = 0
        try:
            with open(dest, "wb") as out:
                while chunk := await file.read(CHUNK):
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB.")
                    out.write(chunk)
            info = probe_stage.probe_video(dest, settings)
            warnings = probe_stage.validate(info, settings)
        except probe_stage.InvalidVideo as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            store.delete(job["id"])
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            shutil.rmtree(job_dir, ignore_errors=True)
            store.delete(job["id"])
            raise
        store.update(job["id"], video_info=info.to_dict(), warnings=warnings,
                     message="Uploaded - press Start to reconstruct")
        if auto_start:
            store.enqueue(job["id"])
        return public(store.get(job["id"]), request)  # type: ignore[arg-type]

    @app.post("/api/jobs/{job_id}/start")
    def start(job_id: str, request: Request, body: StartBody | None = None) -> dict[str, Any]:
        job = job_or_404(job_id)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, f"Job is already {job['status']}.")
        if job["status"] == "done":
            raise HTTPException(409, "Job is already done. Use retrain to train again.")
        quality = body.quality if body and body.quality in QUALITY_PRESETS else None
        return public(store.enqueue(job_id, quality), request)  # type: ignore[arg-type]

    @app.post("/api/jobs/{job_id}/retrain")
    def retrain(job_id: str, request: Request, body: StartBody | None = None) -> dict[str, Any]:
        """Re-run training (and export) with another quality, reusing frames + poses."""
        job = job_or_404(job_id)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, f"Job is already {job['status']}.")
        job_dir = settings.job_dir(job_id)
        if not (job_dir / ".done_colmap").exists() or not (job_dir / "dataset").exists():
            raise HTTPException(409, "Camera poses are not available; use Start/Retry instead.")
        for stage in ("train", "export"):
            (job_dir / f".done_{stage}").unlink(missing_ok=True)
        quality = body.quality if body and body.quality in QUALITY_PRESETS else None
        return public(store.enqueue(job_id, quality), request)  # type: ignore[arg-type]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request) -> dict[str, Any]:
        job = job_or_404(job_id)
        if job["status"] == "queued":
            store.update(job_id, status="cancelled", message="Cancelled before it started")
        elif job["status"] == "running":
            store.update(job_id, cancel_requested=True, message="Cancelling...")
        else:
            raise HTTPException(409, f"Job is {job['status']}, nothing to cancel.")
        return public(store.get(job_id), request)  # type: ignore[arg-type]

    @app.delete("/api/jobs/{job_id}")
    def delete(job_id: str) -> dict[str, str]:
        job = job_or_404(job_id)
        if job["status"] == "running":
            raise HTTPException(409, "Cancel the job before deleting it.")
        shutil.rmtree(settings.job_dir(job_id), ignore_errors=True)
        store.delete(job_id)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/video")
    def video(job_id: str) -> FileResponse:
        job_or_404(job_id)
        for p in sorted(settings.job_dir(job_id).glob("input.*")):
            return FileResponse(p)
        raise HTTPException(404, "Video not found")

    @app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
    def job_log(job_id: str, lines: int = 200) -> str:
        job_or_404(job_id)
        path = settings.job_dir(job_id) / "job.log"
        if not path.exists():
            return ""
        content = path.read_text(errors="replace").splitlines()
        return "\n".join(content[-max(1, min(lines, 5000)):])

    # -- scenes (consumed by the Unity viewer) --------------------------------
    @app.get("/api/scenes")
    def scenes(request: Request) -> list[dict[str, Any]]:
        out = []
        for job in store.list("done"):
            scene_dir = settings.job_dir(job["id"]) / "scene"
            if (scene_dir / "scene.ply").exists():
                out.append({"id": job["id"], "name": job["name"], "splat_count": job["splat_count"],
                            "ply_size_bytes": (scene_dir / "scene.ply").stat().st_size,
                            "created_at": job["created_at"]})
        return out

    @app.get("/api/scenes/{job_id}/{filename}")
    def scene_file(job_id: str, filename: str) -> FileResponse:
        job_or_404(job_id)
        if filename not in ("scene.ply", "scene.json", "preview.jpg"):
            raise HTTPException(404, "Unknown scene file")
        path = settings.job_dir(job_id) / "scene" / filename
        if not path.exists():
            raise HTTPException(404, "Scene file not ready")
        return FileResponse(path)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # -- web frontend --------------------------------------------------------
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
