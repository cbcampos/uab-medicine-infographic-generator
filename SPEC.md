# UAB Medicine Infographic Generator — SPEC.md
## Version: Hardening v1 (2026-05-04)

---

## Overview

A Streamlit web app that generates UAB Medicine-branded infographics using GPT Image 2.0 (OpenAI direct) or GPT Image 2.0 (Azure OpenAI). Built on the notex (smallnest/notex) infographic prompt architecture.

**Location:** `~/infographic-app/` on Chris's Mac  
**Running at:** http://localhost:8502  
**Tech stack:** Python 3.9 · Streamlit 1.57 · openai 2.33 · python-docx · PyPDF2 · OpenAI Python client

---

## Features (Current + Planned)

### ✅ Implemented

**1. Provider Toggle**
- Sidebar radio: 🟢 OpenAI (GPT Image 2) vs 🔷 Azure OpenAI (GPT Image 2)
- OpenAI: drop in `sk-...` API key directly
- Azure: API key + endpoint + API version + model deployment name

**2. 10 UAB Medicine-branded visual styles**
All prefixed with `uab-`:
- `uab-craft-handmade` — Hand-drawn paper craft, warm/organic
- `uab-watercolor` — Storybook watercolor, editorial
- `uab-academia` — Aged academia, vintage scientific
- `uab-bold-graphic` — Comic/halftone, high energy
- `uab-corporate` — Corporate Memphis, flat vector
- `uab-technical` — Technical schematic, clinical
- `uab-chalkboard` — Chalkboard dark background
- `uab-kawaii` — Japanese cute, patient-friendly
- `uab-claymation` — 3D clay figure aesthetic
- `uab-cyberpunk-neon` — Neon glow, futuristic

