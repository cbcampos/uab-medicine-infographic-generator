"""Assemble the full image-generation prompt."""

from __future__ import annotations

import re

from uab_app.constants import AUDIENCE_GUIDANCE
from uab_app.styles import STYLES


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _extract_doc_evidence_snapshot(cleaned_document_texts: list[str]) -> str:
    if not cleaned_document_texts:
        return "- No source document summary available."
    text = _compact_ws("\n".join(cleaned_document_texts))
    lowered = text.lower()
    bullets: list[str] = []

    # Cohort/sample size: prefer "final sample of X" when present.
    m_final = re.search(r"final sample of\s*([\d,]+)", lowered, flags=re.I)
    if m_final:
        bullets.append(f"- Final analytic cohort: n = {m_final.group(1)} participants.")
    else:
        n_match = re.search(r"\b(?:n\s*[=:\u00A0]?\s*)(\d{1,3}(?:,\d{3})+|\d{4,})\b", text, flags=re.I)
        if n_match:
            bullets.append(f"- Cohort size reported: n = {n_match.group(1)}.")

    # Follow-up duration
    m_fu = re.search(r"median follow[- ]?up[^0-9]{0,20}([0-9]+(?:\.[0-9]+)?)\s*years", lowered, flags=re.I)
    if m_fu:
        bullets.append(f"- Median follow-up: {m_fu.group(1)} years.")

    # Incident events (generic)
    m_evt = re.search(
        r"there were\s*([\d,]+)\s*(?:incident|new)\s+([a-z][a-z\s\-]{3,40}?)(?:,|\.|\s+including)",
        lowered,
        flags=re.I,
    )
    if m_evt:
        bullets.append(
            f"- Incident events reported: {m_evt.group(1)} ({_compact_ws(m_evt.group(2))})."
        )

    # Key effect-size signal (generic HR/RR/OR + CI)
    m_eff = re.search(
        r"\b(hr|hazard ratio|rr|relative risk|or|odds ratio)\b[^.]{0,80}?"
        r"([0-9]+(?:\.[0-9]+)?)\s*[,;)]?\s*[^.]{0,60}?"
        r"95%\s*ci[^0-9]{0,10}([0-9]+(?:\.[0-9]+)?)\s*[-–]\s*([0-9]+(?:\.[0-9]+)?)",
        lowered,
        flags=re.I,
    )
    if m_eff:
        metric = m_eff.group(1).upper().replace(" ", "")
        bullets.append(
            f"- Key effect size: {metric} {m_eff.group(2)} (95% CI {m_eff.group(3)}-{m_eff.group(4)})."
        )

    # Generic conclusion signal
    concl_match = re.search(
        r"((?:in conclusion|conclusion|our findings|we found|this study)[^.]{0,220}\.)",
        text,
        flags=re.I,
    )
    if concl_match:
        bullets.append(f"- {_compact_ws(concl_match.group(1))}")

    if not bullets:
        # Fallback: take first ~2 informative sentences
        sents = re.split(r"(?<=[.!?])\s+", text)
        picked = [s for s in sents if len(_compact_ws(s)) > 40][:2]
        if picked:
            bullets = [f"- {_compact_ws(s)}" for s in picked]
        else:
            bullets = [f"- {_compact_ws(text)[:240]}..."]
    return "\n".join(bullets[:6])


def _extract_chart_snapshot(chart_reference_block: str) -> str:
    block = chart_reference_block or ""
    if not block.strip():
        return "- No structured chart reference attached."
    snippets: list[str] = []
    titles = re.findall(r'"title"\s*:\s*"([^"]+)"', block)
    types = re.findall(r'"chart_type"\s*:\s*"([^"]+)"', block)
    if titles:
        snippets.append(f"- Chart titles: {', '.join(titles[:3])}.")
    if types:
        uniq_types = []
        for t in types:
            if t not in uniq_types:
                uniq_types.append(t)
        snippets.append(f"- Chart types requested: {', '.join(uniq_types[:4])}.")

    # Capture a few explicit HR/CI tuples if present
    pe = re.findall(r'"point_estimate"\s*:\s*([0-9]+(?:\.[0-9]+)?)', block)
    lo = re.findall(r'"lower_ci"\s*:\s*([0-9]+(?:\.[0-9]+)?)', block)
    hi = re.findall(r'"upper_ci"\s*:\s*([0-9]+(?:\.[0-9]+)?)', block)
    if pe and lo and hi:
        tuples = []
        for i in range(min(3, len(pe), len(lo), len(hi))):
            tuples.append(f"{pe[i]} ({lo[i]}-{hi[i]})")
        snippets.append(f"- Example HR (95% CI) values to preserve exactly: {', '.join(tuples)}.")

    # Capture a few bar values if present
    vals = re.findall(r'"value"\s*:\s*([0-9]+(?:\.[0-9]+)?)', block)
    if vals:
        snippets.append(f"- Example explicit metric values present: {', '.join(vals[:8])}.")

    return "\n".join(snippets[:6]) if snippets else "- Structured chart reference present; preserve all listed labels and values."


