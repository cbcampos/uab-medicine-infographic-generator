"""Streamlit UI for the Infographic Generator - calls backend API."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from aiweb_common.streamlit.streamlit_common import apply_uab_font, hide_streamlit_branding
from aiweb_common.WorkflowHandler import manage_sensitive

from frontend.Api import generate_infographic


AZURE_PROXY_ENDPOINT = manage_sensitive("azure_proxy_endpoint")
AZURE_PROXY_KEY = manage_sensitive("azure_proxy_key")


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


def apply_uab_brand_css() -> None:
    """Apply a UAB-inspired visual skin for Streamlit controls/layout."""
    st.markdown(
        """
        <style>
        :root {
            --uab-green: #1A5632;
            --uab-navy: #003A5C;
            --uab-gold: #FFC72C;
            --uab-teal: #08948E;
            --uab-border: #d8e8e2;
            --uab-soft: #f7fbf9;
        }

        .stApp {
            background: linear-gradient(180deg, #f3f6f8 0%, #eef3f6 100%);
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f6faf8 0%, #eef6f2 100%);
            border-right: 1px solid var(--uab-border);
        }

        .uab-site-wrap {
            border: 1px solid #d8e3e8;
            border-radius: 14px;
            overflow: hidden;
            margin-bottom: 14px;
            background: #fff;
            box-shadow: 0 8px 24px rgba(8, 35, 53, 0.08);
        }
        .uab-topline {
            background: #0f5d38;
            color: #eaf5ef;
            font-size: 0.82rem;
            padding: 7px 18px;
            display: flex;
            justify-content: space-between;
            letter-spacing: 0.2px;
        }
        .uab-topline strong { color: #fff; font-weight: 700; }
        .uab-mainnav {
            background: #fff;
            border-bottom: 1px solid #e2eaee;
            padding: 14px 18px 10px;
        }
        .uab-brand {
            color: #0f2434;
            font-size: 2rem;
            line-height: 1.06;
            margin: 0;
            font-weight: 700;
            letter-spacing: -0.02em;
        }
        .uab-brand-sub {
            margin: 4px 0 0;
            color: #355264;
            font-size: 0.92rem;
        }
        .uab-hero {
            background:
              radial-gradient(circle at 22% 22%, rgba(255, 199, 44, 0.18), transparent 42%),
              radial-gradient(circle at 78% 42%, rgba(8, 148, 142, 0.16), transparent 44%),
              linear-gradient(120deg, #00263f 0%, #003a5c 52%, #0a5f53 100%);
            color: #fff;
            padding: 28px 20px;
        }
        .uab-hero h2 {
            margin: 0;
            font-size: 2.15rem;
            line-height: 1.1;
            letter-spacing: -0.02em;
            max-width: 760px;
        }
        .uab-hero p {
            margin: 10px 0 0;
            color: #dbe7ee;
            max-width: 760px;
            font-size: 1rem;
        }

        h3 {
            color: #12374c;
            letter-spacing: -0.01em;
            font-weight: 700 !important;
        }

        [data-testid="stExpander"] {
            border: 1px solid var(--uab-border);
            border-radius: 12px;
            background: #fff;
            box-shadow: 0 4px 12px rgba(16, 39, 54, 0.06);
        }

        [data-testid="stExpander"] summary {
            background: var(--uab-soft);
            border-radius: 12px;
            padding: 0.35rem 0.6rem;
        }

        .stTextArea textarea, .stTextInput input, .stSelectbox select {
            border-radius: 10px !important;
            border: 1px solid #cfded8 !important;
        }

        div[data-testid="stButton"] > button {
            border-radius: 10px;
            border: 1px solid #c7dad3;
            font-weight: 600;
        }

        div[data-testid="stButton"] > button[kind="primary"] {
            background: linear-gradient(135deg, var(--uab-green), #226d40);
            color: white;
            border: none;
            box-shadow: 0 2px 8px rgba(26, 86, 50, 0.25);
        }

        div[data-testid="stButton"] > button[kind="secondary"] {
            background: white;
            color: var(--uab-navy);
        }
        div[data-testid="stButton"] > button:hover {
            transform: translateY(-1px);
        }

        [data-testid="stAlert"] {
            border-radius: 10px;
        }

        hr {
            border-color: #d7e7e2 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="Infographic Generator",
        page_icon="🖼️",
        layout="wide",
    )
    apply_uab_brand_css()
    hide_streamlit_branding()

    backend_url = f"http://{os.environ.get('BACKEND_HOST', 'localhost')}:{os.environ.get('BACKEND_PORT', '8000')}"

    st.markdown(
        """
        <div class="uab-site-wrap">
            <div class="uab-topline">
                <div><strong>UAB MEDICINE</strong> | Infographic Studio</div>
                <div>Explore UAB Medicine</div>
            </div>
            <div class="uab-mainnav">
                <h1 class="uab-brand">Infographic Generator</h1>
                <p class="uab-brand-sub">Clinical and community communication design workspace</p>
            </div>
            <div class="uab-hero">
                <h2>Turn source evidence into polished UAB-style infographic concepts.</h2>
                <p>Generate draft visuals rapidly, compare styles, and refine with AI-guided feedback.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.warning(
        "For ideation only — not for production use. Generated infographics may contain hallucinations, "
        "inaccurate numbers, or misleading wording. Always perform human review and final editing, and "
        "use a qualified graphic designer/content owner before publication."
    )

    with st.sidebar:
        st.markdown("### 🎯 Generation Settings")
        style = st.selectbox(
            "Style",
            options=["uab-craft-handmade", "uab-modern-clean", "uab-bold-graphic"],
            index=0,
            help="Visual style for the generated infographic",
        )
        audience = st.selectbox(
            "Audience",
            options=["academic", "clinical", "patient", "community"],
            index=0,
            help="Target audience for the infographic",
        )
        size = st.selectbox(
            "Image size",
            options=["1024x1024", "1024x1792", "1792x1024"],
            index=2,
        )
        quality = st.selectbox("Quality", options=["high", "medium"], index=0)

        st.markdown("---")
        st.markdown(f"**Backend:** {backend_url}")

    col_main, col_doc = st.columns([1, 1])

    with col_main:
        st.markdown("### 📄 Upload Source Document")
        st.caption("Max 10MB. Supported: PDF, DOCX, TXT")
        uploaded_file = st.file_uploader(
            "Upload files",
            type=["pdf", "docx", "txt"],
            help="Source document for context",
        )

        user_context = st.text_area(
            "Optional: describe the topic and key points",
            height=150,
            placeholder="e.g. Create a clean summary infographic highlighting the main findings.",
        )

        phi_confirm = st.checkbox(
            "I confirm this content does NOT contain protected health information (PHI).",
            value=False,
        )

        generate_disabled = not (uploaded_file and phi_confirm)
        if st.button("🖼️ Generate Infographic", disabled=generate_disabled, type="primary"):
            if uploaded_file is None:
                st.error("Please upload a document first.")
            else:
                with st.spinner("Generating infographic..."):
                    file_bytes = uploaded_file.getvalue()
                    extension = Path(uploaded_file.name).suffix.lower()

                    image_bytes, prompt, error = generate_infographic(
                        file_bytes,
                        extension,
                        AZURE_PROXY_ENDPOINT,
                        "gpt-image-2",
                        AZURE_PROXY_KEY,
                        style=style,
                        audience=audience,
                        user_context=user_context,
                        size=size,
                        quality=quality,
                    )

                if error:
                    st.error(f"Generation failed: {error}")
                else:
                    st.success("Infographic generated successfully!")
                    st.image(image_bytes, use_container_width=True)

                    st.download_button(
                        label="📥 Download Image",
                        data=image_bytes,
                        file_name="infographic.png",
                        mime="image/png",
                    )

                    with st.expander("View generated prompt"):
                        st.text(prompt[:2000] + ("..." if len(prompt) > 2000 else ""))

    with col_doc:
        st.markdown("### 📖 Instructions")
        st.markdown("""
        1. Upload a source document (PDF, DOCX, or TXT)
        2. Optionally describe your goal
        3. Confirm the content contains no PHI
        4. Click Generate

        The infographic will be generated using the UAB Medicine visual style.
        """)

        st.markdown("### ℹ️ About")
        st.markdown("""
        This tool uses AI image generation to create infographic concepts from your source materials.
        Results are for ideation and must be reviewed by a human before any publication.
        """)


if __name__ == "__main__":
    main()