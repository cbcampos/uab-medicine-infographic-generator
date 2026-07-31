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
    build_pin_edit_mask,
    build_guided_refinement_notes,
    composite_logo_footer,
    edit_image_with_mask,
    fetch_image_bytes,
    generate_with_retry,
    make_client,
    optimize_azure_image_prompt,
    remove_logo_safe_zone_for_edit,
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
    refinement_notes: str = "",
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
            refinement_notes=refinement_notes,
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
        refinement_notes,
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


def revise_infographic_with_pins(
    *,
    audience: str,
    style_id: str,
    user_context: str,
    files: list[SourceUpload],
    current_image_bytes: bytes,
    pinned_comments: list[dict[str, object]],
    paint_mask_bytes: bytes | None = None,
    current_topic: str = "",
    current_citation: str = "",
    session_id: str | None = None,
) -> GenerationResult:
    if not pinned_comments:
        raise ValueError("Add at least one pinned edit comment before applying edits.")

    pin_lines: list[str] = []
    for index, pin in enumerate(pinned_comments, start=1):
        try:
            x_pct = float(pin.get("xPercent", 0))
            y_pct = float(pin.get("yPercent", 0))
        except (TypeError, ValueError):
            x_pct = 0
            y_pct = 0
        comment = str(pin.get("comment") or "").strip()
        if not comment:
            continue
        pin_lines.append(f"{index}. (x: {x_pct:.1f}%, y: {y_pct:.1f}%) {comment}")
    if not pin_lines:
        raise ValueError("Pinned edits need comment text.")

    pinned_block = "Image 1 pinned edit requests:\n" + "\n".join(pin_lines)
    api_key, endpoint, image_model, chat_model = validate_azure_config()
    vision_model = os.environ.get("AZURE_OPENAI_VISION_DEPLOYMENT", chat_model).strip() or chat_model
    client = make_client("azure", api_key, endpoint, "2024-02-01")

    try:
        guided_notes = build_guided_refinement_notes(
            client=client,
            vision_model=vision_model,
            current_image_bytes=current_image_bytes,
            user_notes=pinned_block,
            audience=audience,
        )
    except Exception:
        guided_notes = ""

    refinement_notes = "\n\n".join(
        part
        for part in [
            "This is a revision pass for a previously generated infographic.",
            pinned_block,
            "Preserve the successful overall topic, citation, audience, visual style, footer, and UAB logo safe area.",
            "Apply the requested edits at the indicated normalized image coordinates. Treat coordinates as anchors; adjust surrounding layout if needed.",
            guided_notes,
        ]
        if part.strip()
    )

    edit_prompt = "\n".join(
        [
            "You are editing a UAB Medicine scientific infographic image.",
            "Apply only the pinned edit requests below, using the coordinates as visual anchors.",
            "If a painted mask is provided, treat the painted region as the exact scope for the requested local edit.",
            "Preserve the existing scientific topic, layout logic, source citation, audience framing, and visual style unless a pinned request specifically changes that local area.",
            "Do not invent new data, new citations, new recommendations, or unsupported numbers.",
            "Keep the bottom-right logo/footer area clean and empty; the approved UAB Medicine logo will be composited by the application after this edit.",
            "Return a complete polished infographic image, not an explanation.",
            "",
            pinned_block,
            "",
            "Vision-derived implementation notes:",
            guided_notes,
        ]
    ).strip()

    try:
        editable_image_bytes = remove_logo_safe_zone_for_edit(current_image_bytes)
        mask_bytes = build_pin_edit_mask(editable_image_bytes, pinned_comments, paint_mask_bytes)
        image_ref = edit_image_with_mask(
            client=client,
            provider="azure",
            model=image_model,
            image_bytes=editable_image_bytes,
            mask_bytes=mask_bytes,
            prompt=edit_prompt,
            size="1792x1024",
            quality="high",
        )
        edited_bytes = fetch_image_bytes(image_ref)
        logo_path = resolve_logo_path()
        if logo_path:
            edited_bytes = composite_logo_footer(edited_bytes, logo_path, style_id)
        prompt_sha = hashlib.sha256(edit_prompt.encode("utf-8")).hexdigest()
        result_profile = {
            "topic": current_topic.strip() or "Edited infographic",
            "citation_footer": current_citation.strip(),
            "citation_title": current_topic.strip(),
        }
        base = download_basename_from_profile(result_profile, user_context)
        audit_log(
            session_id or str(uuid.uuid4()),
            "azure",
            style_id,
            audience,
            True,
            0,
            "react_image_edit",
            {
                "edit_mode": "masked_image_edit",
                "prompt_sha256": prompt_sha,
                "pin_count": len(pin_lines),
            },
        )
        return GenerationResult(
            image_bytes=edited_bytes,
            filename=png_filename(base, style_id, audience, "edited"),
            prompt_sha256=prompt_sha,
            prompt_text=edit_prompt,
            structured_brief_sha256="image-edit",
            inferred_profile=result_profile,
        )
    except Exception:
        # Some Azure image deployments/proxies support generation before edits. Preserve
        # the UX by falling back to a full revision generation using the same pinned notes.
        pass

    result = generate_infographic(
        audience=audience,
        style_id=style_id,
        user_context=user_context,
        files=files,
        refinement_notes=refinement_notes,
        session_id=session_id,
    )
    base = download_basename_from_profile(result.inferred_profile, user_context)
    return GenerationResult(
        image_bytes=result.image_bytes,
        filename=png_filename(base, style_id, audience, "edited"),
        prompt_sha256=result.prompt_sha256,
        prompt_text=result.prompt_text,
        structured_brief_sha256=result.structured_brief_sha256,
        inferred_profile=result.inferred_profile,
    )
