"""Shared constants for the UAB Medicine Infographic Generator."""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
IMAGE_GEN_TIMEOUT_S = 90
MAX_GENERATION_ATTEMPTS = 4  # initial + 3 retries
BACKOFF_BASE_S = 5

OPENAI_DEFAULT_IMAGE_MODEL = "gpt-image-2"
OPENAI_DEFAULT_CHAT_MODEL = "gpt-4o-mini"
OPENAI_DEFAULT_VISION_MODEL = "gpt-4o"

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
