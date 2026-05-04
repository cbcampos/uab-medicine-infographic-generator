"""
UAB Medicine Infographic Generator
GPT Image 2.0 (OpenAI direct or Azure OpenAI) · notex-style prompt architecture
"""

from __future__ import annotations

import base64
import copy
import csv
import io
import json
import logging
import os
import re
import time
import uuid
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import pandas as pd

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
OPENAI_DEFAULT_VISION_MODEL = "gpt-4o"

# Chart verification (SPEC §19)
CHART_VERIFICATION_STATUSES = frozenset(
    {
        "unverified",
        "needs_review",
        "conflict_unresolved",
        "verified",
        "placeholder_approved",
        "removed",
    }
)
CHART_BLOCKING_STATUSES = frozenset({"unverified", "needs_review", "conflict_unresolved"})
CHART_MODES = ("exact", "style_transform", "reference_only")
CHART_DATA_FILE_EXT = {".csv", ".xlsx", ".json"}
CHART_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_CHART_UPLOAD_BYTES = MAX_UPLOAD_BYTES

EXTRACTION_SYSTEM_PROMPT = """You extract structured data from medical/statistical charts for an infographic pipeline.
Output ONLY valid JSON (no markdown fences, no commentary). Use this exact schema:
{
  "chart_type": string,
  "title": string,
  "axis_labels": {"x": string, "y": string},
  "axis_units": {"x": string, "y": string},
  "category_labels": [string],
  "legend_labels": [string],
  "data_series": [
    {
      "label": string,
      "n": number or null,
      "median": number or null,
      "mean": number or null,
      "value": number or null,
      "range_type": "IQR"|"CI"|"SD"|"SE"|"min_max"|null,
      "range_low": number or null,
      "range_high": number or null,
      "point_estimate": number or null,
      "lower_ci": number or null,
      "upper_ci": number or null,
      "category": string,
      "unit": string
    }
  ],
  "footnotes": [string],
  "source_citation": string,
  "confidence_level": { "field.path": "high"|"medium"|"low" },
  "extraction_warnings": [string],
  "suggested_chart_render": "dot_and_range"|"evidence_callout"|"bar"|"pie"|"placeholder"|"other"
}
If the image is not a chart, set chart_type to "not_a_chart" and data_series to [].
Never invent values: if unreadable, use null and lower confidence.
"""

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


# ─── Chart verification data model & helpers (SPEC §19) ────────
@dataclass
class ChartData:
    chart_id: str
    chart_type: str = "unknown"
    title: str = ""
    axis_labels: dict[str, str] = field(default_factory=dict)
    axis_units: dict[str, str] = field(default_factory=dict)
    category_labels: list[str] = field(default_factory=list)
    legend_labels: list[str] = field(default_factory=list)
    data_series: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    source_citation: str = ""
    verification_status: str = "unverified"
    confidence_level: dict[str, str] = field(default_factory=dict)
    data_source_types: list[str] = field(default_factory=list)
    visual_reference: bool = False
    chart_mode: str = "exact"
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    placeholder_text: str = ""
    extraction_warnings: list[str] = field(default_factory=list)
    source_file: str = ""
    source_location: str = ""
    low_confidence_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ChartData:
        known = {f.name for f in fields(ChartData)}
        clean = {k: v for k, v in d.items() if k in known}
        return ChartData(**clean)


def new_chart_id() -> str:
    return f"chart_{uuid.uuid4().hex[:10]}"


def chart_dict_to_dataclass(d: dict[str, Any]) -> ChartData:
    return ChartData.from_dict(d)