def _build_trimmed_doc_block(cleaned_document_texts: list[str]) -> str:
    if not cleaned_document_texts:
        return ""
    max_total = 7000
    max_per_doc = 2500
    chunks: list[str] = []
    used = 0
    for i, text in enumerate(cleaned_document_texts):
        t = (text or "").strip()
        if not t:
            continue
        t = t[:max_per_doc]
        remaining = max_total - used
        if remaining <= 0:
            break
        t = t[:remaining]
        chunks.append(f"### Document {i + 1}\n{t}")
        used += len(t)
    return "\n\n".join(chunks)


def _infer_topic_anchor(cleaned_document_texts: list[str], user_context: str) -> str:
    """Infer a short topic anchor from user context or first source lines."""
    if (user_context or "").strip():
        return _compact_ws(user_context)[:180]
    if cleaned_document_texts:
        first = _compact_ws(cleaned_document_texts[0])
        if first:
            # Prefer initial title-like segment before "Introduction"/"Methods".
            for marker in ("Introduction", "Methods", "BACKGROUND"):
                idx = first.lower().find(marker.lower())
                if idx > 40:
                    return first[:idx].strip()[:180]
            return first[:180]
    return "the provided source document"


def _extract_explicit_title(user_context: str) -> str:
    """Allow users to force a title via 'Title: ...' or 'Headline: ...'."""
    txt = (user_context or "").strip()
    if not txt:
        return ""
    m = re.search(r"(?im)^(?:title|headline)\s*:\s*(.+)$", txt)
    if not m:
        return ""
    t = _compact_ws(m.group(1))
    return t[:140]