Brand colors: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C), Healing Teal (#08948E)  
Athletic Gold (#E87722) is NOT part of the Medicine palette.

**3. Document Upload & Parsing**
- Accepts PDF, DOCX, TXT via Streamlit file_uploader
- Extracted text is prepended to the prompt as source context
- Preview of extracted content shown before generation

**4. Style Comparison (3-way)**
- User selects 3 styles from the dropdown
- App generates the same prompt in all 3 styles simultaneously
- Results shown side-by-side for comparison
- User picks their favorite to download/refine

**5. Image Refinement Loop**
- After initial generation, user can provide feedback/refinement direction
- Second generation pass uses the same style + original prompt + refinement notes
- Can iterate until satisfied

**6. Prompt Preview**
- Collapsible "View full prompt" section before submission
- User can audit exactly what will be sent to the API

**7. Generation Status Progress**
- Clear stage indicators: "Building prompt → Cleaning document text → Submitting to API → Fetching image → Displaying"
- Progress bar instead of a simple spinner

**8. PHI Flag / No-PHI Checkbox**
- Mandatory checkbox: "I confirm this content does NOT contain protected health information (PHI)"
- Warning banner when documents are uploaded
- Disabled generation until checkbox is checked

**9. LLM Document Cleanup**
- Before being added to the prompt, extracted document text is sent through a lightweight LLM (same provider) for sanitization/cleaning:
  - Remove control characters, weird whitespace
  - Normalize OCR artifacts (common in scanned PDFs)
  - Remove any accidentally included PHI-adjacent content (names that might be patients, dates of birth, etc.)
  - Preserve structure and key facts
- This is done via a separate, fast LLM call before the image generation prompt is assembled

### 🔲 Planned / In Progress

**10. Audience Selector**
- Dropdown/radio with options:
  - `academic` — Academic journal/presentation (formal, data-dense, minimal illustration)
  - `clinical` — Healthcare providers (clinical tone, evidence-based)
  - `patient` — Patients/lay audience (plain language, warm, accessible)
  - `community` — Community outreach (culturally relevant, action-oriented, warm visuals)
- Audience is injected into the prompt to guide tone, detail level, vocabulary, and visual density

**11. Input Sanitization**
- Strip control characters from user text and document content
- Prevent prompt injection (detect and flag attempts to override system instructions)
- Max file size enforced (10MB per file)
- Strict file type enforcement

**12. UAB Medicine Logo (Hardened — use only the approved file)**
- Logo file: `projects/ai-summit-table-talk/uab-medicine-logo.jpg`
- Use ONLY the approved UAB Medicine logo file as the logo source.
- Do NOT redraw, reinterpret, recreate, stylize, distort, watercolor, trace, recolor, crop, stretch, or modify the logo.
- Do NOT generate any alternate UAB, UAB Medicine, university, hospital, school, or department logo.
- Do NOT invent seals, icons, shield marks, taglines, or substitute brand marks.
- The approved logo must appear exactly as provided, with correct proportions and colors.
- Place the logo in a clean white footer or corner area with adequate clear space.
- Keep the logo separate from watercolor effects, textures, shadows, illustrations, or background patterns.
- If the logo cannot be reproduced exactly from the attached source, leave a blank white logo placement box labeled: "Approved UAB Medicine logo placement."

**13. Chart/Data Accuracy Enforcement (CRITICAL)**
- Do NOT fabricate, estimate, or hallucinate any data values, percentages, statistics, or chart data.
- Only depict data that is explicitly provided in the source content or user-provided context.
- If no specific data is provided, represent the concept generically without specific numbers.
- All labels, axis values, and legend entries must match the source data exactly.
- If provided data is insufficient for a complete chart, generate a placeholder with "Data to be inserted" label.
- The app must include this as an explicit block in the prompt template — not optional.

**14. Input Sanitization**
- Strip control characters from user text and document content
- Prevent prompt injection (detect and flag attempts to override system instructions)
- Max file size enforced (10MB per file)
- Strict file type enforcement

**15. LLM Document Cleanup**
- Before being added to the prompt, extracted document text passes through a lightweight LLM cleanup:
  - Remove control characters, weird whitespace
  - Normalize OCR artifacts (common in scanned PDFs)
  - Flag potential PHI-adjacent content (names, DOBs, MRNs) for removal
  - Preserve structure and key facts
- Done via a separate, fast LLM call before the image generation prompt is assembled
- If no API available, fall back to regex-based sanitization

**16. Retry Logic + Timeout**
- 3 automatic retries with 5s exponential backoff on image generation failure
- Hard timeout at 90s
- Clear error messaging (no stack traces exposed to users)

**17. Audit Logging**
- Log: timestamp, session ID, provider, style, audience type, generation success/failure, latency
- Do NOT log full prompts (security)
- JSON structured logging for observability

**18. Style Comparison Results**
- Store last comparison results in `st.session_state`
- Allow user to download any of the 3 results individually

---

## Prompt Template Structure

```
## Image Specifications
- Type: Infographic
- Layout: Horizontal layout (16:9 aspect ratio)
- Style: {selected_style_name}
- Audience: {selected_audience}  ← new

## Style Guidelines
{style_prompt}

## UAB Medicine Brand Compliance
- Primary colors: UAB Green (#1A5632), UAB Gold (#FFC72C)
- Accent: UAB Medicine Navy (#003A5C)
- Secondary/Patient color: Healing Teal (#08948E)
- White background (#FFFFFF) or Light Gray (#F5F5F5) preferred
- Do NOT use Athletic Gold (#E87722) — not part of Medicine palette
- Maintain high contrast for accessibility (WCAG AA minimum)

## UAB Medicine Logo Rules  ← new
- Use ONLY the approved UAB Medicine logo file as the logo source.
- Do NOT redraw, reinterpret, recreate, stylize, distort, watercolor, trace, recolor, crop, stretch, or modify the logo.
- Do NOT generate any alternate UAB, UAB Medicine, university, hospital, school, or department logo.
- Do NOT invent seals, icons, shield marks, taglines, or substitute brand marks.
- The approved logo must appear exactly as provided, with correct proportions and colors.
- Place the logo in a clean white footer or corner area with adequate clear space.
- Keep the logo separate from watercolor effects, textures, shadows, illustrations, or background patterns.
- If the logo cannot be reproduced exactly from the attached source, leave a blank white logo placement box labeled: "Approved UAB Medicine logo placement."

## Chart/Data Accuracy Rules  ← new
- Do NOT fabricate, estimate, or hallucinate any data values, percentages, statistics, or chart data.
- Only depict data that is explicitly provided in the source content or user-provided context.
- If no specific data is provided, represent the concept generically without specific numbers.
- All labels, axis values, and legend entries must match the source data exactly.
- If the provided data is insufficient for a complete chart, generate a placeholder chart with the label "Data to be inserted" in the appropriate field.

## Audience-Specific Guidelines  ← new
{audience_guidance}

## Content Requirements
- Include simple visual elements, icons, or illustrations to enhance visual appeal
- If content involves sensitive or copyrighted figures, replace with visually similar alternatives
- Keep information concise, highlight keywords and core concepts
- Use whitespace effectively to emphasize key points
- Output in the same language as the provided content

## Instructions
Use the image generation model to create the illustration based on the provided input.

## Source Documents (Additional Context)
{cleaned_document_texts}

## User-Provided Context and Custom Content
{user_context}

## Refinement Notes (if any)
{refinement_notes}
```

---

## Audience Guidance by Setting

### `academic`
- Formal tone, data-dense
- Includes citation placeholders where appropriate
- Minimal decorative illustration; more diagram/chart focused
- Professional color palette, restrained use of UAB Gold
- Written for peers (researchers, faculty, clinicians)

### `clinical`
- Clinical tone, evidence-based framing
- Clear clinical endpoints and outcomes data
- Professional but slightly more visual than academic
- Uses clinical terminology appropriately
- Suitable for HCP education materials

### `patient`
- Plain language, warm, non-technical
- Action-oriented messaging ("what you can do")
- Encouraging tone, positive framing
- Friendly illustrations, accessible iconography
- Large readable text, minimal jargon

### `community`
- Culturally relevant, warm, community-centered
- Action-oriented with clear calls to action
- Celebratory of community assets and strengths
- Accessible language, relatable visuals
- Suitable for flyers, community presentations, health fairs

---

## App Architecture

### File Structure
```
~/infographic-app/
├── infographic_app.py    ← main app (all-in-one)
├── .venv/                ← Python venv (streamlit + openai + docx + pypdf2)
├── requirements.txt
└── .env.example
```

### Key Functions
- `make_client()` — factory for OpenAI vs Azure client
- `generate_image()` — calls image gen API with provider routing
- `extract_document_text()` — routes PDF/DOCX/TXT to correct parser
- `build_infographic_prompt()` — assembles full prompt from style + docs + user text + audience
- `clean_document_text()` — LLM cleanup pass on extracted text ← TODO
- `sanitize_input()` — strip control chars, detect prompt injection ← TODO
- `generate_with_retry()` — 3-attempt retry with backoff ← TODO

### Session State Keys
- `comparison_results` — list of 3 image URLs from style comparison
- `last_prompt` — last built prompt (for preview/debugging)
- `last_image_url` — most recent generated image
- `generation_history` — list of past generations with thumbnails

---

## Critical Constraints

1. **Athletic Gold (#E87722) is NOT UAB Medicine** — must not appear in any generated output
2. **No logo generation** — only use the approved logo file as-is
3. **No chart hallucinations** — data must come only from user-provided content
4. **No PHI** — uploaded documents must not contain protected health information
5. **API keys are per-user** — entered in sidebar or via env vars; never logged

---

## Files

- App: `projects/ai-summit-table-talk/infographic_app.py` (development source)
- Mac copy: `~/infographic-app/infographic_app.py` (live running version)
- Requirements: `projects/ai-summit-table-talk/requirements.txt`
- Logo file: `/home/ccampos/.openclaw/media/inbound/cbe42d6c-530a-4a60-9dce-00e7648c8043.jpg`

---

*Last updated: 2026-05-04 by Forge*
