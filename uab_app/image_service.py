"""OpenAI/Azure/Gemini clients, image generation, fetch, and logo compositing."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import httpx
from openai import APITimeoutError, AzureOpenAI, OpenAI
from PIL import Image

from uab_app.constants import (
    AUDIENCE_SECTION_PLANS,
    BACKOFF_BASE_S,
    GEMINI_IMAGE_MODEL_ALIASES,
    GEMINI_OPENAI_BASE_URL,
    IMAGE_GEN_TIMEOUT_S,
    MAX_GENERATION_ATTEMPTS,
)


def normalize_azure_endpoint(endpoint: str) -> str:
    """Clean Azure endpoint URL - remove trailing /v1, /openai, slashes that cause 404."""
    if not endpoint:
        return endpoint
    # Strip trailing slashes
    endpoint = endpoint.rstrip("/")
    # Remove /v1 suffix if present (client appends /openai internally)
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    return endpoint


def normalize_gemini_image_model_id(raw: str) -> str:
    """Map marketing names → REST model IDs; strip optional models/ prefix."""
    m = (raw or "").strip()
    if m.startswith("models/"):
        m = m[len("models/"):].strip()
    key = m.lower().replace(" ", "-")
    return GEMINI_IMAGE_MODEL_ALIASES.get(key, m)


def resolve_logo_path() -> Optional[Path]:
    env_p = os.environ.get("UAB_MEDICINE_LOGO_PATH", "").strip()
    if env_p:
        p = Path(env_p).expanduser()
        if p.is_file():
            return p
    # package-aware default: cwd / assets (Streamlit sets cwd to app root)
    here = Path(__file__).resolve().parent.parent
    default = here / "assets" / "uab-medicine-logo.jpg"
    if default.is_file():
        return default
    return None


def make_client(
    provider: str,
    api_key: str,
    endpoint: Optional[str],
    api_version: Optional[str],
) -> OpenAI | AzureOpenAI:
    timeout = float(IMAGE_GEN_TIMEOUT_S)
    if provider == "openai":
        return OpenAI(api_key=api_key, timeout=timeout)
    if provider == "gemini":
        c = OpenAI(
            api_key=api_key,
            base_url=GEMINI_OPENAI_BASE_URL,
            timeout=timeout,
        )
        setattr(c, "_gemini_api_key", api_key)
        return c
    normalized = normalize_azure_endpoint(endpoint) if endpoint else ""
    print(
        "[DEBUG] Azure client created with endpoint: "
        f"{normalized}, api_version: {AZURE_API_VERSION_LOCKED}"
    )
    return AzureOpenAI(
        api_key=api_key,
        api_version=AZURE_API_VERSION_LOCKED,
        azure_endpoint=normalized,
        timeout=timeout,
    )


def _openai_size_map(size: str) -> str:
    m = {"1024x1024": "1024x1024", "1024x1792": "1024x1792", "1792x1024": "1792x1024"}
    return m.get(size, "1792x1024")


import logging

logger = logging.getLogger(__name__)


AZURE_API_VERSION_LOCKED = "2024-02-01"
AZURE_IMAGE_PROMPT_MAX_CHARS = 32000
AZURE_IMAGE_PROMPT_SAFETY_MARGIN = 200
AZURE_IMAGE_READ_TIMEOUT_S = 420

# Canonical app-side logo placement. The image model is still prompted to leave this
# area empty, but final output should not depend on whether it complies.
LOGO_SAFE_ZONE_WIDTH_RATIO = 0.285
LOGO_SAFE_ZONE_HEIGHT_RATIO = 0.09
LOGO_SAFE_ZONE_RIGHT_MARGIN_RATIO = 0.0
LOGO_SAFE_ZONE_BOTTOM_MARGIN_RATIO = 0.0
LOGO_SAFE_ZONE_MIN_WIDTH = 360
LOGO_SAFE_ZONE_MIN_HEIGHT = 92
LOGO_SAFE_ZONE_MAX_WIDTH_RATIO = 0.34
LOGO_SAFE_ZONE_MAX_HEIGHT_RATIO = 0.12
LOGO_SAFE_ZONE_PADDING_RATIO = 0.08
LOGO_SAFE_ZONE_LOGO_WIDTH_RATIO = 0.88
LOGO_SAFE_ZONE_LOGO_BOTTOM_PADDING_RATIO = 0.10
LOGO_SAFE_ZONE_LOGO_RIGHT_PADDING_RATIO = 0.03


def _trim_prompt_section(prompt: str, section_title: str, max_chars: int, note: str) -> str:
    """Trim one markdown section body while preserving heading and structure."""
    if max_chars <= 0:
        return prompt
    pattern = rf"(## {re.escape(section_title)}\n)(.*?)(?=\n## |\Z)"
    m = re.search(pattern, prompt, flags=re.DOTALL)
    if not m:
        return prompt
    body = (m.group(2) or "").strip()
    if len(body) <= max_chars:
        return prompt
    trimmed = body[:max_chars].rstrip()
    replacement = f"{m.group(1)}{trimmed}\n\n[{note}]"
    return prompt[: m.start()] + replacement + prompt[m.end() :]


def optimize_azure_image_prompt(prompt: str, max_prompt_len: int) -> str:
    """Reduce prompt size while preserving high-priority constraints."""
    if len(prompt) <= max_prompt_len:
        return prompt

    optimized = prompt
    # Trim lower-priority high-verbosity sections first.
    optimized = _trim_prompt_section(
        optimized,
        "Source Documents (Additional Context)",
        9000,
        "TRUNCATED: source documents shortened for Azure image prompt limit",
    )
    optimized = _trim_prompt_section(
        optimized,
        "User-Provided Context and Custom Content",
        5000,
        "TRUNCATED: user context shortened for Azure image prompt limit",
    )
    optimized = _trim_prompt_section(
        optimized,
        "Refinement Notes (if any)",
        1500,
        "TRUNCATED: refinement notes shortened for Azure image prompt limit",
    )
    if len(optimized) <= max_prompt_len:
        return optimized

    # If still too long, tighten those same sections further.
    optimized = _trim_prompt_section(
        optimized,
        "Source Documents (Additional Context)",
        5000,
        "TRUNCATED: source documents aggressively shortened",
    )
    optimized = _trim_prompt_section(
        optimized,
        "User-Provided Context and Custom Content",
        2500,
        "TRUNCATED: user context aggressively shortened",
    )
    optimized = _trim_prompt_section(
        optimized,
        "Refinement Notes (if any)",
        800,
        "TRUNCATED: refinement notes aggressively shortened",
    )
    if len(optimized) <= max_prompt_len:
        return optimized

    # Final guardrail: hard cap to guarantee request validity.
    return (
        optimized[:max_prompt_len].rstrip()
        + "\n\n[TRUNCATED: prompt shortened to fit Azure image request limit]"
    )


def generate_image(
    client: OpenAI | AzureOpenAI,
    provider: str,
    model: str,
    prompt: str,
    size: str,
    quality: str,
) -> str:
    print(f"[DEBUG] generate_image called: provider={provider}, model={model}, size={size}, quality={quality}")
    n = 1
    
    if provider == "openai":
        logger.info("Calling OpenAI images.generate...")
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            size=_openai_size_map(size),
            quality=quality,
            n=n,
        )
        logger.info("OpenAI images.generate completed")
    elif provider == "azure":
        max_prompt_len = AZURE_IMAGE_PROMPT_MAX_CHARS - AZURE_IMAGE_PROMPT_SAFETY_MARGIN
        prompt_to_send = optimize_azure_image_prompt(prompt, max_prompt_len)
        if len(prompt_to_send) != len(prompt):
            logger.info(
                "Azure prompt optimized from %s to %s chars",
                len(prompt),
                len(prompt_to_send),
            )
        prompt_hash = hashlib.sha256(prompt_to_send.encode("utf-8")).hexdigest()[:16]
        prompt_preview = prompt_to_send[:1200].replace("\n", "\\n")
        logger.info(
            "Azure prompt payload: len=%s sha256_prefix=%s preview=%s",
            len(prompt_to_send),
            prompt_hash,
            prompt_preview,
        )
        azure_size = size if size in ("1024x1024", "1024x1792", "1792x1024") else "1792x1024"
        azure_endpoint = str(getattr(client, "_azure_endpoint", "")).rstrip("/")
        # Empirically validated against this project's LiteLLM/Azure proxy: image generation
        # succeeds on the deployment path with api-version 2024-02-01.
        image_api_version = AZURE_API_VERSION_LOCKED
        req_url = (
            f"{azure_endpoint}/openai/deployments/{model}/images/generations"
            f"?api-version={image_api_version}"
        )
        req_body = {
            "prompt": prompt_to_send,
            "size": azure_size,
            "quality": quality,
            "output_compression": 100,
            "output_format": "png",
            "n": n,
        }
        timeout = httpx.Timeout(
            connect=20.0,
            read=float(AZURE_IMAGE_READ_TIMEOUT_S),
            write=30.0,
            pool=30.0,
        )
        print(
            "[DEBUG] Calling Azure deployment image endpoint: "
            f"deployment='{model}', size={azure_size}, quality='{quality}', api_version={image_api_version}"
        )
        try:
            with httpx.Client(timeout=timeout) as http_client:
                resp_http = http_client.post(
                    req_url,
                    headers={
                        "Authorization": f"Bearer {client.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=req_body,
                )
            if resp_http.status_code >= 400:
                msg = resp_http.text[:500].replace("\n", " ")
                raise RuntimeError(
                    f"Azure image generation failed (HTTP {resp_http.status_code}) "
                    f"for deployment '{model}': {msg}"
                )
            payload = resp_http.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                raise RuntimeError(
                    f"Azure image generation returned no image payload for deployment '{model}'."
                )
            first = data[0] if isinstance(data[0], dict) else {}
            b64 = first.get("b64_json")
            url = first.get("url")
            if b64:
                return f"data:image/png;base64,{b64}"
            if url:
                return str(url)
            raise RuntimeError(
                f"Azure image generation returned data without b64_json/url for deployment '{model}'."
            )
        except Exception as azure_err:
            logger.error(f"Azure image generation failed: {azure_err}")
            err_msg = str(azure_err)
            if "timed out" in err_msg.lower() or "readtimeout" in err_msg.lower():
                raise RuntimeError(
                    "Azure image generation timed out while waiting for the image payload. "
                    "Try medium quality or 1024x1024 for faster completion, or retry."
                ) from azure_err
            if "404" in err_msg or "Not Found" in err_msg:
                raise RuntimeError(
                    f"Azure image generation failed with 404. "
                    f"Deployment/model: '{model}'. "
                    f"This usually means the deployment doesn't exist or image generation isn't enabled. "
                    f"Verify in Azure portal: check the deployment name matches exactly and the resource has "
                    f"image generation capability."
                ) from azure_err
            raise
    else:
        model_id = normalize_gemini_image_model_id(model)
        req_body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
            data=json.dumps(req_body).encode("utf-8"),
            headers={
                "x-goog-api-key": str(getattr(client, "_gemini_api_key", "")),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=IMAGE_GEN_TIMEOUT_S) as resp_http:
            payload = json.loads(resp_http.read().decode("utf-8", errors="replace"))
        for cand in payload.get("candidates", []):
            content = cand.get("content") or {}
            for part in content.get("parts", []):
                inline = part.get("inlineData") or {}
                data_b64 = inline.get("data")
                mime = inline.get("mimeType", "image/png")
                if data_b64:
                    return f"data:{mime};base64,{data_b64}"
        raise RuntimeError("Gemini returned no image payload.")
    image_data = resp.data[0]
    if getattr(image_data, "url", None):
        return str(image_data.url)
    if getattr(image_data, "b64_json", None):
        return f"data:image/png;base64,{image_data.b64_json}"
    raise RuntimeError("No image payload returned.")


def user_friendly_error(exc: BaseException) -> str:
    if isinstance(exc, APITimeoutError):
        return "The image request timed out. Try again in a moment or simplify your prompt."
    if isinstance(exc, urllib.error.HTTPError) and getattr(exc, "code", None) == 404:
        return (
            "Generation failed: HTTP 404 (not found). "
            "**Azure:** verify your image deployment exists and the API version supports image generation. "
            "This app is locked to Azure API version `2024-02-01`. "
            "Ensure your subscription has the Azure OpenAI image generation capability enabled. "
            "**Gemini:** use an image-generation model ID such as "
            "`gemini-3-pro-image-preview` (Nano Banana Pro), "
            "`gemini-3.1-flash-image-preview`, or `gemini-2.5-flash-image`. "
            "**OpenAI:** verify the image model exists for your API key."
        )
    exc_str = str(exc)
    if "404" in exc_str or "Not Found" in exc_str:
        return (
            "Generation failed: 404 Not Found. This usually means the model/deployment name "
            "doesn't exist or image generation isn't enabled. Check your Azure portal to verify "
            "the deployment name matches exactly and the resource has image generation capability."
        )
    msg = exc_str.strip()
    if len(msg) > 220:
        msg = msg[:217] + "..."
    return f"Generation failed: {msg}"


def generate_with_retry(
    client: OpenAI | AzureOpenAI,
    provider: str,
    model: str,
    prompt: str,
    size: str,
    quality: str,
    progress_callback: Optional[Any] = None,
) -> str:
    logger.info(f"Starting generate_with_retry, max attempts: {MAX_GENERATION_ATTEMPTS}")
    last_err: Optional[BaseException] = None
    for attempt in range(MAX_GENERATION_ATTEMPTS):
        logger.info(f"Attempt {attempt + 1}/{MAX_GENERATION_ATTEMPTS}")
        try:
            if progress_callback:
                progress_callback(attempt)
            return generate_image(client, provider, model, prompt, size, quality)
        except BaseException as e:
            logger.error(f"Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            last_err = e
            emsg = str(e)
            # Validation errors should fail fast (retries won't help).
            if "string_above_max_length" in emsg or "Invalid 'prompt': string too long" in emsg:
                logger.error("Non-retryable prompt length validation error detected; failing fast.")
                raise
            if attempt < MAX_GENERATION_ATTEMPTS - 1:
                delay = BACKOFF_BASE_S * (2**attempt)
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
    assert last_err is not None
    logger.error(f"All {MAX_GENERATION_ATTEMPTS} attempts failed")
    raise last_err


def _vision_chat_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return str(content).strip()


def build_guided_refinement_notes(
    client: OpenAI | AzureOpenAI,
    vision_model: str,
    current_image_bytes: bytes,
    user_notes: str,
    audience: str,
) -> str:
    """Use vision to convert free-form notes into actionable, image-grounded edits."""
    img_b64 = base64.b64encode(current_image_bytes).decode("ascii")
    prompt = (
        "You are preparing an image refinement plan for an infographic generator.\n"
        f"Audience: {audience}\n"
        "Given the CURRENT IMAGE and USER REQUESTED CHANGES, output concise edit instructions.\n"
        "Return plain text with exactly these sections:\n"
        "KEEP:\n- ...\n"
        "CHANGE:\n- ...\n"
        "DO NOT CHANGE:\n- ...\n"
        "RULES:\n"
        "- Be specific about layout regions and text changes.\n"
        "- Preserve factual claims unless user explicitly asks to change them.\n"
        "- Avoid adding new unsupported numbers.\n"
        f"USER REQUESTED CHANGES:\n{(user_notes or '').strip()}\n"
    )
    resp = client.chat.completions.create(
        model=vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=700,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    return _vision_chat_message_text(content)


def _parse_refinements_scan_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    # Allow optional ```json fences
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
    brace = re.search(r"(\{[\s\S]*\})", text)
    if brace:
        try:
            data = json.loads(brace.group(1))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def normalize_refinements_scan(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce vision JSON into stable keys."""
    grade = str(data.get("letter_grade") or data.get("grade") or "").strip()
    refs = data.get("recommended_refinements") or data.get("refinements") or []
    if not isinstance(refs, list):
        refs = []
    refs = [str(x).strip() for x in refs if str(x).strip()]
    strengths = data.get("strengths") or []
    if not isinstance(strengths, list):
        strengths = []
    strengths = [str(x).strip() for x in strengths if str(x).strip()]
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    issues = [str(x).strip() for x in issues if str(x).strip()]
    summary = str(data.get("alignment_summary") or data.get("summary") or "").strip()
    fidelity = str(data.get("fidelity_notes") or data.get("source_fidelity") or "").strip()
    return {
        "letter_grade": grade or "?",
        "alignment_summary": summary,
        "strengths": strengths,
        "issues": issues,
        "recommended_refinements": refs,
        "fidelity_notes": fidelity,
    }


