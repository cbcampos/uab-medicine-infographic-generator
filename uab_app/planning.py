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
class ReferenceNotes:
    """Local reference guidance selected from UAB style metadata."""

    style_id: str = ""
    style_name: str = ""
    audience: str = ""
    source_type: str = ""
    visual_intent: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlannerBrief:
    """Content and structure plan. No aesthetic/style rewriting."""

    core_message: str = ""
    audience_objective: str = ""
    intended_sections: list[str] = field(default_factory=list)
    visual_hierarchy: list[str] = field(default_factory=list)
    claim_evidence_anchors: list[str] = field(default_factory=list)
    chart_or_diagram_candidates: list[str] = field(default_factory=list)
    avoid_items: list[str] = field(default_factory=list)
    citation_lock: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StylistBrief:
    """Style execution plan. Must preserve Planner content."""

    style_translation: list[str] = field(default_factory=list)
    layout_notes: list[str] = field(default_factory=list)
    typography_notes: list[str] = field(default_factory=list)
    iconography_notes: list[str] = field(default_factory=list)
    density_controls: list[str] = field(default_factory=list)
    critic_contract: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredVisualBrief:
    """PaperBanana-style planning and styling brief for one generation target."""

    retrieved_reference_notes: list[str] = field(default_factory=list)
    core_message: str = ""
    audience_objective: str = ""
    intended_sections: list[str] = field(default_factory=list)
    visual_hierarchy: list[str] = field(default_factory=list)
    claim_evidence_anchors: list[str] = field(default_factory=list)
    chart_or_diagram_candidates: list[str] = field(default_factory=list)
    avoid_items: list[str] = field(default_factory=list)
    style_translation: list[str] = field(default_factory=list)
    layout_notes: list[str] = field(default_factory=list)
    typography_notes: list[str] = field(default_factory=list)
    iconography_notes: list[str] = field(default_factory=list)
    density_controls: list[str] = field(default_factory=list)
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


def _planner_from_obj(obj: dict[str, Any]) -> PlannerBrief:
    return PlannerBrief(
        core_message=str(obj.get("core_message") or "").strip()[:260],
        audience_objective=str(obj.get("audience_objective") or "").strip()[:260],
        intended_sections=_clean_list(obj.get("intended_sections"), 6),
        visual_hierarchy=_clean_list(obj.get("visual_hierarchy"), 8),
        claim_evidence_anchors=_clean_list(obj.get("claim_evidence_anchors"), 8),
        chart_or_diagram_candidates=_clean_list(obj.get("chart_or_diagram_candidates"), 6),
        avoid_items=_clean_list(obj.get("avoid_items"), 8),
        citation_lock=str(obj.get("citation_lock") or "").strip()[:260],
    )


def _stylist_from_obj(obj: dict[str, Any]) -> StylistBrief:
    return StylistBrief(
        style_translation=_clean_list(obj.get("style_translation"), 8),
        layout_notes=_clean_list(obj.get("layout_notes"), 8),
        typography_notes=_clean_list(obj.get("typography_notes"), 6),
        iconography_notes=_clean_list(obj.get("iconography_notes"), 6),
        density_controls=_clean_list(obj.get("density_controls"), 6),
        critic_contract=_clean_list(obj.get("critic_contract"), 8),
    )


def _infer_visual_intent(inferred_profile: dict[str, Any], chart_reference_block: str) -> str:
    text = " ".join(
        [
            str(inferred_profile.get("source_type") or ""),
            str(inferred_profile.get("topic") or ""),
            str(inferred_profile.get("objective") or ""),
            " ".join(str(x) for x in inferred_profile.get("recommended_sections", [])[:6])
            if isinstance(inferred_profile.get("recommended_sections"), list)
            else "",
            chart_reference_block or "",
        ]
    ).lower()
    if chart_reference_block.strip() or any(k in text for k in ("hazard ratio", "odds ratio", "median", "iqr", "p value", "regression")):
        return "evidence summary with numeric callouts"
    if any(k in text for k in ("qualitative", "interview", "focus group", "barrier", "facilitator", "implementation")):
        return "barriers and supports map"
    if any(k in text for k in ("workflow", "process", "pathway", "intervention", "protocol")):
        return "process or care pathway"
    if any(k in text for k in ("community", "outreach", "resource", "trust", "access")):
        return "community context and action summary"
    return "evidence summary infographic"


