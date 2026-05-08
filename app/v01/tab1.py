import base64
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from infographic.parsers import extract_document_text
from infographic.cleanup import clean_document_text_llm
from infographic.image_service import (
    fetch_image_bytes,
    generate_image,
    make_client,
    composite_logo_footer,
    resolve_logo_path,
)
from infographic.prompts import build_infographic_prompt
from infographic.constants import LITELLM_IMAGE_MODEL


logger = logging.getLogger(__name__)

router = APIRouter(tags=["InfographicGeneration"])


class GenerateRequest(BaseModel):
    file_encoded: str = Field(..., description="Base64-encoded document content")
    extension: str = Field(..., description="File extension: '.pdf', '.docx', '.txt'")
    style: str = Field(default="uab-craft-handmade", description="Visual style key")
    audience: str = Field(default="academic", description="Target audience")
    user_context: str = Field(default="", description="Optional context/goal")
    size: str = Field(default="1792x1024", description="Image size")
    quality: str = Field(default="high", description="Image quality")
    openai_compatible_endpoint: str = Field(
        ..., description="OpenAI-compatible endpoint URL"
    )
    openai_compatible_model: str = Field(..., description="Model name to use")


class GenerateResponse(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded PNG image")
    prompt_used: str = Field(..., description="The prompt that was sent to the model")


@router.post("/v01/generate")
async def generate_infographic(
    background_tasks: BackgroundTasks,
    request: GenerateRequest,
    authorization: str = Header(...),
) -> GenerateResponse:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    api_key = authorization[7:]

    try:
        extension = request.extension.lower().strip()
        if not extension.startswith("."):
            extension = f".{extension}"

        file_bytes = base64.b64decode(request.file_encoded)

        class FakeFile:
            def __init__(self, name: str, data: bytes):
                self.name = name
                self._data = data

            def getvalue(self) -> bytes:
                return self._data

        fake_file = FakeFile(f"doc{extension}", file_bytes)
        extracted_text = extract_document_text(fake_file)

        client = make_client("openai", api_key, request.openai_compatible_endpoint, None)

        result = clean_document_text_llm(
            client, "openai", request.openai_compatible_model, extracted_text
        )
        cleaned_docs = [result.text]

        prompt = build_infographic_prompt(
            style_id=request.style,
            user_context=request.user_context,
            cleaned_document_texts=cleaned_docs,
            audience_key=request.audience,
            refinement_notes="",
            logo_instructions_extra="",
        )

        image_url_or_data = generate_image(
            client,
            "openai",
            request.openai_compatible_model,
            prompt,
            request.size,
            request.quality,
        )
        image_bytes = fetch_image_bytes(image_url_or_data)

        logo_path = resolve_logo_path()
        if logo_path and image_bytes:
            image_bytes = composite_logo_footer(image_bytes, logo_path, request.style)

        return GenerateResponse(
            image_b64=base64.b64encode(image_bytes).decode("utf-8"),
            prompt_used=prompt,
        )

    except Exception as e:
        logger.error(f"Generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e