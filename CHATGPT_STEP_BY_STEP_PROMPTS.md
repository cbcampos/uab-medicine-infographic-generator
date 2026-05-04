# ChatGPT Manual Workflow Prompts (Step-by-Step)

Use these prompts in sequence to replicate the app workflow manually in ChatGPT without an API key.

---

## How to use this file

- Replace text inside `[brackets]` with your specifics.
- Keep each step in a separate chat turn (or separate chat) for cleaner outputs.
- When a step says "paste source text", include only de-identified, non-PHI content.

---

## Step 1) Clean and structure source content

**Goal:** Convert raw notes/document text into clean, concise, infographic-ready input.

```text
You are preparing source material for an infographic.

Task:
1) Clean and de-duplicate the text I paste.
2) Remove boilerplate, repeated disclaimers, and irrelevant paragraphs.
3) Preserve all numeric facts exactly as written.
4) Return:
   - Key message (1-2 sentences)
   - 5-10 core facts (bullet list)
   - Data points (bullet list with units and context)
   - Missing details / ambiguities to clarify

Constraints:
- Do not invent facts.
- Do not change numeric values.
- Keep output concise and scannable.

Source text:
[PASTE SOURCE TEXT]
```

---

## Step 2) Extract chart/table-ready data

**Goal:** Turn findings into structured chart rows.

```text
Convert the information below into chart-ready data rows.

Return JSON with this shape:
{
  "charts": [
    {
      "title": "",
      "chart_type": "",
      "x_axis": "",
      "y_axis": "",
      "data_series": [
        {"group": "", "label": "", "value": "", "unit": ""}
      ],
      "source_citation": "",
      "source_location": "",
      "notes": []
    }
  ]
}

Rules:
- Keep numbers exact.
- If a value is missing, use empty string "" and add a note.
- Separate multiple logical charts into separate objects.
- No markdown, JSON only.

Input summary:
[PASTE CLEANED SUMMARY FROM STEP 1]
```

---

## Step 2B) Provide source chart image(s) for extraction

**Goal:** Upload figure images from papers/slides and extract chart values into structured JSON.

> In ChatGPT, upload one or more source chart images first, then run:

```text
Extract chart data from the uploaded source figure image(s).

Return JSON only:
{
  "charts": [
    {
      "title": "",
      "chart_type": "",
      "axis_labels": {"x": "", "y": ""},
      "axis_units": {"x": "", "y": ""},
      "category_labels": [],
      "legend_labels": [],
      "data_series": [
        {"group": "", "label": "", "value": "", "unit": ""}
      ],
      "footnotes": [],
      "source_citation": "",
      "source_location": "",
      "extraction_warnings": [],
      "low_confidence_fields": []
    }
  ]
}

Rules:
- Read values as precisely as possible from the figure.
- Do NOT infer or fabricate missing values.
- If text/values are unclear, leave the field empty and list it in `low_confidence_fields`.
- Capture uncertainty notes in `extraction_warnings`.
- If multiple charts are present, return one object per chart.

Additional context (optional):
[PASTE RELEVANT CAPTION OR SOURCE TEXT]
```

**Tip:** After this step, merge the extracted JSON with Step 2 output (table/raw-data extraction) and use the combined set for later prompt + QA steps.

---

## Step 3) Create placeholder chart entries for missing values

**Goal:** Create explicit placeholders where values are TBD.

```text
From the context below, identify any chart elements that should exist but have missing numeric values.

Return JSON:
{
  "placeholders": [
    {
      "title": "",
      "placeholder_text": "Exact values to be inserted from [source figure/table]",
      "reason_missing": "",
      "expected_source_location": ""
    }
  ]
}

Rules:
- Do not guess any numbers.
- Keep placeholder text explicit and publication-safe.
- If nothing is missing, return {"placeholders": []}.

Context:
[PASTE CLEANED SUMMARY + STEP 2 JSON]
```

---

## Step 4) Draft infographic content architecture

**Goal:** Define the visual story before image generation.