def format_refinements_scan_for_notes(scan: dict[str, Any]) -> str:
    """Turn scan output into text suitable for the refinement notes field."""
    s = normalize_refinements_scan(scan)
    lines: list[str] = [
        f"[Refinements scan — letter grade: {s['letter_grade']}]",
        "",
        "Suggested changes for the next generation:",
    ]
    for r in s["recommended_refinements"]:
        lines.append(f"- {r}")
    if not s["recommended_refinements"]:
        lines.append(
            "- (No specific refinements returned — re-run the scan or add manual notes.)"
        )
    return "\n".join(lines).strip()


def run_refinements_scan_vision(
    client: OpenAI | AzureOpenAI,
    vision_model: str,
    image_bytes: bytes,
    scan_context: dict[str, str],
) -> dict[str, Any]:
    """
    Vision review: compare the rendered infographic to user intent and source excerpts.
    Returns a dict with letter_grade, alignment_summary, strengths, issues,
    recommended_refinements, fidelity_notes.
    """
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    audience = scan_context.get("audience", "")
    audience_plan = AUDIENCE_SECTION_PLANS.get(audience, AUDIENCE_SECTION_PLANS["patient"])
    required_panel_title = str(audience_plan.get("required_panel_title") or "")
    ctx_lines = [
        "## Generation context (ground truth for alignment)",
        f"Audience: {audience}",
        f'Expected audience panel title: "{required_panel_title}"',
        f"Visual style: {scan_context.get('style_name', '')}",
        "## User-context / intent",
        scan_context.get("user_context", "")[:8000],
        "## Source excerpt (cleaned documents; do not invent beyond this)",
        scan_context.get("source_excerpt", "")[:12000],
        "## Chart reference excerpt (if any)",
        scan_context.get("chart_reference_excerpt", "")[:8000],
        "## Refinement notes used for this generation",
        scan_context.get("refinement_notes_used", "")[:4000],
        "## Effective image prompt excerpt (what the image model was asked)",
        scan_context.get("effective_prompt_excerpt", "")[:14000],
    ]
    instruction = (
        "You are a strict reviewer of an audience-specific medical/research infographic image.\n"
        "Read the attached infographic image carefully.\n"
        "Compare what is VISUALLY shown (headings, bullets, numbers, charts) to the user intent and "
        "source excerpt above.\n"
        "Flag missing required elements, missing expected audience panel title, wrong emphasis, audience mismatch, illegible text, clutter, "
        "duplicated or conflicting numbers, and any numbers/claims that are not supported by the excerpt.\n"
        "Ignore the bottom-right corner: a real logo may be composited there by the app; do not grade "
        "logo rendering quality.\n"
        "Return ONE JSON object ONLY (no markdown) with EXACTLY these keys:\n"
        '"letter_grade": string (choose one of: A+, A, A-, B+, B, B-, C+, C, C-, D, F)\n'
        '"alignment_summary": string (2-4 sentences)\n'
        '"strengths": array of strings (2-5 short bullets)\n'
        '"issues": array of strings (2-7 short bullets)\n'
        '"recommended_refinements": array of strings (5-12 concise, actionable instructions for the '
        "NEXT image generation; reference regions like TOP / LEFT / RIGHT / BOTTOM; do not repeat the whole brief)\n"
        '"fidelity_notes": string (optional; call out any numeric or citation mismatches you notice)\n'
    )
    user_text = instruction + "\n" + "\n".join(ctx_lines)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }
    ]
    resp = None
    try:
        resp = client.chat.completions.create(
            model=vision_model,
            messages=messages,
            temperature=0.15,
            max_tokens=1400,
            response_format={"type": "json_object"},
        )
    except BaseException:
        resp = client.chat.completions.create(
            model=vision_model,
            messages=messages,
            temperature=0.15,
            max_tokens=1400,
        )
    content = resp.choices[0].message.content if resp and resp.choices else ""
    raw = _vision_chat_message_text(content)
    parsed = _parse_refinements_scan_json(raw)
    if not parsed:
        raise RuntimeError(
            "Refinements scan did not return valid JSON. "
            f"Raw (truncated): {raw[:400]!r}"
        )
    return normalize_refinements_scan(parsed)


