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
- Written for peers (researchers, faculty, clinicians)""",
    "clinical": """### clinical
- Clinical tone, evidence-based framing
- Clear clinical endpoints and outcomes when data exists in the source
- Professional but slightly more visual than academic
- Use clinical terminology appropriately
- Suitable for HCP education materials""",
    "patient": """### patient
- Plain language, warm, non-technical
- Action-oriented messaging ("what you can do")
- Encouraging tone, positive framing
- Friendly illustrations, accessible iconography
- Large readable text, minimal jargon""",
    "community": """### community
- Culturally relevant, warm, community-centered
- Action-oriented with clear calls to action
- Celebratory of community assets and strengths
- Accessible language, relatable visuals
- Suitable for flyers, community presentations, health fairs""",
}
