"""User-provided chart reference data: extraction helpers, prompt blocks, post-gen QA."""

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

from uab_app.constants import EXTRACTION_SYSTEM_PROMPT

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


def _parse_json_fallback_dict(detail: str = "") -> dict[str, Any]:
    msg = "Vision model returned invalid JSON — try extraction again or edit fields manually."
    if detail.strip():
        msg = f"{msg} ({detail.strip()[:200]})"
    return {
        "chart_type": "json_parse_error",
        "data_series": [],
        "extraction_warnings": [msg],
        "confidence_level": {"parse": "low"},
    }


def _chat_completion_content_as_text(content: Any) -> str:
    """Normalize OpenAI-style message content (Gemini/other may use str or multimodal lists)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif getattr(item, "text", None) is not None and isinstance(item.text, str):
                parts.append(item.text)
        return "\n".join(x.strip() for x in parts if x and str(x).strip()).strip()
    return str(content).strip()


def _json_candidate_strings(raw: str) -> list[str]:
    """Build substrings likely to contain JSON (Gemini often adds prose / markdown fences)."""
    t = raw.strip()
    if not t:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    add(t)
    add(re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE))
    add(re.sub(r"\s*```\s*$", "", t))

    # Any fenced blocks anywhere in the response
    for m in re.finditer(r"```(?:json)?\s*\r?\n(.*?)```", t, flags=re.DOTALL | re.IGNORECASE):
        blk = m.group(1).strip()
        blk = re.sub(r"^(?:json)\s*", "", blk, flags=re.IGNORECASE)
        add(blk)
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", t, flags=re.DOTALL | re.IGNORECASE):
        blk = m.group(1).strip()
        blk = re.sub(r"^(?:json)\s*", "", blk, flags=re.IGNORECASE)
        add(blk)

    return out


def _try_decode_json_dict(text: str) -> Optional[dict[str, Any]]:
    decoder = json.JSONDecoder()
    cand = text.strip()
    if not cand:
        return None
    try:
        val = json.loads(cand)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    # Embedded JSON object (leading prose / trailing commentary)
    for i, ch in enumerate(cand):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(cand[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def parse_json_relaxed(text: str) -> Any:
    if text is None or not str(text).strip():
        return _parse_json_fallback_dict("Empty model response")
    for cand in _json_candidate_strings(str(text)):
        parsed = _try_decode_json_dict(cand)
        if parsed is not None:
            return parsed
    snippet = str(text).strip().replace("\n", " ")
    if len(snippet) > 300:
        snippet = snippet[:297] + "..."
    return _parse_json_fallback_dict(f"snippet: {snippet}")


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
        "Extract the chart faithfully into JSON matching the system schema. "
        "Output exactly one JSON object with no prose before or after it. "
        "Do not use markdown fences. "
        "Flag unreadable elements with low confidence in confidence_level."
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
    content = _chat_completion_content_as_text(resp.choices[0].message.content)
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


def format_chart_reference_for_prompt(charts: list[dict[str, Any]]) -> str:
    """Fold user-provided publication figures / data files into the image prompt (no pre-verify step)."""
    blocks: list[str] = []
    for c in charts:
        if c.get("verification_status") == "removed":
            continue
        cd = chart_dict_to_dataclass(c)
        mode_note = ""
        if cd.chart_mode == "reference_only":
            mode_note = (
                " [REFERENCE-ONLY — use as visual inspiration; copy only numbers explicitly listed below.]"
            )
        ct = str(cd.chart_type or "").lower()
        if ct == "placeholder":
            blocks.append(
                f"- PLACEHOLDER{mode_note}: {cd.placeholder_text or 'Exact values to be inserted from source figure/table'}"
            )
            continue
        payload: dict[str, Any] = {
            "chart_type": cd.chart_type,
            "title": cd.title,
            "axis_labels": cd.axis_labels,
            "axis_units": cd.axis_units,
            "category_labels": cd.category_labels,
            "legend_labels": cd.legend_labels,
            "data_series": cd.data_series,
            "footnotes": cd.footnotes,
            "source_citation": cd.source_citation,
            "source_file": cd.source_file,
            "chart_mode": cd.chart_mode,
            "visual_reference_upload": cd.visual_reference,
        }
        if cd.visual_reference and not cd.data_series:
            payload["note"] = (
                "Figure uploaded from publication — run vision extraction to capture numbers, "
                "or rely on accompanying document text."
            )
        blocks.append(json.dumps(payload, ensure_ascii=False, indent=2))
    if not blocks:
        return ""
    return (
        "CHART ACCURACY: User-provided reference below is from manuscripts/publications or their data files. "
        "When you include charts in the infographic, align labels, values, intervals, and group names with "
        "this reference. Do not invent statistics beyond what the reference and source documents support. "
        "If reference values are incomplete, use labeled placeholder boxes as required by the chart rules.\n\n"
        "[USER CHART REFERENCE]:\n" + "\n\n".join(blocks)
    )


def publication_reference_preflight_issues(charts: list[dict[str, Any]]) -> list[str]:
    """Deterministic checks before generation for publication-fidelity mode."""
    issues: list[str] = []
    for c in charts:
        if c.get("verification_status") == "removed":
            continue
        title = str(c.get("title") or c.get("chart_id") or "chart")
        ct = str(c.get("chart_type") or "").lower()
        rows = c.get("data_series") or []
        if not isinstance(rows, list):
            rows = []

        is_hr_chart = any(x in ct for x in ("hazard", "forest", "hr"))
        if is_hr_chart and rows:
            has_n = any(str(r.get("n", "")).strip() for r in rows if isinstance(r, dict))
            has_events = any(str(r.get("events", "")).strip() for r in rows if isinstance(r, dict))
            has_hr = any(
                str(r.get("point_estimate", "")).strip()
                or str(r.get("hr", "")).strip()
                or str(r.get("value", "")).strip()
                for r in rows
                if isinstance(r, dict)
            )
            has_ci = any(
                (str(r.get("lower_ci", "")).strip() and str(r.get("upper_ci", "")).strip())
                or (
                    str(r.get("range_type", "")).strip().lower() == "ci"
                    and str(r.get("range_low", "")).strip()
                    and str(r.get("range_high", "")).strip()
                )
                for r in rows
                if isinstance(r, dict)
            )
            if not has_events:
                issues.append(f"{title}: HR-style chart is missing `Events` column in reference data.")
            if not has_n:
                issues.append(f"{title}: HR-style chart is missing `N` column in reference data.")
            if not has_hr:
                issues.append(f"{title}: HR-style chart is missing HR/point-estimate values.")
            if not has_ci:
                issues.append(f"{title}: HR-style chart is missing confidence interval bounds.")
    return issues


def refresh_chart_reference_hints(
    c: dict[str, Any],
    document_text: str,
    cross_check_documents: bool = False,
) -> None:
    """Optional hints: doc-vs-chart heuristics and IQR warnings — informational only (does not block generation)."""
    if c.get("verification_status") == "removed":
        return
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
    warns = median_iqr_data_sufficiency_warning(cd)
    c["extraction_warnings"] = list(
        dict.fromkeys((c.get("extraction_warnings") or []) + warns)
    )


def run_post_generation_chart_qa(
    client: OpenAI | AzureOpenAI,
    vision_model: str,
    image_bytes: bytes,
    chart_reference_block: str,
) -> str:
    """Vision review of the rendered infographic — with optional comparison to user chart reference."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    if chart_reference_block.strip():
        prompt = (
            "You are reviewing a FINISHED INFOGRAPHIC IMAGE.\n"
            "Compare it to the USER-PROVIDED chart reference (from publications / extracted data) below.\n"
            "Answer in plain bullet points:\n"
            "(1) Do numbers, labels, and intervals in the infographic match the reference where applicable?\n"
            "(2) Any invented, duplicated, or inconsistent statistics?\n"
            "(3) Missing labels or incorrect chart types?\n"
            "(4) Should the user regenerate? One sentence recommendation.\n"
            "Be concise.\n\n"
            "[USER CHART REFERENCE]:\n"
            + chart_reference_block[:12000]
        )
    else:
        prompt = (
            "You are reviewing a FINISHED INFOGRAPHIC IMAGE for data visualization quality.\n"
            "Identify charts, plots, tables, or numeric callouts. List key numbers you can read.\n"
            "Flag anything that looks inconsistent, like placeholder text, contradictory values, "
            "or suspicious precision. Give concise bullet points to decide if regeneration is needed.\n"
            "No separate reference was supplied — judge only what appears in the image."
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
    return _chat_completion_content_as_text(resp.choices[0].message.content)


def run_publication_fidelity_qa(
    client: OpenAI | AzureOpenAI,
    vision_model: str,
    image_bytes: bytes,
    chart_reference_block: str,
    expected_terms: list[str],
    expected_citation: str,
) -> dict[str, Any]:
    """Structured pass/fail QA for publication-fidelity mode."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    term_hint = ", ".join([t for t in expected_terms if t.strip()]) or "[none specified]"
    citation_hint = expected_citation.strip() or "[none specified]"
    prompt = (
        "Review the generated infographic for publication fidelity.\n"
        "Return ONLY valid JSON, no markdown, using this schema:\n"
        "{\n"
        '  "pass": boolean,\n'
        '  "critical_issues": [string],\n'
        '  "minor_issues": [string],\n'
        '  "checks": {\n'
        '    "required_columns_present": "pass|fail|unknown",\n'
        '    "axis_range_matches_reference": "pass|fail|unknown",\n'
        '    "terminology_consistency": "pass|fail|unknown",\n'
        '    "typo_scan": "pass|fail|unknown",\n'
        '    "citation_quality": "pass|fail|unknown",\n'
        '    "placeholder_tokens_absent": "pass|fail|unknown"\n'
        "  },\n"
        '  "recommendation": string\n'
        "}\n\n"
        "Mark `pass=false` if any critical issue exists.\n"
        "Detect placeholder tokens like xxx-xxx, TBD, [source], lorem.\n"
        f"Preferred terminology: {term_hint}\n"
        f"Expected citation if available: {citation_hint}\n\n"
        "[USER CHART REFERENCE]:\n"
        + (chart_reference_block[:12000] if chart_reference_block.strip() else "[none]")
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
        temperature=0.1,
        max_tokens=1200,
    )
    content = _chat_completion_content_as_text(resp.choices[0].message.content)
    parsed = parse_json_relaxed(content)
    if isinstance(parsed, dict) and "pass" in parsed:
        return parsed
    return {
        "pass": False,
        "critical_issues": ["Could not parse structured QA response."],
        "minor_issues": [],
        "checks": {
            "required_columns_present": "unknown",
            "axis_range_matches_reference": "unknown",
            "terminology_consistency": "unknown",
            "typo_scan": "unknown",
            "citation_quality": "unknown",
            "placeholder_tokens_absent": "unknown",
        },
        "recommendation": "Re-run QA or regenerate.",
    }