def fetch_image_bytes(image_url_or_data: str) -> bytes:
    if image_url_or_data.startswith("data:"):
        b64 = image_url_or_data.split(",", 1)[1]
        return base64.b64decode(b64)
    req = urllib.request.Request(image_url_or_data, headers={"User-Agent": "UAB-Infographic-Gen/1.0"})
    with urllib.request.urlopen(req, timeout=IMAGE_GEN_TIMEOUT_S) as resp:
        return resp.read()


def composite_logo_footer(image_bytes: bytes, logo_path: Path, style_key: str = "") -> bytes:
    """Paste the approved logo with a tight bottom-right white knockout."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    w, h = img.size

    safe_w = min(
        int(w * LOGO_SAFE_ZONE_MAX_WIDTH_RATIO),
        max(LOGO_SAFE_ZONE_MIN_WIDTH, int(w * LOGO_SAFE_ZONE_WIDTH_RATIO)),
    )
    safe_h = min(
        int(h * LOGO_SAFE_ZONE_MAX_HEIGHT_RATIO),
        max(LOGO_SAFE_ZONE_MIN_HEIGHT, int(h * LOGO_SAFE_ZONE_HEIGHT_RATIO)),
    )
    margin_x = max(int(w * LOGO_SAFE_ZONE_RIGHT_MARGIN_RATIO), 0)
    margin_y = max(int(h * LOGO_SAFE_ZONE_BOTTOM_MARGIN_RATIO), 0)
    safe_x0 = max(0, w - margin_x - safe_w)
    safe_y0 = max(0, h - margin_y - safe_h)
    safe_x1 = min(w, safe_x0 + safe_w)
    safe_y1 = min(h, safe_y0 + safe_h)

    pad = max(int(min(safe_w, safe_h) * LOGO_SAFE_ZONE_PADDING_RATIO), 8)
    max_logo_w = max(1, min(safe_w - (2 * pad), int(safe_w * LOGO_SAFE_ZONE_LOGO_WIDTH_RATIO)))
    max_logo_h = max(1, safe_h - (2 * pad))
    lw, lh = logo.size
    scale = min(max_logo_w / max(lw, 1), max_logo_h / max(lh, 1), 1.0)
    new_w, new_h = max(1, int(lw * scale)), max(1, int(lh * scale))
    logo_r = logo.resize((new_w, new_h), Image.Resampling.LANCZOS)

    right_pad = max(int(safe_w * LOGO_SAFE_ZONE_LOGO_RIGHT_PADDING_RATIO), 8)
    bottom_pad = max(int(safe_h * LOGO_SAFE_ZONE_LOGO_BOTTOM_PADDING_RATIO), 8)
    lx = max(safe_x0, safe_x1 - new_w - right_pad)
    ly = max(safe_y0, safe_y1 - new_h - bottom_pad)

    # Clear only the area needed by the final logo. This avoids a tall white block
    # covering lower-right content when the generated footer is already mostly usable.
    knockout_pad_x = max(int(new_w * 0.025), 8)
    knockout_pad_y = max(int(new_h * 0.14), 6)
    card_x0 = max(0, lx - knockout_pad_x)
    card_y0 = max(0, ly - knockout_pad_y)
    card_x1 = min(w, lx + new_w + knockout_pad_x)
    card_y1 = min(h, ly + new_h + knockout_pad_y)
    card = Image.new(
        "RGBA",
        (card_x1 - card_x0, card_y1 - card_y0),
        (255, 255, 255, 255),
    )
    img.alpha_composite(card, (card_x0, card_y0))

    img.paste(logo_r, (lx, ly), logo_r)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
