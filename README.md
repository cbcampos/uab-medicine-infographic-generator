# Infographic Generator

Generate infographic drafts and style concepts using GPT Image 2.0 (OpenAI direct, Azure OpenAI, or Gemini image models where configured). Suited to academic medicine workflows — with optional logo compositing, PHI safeguards, audience targeting, and strict chart accuracy guidance.

---

## Features

### 🎨 Visual Styles (11 curated styles)
| Style | Best for |
|---|---|
| Hand-drawn Paper Craft | Patient education, community outreach |
| Storybook Watercolor | Editorial, professional publications |
| Aged Academia | Research posters, journal presentations |
| Bold Graphic (Comic/Halftone) | Social media, high-energy presentations |
| Corporate Memphis | Institutional, professional materials |
| Technical Schematic | Clinical audiences, data-heavy contexts |
| Chalkboard | Workshops, educational settings |
| Kawaii (Japanese Cute) | Pediatric/family health, friendly materials |
| Claymation | Community engagement, warm tones |
| Cyberpunk Neon | Tech-forward, futuristic health topics |
| Poster Classic (Experimental) | Academic poster layout inspired by UAB-style research posters |

### 🧭 UX Modes
- **Basic mode** — one-shot flow for first-time users (upload sources → generate)
- **Advanced mode** — full controls for provider/model settings, chart references, fidelity checks, compare mode
- **Step-based navigation** — collapsible Step 1/2/3 sections for guided completion
- **Style guide modal** — preview generated examples for each style before choosing

### 🎯 Audience Targeting
Tailor tone, vocabulary, and visual density to your exact audience:
- **Academic** — formal, data-dense, citation-ready for journals and conferences
- **Clinical** — evidence-based framing for healthcare providers
- **Patient** — plain language, warm, action-oriented
- **Community** — culturally relevant, strength-based, action-driven

### 🛡️ Clinical-grade Safeguards

**PHI Protection**
- Mandatory "No PHI" checkbox before every generation
- Warning banner on document upload
- Uploaded documents are LLM-scrubbed before use (control characters, OCR artifacts, PHI-adjacent content removed)

**Chart Accuracy Enforcement**
- Box plots and forest plots are explicitly forbidden (model invents whisker/HR values)
- Only these are allowed: dot-and-range charts, evidence callout cards, bar/pie with exact values
- Incomplete data → placeholder boxes labeled "Exact values to be inserted from [source]"
- No invented whiskers, quartile boundaries, or inferred HRs by subgroup

**Logo compliance**
- When a logo file is configured, only that file is used — never generated, redrawn, or modified
- Explicit do-not-do rules in every prompt (no invented seals, shields, or brand marks)
- Logo composited via Pillow (exact pixels) after generation

**Input Sanitization**
- Control characters stripped from all user text and documents
- Prompt injection detection (repeated system override attempts flagged and blocked)

### 🔁 Generation Tools

- **Style Comparison** — generate the same content in 3 styles simultaneously, side-by-side
- **Image Refinement** — iterative feedback loop to fine-tune results
- **Refinements Scan (Vision)** — AI grades the generated image and proposes practical edits
- **Use AI Suggestions** — one-click apply scan suggestions and regenerate
- **Prompt Preview** — audit the full prompt before it is sent to the API
- **Generation Progress** — staged progress bar: Parsing → Cleaning → Building → Generating → Fetching
- **Retry Logic** — automatic retries with exponential backoff

### 📄 Document Ingestion
Upload PDFs, DOCX, or TXT files as source context. Content is:
1. Extracted and parsed
2. LLM-scrubbed for artifacts and PHI-adjacent content
3. Folded into the generation prompt

---

## Default palette (styles)

| Color | Hex | Use |
|---|---|---|
| Primary green | `#1A5632` | Primary, headings, key elements |
| Accent yellow | `#FFC72C` | Accents, highlights, emphasis |
| Navy | `#003A5C` | Secondary, text contrast |
| Healing teal | `#08948E` | Patient content, secondary accents |

> **Athletic Gold `#E87722`** is excluded from the default generation palette in prompts.

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/cbcampos/uab-medicine-infographic-generator.git
cd uab-medicine-infographic-generator
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Add your API credentials

```bash
cp .env.example .env
# Edit .env with your credentials
```

**Option A — OpenAI Direct (GPT Image 2)**
```
OPENAI_API_KEY=sk-...
```

**Option B — Azure OpenAI**
```
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_IMAGE_MODEL=gpt-image-2
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_VISION_DEPLOYMENT=gpt-4o
```
> Note: the app is locked to Azure API version `2024-02-01` internally for compatibility.

### 4. Run

```bash
streamlit run infographic_app.py --server.port 8502
```

Open [http://localhost:8502](http://localhost:8502)

---

## Architecture

```
infographic_app.py           ← Streamlit entrypoint
uab_app/ui.py                ← UI flow (basic/advanced, step navigation, style guide modal)
uab_app/styles.py            ← 11 curated visual styles
uab_app/prompts.py           ← prompt builder + hard constraints
uab_app/cleanup.py           ← LLM document cleanup + source profile inference
uab_app/image_service.py     ← model calls, retry logic, logo compositing
uab_app/charts.py            ← chart extraction/QA/fidelity helpers
uab_app/parsers.py           ← PDF/DOCX/TXT parsing
generate_style_examples.py   ← batch style example generation script
```

---

## Prompt Safety Layers

Every generation runs through multiple enforcement layers:

1. **Chart accuracy rules** — forbids box plots, forest plots, invented data
2. **Logo compliance rules** — forbids logo generation, requires approved file
3. **Athletic Gold exclusion** — explicit do-not-use color rule
4. **PHI checkbox** — blocks generation without confirmation
5. **Document LLM scrub** — removes PHI-adjacent content before prompt assembly
6. **Input sanitization** — strips control chars, detects injection attempts
7. **Prompt preview** — user audits prompt before API call (Advanced mode)

---

## Style Example Generation

Generate/refresh style examples (saved under `assets/style_examples/`):

```bash
.venv/bin/python generate_style_examples.py "/absolute/path/to/source.pdf"
```

The script mirrors app behavior (cleanup + inferred profile + prompt builder + same image dimensions).

---

## Keyboard Shortcuts / Tips

- Use **landscape (1792×1024)** for deck-ready infographics
- Set quality to **high** when the image has small text, legends, or footnotes
- Run the same content in 3 different styles to compare before choosing
- When refining, make one small change at a time (colors vs. layout vs. text size)
- Do not upload documents containing PHI — scrubbed or not

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit and push (`git push origin feature/my-feature`)
4. Open a Pull Request

---

## License

See the repository root for license and use terms.

---

> Built with inspiration from the [notex](https://github.com/smallnest/notex) infographic prompt architecture. Powered by GPT Image 2.0.
