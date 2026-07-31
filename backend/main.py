"""FastAPI server for the React UAB Medicine Infographic Generator."""

from __future__ import annotations

import base64
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


app = FastAPI(title="UAB Medicine Infographic Generator", version="2026.7.31")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


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

    source_uploads: list[SourceUpload] = []
    for upload in files:
        content = await upload.read()
        source_uploads.append(SourceUpload(name=upload.filename or "source", content=content))

    try:
        result = await anyio.to_thread.run_sync(
            lambda: generate_infographic(
                audience=audience,
                style_id=style,
                user_context=context,
                files=source_uploads,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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


def _react_file_response(path: str) -> FileResponse:
    target = DIST_DIR / path
    if path and target.is_file():
        return FileResponse(target)
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