def parse_json_relaxed(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return json.loads(t)


def _float_try(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def extract_numbers_from_text(text: str) -> list[float]:
    nums: list[float] = []
    for m in re.finditer(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", text, re.I):
        try:
            nums.append(float(m.group(0)))
        except ValueError:
            pass
    return nums


def values_from_data_series(series: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    keys = (
        "median",
        "mean",
        "value",
        "range_low",
        "range_high",
        "point_estimate",
        "lower_ci",
        "upper_ci",
    )
    for row in series:
        for k in keys:
            v = _float_try(row.get(k))
            if v is not None:
                out.append(v)
    return out


def detect_document_chart_conflicts(
    chart: ChartData,
    document_text: str,
    rtol: float = 0.02,
    atol: float = 1e-6,
) -> list[dict[str, Any]]:
    """Heuristic: numbers in chart series not matching any number in source text."""
    doc_nums = extract_numbers_from_text(document_text)
    if not doc_nums:
        return []
    conflicts: list[dict[str, Any]] = []
    for i, row in enumerate(chart.data_series):
        for key in (
            "median",
            "mean",
            "value",
            "range_low",
            "range_high",
            "point_estimate",
            "lower_ci",
            "upper_ci",
        ):
            v = _float_try(row.get(key))
            if v is None:
                continue
            matched = any(abs(v - d) <= atol + rtol * max(abs(v), abs(d)) for d in doc_nums)
            if not matched:
                conflicts.append(
                    {
                        "field": f"data_series[{i}].{key}",
                        "chart_image_value": str(v),
                        "document_value": "(no close match in uploaded text — confirm manually)",
                        "required_action": "user_select_or_edit",
                    }
                )
    # Reverse check: prominent doc numbers missing from chart (optional warning)
    # Reverse direction omitted: document text includes years/sample sizes that are not chart labels.

    # Dedupe by field + values
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for c in conflicts:
        sig = (c["field"], str(c.get("document_value")), str(c.get("chart_image_value")))
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(c)
    return unique[:25]


def merge_extraction_into_chart(raw: dict[str, Any], existing: ChartData) -> None:
    existing.chart_type = str(raw.get("chart_type") or existing.chart_type)
    existing.title = str(raw.get("title") or existing.title)
    existing.axis_labels = dict(raw.get("axis_labels") or existing.axis_labels)
    existing.axis_units = dict(raw.get("axis_units") or existing.axis_units)
    existing.category_labels = list(raw.get("category_labels") or [])
    existing.legend_labels = list(raw.get("legend_labels") or [])
    ds = raw.get("data_series")
    if isinstance(ds, list):
        existing.data_series = [dict(r) for r in ds if isinstance(r, dict)]
    existing.footnotes = list(raw.get("footnotes") or [])
    existing.source_citation = str(raw.get("source_citation") or "")
    cl = raw.get("confidence_level")
    if isinstance(cl, dict):
        existing.confidence_level = {str(k): str(v) for k, v in cl.items()}
    ew = raw.get("extraction_warnings")
    if isinstance(ew, list):
        existing.extraction_warnings = [str(x) for x in ew]
    existing.low_confidence_fields = [
        k for k, v in existing.confidence_level.items() if str(v).lower() == "low"
    ]
    if str(raw.get("suggested_chart_render") or "") == "dot_and_range":
        if "box" in existing.chart_type.lower():
            existing.extraction_warnings.append(
                "Box plots and whiskers are not allowed with median+IQR-only data — use dot-and-range."
            )


def gpt4o_extract_chart_from_image(
    client: OpenAI | AzureOpenAI,
    image_bytes: bytes,
    mime_type: str,
    vision_model: str,
    extra_context: str = "",
) -> dict[str, Any]:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"
    user_text = (
        "Extract the chart faithfully into JSON per schema. "
        "Flag unreadable elements with low confidence."
    )
    if extra_context.strip():
        user_text += "\n\nSource document excerpt for cross-check:\n" + extra_context[:6000]
    resp = client.chat.completions.create(
        model=vision_model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    content = (resp.choices[0].message.content or "").strip()
    return parse_json_relaxed(content)


def parse_csv_to_data_series(file_bytes: bytes) -> list[dict[str, Any]]:
    text = file_bytes.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for row in reader:
        clean = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
        if any(clean.values()):
            rows.append(clean)
    return rows


def parse_json_data_file(file_bytes: bytes) -> list[dict[str, Any]]:
    data = json.loads(file_bytes.decode("utf-8", errors="replace"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("data_series"), list):
            return [x for x in data["data_series"] if isinstance(x, dict)]
        if isinstance(data.get("series"), list):
            return [x for x in data["series"] if isinstance(x, dict)]
        return [data]
    return []


def parse_xlsx_to_data_series(file_bytes: bytes) -> list[dict[str, Any]]:
    try:
        import openpyxl
    except ImportError:
        return []
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return []
    headers = [str(h or "").strip() for h in header]
    out: list[dict[str, Any]] = []
    for row in rows_iter:
        if row is None:
            continue
        d: dict[str, Any] = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            if i < len(row) and row[i] is not None:
                d[h] = row[i]
        if d:
            out.append(d)
    return out


def median_iqr_data_sufficiency_warning(chart: ChartData) -> list[str]:
    warns: list[str] = []
    ct = chart.chart_type.lower()
    if any(x in ct for x in ("box", "forest", "whisker")):
        for row in chart.data_series:
            has_m = _float_try(row.get("median")) is not None
            has_r = (
                _float_try(row.get("range_low")) is not None
                and _float_try(row.get("range_high")) is not None
            )
            if has_m and has_r:
                warns.append(
                    "Box plots and whiskers are not allowed with this data — use dot-and-range with exact median and IQR range only."
                )
                break
    return warns


def format_verified_charts_for_prompt(charts: list[dict[str, Any]]) -> str:
    """SPEC §19j — only verified or placeholder_approved charts contribute."""
    blocks: list[str] = []
    for c in charts:
        st = c.get("verification_status", "")
        if st == "removed":
            continue
        if st not in ("verified", "placeholder_approved"):
            continue
        cd = chart_dict_to_dataclass(c)
        mode_note = ""
        if cd.chart_mode == "reference_only":
            mode_note = " [REFERENCE-ONLY — non-exact visual inspiration; do not copy numbers unless listed below.]"
        if st == "placeholder_approved":
            blocks.append(
                f"- PLACEHOLDER{mode_note}: {cd.placeholder_text or 'Exact values to be inserted from source figure/table'}"
            )
            continue
        payload = {
            "chart_type": cd.chart_type,
            "title": cd.title,
            "axis_labels": cd.axis_labels,
            "axis_units": cd.axis_units,
            "category_labels": cd.category_labels,
            "legend_labels": cd.legend_labels,
            "data_series": cd.data_series,
            "footnotes": cd.footnotes,
            "source_citation": cd.source_citation,
            "chart_mode": cd.chart_mode,
        }
        blocks.append(json.dumps(payload, ensure_ascii=False, indent=2))
    if not blocks:
        return ""
    return (
        "CHART ACCURACY RULES: Use only the verified chart data below. Do not infer, estimate, "
        "interpolate, or invent values. Do not add extra series, labels, axes, subgroups, "
        "confidence intervals, or legends. If any value is missing, render the approved "
        "placeholder text exactly. Preserve all numeric labels exactly.\n\n"
        "[VERIFIED CHART DATA]:\n" + "\n\n".join(blocks)
    )


def chart_gate_blocks_generation(
    includes_charts: bool,
    charts: list[dict[str, Any]],
) -> tuple[bool, str]:
    if not includes_charts:
        return False, ""
    active = [c for c in charts if c.get("verification_status") != "removed"]
    if not active:
        return (
            True,
            "Chart workflow is on: add at least one chart (data, figure, or placeholder) or turn off "
            "'includes charts'.",
        )
    for c in active:
        if c.get("verification_status") in CHART_BLOCKING_STATUSES:
            label = c.get("title") or c.get("chart_id")
            return True, f"Chart '{label}' is not verified — complete verification or use placeholder/remove."
    return False, ""


def recompute_chart_status_after_change(c: dict[str, Any], document_text: str) -> None:
    """Set needs_review / conflict_unresolved based on confidence and heuristic doc conflicts."""
    cd = chart_dict_to_dataclass(c)
    extra = detect_document_chart_conflicts(cd, document_text)
    merged: list[dict[str, Any]] = list(c.get("conflicts") or [])
    seen = {(x.get("field"), x.get("chart_image_value"), x.get("document_value")) for x in merged}
    for ex in extra:
        sig = (ex.get("field"), ex.get("chart_image_value"), ex.get("document_value"))
        if sig not in seen:
            seen.add(sig)
            merged.append(ex)
    c["conflicts"] = merged
    if c.get("verification_status") in ("verified", "placeholder_approved", "removed"):
        return
    warns = median_iqr_data_sufficiency_warning(cd)
    c["extraction_warnings"] = list(
        dict.fromkeys((c.get("extraction_warnings") or []) + warns)
    )
    if merged:
        c["verification_status"] = "conflict_unresolved"
    elif c.get("low_confidence_fields"):
        c["verification_status"] = "needs_review"
    else:
        c["verification_status"] = c.get("verification_status") or "unverified"


def run_post_generation_chart_qa(
    client: OpenAI | AzureOpenAI,
    vision_model: str,
    image_bytes: bytes,
    verified_charts_block: str,
) -> str:
    """SPEC §19i — lightweight vision checklist vs verified chart object."""
    if not verified_charts_block.strip():
        return "No verified chart block was in the prompt — QA skipped."
    b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "You verify an infographic image against the REQUIRED chart specification below.\n"
        "Answer in plain bullet points: (1) Are all chart labels/values from the spec visible? "
        "(2) Any extra numbers not in the spec? (3) Missing values? (4) Placeholder handling OK?\n"
        "Be concise.\n\nREQUIRED:\n"
        + verified_charts_block[:12000]
    )
    resp = client.chat.completions.create(
        model=vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    return (resp.choices[0].message.content or "").strip()


def log_chart_audit_trail(
    session_id: str,
    provider: str,
    style_key: str,
    audience: str,
    entry: dict[str, Any],
) -> None:
    row = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "provider": provider,
        "style": style_key,
        "audience": audience,
        "success": True,
        "latency_ms": 0,
        "event": "chart_audit_trail",
        "chart_audit": entry,
    }
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
    verified_charts_block: str = "",
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

## Verified chart data (authoritative when present)
{verified_charts_block if verified_charts_block.strip() else "[No verified chart object attached — follow Source Documents and explicit user numbers only; do not invent chart statistics.]"}

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
    if "charts" not in st.session_state:
        st.session_state.charts = []
    if "processed_chart_figure_sigs" not in st.session_state:
        st.session_state.processed_chart_figure_sigs = set()
    if "processed_data_file_sigs" not in st.session_state:
        st.session_state.processed_data_file_sigs = set()
    if "last_verified_charts_block" not in st.session_state:
        st.session_state.last_verified_charts_block = ""
    if "chart_extraction_nonce" not in st.session_state:
        st.session_state.chart_extraction_nonce = 0


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
                st.text_input(
                    "Vision model (chart extraction / QA)",
                    value=os.environ.get("OPENAI_VISION_MODEL", OPENAI_DEFAULT_VISION_MODEL),
                    key="openai_vision_model",
                    help="Use a vision-capable model (e.g. gpt-4o)",
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
                st.text_input(
                    "Vision deployment (chart extraction / QA)",
                    value=os.environ.get(
                        "AZURE_OPENAI_VISION_DEPLOYMENT",
                        OPENAI_DEFAULT_VISION_MODEL,
                    ),
                    key="azure_vision_deployment",
                    help="Deployment name for gpt-4o-class vision model",
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

    # ── API helpers (used by chart extraction + generation) ──
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

    def get_vision_model_name() -> str:
        if provider == "openai":
            return (
                st.session_state.get("openai_vision_model", "").strip()
                or os.environ.get("OPENAI_VISION_MODEL", OPENAI_DEFAULT_VISION_MODEL)
            )
        return (
            st.session_state.get("azure_vision_deployment", "").strip()
            or os.environ.get("AZURE_OPENAI_VISION_DEPLOYMENT", OPENAI_DEFAULT_VISION_MODEL)
        )

    def run_cleanups(client: Any, chat_model: str) -> list[str]:
        cleaned: list[str] = []
        for name, raw in extracted_preview:
            c, _ = sanitize_input(raw)
            if not c.strip():
                continue
            set_progress(progress_bar, status_label, "Cleaning document text", 0.35)
            cleaned.append(clean_document_text_llm(client, provider, chat_model, c))
        return cleaned

    # ── Chart accuracy inputs & verification (SPEC §19) ────────
    st.markdown("### 📊 Chart accuracy inputs")
    st.caption(
        "Upload existing chart or figure (optional). This helps preserve structure, labels, and layout. "
        "For best accuracy, also provide raw data or confirm extracted values before generation."
    )
    includes_charts_chk = st.checkbox(
        "This infographic includes charts, statistics, confidence intervals, or numeric data "
        "(verification workflow)",
        key="infographic_includes_charts",
        help="When enabled, generation is blocked until every chart is verified, placeholder-approved, or removed.",
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
    chart_context_snippet = st.text_area(
        "Optional: paste extra source text for extraction cross-check",
        height=80,
        key="chart_context_snippet",
        help="Shown to the vision model when extracting from chart images.",
    )
    snippet_txt = chart_context_snippet.strip()
    cross_text = (snippet_txt + "\n" + combined_docs).strip()

    wf_charts_on = includes_charts_chk or bool(chart_figures) or bool(chart_data_files) or bool(
        st.session_state.charts
    )

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
                    "verification_status": "unverified",
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
                    "verification_status": "unverified",
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
            recompute_chart_status_after_change(c_last, cross_text)
    st.session_state.processed_data_file_sigs = data_sigs

    c1p, c2p, c3p = st.columns(3)
    with c1p:
        if st.button("➕ Add manual chart row (empty)", key="btn_add_manual_chart"):
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
                    "verification_status": "unverified",
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
            recompute_chart_status_after_change(st.session_state.charts[-1], cross_text)
    with c2p:
        if st.button("➕ Add placeholder chart", key="btn_add_placeholder_chart"):
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
                    "verification_status": "unverified",
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
        if st.session_state.charts and st.button("🗑️ Clear all charts", key="btn_clear_charts"):
            st.session_state.charts = []
            st.session_state.processed_chart_figure_sigs = set()
            st.session_state.processed_data_file_sigs = set()
            st.rerun()

    st.markdown("#### Verification")
    for idx, chart in enumerate(list(st.session_state.charts)):
        cid = chart.get("chart_id", f"idx_{idx}")
        status = chart.get("verification_status", "unverified")
        badge = {"verified": "✅ verified", "placeholder_approved": "✅ placeholder OK", "removed": "⛔ removed", "needs_review": "⚠️ needs review", "conflict_unresolved": "❌ conflict", "unverified": "⏳ unverified"}.get(
            status, status
        )
        with st.expander(f"Chart {idx + 1}: {chart.get('title') or cid} — {badge}", expanded=(status not in ("verified", "placeholder_approved", "removed"))):
            t1, t2 = st.columns(2)
            with t1:
                chart["title"] = st.text_input("Title", value=chart.get("title", ""), key=f"tit_{cid}")
                chart["chart_type"] = st.text_input("Chart type", value=chart.get("chart_type", ""), key=f"ct_{cid}")
            with t2:
                chart["chart_mode"] = st.selectbox(
                    "Chart mode",
                    options=list(CHART_MODES),
                    format_func=lambda m: {
                        "exact": "Exact (default)",
                        "style_transform": "Style transform (data locked)",
                        "reference_only": "Reference-only (non-exact)",
                    }[m],
                    index=list(CHART_MODES).index(chart.get("chart_mode", "exact"))
                    if chart.get("chart_mode") in CHART_MODES
                    else 0,
                    key=f"mode_{cid}",
                )
            chart["source_citation"] = st.text_input(
                "Source citation", value=chart.get("source_citation", ""), key=f"src_{cid}"
            )

            ds_default = chart.get("data_series") or [{"group": "", "value": "", "label": "", "unit": ""}]
            edited = st.data_editor(
                pd.DataFrame(ds_default),
                num_rows="dynamic",
                key=f"de_{cid}",
                use_container_width=True,
            )
            if len(edited):
                chart["data_series"] = edited.to_dict(orient="records")

            foot = "\n".join(chart.get("footnotes") or [])
            nfoot = st.text_area("Footnotes (one per line)", value=foot, height=60, key=f"ft_{cid}")
            chart["footnotes"] = [ln for ln in nfoot.splitlines() if ln.strip()]

            if chart.get("_bytes_b64"):
                st.caption("Figure upload — run extraction to populate fields from the image.")
                ex_col = st.columns([1, 2])[0]
                with ex_col:
                    if st.button("Run GPT-4o extraction", key=f"ex_{cid}"):
                        ok_e, err_e, client_e, _im, _cm = get_credentials()
                        if not ok_e or client_e is None:
                            st.error(err_e or "API not configured.")
                        else:
                            try:
                                raw_b = base64.b64decode(chart["_bytes_b64"])
                                mime = chart.get("_mime") or "image/png"
                                extra = cross_text[:8000]
                                if snippet_txt:
                                    extra = snippet_txt[:8000]
                                raw_j = gpt4o_extract_chart_from_image(
                                    client_e, raw_b, mime, get_vision_model_name(), extra
                                )
                                cd = chart_dict_to_dataclass(chart)
                                merge_extraction_into_chart(raw_j, cd)
                                chart.update(cd.to_dict())
                                chart["_bytes_b64"] = chart.get("_bytes_b64")
                                chart["_mime"] = chart.get("_mime")
                                recompute_chart_status_after_change(chart, cross_text)
                                if chart.get("low_confidence_fields"):
                                    chart["verification_status"] = "needs_review"
                                st.session_state.chart_extraction_nonce += 1
                                st.success("Extraction complete — review and confirm.")
                                st.rerun()
                            except Exception as ex:
                                st.error(user_friendly_error(ex))

            warns = chart.get("extraction_warnings") or []
            lowc = chart.get("low_confidence_fields") or []
            if warns or lowc:
                st.warning(
                    "Low confidence or warnings: "
                    + "; ".join(warns)
                    + (" | Fields: " + ", ".join(lowc) if lowc else "")
                )

            conf = chart.get("conflicts") or []
            if conf:
                st.error(
                    "Possible conflict between chart values and source text — edit the data table "
                    "to match your approved source, or document why values differ, then re-check."
                )
                for co in conf:
                    st.markdown(
                        f"- **{co.get('field')}** — document: `{co.get('document_value')}` vs chart: `{co.get('chart_image_value')}`"
                    )
                if st.button("Mark conflicts reviewed (I edited the data table)", key=f"cf_done_{cid}"):
                    chart["conflicts"] = []
                    chart["verification_status"] = "unverified"
                    recompute_chart_status_after_change(chart, cross_text)
                    st.rerun()

            ph_cols = st.columns([2, 1])
            with ph_cols[0]:
                chart["placeholder_text"] = st.text_input(
                    "Placeholder text (if using placeholder approval)",
                    value=chart.get("placeholder_text", ""),
                    key=f"pht_{cid}",
                )
            action_cols = st.columns(4)
            with action_cols[0]:
                if st.button("✓ Confirm chart data", key=f"vok_{cid}"):
                    if chart.get("conflicts"):
                        st.error("Resolve conflicts first (use Re-check after editing the table).")
                    else:
                        chart["verification_status"] = "verified"
                        audit_entry = {
                            "chart_id": chart.get("chart_id"),
                            "source_file": chart.get("source_file"),
                            "source_location": chart.get("source_location"),
                            "source_type": "|".join(chart.get("data_source_types") or ["unknown"]),
                            "verified_by_user": True,
                            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "final_chart_type": chart.get("chart_type"),
                            "allowed_values_only": True,
                            "conflicts_resolved": copy.deepcopy(chart.get("conflicts") or []),
                        }
                        log_chart_audit_trail(
                            session_id,
                            provider,
                            selected_style_key if mode == "single" else "compare",
                            audience,
                            audit_entry,
                        )
                        audit_log(
                            session_id,
                            provider,
                            selected_style_key if mode == "single" else "compare_run",
                            audience,
                            True,
                            0,
                            "chart_verified",
                            {"chart_id": chart.get("chart_id")},
                        )
                        st.rerun()
            with action_cols[1]:
                if st.button("Placeholder OK", key=f"pok_{cid}"):
                    chart["verification_status"] = "placeholder_approved"
                    audit_entry = {
                        "chart_id": chart.get("chart_id"),
                        "source_file": chart.get("source_file"),
                        "source_location": chart.get("source_location"),
                        "source_type": "|".join(chart.get("data_source_types") or ["placeholder"]),
                        "verified_by_user": True,
                        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "final_chart_type": "placeholder",
                        "allowed_values_only": True,
                        "conflicts_resolved": [],
                    }
                    log_chart_audit_trail(session_id, provider, selected_style_key, audience, audit_entry)
                    st.rerun()
            with action_cols[2]:
                if st.button("Remove chart", key=f"rm_{cid}"):
                    chart["verification_status"] = "removed"
                    st.rerun()
            with action_cols[3]:
                if st.button("Re-check conflicts", key=f"rc_{cid}"):
                    recompute_chart_status_after_change(chart, cross_text)
                    st.rerun()

    verified_charts_block = format_verified_charts_for_prompt(st.session_state.charts)

    chart_blocked, chart_block_msg = chart_gate_blocks_generation(
        wf_charts_on, st.session_state.charts
    )
    if wf_charts_on:
        if chart_blocked:
            st.error(f"Verification gate: {chart_block_msg}")
        else:
            st.success("Verification gate: all active charts are verified or placeholder-approved. ✅")

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
        verified_charts_block=verified_charts_block,
    )

    with st.expander("View full prompt (audited locally only — never logged server-side)", expanded=False):
        st.code(tentative_prompt[:12000] + ("\n...[truncated]..." if len(tentative_prompt) > 12000 else ""))

    gen_disabled = (
        not phi_ok
        or bool(inj_flags)
        or bool(file_issues)
        or (mode == "compare" and len(set(compare_style_keys)) < 3)
        or (wf_charts_on and chart_blocked)
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
            set_progress(progress_bar, status_label, "Building prompt", 0.1)
            cleaned_docs = run_cleanups(client, chat_model) if extracted_preview else []

            styles_to_run = (
                [selected_style_key] if mode == "single" else compare_style_keys
            )
            results: list[dict[str, Any]] = []
            logo_file = resolve_logo_path()

            gen_verified_block = format_verified_charts_for_prompt(st.session_state.charts)
            st.session_state.last_verified_charts_block = gen_verified_block

            for si, style_id in enumerate(styles_to_run):
                refinement = str(st.session_state.get("refinement_notes", "") or "")
                prompt = build_infographic_prompt(
                    style_id,
                    sanitized_context,
                    cleaned_docs,
                    audience,
                    refinement,
                    logo_extra,
                    verified_charts_block=gen_verified_block,
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
        with st.expander("📋 Post-generation chart QA (vision check vs verified data)", expanded=False):
            st.caption(
                "Compares the generated image to the last verified chart block sent in the prompt (SPEC §19i)."
            )
            if st.button("Run automated QA", key="btn_post_gen_chart_qa"):
                ok_q, err_q, client_q, _im_q, _cm_q = get_credentials()
                if not ok_q or client_q is None:
                    st.error(err_q or "Configure API keys for QA.")
                else:
                    try:
                        qa_txt = run_post_generation_chart_qa(
                            client_q,
                            get_vision_model_name(),
                            st.session_state.last_image_bytes,
                            str(st.session_state.get("last_verified_charts_block", "") or ""),
                        )
                        st.markdown(qa_txt)
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
