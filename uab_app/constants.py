"""Shared constants for the UAB Medicine Infographic Generator."""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
IMAGE_GEN_TIMEOUT_S = 180
MAX_GENERATION_ATTEMPTS = 4  # initial + 3 retries
BACKOFF_BASE_S = 5

OPENAI_DEFAULT_IMAGE_MODEL = "gpt-image-2"
OPENAI_DEFAULT_CHAT_MODEL = "gpt-4o-mini"
OPENAI_DEFAULT_VISION_MODEL = "gpt-4o"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Image generation uses generateContent — model must be a real API resource ID.
# Nano Banana Pro = gemini-3-pro-image-preview (see Gemini API image-gen docs).
GEMINI_DEFAULT_IMAGE_MODEL = "gemini-3-pro-image-preview"

# Friendly / marketing aliases → official model IDs (404 if wrong ID is passed to REST).
GEMINI_IMAGE_MODEL_ALIASES = {
    "nano-banana-pro": "gemini-3-pro-image-preview",
    "nano-banana-pro-preview": "gemini-3-pro-image-preview",
    "nano-banana": "gemini-2.5-flash-image",
    "nano-banana-2": "gemini-3.1-flash-image-preview",
    "nano-banana-flash-image": "gemini-3.1-flash-image-preview",
}
GEMINI_DEFAULT_CHAT_MODEL = "gemini-2.5-pro"
GEMINI_DEFAULT_VISION_MODEL = "gemini-2.5-pro"

# LLM document cleanup chunking (per OpenAI chat input limits)
DOCUMENT_CLEAN_MAX_CHUNK_CHARS = 14_000
DOCUMENT_CLEAN_CHUNK_OVERLAP = 400

CHART_VERIFICATION_STATUSES = frozenset(
    {
        "unverified",
        "needs_review",
        "conflict_unresolved",
        "verified",
        "placeholder_approved",
        "removed",
    }
)
CHART_BLOCKING_STATUSES = frozenset({"unverified", "needs_review", "conflict_unresolved"})
CHART_MODES = ("exact", "style_transform", "reference_only")
CHART_DATA_FILE_EXT = {".csv", ".xlsx", ".json"}
CHART_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_CHART_UPLOAD_BYTES = MAX_UPLOAD_BYTES

# Chart extraction limits
CHART_EXTRACTION_CONTEXT_MAX_CHARS = 8000
CHART_CONFLICT_MAX_ITEMS = 25

# Session state size limits
MAX_GENERATION_HISTORY = 20
MAX_CLEANED_DOCS_CACHE = 10
MAX_SESSION_CHARTS = 50

EXTRACTION_SYSTEM_PROMPT = """You extract structured data from medical/statistical charts for an infographic pipeline.
Output ONLY valid JSON (no markdown fences, no commentary). Use this exact schema:
{
  "chart_type": string,
  "title": string,
  "axis_labels": {"x": string, "y": string},
  "axis_units": {"x": string, "y": string},
  "category_labels": [string],
  "legend_labels": [string],
  "data_series": [
    {
      "label": string,
      "n": number or null,
      "median": number or null,
      "mean": number or null,
      "value": number or null,
      "range_type": "IQR"|"CI"|"SD"|"SE"|"min_max"|null,
      "range_low": number or null,
      "range_high": number or null,
      "point_estimate": number or null,
      "lower_ci": number or null,
      "upper_ci": number or null,
      "category": string,
      "unit": string
    }
  ],
  "footnotes": [string],
  "source_citation": string,
  "confidence_level": { "field.path": "high"|"medium"|"low" },
  "extraction_warnings": [string],
  "suggested_chart_render": "dot_and_range"|"evidence_callout"|"bar"|"pie"|"placeholder"|"other"
}
If the image is not a chart, set chart_type to "not_a_chart" and data_series to [].
Never invent values: if unreadable, use null and lower confidence.
"""

AUDIENCE_GUIDANCE = {
    "academic": """### academic
- Formal tone, data-dense layout
- Include citation placeholders where appropriate
- Minimal decorative illustration; emphasize diagrams and charts
- Professional color palette; restrained use of UAB Gold
- Written for peers (researchers, faculty, clinicians)
- Keep technical precision and report uncertainty when relevant""",
    "clinical": """### clinical
- Clinical tone, evidence-based framing
- Clear clinical endpoints and outcomes when data exists in the source
- Professional but slightly more visual than academic
- Use clinical terminology appropriately
- Suitable for HCP education materials
- Emphasize practical clinical relevance and care implications""",
    "patient": """### patient
- Use plain language at approximately 8th-grade reading level
- Define or replace complex medical terms in simple words
- Short sentences, clear headings, and concrete takeaways
- Action-oriented messaging ("what you can do")
- Encouraging tone, positive framing
- Friendly illustrations, accessible iconography
- Large readable text, minimal jargon""",
    "community": """### community
- Culturally relevant, warm, community-centered
- Tell a compelling story arc: problem -> why this matters -> actions
- Include an explicit "Why this matters" element tied to real-life impact
- Action-oriented with clear calls to action
- Celebratory of community assets and strengths
- Accessible language, relatable visuals
- Suitable for flyers, community presentations, health fairs""",
}