def retrieve_reference_notes(
    *,
    style_id: str,
    audience: str,
    inferred_profile: dict[str, Any],
    chart_reference_block: str,
) -> ReferenceNotes:
    """Select concise local reference guidance from existing UAB style metadata."""
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    source_type = str(inferred_profile.get("source_type") or "unknown source").strip()
    visual_intent = _infer_visual_intent(inferred_profile, chart_reference_block)
    notes = [
        f"Use the local UAB style family: {style.get('name', style_id)}.",
        f"Style intent: {style.get('description', '').strip()}",
        f"Audience lens: {audience}.",
        f"Source type lens: {source_type}.",
        f"Visual intent: {visual_intent}.",
    ]
    if audience == "academic":
        notes.append("Favor evidence hierarchy, methods/results structure, and exact supported values.")
    elif audience == "clinical":
        notes.append("Favor decision relevance, population/outcome framing, and restrained care implications.")
        notes.append("Practice language should emphasize assessment and discussion, not directives, unless the source recommends action.")
    elif audience == "patient":
        notes.append("Favor plain-language takeaways, care-team conversation prompts, and low text density.")
        notes.append("Use one short 'study found' evidence note; avoid repeated beta/p-value parentheticals in patient panels.")
    elif audience == "community":
        notes.append("Favor systems/access/trust framing, barriers/supports, and collective context.")
        notes.append("Name specific barriers or resources only when explicitly supported by the source; otherwise keep labels generic.")
        notes.append("Use Community Interpretation instead of Community Actions/Resources unless the source includes interventions, resources, implementation, or explicit recommendations.")
        notes.append("Use Barriers/Supports only for sources that explicitly study barriers, facilitators, access, implementation, resources, or qualitative/community context; otherwise use Community Lens or Shared Takeaway.")
    if visual_intent == "evidence summary with numeric callouts":
        notes.append("Make exact numeric evidence prominent only where source-supported.")
    elif visual_intent == "barriers and supports map":
        notes.append("Use grouped barrier/support panels rather than fabricated quantitative charts.")
    elif visual_intent == "process or care pathway":
        notes.append("Use ordered steps and arrows only for source-supported sequences.")
    return ReferenceNotes(
        style_id=style_id,
        style_name=str(style.get("name", style_id)),
        audience=audience,
        source_type=source_type,
        visual_intent=visual_intent,
        notes=notes,
    )


def build_planner_brief(
    client: OpenAI | AzureOpenAI,
    chat_model: str,
    *,
    user_context: str,
    cleaned_document_texts: list[str],
    audience: str,
    inferred_profile: dict[str, Any],
    chart_reference_block: str,
    refinement_notes: str,
    reference_notes: ReferenceNotes,
) -> PlannerBrief:
    """Build the content/structure plan without style execution details."""
    audience_plan = AUDIENCE_SECTION_PLANS.get(audience, AUDIENCE_SECTION_PLANS["patient"])
    docs = "\n\n".join([d for d in cleaned_document_texts if str(d).strip()])[:12000]
    chart_ref = (chart_reference_block or "")[:5000]
    profile = json.dumps(inferred_profile or {}, ensure_ascii=False)[:6000]
    refs = "\n".join(f"- {n}" for n in reference_notes.notes)

    sys_msg = (
        "You are the Planner for a UAB Medicine scientific infographic generator. "
        "Return ONLY valid JSON. Decide content structure and evidence hierarchy. "
        "Do not write aesthetic style instructions. Do not invent facts, statistics, recommendations, "
        "resources, programs, outcomes, or source metadata."
    )
    user_msg = f"""
Create a source-grounded Planner brief for one infographic.

Audience: {audience}
Required audience sections or close variants: {", ".join(map(str, audience_plan.get("required_sections", [])))}
Required audience panel title: {audience_plan.get("required_panel_title", "")}
Avoid sections/framing: {", ".join(map(str, audience_plan.get("avoid_sections", [])))}

Retrieved reference notes:
{refs}

Return exactly this JSON object shape:
{{
  "core_message": "one concise sentence",
  "audience_objective": "one concise sentence",
  "intended_sections": ["4-6 section names"],
  "visual_hierarchy": ["TOP: ...", "LEFT: ...", "CENTER: ...", "RIGHT: ...", "BOTTOM: ..."],
  "claim_evidence_anchors": ["Claim: ... Evidence: exact source phrase/statistic ..."],
  "chart_or_diagram_candidates": ["supported visual idea ..."],
  "avoid_items": ["unsupported or audience-inappropriate item ..."],
  "citation_lock": "exact source footer fields to preserve, or empty string"
}}

Rules:
- Content only. Do not specify fonts, colors, textures, icon style, or decorations.
- Preserve exact numbers only if present below.
- Keep academic numeric evidence prominent when supported by exact values.
- Keep clinical/patient/community implications restrained unless source-supported.
- For clinical audiences, frame action panels as assessment, conversation, and source-informed planning rather than direct orders.
- For patient audiences, keep text especially sparse: 3-4 main panels, 2-3 bullets per panel, and at most one simple evidence note.
- For community audiences, do not name specific barriers, resources, services, transportation issues, cost issues, or messenger strategies unless they appear in the source excerpt or inferred profile.
- For community audiences, do not force action/resource sections; choose Community Interpretation when the source only supports meaning or context.
- For community audiences, use Barriers/Supports only when barriers, facilitators, access, implementation, resources, or qualitative/community context are explicit in the source; otherwise choose Community Lens or Shared Takeaway.
- Prefer "study suggests", "study shows", "may support", "can inform", or "is associated with" language for observational evidence.
- Preserve normalized citation fields exactly when present in the inferred source profile.

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
""".strip()

    resp = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=850,
    )
    raw = (resp.choices[0].message.content if resp.choices else "") or ""
    obj = _safe_json_object(raw)
    if not obj:
        raise ValueError("Planner did not return valid JSON.")
    brief = _planner_from_obj(obj)
    if not brief.core_message and not brief.visual_hierarchy and not brief.intended_sections:
        raise ValueError("Planner returned an empty brief.")
    return brief


