"""Streamlit UI for the Infographic Generator."""

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import logging
import pandas as pd
import streamlit as st

# Configure logging to show in Streamlit
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

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
from uab_app.cleanup import infer_source_profile_llm
from uab_app.constants import (
    ALLOWED_UPLOAD_EXTENSIONS,
    CHART_EXTRACTION_CONTEXT_MAX_CHARS,
    CHART_MODES,
    GEMINI_DEFAULT_CHAT_MODEL,
    GEMINI_DEFAULT_IMAGE_MODEL,
    GEMINI_DEFAULT_VISION_MODEL,
    MAX_CLEANED_DOCS_CACHE,
    MAX_CHART_UPLOAD_BYTES,
    MAX_GENERATION_HISTORY,
    MAX_SESSION_CHARTS,
    MAX_UPLOAD_BYTES,
    OPENAI_DEFAULT_CHAT_MODEL,
    OPENAI_DEFAULT_IMAGE_MODEL,
    OPENAI_DEFAULT_VISION_MODEL,
)
from uab_app.image_service import (
    AZURE_IMAGE_PROMPT_MAX_CHARS,
    AZURE_IMAGE_PROMPT_SAFETY_MARGIN,
    build_guided_refinement_notes,
    composite_logo_footer,
    fetch_image_bytes,
    format_refinements_scan_for_notes,
    generate_with_retry,
    make_client,
    optimize_azure_image_prompt,
    resolve_logo_path,
    run_refinements_scan_vision,
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
    extra = snippet_txt[:CHART_EXTRACTION_CONTEXT_MAX_CHARS] if snippet_txt.strip() else cross_text[:CHART_EXTRACTION_CONTEXT_MAX_CHARS]
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
    if "last_effective_prompt" not in st.session_state:
        st.session_state.last_effective_prompt = ""
    if "last_effective_prompt_sha256" not in st.session_state:
        st.session_state.last_effective_prompt_sha256 = ""
    if "last_inferred_profile" not in st.session_state:
        st.session_state.last_inferred_profile = {}
    if "refine_generate_now" not in st.session_state:
        st.session_state.refine_generate_now = False
    if "last_guided_refine_plan" not in st.session_state:
        st.session_state.last_guided_refine_plan = ""
    if "last_refinements_scan" not in st.session_state:
        st.session_state.last_refinements_scan = None
    if "last_refinements_scan_context" not in st.session_state:
        st.session_state.last_refinements_scan_context = {}
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

    # Enforce session state size limits to prevent memory bloat
    if len(st.session_state.generation_history) > MAX_GENERATION_HISTORY:
        st.session_state.generation_history = st.session_state.generation_history[-MAX_GENERATION_HISTORY:]
    if len(st.session_state.charts) > MAX_SESSION_CHARTS:
        st.session_state.charts = st.session_state.charts[-MAX_SESSION_CHARTS:]
    if len(st.session_state.cleaned_docs_cache) > MAX_CLEANED_DOCS_CACHE:
        # Remove oldest entries
        cache_items = list(st.session_state.cleaned_docs_cache.items())
        st.session_state.cleaned_docs_cache = dict(cache_items[-MAX_CLEANED_DOCS_CACHE:])


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


def render_generation_placeholder(slot: Any, title: str = "Generating image...") -> None:
    """Render an animated placeholder where the final image will appear."""
    slot.markdown(
        """
        <style>
        @keyframes uab-shimmer {
            0% { background-position: -200% 0; }
            100% { background-position: 200% 0; }
        }
        .uab-gen-wrap {
            border: 1px solid #cfe3dc;
            border-radius: 14px;
            padding: 16px;
            background: #f7fbf9;
            margin-top: 8px;
            margin-bottom: 8px;
        }
        .uab-gen-title {
            font-weight: 700;
            color: #1A5632;
            margin-bottom: 10px;
            font-size: 1rem;
        }
        .uab-gen-skeleton {
            width: 100%;
            height: 360px;
            border-radius: 10px;
            border: 1px solid #d9e8e2;
            background: linear-gradient(90deg, #edf4f1 20%, #dbe9e3 40%, #edf4f1 60%);
            background-size: 200% 100%;
            animation: uab-shimmer 2.2s linear infinite;
        }
        .uab-gen-note {
            margin-top: 10px;
            color: #38655b;
            font-size: 0.9rem;
        }
        </style>
        """
        f"""
        <div class="uab-gen-wrap">
            <div class="uab-gen-title">{title}</div>
            <div class="uab-gen-skeleton"></div>
            <div class="uab-gen-note">Building and rendering high-detail infographic…</div>
        </div>
        """,
        unsafe_allow_html=True,
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
    st.warning(
        "For ideation only — not for production use. Generated infographics may contain hallucinations, "
        "inaccurate numbers, or misleading wording. Always perform human review and final editing, and "
        "use a qualified graphic designer/content owner before publication."
    )

    def _autodetect_provider() -> str:
        if (
            os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
            and os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        ):
            return "azure"
        if os.environ.get("OPENAI_API_KEY", "").strip():
            return "openai"
        if os.environ.get("GEMINI_API_KEY", "").strip():
            return "gemini"
        return "azure"

    with st.sidebar:
        experience_mode = st.radio(
            "Experience",
            options=["basic", "advanced"],
            format_func=lambda m: "Basic (recommended)" if m == "basic" else "Advanced",
            index=0,
            key="ux_experience_mode",
            help="Basic keeps the workflow simple. Advanced exposes full controls.",
        )
        st.markdown("---")
        if experience_mode == "basic":
            provider = st.session_state.get("sidebar_provider", _autodetect_provider())
            st.caption(
                "Basic mode uses saved/environment API settings automatically. "
                "Switch to Advanced to change provider and model deployments."
            )
        else:
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

        if experience_mode == "advanced" and provider == "openai":
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
        elif experience_mode == "advanced" and provider == "azure":
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
                    value="2024-02-01",
                    key="azure_api_version",
                    help=(
                        "Locked for all Azure calls in this app (chat, vision, and image generation)."
                    ),
                    disabled=True,
                )
                st.text_input(
                    "Image deployment",
                    value=os.environ.get("AZURE_OPENAI_IMAGE_MODEL", "gpt-image-2"),
                    key="azure_image_model",
                    help=(
                        "Azure deployment name for image generation. The app calls "
                        "/openai/deployments/{deployment}/images/generations."
                    ),
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
        elif experience_mode == "advanced":
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
        if experience_mode == "basic":
            mode = "single"
            st.caption("One-shot workflow: upload sources and generate.")
        else:
            mode = st.radio(
                "Mode",
                options=["single", "compare"],
                format_func=lambda m: "Single style" if m == "single" else "Style comparison (3-way)",
                key="gen_mode",
            )

        st.markdown("### 📋 Style (single mode)")
        if experience_mode == "basic":
            selected_style_key = "uab-craft-handmade"
            st.caption("Using default style: UAB Craft Handmade")
        else:
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
            compare_style_keys: list[str] = []
        else:
            st.markdown("### 🔀 Styles to compare")
            all_style_keys = list(STYLES.keys())
            default_compare = all_style_keys[:3] if len(all_style_keys) >= 3 else all_style_keys
            compare_style_keys = st.multiselect(
                "Pick exactly 3 styles",
                options=all_style_keys,
                default=default_compare,
                format_func=lambda k: STYLES[k]["name"],
                key="compare_style_keys_multiselect",
                max_selections=3,
            )
            if len(compare_style_keys) != 3:
                st.warning("Pick exactly 3 styles for comparison mode.")

        st.markdown("---")
        logo_path = resolve_logo_path()
        if logo_path:
            st.success(f"Logo file found: `{logo_path.name}`")
        else:
            st.info(
                "Place `uab-medicine-logo.jpg` in `assets/` or set `UAB_MEDICINE_LOGO_PATH`. "
                "Post-processing will composite the approved logo when the file is available."
            )

    step1_open = True
    step2_open = bool(st.session_state.get("docs_uploader"))
    step3_open = False

    with st.expander("Step 1: Add sources and topic", expanded=step1_open):
        col_main, col_doc = st.columns([1, 1])

        with col_main:
            if experience_mode == "basic":
                audience = "academic"
                st.markdown("### 🚀 One-shot generation")
                st.caption("Upload sources, add an optional goal, and generate.")
                user_context = st.text_area(
                    "Optional: one-sentence goal",
                    height=90,
                    key="user_context_area",
                    placeholder="e.g. Create a clean summary infographic highlighting the main findings.",
                )
            else:
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

            size = "1792x1024"
            quality = "high"
            publication_fidelity_mode = False
            expected_citation = ""
            preferred_terms: list[str] = []
            if experience_mode == "advanced":
                with st.expander("Advanced generation settings", expanded=True):
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

            if experience_mode == "advanced":
                phi_ok = st.checkbox(
                    "I confirm this content does NOT contain protected health information (PHI).",
                    value=False,
                    key="phi_confirm",
                )

        with col_doc:
            st.markdown("### 📄 Documents (PDF, DOCX, TXT)")
            st.caption(
                f"Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB per file. These documents provide context and knowledge "
                "for the infographic — the content will be extracted, cleaned, and included as source material in the generation prompt."
            )
            uploaded_files = st.file_uploader(
                "Upload files",
                type=["pdf", "docx", "txt"],
                accept_multiple_files=True,
                key="docs_uploader",
                help="Upload source documents (papers, articles, reports) to provide context and facts for the infographic. Text will be extracted and used as knowledge in the prompt.",
            )

    file_issues: list[str] = []
    files_list = list(uploaded_files) if uploaded_files else []
    if files_list:
        if experience_mode == "advanced":
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

    sanitized_context, inj_flags_context = sanitize_input(user_context, source="user")

    extracted_preview: list[tuple[str, str]] = []
    for f in files_list:
        _sz = getattr(f, "size", None) or len(f.getbuffer())
        if Path(f.name).suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS and _sz <= MAX_UPLOAD_BYTES:
            extracted_preview.append((f.name, extract_document_text(f)))

    combined_docs = "\n".join(t for _, t in extracted_preview)
    _, inj_flags_docs = sanitize_input(combined_docs, source="document")
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
                preview = sanitize_input(text, source="document")[0][:1500]
                st.markdown(f"**{name}** ({len(text)} chars)")
                st.text(preview + ("..." if len(text) > 1500 else ""))
        else:
            st.markdown(
                "<span style='color: #666'>No documents uploaded yet. Upload PDF, DOCX, or TXT files above to see extracted text here.</span>",
                unsafe_allow_html=True,
            )

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
            api_version = "2024-02-01"
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
        cleanup_targets = []
        for name, raw in extracted_preview:
            c, _ = sanitize_input(raw, source="document")
            if not c.strip():
                continue
            cleanup_targets.append((name, c))

        total = len(cleanup_targets)
        if total == 0:
            cleaning_panel_slot.empty()
            return cleaned, notes

        cleaning_started = time.perf_counter()
        cleaned_count = 0
        detail_lines: list[str] = []

        def run_cleanup_with_live_timer(
            name: str,
            text_in: str,
            idx: int,
            total_docs: int,
        ) -> Any:
            result_holder: dict[str, Any] = {"value": None, "error": None}

            def _worker() -> None:
                try:
                    result_holder["value"] = clean_document_text_llm(
                        client, provider, chat_model, text_in
                    )
                except BaseException as ex:
                    result_holder["error"] = ex

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()
            while worker.is_alive():
                elapsed_total_local = int(time.perf_counter() - cleaning_started)
                mm_local = elapsed_total_local // 60
                ss_local = elapsed_total_local % 60
                set_progress(
                    progress_bar,
                    status_label,
                    f"Cleaning document text ({idx}/{total_docs})",
                    0.12 + (0.23 * ((idx - 1) / max(total_docs, 1))),
                )
                cleaning_panel_slot.info(
                    f"Cleaning documents: {idx}/{total_docs} in progress ({mm_local:02d}:{ss_local:02d} elapsed)  \n"
                    f"Current file: `{name}`"
                )
                time.sleep(1)

            worker.join()
            if result_holder["error"] is not None:
                raise result_holder["error"]
            return result_holder["value"]

        for idx, (name, c) in enumerate(cleanup_targets, start=1):
            elapsed_total = int(time.perf_counter() - cleaning_started)
            mm = elapsed_total // 60
            ss = elapsed_total % 60
            set_progress(
                progress_bar,
                status_label,
                f"Cleaning document text ({idx}/{total})",
                0.12 + (0.23 * ((idx - 1) / max(total, 1))),
            )
            cleaning_panel_slot.info(
                f"Cleaning documents: {idx}/{total} in progress ({mm:02d}:{ss:02d} elapsed)  \n"
                f"Current file: `{name}`"
            )
            ck = cache_key_for_raw(name, c)
            if ck in cache:
                cleaned.append(cache[ck])
                cleaned_count += 1
                detail_lines.append(f"✅ `{name}` cleaned from cache")
                cleaning_panel_slot.info(
                    f"Cleaning documents: {cleaned_count}/{total} complete ({mm:02d}:{ss:02d} elapsed)"
                )
                continue
            doc_started = time.perf_counter()
            result = run_cleanup_with_live_timer(name, c, idx, total)
            cleaned.append(result.text)
            cache[ck] = result.text
            cleaned_count += 1
            doc_elapsed = int(time.perf_counter() - doc_started)
            if result.truncated_input:
                detail_lines.append(
                    f"⚠️ `{name}` cleaned in {result.chunks_processed} chunk(s) ({doc_elapsed}s)"
                )
            else:
                detail_lines.append(f"✅ `{name}` cleaned ({doc_elapsed}s)")
            if result.truncated_input:
                notes.append(
                    f"`{name}`: large document — sanitized in {result.chunks_processed} chunk(s); "
                    "review the preview for completeness."
                )
            elapsed_total = int(time.perf_counter() - cleaning_started)
            mm = elapsed_total // 60
            ss = elapsed_total % 60
            cleaning_panel_slot.info(
                f"Cleaning documents: {cleaned_count}/{total} complete ({mm:02d}:{ss:02d} elapsed)"
            )
            if detail_lines:
                cleaning_detail_slot.markdown("  \n".join(detail_lines[-6:]))

        cleaning_panel_slot.success(
            f"Document cleaning complete: {cleaned_count}/{total} file(s) processed."
        )
        return cleaned, notes

    if experience_mode == "basic":
        with st.expander("Step 2: Review defaults", expanded=step2_open):
            st.caption("No tuning needed for first run. These defaults are applied automatically:")
            st.markdown(
                "- Style: `UAB Craft Handmade`\n"
                "- Audience: `Academic / research`\n"
                "- Layout: `1792x1024` (landscape)\n"
                "- Quality: `high`"
            )
            st.caption("Tip: switch to Advanced anytime to adjust style, compare variants, or add chart references.")

    has_chart_inputs = False
    step_context_ready = bool(sanitized_context.strip() or extracted_preview)
    step_refs_ready = False
    step_generate_ready = bool(phi_ok and not inj_rule_ids and not file_issues)

    if experience_mode == "advanced":
        # ── Publication chart / data reference (optional, no pre-verify step) ──
        st.markdown("### 📊 Publication chart reference (optional)")
        st.caption(
            "Upload figures or tables from manuscripts, or raw chart data (CSV/XLSX/JSON). "
            "This is folded into the prompt so generated infographics align with publication values. "
            "Use **Review charts in output** below after generation to validate what was rendered."
        )
        chart_figures = st.file_uploader(
            "Upload existing chart or figure (PNG, JPG, WEBP)",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="chart_figure_uploader",
            help="Upload chart images from publications. The model will extract values for accuracy.",
        )
        chart_data_files = st.file_uploader(
            "Upload raw data (CSV, XLSX, JSON)",
            type=["csv", "xlsx", "json"],
            accept_multiple_files=True,
            key="chart_data_uploader",
            help="Upload data files with exact values for charts, bars, or statistics.",
        )
        has_chart_inputs = bool(st.session_state.charts or chart_figures or chart_data_files)
        step_context_ready = bool(sanitized_context.strip() or extracted_preview)
        step_refs_ready = has_chart_inputs
        step_generate_ready = bool(phi_ok and not inj_rule_ids and not file_issues)
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

        if not st.session_state.charts:
            st.markdown(
                "<span style='color: #666'>No chart references yet. Upload chart images or data files above, or use the buttons below to add manual entries.</span>",
                unsafe_allow_html=True,
            )

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
                        "Footnotes (one per line)", value=foot, height=68, key=f"ft_{cid}"
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

    else:
        chart_reference_block = ""
        fidelity_preflight = []
        chart_context_snippet = ""
        snippet_txt = ""
        cross_text = combined_docs.strip()
        st.caption("Basic mode uses document-only generation. Use Advanced for chart/data controls.")
    if experience_mode == "advanced":
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

    inferred_profile_preview: dict[str, Any] = {}
    tentative_prompt = build_infographic_prompt(
        selected_style_key if mode == "single" else list(STYLES.keys())[0],
        sanitized_context,
        [regex_cleanup_fallback(combined_docs)] if combined_docs else [],
        audience,
        st.session_state.get("refinement_notes", ""),
        logo_extra,
        chart_reference_block=chart_reference_block,
        inferred_profile=inferred_profile_preview,
    )

    if experience_mode == "advanced":
        with st.expander("View full prompt (audited locally only — never logged server-side)", expanded=False):
            st.caption(
                "This preview uses **regex cleanup** on uploaded documents. On **Generate**, the app runs "
                "**LLM document cleanup first**, then builds the prompt — so live prompts can differ slightly "
                "from this preview when files are attached."
            )
            if provider == "azure":
                max_prompt_len = AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
                optimized_preview = optimize_azure_image_prompt(tentative_prompt, max_prompt_len)
                st.caption(
                    f"Azure effective prompt preview (after optimization): "
                    f"{len(optimized_preview)} chars (limit {max_prompt_len})."
                )
                st.text_area(
                    "Effective prompt sent to Azure image endpoint",
                    value=optimized_preview,
                    height=420,
                    disabled=True,
                    key="effective_azure_prompt_preview",
                )
                if optimized_preview != tentative_prompt:
                    st.caption(
                        f"Original prompt length before optimization: {len(tentative_prompt)} chars."
                    )
            else:
                st.caption(f"Prompt length: {len(tentative_prompt)} chars.")
                st.text_area(
                    "Full prompt preview",
                    value=tentative_prompt,
                    height=420,
                    disabled=True,
                    key="full_prompt_preview",
                )

        with st.expander("Prompt sent to API (last run)", expanded=False):
            sent_prompt = str(st.session_state.get("last_effective_prompt", "") or "")
            sent_hash = str(st.session_state.get("last_effective_prompt_sha256", "") or "")
            if sent_prompt:
                st.caption(
                    f"Length: {len(sent_prompt)} chars | SHA-256: `{sent_hash}`"
                )
                st.text_area(
                    "Exact prompt body sent on last generation call",
                    value=sent_prompt,
                    height=420,
                    disabled=True,
                    key="sent_prompt_preview",
                )
            else:
                st.caption("No generation run yet in this session.")

        with st.expander("Inferred source profile (last run)", expanded=False):
            prof = st.session_state.get("last_inferred_profile", {}) or {}
            if isinstance(prof, dict) and prof:
                st.caption(
                    "Normalized citation fields and inferred mode used for prompt building."
                )
                st.markdown(
                    f"- Citation title: `{str(prof.get('citation_title','') or '[not inferred]')}`\n"
                    f"- Citation journal: `{str(prof.get('citation_journal','') or '[not inferred]')}`\n"
                    f"- Citation year: `{str(prof.get('citation_year','') or '[not inferred]')}`\n"
                    f"- Citation authors: `{str(prof.get('citation_authors_short','') or '[not inferred]')}`\n"
                    f"- Non-numeric mode: `{'ON' if bool(prof.get('non_numeric_mode')) else 'OFF'}`"
                )
            else:
                st.caption("No inferred profile yet. Generate once to inspect inferred fields.")

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

    generate_btn = False
    step3_title = "Step 3: Generate"
    with st.expander(step3_title, expanded=(experience_mode == "basic" or step3_open)):
        if experience_mode == "basic":
            st.markdown(
                "<div style='border:1px solid #d9e8e2;border-radius:12px;padding:14px;background:#f7fbf9'>"
                "<div style='font-weight:700;color:#1A5632;margin-bottom:4px'>Ready to generate</div>"
                "<div style='color:#38655b;font-size:0.92rem'>"
                "The app will clean your sources and generate a complete first draft infographic."
                "</div></div>",
                unsafe_allow_html=True,
            )
            phi_ok = st.checkbox(
                "I confirm this content does NOT contain protected health information (PHI).",
                value=bool(st.session_state.get("phi_confirm", False)),
                key="phi_confirm",
            )
        else:
            phi_ok = bool(st.session_state.get("phi_confirm", False))

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
        if mode == "compare" and len(compare_style_keys) != 3:
            readiness_issues.append("Pick exactly 3 styles for comparison mode.")
        if publication_fidelity_mode and fidelity_preflight:
            readiness_issues.append("Resolve publication fidelity preflight issues in chart references.")

        gen_disabled = (
            not phi_ok
            or bool(credential_issue)
            or bool(inj_rule_ids)
            or bool(file_issues)
            or (mode == "compare" and len(compare_style_keys) != 3)
            or (publication_fidelity_mode and bool(fidelity_preflight))
        )

        st.markdown("### Readiness")
        if experience_mode == "basic":
            sources_ok = bool(step_context_ready) and not bool(file_issues) and not bool(inj_rule_ids)
            phi_status_ok = bool(phi_ok)
            api_ok = not bool(credential_issue)

            def _badge(ok: bool) -> str:
                return "✅" if ok else "⚠️"

            st.markdown(
                f"{_badge(sources_ok)} **Sources**  \u00a0\u00a0 "
                f"{_badge(phi_status_ok)} **PHI Confirmed**  \u00a0\u00a0 "
                f"{_badge(api_ok)} **API Connected**"
            )
            if not gen_disabled:
                st.caption("Ready. Click Generate to create your infographic.")
            else:
                st.caption("Complete all checklist items above to enable Generate.")
        else:
            if readiness_issues:
                st.warning("Generation is currently blocked:")
                for issue in readiness_issues:
                    st.markdown(f"- {issue}")
            else:
                st.success("All checks passed. Ready to generate.")

        if mode == "single":
            generate_btn = st.button(
                "🎨 Generate infographic" if experience_mode == "advanced" else "🎨 Generate",
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

    # Allow refinement flow to trigger generation without requiring users to scroll back up.
    if mode == "single" and st.session_state.pop("refine_generate_now", False):
        generate_btn = True

    progress_bar = st.progress(0.0)
    status_label = st.empty()
    error_box = st.empty()
    image_preview_slot = st.empty()
    timer_slot = st.empty()
    cleaning_panel_slot = st.empty()
    cleaning_detail_slot = st.empty()

    if generate_btn:
        render_generation_placeholder(image_preview_slot, "Creating infographic...")
        # Prevent stale compare outputs from appearing if a new run fails/aborts.
        if mode == "compare":
            st.session_state.comparison_results = []
        else:
            # Prevent stale single output from appearing if a new run fails/aborts.
            st.session_state.last_image_bytes = None
            st.session_state.last_fidelity_qa_pass = False
            st.session_state.last_fidelity_qa_result = None
            st.session_state.last_post_gen_chart_qa_text = ""
            st.session_state.last_refinements_scan = None
        ok, err_msg, client, image_model, chat_model = get_credentials()
        if not ok:
            error_box.error(err_msg)
            image_preview_slot.empty()
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
                cleaning_panel_slot.empty()
                cleaning_detail_slot.empty()
            for line in cleanup_notes:
                st.info(line)

            set_progress(progress_bar, status_label, "Inferring objective/topic", 0.40)
            inferred = infer_source_profile_llm(
                client=client,
                provider=provider,
                chat_model=chat_model,
                user_context=sanitized_context,
                cleaned_document_texts=cleaned_docs,
                audience=audience,
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
            st.session_state.last_inferred_profile = inferred_profile

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
                    inferred_profile=inferred_profile,
                )
                if provider == "azure":
                    max_prompt_len = (
                        AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
                    )
                    effective_prompt = optimize_azure_image_prompt(prompt, max_prompt_len)
                else:
                    effective_prompt = prompt
                if store_prompt:
                    st.session_state.last_prompt = prompt
                    st.session_state.last_effective_prompt = effective_prompt
                    st.session_state.last_effective_prompt_sha256 = hashlib.sha256(
                        effective_prompt.encode("utf-8")
                    ).hexdigest()
                t_req = time.perf_counter()

                def run_with_visual_timer(
                    fn: Any,
                    target_seconds: int = 180,
                    start_frac: float = 0.55,
                    end_frac: float = 0.82,
                ) -> Any:
                    result: dict[str, Any] = {"value": None, "error": None}

                    def _runner() -> None:
                        try:
                            result["value"] = fn()
                        except BaseException as ex:
                            result["error"] = ex

                    worker = threading.Thread(target=_runner, daemon=True)
                    worker.start()
                    t0_wait = time.perf_counter()

                    while worker.is_alive():
                        elapsed = int(time.perf_counter() - t0_wait)
                        mm = elapsed // 60
                        ss = elapsed % 60
                        progress_ratio = min(elapsed / max(target_seconds, 1), 1.0)
                        gen_frac = start_frac + (end_frac - start_frac) * progress_ratio
                        progress_bar.progress(min(1.0, max(0.0, gen_frac)))

                        if elapsed <= target_seconds:
                            status_label.markdown(
                                "**Progress:** Cleaning document text → Building prompt → Submitting to API "
                                "→ Fetching image → Displaying  \n"
                                f"**Current:** Generating image ({mm:02d}:{ss:02d} / 03:00)"
                            )
                            timer_slot.info(
                                f"Rendering in progress: {mm:02d}:{ss:02d} elapsed (target ~03:00)."
                            )
                        else:
                            status_label.markdown(
                                "**Progress:** Cleaning document text → Building prompt → Submitting to API "
                                "→ Fetching image → Displaying  \n"
                                f"**Current:** Generating image ({mm:02d}:{ss:02d})"
                            )
                            timer_slot.warning(
                                f"Still generating ({mm:02d}:{ss:02d}). "
                                "Complex infographic requests can finish any minute now."
                            )
                        time.sleep(1)

                    worker.join()
                    timer_slot.empty()
                    if result["error"] is not None:
                        raise result["error"]
                    return result["value"]

                try:
                    image_ref = run_with_visual_timer(
                        lambda: generate_with_retry(
                            client,
                            provider,
                            image_model,
                            effective_prompt,
                            size,
                            quality,
                            progress_callback=None,
                        )
                    )
                    set_progress(progress_bar, status_label, "Fetching image", 0.82)
                    raw_bytes = fetch_image_bytes(image_ref)
                    if logo_file:
                        raw_bytes = composite_logo_footer(raw_bytes, logo_file, style_id)
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
                        inferred_profile=inferred_profile,
                    )
                    if provider == "azure":
                        max_prompt_len = (
                            AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
                        )
                        effective_prompt = optimize_azure_image_prompt(prompt, max_prompt_len)
                    else:
                        effective_prompt = prompt
                    prompt_sha = hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest()
                    source_docs_sha = hashlib.sha256(
                        "\n\n".join(cleaned_docs).encode("utf-8")
                    ).hexdigest()
                    t_req = time.perf_counter()
                    try:
                        image_ref = generate_with_retry(
                            client,
                            provider,
                            image_model,
                            effective_prompt,
                            size,
                            quality,
                            progress_callback=None,
                        )
                        raw_bytes = fetch_image_bytes(image_ref)
                        if logo_file:
                            raw_bytes = composite_logo_footer(raw_bytes, logo_file, sid)
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
                            "prompt_sha256": prompt_sha,
                            "source_docs_sha256": source_docs_sha,
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
                    t0_wait = time.perf_counter()
                    target_seconds = 180
                    while True:
                        done_count = sum(1 for f in futs if f.done())
                        if done_count == len(futs):
                            break
                        elapsed = int(time.perf_counter() - t0_wait)
                        mm = elapsed // 60
                        ss = elapsed % 60
                        progress_ratio = min(elapsed / max(target_seconds, 1), 1.0)
                        gen_frac = 0.55 + (0.82 - 0.55) * progress_ratio
                        progress_bar.progress(min(1.0, max(0.0, gen_frac)))
                        if elapsed <= target_seconds:
                            status_label.markdown(
                                "**Progress:** Cleaning document text → Building prompt → Submitting to API "
                                "→ Fetching image → Displaying  \n"
                                f"**Current:** Generating 3 styles in parallel ({done_count}/3 done, {mm:02d}:{ss:02d} / 03:00)"
                            )
                            timer_slot.info(
                                f"Rendering 3 styles in parallel: {done_count}/3 complete, "
                                f"{mm:02d}:{ss:02d} elapsed (target ~03:00)."
                            )
                        else:
                            status_label.markdown(
                                "**Progress:** Cleaning document text → Building prompt → Submitting to API "
                                "→ Fetching image → Displaying  \n"
                                f"**Current:** Generating 3 styles in parallel ({done_count}/3 done, {mm:02d}:{ss:02d})"
                            )
                            timer_slot.warning(
                                f"Still generating comparison set ({done_count}/3 complete, {mm:02d}:{ss:02d}). "
                                "Complex requests can finish any minute now."
                            )
                        time.sleep(1)
                    by_sid: dict[str, dict[str, Any]] = {}
                    for fut in futs:
                        row = fut.result()
                        by_sid[row["style_key"]] = row
                timer_slot.empty()
                results = [by_sid[sid] for sid in styles_to_run]
                # Persist one effective prompt hash so users can verify which prompt family
                # the compare run used.
                if results:
                    st.session_state.last_effective_prompt_sha256 = results[0].get(
                        "prompt_sha256", ""
                    )
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
                _sk = str(results[0].get("style_key") or selected_style_key)
                _st_meta = STYLES.get(_sk, {})
                st.session_state.last_refinements_scan_context = {
                    "audience": audience,
                    "style_name": str(_st_meta.get("name") or _sk),
                    "user_context": sanitized_context or "",
                    "source_excerpt": "\n\n".join(cleaned_docs) if cleaned_docs else "",
                    "chart_reference_excerpt": gen_ref_block or "",
                    "refinement_notes_used": refinement or "",
                    "effective_prompt_excerpt": str(
                        st.session_state.get("last_effective_prompt") or ""
                    ),
                }
                st.session_state.last_refinements_scan = None
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
            image_preview_slot.empty()
            timer_slot.empty()
            cleaning_panel_slot.empty()
            cleaning_detail_slot.empty()

        except BaseException as exc:
            error_box.error(user_friendly_error(exc))
            image_preview_slot.empty()
            timer_slot.empty()
            cleaning_panel_slot.empty()
            cleaning_detail_slot.empty()
            if mode == "compare":
                st.session_state.comparison_results = []
            else:
                st.session_state.last_image_bytes = None
                st.session_state.last_fidelity_qa_pass = False
                st.session_state.last_fidelity_qa_result = None
                st.session_state.last_post_gen_chart_qa_text = ""
                st.session_state.last_refinements_scan = None
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
        latest_scan = st.session_state.get("last_refinements_scan")
        if isinstance(latest_scan, dict) and latest_scan:
            if st.button(
                "✨ Use AI suggestions",
                key="btn_use_ai_suggestions_primary",
                type="primary",
                use_container_width=True,
                help="Applies the latest refinements scan suggestions and immediately generates a new version.",
            ):
                apply_txt = format_refinements_scan_for_notes(latest_scan)
                st.session_state.refinement_notes = apply_txt
                st.session_state.refinement_loop_area = apply_txt
                st.session_state.refine_generate_now = True
                st.rerun()
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

        _scan_open = bool(
            (
                isinstance(st.session_state.get("last_refinements_scan"), dict)
                and st.session_state.last_refinements_scan
            )
            or bool(st.session_state.get("refinements_scan_allow", False))
        )
        with st.expander(
            "🔎 Refinements scan (vision — align infographic to intent)",
            expanded=_scan_open,
        ):
            st.caption(
                "After generation, the vision model reads **this PNG** (with composited logo) and scores "
                "how well it matches your context, cleaned sources, and the prompt from the **last successful** "
                "single-mode run. Enables one-click edits for the next generation."
            )
            scan_ctx_raw = st.session_state.get("last_refinements_scan_context") or {}
            scan_ctx: dict[str, str] = {
                str(k): str(v) if v is not None else ""
                for k, v in scan_ctx_raw.items()
            }
            if not scan_ctx.get("effective_prompt_excerpt", "").strip():
                st.warning(
                    "Generate once in single mode so the app can snapshot your prompt "
                    "and sources for this scan."
                )
            scan_allow = st.checkbox(
                "Run refinements scan (uses API credits)",
                value=False,
                key="refinements_scan_allow",
            )
            if st.button(
                "Run refinements scan",
                key="btn_refinements_scan",
                disabled=not scan_allow,
            ):
                ok_s, err_s, client_s, _im_s, _cm_s = get_credentials()
                if not ok_s or client_s is None:
                    st.error(err_s or "Configure API keys for refinements scan.")
                elif not st.session_state.get("last_image_bytes"):
                    st.error("Generate an image first.")
                else:
                    try:
                        with st.spinner("Scanning infographic with vision model…"):
                            scan_result = run_refinements_scan_vision(
                                client_s,
                                get_vision_model_name(),
                                st.session_state.last_image_bytes,
                                scan_ctx,
                            )
                        st.session_state.last_refinements_scan = scan_result
                        audit_log(
                            session_id,
                            provider,
                            selected_style_key,
                            audience,
                            True,
                            0,
                            "refinements_scan_vision",
                        )
                        st.success("Refinements scan complete.")
                        st.rerun()
                    except Exception as ex:
                        st.error(user_friendly_error(ex))

            scan_data = st.session_state.get("last_refinements_scan")
            if isinstance(scan_data, dict) and scan_data:
                lg = scan_data.get("letter_grade") or "?"
                st.markdown(f"#### Letter grade: **{lg}**")
                if (scan_data.get("alignment_summary") or "").strip():
                    st.markdown("**Summary**")
                    st.markdown(scan_data["alignment_summary"])
                if scan_data.get("strengths"):
                    st.markdown("**Strengths**")
                    for s in scan_data["strengths"]:
                        st.markdown(f"- {s}")
                if scan_data.get("issues"):
                    st.markdown("**Issues**")
                    for s in scan_data["issues"]:
                        st.markdown(f"- {s}")
                if (scan_data.get("fidelity_notes") or "").strip():
                    st.markdown("**Source / number fidelity**")
                    st.markdown(scan_data["fidelity_notes"])
                st.markdown("**Recommended refinements (next prompt)**")
                for r in scan_data.get("recommended_refinements") or []:
                    st.markdown(f"- {r}")

                apply_txt = format_refinements_scan_for_notes(scan_data)
                ap1, ap2 = st.columns(2)
                with ap1:
                    if st.button(
                        "Apply refinements to notes field",
                        key="btn_apply_scan_notes",
                        use_container_width=True,
                        type="secondary",
                    ):
                        st.session_state.refinement_notes = apply_txt
                        st.session_state.refinement_loop_area = apply_txt
                        st.success("Copied into refinement notes (scroll down to edit if needed).")
                        st.rerun()
                with ap2:
                    if st.button(
                        "Apply refinements & generate now",
                        key="btn_apply_scan_generate",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state.refinement_notes = apply_txt
                        st.session_state.refinement_loop_area = apply_txt
                        st.session_state.refine_generate_now = True
                        st.rerun()

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
        if st.session_state.get("last_guided_refine_plan", "").strip():
            with st.expander("Latest guided refinement plan", expanded=False):
                st.code(st.session_state.get("last_guided_refine_plan", ""))
        rb1, rb2 = st.columns(2)
        with rb1:
            if st.button(
                "🔁 Save refinement notes",
                key="btn_refine",
                type="secondary",
                help="Applies your notes to the next prompt.",
                use_container_width=True,
            ):
                st.session_state.refinement_notes = refinement
                st.rerun()
        with rb2:
            if st.button(
                "⚡ Save + Generate now",
                key="btn_refine_generate_now",
                type="primary",
                help="Saves notes and immediately starts a new generation.",
                use_container_width=True,
            ):
                st.session_state.refinement_notes = refinement
                st.session_state.refine_generate_now = True
                st.rerun()
        if st.button(
            "🧭 Guided refine (use current image)",
            key="btn_guided_refine_generate",
            type="secondary",
            help="Uses the current image plus your notes to build a structured edit plan, then regenerates.",
            use_container_width=True,
        ):
            if not refinement.strip():
                st.warning("Add refinement notes first, then run guided refine.")
            else:
                ok_g, err_g, client_g, _im_g, _cm_g = get_credentials()
                if not ok_g or client_g is None:
                    st.error(err_g or "Configure API credentials first.")
                elif not st.session_state.get("last_image_bytes"):
                    st.error("Generate an image first so guided refine can analyze it.")
                else:
                    try:
                        plan = build_guided_refinement_notes(
                            client=client_g,
                            vision_model=get_vision_model_name(),
                            current_image_bytes=st.session_state.last_image_bytes,
                            user_notes=refinement,
                            audience=audience,
                        )
                        st.session_state.last_guided_refine_plan = plan
                        st.session_state.refinement_notes = (
                            f"{refinement.strip()}\n\n[GUIDED REFINEMENT PLAN]\n{plan}"
                        )
                        st.session_state.refine_generate_now = True
                        st.rerun()
                    except Exception as ex:
                        st.error(user_friendly_error(ex))
        st.caption(
            "Guided refine uses vision-guided prompt refinement from the current image "
            "(not direct image-edit endpoint replacement)."
        )

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
                if r.get("prompt_sha256"):
                    st.caption(f"Prompt hash: `{str(r['prompt_sha256'])[:12]}`")
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
