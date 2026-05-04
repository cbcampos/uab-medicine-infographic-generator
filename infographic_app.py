"""
UAB Medicine Infographic Generator
GPT Image 2.0 (OpenAI direct or Azure OpenAI) · notex-style prompt architecture
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Optional

import docx
import PyPDF2
import streamlit as st
from openai import APITimeoutError, AzureOpenAI, OpenAI
from PIL import Image

# Optional .env loading
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ─── Constants ─────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
IMAGE_GEN_TIMEOUT_S = 90
MAX_GENERATION_ATTEMPTS = 4  # initial + 3 retries
BACKOFF_BASE_S = 5

OPENAI_DEFAULT_IMAGE_MODEL = "gpt-image-2"
OPENAI_DEFAULT_CHAT_MODEL = "gpt-4o-mini"

INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)disregard\s+(the\s+)?(above|system)"),
    re.compile(r"(?i)system\s*:\s*"),
    re.compile(r"(?i)\[INST\]"),
    re.compile(r"(?i)you\s+are\s+now\s+(a|an|the)\s+"),
    re.compile(r"(?i)override\s+(safety|rules|instructions)"),
    re.compile(r"(?i)developer\s+mode"),
    re.compile(r"(?i)<%.*?%>"),
]

AUDIENCE_GUIDANCE = {
    "academic": """### academic
- Formal tone, data-dense layout
- Include citation placeholders where appropriate
- Minimal decorative illustration; emphasize diagrams and charts
- Professional color palette; restrained use of UAB Gold
- Written for peers (researchers, faculty, clinicians)""",
    "clinical": """### clinical
- Clinical tone, evidence-based framing
- Clear clinical endpoints and outcomes when data exists in the source
- Professional but slightly more visual than academic
- Use clinical terminology appropriately
- Suitable for HCP education materials""",
    "patient": """### patient
- Plain language, warm, non-technical
- Action-oriented messaging ("what you can do")
- Encouraging tone, positive framing
- Friendly illustrations, accessible iconography
- Large readable text, minimal jargon""",
    "community": """### community
- Culturally relevant, warm, community-centered
- Action-oriented with clear calls to action
- Celebratory of community assets and strengths
- Accessible language, relatable visuals
- Suitable for flyers, community presentations, health fairs""",
}

# ─── JSON audit logging (no prompts, no secrets) ───────────────
_audit_logger = logging.getLogger("uab_infographic_audit")
_audit_logger.setLevel(logging.INFO)
if not _audit_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(_h)


def audit_log(
    session_id: str,
    provider: str,
    style: str,
    audience: str,
    success: bool,
    latency_ms: int,
    event: str,
    extra: Optional[dict] = None,
) -> None:
    row: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "provider": provider,
        "style": style,
        "audience": audience,
        "success": success,
        "latency_ms": latency_ms,
        "event": event,
    }
    if extra:
        row.update(extra)
    _audit_logger.info(json.dumps(row))


# ─── Style Library (UAB Medicine) ──────────────────────────────
STYLES: dict[str, dict[str, str]] = {
    "uab-craft-handmade": {
        "name": "UAB Hand-drawn Paper Craft",
        "description": "Warm, organic, community-focused — ideal for patient education",
        "color_palette": """