def build_infographic_prompt(
    style_id: str,
    user_context: str,
    cleaned_document_texts: list[str],
    audience_key: str,
    refinement_notes: str,
    logo_instructions_extra: str,
    chart_reference_block: str = "",
    inferred_profile: dict[str, str | list[str]] | None = None,
) -> str:
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    audience_key = audience_key if audience_key in AUDIENCE_GUIDANCE else "patient"
    aud = AUDIENCE_GUIDANCE[audience_key]

    doc_block = _build_trimmed_doc_block(cleaned_document_texts)
    doc_evidence_snapshot = _extract_doc_evidence_snapshot(cleaned_document_texts)
    chart_snapshot = _extract_chart_snapshot(chart_reference_block)
    topic_anchor = _infer_topic_anchor(cleaned_document_texts, user_context)
    explicit_title = _extract_explicit_title(user_context)

    refinement_block = refinement_notes.strip() if refinement_notes.strip() else "[None]"

    ref_title = ""
    m = re.search(r'"title"\s*:\s*"([^"]+)"', chart_reference_block or "")
    if m:
        ref_title = m.group(1).strip()

    user_block = (
        user_context.strip()
        if user_context.strip()
        else "[No additional context provided — infer key themes only from approved source text above.]"
    )
    inferred_profile = inferred_profile or {}
    inferred_objective = str(inferred_profile.get("objective") or "").strip()
    inferred_topic = str(inferred_profile.get("topic") or "").strip()
    inferred_why = str(inferred_profile.get("why_matters") or "").strip()
    inferred_source_type = str(inferred_profile.get("source_type") or "").strip()
    inferred_key_points = inferred_profile.get("key_points")
    inferred_sections = inferred_profile.get("recommended_sections")
    inferred_chart_guidance = str(inferred_profile.get("chart_guidance") or "").strip()
    inferred_citation_title = str(inferred_profile.get("citation_title") or "").strip()
    inferred_citation_journal = str(inferred_profile.get("citation_journal") or "").strip()
    inferred_citation_year = str(inferred_profile.get("citation_year") or "").strip()
    inferred_citation_authors = str(inferred_profile.get("citation_authors_short") or "").strip()
    inferred_citation_footer = str(inferred_profile.get("citation_footer") or "").strip()
    inferred_implications = str(inferred_profile.get("implications_panel") or "").strip()
    inferred_claim_pairs = inferred_profile.get("claim_evidence_pairs")
    inferred_non_numeric = bool(inferred_profile.get("non_numeric_mode"))
    claim_anchor_block = (
        ("  - " + "\n  - ".join([str(x) for x in inferred_claim_pairs][:6]))
        if isinstance(inferred_claim_pairs, list) and inferred_claim_pairs
        else "  - [No claim-evidence pairs inferred; only use directly sourced claims.]"
    )

    user_goal = _compact_ws(user_context)
    if user_goal:
        if explicit_title:
            primary_deliverable = (
                f"Create an infographic aligned to this objective: {user_goal}. "
                f'Use this exact title text: "{explicit_title}".'
            )
        else:
            primary_deliverable = (
                f"Create an infographic aligned to this objective: {user_goal}. "
                "Treat this as intent and direction, not as literal on-image title text."
            )
    else:
        primary_deliverable = (inferred_objective if inferred_objective else {
            "academic": (
                "Create an academic infographic that summarizes the key evidence from "
                f"{topic_anchor} for a research audience. "
                "Prioritize publication-style clarity and factual precision."
            ),
            "clinical": (
                "Create a clinician-facing infographic highlighting actionable findings from "
                f"{topic_anchor}, with evidence-focused numeric callouts."
            ),
            "patient": (
                "Create a patient-friendly infographic explaining the key findings from "
                f"{topic_anchor}, "
                "using plain language at about an 8th-grade reading level, defining complex terms in simple words, "
                "and emphasizing clear next steps."
            ),
            "community": (
                "Create a community outreach infographic communicating why the findings from "
                f"{topic_anchor} matter for everyday life. "
                "Use a compelling story flow and include a clear 'Why this matters' section with practical community impact."
            ),
        }.get(audience_key, "Create a focused infographic using the provided evidence."))
    if ref_title:
        primary_deliverable += f" Use '{ref_title}' as a central section heading."

    return f"""## Image Specifications
- Type: Infographic
- Layout: Horizontal layout (16:9 aspect ratio)
- Style: {style["name"]}
- Audience: {audience_key}

## Primary Deliverable (MUST FOLLOW)
- Build exactly this deliverable: {primary_deliverable}
- Keep the infographic focused on this deliverable; do not substitute unrelated topics or scenes.
- Do NOT produce grammar lessons, writing tips, active/passive voice comparisons, or language-learning posters.
- Do NOT output generic classroom templates unrelated to cardiovascular research content.

## Composition and Layout (follow this structure)
- TOP REGION: Title/headline centered, large, bold — reserve for key message
- LEFT / CENTER: Primary content area — key points, icons, illustrations, data callouts
- RIGHT / LOWER: Supporting content, secondary data, evidence summaries
- BOTTOM: Clean white band for logo placement (bottom-right or bottom-center)
- Leave generous negative space — do NOT crowd the layout
- When specific text must appear in the image, write it EXACTLY as it should appear (use ALL CAPS for headings, spell out all technical/medical terms letter by letter)

## Title Generation Rules (STRICT)
- Generate a concise, audience-appropriate infographic title from the source evidence and objective.
- Do NOT copy the entire user context/objective sentence as the title.
- If user context includes `Title:` or `Headline:`, use that exact value as the title.
- Otherwise, create a polished title (about 6-12 words) that reflects the actual project/topic.

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
- Do NOT render the strings "UAB", "UAB Medicine", or any brand wordmark anywhere in the image.
- Reserve a bottom-right logo-safe region as a plain white rectangle with NO text, NO icons, and NO artwork (this area is intentionally left blank for app-side logo compositing).
- Treat the logo-safe region as protected negative space: no background texture, chalk dust, gradients, borders, or decorative elements.
{logo_instructions_extra}

## Chart/Data Accuracy Rules (STRICT — no exceptions)
- Do NOT create box plots, forest plots, whisker elements, CI lines, or any chart with inferred data.
- If the source provides only median + IQR: render a DOT-AND-RANGE chart with exact median dots and IQR range bars — nothing else.
- If the source provides only a hazard ratio + CI: render a single EVIDENCE CALLOUT CARD with that exact HR and CI.
- Do NOT display HRs by outcome subtype (e.g. HFpEF vs HFrEF) unless those exact values are explicitly provided.
- If the source provides incomplete data: generate a placeholder box labeled "Exact values to be inserted from [source figure/table]" — do not estimate or fill in.
- All numeric labels, axis values, and legend entries must match the source EXACTLY.
- Do NOT invent whiskers, quartile boundaries, outlier points, or any visual element not explicitly in the source.
- Do NOT introduce medical outcomes, disease terms, or metrics that are not present in the source documents or user context.

## Evidence Snapshot (PRIORITIZE THIS)
Use this compact evidence summary as the primary factual source for the infographic narrative and callouts:
{doc_evidence_snapshot}

Use this structured chart summary to preserve chart intent and numeric fidelity:
{chart_snapshot}

## Inferred Source Profile (AUTO-FILLED)
- Inferred topic: {inferred_topic or "[not inferred]"}
- Inferred source type: {inferred_source_type or "[not inferred]"}
- Why this matters: {inferred_why or "[not inferred]"}
- Key points to prioritize: {", ".join([str(x) for x in inferred_key_points][:5]) if isinstance(inferred_key_points, list) and inferred_key_points else "[not inferred]"}
- Suggested sections: {", ".join([str(x) for x in inferred_sections][:6]) if isinstance(inferred_sections, list) and inferred_sections else "[not inferred]"}
- Chart guidance: {inferred_chart_guidance or "[not inferred]"}
- Non-numeric mode: {"ON" if inferred_non_numeric else "OFF"}
- Normalized citation fields: title="{inferred_citation_title or '[not inferred]'}", journal="{inferred_citation_journal or '[not inferred]'}", year="{inferred_citation_year or '[not inferred]'}", authors="{inferred_citation_authors or '[not inferred]'}"

## User-provided chart reference (from publications / uploads)
{chart_reference_block if chart_reference_block.strip() else "[No chart figures or data files attached — use only Source Documents and user context for any numbers; do not invent statistics.]"}

## Audience-Specific Guidelines
{aud}

## Content Requirements
- Include simple visual elements, icons, or illustrations to enhance visual appeal
- If content involves sensitive or copyrighted figures, replace with visually similar alternatives
- Keep information concise, highlight keywords and core concepts
- Use whitespace effectively to emphasize key points
- Output in the same language as the provided content
- TEXT IN IMAGE: Write any specific text EXACTLY as it should appear. Use ALL CAPS for titles/headlines. Do not approximate — if exact wording matters, quote it verbatim.

## HARD CONSTRAINT: Source-Grounded Claims Only
- Every major claim in the infographic MUST map to source evidence.
- For each major claim, include a concise evidence anchor in text (statistic, quote phrase, or cited finding).
- If evidence for a claim is not present in source/user context, do NOT include the claim.
- Preferred claim-evidence anchors:
{claim_anchor_block}

## HARD CONSTRAINT: Required Implications Panel
- Include at least one dedicated panel titled exactly: "What This Means for Practice/Research".
- Panel content must be tied directly to source wording and remain concise (2-4 bullets).
- Use this inferred guidance if helpful: {inferred_implications or "[not inferred]"}

## HARD CONSTRAINT: Citation Normalization Footer
- Include one citation footer block using this exact normalized format:
  "Source: {inferred_citation_authors or '[Authors]'} ({inferred_citation_year or '[Year]'}). {inferred_citation_title or '[Title]'}. {inferred_citation_journal or '[Journal]'}."
- If any field is missing, keep the placeholder label instead of inventing details.

## HARD CONSTRAINT: Non-Numeric Source Handling
- If NON_NUMERIC_MODE is ON, do NOT generate quantitative charts, effect-size plots, CI bars, p-values, or fabricated numbers.
- In NON_NUMERIC_MODE, prefer concept maps, framework diagrams, process flows, and labeled relationship panels.
- Current mode: {"NON_NUMERIC_MODE=ON" if inferred_non_numeric else "NON_NUMERIC_MODE=OFF"}

## Slides, Diagrams & Charts — Artifact Spec Format (STRICT)
- Treat this as an artifact spec, not an illustration request. Name the exact deliverable, define the canvas and visual hierarchy, and provide the real text or data.
- For slides, charts, or diagram-heavy assets: include ALL numbers, labels, axes, and footnotes DIRECTLY in the prompt. Do not assume the model will fill in realistic-looking data — it must be explicit.
- Use quality="high" for any image that contains small text, legends, axes, footnotes, or data labels.
- Use landscape orientation (1792x1024) for deck-style outputs.
- Design requirements: readable typography, polished spacing, clear data hierarchy, professional visual language.
- AVOID: clip art, stock photography, gradients, shadows, decorative clutter, generic or overdesigned elements.
- Structure chart/diagram content with exact values:
 * BAR CHART: Label each bar exactly (e.g. "Group A: 43%", "Group B: 27%"), include axis labels and units.
 * EVIDENCE CALLOUT: Quote an exact finding from the attached source with its exact numbers/statistics.
 * DOT-AND-RANGE: Show exact median and IQR values as labeled markers.
 * WORKFLOW DIAGRAM: List each step in sequence with exact wording.
 * If data is incomplete: use a placeholder box labeled "Exact values to be inserted from [source figure/table]" — never estimate.

## Instructions
Use the image generation model to create the illustration based on the provided input.

## Source Documents (Additional Context)
{doc_block if doc_block else "[No document uploads — rely on Evidence Snapshot and User-Provided Context below.]"}

## User-Provided Context and Custom Content
{user_block}

## Refinement Notes (if any)
{refinement_block}
"""
