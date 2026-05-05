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

    # Incident events
    m_evt = re.search(r"there were\s*([\d,]+)\s*incident hf hospitalization", lowered, flags=re.I)
    if m_evt:
        bullets.append(f"- Incident HF hospitalization/death events: {m_evt.group(1)}.")

    # Key effect-size signal (10-point LE8 with HR/CI)
    m_main = re.search(
        r"10-point higher le8 score was associated with\s*([0-9]+)%\s*lower risk[^.]{0,140}"
        r"hr[:\s]*([0-9.]+)[^0-9]+95%\s*ci[:\s]*([0-9.]+)\s*[-–]\s*([0-9.]+)",
        lowered,
        flags=re.I,
    )
    if m_main:
        bullets.append(
            "- Main result: 10-point higher LE8 linked to "
            f"{m_main.group(1)}% lower incident HF risk "
            f"(HR {m_main.group(2)}, 95% CI {m_main.group(3)}-{m_main.group(4)})."
        )

    # Diabetes subgroup signal
    m_dm = re.search(
        r"among those with diabetes[^.]{0,120}hr[:\s]*([0-9.]+)[^0-9]+95%\s*ci[:\s]*([0-9.]+)\s*[-–]\s*([0-9.]+)",
        lowered,
        flags=re.I,
    )
    if m_dm:
        bullets.append(
            "- Diabetes subgroup: similar protective association "
            f"(HR {m_dm.group(1)}, 95% CI {m_dm.group(2)}-{m_dm.group(3)})."
        )

    # Conclusion signal
    concl_match = re.search(r"(maintaining[^.]{0,160}risk of (?:incident )?hf[^.]{0,120}\.)", text, flags=re.I)
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


def build_infographic_prompt(
    style_id: str,
    user_context: str,
    cleaned_document_texts: list[str],
    audience_key: str,
    refinement_notes: str,
    logo_instructions_extra: str,
    chart_reference_block: str = "",
) -> str:
    style = STYLES.get(style_id, STYLES["uab-craft-handmade"])
    audience_key = audience_key if audience_key in AUDIENCE_GUIDANCE else "patient"
    aud = AUDIENCE_GUIDANCE[audience_key]

    doc_block = _build_trimmed_doc_block(cleaned_document_texts)
    doc_evidence_snapshot = _extract_doc_evidence_snapshot(cleaned_document_texts)
    chart_snapshot = _extract_chart_snapshot(chart_reference_block)

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
    primary_deliverable = (
        user_context.strip()
        if user_context.strip()
        else {
            "academic": (
                "Create an academic infographic that summarizes provided heart-failure evidence "
                "(Life's Essential 8 and incident HF) for a research audience. "
                "Prioritize publication-style clarity and exact chart values."
            ),
            "clinical": (
                "Create a clinician-facing infographic that highlights actionable LE8 and heart-failure findings, "
                "with evidence-focused numeric callouts."
            ),
            "patient": (
                "Create a patient-friendly infographic explaining how better LE8 health links to lower heart-failure risk, "
                "using plain language at about an 8th-grade reading level, defining complex terms in simple words, "
                "and emphasizing clear next steps."
            ),
            "community": (
                "Create a community outreach infographic showing that stronger cardiovascular health habits "
                "(Life's Essential 8) are linked to lower heart-failure risk across diabetes groups. "
                "Use a compelling story flow and include a clear 'Why this matters' section with practical community impact."
            ),
        }.get(audience_key, "Create a focused infographic using the provided evidence.")
    )
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
- If the source provides incomplete data: generate a placeholder box labeled "Exact values to be inserted from [source figure/table]" — do not estimate or fill in.
- All numeric labels, axis values, and legend entries must match the source EXACTLY.
- Do NOT invent whiskers, quartile boundaries, outlier points, or any visual element not explicitly in the source.

## Evidence Snapshot (PRIORITIZE THIS)
Use this compact evidence summary as the primary factual source for the infographic narrative and callouts:
{doc_evidence_snapshot}

Use this structured chart summary to preserve chart intent and numeric fidelity:
{chart_snapshot}

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
{doc_block if doc_block else "[No document uploads — rely on Evidence Snapshot and User-Provided Context below.]"}

## User-Provided Context and Custom Content
{user_block}

## Refinement Notes (if any)
{refinement_block}
"""
