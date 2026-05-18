"""Structured visual planning for infographic generation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import AzureOpenAI, OpenAI

from uab_app.constants import AUDIENCE_SECTION_PLANS
from uab_app.styles import STYLES


@dataclass
class StructuredVisualBrief:
    """PaperBanana-style planning and styling brief for one generation target."""

    core_message: str = ""
    audience_objective: str = ""
    intended_sections: list[str] = field(default_factory=list)
    visual_hierarchy: list[str] = field(default_factory=list)
    claim_evidence_anchors: list[str] = field(default_factory=list)
    chart_or_diagram_candidates: list[str] = field(default_factory=list)
    avoid_items: list[str] = field(default_factory=list)
    style_translation: list[str] = field(default_factory=list)
    citation_lock: str = ""
    critic_contract: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if not t:
        return {}
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", t)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            pass
    m = re.search(r"(\{[\s\S]*\})", t)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(1))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clean_list(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        txt = re.sub(r"\s+", " ", str(item or "")).strip()
        if txt:
            out.append(txt[:220])
    return out[:limit]


def _brief_from_obj(obj: dict[str, Any]) -> StructuredVisualBrief:
    return StructuredVisualBrief(
        core_message=str(obj.get("core_message") or "").strip()[:260],
        audience_objective=str(obj.get("audience_objective") or "").strip()[:260],
        intended_sections=_clean_list(obj.get("intended_sections"), 6),
        visual_hierarchy=_clean_list(obj.get("visual_hierarchy"), 8),
        claim_evidence_anchors=_clean_list(obj.get("claim_evidence_anchors"), 8),
        chart_or_diagram_candidates=_clean_list(obj.get("chart_or_diagram_candidates"), 6),
        avoid_items=_clean_list(obj.get("avoid_items"), 8),
        style_translation=_clean_list(obj.get("style_translation"), 8),
        citation_lock=str(obj.get("citation_lock") or "").strip()[:260],
        critic_contract=_clean_list(obj.get("critic_contract"), 8),
    )


def build_structured_visual_brief(
    client: OpenAI | AzureOpenAI,
    chat_model: str,
    *,
    user_context: str,
    cleaned_document_texts: list[str],
    audience: str,
    style_id: str,
    inferred_profile: dict[str, Any],
    chart_reference_block: str,
    refinement_notes: str,
) -> StructuredVisualBrief:
    """Build a source-grounded visual brief using the existing chat model."""
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    audience_plan = AUDIENCE_SECTION_PLANS.get(audience, AUDIENCE_SECTION_PLANS["patient"])
    docs = "\n\n".join([d for d in cleaned_document_texts if str(d).strip()])[:12000]
    chart_ref = (chart_reference_block or "")[:5000]
    profile = json.dumps(inferred_profile or {}, ensure_ascii=False)[:6000]
    style_prompt = str(style.get("prompt") or "")[:3500]

    sys_msg = (
        "You are the Planner and Stylist for a UAB Medicine scientific infographic generator. "
        "Return ONLY valid JSON. Do not add markdown. Do not invent facts, statistics, source details, "
        "programs, resources, or outcomes. Your job is to create a concise visual brief for a downstream "
        "image prompt, not to write the final infographic."
    )
    user_msg = f"""
Create a PaperBanana-style structured visual brief for one infographic.

Audience: {audience}
Selected UAB style: {style.get("name", style_id)}
Required audience sections or close variants: {", ".join(map(str, audience_plan.get("required_sections", [])))}
Required audience panel title: {audience_plan.get("required_panel_title", "")}
Avoid sections/framing: {", ".join(map(str, audience_plan.get("avoid_sections", [])))}

