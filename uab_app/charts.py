"""Chart verification data model, parsing, extraction, and prompt blocks (SPEC §19)."""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

from openai import AzureOpenAI, OpenAI

from uab_app.constants import (
    CHART_BLOCKING_STATUSES,
    EXTRACTION_SYSTEM_PROMPT,
)

# Auto-generated doc-vs-chart conflict rows use this marker so toggling
# cross-check can drop them without losing user-entered conflicts.
HEURISTIC_CONFLICT_DOC_VALUE = "(no close match in uploaded text — confirm manually)"


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
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return {
            "chart_type": "json_parse_error",
            "data_series": [],
            "extraction_warnings": [
                "Vision model returned invalid JSON — try extraction again or edit fields manually."
            ],
            "confidence_level": {"parse": "low"},
        }


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


def document_numbers_for_matching(document_text: str) -> list[float]:
    """Like extract_numbers_from_text but drops common false-positive integers (e.g. years)."""
    raw = extract_numbers_from_text(document_text)
    out: list[float] = []
    for v in raw:
        if 1900 <= v <= 2100 and abs(v - round(v)) < 1e-9:
            continue
        out.append(v)
    return out


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
    rtol: float = 0.03,
    atol: float = 1e-5,
) -> list[dict[str, Any]]:
    """Heuristic: numbers in chart series not matching any number in source text."""
    doc_nums = document_numbers_for_matching(document_text)
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
                        "document_value": HEURISTIC_CONFLICT_DOC_VALUE,
                        "required_action": "user_select_or_edit",
                    }
                )

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
    out = parse_json_relaxed(content)
    if isinstance(out, dict):
        return out
    return {
        "chart_type": "unexpected_payload",
        "data_series": [],
        "extraction_warnings": ["Unexpected extraction format — try again."],
    }


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


def recompute_chart_status_after_change(
    c: dict[str, Any],
    document_text: str,
    cross_check_documents: bool = False,
) -> None:
    """Set needs_review / conflict_unresolved based on confidence and optional doc cross-check."""
    cd = chart_dict_to_dataclass(c)
    prev = list(c.get("conflicts") or [])
    kept = [x for x in prev if x.get("document_value") != HEURISTIC_CONFLICT_DOC_VALUE]

    extra: list[dict[str, Any]] = []
    if cross_check_documents:
        extra = detect_document_chart_conflicts(cd, document_text)

    merged = kept + extra
    seen: set[tuple[str | None, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in merged:
        sig = (item.get("field"), str(item.get("document_value")), str(item.get("chart_image_value")))
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(item)
    c["conflicts"] = unique[:25]

    if c.get("verification_status") in ("verified", "placeholder_approved", "removed"):
        return
    warns = median_iqr_data_sufficiency_warning(cd)
    c["extraction_warnings"] = list(
        dict.fromkeys((c.get("extraction_warnings") or []) + warns)
    )
    if c["conflicts"]:
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
