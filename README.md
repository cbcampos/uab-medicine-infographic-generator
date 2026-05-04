# UAB Medicine Infographic Generator

Generate production-ready, branded infographics using GPT Image 2.0 (OpenAI direct or Azure OpenAI). Built for academic medicine — with UAB Medicine branding, PHI safeguards, audience targeting, and strict chart accuracy enforcement.

**[→ Live app on your Mac](http://localhost:8502)** · **[→ GitHub repo](https://github.com/cbcampos/uab-medicine-infographic-generator)**

---

## Features

### 🎨 Visual Styles (10 UAB Medicine-branded)
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

**UAB Medicine Logo Compliance**
- Only the approved logo file is used — never generated, redrawn, or modified
- Explicit do-not-do rules in every prompt (no invented seals, shields, or brand marks)
- Logo composited via Pillow (exact pixels) after generation

**Input Sanitization**
- Control characters stripped from all user text and documents
- Prompt injection detection (repeated system override attempts flagged and blocked)

### 🔁 Generation Tools

- **Style Comparison** — generate the same content in 3 styles simultaneously, side-by-side
- **Image Refinement** — iterative feedback loop (up to 3 passes) to fine-tune results
- **Prompt Preview** — audit the full prompt before it is sent to the API
- **Generation Progress** — staged progress bar: Parsing → Cleaning → Building → Generating → Fetching
- **Retry Logic** — 3 automatic retries with exponential backoff, 90s hard timeout

### 📄 Document Ingestion
Upload PDFs, DOCX, or TXT files as source context. Content is:
1. Extracted and parsed
2. LLM-scrubbed for artifacts and PHI-adjacent content
3. Folded into the generation prompt

---

## Brand Colors

| Color | Hex | Use |
|---|---|---|
| UAB Green | `#1A5632` | Primary, headings, key elements |
| UAB Gold | `#FFC72C` | Accents, highlights, emphasis |
| Navy | `#003A5C` | Secondary, text contrast |
| Healing Teal | `#08948E` | Patient content, secondary accents |

> **Athletic Gold `#E87722` is NOT part of UAB Medicine** — it is explicitly forbidden in all generations.

Brand guide: [uab.edu/brandguide/uab-medicine/colors](https://www.uab.edu/brandguide/uab-medicine/colors)

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
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_IMAGE_MODEL=gpt-image-2
```

### 4. Run

```bash
streamlit run infographic_app.py --server.port 8502
```

Open [http://localhost:8502](http://localhost:8502)

---

## Architecture

```
infographic_app.py     ← all-in-one Streamlit app
├── Style library      ← 10 UAB-branded visual styles
├── Document parsers    ← PDF (PyPDF2), DOCX (python-docx), TXT
├── LLM cleanup        ← PHI scrub + OCR normalization via LLM
├── Prompt builder     ← assembles style + audience + docs + rules
├── Image generator    ← OpenAI direct or Azure OpenAI
├── Logo compositor    ← Pillow composites approved logo post-generation
├── Retry logic        ← 3x backoff on failure
└── Audit logger      ← JSON logs (no prompts stored)
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
7. **Prompt preview** — user audits prompt before API call

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

Internal use — UAB Medicine / Forge AHEAD Center

---

> Built on the [notex](https://github.com/smallnest/notex) infographic prompt architecture. Powered by GPT Image 2.0 via OpenAI API.