def build_stylist_brief(
    client: OpenAI | AzureOpenAI,
    chat_model: str,
    *,
    audience: str,
    style_id: str,
    planner_brief: PlannerBrief,
    reference_notes: ReferenceNotes,
) -> StylistBrief:
    """Build style execution notes without changing the Planner's meaning."""
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    style_prompt = str(style.get("prompt") or "")[:4500]
    planner_json = json.dumps(planner_brief.to_dict(), ensure_ascii=False)[:6000]
    refs = "\n".join(f"- {n}" for n in reference_notes.notes)

    sys_msg = (
        "You are the Stylist for a UAB Medicine infographic generator. Return ONLY valid JSON. "
        "Translate an approved Planner brief into visual execution guidance. Do not change, add, "
        "remove, reinterpret, or soften claims, sections, numbers, recommendations, or citation fields."
    )
    user_msg = f"""
Create a style execution brief for the selected UAB style.

Audience: {audience}
Selected UAB style: {style.get("name", style_id)}

Retrieved reference notes:
{refs}

Approved Planner brief:
{planner_json}

Selected style rules excerpt:
{style_prompt}

Return exactly this JSON object shape:
{{
  "style_translation": ["style-specific instruction tied to the selected UAB style ..."],
  "layout_notes": ["layout instruction preserving Planner sections ..."],
  "typography_notes": ["readability/heading/body text instruction ..."],
  "iconography_notes": ["visual metaphor/icon instruction, source-grounded ..."],
  "density_controls": ["text density / spacing instruction ..."],
  "critic_contract": ["check the final image for ..."]
}}

Rules:
- Style only. Do not add claims, numbers, care advice, patient instructions, community resources, or recommendations.
- Preserve the Planner's intended sections, visual hierarchy, claim-evidence anchors, and citation lock.
- Use the selected UAB style; do not drift into a generic poster style.
- Keep text scannable and reduce paragraph blocks.
- For patient and community audiences, favor larger type, fewer words, and simpler section bodies over dense explanatory detail.
- Footer/logo rules are handled elsewhere; do not mention logo placement.
""".strip()

    resp = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=750,
    )
    raw = (resp.choices[0].message.content if resp.choices else "") or ""
    obj = _safe_json_object(raw)
    if not obj:
        raise ValueError("Stylist did not return valid JSON.")
    brief = _stylist_from_obj(obj)
    if not brief.style_translation and not brief.layout_notes and not brief.density_controls:
        raise ValueError("Stylist returned an empty brief.")
    return brief


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
    """Build a source-grounded visual brief using Retriever -> Planner -> Stylist."""
    reference_notes = retrieve_reference_notes(
        style_id=style_id,
        audience=audience,
        inferred_profile=inferred_profile,
        chart_reference_block=chart_reference_block,
    )
    planner = build_planner_brief(
        client,
        chat_model,
        user_context=user_context,
        cleaned_document_texts=cleaned_document_texts,
        audience=audience,
        inferred_profile=inferred_profile,
        chart_reference_block=chart_reference_block,
        refinement_notes=refinement_notes,
        reference_notes=reference_notes,
    )
    stylist = build_stylist_brief(
        client,
        chat_model,
        audience=audience,
        style_id=style_id,
        planner_brief=planner,
        reference_notes=reference_notes,
    )
    return StructuredVisualBrief(
        retrieved_reference_notes=reference_notes.notes,
        core_message=planner.core_message,
        audience_objective=planner.audience_objective,
        intended_sections=planner.intended_sections,
        visual_hierarchy=planner.visual_hierarchy,
        claim_evidence_anchors=planner.claim_evidence_anchors,
        chart_or_diagram_candidates=planner.chart_or_diagram_candidates,
        avoid_items=planner.avoid_items,
        citation_lock=planner.citation_lock,
        style_translation=stylist.style_translation,
        layout_notes=stylist.layout_notes,
        typography_notes=stylist.typography_notes,
        iconography_notes=stylist.iconography_notes,
        density_controls=stylist.density_controls,
        critic_contract=stylist.critic_contract,
    )


