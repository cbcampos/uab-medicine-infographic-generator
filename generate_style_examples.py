"""Generate one style example image per available style.

Usage:
  .venv/bin/python generate_style_examples.py "/path/to/source.pdf"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from uab_app.cleanup import clean_document_text_llm, infer_source_profile_llm
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
from uab_app.parsers import read_pdf
from uab_app.prompts import build_infographic_prompt
from uab_app.sanitize import regex_cleanup_fallback, sanitize_input
from uab_app.styles import STYLES


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: .venv/bin/python generate_style_examples.py /absolute/path/to/source.pdf")
        return 2

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}")
        return 2

    load_dotenv(Path(__file__).resolve().parent / ".env")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    image_model = os.getenv("AZURE_OPENAI_IMAGE_MODEL", "gpt-image-2").strip()
    chat_model = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini").strip()
    if not api_key or not endpoint:
        print("Missing AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT in environment/.env.")
        return 2

    with pdf_path.open("rb") as f:
        raw_text = read_pdf(f) or ""
    cleaned_doc_seed = regex_cleanup_fallback(raw_text)
    cleaned_doc_seed = sanitize_input(cleaned_doc_seed, source="document")[0]
    if not cleaned_doc_seed.strip():
        print("Could not extract text from PDF.")
        return 2

    # Keep context neutral to avoid steering beyond the source.
    user_context = ""
    logo_extra = (
        "\n- Technical note for layout: Leave the bottom edge clear or use a plain white band; "
        "the application may composite the approved logo file onto a white footer after generation.\n"
    )
    client = make_client("azure", api_key, endpoint, "2024-02-01")
    logo_path = resolve_logo_path()
    out_dir = Path(__file__).resolve().parent / "assets" / "style_examples"
    out_dir.mkdir(parents=True, exist_ok=True)
    max_prompt_len = AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
    print("[PIPELINE] Running LLM cleanup to mirror Streamlit flow...")
    cleanup_result = clean_document_text_llm(client, "azure", chat_model, cleaned_doc_seed)
    cleaned_doc = cleanup_result.text if cleanup_result and cleanup_result.text else cleaned_doc_seed
    print("[PIPELINE] Running source-profile inference to mirror Streamlit flow...")
    inferred = infer_source_profile_llm(
        client=client,
        provider="azure",
        chat_model=chat_model,
        user_context=user_context,
        cleaned_document_texts=[cleaned_doc],
        audience="community",
    )
    inferred_profile = {
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

    for style_key in STYLES.keys():
        out_path = out_dir / f"{style_key}.png"
        if out_path.is_file():
            print(f"[STYLE] {style_key} (skip: already exists)")
            continue
        print(f"[STYLE] {style_key}")
        prompt = build_infographic_prompt(
            style_id=style_key,
            user_context=user_context,
            cleaned_document_texts=[cleaned_doc],
            audience_key="community",
            refinement_notes="",
            logo_instructions_extra=logo_extra,
            chart_reference_block="",
            inferred_profile=inferred_profile,
        )
        effective_prompt = optimize_azure_image_prompt(prompt, max_prompt_len)
        image_ref = generate_with_retry(
            client=client,
            provider="azure",
            model=image_model,
            prompt=effective_prompt,
            size="1792x1024",
            quality="high",
            progress_callback=None,
        )
        image_bytes = fetch_image_bytes(image_ref)
        if logo_path:
            image_bytes = composite_logo_footer(image_bytes, logo_path, style_key)
        out_path.write_bytes(image_bytes)
        print(f"  -> wrote {out_path}")

    print(f"Done. Style examples saved to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