```text
Design an infographic content blueprint from this material.

Audience: [academic | clinical | patient | community]
Tone: [clear, evidence-based, visually engaging]

Return:
1) Headline options (5)
2) Subheadline options (3)
3) Section structure (4-7 sections)
4) For each section: key point + supporting evidence + suggested visual element
5) Callout ideas for the most important numbers
6) Potential misinterpretation risks and wording fixes

Constraints:
- Keep numerical claims faithful to source.
- Flag where citation labels should appear.
- Do not introduce new claims.

Source:
[PASTE STEP 1 + STEP 2 OUTPUTS]
```

---

## Step 5) Generate final image prompt (GPT Image-ready)

**Goal:** Produce a robust final prompt for image generation.

```text
Write a production-quality prompt for GPT Image to generate a single infographic.

Inputs:
- Audience: [audience]
- Visual style: [style description]
- Topic: [topic]
- Required facts and chart values: [paste structured values]
- Content architecture: [paste step 4 structure]
- Citation text to show: [optional citation]

Prompt requirements:
- Include explicit layout guidance (header, sections, chart blocks, footer).
- Preserve all numeric values exactly.
- If a value is unknown, show a clear placeholder label and do not fabricate data.
- Use clean hierarchy and readable typography.
- Avoid clutter; maintain strong contrast and accessibility.
- Output should be suitable for 16:9 landscape infographic.

Return:
1) Final image prompt
2) Negative prompt (what to avoid)
3) Short "data integrity checklist" used to validate the render
```

---

## Step 5B) Generate the infographic image in ChatGPT

**Goal:** Actually create the image using the Step 5 prompt.

> In ChatGPT, switch to an image-capable model (GPT Image), then paste:

```text
Generate one 16:9 landscape infographic image using this exact prompt.

[PASTE STEP 5 FINAL IMAGE PROMPT]

Negative constraints to enforce:
[PASTE STEP 5 NEGATIVE PROMPT]

Output requirements:
- Produce exactly 1 image.
- Preserve all provided numeric values exactly.
- If any value is unknown, keep the explicit placeholder label (do not invent numbers).
- Keep text legible at presentation size.
```

**After generation:** Save/download the image, then continue to **Step 6 (visual QA)**.

---

## Step 6) Run visual QA on generated image

**Goal:** Verify rendered chart/data fidelity after image creation.

> In ChatGPT, upload your generated image and then use this prompt:

```text
Perform a strict QA review of this infographic image against the reference data below.

Reference data:
[PASTE STEP 2 JSON + PLACEHOLDERS]

Return:
- PASS/FAIL
- Critical issues (wrong numbers, wrong units, wrong labels)
- Moderate issues (layout clarity, ambiguous legends, weak hierarchy)
- Missing citation/location labels
- Exact text snippets to fix
- Regeneration instructions in 8 bullets max

Rules:
- Treat numeric mismatches as critical.
- Do not assume unreadable text is correct; flag uncertainty.
- Be concise and actionable.
```

---

## Step 7) Refinement prompt for next iteration

**Goal:** Improve output while locking data integrity.

```text
Revise the previous infographic prompt using this QA feedback.

Non-negotiable constraints:
- Keep all approved numeric values unchanged.
- Keep citations/source labels intact.
- Only modify layout, readability, emphasis, or wording clarity unless explicitly requested.

QA feedback:
[PASTE STEP 6 OUTPUT]

Current prompt:
[PASTE STEP 5 FINAL PROMPT]

Return:
1) Updated prompt
2) One-paragraph summary of what changed
3) "Locked elements" list (values/claims that must remain unchanged)
```

---

## Optional: One-shot master prompt

Use this if you want ChatGPT to run all stages in one response.

```text
You are an infographic production assistant. Run this workflow in order:
1) Clean source text
2) Extract chart-ready JSON
3) Identify placeholders for missing values
4) Propose content architecture
5) Draft final GPT Image prompt + negative prompt + QA checklist

Constraints:
- Do not invent data.
- Preserve all numeric values exactly.
- Mark unknowns as placeholders.
- Keep output organized by step with clear headings.

Inputs:
- Audience: [audience]
- Style: [style]
- Source text: [paste text]
- Citation requirement: [optional]
```

