"""Assemble the full image-generation prompt."""

from __future__ import annotations

from uab_app.constants import AUDIENCE_GUIDANCE
from uab_app.styles import STYLES


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
- If the source provides incomplete data: generate a placeholder box labeled "Exact values to be inserted from [source figure/table]" — do not estimate or fill in.
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
