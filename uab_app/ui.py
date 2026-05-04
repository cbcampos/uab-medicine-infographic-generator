"""Streamlit UI for the Infographic Generator."""

from __future__ import annotations

import base64
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from uab_app.audit import audit_log
from uab_app.charts import (
    chart_dict_to_dataclass,
    format_chart_reference_for_prompt,
    format_publication_fidelity_qa_markdown,
    gpt4o_extract_chart_from_image,
    merge_extraction_into_chart,
    new_chart_id,
    parse_csv_to_data_series,
    parse_json_data_file,
    parse_xlsx_to_data_series,
    publication_reference_preflight_issues,
    refresh_chart_reference_hints,
    run_publication_fidelity_qa,
    run_post_generation_chart_qa,
)
from uab_app.cleanup import cache_key_for_raw, clean_document_text_llm
from uab_app.constants import (
    ALLOWED_UPLOAD_EXTENSIONS,
    CHART_MODES,
    GEMINI_DEFAULT_CHAT_MODEL,
    GEMINI_DEFAULT_IMAGE_MODEL,
    GEMINI_DEFAULT_VISION_MODEL,
    MAX_CHART_UPLOAD_BYTES,
    MAX_UPLOAD_BYTES,
    OPENAI_DEFAULT_CHAT_MODEL,
    OPENAI_DEFAULT_IMAGE_MODEL,
    OPENAI_DEFAULT_VISION_MODEL,
)
from uab_app.image_service import (
    composite_logo_footer,
    fetch_image_bytes,
    generate_with_retry,
    make_client,
    resolve_logo_path,
    user_friendly_error,
)
from uab_app.parsers import extract_document_text
from uab_app.prompts import build_infographic_prompt
from uab_app.sanitize import injection_labels_for_ids, regex_cleanup_fallback, sanitize_input
from uab_app.styles import STYLES


def _data_series_has_meaningful_numbers(series: list[dict[str, Any]]) -> bool:
    """True if extracted/manual rows likely contain chart numbers for prompt use."""
    if not series:
        return False
    numeric_keys = (
        "median",
        "mean",
        "value",
        "range_low",
        "range_high",
        "point_estimate",
        "lower_ci",
        "upper_ci",
        "n",
    )
    for row in series:
        if not isinstance(row, dict):
            continue
        for k in numeric_keys:
            v = row.get(k)
            if v is None or v == "":
                continue
            try:
                float(str(v).replace(",", ""))
                return True
            except (TypeError, ValueError):
                pass
        if str(row.get("value", "")).strip() or str(row.get("group", "")).strip():
            return True
    return False


def chart_figure_pending_extraction(chart: dict[str, Any]) -> bool:
    """Uploaded figure image with no usable numeric table yet — needs vision extraction."""
    if chart.get("verification_status") == "removed":
        return False
    if not chart.get("_bytes_b64"):
        return False
    return not _data_series_has_meaningful_numbers(chart.get("data_series") or [])


def run_chart_figure_vision_extract(
    chart: dict[str, Any],
    client: Any,
    cross_text: str,
    snippet_txt: str,
    vision_model: str,
    doc_cross_check: bool,
) -> None:
    raw_b = base64.b64decode(chart["_bytes_b64"])
    mime = chart.get("_mime") or "image/png"
    extra = snippet_txt[:8000] if snippet_txt.strip() else cross_text[:8000]
    raw_j = gpt4o_extract_chart_from_image(
        client, raw_b, mime, vision_model, extra
    )
    cd = chart_dict_to_dataclass(chart)
    merge_extraction_into_chart(raw_j, cd)
    chart.update(cd.to_dict())
    chart["_bytes_b64"] = chart.get("_bytes_b64")
    chart["_mime"] = chart.get("_mime")
    refresh_chart_reference_hints(chart, cross_text, doc_cross_check)


# ─── Session init ───────────────────────────────────────────────
def init_session_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "comparison_results" not in st.session_state:
        st.session_state.comparison_results = []
    if "last_prompt" not in st.session_state:
        st.session_state.last_prompt = ""
    if "last_image_bytes" not in st.session_state:
        st.session_state.last_image_bytes = None
    if "generation_history" not in st.session_state:
        st.session_state.generation_history = []
    if "refinement_notes" not in st.session_state:
        st.session_state.refinement_notes = ""
    if "charts" not in st.session_state:
        st.session_state.charts = []
    if "processed_chart_figure_sigs" not in st.session_state:
        st.session_state.processed_chart_figure_sigs = set()
    if "processed_data_file_sigs" not in st.session_state:
        st.session_state.processed_data_file_sigs = set()
    if "last_chart_reference_block" not in st.session_state:
        st.session_state.last_chart_reference_block = ""
    if "chart_extraction_nonce" not in st.session_state:
        st.session_state.chart_extraction_nonce = 0
    if "cleaned_docs_cache" not in st.session_state:
        st.session_state.cleaned_docs_cache = {}
    if "last_fidelity_qa_result" not in st.session_state:
        st.session_state.last_fidelity_qa_result = None
    if "last_fidelity_qa_pass" not in st.session_state:
        st.session_state.last_fidelity_qa_pass = False
    if "last_post_gen_chart_qa_text" not in st.session_state:
        st.session_state.last_post_gen_chart_qa_text = ""


def set_progress(
    progress_bar: Any,
    status_text: Any,
    stage: str,
    frac: float,
) -> None:
    progress_bar.progress(min(1.0, max(0.0, frac)))
    status_text.markdown(
        "**Progress:** Cleaning document text → Building prompt → Submitting to API "
        "→ Fetching image → Displaying  \n"
        f"**Current:** {stage}"
    )