AUDIENCE_SECTION_PLANS = {
    "academic": {
        "required_sections": [
            "Background/Objective",
            "Methods/Study Design",
            "Key Findings",
            "Evidence Snapshot",
            "What This Means for Practice/Research",
        ],
        "avoid_sections": [
            "Oversimplified patient instructions",
            "Community action/resource lists",
            "Promotional calls to action",
        ],
        "primary_visual_emphasis": (
            "Research structure, methods, diagrams, precise charts, and evidence callouts."
        ),
        "chart_policy": (
            "Use charts and numeric evidence when explicitly supported; preserve exact values, "
            "uncertainty, and source terminology. Do not add demographic percentages, sample descriptors, "
            "or participant characteristics unless those exact details appear in the source."
        ),
        "language_policy": (
            "Formal, technical, concise language suitable for researchers, faculty, and clinicians. "
            "Use compact labels and short evidence callouts rather than paragraph text."
        ),
        "required_panel_title": "What This Means for Practice/Research",
    },
    "clinical": {
        "required_sections": [
            "Clinical Question",
            "Patient/Population Context",
            "Key Outcomes",
            "Care Implications",
            "What To Do in Practice",
        ],
        "avoid_sections": [
            "Detailed research-methods sections unless essential to interpretation",
            "Patient self-care instructions framed as personal medical advice",
            "Community resource/action lists",
        ],
        "primary_visual_emphasis": (
            "Decision relevance: population, outcomes, risk/benefit, care pathway, and practice implications."
        ),
        "chart_policy": (
            "Use outcome, risk, benefit, and care-relevant numeric callouts when supported; "
            "avoid excessive research-method chart density. Do not add demographic percentages, "
            "risk scores, or clinical measurements unless those exact values are source-supported."
        ),
        "language_policy": (
            "Use clear clinical terminology for health professionals. Frame implications as care-team "
            "assessment, shared planning, burden reduction, and follow-up support. Population context may "
            "include only source-confirmed descriptors; do not invent demographic percentages, age, food "
            "insecurity, deprivation index, or clinical measurements. Keep bullets short and action-oriented."
        ),
        "required_panel_title": "What This Means for Care",
    },
    "patient": {
        "required_sections": [
            "What This Means",
            "Key Takeaways",
            "What You Can Do",
            "When To Talk With Your Care Team",
            "Source",
        ],
        "avoid_sections": [
            "Methods/Study Design",
            "Dense statistical charts",
            "Unexplained medical jargon",
            "Research uncertainty panels unless written in plain language",
        ],
        "primary_visual_emphasis": (
            "Large plain-language takeaways, friendly icons, simple steps, and care-team conversation prompts."
        ),
        "chart_policy": (
            "Avoid technical charts unless essential; translate supported statistics into simple callouts "
            "while preserving exact numbers. Do not show device readings, percentages, or measurements "
            "unless those exact values appear in the source."
        ),
        "language_policy": (
            "Plain language around an 8th-grade reading level; encouraging, choice-centered, and not medical advice. "
            "Use 'may help' language and remind viewers they can ask their care team for a plan that fits their life. "
            "Avoid titles or bullets that promise disease management outcomes; frame support as something that can "
            "be easier when tools fit the person's life and support is available. Use very short plain-language phrases "
            "and no paragraph blocks."
        ),
        "required_panel_title": "What This Means for You",
    },
    "community": {
        "required_sections": [
            "Why This Matters Here",
            "Community Context",
            "What We Learned",
            "Barriers/Supports",
            "Community Actions/Resources",
        ],
        "avoid_sections": [
            "Individual-blame framing",
            "Dense research-methods sections",
            "Overly clinical or statistical language",
        ],
        "primary_visual_emphasis": (
            "Local relevance, lived context, barriers and supports, resources, trust, and collective action."
        ),
        "chart_policy": (
            "Use simple evidence callouts or lightweight comparisons; avoid dense charts unless they directly "
            "clarify community impact. Do not add device readings, statistics, demographics, sample descriptors, "
            "or participant characteristics unless exactly source-supported."
        ),
        "language_policy": (
            "Accessible, warm, strengths-based language that avoids blaming individuals or communities. "
            "Emphasize systems, access, trust, technology fit, resources, culturally responsive support, and collective action. "
            "Prefer systems/access/trust/resource framing over individual behavior instructions, and do not imply specific "
            "local programs or resources unless they are named in the source. Community actions should be program/system "
            "design actions such as designing with community partners, reducing access barriers, supporting trusted messengers, "
            "and offering flexible low-burden options. Use short labels and brief action phrases, not explanatory paragraphs."
        ),
        "required_panel_title": "What This Means for Our Community",
    },
}