- Primary: UAB Green (#1A5632), Healing Teal (#08948E), soft warm pastels
- Background: White (#FFFFFF) or Light Cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Cards/panels: Light Teal tint (#E8F6F5)
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), Healing Teal (#08948E), soft warm pastels
- Background: White (#FFFFFF) or Light Cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Fill: Light Teal tint (#E8F6F5) for cards and panels

## Visual Elements
- Hand-drawn or cut-paper quality with organic, slightly imperfect shapes
- Layered depth with soft paper-shadow effects
- Simple cartoon icons representing people and health
- Community/human figures in friendly, approachable cartoon form
- Ample whitespace, clean composition
- Keywords and core concepts highlighted with UAB Gold
- Strictly hand-drawn — no realistic or photographic elements

## Typography
- Clean sans-serif (Source Sans Pro or Open Sans)
- Bold keywords in UAB Green (#1A5632) or Navy (#003A5C)
- Body text in Dark Gray (#4A4A4A)
- Keywords emphasized with larger/bolder text in UAB Gold
""",
    },
    "uab-watercolor": {
        "name": "UAB Storybook Watercolor",
        "description": "Soft hand-painted illustration — professional, editorial quality",
        "color_palette": """
- Primary: Soft washes of UAB Green (#1A5632, low opacity), Healing Teal (#08948E)
- Background: White or cream (#FFF8F0) with watercolor paper texture
- Accents: UAB Gold (#FFC72C) for deeper pigment pools and splatter
- Navy (#003A5C) for line work and detail
""",
        "prompt": """
## Color Palette
- Primary: Soft washes of UAB Green (#1A5632 at low opacity), Healing Teal (#08948E)
- Background: Watercolor paper texture — white or cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C) as deeper pigment pools and splatter
- Navy (#003A5C) for line work and detail

## Visual Elements
- Visible brushstrokes in UAB Green and Teal
- Soft color bleeds and gradients
- White space as a deliberate design element
- Delicate line work over washes
- Organic, flowing medical/health motifs
- Dreamy, atmospheric quality with professional restraint

## Typography
- Elegant serif or humanist sans-serif
- Watercolor-style text integration
- UAB Green or Navy for headings
- Dark Gray (#4A4A4A) for body text
""",
    },
    "uab-academia": {
        "name": "UAB Aged Academia (Vintage Scientific)",
        "description": "Historical scientific illustration — research credibility",
        "color_palette": """
- Primary: Sepia Brown (#704214), UAB Green (#1A5632 at aged tone)
- Background: Parchment (#F4E4BC) or aged cream (#FAF3E0)
- Accents: UAB Gold (#FFC72C) as faded annotation, Navy (#003A5C) ink
""",
        "prompt": """
## Color Palette
- Primary: Sepia Brown (#704214), UAB Green (#1A5632 at aged tone)
- Background: Parchment (#F4E4BC) or aged cream (#FAF3E0)
- Accents: UAB Gold (#FFC72C) as faded annotation, Navy (#003A5C) ink

## Visual Elements
- Aged paper texture overlay
- Detailed cross-hatching and line work in Navy
- Scientific illustration precision
- Study notes and annotations in margins
- Specimen plate / sketch aesthetic
- Numbered diagram elements with UAB Green call-outs
- Visible ink strokes and hand-drawn annotations

## Typography
- Handwritten serif or italic cursive
- Scientific annotations in Dark Gray or Navy
- Small caps for labels in UAB Green
- Italics for scientific names
- UAB Gold for highlighted annotations
""",
    },
    "uab-bold-graphic": {
        "name": "UAB Bold Graphic (Comic/Halftone)",
        "description": "High-contrast comic style — high energy, social media ready",
        "color_palette": """
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), halftone patterns
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), halftone dot patterns

## Visual Elements
- Bold black outlines on all elements
- High contrast UAB color compositions
- Halftone dot patterns in green and gold
- Comic panel borders with UAB Medicine aesthetic
- Action lines and motion for dynamic health content
- Speech bubbles and call-out boxes in UAB palette
- Bold, punchy visual hierarchy

## Typography
- Bold comic lettering in impact style
- UAB Green and Navy for headings
- POW/BANG pop-art effects in UAB Gold
- Caption boxes for data and key statistics
""",
    },
    "uab-corporate": {
        "name": "UAB Corporate Memphis",
        "description": "Flat vector illustration — professional, institutional feel",
        "color_palette": """
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), soft pastels
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), soft pastels

## Visual Elements
- Flat vector illustration style
- Disproportionate friendly human figures
- Abstract body shapes and health symbols
- Floating geometric elements in UAB colors
- Solid fills only — no outlines on figures
- Plant and health object accents

## Typography
- Clean sans-serif (Open Sans, Source Sans Pro)
- Bold UAB Green or Navy headings
- Professional but warm and approachable
- Minimal decoration
""",
    },
    "uab-technical": {
        "name": "UAB Technical Schematic",
        "description": "Engineering-precision diagrams — clinical audience",
        "color_palette": """
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Primary lines: UAB Green (#1A5632) or Navy (#003A5C)
- Accents: UAB Gold (#FFC72C) for highlights, Healing Teal (#08948E) for data
- Critical markup: Red (#CC0000) — sparingly for alerts only
""",
        "prompt": """
## Color Palette
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Primary lines: UAB Green (#1A5632) or Navy (#003A5C)
- Accents: UAB Gold (#FFC72C) for highlights, Healing Teal (#08948E) for data
- Critical markup: Red (#CC0000) — sparingly for alerts only

## Visual Elements
- Precise geometric lines and angles
- Grid patterns in Light Gray
- Measurement and data annotations
- Technical symbols and health data notation
- Dashed construction guides
- Clean clinical aesthetic
- Isometric or orthographic projections

## Typography
- Monospace or technical sans-serif
- Coordinate and dimension labels in Dark Gray
- UAB Green for section labels
- Navy for technical headings
- No decorative elements
""",
    },
    "uab-chalkboard": {
        "name": "UAB Chalkboard",
        "description": "Dark chalkboard background — educational and workshop-friendly",
        "color_palette": """
- Background: Dark Navy (#002A4D) or Chalkboard Black (#1A1A1A)
- Primary Text: Chalk White (#F5F5F5)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E), UAB Green (#1A5632)
""",
        "prompt": """
## Color Palette
- Background: Dark Navy (#002A4D) or Chalkboard Black (#1A1A1A)
- Primary Text: Chalk White (#F5F5F5)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E), UAB Green (#1A5632)
- Available chalk colors: Gold, Teal, Green, White, Navy

## Visual Elements
- Hand-drawn chalk illustrations — sketchy, imperfect lines
- Chalk dust effects around text and key elements
- Doodles: stars, arrows, circles, checkmarks, hearts, plus signs
- Stick figures and simple medical icons
- UAB-style doodads: cross/heart motifs in chalk
- Eraser smudges and chalk residue textures

## Typography
- Hand-drawn chalk lettering style
- Imperfect baseline for authenticity
- White chalk for body text
- UAB Gold chalk for emphasis and call-outs
- Teal and Green as secondary chalk colors
""",
    },
    "uab-kawaii": {
        "name": "UAB Kawaii (Japanese Cute)",
        "description": "Soft, patient-friendly — pastel health themes with big eyes",
        "color_palette": """
- Primary: Soft pastels — mint (#98D8C8), light lavender (#E6E6FA), pale pink
- Background: White (#FFFFFF) or very light cream
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
""",
        "prompt": """
## Color Palette
- Primary: Soft pastels — mint (#98D8C8), light lavender (#E6E6FA), pale pink
- Background: White (#FFFFFF) or very light cream
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Warm tones: soft versions of UAB palette

## Visual Elements
- Big sparkly eyes on cartoon health characters
- Rounded, soft shapes
- Gentle health symbols (hearts, plus signs) in UAB colors
- Sparkles and stars scattered in Gold and Teal
- Cute medical icons (stethoscopes, hearts, pills) in cartoon form
- Chibi-proportioned friendly human figures

## Typography
- Rounded, bubbly sans-serif
- Soft pastels derived from UAB palette
- UAB Gold hearts and Teal dots decorating letters
- Cute, friendly appearance throughout
""",
    },
    "uab-claymation": {
        "name": "UAB Claymation",
        "description": "3D clay figure aesthetic — warm, approachable, stop-motion charm",
        "color_palette": """
- Primary: Saturated UAB Green (#1A5632), Healing Teal (#08948E)
- Background: Light Gray (#F5F5F5) or soft white
- Accents: UAB Gold (#FFC72C), Navy (#003A5C) highlights
- Clay tones: slightly muted, warm
""",
        "prompt": """
## Color Palette
- Primary: Saturated UAB Green (#1A5632), Healing Teal (#08948E)
- Background: Light Gray (#F5F5F5) or soft white
- Accents: UAB Gold (#FFC72C), Navy (#003A5C) highlights
- Clay tones: slightly muted, warm

## Visual Elements
- Clay/plasticine texture on all objects
- Rounded, sculpted human figures (friendly medical staff/patients)
- Soft shadows, stop-motion staging
- Fingerprint marks and imperfections for authenticity
- Miniature set aesthetic
- Warm and approachable health characters

## Typography
- Extruded, dimensional text (as if made of clay)
- Rounded, friendly sans-serif
- Bold UAB Green or Navy for emphasis
- Chunky, playful lettering
""",
    },
    "uab-cyberpunk-neon": {
        "name": "UAB Cyberpunk Neon",
        "description": "Neon glow on deep navy — futuristic medical tech aesthetic",
        "color_palette": """
- Primary: Healing Teal (#08948E) as neon glow, Electric Blue (#00B0FF)
- Background: Deep Navy (#002A4D) or near-black (#0A0A0A)
- Accents: UAB Gold (#FFC72C) neon glow, UAB Green (#1A5632) glow
""",
        "prompt": """
## Color Palette
- Primary: Healing Teal (#08948E) as neon glow, Electric Blue (#00B0FF)
- Background: Deep Navy (#002A4D) or near-black (#0A0A0A)
- Accents: UAB Gold (#FFC72C) neon glow, UAB Green (#1A5632) glow
- Chrome and teal highlights

## Visual Elements
- Glowing neon outlines in Teal and Gold
- Dark atmospheric backgrounds (deep navy, not pure black)
- Subtle circuit/health data patterns
- Digital holographic elements
- Health metric visualizations in neon
- Rain and reflection effects

## Typography
- Glowing neon text — Teal (#08948E) and Gold (#FFC72C)
- Digital/tech sans-serif
- Outlined glow letters for headings
- Flickering or pulsing text effects
""",
    },
}


# ─── Logo path ─────────────────────────────────────────────────
def resolve_logo_path() -> Optional[Path]:
    env_p = os.environ.get("UAB_MEDICINE_LOGO_PATH", "").strip()
    if env_p:
        p = Path(env_p).expanduser()
        if p.is_file():
            return p
    default = Path(__file__).resolve().parent / "assets" / "uab-medicine-logo.jpg"
    if default.is_file():
        return default
    return None


# ─── Input sanitization ────────────────────────────────────────
def strip_control_chars(s: str) -> str:
    return "".join(ch for ch in s if ch == "\n" or ch == "\t" or ord(ch) >= 32)


def detect_prompt_injection(text: str) -> list[str]:
    flags: list[str] = []
    for pat in INJECTION_PATTERNS:
        if pat.search(text):
            flags.append(pat.pattern)
    return flags


def sanitize_input(text: str) -> tuple[str, list[str]]:
    cleaned = strip_control_chars(text)
    flags = detect_prompt_injection(cleaned)
    return cleaned, flags


def regex_cleanup_fallback(text: str) -> str:
    t = strip_control_chars(text)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"(?i)\b(MRN|DOB|SSN)\s*[:#]?\s*[\w\-./]+", "[REDACTED]", t)
    return t.strip()


# ─── Document parsing ───────────────────────────────────────────
def read_pdf(file: io.BytesIO) -> str:
    try:
        reader = PyPDF2.PdfReader(file)
        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n\n".join(parts)
    except Exception:
        return ""


def read_docx_bytes(file: io.BytesIO) -> str:
    try:
        document = docx.Document(file)
        paras = [p.text for p in document.paragraphs if p.text.strip()]
        return "\n\n".join(paras)
    except Exception:
        return ""


def read_txt_bytes(file: io.BytesIO) -> str:
    try:
        return file.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_document_text(uploaded_file: Any) -> str:
    fname = uploaded_file.name.lower()
    ext = Path(fname).suffix
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return ""
    raw = uploaded_file.getvalue()
    file_bytes = io.BytesIO(raw)
    if ext == ".pdf":
        return read_pdf(file_bytes)
    if ext == ".docx":
        return read_docx_bytes(file_bytes)
    if ext == ".txt":
        return read_txt_bytes(file_bytes)
    return ""


# ─── LLM document cleanup ──────────────────────────────────────
def clean_document_text_llm(
    client: OpenAI | AzureOpenAI,
    provider: str,
    chat_model: str,
    text: str,
) -> str:
    max_in = 14_000
    chunk = text[:max_in]
    sys_msg = (
        "You sanitize text for UAB Medicine infographic source context. "
        "Remove control characters and odd whitespace; normalize common OCR artifacts; "
        "redact or remove PHI-adjacent lines (patient names, DOB, MRN, phone numbers, addresses). "
        "Preserve headings, bullets, structure, and factual medical content. "
        "Output ONLY the cleaned text, no preamble."
    )
    try:
        model = chat_model
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": chunk},
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message.content
        if choice and choice.strip():
            return strip_control_chars(choice.strip())
    except Exception:
        pass
    return regex_cleanup_fallback(text)


# ─── OpenAI clients ────────────────────────────────────────────
def make_client(
    provider: str,
    api_key: str,
    endpoint: Optional[str],
    api_version: Optional[str],
) -> OpenAI | AzureOpenAI:
    timeout = float(IMAGE_GEN_TIMEOUT_S)
    if provider == "openai":
        return OpenAI(api_key=api_key, timeout=timeout)
    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version or "2024-12-01-preview",
        azure_endpoint=endpoint or "",
        timeout=timeout,
    )


# ─── Image generation ───────────────────────────────────────────
def _openai_size_map(size: str) -> str:
    m = {"1024x1024": "1024x1024", "1024x1792": "1024x1792", "1792x1024": "1792x1024"}
    return m.get(size, "1792x1024")


def generate_image(
    client: OpenAI | AzureOpenAI,
    provider: str,
    model: str,
    prompt: str,
    size: str,
    quality: str,
) -> str:
    n = 1
    if provider == "openai":
        resp = client.images.generate(
            model=OPENAI_DEFAULT_IMAGE_MODEL,
            prompt=prompt,
            size=_openai_size_map(size),
            quality=quality,
            n=n,
        )
    else:
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            n=n,
        )
    image_data = resp.data[0]
    if getattr(image_data, "url", None):
        return str(image_data.url)
    if getattr(image_data, "b64_json", None):
        return f"data:image/png;base64,{image_data.b64_json}"
    raise RuntimeError("No image payload returned.")


def user_friendly_error(exc: BaseException) -> str:
    if isinstance(exc, APITimeoutError):
        return "The image request timed out. Try again in a moment or simplify your prompt."
    msg = str(exc).strip()
    if len(msg) > 220:
        msg = msg[:217] + "..."
    return f"Generation failed: {msg}"


def generate_with_retry(
    client: OpenAI | AzureOpenAI,
    provider: str,
    model: str,
    prompt: str,
    size: str,
    quality: str,
    progress_callback: Optional[Any] = None,
) -> str:
    last_err: Optional[BaseException] = None
    for attempt in range(MAX_GENERATION_ATTEMPTS):
        try:
            if progress_callback:
                progress_callback(attempt)
            return generate_image(client, provider, model, prompt, size, quality)
        except BaseException as e:
            last_err = e
            if attempt < MAX_GENERATION_ATTEMPTS - 1:
                delay = BACKOFF_BASE_S * (2**attempt)
                time.sleep(delay)
    assert last_err is not None
    raise last_err


# ─── Fetch / composite ────────────────────────────────────────
def fetch_image_bytes(image_url_or_data: str) -> bytes:
    if image_url_or_data.startswith("data:"):
        b64 = image_url_or_data.split(",", 1)[1]
        return base64.b64decode(b64)
    req = urllib.request.Request(image_url_or_data, headers={"User-Agent": "UAB-Infographic-Gen/1.0"})
    with urllib.request.urlopen(req, timeout=IMAGE_GEN_TIMEOUT_S) as resp:
        return resp.read()


def composite_logo_footer(image_bytes: bytes, logo_path: Path) -> bytes:
    """Paste approved logo onto a clean white footer strip (exact pixels from file)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    w, h = img.size
    footer_h = max(int(h * 0.11), 48)
    bar = Image.new("RGBA", (w, footer_h), (255, 255, 255, 255))
    lw, lh = logo.size
    pad = int(footer_h * 0.15)
    max_logo_h = footer_h - 2 * pad
    scale = min((w - 2 * pad) / lw, max_logo_h / lh, 1.0)
    new_w, new_h = int(lw * scale), int(lh * scale)
    logo_r = logo.resize((new_w, new_h), Image.Resampling.LANCZOS)
    lx = (w - new_w) // 2
    ly = footer_h - new_h - pad
    if ly < pad:
        ly = pad
    bar.paste(logo_r, (lx, ly), logo_r)
    out = Image.new("RGBA", (w, h + footer_h), (255, 255, 255, 255))
    out.paste(img, (0, 0))
    out.paste(bar, (0, h))
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# ─── Prompt builder ────────────────────────────────────────────
def build_infographic_prompt(
    style_id: str,
    user_context: str,
    cleaned_document_texts: list[str],
    audience_key: str,
    refinement_notes: str,
    logo_instructions_extra: str,
) -> str:
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    audience_key = audience_key if audience_key in AUDIENCE_GUIDANCE else "patient"
    aud = AUDIENCE_GUIDANCE[audience_key]

    doc_parts = [
        f"### Document {i + 1}\n{text}"
        for i, text in enumerate(cleaned_document_texts)
        if text.strip()
    ]
    doc_block = "\n\n".join(doc_parts) if doc_parts else ""

    refinement_block = refinement_notes.strip() if refinement_notes.strip() else "[None]"

    user_block = (
        user_context.strip()
        if user_context.strip()
        else "[No additional context provided — infer key themes only from approved source text above.]"
    )

    return f"""## Image Specifications
- Type: Infographic
- Layout: Horizontal layout (16:9 aspect ratio)
- Style: {style["name"]}
- Audience: {audience_key}

## Composition and Layout (follow this structure)
- TOP REGION: Title/headline centered, large, bold — reserve for key message
- LEFT / CENTER: Primary content area — key points, icons, illustrations, data callouts
- RIGHT / LOWER: Supporting content, secondary data, evidence summaries
- BOTTOM: Clean white band for logo placement (bottom-right or bottom-center)
- Leave generous negative space — do NOT crowd the layout
- When specific text must appear in the image, write it EXACTLY as it should appear (use ALL CAPS for headings, spell out all technical/medical terms letter by letter)

## Style Guidelines
{style["prompt"]}

## UAB Medicine Brand Compliance
- Primary colors: UAB Green (#1A5632), UAB Gold (#FFC72C)
- Accent: UAB Medicine Navy (#003A5C)
- Secondary/Patient color: Healing Teal (#08948E)
- White background (#FFFFFF) or Light Gray (#F5F5F5) preferred
- Do NOT use Athletic Gold (#E87722) — not part of Medicine palette
- Maintain high contrast for accessibility (WCAG AA minimum)

## UAB Medicine Logo Rules
- Use ONLY the approved UAB Medicine logo file as the logo source.
- Do NOT redraw, reinterpret, recreate, stylize, distort, watercolor, trace, recolor, crop, stretch, or modify the logo.
- Do NOT generate any alternate UAB, UAB Medicine, university, hospital, school, or department logo.
- Do NOT invent seals, icons, shield marks, taglines, or substitute brand marks.
- The approved logo must appear exactly as provided, with correct proportions and colors.
- Place the logo in a clean white footer or corner area with adequate clear space.
- Keep the logo separate from watercolor effects, textures, shadows, illustrations, or background patterns.
- If the logo cannot be reproduced exactly from the attached source, leave a blank white logo placement box labeled: "Approved UAB Medicine logo placement."
{logo_instructions_extra}

## Chart/Data Accuracy Rules (STRICT — no exceptions)
- Do NOT create box plots, forest plots, whisker elements, CI lines, or any chart with inferred data.
- If the source provides only median + IQR: render a DOT-AND-RANGE chart with exact median dots and IQR range bars — nothing else.
- If the source provides only a hazard ratio + CI: render a single EVIDENCE CALLOUT CARD with that exact HR and CI.
- Do NOT display HRs by outcome subtype (e.g. HFpEF vs HFrEF) unless those exact values are explicitly provided.
- If data is incomplete: generate a placeholder box labeled "Exact values to be inserted from [source figure/table]" — do not estimate or fill in.
- All numeric labels, axis values, and legend entries must match the source EXACTLY.
- Do NOT invent whiskers, quartile boundaries, outlier points, or any visual element not explicitly in the source.

## Audience-Specific Guidelines
{aud}

## Content Requirements
- Include simple visual elements, icons, or illustrations to enhance visual appeal
- If content involves sensitive or copyrighted figures, replace with visually similar alternatives
- Keep information concise, highlight keywords and core concepts
- Use whitespace effectively to emphasize key points
- Output in the same language as the provided content
- TEXT IN IMAGE: Write any specific text EXACTLY as it should appear. Use ALL CAPS for titles/headlines. Spell out technical and medical terms letter by letter for accuracy (e.g. "H Y P E R T E N S I O N" not "hypertension" if that is the intended rendering). Do not approximate — if exact wording matters, quote it verbatim.

## Slides, Diagrams & Charts — Artifact Spec Format (STRICT)
- Treat this as an artifact spec, not an illustration request. Name the exact deliverable, define the canvas and visual hierarchy, and provide the real text or data.
- For slides, charts, or diagram-heavy assets: include ALL numbers, labels, axes, and footnotes DIRECTLY in the prompt. Do not assume the model will fill in realistic-looking data — it must be explicit.
- Use quality="high" for any image that contains small text, legends, axes, footnotes, or data labels.
- Use landscape orientation (1792x1024) for deck-style outputs.
- Design requirements: readable typography, polished spacing, clear data hierarchy, professional visual language.
- AVOID: clip art, stock photography, gradients, shadows, decorative clutter, generic or overdesigned elements.
- Structure chart/diagram content with exact values:
  * BAR CHART: Label each bar exactly (e.g. "Group A: 43%", "Group B: 27%"), include axis labels and units.
  * EVIDENCE CALLOUT: Quote the exact finding (e.g. "A 10-point higher LE8 score was associated with 28% lower risk of HF hospitalization. HR: 0.72; 95% CI: 0.66-0.79").
  * DOT-AND-RANGE: Show exact median and IQR values as labeled markers.
  * WORKFLOW DIAGRAM: List each step in sequence with exact wording.
  * If data is incomplete: use a placeholder box labeled "Exact values to be inserted from [source figure/table]" — never estimate.

## Instructions
Use the image generation model to create the illustration based on the provided input.

## Source Documents (Additional Context)
{doc_block if doc_block else "[No document uploads — rely on User-Provided Context below.]"}

## User-Provided Context and Custom Content
{user_block}

## Refinement Notes (if any)
{refinement_block}
"""


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


def set_progress(
    progress_bar: Any,
    status_text: Any,
    stage: str,
    frac: float,
) -> None:
    progress_bar.progress(min(1.0, max(0.0, frac)))
    status_text.markdown(
        "**Progress:** Building prompt → Cleaning document text → Submitting to API "
        "→ Fetching image → Displaying  \n"
        f"**Current:** {stage}"
    )


# ─── Main app ───────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="UAB Medicine Infographic Generator",
        page_icon="🖼️",
        layout="wide",
    )
    init_session_state()
    session_id = st.session_state.session_id

    st.markdown(
        "<div style='background:#1A5632;padding:16px 24px;border-radius:8px;margin-bottom:24px'>"
        "<h1 style='color:white;margin:0;font-family:Source Sans Pro,sans-serif'>"
        "🖼️ UAB Medicine Infographic Generator</h1>"
        "<p style='color:#FFC72C;margin:4px 0 0;font-size:14px'>"
        "GPT Image 2.0 · OpenAI or Azure · UAB Medicine branding"
        "</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### ⚙️ API Configuration")
        provider = st.radio(
            "Provider",
            options=["openai", "azure"],
            format_func=lambda p: (
                "🟢 OpenAI (GPT Image 2)" if p == "openai" else "🔷 Azure OpenAI (GPT Image 2)"
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
        else:
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
    inj_flags = sorted(set(inj_flags_context + inj_flags_docs))

    if inj_flags:
        st.error(
            "Possible prompt-injection phrases were detected. Remove them or revise your text before generating."
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
    )

    with st.expander("View full prompt (audited locally only — never logged server-side)", expanded=False):
        st.code(tentative_prompt[:12000] + ("\n...[truncated]..." if len(tentative_prompt) > 12000 else ""))

    gen_disabled = (
        not phi_ok
        or bool(inj_flags)
        or bool(file_issues)
        or (mode == "compare" and len(set(compare_style_keys)) < 3)
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

    # ── CREDENTIALs + CLIENT ──
    def get_credentials() -> tuple[bool, str, Any, str, str]:
        if provider == "openai":
            api_key = st.session_state.get("openai_api_key", "") or os.environ.get("OPENAI_API_KEY", "")
            chat_model = (
                st.session_state.get("openai_chat_model", "") or OPENAI_DEFAULT_CHAT_MODEL
            )
            if not api_key:
                return False, "OpenAI API key is required.", None, "", chat_model
            client = make_client("openai", api_key, None, None)
            return True, "", client, OPENAI_DEFAULT_IMAGE_MODEL, chat_model
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

    def run_cleanups(client: Any, chat_model: str) -> list[str]:
        cleaned: list[str] = []
        for name, raw in extracted_preview:
            c, _ = sanitize_input(raw)
            if not c.strip():
                continue
            set_progress(progress_bar, status_label, "Cleaning document text", 0.35)
            cleaned.append(clean_document_text_llm(client, provider, chat_model, c))
        return cleaned

    if generate_btn:
        ok, err_msg, client, image_model, chat_model = get_credentials()
        if not ok:
            error_box.error(err_msg)
            return

        assert client is not None

        t0 = time.perf_counter()
        try:
            set_progress(progress_bar, status_label, "Building prompt", 0.1)
            cleaned_docs = run_cleanups(client, chat_model) if extracted_preview else []

            styles_to_run = (
                [selected_style_key] if mode == "single" else compare_style_keys
            )
            results: list[dict[str, Any]] = []
            logo_file = resolve_logo_path()

            for si, style_id in enumerate(styles_to_run):
                refinement = str(st.session_state.get("refinement_notes", "") or "")
                prompt = build_infographic_prompt(
                    style_id,
                    sanitized_context,
                    cleaned_docs,
                    audience,
                    refinement,
                    logo_extra,
                )
                if mode == "single":
                    st.session_state.last_prompt = prompt

                set_progress(progress_bar, status_label, "Submitting to API", 0.55)
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
                    results.append(
                        {
                            "style_key": style_id,
                            "bytes": raw_bytes,
                            "prompt_len": len(prompt),
                        }
                    )
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

            if mode == "single" and results:
                st.session_state.last_image_bytes = results[0]["bytes"]
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
        st.download_button(
            "📥 Download PNG",
            data=st.session_state.last_image_bytes,
            file_name="uab_infographic.png",
            mime="image/png",
            use_container_width=True,
            key="dl_single",
        )
        refinement = st.text_area(
            "Refinement notes (next generation)",
            key="refinement_loop_area",
            height=90,
            placeholder="e.g. Emphasize the screening workflow; enlarge the headline.",
        )
        if st.button(
            "🔁 Save refinement notes and prepare next run",
            key="btn_refine",
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
                    file_name=f"uab_compare_{r['style_key']}.png",
                    mime="image/png",
                    use_container_width=True,
                    key=f"dl_cmp_{i}",
                )

    if st.session_state.generation_history:
        with st.expander("📚 Recent generations (this session)", expanded=False):
            for idx, entry in enumerate(reversed(st.session_state.generation_history[-8:])):
                st.caption(f"{entry.get('style', '')} · {entry.get('audience', '')}")
                st.image(entry["thumb_bytes"], use_container_width=True)

    st.markdown(
        "<hr style='margin-top:32px;border-color:#E8F6F5'>"
        "<p style='color:#6B6B6B;font-size:12px;text-align:center'>"
        "UAB Medicine Infographic Generator · Brand: UAB Medicine colors only · No Athletic Gold (#E87722)"
        "</p>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
