# UAB Medicine Division Digital Design System

**Version:** 1.0.0

**Status:** Approved baseline derived from the Collaborator Finder

**Applies to:** Division-sponsored websites, search tools, directories, dashboards, and
faculty-facing workflows

## 1. Design intent

The system should feel unmistakably connected to UAB Medicine while remaining practical,
calm, and credible for faculty work. It is designed for information-heavy products, not
marketing microsites.

Every product should communicate four qualities:

1. **Institutional trust.** UAB Medicine is the primary visual anchor.
2. **Division ownership.** The Division of General Internal Medicine & Population
   Science is visible without competing with the institutional brand.
3. **Clarity over decoration.** Hierarchy, spacing, and evidence do more work than visual
   effects.
4. **Accessible confidence.** Controls are obvious, language is direct, and every
   experience works with keyboard, touch, zoom, and assistive technology.

### Normative visual references

These captured production states are the visual baseline for implementation review:

- [Desktop landing page](../evidence/institutional-shell-2026-07-27/home-1440x900.png)
- [Desktop search results](../evidence/campus-deployment-2026-07-27/collab-campus-search-1440x900-v2.png)
- [Phone landing page](../evidence/campus-deployment-2026-07-27/collab-campus-home-playwright-430x844.png)
- [Phone search results](../evidence/campus-deployment-2026-07-27/collab-campus-search-playwright-430x844.png)
- [Constrained-height profile](../evidence/institutional-shell-2026-07-27/dalton-profile-full-900x500.png)

Use these references for density, hierarchy, and shell behavior. They do not make
search-specific content or layout mandatory for unrelated tools.

## 2. Brand hierarchy

Use this order consistently:

1. **UAB Medicine logo**
2. **Product or tool name**
3. **Division of General Internal Medicine & Population Science**

The header uses a horizontal lockup. The footer repeats the UAB Medicine identity, spells
out the university relationship, names the division, and includes the University of
Alabama System mark.

### Header lockup

- Place the white UAB Medicine logo at the far left.
- Follow it with a 1 px translucent divider.
- Set the product name in bold Inter.
- Place the division name beneath the product name on wide screens.
- Hide only the division subtitle at narrower desktop widths; retain it in the mobile
  menu so division ownership is never lost.
- Do not recreate the logo as text, recolor it, stretch it, add effects, or place it on a
  low-contrast background.
- Use the approved asset at `/uab-medicine-logo-white.svg`.

### Footer lockup

- Use two green tiers.
- The upper tier contains UAB Medicine and division attribution on the left and the
  University of Alabama System mark on the right.
- The lower tier contains the nondiscrimination link, institutional links, and copyright.
- At phone widths, stack brands and legal links vertically.
- Use `/uab-system-logo-white.png` for the system mark.

## 3. Foundations

### 3.1 Color

| Token                |     Value | Use                                     |
| -------------------- | --------: | --------------------------------------- |
| Brand green          | `#005A43` | Primary actions, selected states, icons |
| Brand green dark     | `#004737` | Headlines, hover states                 |
| Brand green deep     | `#003F31` | Deep brand surfaces                     |
| Institutional header | `#14513F` | Global header                           |
| Footer upper         | `#134A3A` | Footer brand tier                       |
| Footer lower         | `#113F33` | Footer legal tier and mobile navigation |
| Mint                 | `#EAF4F0` | Selected filters and quiet emphasis     |
| Warm canvas          | `#F6F3EC` | Default page background                 |
| White                | `#FFFFFF` | Cards, controls, working surfaces       |
| Ink                  | `#17211E` | Primary text                            |
| Muted ink            | `#56635F` | Secondary text and helper copy          |
| Border warm          | `#DDD8CD` | Default border on warm canvas           |
| Border cool          | `#D9DFDC` | Dividers and neutral controls           |
| Focus gold           | `#FDB913` | Keyboard focus only                     |
| Active gold          | `#F4C95D` | Active navigation underline             |
| Error                | `#8B2C2C` | Errors and destructive warnings         |

Green must not be the sole indicator of selection or status. Pair it with text, shape,
icons, `aria-current`, `aria-pressed`, or native state.

### 3.2 Typography

Use **Inter** as the product typeface, with this fallback stack:

