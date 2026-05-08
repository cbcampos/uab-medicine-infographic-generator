"""UAB Medicine branded visual styles."""

STYLES: dict[str, dict[str, str]] = {
    "uab-craft-handmade": {
        "name": "UAB Hand-drawn Paper Craft",
        "description": "Warm, organic, community-focused — ideal for patient education",
        "color_palette": """
- Primary: UAB Green (#1A5632), Healing Teal (#08948E), soft warm pastels
- Background: White (#FFFFFF) or Light Cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Cards/panels: Light Teal tint (#E8F6F5)
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), Healing Teal (#08948E), soft warm pastels
- Background: White (#FFFFFF) or Light Cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Fill: Light Teal tint (#E8F6F5) for cards and panels

## Visual Elements
- Hand-drawn or cut-paper quality with organic, slightly imperfect shapes
- Layered depth with soft paper-shadow effects
- Simple cartoon icons representing people and health
- Community/human figures in friendly, approachable cartoon form
- Ample whitespace, clean composition
- Keywords and core concepts highlighted with UAB Gold
- Strictly hand-drawn — no realistic or photographic elements

## Typography
- Clean sans-serif (Source Sans Pro or Open Sans)
- Bold keywords in UAB Green (#1A5632) or Navy (#003A5C)
- Body text in Dark Gray (#4A4A4A)
- Keywords emphasized with larger/bolder text in UAB Gold
""",
    },
    "uab-watercolor": {
        "name": "UAB Storybook Watercolor",
        "description": "Soft hand-painted illustration — professional, editorial quality",
        "color_palette": """
- Primary: Soft washes of UAB Green (#1A5632, low opacity), Healing Teal (#08948E)
- Background: White or cream (#FFF8F0) with watercolor paper texture
- Accents: UAB Gold (#FFC72C) for deeper pigment pools and splatter
- Navy (#003A5C) for line work and detail
""",
        "prompt": """
## Color Palette
- Primary: Soft washes of UAB Green (#1A5632 at low opacity), Healing Teal (#08948E)
- Background: Watercolor paper texture — white or cream (#FFF8F0)
- Accents: UAB Gold (#FFC72C) as deeper pigment pools and splatter
- Navy (#003A5C) for line work and detail

## Visual Elements
- Visible brushstrokes in UAB Green and Teal
- Soft color bleeds and gradients
- White space as a deliberate design element
- Delicate line work over washes
- Organic, flowing medical/health motifs
- Dreamy, atmospheric quality with professional restraint

## Typography
- Elegant serif or humanist sans-serif
- Watercolor-style text integration
- UAB Green or Navy for headings
- Dark Gray (#4A4A4A) for body text
""",
    },
    "uab-academia": {
        "name": "UAB Aged Academia (Vintage Scientific)",
        "description": "Historical scientific illustration — research credibility",
        "color_palette": """
- Primary: Sepia Brown (#704214), UAB Green (#1A5632 at aged tone)
- Background: Parchment (#F4E4BC) or aged cream (#FAF3E0)
- Accents: UAB Gold (#FFC72C) as faded annotation, Navy (#003A5C) ink
""",
        "prompt": """
## Color Palette
- Primary: Sepia Brown (#704214), UAB Green (#1A5632 at aged tone)
- Background: Parchment (#F4E4BC) or aged cream (#FAF3E0)
- Accents: UAB Gold (#FFC72C) as faded annotation, Navy (#003A5C) ink

## Visual Elements
- Aged paper texture overlay
- Detailed cross-hatching and line work in Navy
- Scientific illustration precision
- Study notes and annotations in margins
- Specimen plate / sketch aesthetic
- Numbered diagram elements with UAB Green call-outs
- Visible ink strokes and hand-drawn annotations

## Typography
- Handwritten serif or italic cursive
- Scientific annotations in Dark Gray or Navy
- Small caps for labels in UAB Green
- Italics for scientific names
- UAB Gold for highlighted annotations
""",
    },
    "uab-bold-graphic": {
        "name": "UAB Bold Graphic (Comic/Halftone)",
        "description": "High-contrast comic style — high energy, social media ready",
        "color_palette": """
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), halftone patterns
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), halftone dot patterns

## Visual Elements
- Bold black outlines on all elements
- High contrast UAB color compositions
- Halftone dot patterns in green and gold
- Comic panel borders with UAB Medicine aesthetic
- Action lines and motion for dynamic health content
- Speech bubbles and call-out boxes in UAB palette
- Bold, punchy visual hierarchy

## Typography
- Bold comic lettering in impact style
- UAB Green and Navy for headings
- POW/BANG pop-art effects in UAB Gold
- Caption boxes for data and key statistics
""",
    },
    "uab-corporate": {
        "name": "UAB Corporate Memphis",
        "description": "Flat vector illustration — professional, institutional feel",
        "color_palette": """
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), soft pastels
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), UAB Gold (#FFC72C), Navy (#003A5C)
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Accents: Healing Teal (#08948E), soft pastels

## Visual Elements
- Flat vector illustration style
- Disproportionate friendly human figures
- Abstract body shapes and health symbols
- Floating geometric elements in UAB colors
- Solid fills only — no outlines on figures
- Plant and health object accents

## Typography
- Clean sans-serif (Open Sans, Source Sans Pro)
- Bold UAB Green or Navy headings
- Professional but warm and approachable
- Minimal decoration
""",
    },
    "uab-technical": {
        "name": "UAB Technical Schematic",
        "description": "Engineering-precision diagrams — clinical audience",
        "color_palette": """
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Primary lines: UAB Green (#1A5632) or Navy (#003A5C)
- Accents: UAB Gold (#FFC72C) for highlights, Healing Teal (#08948E) for data
- Critical markup: Red (#CC0000) — sparingly for alerts only
""",
        "prompt": """
## Color Palette
- Background: White (#FFFFFF) or Light Gray (#F5F5F5)
- Primary lines: UAB Green (#1A5632) or Navy (#003A5C)
- Accents: UAB Gold (#FFC72C) for highlights, Healing Teal (#08948E) for data
- Critical markup: Red (#CC0000) — sparingly for alerts only

## Visual Elements
- Precise geometric lines and angles
- Grid patterns in Light Gray
- Measurement and data annotations
- Technical symbols and health data notation
- Dashed construction guides
- Clean clinical aesthetic
- Isometric or orthographic projections

## Typography
- Monospace or technical sans-serif
- Coordinate and dimension labels in Dark Gray
- UAB Green for section labels
- Navy for technical headings
- No decorative elements
""",
    },
    "uab-chalkboard": {
        "name": "UAB Chalkboard",
        "description": "Dark chalkboard background — educational and workshop-friendly",
        "color_palette": """
- Background: Dark Navy (#002A4D) or Chalkboard Black (#1A1A1A)
- Primary Text: Chalk White (#F5F5F5)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E), UAB Green (#1A5632)
""",
        "prompt": """
## Color Palette
- Background: Dark Navy (#002A4D) or Chalkboard Black (#1A1A1A)
- Primary Text: Chalk White (#F5F5F5)
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E), UAB Green (#1A5632)
- Available chalk colors: Gold, Teal, Green, White, Navy

## Visual Elements
- Hand-drawn chalk illustrations — sketchy, imperfect lines
- Chalk dust effects around text and key elements
- Doodles: stars, arrows, circles, checkmarks, hearts, plus signs
- Stick figures and simple medical icons
- UAB-style doodads: cross/heart motifs in chalk
- Eraser smudges and chalk residue textures

## Typography
- Hand-drawn chalk lettering style
- Imperfect baseline for authenticity
- White chalk for body text
- UAB Gold chalk for emphasis and call-outs
- Teal and Green as secondary chalk colors
""",
    },
    "uab-kawaii": {
        "name": "UAB Kawaii (Japanese Cute)",
        "description": "Soft, patient-friendly — pastel health themes with big eyes",
        "color_palette": """
- Primary: Soft pastels — mint (#98D8C8), light lavender (#E6E6FA), pale pink
- Background: White (#FFFFFF) or very light cream
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
""",
        "prompt": """
## Color Palette
- Primary: Soft pastels — mint (#98D8C8), light lavender (#E6E6FA), pale pink
- Background: White (#FFFFFF) or very light cream
- Accents: UAB Gold (#FFC72C), Healing Teal (#08948E)
- Warm tones: soft versions of UAB palette

## Visual Elements
- Big sparkly eyes on cartoon health characters
- Rounded, soft shapes
- Gentle health symbols (hearts, plus signs) in UAB colors
- Sparkles and stars scattered in Gold and Teal
- Cute medical icons (stethoscopes, hearts, pills) in cartoon form
- Chibi-proportioned friendly human figures

## Typography
- Rounded, bubbly sans-serif
- Soft pastels derived from UAB palette
- UAB Gold hearts and Teal dots decorating letters
- Cute, friendly appearance throughout
""",
    },
    "uab-claymation": {
        "name": "UAB Claymation",
        "description": "3D clay figure aesthetic — warm, approachable, stop-motion charm",
        "color_palette": """
- Primary: Saturated UAB Green (#1A5632), Healing Teal (#08948E)
- Background: Light Gray (#F5F5F5) or soft white
- Accents: UAB Gold (#FFC72C), Navy (#003A5C) highlights
- Clay tones: slightly muted, warm
""",
        "prompt": """
## Color Palette
- Primary: Saturated UAB Green (#1A5632), Healing Teal (#08948E)
- Background: Light Gray (#F5F5F5) or soft white
- Accents: UAB Gold (#FFC72C), Navy (#003A5C) highlights
- Clay tones: slightly muted, warm

## Visual Elements
- Clay/plasticine texture on all objects
- Rounded, sculpted human figures (friendly medical staff/patients)
- Soft shadows, stop-motion staging
- Fingerprint marks and imperfections for authenticity
- Miniature set aesthetic
- Warm and approachable health characters

## Typography
- Extruded, dimensional text (as if made of clay)
- Rounded, friendly sans-serif
- Bold UAB Green or Navy for emphasis
- Chunky, playful lettering
""",
    },
    "uab-cyberpunk-neon": {
        "name": "UAB Cyberpunk Neon",
        "description": "Neon glow on deep navy — futuristic medical tech aesthetic",
        "color_palette": """
- Primary: Healing Teal (#08948E) as neon glow, Electric Blue (#00B0FF)
- Background: Deep Navy (#002A4D) or near-black (#0A0A0A)
- Accents: UAB Gold (#FFC72C) neon glow, UAB Green (#1A5632) glow
""",
        "prompt": """
## Color Palette
- Primary: Healing Teal (#08948E) as neon glow, Electric Blue (#00B0FF)
- Background: Deep Navy (#002A4D) or near-black (#0A0A0A)
- Accents: UAB Gold (#FFC72C) neon glow, UAB Green (#1A5632) glow
- Chrome and teal highlights

## Visual Elements
- Glowing neon outlines in Teal and Gold
- Dark atmospheric backgrounds (deep navy, not pure black)
- Subtle circuit/health data patterns
- Digital holographic elements
- Health metric visualizations in neon
- Rain and reflection effects

## Typography
- Glowing neon text — Teal (#08948E) and Gold (#FFC72C)
- Digital/tech sans-serif
- Outlined glow letters for headings
- Flickering or pulsing text effects
""",
    },
    "uab-poster-classic-experimental": {
        "name": "UAB Poster Classic (Experimental)",
        "description": "Academic poster layout with top brand bar, centered title, and multi-column content blocks",
        "color_palette": """
- Primary: UAB Green (#1A5632), White (#FFFFFF)
- Background: Light Gray (#F2F2F2) or White (#FFFFFF)
- Text: Dark Gray (#1F2933) and near-black for body copy
- Accent: Healing Teal (#08948E) sparingly for subtitle emphasis
""",
        "prompt": """
## Color Palette
- Primary: UAB Green (#1A5632), White (#FFFFFF)
- Background: Light Gray (#F2F2F2) or White (#FFFFFF)
- Text: Dark Gray (#1F2933) and near-black for body copy
- Accent: Healing Teal (#08948E) used sparingly for subtitle emphasis

## Poster Layout Structure (STRICT)
- Build a clean academic poster composition (16:9 landscape) with strong grid alignment.
- TOP EDGE: thin UAB Green horizontal brand strip.
- HEADER BLOCK (centered): large poster title, subtitle below it, and a short horizontal divider line.
- BODY: 4-column poster-style layout with clear section blocks and ample spacing.
- Include section headings such as: Abstract, Introduction, Methodology, Results, Acknowledgements, Conclusion.
- Add one emphasized recommendation/callout panel in UAB Green with white text.
- Include 1-2 rectangular image/photo placeholder areas integrated into the column layout.
- Use thin UAB Green divider lines between major columns/sections.
- Keep overall look clean, institutional, and publication-ready (not playful, not comic, not watercolor).

## Visual Style Rules
- Flat, professional poster aesthetic with minimal decoration.
- Strong hierarchy: title > section headers > body text > bullets.
- Use bullet lists where appropriate for methods/results summaries.
- Avoid clutter: prioritize whitespace and alignment.
- No heavy gradients, shadows, or noisy textures.

## Typography
- Use a clean sans-serif poster style (Source Sans / Open Sans equivalent feel).
- Title in UAB Green, bold and uppercase-like emphasis.
- Subtitle in green/teal and smaller than title.
- Section headers bold and high-contrast.
- Body text readable and compact for poster scanning distance.
""",
    },
}
