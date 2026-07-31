"""FastAPI server for the React UAB Medicine Infographic Generator."""

from __future__ import annotations

import base64
import asyncio
import time
import uuid
from pathlib import Path
from typing import Annotated

import anyio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from uab_app.generation_pipeline import (
    SourceUpload,
    available_audiences,
    available_styles,
    generate_infographic,
)


ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
DIST_DIR = ROOT / "frontend" / "dist"


class ConfigResponse(BaseModel):
    audiences: list[dict[str, str]]
    styles: list[dict[str, str]]
    defaultAudience: str
    defaultStyle: str
    maxUploadMb: int


class GenerateResponse(BaseModel):
    imageBase64: str
    filename: str
    promptSha256: str
    structuredBriefSha256: str
    topic: str
    citation: str


class GenerateJobStartResponse(BaseModel):
    jobId: str
    status: str


class GenerateJobStatusResponse(BaseModel):
    jobId: str
    status: str
    result: GenerateResponse | None = None
    error: str | None = None


app = FastAPI(title="UAB Medicine Infographic Generator", version="2026.7.31")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

_generation_jobs: dict[str, dict[str, object]] = {}
_JOB_TTL_SECONDS = 60 * 60


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", response_model=ConfigResponse)
def config() -> ConfigResponse:
    return ConfigResponse(
        audiences=available_audiences(),
        styles=available_styles(),
        defaultAudience="academic",
        defaultStyle="uab-corporate",
        maxUploadMb=10,
    )


async def _collect_source_uploads(files: list[UploadFile]) -> list[SourceUpload]:
    source_uploads: list[SourceUpload] = []
    for upload in files:
        content = await upload.read()
        source_uploads.append(SourceUpload(name=upload.filename or "source", content=content))
    return source_uploads


def _generate_response_from_inputs(
    *,
    audience: str,
    style: str,
    context: str,
    source_uploads: list[SourceUpload],
) -> GenerateResponse:
    result = generate_infographic(
        audience=audience,
        style_id=style,
        user_context=context,
        files=source_uploads,
    )
    profile = result.inferred_profile or {}
    citation = str(profile.get("citation_footer") or "").strip()
    topic = str(profile.get("topic") or profile.get("citation_title") or "Generated infographic").strip()
    return GenerateResponse(
        imageBase64=base64.b64encode(result.image_bytes).decode("ascii"),
        filename=result.filename,
        promptSha256=result.prompt_sha256,
        structuredBriefSha256=result.structured_brief_sha256,
        topic=topic,
        citation=citation,
    )


def _prune_generation_jobs() -> None:
    now = time.time()
    stale_job_ids = [
        job_id
        for job_id, job in _generation_jobs.items()
        if now - float(job.get("created_at", now)) > _JOB_TTL_SECONDS
    ]
    for job_id in stale_job_ids:
        _generation_jobs.pop(job_id, None)


async def _run_generation_job(
    job_id: str,
    *,
    audience: str,
    style: str,
    context: str,
    source_uploads: list[SourceUpload],
) -> None:
    _generation_jobs[job_id]["status"] = "running"
    try:
        response = await anyio.to_thread.run_sync(
            lambda: _generate_response_from_inputs(
                audience=audience,
                style=style,
                context=context,
                source_uploads=source_uploads,
            )
        )
        _generation_jobs[job_id].update(status="succeeded", result=response, completed_at=time.time())
    except Exception as exc:  # noqa: BLE001 - surface user-facing generation failure text
        _generation_jobs[job_id].update(status="failed", error=str(exc), completed_at=time.time())


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(
    audience: Annotated[str, Form()],
    style: Annotated[str, Form()],
    context: Annotated[str, Form()] = "",
    phiConfirmed: Annotated[bool, Form()] = False,
    files: Annotated[list[UploadFile], File()] = [],
) -> GenerateResponse:
    if not phiConfirmed:
        raise HTTPException(status_code=400, detail="Confirm that the content does not contain PHI.")

    source_uploads = await _collect_source_uploads(files)

    try:
        result = await anyio.to_thread.run_sync(
            lambda: _generate_response_from_inputs(
                audience=audience,
                style=style,
                context=context,
                source_uploads=source_uploads,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


@app.post("/api/generate-jobs", response_model=GenerateJobStartResponse)
async def start_generate_job(
    audience: Annotated[str, Form()],
    style: Annotated[str, Form()],
    context: Annotated[str, Form()] = "",
    phiConfirmed: Annotated[bool, Form()] = False,
    files: Annotated[list[UploadFile], File()] = [],
) -> GenerateJobStartResponse:
    if not phiConfirmed:
        raise HTTPException(status_code=400, detail="Confirm that the content does not contain PHI.")

    source_uploads = await _collect_source_uploads(files)
    job_id = str(uuid.uuid4())
    _prune_generation_jobs()
    _generation_jobs[job_id] = {"status": "queued", "created_at": time.time()}
    asyncio.create_task(
        _run_generation_job(
            job_id,
            audience=audience,
            style=style,
            context=context,
            source_uploads=source_uploads,
        )
    )
    return GenerateJobStartResponse(jobId=job_id, status="queued")


@app.get("/api/generate-jobs/{job_id}", response_model=GenerateJobStatusResponse)
def get_generate_job(job_id: str) -> GenerateJobStatusResponse:
    _prune_generation_jobs()
    job = _generation_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job was not found.")
    return GenerateJobStatusResponse(
        jobId=job_id,
        status=str(job.get("status", "unknown")),
        result=job.get("result") if isinstance(job.get("result"), GenerateResponse) else None,
        error=str(job.get("error")) if job.get("error") else None,
    )


def _react_file_response(path: str) -> FileResponse:
    target = (DIST_DIR / path).resolve()
    if path and DIST_DIR.resolve() in target.parents and target.is_file():
        return FileResponse(target)

    requested = Path(path)
    if path.startswith("api/") or requested.name.startswith(".") or requested.suffix:
        raise HTTPException(status_code=404, detail="Not found.")

    index = DIST_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend has not been built.")
    return FileResponse(index)


@app.get("/{path:path}")
def serve_react_app(path: str) -> FileResponse:
    return _react_file_response(path)


@app.head("/{path:path}")
def head_react_app(path: str) -> FileResponse:
    return _react_file_response(path)
