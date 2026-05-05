# Azure GPT Image 2 Findings

Date: 2026-05-05

## What We Tested

- Proxy health endpoints (`/`, `/openapi.json`) and model listing (`/models`, `/v1/models`)
- OpenAI-compatible image routes:
  - `POST /images/generations`
  - `POST /v1/images/generations`
- Azure deployment-style route:
  - `POST /openai/deployments/gpt-image-2/images/generations?api-version=...`
- Text sanity check:
  - `POST /v1/chat/completions` with `gpt-4o`

## Observed Behavior

- Text models are healthy (`gpt-4o` returned `200` with valid completion).
- Model listing includes `gpt-image-2`.
- OpenAI-compatible image routes failed for `gpt-image-2` with LiteLLM `429` wrappers and upstream Azure `404 Resource not found`.
- Azure deployment-style image request succeeded with:
  - path: `/openai/deployments/gpt-image-2/images/generations`
  - api-version: `2024-02-01`
  - auth: `Authorization: Bearer <key>`
  - response payload containing `data[0].b64_json`.
- The same deployment-style path failed for:
  - `api-version=2024-02-01-preview`
  - `api-version=2024-12-01-preview`

## Implementation Decision

For Azure image generation in the Streamlit app, use the deployment-style HTTP call that worked in live testing:

- `POST {AZURE_ENDPOINT}/openai/deployments/{DEPLOYMENT}/images/generations?api-version=2024-02-01`
- Request body includes `prompt`, `size`, `quality`, `output_compression`, `output_format`, `n`.
- Parse `data[0].b64_json` first; fallback to `data[0].url` if present.

## Notes

- This is specific to the current LiteLLM/Azure proxy behavior observed in this environment.
- If backend routing changes, rerun `test_azure_image_comprehensive.py`.