# ─── Main app ───────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="Infographic Generator",
        page_icon="🖼️",
        layout="wide",
    )
    init_session_state()
    session_id = st.session_state.session_id

    st.markdown(
        "<div style='background:#1A5632;padding:16px 24px;border-radius:8px;margin-bottom:24px'>"
        "<h1 style='color:white;margin:0;font-family:Source Sans Pro,sans-serif'>"
        "🖼️ Infographic Generator</h1>"
        "<p style='color:#FFC72C;margin:4px 0 0;font-size:14px'>"
        "Image generation via OpenAI, Azure OpenAI, or Gemini"
        "</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### ⚙️ API Configuration")
        provider = st.radio(
            "Provider",
            options=["openai", "azure", "gemini"],
            format_func=lambda p: (
                "🟢 OpenAI (GPT Image 2)"
                if p == "openai"
                else ("🔷 Azure OpenAI (GPT Image 2)" if p == "azure" else "🟣 Gemini")
            ),
            index=0,
            horizontal=False,
            key="sidebar_provider",
        )
        st.markdown("---")

        if provider == "openai":
            with st.expander("🔑 OpenAI", expanded=True):
                st.text_input(
                    "API Key",
                    value=os.environ.get("OPENAI_API_KEY", ""),
                    key="openai_api_key",
                    type="password",
                )
                st.text_input(
                    "Chat model (document cleanup)",
                    value=os.environ.get("OPENAI_CHAT_MODEL", OPENAI_DEFAULT_CHAT_MODEL),
                    key="openai_chat_model",
                    help="e.g. gpt-4o-mini",
                )
                st.text_input(
                    "Vision model (chart extraction / QA)",
                    value=os.environ.get("OPENAI_VISION_MODEL", OPENAI_DEFAULT_VISION_MODEL),
                    key="openai_vision_model",
                    help="Use a vision-capable model (e.g. gpt-4o)",
                )
                st.text_input(
                    "Image model (GPT Image)",
                    value=os.environ.get("OPENAI_IMAGE_MODEL", OPENAI_DEFAULT_IMAGE_MODEL),
                    key="openai_image_model",
                    help="Must match an image-capable model available to your API key (e.g. gpt-image-2).",
                )
        elif provider == "azure":
            with st.expander("🔷 Azure OpenAI", expanded=True):
                st.text_input(
                    "API Key",
                    value=os.environ.get("AZURE_OPENAI_API_KEY", ""),
                    key="azure_api_key",
                    type="password",
                )
                st.text_input(
                    "Endpoint",
                    value=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
                    key="azure_endpoint",
                    placeholder="https://your-resource.openai.azure.com",
                )
                st.text_input(
                    "API Version",
                    value=os.environ.get(
                        "AZURE_OPENAI_API_VERSION",
                        "2024-12-01-preview",
                    ),
                    key="azure_api_version",
                )
                st.text_input(
                    "Image deployment",
                    value=os.environ.get("AZURE_OPENAI_IMAGE_MODEL", "gpt-image-2"),
                    key="azure_image_model",
                )
                st.text_input(
                    "Chat deployment (cleanup)",
                    value=os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"),
                    key="azure_chat_model",
                    help="Azure deployment name for text cleanup",
                )
                st.text_input(
                    "Vision deployment (chart extraction / QA)",
                    value=os.environ.get(
                        "AZURE_OPENAI_VISION_DEPLOYMENT",
                        OPENAI_DEFAULT_VISION_MODEL,
                    ),
                    key="azure_vision_deployment",
                    help="Deployment name for gpt-4o-class vision model",
                )
        else:
            with st.expander("🟣 Gemini", expanded=True):
                st.text_input(
                    "API Key",
                    value=os.environ.get("GEMINI_API_KEY", ""),
                    key="gemini_api_key",
                    type="password",
                )
                st.text_input(
                    "Chat model (document cleanup)",
                    value=os.environ.get("GEMINI_CHAT_MODEL", GEMINI_DEFAULT_CHAT_MODEL),
                    key="gemini_chat_model",
                    help="e.g. gemini-2.5-pro",
                )
                st.text_input(
                    "Vision model (chart extraction / QA)",
                    value=os.environ.get("GEMINI_VISION_MODEL", GEMINI_DEFAULT_VISION_MODEL),
                    key="gemini_vision_model",
                    help="Vision-capable Gemini model, e.g. gemini-2.5-pro",
                )
                st.text_input(
                    "Image model",
                    value=os.environ.get("GEMINI_IMAGE_MODEL", GEMINI_DEFAULT_IMAGE_MODEL),
                    key="gemini_image_model",
                    help=(
                        "Gemini image-generation model ID, e.g. gemini-3-pro-image-preview "
                        "(Nano Banana Pro). Names like nano-banana-pro are rewritten to the API ID automatically."
                    ),
                )

        st.markdown("---")
        st.markdown("### 🎯 Generation mode")
        mode = st.radio(
            "Mode",
            options=["single", "compare"],
            format_func=lambda m: "Single style" if m == "single" else "Style comparison (3-way)",
            key="gen_mode",
        )

        st.markdown("### 📋 Style (single mode)")
        selected_style_key = st.selectbox(
            "Visual style",
            options=list(STYLES.keys()),
            format_func=lambda k: STYLES[k]["name"],
            index=0,
            key="single_style",
            disabled=(mode == "compare"),
        )
        if mode == "single":
            st.caption(STYLES[selected_style_key]["description"])

        st.markdown("---")
        logo_path = resolve_logo_path()
        if logo_path:
            st.success(f"Logo file found: `{logo_path.name}`")
        else:
            st.info(
                "Place `uab-medicine-logo.jpg` in `assets/` or set `UAB_MEDICINE_LOGO_PATH`. "
                "Post-processing will composite the approved logo when the file is available."
            )

    col_main, col_doc = st.columns([1, 1])

    with col_main:
        st.markdown("### 👥 Audience")
        audience = st.radio(
            "Who is this for?",
            options=["academic", "clinical", "patient", "community"],
            format_func=lambda a: {
                "academic": "Academic / research",
                "clinical": "Clinical / HCP",
                "patient": "Patient / lay audience",
                "community": "Community outreach",
            }[a],
            horizontal=True,
            key="audience_radio",
        )

        st.markdown("### ✏️ Context")
        user_context = st.text_area(
            "Describe the topic and key points",
            height=200,
            key="user_context_area",
            placeholder="Be specific. Only include data you want shown on charts.",
        )

        size = st.selectbox(
            "Image size",
            options=["1024x1024", "1024x1792", "1792x1024"],
            index=2,
            key="img_size",
            help="1792x1024 = 16:9 (recommended for infographics) | 2K (2560x1440) is supported but flagged as experimental by OpenAI — results may be more variable above this resolution",
        )
        quality = st.selectbox("Quality", options=["high", "medium"], index=0, key="img_quality")
        publication_fidelity_mode = st.checkbox(
            "Publication fidelity mode (strict chart/text/citation QA before download)",
            value=False,
            key="publication_fidelity_mode",
        )
        expected_citation = ""
        preferred_terms: list[str] = []
        if publication_fidelity_mode:
            expected_citation = st.text_input(
                "Expected citation text (optional)",
                value="",
                key="expected_citation_text",
                placeholder="e.g. JACC: Heart Failure. 2026;14(2):102686. doi:10.1016/j.jchf.2025.102686",
            )
            terms_raw = st.text_input(
                "Preferred terminology (comma-separated)",
                value="Prediabetes,myocardial infarction",
                key="preferred_terms_text",
            )
            preferred_terms = [x.strip() for x in terms_raw.split(",") if x.strip()]

        compare_style_keys: list[str] = []
        if mode == "compare":
            st.markdown("### 🔀 Pick 3 styles to compare")
            keys = list(STYLES.keys())
            c1, c2, c3 = st.columns(3)
            with c1:
                s1 = st.selectbox("Style A", keys, index=0, key="cmp_s1")
            with c2:
                s2 = st.selectbox("Style B", keys, index=min(1, len(keys) - 1), key="cmp_s2")
            with c3:
                s3 = st.selectbox("Style C", keys, index=min(2, len(keys) - 1), key="cmp_s3")
            compare_style_keys = [s1, s2, s3]
            if len(set(compare_style_keys)) < 3:
                st.warning("Choose three different styles for a meaningful comparison.")

        phi_ok = st.checkbox(
            "I confirm this content does NOT contain protected health information (PHI).",
            value=False,
            key="phi_confirm",
        )

    with col_doc:
        st.markdown("### 📄 Documents (PDF, DOCX, TXT)")
        st.caption(f"Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB per file. Uploaded content is summarized in PHI guidance.")
        uploaded_files = st.file_uploader(
            "Upload files",
            type=["pdf", "docx", "txt"],
            accept_multiple_files=True,
            key="docs_uploader",
        )

    file_issues: list[str] = []
    files_list = list(uploaded_files) if uploaded_files else []
    if files_list:
        st.warning("⚠️ Documents are attached. Ensure no PHI before generating.")
        for f in files_list:
            ext = Path(f.name).suffix.lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                file_issues.append(f"`{f.name}`: unsupported type")
                continue
            nbytes = getattr(f, "size", None)
            if nbytes is None:
                nbytes = len(f.getbuffer())
            if nbytes > MAX_UPLOAD_BYTES:
                file_issues.append(f"`{f.name}` exceeds 10MB")

    sanitized_context, inj_flags_context = sanitize_input(user_context)

    extracted_preview: list[tuple[str, str]] = []
    for f in files_list:
        _sz = getattr(f, "size", None) or len(f.getbuffer())
        if Path(f.name).suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS and _sz <= MAX_UPLOAD_BYTES:
            extracted_preview.append((f.name, extract_document_text(f)))

    combined_docs = "\n".join(t for _, t in extracted_preview)
    _, inj_flags_docs = sanitize_input(combined_docs)
    inj_rule_ids = sorted(set(inj_flags_context + inj_flags_docs))

    if inj_rule_ids:
        shown = ", ".join(injection_labels_for_ids(inj_rule_ids))
        st.error(
            "Possible prompt-injection patterns were detected — revise your text before generating. "
            f"Flags: {shown}"
        )

    if file_issues:
        for issue in file_issues:
            st.error(issue)

    with st.expander("📖 Extracted document preview", expanded=False):
        if extracted_preview:
            for name, text in extracted_preview:
                preview = sanitize_input(text)[0][:1500]
                st.markdown(f"**{name}** ({len(text)} chars)")
                st.text(preview + ("..." if len(text) > 1500 else ""))
        else:
            st.caption("Upload PDF, DOCX, or TXT to preview extracted text.")

    # ── API helpers (used by chart extraction + generation) ──
    def get_credentials() -> tuple[bool, str, Any, str, str]:
        if provider == "openai":
            api_key = st.session_state.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
            chat_model = (
                st.session_state.get("openai_chat_model", "") or OPENAI_DEFAULT_CHAT_MODEL
            )
            img_model = (
                st.session_state.get("openai_image_model", "").strip()
                or os.environ.get("OPENAI_IMAGE_MODEL", "")
                or OPENAI_DEFAULT_IMAGE_MODEL
            )
            if not api_key:
                return False, "OpenAI API key is required.", None, "", chat_model
            client = make_client("openai", api_key, None, None)
            return True, "", client, img_model, chat_model
        if provider == "azure":
            api_key = st.session_state.get("azure_api_key", "") or os.environ.get(
                "AZURE_OPENAI_API_KEY", ""
            )
            endpoint = st.session_state.get("azure_endpoint", "") or os.environ.get(
                "AZURE_OPENAI_ENDPOINT", ""
            )
            api_version = st.session_state.get("azure_api_version", "") or os.environ.get(
                "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
            )
            img_model = st.session_state.get("azure_image_model", "") or os.environ.get(
                "AZURE_OPENAI_IMAGE_MODEL", "gpt-image-2"
            )
            chat_model = st.session_state.get("azure_chat_model", "") or os.environ.get(
                "AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini"
            )
            if not api_key or not endpoint:
                return False, "Azure API key and endpoint are required.", None, "", chat_model
            client = make_client("azure", api_key, endpoint, api_version)
            return True, "", client, img_model, chat_model
        api_key = st.session_state.get("gemini_api_key", "") or os.environ.get("GEMINI_API_KEY", "")
        img_model = st.session_state.get("gemini_image_model", "").strip() or os.environ.get(
            "GEMINI_IMAGE_MODEL", GEMINI_DEFAULT_IMAGE_MODEL
        )
        chat_model = st.session_state.get("gemini_chat_model", "").strip() or os.environ.get(
            "GEMINI_CHAT_MODEL", GEMINI_DEFAULT_CHAT_MODEL
        )
        if not api_key:
            return False, "Gemini API key is required.", None, "", chat_model
        client = make_client("gemini", api_key, None, None)
        return True, "", client, img_model, chat_model

    def get_vision_model_name() -> str:
        if provider == "openai":
            return (
                st.session_state.get("openai_vision_model", "").strip()
                or os.environ.get("OPENAI_VISION_MODEL", OPENAI_DEFAULT_VISION_MODEL)
            )
        if provider == "azure":
            return (
                st.session_state.get("azure_vision_deployment", "").strip()
                or os.environ.get("AZURE_OPENAI_VISION_DEPLOYMENT", OPENAI_DEFAULT_VISION_MODEL)
            )
        return (
            st.session_state.get("gemini_vision_model", "").strip()
            or os.environ.get("GEMINI_VISION_MODEL", GEMINI_DEFAULT_VISION_MODEL)
        )

    def run_cleanups(client: Any, chat_model: str) -> tuple[list[str], list[str]]:
        cleaned: list[str] = []
        notes: list[str] = []
        cache: dict[str, str] = st.session_state.cleaned_docs_cache
        for name, raw in extracted_preview:
            c, _ = sanitize_input(raw)
            if not c.strip():
                continue
            ck = cache_key_for_raw(name, c)
            if ck in cache:
                cleaned.append(cache[ck])
                continue
            set_progress(progress_bar, status_label, "Cleaning document text", 0.35)
            result = clean_document_text_llm(client, provider, chat_model, c)
            cleaned.append(result.text)
            cache[ck] = result.text
            if result.truncated_input:
                notes.append(
                    f"`{name}`: large document — sanitized in {result.chunks_processed} chunk(s); "
                    "review the preview for completeness."
                )
        return cleaned, notes

    # ── Publication chart / data reference (optional, no pre-verify step) ──
    st.markdown("### 📊 Publication chart reference (optional)")
    st.caption(
        "Upload figures or tables from manuscripts, or raw chart data (CSV/XLSX/JSON). "
        "This is folded into the prompt so generated infographics align with publication values. "
        "Use **Review charts in output** below after generation to validate what was rendered."
    )
    chart_figures = st.file_uploader(
        "Upload existing chart or figure",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="chart_figure_uploader",
    )
    chart_data_files = st.file_uploader(
        "Upload raw data (CSV, XLSX, JSON)",
        type=["csv", "xlsx", "json"],
        accept_multiple_files=True,
        key="chart_data_uploader",
    )
    has_chart_inputs = bool(st.session_state.charts or chart_figures or chart_data_files)
    step_context_ready = bool(sanitized_context.strip() or extracted_preview)
    step_refs_ready = has_chart_inputs
    step_generate_ready = bool(phi_ok and not inj_rule_ids and not file_issues)
    st.markdown("### Quick start")
    q1, q2, q3 = st.columns(3)
    with q1:
        st.markdown(
            (
                "✅ **Step 1: Add topic context**\n\n"
                "Describe the topic and/or upload source docs."
            )
            if step_context_ready
            else "⬜ **Step 1: Add topic context**\n\nAdd context text or upload at least one source document."
        )
    with q2:
        st.markdown(
            (
                "✅ **Step 2: Add chart references (optional)**\n\n"
                "You have at least one uploaded or manual reference entry."
            )
            if step_refs_ready
            else "⬜ **Step 2: Add chart references (optional)**\n\nSkip if not needed, or add a chart/file/manual row."
        )
    with q3:
        st.markdown(
            (
                "✅ **Step 3: Ready to generate**\n\n"
                "Safety checks are currently passing."
            )
            if step_generate_ready
            else "⬜ **Step 3: Ready to generate**\n\nConfirm PHI checkbox and clear any warnings first."
        )
    chart_context_snippet = st.text_area(
        "Optional: paste extra source text to help extraction",
        height=80,
        key="chart_context_snippet",
        help="Shown to the vision model when extracting numbers from figure uploads.",
    )
    snippet_txt = chart_context_snippet.strip()
    cross_text = (snippet_txt + "\n" + combined_docs).strip()

    st.checkbox(
        "Cross-check chart numbers vs uploaded document text (may flag rounding differences or missing table values)",
        value=False,
        key="chart_cross_check_documents",
    )
    _cc = st.session_state.get("chart_cross_check_documents", False)

    fig_sigs: set[tuple[str, int]] = set(st.session_state.processed_chart_figure_sigs)
    if chart_figures:
        for f in chart_figures:
            raw = f.getvalue()
            nbytes = len(raw)
            if nbytes > MAX_CHART_UPLOAD_BYTES:
                st.error(f"`{f.name}` exceeds max upload size.")
                continue
            sig = (f.name, nbytes)
            if sig in fig_sigs:
                continue
            fig_sigs.add(sig)
            ext = Path(f.name).suffix.lower()
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }
            st.session_state.charts.append(
                {
                    "chart_id": new_chart_id(),
                    "chart_type": "unknown",
                    "title": Path(f.name).stem,
                    "axis_labels": {},
                    "axis_units": {},
                    "category_labels": [],
                    "legend_labels": [],
                    "data_series": [],
                    "footnotes": [],
                    "source_citation": "",
                    "verification_status": "active",
                    "confidence_level": {},
                    "data_source_types": ["chart_image"],
                    "visual_reference": True,
                    "chart_mode": "exact",
                    "conflicts": [],
                    "placeholder_text": "",
                    "extraction_warnings": [],
                    "source_file": f.name,
                    "source_location": "",
                    "low_confidence_fields": [],
                    "_mime": mime_map.get(ext, "image/png"),
                    "_bytes_b64": base64.b64encode(raw).decode("ascii"),
                }
            )
    st.session_state.processed_chart_figure_sigs = fig_sigs

    data_sigs: set[tuple[str, int]] = set(st.session_state.processed_data_file_sigs)
    if chart_data_files:
        for f in chart_data_files:
            raw = f.getvalue()
            nbytes = len(raw)
            if nbytes > MAX_CHART_UPLOAD_BYTES:
                st.error(f"`{f.name}` exceeds max upload size.")
                continue
            sig = (f.name, nbytes)
            if sig in data_sigs:
                continue
            data_sigs.add(sig)
            ext = Path(f.name).suffix.lower()
            try:
                if ext == ".csv":
                    series = parse_csv_to_data_series(raw)
                elif ext == ".json":
                    series = parse_json_data_file(raw)
                elif ext == ".xlsx":
                    series = parse_xlsx_to_data_series(raw)
                    if not series:
                        st.warning("XLSX parse failed — install openpyxl or use CSV/JSON.")
                else:
                    series = []
            except Exception as ex:
                st.error(f"Could not parse `{f.name}`: {ex}")
                series = []
            dtype = ext.strip(".") or "file"
            st.session_state.charts.append(
                {
                    "chart_id": new_chart_id(),
                    "chart_type": "bar",
                    "title": Path(f.name).stem,
                    "axis_labels": {},
                    "axis_units": {},
                    "category_labels": [],
                    "legend_labels": [],
                    "data_series": series,
                    "footnotes": [],
                    "source_citation": "",
                    "verification_status": "active",
                    "confidence_level": {},
                    "data_source_types": [dtype],
                    "visual_reference": False,
                    "chart_mode": "exact",
                    "conflicts": [],
                    "placeholder_text": "",
                    "extraction_warnings": [],
                    "source_file": f.name,
                    "source_location": "",
                    "low_confidence_fields": [],
                }
            )
            c_last = st.session_state.charts[-1]
            refresh_chart_reference_hints(c_last, cross_text, _cc)
    st.session_state.processed_data_file_sigs = data_sigs

    st.markdown("**Optional: add data without uploading a file**")
    st.caption(
        "Use these when your numbers live elsewhere (another document or tab) or you want to reserve "
        "space before you paste values. Uploaded figures and data files stay above; these buttons add "
        "extra reference rows below."
    )
    c1p, c2p, c3p = st.columns(3)
    with c1p:
        st.caption(
            "**Manual chart row** — Adds an empty table you fill in: groups, values, units, and notes. "
            "Best when you are typing or pasting real numbers (for example from a paper or slide you have open)."
        )
        if st.button(
            "➕ Add manual chart row (empty)",
            key="btn_add_manual_chart",
            help="Creates a new reference with a data grid in the expander below. No file upload required.",
        ):
            st.session_state.charts.append(
                {
                    "chart_id": new_chart_id(),
                    "chart_type": "manual",
                    "title": "Manual entry",
                    "axis_labels": {},
                    "axis_units": {},
                    "category_labels": [],
                    "legend_labels": [],
                    "data_series": [
                        {"group": "", "value": "", "label": "", "unit": ""},
                    ],
                    "footnotes": [],
                    "source_citation": "",
                    "verification_status": "active",
                    "confidence_level": {},
                    "data_source_types": ["manual"],
                    "visual_reference": False,
                    "chart_mode": "exact",
                    "conflicts": [],
                    "placeholder_text": "",
                    "extraction_warnings": [],
                    "source_file": "",
                    "source_location": "",
                    "low_confidence_fields": [],
                }
            )
            refresh_chart_reference_hints(st.session_state.charts[-1], cross_text, _cc)
    with c2p:
        st.caption(
            "**Placeholder chart** — No numeric grid. Adds a labeled box whose text is sent to the model so "
            "the infographic can show “values to follow” or similar without inventing numbers."
        )
        if st.button(
            "➕ Add placeholder chart",
            key="btn_add_placeholder_chart",
            help="Adds a placeholder entry; edit the wording in its expander. Use when values are not finalized.",
        ):
            st.session_state.charts.append(
                {
                    "chart_id": new_chart_id(),
                    "chart_type": "placeholder",
                    "title": "Approved placeholder",
                    "axis_labels": {},
                    "axis_units": {},
                    "category_labels": [],
                    "legend_labels": [],
                    "data_series": [],
                    "footnotes": [],
                    "source_citation": "",
                    "verification_status": "active",
                    "confidence_level": {},
                    "data_source_types": ["manual"],
                    "visual_reference": False,
                    "chart_mode": "exact",
                    "conflicts": [],
                    "placeholder_text": "Exact values to be inserted from [source figure/table]",
                    "extraction_warnings": [],
                    "source_file": "",
                    "source_location": "",
                    "low_confidence_fields": [],
                }
            )
    with c3p:
        st.caption(
            "**Clear all charts** — Removes every reference in this list (uploads, manual rows, and placeholders) "
            "for this session."
        )
        if st.session_state.charts and st.button(
            "🗑️ Clear all charts",
            key="btn_clear_charts",
            help="Deletes all chart references. Re-upload or re-add anything you still need.",
        ):
            st.session_state.charts = []
            st.session_state.processed_chart_figure_sigs = set()
            st.session_state.processed_data_file_sigs = set()
            st.rerun()

    st.markdown("#### Reference entries")

    _flash_n = st.session_state.pop("_extract_all_ok", None)
    if _flash_n:
        st.success(f"Vision extraction completed for {_flash_n} figure(s).")

    pending_fig = [
        c
        for c in st.session_state.charts
        if chart_figure_pending_extraction(c)
    ]
    if pending_fig:
        st.warning(
            f"**{len(pending_fig)} uploaded figure(s) need vision extraction** "
            "before numeric values are included in the prompt (expanders below are opened for those)."
        )
        ec1, ec2 = st.columns([2, 1])
        with ec1:
            if st.button(
                "🔍 Run vision extraction on all pending figures",
                type="primary",
                use_container_width=True,
                key="btn_extract_all_pending_figures",
            ):
                ok_e, err_e, client_e, _im, _cm = get_credentials()
                if not ok_e or client_e is None:
                    st.error(err_e or "Configure API keys.")
                else:
                    errs: list[str] = []
                    vm = get_vision_model_name()
                    with st.spinner(f"Running vision on {len(pending_fig)} figure(s)…"):
                        for c in pending_fig:
                            try:
                                run_chart_figure_vision_extract(
                                    c,
                                    client_e,
                                    cross_text,
                                    snippet_txt,
                                    vm,
                                    _cc,
                                )
                                st.session_state.chart_extraction_nonce += 1
                            except Exception as ex:
                                ttl = str(c.get("title") or c.get("chart_id") or "untitled")
                                errs.append(f"{ttl}: {user_friendly_error(ex)}")
                    if errs:
                        st.error("Some extractions failed:\n\n" + "\n".join(errs))
                    else:
                        st.session_state["_extract_all_ok"] = len(pending_fig)
                    st.rerun()
        with ec2:
            st.caption("One vision API call per figure.")

    for idx, chart in enumerate(list(st.session_state.charts)):
        cid = chart.get("chart_id", f"idx_{idx}")
        removed = chart.get("verification_status") == "removed"
        badge = "removed from prompt" if removed else "included in prompt"
        pending_px = chart_figure_pending_extraction(chart)
        ex_tag = " — needs vision extraction" if (pending_px and not removed) else ""
        with st.expander(
            f"Reference {idx + 1}: {chart.get('title') or cid} — {badge}{ex_tag}",
            expanded=(pending_px and not removed),
        ):
            if removed:
                st.caption("Excluded from the generation prompt.")
                if st.button("Include in prompt again", key=f"restore_{cid}"):
                    chart["verification_status"] = "active"
                    st.rerun()
            else:
                t1, t2 = st.columns(2)
                with t1:
                    chart["title"] = st.text_input(
                        "Title", value=chart.get("title", ""), key=f"tit_{cid}"
                    )
                    chart["chart_type"] = st.text_input(
                        "Chart type", value=chart.get("chart_type", ""), key=f"ct_{cid}"
                    )
                with t2:
                    chart["chart_mode"] = st.selectbox(
                        "How strictly should this chart be followed?",
                        options=list(CHART_MODES),
                        format_func=lambda m: {
                            "exact": "Exact numbers (recommended)",
                            "style_transform": "Restyle visual only (keep all data exact)",
                            "reference_only": "Reference only (layout guidance, non-exact)",
                        }[m],
                        index=list(CHART_MODES).index(chart.get("chart_mode", "exact"))
                        if chart.get("chart_mode") in CHART_MODES
                        else 0,
                        key=f"mode_{cid}",
                        help=(
                            "Choose Exact for publication values. Use Restyle visual only when you want a "
                            "different look but identical numbers. Use Reference only if exact data matching "
                            "is not required for this entry."
                        ),
                    )
                chart["source_citation"] = st.text_input(
                    "Source citation",
                    value=chart.get("source_citation", ""),
                    key=f"src_{cid}",
                    placeholder="e.g. Journal Name. 2026;14(2):123-130. doi:10.xxxx/xxxx",
                )
                chart["source_location"] = st.text_input(
                    "Source location (optional)",
                    value=chart.get("source_location", ""),
                    key=f"loc_{cid}",
                    placeholder="e.g. Figure 2B, Table 1, Supplementary eFigure 3",
                )

                ds_default = chart.get("data_series") or [
                    {"group": "", "value": "", "label": "", "unit": ""}
                ]
                edited = st.data_editor(
                    pd.DataFrame(ds_default),
                    num_rows="dynamic",
                    key=f"de_{cid}",
                    use_container_width=True,
                )
                if edited is not None and not edited.empty:
                    chart["data_series"] = edited.to_dict(orient="records")

                foot = "\n".join(chart.get("footnotes") or [])
                nfoot = st.text_area(
                    "Footnotes (one per line)", value=foot, height=60, key=f"ft_{cid}"
                )
                chart["footnotes"] = [ln for ln in nfoot.splitlines() if ln.strip()]

                if chart.get("_bytes_b64"):
                    st.caption(
                        "Publication figure — run vision extraction to pull numbers into the table, "
                        "or use the primary “Run vision extraction on all pending figures” button above."
                    )
                    if st.button("Run vision extraction (this figure only)", key=f"ex_{cid}"):
                        ok_e, err_e, client_e, _im, _cm = get_credentials()
                        if not ok_e or client_e is None:
                            st.error(err_e or "API not configured.")
                        else:
                            try:
                                run_chart_figure_vision_extract(
                                    chart,
                                    client_e,
                                    cross_text,
                                    snippet_txt,
                                    get_vision_model_name(),
                                    _cc,
                                )
                                st.session_state.chart_extraction_nonce += 1
                                st.success("Extraction complete — values are sent with the next generation.")
                                st.rerun()
                            except Exception as ex:
                                st.error(user_friendly_error(ex))

                warns = chart.get("extraction_warnings") or []
                lowc = chart.get("low_confidence_fields") or []
                if warns or lowc:
                    st.warning(
                        "Extraction notes: "
                        + "; ".join(warns)
                        + (" | Fields: " + ", ".join(lowc) if lowc else "")
                    )

                conf = chart.get("conflicts") or []
                if conf:
                    st.info(
                        "Optional cross-check vs document text (edit the table if you disagree with a flag):"
                    )
                    for co in conf:
                        st.markdown(
                            f"- **{co.get('field')}** — document: `{co.get('document_value')}` vs "
                            f"reference: `{co.get('chart_image_value')}`"
                        )

                chart["placeholder_text"] = st.text_input(
                    "Placeholder label (for TBD values)", value=chart.get("placeholder_text", ""), key=f"pht_{cid}"
                )
                a1, a2 = st.columns(2)
                with a1:
                    if st.button("Remove from prompt", key=f"rm_{cid}", type="secondary"):
                        chart["verification_status"] = "removed"
                        st.rerun()
                with a2:
                    if st.button("Refresh cross-check hints", key=f"rc_{cid}", type="secondary"):
                        refresh_chart_reference_hints(chart, cross_text, _cc)
                        st.rerun()

    chart_reference_block = format_chart_reference_for_prompt(st.session_state.charts)
    fidelity_preflight = publication_reference_preflight_issues(st.session_state.charts)
    if publication_fidelity_mode and fidelity_preflight:
        st.error("Publication fidelity preflight checks failed:")
        for item in fidelity_preflight:
            st.markdown(f"- {item}")

    st.markdown("---")
    tentative_prompt = ""

    logo_extra = ""
    if resolve_logo_path():
        logo_extra = (
            "\n- Technical note for layout: Leave the bottom edge clear or use a plain white band; "
            "the application may composite the approved logo file onto a white footer after generation.\n"
        )

    tentative_prompt = build_infographic_prompt(
        selected_style_key if mode == "single" else list(STYLES.keys())[0],
        sanitized_context,
        [regex_cleanup_fallback(combined_docs)] if combined_docs else [],
        audience,
        st.session_state.get("refinement_notes", ""),
        logo_extra,
        chart_reference_block=chart_reference_block,
    )

    with st.expander("View full prompt (audited locally only — never logged server-side)", expanded=False):
        st.caption(
            "This preview uses **regex cleanup** on uploaded documents. On **Generate**, the app runs "
            "**LLM document cleanup first**, then builds the prompt — so live prompts can differ slightly "
            "from this preview when files are attached."
        )
        st.code(tentative_prompt[:12000] + ("\n...[truncated]..." if len(tentative_prompt) > 12000 else ""))

    credential_issue = ""
    if provider == "openai":
        key_val = st.session_state.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
        if not key_val:
            credential_issue = "OpenAI API key is missing."
    elif provider == "azure":
        key_val = st.session_state.get("azure_api_key", "") or os.environ.get("AZURE_OPENAI_API_KEY", "")
        endpoint_val = st.session_state.get("azure_endpoint", "") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if not key_val or not endpoint_val:
            credential_issue = "Azure API key and endpoint are required."
    else:
        key_val = st.session_state.get("gemini_api_key", "") or os.environ.get("GEMINI_API_KEY", "")
        if not key_val:
            credential_issue = "Gemini API key is missing."

    readiness_issues: list[str] = []
    if not step_context_ready:
        readiness_issues.append("Add topic context text or upload at least one source document.")
    if not phi_ok:
        readiness_issues.append("Confirm the PHI checkbox.")
    if credential_issue:
        readiness_issues.append(credential_issue)
    if inj_rule_ids:
        readiness_issues.append("Resolve possible prompt-injection flags in context/documents.")
    if file_issues:
        readiness_issues.append("Fix upload issues (unsupported type or oversized file).")
    if mode == "compare" and len(set(compare_style_keys)) < 3:
        readiness_issues.append("Pick three different styles for comparison mode.")
    if publication_fidelity_mode and fidelity_preflight:
        readiness_issues.append("Resolve publication fidelity preflight issues in chart references.")

    st.markdown("### Readiness")
    if readiness_issues:
        st.warning("Generation is currently blocked:")
        for issue in readiness_issues:
            st.markdown(f"- {issue}")
    else:
        st.success("All checks passed. Ready to generate.")

    gen_disabled = (
        not phi_ok
        or bool(credential_issue)
        or bool(inj_rule_ids)
        or bool(file_issues)
        or (mode == "compare" and len(set(compare_style_keys)) < 3)
        or (publication_fidelity_mode and bool(fidelity_preflight))
    )

    if mode == "single":
        generate_btn = st.button(
            "🎨 Generate infographic",
            type="primary",
            use_container_width=True,
            disabled=gen_disabled,
        )
    else:
        generate_btn = st.button(
            "🎨 Generate 3-way comparison",
            type="primary",
            use_container_width=True,
            disabled=gen_disabled,
        )

    progress_bar = st.progress(0.0)
    status_label = st.empty()
    error_box = st.empty()

    if generate_btn:
        ok, err_msg, client, image_model, chat_model = get_credentials()
        if not ok:
            error_box.error(err_msg)
            return

        assert client is not None

        t0 = time.perf_counter()
        try:
            cleanup_notes: list[str] = []
            if extracted_preview:
                set_progress(progress_bar, status_label, "Cleaning document text", 0.12)
                cleaned_docs, cleanup_notes = run_cleanups(client, chat_model)
            else:
                cleaned_docs = []
            for line in cleanup_notes:
                st.info(line)

            set_progress(progress_bar, status_label, "Building prompt", 0.45)

            styles_to_run = (
                [selected_style_key] if mode == "single" else compare_style_keys
            )
            results: list[dict[str, Any]] = []
            logo_file = resolve_logo_path()

            gen_ref_block = format_chart_reference_for_prompt(st.session_state.charts)
            st.session_state.last_chart_reference_block = gen_ref_block

            refinement = str(st.session_state.get("refinement_notes", "") or "")

            def run_single_style(style_id: str, store_prompt: bool) -> dict[str, Any]:
                prompt = build_infographic_prompt(
                    style_id,
                    sanitized_context,
                    cleaned_docs,
                    audience,
                    refinement,
                    logo_extra,
                    chart_reference_block=gen_ref_block,
                )
                if store_prompt:
                    st.session_state.last_prompt = prompt
                t_req = time.perf_counter()

                def submit_progress(attempt: int) -> None:
                    frac = 0.55 + 0.05 * attempt
                    set_progress(progress_bar, status_label, "Submitting to API", frac)

                try:
                    image_ref = generate_with_retry(
                        client,
                        provider,
                        image_model,
                        prompt,
                        size,
                        quality,
                        progress_callback=submit_progress,
                    )
                    set_progress(progress_bar, status_label, "Fetching image", 0.82)
                    raw_bytes = fetch_image_bytes(image_ref)
                    if logo_file:
                        raw_bytes = composite_logo_footer(raw_bytes, logo_file)
                    set_progress(progress_bar, status_label, "Displaying", 1.0)
                    out = {
                        "style_key": style_id,
                        "bytes": raw_bytes,
                        "prompt_len": len(prompt),
                    }
                    latency = int((time.perf_counter() - t_req) * 1000)
                    audit_log(
                        session_id,
                        provider,
                        style_id,
                        audience,
                        True,
                        latency,
                        "image_generation_compare" if mode == "compare" else "image_generation",
                    )
                    return out
                except BaseException as exc:
                    latency = int((time.perf_counter() - t_req) * 1000)
                    audit_log(
                        session_id,
                        provider,
                        style_id,
                        audience,
                        False,
                        latency,
                        "image_generation",
                        {"error_kind": exc.__class__.__name__},
                    )
                    raise

            if mode == "compare" and len(styles_to_run) == 3:
                set_progress(progress_bar, status_label, "Submitting to API (3 parallel)", 0.55)

                def parallel_worker(sid: str) -> dict[str, Any]:
                    prompt = build_infographic_prompt(
                        sid,
                        sanitized_context,
                        cleaned_docs,
                        audience,
                        refinement,
                        logo_extra,
                        chart_reference_block=gen_ref_block,
                    )
                    t_req = time.perf_counter()
                    try:
                        image_ref = generate_with_retry(
                            client,
                            provider,
                            image_model,
                            prompt,
                            size,
                            quality,
                            progress_callback=None,
                        )
                        raw_bytes = fetch_image_bytes(image_ref)
                        if logo_file:
                            raw_bytes = composite_logo_footer(raw_bytes, logo_file)
                        latency = int((time.perf_counter() - t_req) * 1000)
                        audit_log(
                            session_id,
                            provider,
                            sid,
                            audience,
                            True,
                            latency,
                            "image_generation_compare",
                        )
                        return {
                            "style_key": sid,
                            "bytes": raw_bytes,
                            "prompt_len": len(prompt),
                        }
                    except BaseException as exc:
                        latency = int((time.perf_counter() - t_req) * 1000)
                        audit_log(
                            session_id,
                            provider,
                            sid,
                            audience,
                            False,
                            latency,
                            "image_generation",
                            {"error_kind": exc.__class__.__name__},
                        )
                        raise

                with ThreadPoolExecutor(max_workers=3) as pool:
                    futs = [pool.submit(parallel_worker, sid) for sid in styles_to_run]
                    by_sid: dict[str, dict[str, Any]] = {}
                    for fut in futs:
                        row = fut.result()
                        by_sid[row["style_key"]] = row
                results = [by_sid[sid] for sid in styles_to_run]
                set_progress(progress_bar, status_label, "Displaying", 1.0)
            else:
                for si, style_id in enumerate(styles_to_run):
                    results.append(
                        run_single_style(style_id, store_prompt=(mode == "single" and si == 0))
                    )

            if mode == "single" and results:
                st.session_state.last_image_bytes = results[0]["bytes"]
                st.session_state.last_fidelity_qa_pass = False
                st.session_state.last_fidelity_qa_result = None
                st.session_state.last_post_gen_chart_qa_text = ""
                st.session_state.generation_history.append(
                    {
                        "thumb_bytes": results[0]["bytes"],
                        "style": results[0]["style_key"],
                        "ts": time.time(),
                        "audience": audience,
                    }
                )
            elif mode == "compare":
                st.session_state.comparison_results = results

            progress_bar.progress(1.0)
            st.success("Done.")

        except BaseException as exc:
            error_box.error(user_friendly_error(exc))
            audit_log(
                session_id,
                provider,
                styles_to_run[0] if styles_to_run else "",
                audience,
                False,
                int((time.perf_counter() - t0) * 1000),
                "batch_failed",
                {"error_kind": exc.__class__.__name__},
            )
        finally:
            pass

    # ── Output: single ──
    if st.session_state.last_image_bytes and mode == "single":
        st.markdown("### 🖼️ Latest infographic")
        st.image(st.session_state.last_image_bytes, use_container_width=True)
        dl_disabled = publication_fidelity_mode and not bool(
            st.session_state.get("last_fidelity_qa_pass", False)
        )
        if dl_disabled:
            st.warning(
                "Publication fidelity mode is enabled. Run chart review and get a PASS before download."
            )
        st.download_button(
            "📥 Download PNG",
            data=st.session_state.last_image_bytes,
            file_name="infographic.png",
            mime="image/png",
            use_container_width=True,
            key="dl_single",
            disabled=dl_disabled,
        )
        with st.expander("📋 Review charts in the generated image (vision QA)", expanded=True):
            has_ref = bool(str(st.session_state.get("last_chart_reference_block", "") or "").strip())
            st.caption(
                "The vision model reads your **exported PNG** and returns bullet-point feedback **below** "
                "(saved until you generate again or clear it). "
                + (
                    "With a chart reference in the last prompt, it compares on-screen numbers/labels to that reference."
                    if has_ref
                    else "No chart reference was in the last prompt — it comments only on what it can read in the image."
                )
            )
            allow_qa = st.checkbox(
                "Run vision review (uses API credits)",
                value=False,
                key="post_gen_chart_qa_enable",
            )
            if st.button(
                "Run chart review",
                key="btn_post_gen_chart_qa",
                disabled=(not allow_qa),
            ):
                ok_q, err_q, client_q, _im_q, _cm_q = get_credentials()
                if not ok_q or client_q is None:
                    st.error(err_q or "Configure API keys for QA.")
                else:
                    try:
                        if publication_fidelity_mode:
                            qa_obj = run_publication_fidelity_qa(
                                client_q,
                                get_vision_model_name(),
                                st.session_state.last_image_bytes,
                                str(st.session_state.get("last_chart_reference_block", "") or ""),
                                preferred_terms,
                                expected_citation,
                            )
                            st.session_state.last_fidelity_qa_result = qa_obj
                            st.session_state.last_fidelity_qa_pass = bool(qa_obj.get("pass", False))
                            st.session_state.last_post_gen_chart_qa_text = (
                                format_publication_fidelity_qa_markdown(qa_obj)
                            )
                            if st.session_state.last_fidelity_qa_pass:
                                st.success("Publication fidelity QA: PASS")
                            else:
                                st.error("Publication fidelity QA: FAIL")
                            st.json(qa_obj)
                        else:
                            qa_txt = run_post_generation_chart_qa(
                                client_q,
                                get_vision_model_name(),
                                st.session_state.last_image_bytes,
                                str(st.session_state.get("last_chart_reference_block", "") or ""),
                            )
                            if (qa_txt or "").strip():
                                st.session_state.last_post_gen_chart_qa_text = qa_txt.strip()
                            else:
                                st.session_state.last_post_gen_chart_qa_text = (
                                    "- _(The vision model returned an empty response. "
                                    "Try again, pick another vision model, or check API errors.)_"
                                )
                        audit_log(
                            session_id,
                            provider,
                            selected_style_key,
                            audience,
                            True,
                            0,
                            "post_generation_chart_qa",
                        )
                    except BaseException as ex:
                        st.error(user_friendly_error(ex))
            qa_saved = (st.session_state.get("last_post_gen_chart_qa_text") or "").strip()
            if qa_saved:
                st.markdown("#### Chart review output")
                st.markdown(qa_saved)
                if st.button("Clear chart review output", key="btn_clear_post_gen_qa", type="secondary"):
                    st.session_state.last_post_gen_chart_qa_text = ""
                    st.rerun()
            if publication_fidelity_mode and st.session_state.get("last_fidelity_qa_result") is not None:
                st.caption("Latest publication-fidelity result")
                st.json(st.session_state.last_fidelity_qa_result)
        refinement = st.text_area(
            "Refinement notes (next generation)",
            key="refinement_loop_area",
            height=90,
            placeholder=(
                "e.g. Make title larger, reduce paragraph text by 30%, keep chart values exact, "
                "and emphasize the screening workflow."
            ),
        )
        st.caption("Tip: include what to change, what to keep fixed, and which section it applies to.")
        if st.button(
            "🔁 Save refinement notes and prepare next run",
            key="btn_refine",
            type="secondary",
            help="Applies your notes to the next prompt. Click “Generate infographic” again.",
        ):
            st.session_state.refinement_notes = refinement
            st.rerun()

    # ── Output: comparison ──
    if st.session_state.comparison_results and mode == "compare":
        st.markdown("### 🖼️ Style comparison")
        cols = st.columns(3)
        for i, col in enumerate(cols):
            if i >= len(st.session_state.comparison_results):
                continue
            r = st.session_state.comparison_results[i]
            with col:
                st.caption(STYLES[r["style_key"]]["name"])
                st.image(r["bytes"], use_container_width=True)
                st.download_button(
                    "Download",
                    data=r["bytes"],
                    file_name=f"infographic_compare_{r['style_key']}.png",
                    mime="image/png",
                    use_container_width=True,
                    key=f"dl_cmp_{i}",
                )

    if st.session_state.generation_history:
        with st.expander("📚 Recent generations (this session)", expanded=False):
            for idx, entry in enumerate(reversed(st.session_state.generation_history[-8:])):
                st.caption(f"{entry.get('style', '')} · {entry.get('audience', '')}")
                st.image(entry["thumb_bytes"], use_container_width=True)

    st.markdown("<hr style='margin-top:32px;border-color:#E8F6F5'>", unsafe_allow_html=True)
