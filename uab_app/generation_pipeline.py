"""Backend generation pipeline shared by the React API.

This module intentionally keeps model credentials and provider details server-side.
It reuses the existing UAB prompt, planning, cleanup, image generation, and logo
compositing modules instead of duplicating generation behavior in the frontend.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uab_app.audit import audit_log
from uab_app.cleanup import clean_document_text_llm, infer_source_profile_llm
from uab_app.constants import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_BYTES
from uab_app.image_service import (
    AZURE_IMAGE_PROMPT_MAX_CHARS,
    AZURE_IMAGE_PROMPT_SAFETY_MARGIN,
    composite_logo_footer,
    fetch_image_bytes,
    generate_with_retry,
    make_client,
    optimize_azure_image_prompt,
    resolve_logo_path,
)
from uab_app.parsers import extract_document_text
from uab_app.planning import (
    build_structured_visual_brief,
    format_structured_brief_for_prompt,
    structured_brief_sha256,
)
from uab_app.prompts import build_infographic_prompt
from uab_app.sanitize import regex_cleanup_fallback, sanitize_input
from uab_app.styles import STYLES


PRODUCTION_STYLE_KEYS = ("uab-corporate", "uab-craft-handmade")
AUDIENCE_KEYS = ("academic", "clinical", "patient", "community")
AUDIENCE_LABELS = {
    "academic": "Academic / research",
    "clinical": "Clinical / HCP",
    "patient": "Patient / lay audience",
    "community": "Community outreach",
}


@dataclass(frozen=True)
class SourceUpload:
    """Small adapter matching the parser interface used by the Streamlit app."""

    name: str
    content: bytes

    def getvalue(self) -> bytes:
        return self.content


@dataclass(frozen=True)
class GenerationResult:
    image_bytes: bytes
    filename: str
    prompt_sha256: str
    prompt_text: str
    structured_brief_sha256: str
    inferred_profile: dict[str, Any]


def available_styles() -> list[dict[str, str]]:
    return [
        {
            "id": key,
            "name": STYLES[key]["name"],
            "description": STYLES[key].get("description", ""),
            "sampleUrl": f"/assets/style_examples/{key}.png",
        }
        for key in PRODUCTION_STYLE_KEYS
        if key in STYLES
    ]


def available_audiences() -> list[dict[str, str]]:
    return [{"id": key, "label": AUDIENCE_LABELS[key]} for key in AUDIENCE_KEYS]


def validate_azure_config() -> tuple[str, str, str, str]:
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    image_model = os.environ.get("AZURE_OPENAI_IMAGE_MODEL", "gpt-image-2").strip()
    chat_model = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini").strip()
    if not api_key or not endpoint:
        raise RuntimeError("Azure generation is not configured on this server.")
    return api_key, endpoint, image_model, chat_model


def slugify_filename(value: str, max_len: int = 80) -> str:
    text = value.strip().lower()
    text = re.sub(r"['`]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return (text[:max_len].rstrip("-") or "infographic")


def download_basename_from_profile(
    profile: dict[str, Any] | None,
    user_context: str = "",
) -> str:
    profile = profile or {}
    candidates = (
        str(profile.get("citation_title") or ""),
        str(profile.get("topic") or ""),
        str(profile.get("objective") or ""),
        user_context,
    )
    for candidate in candidates:
        if candidate.strip():
            return slugify_filename(candidate)
    return "infographic"


def png_filename(base: str, *parts: str) -> str:
    suffix = "-".join(
        slugify_filename(str(part), max_len=36)
        for part in parts
        if str(part).strip()
    )
    stem = slugify_filename(base)
    return f"{stem}-{suffix}.png" if suffix else f"{stem}.png"


def source_profile_to_dict(inferred: Any) -> dict[str, Any]:
    return {
        "topic": inferred.topic,
        "objective": inferred.objective,
        "source_type": inferred.source_type,
        "why_matters": inferred.why_matters,
        "key_points": inferred.key_points,
        "recommended_sections": inferred.recommended_sections,
        "chart_guidance": inferred.chart_guidance,
        "citation_title": inferred.citation_title,
        "citation_journal": inferred.citation_journal,
        "citation_year": inferred.citation_year,
        "citation_authors_short": inferred.citation_authors_short,
        "citation_footer": inferred.citation_footer,
        "implications_panel": inferred.implications_panel,
        "claim_evidence_pairs": inferred.claim_evidence_pairs,
        "non_numeric_mode": inferred.non_numeric_mode,
    }


def extract_upload_texts(files: list[SourceUpload]) -> tuple[list[tuple[str, str]], list[str]]:
    extracted: list[tuple[str, str]] = []
    issues: list[str] = []
    for file in files:
        ext = Path(file.name).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            issues.append(f"{file.name}: unsupported file type")
            continue
        if len(file.content) > MAX_UPLOAD_BYTES:
            issues.append(f"{file.name}: file exceeds 10 MB")
            continue
        text = extract_document_text(file)
        if text.strip():
            extracted.append((file.name, text))
        else:
            issues.append(f"{file.name}: no readable text was extracted")
    return extracted, issues


def generate_infographic(
    *,
    audience: str,
    style_id: str,
    user_context: str,
    files: list[SourceUpload],
    session_id: str | None = None,
) -> GenerationResult:
    if audience not in AUDIENCE_KEYS:
        raise ValueError("Unsupported audience.")
    if style_id not in PRODUCTION_STYLE_KEYS or style_id not in STYLES:
        raise ValueError("Unsupported visual style.")

    sanitized_context, context_flags = sanitize_input(user_context or "", source="user")
    extracted, file_issues = extract_upload_texts(files)
    combined_docs = "\n".join(text for _, text in extracted)
    _, doc_flags = sanitize_input(combined_docs, source="document")
    if context_flags or doc_flags:
        raise ValueError("Possible prompt-injection patterns were detected. Revise the text before generating.")
    if file_issues and not extracted and not sanitized_context.strip():
        raise ValueError("; ".join(file_issues))
    if not sanitized_context.strip() and not extracted:
        raise ValueError("Add optional context or upload at least one source document.")

    api_key, endpoint, image_model, chat_model = validate_azure_config()
    client = make_client("azure", api_key, endpoint, "2024-02-01")
    session_id = session_id or str(uuid.uuid4())

    cleaned_docs: list[str] = []
    for name, raw in extracted:
        clean_input, _ = sanitize_input(raw, source="document")
        if not clean_input.strip():
            continue
        try:
            cleaned = clean_document_text_llm(client, "azure", chat_model, clean_input)
            cleaned_docs.append(cleaned.text)
        except Exception:
            cleaned_docs.append(regex_cleanup_fallback(clean_input))

    inferred = infer_source_profile_llm(
        client=client,
        provider="azure",
        chat_model=chat_model,
        user_context=sanitized_context,
        cleaned_document_texts=cleaned_docs,
        audience=audience,
    )
    inferred_profile = source_profile_to_dict(inferred)

    structured_brief_block = ""
    structured_hash = ""
    try:
        brief = build_structured_visual_brief(
            client,
            chat_model,
            user_context=sanitized_context,
            cleaned_document_texts=cleaned_docs,
            audience=audience,
            style_id=style_id,
            inferred_profile=inferred_profile,
            chart_reference_block="",
            refinement_notes="",
        )
        structured_brief_block = format_structured_brief_for_prompt(brief)
        structured_hash = structured_brief_sha256(structured_brief_block)
    except Exception:
        structured_brief_block = ""
        structured_hash = ""

    logo_extra = ""
    if resolve_logo_path():
        logo_extra = (
            "\n- Technical note for layout: The application will composite the approved logo "
            "into the bottom-right corner after generation; keep that corner empty.\n"
        )

    prompt = build_infographic_prompt(
        style_id,
        sanitized_context,
        cleaned_docs,
        audience,
        "",
        logo_extra,
        chart_reference_block="",
        inferred_profile=inferred_profile,
        structured_brief_block=structured_brief_block,
    )
    max_prompt_len = AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
    effective_prompt = optimize_azure_image_prompt(prompt, max_prompt_len)
    prompt_sha = hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest()

    image_ref = generate_with_retry(
        client,
        "azure",
        image_model,
        effective_prompt,
        "1792x1024",
        "high",
        progress_callback=None,
    )
    image_bytes = fetch_image_bytes(image_ref)
    logo_path = resolve_logo_path()
    if logo_path:
        image_bytes = composite_logo_footer(image_bytes, logo_path, style_id)

    base = download_basename_from_profile(inferred_profile, sanitized_context)
    filename = png_filename(base, style_id, audience)
    audit_log(
        session_id,
        "azure",
        style_id,
        audience,
        True,
        0,
        "react_image_generation",
        {
            "structured_planning_enabled": True,
            "structured_brief_sha256": structured_hash,
            "prompt_sha256": prompt_sha,
        },
    )
    return GenerationResult(
        image_bytes=image_bytes,
        filename=filename,
        prompt_sha256=prompt_sha,
        prompt_text=effective_prompt,
        structured_brief_sha256=structured_hash,
        inferred_profile=inferred_profile,
    )