def format_retrieved_reference_notes(brief: StructuredVisualBrief | dict[str, Any] | None) -> str:
    data = brief.to_dict() if isinstance(brief, StructuredVisualBrief) else (brief or {})
    values = _clean_list(data.get("retrieved_reference_notes"), 10)
    return "\n".join(f"- {v}" for v in values)


def format_planner_brief(brief: StructuredVisualBrief | dict[str, Any] | None) -> str:
    data = brief.to_dict() if isinstance(brief, StructuredVisualBrief) else (brief or {})
    planner = {
        "core_message": data.get("core_message"),
        "audience_objective": data.get("audience_objective"),
        "intended_sections": data.get("intended_sections"),
        "visual_hierarchy": data.get("visual_hierarchy"),
        "claim_evidence_anchors": data.get("claim_evidence_anchors"),
        "chart_or_diagram_candidates": data.get("chart_or_diagram_candidates"),
        "avoid_items": data.get("avoid_items"),
        "citation_lock": data.get("citation_lock"),
    }
    return _format_brief_parts(planner, include_style=False)


def format_stylist_brief(brief: StructuredVisualBrief | dict[str, Any] | None) -> str:
    data = brief.to_dict() if isinstance(brief, StructuredVisualBrief) else (brief or {})
    stylist = {
        "style_translation": data.get("style_translation"),
        "layout_notes": data.get("layout_notes"),
        "typography_notes": data.get("typography_notes"),
        "iconography_notes": data.get("iconography_notes"),
        "density_controls": data.get("density_controls"),
        "critic_contract": data.get("critic_contract"),
    }
    return _format_brief_parts(stylist, include_content=False)


def _format_brief_parts(
    data: dict[str, Any],
    *,
    include_content: bool = True,
    include_style: bool = True,
) -> str:
    """Format a structured brief into a deterministic prompt block."""
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
    if include_content and core:
        parts.append(f"Core message: {core}")
    if include_content and objective:
        parts.append(f"Audience objective: {objective}")
    citation_lock = str(data.get("citation_lock") or "").strip()
    if include_content and citation_lock:
        parts.append(f"Citation lock: {citation_lock}")
    if include_content:
        for label, key in (
            ("Intended sections", "intended_sections"),
            ("Visual hierarchy", "visual_hierarchy"),
            ("Claim-evidence anchors", "claim_evidence_anchors"),
            ("Chart or diagram candidates", "chart_or_diagram_candidates"),
            ("Avoid", "avoid_items"),
        ):
            block = lines(label, key)
            if block:
                parts.extend(block)
    if include_style:
        for label, key in (
            ("Style translation", "style_translation"),
            ("Layout notes", "layout_notes"),
            ("Typography notes", "typography_notes"),
            ("Iconography notes", "iconography_notes"),
            ("Density controls", "density_controls"),
            ("Critic contract", "critic_contract"),
        ):
            block = lines(label, key)
            if block:
                parts.extend(block)
    return "\n".join(parts).strip()


def format_structured_brief_for_prompt(brief: StructuredVisualBrief | dict[str, Any] | None) -> str:
    """Format Retriever, Planner, and Stylist outputs for prompt injection."""
    if brief is None:
        return ""
    data = brief.to_dict() if isinstance(brief, StructuredVisualBrief) else brief
    refs = format_retrieved_reference_notes(data)
    planner = format_planner_brief(data)
    stylist = format_stylist_brief(data)
    parts: list[str] = []
    if refs:
        parts.append("Retrieved reference notes:\n" + refs)
    if planner:
        parts.append("Planner brief:\n" + planner)
    if stylist:
        parts.append("Stylist brief:\n" + stylist)
    return "\n\n".join(parts).strip()


def structured_brief_sha256(brief_block: str) -> str:
    """Hash a formatted brief for audit logging without storing the brief text."""
    if not (brief_block or "").strip():
        return ""
    return hashlib.sha256(brief_block.encode("utf-8")).hexdigest()
