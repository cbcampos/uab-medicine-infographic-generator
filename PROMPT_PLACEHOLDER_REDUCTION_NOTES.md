# Prompt Placeholder Reduction Notes

Date: 2026-05-08

Purpose: trial prompt-only changes to reduce visible placeholder boxes, dashed logo areas,
and labeled reserved regions in Azure-generated infographics. The app-side logo compositor
still enforces final logo placement, so this experiment only changes what the image model is
asked to draw.

## Current Working Baseline

Before this experiment, logo placement worked because `composite_logo_footer` cleared a
fixed bottom-right white corner and pasted the approved UAB Medicine logo there. The remaining
visual issue was that the image model sometimes drew dashed or boxed "reserved" areas before
post-processing.

## Files Changed For This Experiment

- `uab_app/prompts.py`
  - Reworded `## UAB Medicine Logo Rules` to avoid terms that invite drawn containers:
    `reserve`, `rectangle`, `region`, `placeholder`, and explicit dimension language.
  - Added a global footer-layout instruction requiring a full-width white footer band:
    source/citation on the left, empty bottom-right corner for app-side logo compositing,
    and no panels/cards/illustrations/textures in the footer.
  - Changed incomplete-data chart instructions from "generate a placeholder box" to
    "omit the chart or use a concise evidence callout."
  - Changed the repeated artifact-spec incomplete-data instruction the same way.

- `uab_app/charts.py`
  - Changed the chart-reference wrapper so incomplete reference values do not ask for
    placeholder boxes unless an explicit `PLACEHOLDER` chart entry is present.
  - The manual placeholder chart path is still preserved: explicit placeholder entries are
    still emitted as `PLACEHOLDER: ...`.

- `uab_app/styles.py`
  - Changed the poster-classic style instruction from "image/photo placeholder areas" to
    "editorial image/photo areas only when useful visual content is available."

## Not Changed

- `uab_app/image_service.py`
  - The deterministic post-processing logo compositor remains active.
  - The current corner placement should continue to clear and paste the logo even if the
    model draws unwanted content in that corner.

- `uab_app/ui.py` and `generate_style_examples.py`
  - Existing logo layout notes from the corner-placement change remain as-is.

## Rollback Plan

If the new prompt wording makes outputs worse, revert only these prompt experiment files:

```bash
git checkout -- uab_app/prompts.py uab_app/charts.py uab_app/styles.py PROMPT_PLACEHOLDER_REDUCTION_NOTES.md
```

If you want to keep this documentation while reverting the prompt behavior, revert only:

```bash
git checkout -- uab_app/prompts.py uab_app/charts.py uab_app/styles.py
```

Do not revert `uab_app/image_service.py` unless you also want to undo the deterministic
bottom-right corner logo compositor.

## Test Criteria

- New generated infographic should not show a dashed or outlined reserved logo area.
- Bottom-right corner should look like natural white footer whitespace before compositing.
- The whole bottom edge should read as a clean white footer band across styles, similar to
  the prior watercolor, corporate, and technical outputs.
- The UAB Medicine logo should still appear cleanly in the bottom-right corner after compositing.
- Missing chart data should not produce visible "Exact values to be inserted..." boxes unless
  the user explicitly adds a placeholder chart entry.