```css
font-family:
  Inter,
  ui-sans-serif,
  -apple-system,
  BlinkMacSystemFont,
  "Segoe UI",
  sans-serif;
```

| Role          |                       Size |  Weight | Line height | Notes                             |
| ------------- | -------------------------: | ------: | ----------: | --------------------------------- |
| Display       | `clamp(44px, 5.5vw, 68px)` |     700 |        1.02 | Landing page only                 |
| Page title    | `clamp(28px, 3.2vw, 40px)` |     700 |        1.12 | Profiles and primary pages        |
| Section title |                      22 px |     700 |        1.25 | Major content group               |
| Card title    |                      17 px |     700 |         1.3 | Person or tool name               |
| Body          |                   15–16 px |     400 |    1.55–1.7 | Default reading text              |
| Compact body  |                   13–14 px | 400–600 |    1.4–1.55 | Cards and supporting UI           |
| Eyebrow       |                      12 px | 700–750 |         1.3 | Uppercase, `0.08–0.10em` tracking |
| Metadata      |                   11–12 px | 500–700 |        1.35 | Tags, timestamps, source labels   |

Guidelines:

- Use sentence case for headings, buttons, navigation, tags, and labels.
- Use title case only when it is part of a proper name.
- Keep prose to roughly 70–88 characters per line.
- Use tight negative letter spacing only for large display headings.
- Avoid all caps except short eyebrow and evidence labels.
- Never reduce functional text below 11 px.

### 3.3 Spacing

Use a 4 px base grid. The preferred scale is:

`4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64, 72`

Standard relationships:

- Icon to label: 8 px
- Tag gap: 6–8 px
- Form field gap: 12–16 px
- Card internal padding: 18–24 px
- Card grid gap: 14–18 px
- Section separation: 32–56 px
- Page side padding: 24 px desktop, 20 px tablet, 16 px phone

Whitespace is functional. Separate discovery, results, and detail regions clearly rather
than relying on borders alone.

### 3.4 Shape and elevation

- Small controls: 6–8 px radius
- Cards and inputs: 10–12 px radius
- Large dialogs: 14–18 px radius
- Tags and chips: pill radius
- Use square-ish faculty photos with 7–10 px radius; do not use decorative circles for
  portrait photography.
- Default card shadow: `0 3px 9px rgba(55, 53, 45, 0.07)`
- Raised card shadow: `0 10px 30px rgba(22, 57, 47, 0.07)`
- Modal shadow: `0 18px 48px rgba(9, 52, 40, 0.13)`
- Prefer borders to shadows. Do not stack multiple strong elevations on one page.

### 3.5 Page geometry

- Institutional shell maximum: 1600 px
- Standard content maximum: 1280 px
- Search/results maximum: 1380 px when a filter rail is present
- Reading text maximum: 70 ch
- The content area must never create page-level horizontal scrolling.
- Use one predictable page-level vertical scroll path. Scrollable dialogs and mobile
  sheets are the only normal exceptions.

## 4. Institutional shell

### 4.1 Desktop header

- Height: 74 px plus a 2 px warm neutral top rule.
- Background: `#14513F`.
- Horizontal padding: 28 px.
- Logo width: about 210 px.
- Product name: 16 px/700.
- Navigation links: minimum 48 px tall, 14 px/540, icon plus label.
- Active link: quiet translucent background plus 3 px gold underline.
- Icons may use a restrained functional accent palette, but labels remain white.
- Header remains sticky.

### 4.2 Mobile header

- Break to the menu at 980 px.
- Height: 64 px at 640 px and below.
- Logo width: about 166 px.
- Keep the product name visible when space permits.
- Menu control is at least 48 × 48 px.
- The expanded menu is full width, deep green, and includes the division name.

### 4.3 Footer

- Upper tier minimum height: 112 px.
- Lower tier minimum height: 68 px.
- UAB Medicine logo width: about 320 px desktop, 250 px phone.
- University System logo width: about 230 px desktop, 205 px phone.
- Legal links have at least 44 px touch height.
- Footer is part of every public-facing route, including error and empty states.

## 5. Core component patterns

### 5.1 Buttons

**Primary**

- White text on brand green.
- Minimum 44 px high; use 48 px for prominent actions.
- 8 px radius, 17–22 px horizontal padding, 600–700 weight.
- Hover uses dark green.