Return exactly this JSON object shape:
{{
  "core_message": "one concise sentence",
  "audience_objective": "one concise sentence",
  "intended_sections": ["4-6 section names"],
  "visual_hierarchy": ["TOP: ...", "LEFT: ...", "CENTER: ...", "RIGHT: ...", "BOTTOM: ..."],
  "claim_evidence_anchors": ["Claim: ... Evidence: exact source phrase/statistic ..."],
  "chart_or_diagram_candidates": ["supported visual idea ..."],
  "avoid_items": ["unsupported or audience-inappropriate item ..."],
  "style_translation": ["style-specific instruction tied to the selected UAB style ..."],
  "citation_lock": "exact source footer fields to preserve, or empty string",
  "critic_contract": ["check the final image for ..."]
}}

Rules:
- Keep every list concise; no paragraphs.
- Preserve exact numbers only if present below.
- Prefer scannability: 4-6 panels, short bullets, strong hierarchy.
- Style translation must use the selected UAB style, not a generic academic poster style.
- Preserve normalized citation fields exactly when present in the inferred source profile; never rename authors, title, journal, or year.
- Keep academic numeric evidence prominent when supported by exact source values.
- For clinical/patient/community audiences, frame implications as source-grounded interpretation. Do not introduce new care recommendations, resource recommendations, intervention recommendations, or behavior instructions unless the source explicitly supports them.
- Prefer "study suggests", "study shows", "may support", or "can inform" language over direct recommendations when evidence is observational or when the source does not prescribe action.
- Critic contract should be actionable for later vision review.
- Footer/logo rules are handled elsewhere; do not mention logo placement.

Inferred source profile:
{profile}

User context:
{(user_context or "[none]")[:4000]}

Cleaned source excerpt:
{docs or "[none]"}

Chart reference excerpt:
{chart_ref or "[none]"}

Refinement notes:
{(refinement_notes or "[none]")[:2500]}

Selected style rules excerpt:
{style_prompt}
""".strip()

    resp = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.15,
        max_tokens=1000,
    )
    raw = (resp.choices[0].message.content if resp.choices else "") or ""
    obj = _safe_json_object(raw)
    if not obj:
        raise ValueError("Structured planning did not return valid JSON.")
    brief = _brief_from_obj(obj)
    if not brief.core_message and not brief.visual_hierarchy and not brief.intended_sections:
        raise ValueError("Structured planning returned an empty brief.")
    return brief


def format_structured_brief_for_prompt(brief: StructuredVisualBrief | dict[str, Any] | None) -> str:
    """Format a structured brief into a deterministic prompt block."""
    if brief is None:
        return ""
    data = brief.to_dict() if isinstance(brief, StructuredVisualBrief) else brief
    if not isinstance(data, dict) or not data:
        return ""

    def lines(label: str, key: str) -> list[str]:
        values = _clean_list(data.get(key), 8)
        if not values:
            return []
        return [f"{label}:"] + [f"- {v}" for v in values]

    parts: list[str] = []
    core = str(data.get("core_message") or "").strip()
    objective = str(data.get("audience_objective") or "").strip()
    if core:
        parts.append(f"Core message: {core}")
    if objective:
        parts.append(f"Audience objective: {objective}")
    citation_lock = str(data.get("citation_lock") or "").strip()
    if citation_lock:
        parts.append(f"Citation lock: {citation_lock}")
    for label, key in (
        ("Intended sections", "intended_sections"),
        ("Visual hierarchy", "visual_hierarchy"),
        ("Claim-evidence anchors", "claim_evidence_anchors"),
        ("Chart or diagram candidates", "chart_or_diagram_candidates"),
        ("Avoid", "avoid_items"),
        ("Style translation", "style_translation"),
        ("Critic contract", "critic_contract"),
    ):
        block = lines(label, key)
        if block:
            parts.extend(block)
    return "\n".join(parts).strip()


def structured_brief_sha256(brief_block: str) -> str:
    """Hash a formatted brief for audit logging without storing the brief text."""
    if not (brief_block or "").strip():
        return ""
    return hashlib.sha256(brief_block.encode("utf-8")).hexdigest()