**Secondary**

- Ink text on white with a neutral border.
- Use for profile links, evidence actions, and secondary workflows.

**Tertiary/text**

- Green text on transparent background.
- Reserve for reversible low-emphasis actions.

Never use an unlabeled icon when a short text label is practical. Disabled controls must
remain legible and must not be the only explanation of why an action is unavailable.

### 5.2 Search field

- Treat search as a composed control: search icon, text input, optional clear action, and
  primary submit button.
- Minimum container height: 58 px; landing variant 66 px.
- White surface, 12 px radius, cool neutral border, subtle green shadow.
- On focus, use a green border and a quiet green focus halo.
- Clear action is a 44 × 44 px target.
- Place sensitive-data guidance immediately below the field where relevant.
- Loading must preserve layout and announce progress with a live region.

### 5.3 Cards

- White surface on warm canvas.
- Warm neutral border and restrained shadow.
- 10–12 px radius.
- Provide one obvious primary action or make the complete card a single accessible link.
- Hover may raise a card by no more than 2 px or add a green outline; avoid both on dense
  result tables.
- Use a clear hierarchy: title, concise metadata, supporting explanation, action.

### 5.4 Tags and chips

- Tags describe immutable metadata; chips represent active or removable state.
- Use pale mint with green text and border.
- Sentence case and normalized capitalization are required.
- Tags may be compact; interactive chips must be at least 44 px high.
- Never expose internal taxonomy IDs, moderation status, field names, or implementation
  terminology to end users.

### 5.5 Filters

- Desktop: 252 px left rail with grouped controls.
- Hide the rail below 1100 px and replace it with a labeled Filters button.
- Mobile: right-side sheet, maximum 440 px, one internal scroll region, fixed action row.
- OR-within/AND-across behavior must be explained in plain language.
- Show active filters outside the sheet as removable chips.
- All checkboxes and options have a 44 px interactive region even when their visual
  indicator is smaller.

### 5.6 Results

- Results page uses a warm canvas and a white search field.
- State the result count and distinguish direct/best matches from related expertise.
- Desktop primary result structure:
  1. rank;
  2. portrait;
  3. identity and role metadata;
  4. source-grounded match explanation.
- Match explanations must be natural, specific, and evidence-relative.
- “View evidence” opens the exact supporting source in an accessible dialog.
- Do not show scores, internal ranking factors, unreviewed vocabulary, or operational
  labels in the normal user interface.

### 5.7 Profile

- Lead with identity, photograph, role, key actions, and a concise biography.
- Use a visible section navigator on desktop; let it wrap on smaller screens.
- Organize expertise rather than presenting one long tag wall.
- Use term chips for concise controlled topics and bordered narrative blocks for projects,
  interests, and other relationship-rich content.
- Use two columns only when both columns remain readable; collapse below 900 px.
- Source links belong next to the relevant content, not in a detached technical appendix.

### 5.8 Dialogs, sheets, and status

- Dialogs have a visible title, close button, focus trap, Escape support, and return focus
  to the trigger.
- Maximum desktop width: 680 px for evidence; use a full-height side sheet for filters.
- Errors explain what happened and the next action.
- Empty states offer a recovery path.
- Success and loading status use `aria-live` without stealing focus.

## 6. Icons and imagery

- Use Lucide-style outline icons at 1.8 px stroke weight.
- Standard inline icon: 18–20 px.
- Decorative circular icon field: 40–42 px, white icon on brand green.
- Icons supplement text; they do not replace accessible names.
- Faculty photos use approved directory images with `object-fit: cover`.
- Missing portraits use a restrained initials placeholder derived from brand greens.
- Do not use fictional people, decorative stock photography, or AI-generated faculty
  images.

## 7. Content and voice

The voice is faculty-centered, direct, and useful.

Use:

- “Why this match”
- “Andrea’s current projects examine…”
- “Related expertise”
- “No direct matches yet”
- “Try removing a filter or broadening the topic”

Avoid:

- “Verified expertise evidence”
- “Structured assertion”
- “Taxonomy concept”
- “Steward-moderated overlay”
- “AI-generated recommendation”
- Any implication of availability, willingness, quality, popularity, or endorsement

Explain the user benefit first. Keep data provenance available one level deeper through
“View evidence.”

## 8. Responsive behavior

Use content-driven layout changes at these shared thresholds:

|    Breakpoint | Behavior                                                |
| ------------: | ------------------------------------------------------- |
| Above 1180 px | Full product and division lockup                        |
|       1100 px | Filter rail becomes a mobile/tablet sheet               |
|        980 px | Desktop navigation becomes mobile menu                  |
|        900 px | Result evidence stacks; profile and card grids simplify |
|        640 px | Phone shell, single-column actions and content          |
|        430 px | Small-phone acceptance target                           |

Required acceptance viewports:

- 1920 × 1080
- 1440 × 900
- 900 × 700
- 900 × 500
- 430 × 844

Also test 100%, 125%, and 200% browser zoom. At every size:

- `scrollWidth <= clientWidth`;
- no clipped text or overlapping actions;
- all content remains reachable;
- primary controls wrap rather than shrink below their target size;
- the footer remains legible;
- only intentional dialogs or sheets create nested scrolling.

## 9. Accessibility requirements

WCAG 2.2 AA is the minimum.

- Minimum interactive target: 44 × 44 CSS px.
- Global focus indicator: 3 px focus gold with 3 px offset.
- Maintain at least 4.5:1 text contrast and 3:1 non-text/control contrast.
- Include a “Skip to main content” link.
- Use semantic landmarks, heading order, lists, buttons, and links.
- Use `aria-current` for navigation and `aria-pressed` for toggles.
- Do not communicate status by color alone.
- Respect `prefers-reduced-motion`.
- Preserve complete keyboard operation and logical focus order.
- Require zero serious or critical automated axe findings plus manual keyboard review.

## 10. Product-state standards

Every tool must design and test:

- initial/empty;
- loading;
- populated;
- filtered;
- no result;
- partial or stale data;
- recoverable error;
- unavailable feature;
- offline/server failure;
- success confirmation.

Skeletons must match the eventual layout to avoid large shifts. Never leave a blank white
region as an error state.

## 11. Implementation contract

1. Import `tokens.css` once at the application root.
2. Build components against semantic tokens such as `--color-action-primary`, not raw
   hexadecimal values.
3. Keep institutional shell components separate from application-specific components.
4. Use CSS Grid and Flexbox with intrinsic sizing; avoid fixed content heights.
5. Keep one screen-level scroll path.
6. Reuse approved logo assets; do not convert logos to text.
7. Do not copy the Collaborator Finder’s search-specific components into unrelated tools.
   Reuse their principles, tokens, and accessibility behavior.

Recommended component boundary:

```text
InstitutionalShell
├── SkipLink
├── SiteHeader
│   ├── BrandLockup
│   ├── DesktopNavigation
│   └── MobileNavigation
├── MainContent
└── SiteFooter
    ├── InstitutionalBrandTier
    └── LegalTier
```

## 12. Quality checklist

Before a new product is considered visually aligned:

- [ ] UAB Medicine logo, product name, and division name use the approved hierarchy.
- [ ] Header and footer match the institutional shell dimensions and responsive behavior.
- [ ] Inter and the shared type scale are used.
- [ ] Warm canvas, white working surfaces, and semantic green states are used consistently.
- [ ] Raw hex colors are limited to the token source.
- [ ] Cards, controls, tags, and dialogs follow the shared geometry.
- [ ] No internal data-governance or implementation language is visible.
- [ ] All controls meet 44 × 44 px targets.
- [ ] Focus, hover, active, disabled, loading, empty, and error states are present.
- [ ] Required viewports and 200% zoom pass without horizontal scrolling.
- [ ] Keyboard navigation and screen-reader labels have been manually verified.
- [ ] Serious and critical axe violations are zero.
- [ ] UAB Medicine and University System brand assets have not been altered.

## 13. Governance

- Version this specification using semantic versioning.
- **Patch:** token correction or clarification without visible behavior change.
- **Minor:** backward-compatible component or pattern.
- **Major:** brand hierarchy, token meaning, breakpoint, or component contract change.
- Record product-specific exceptions in that product’s architecture decision log.
- Do not silently change shared tokens to fix one application. Confirm the change works
  across the shell, forms, cards, results, profiles, and mobile layouts first.
