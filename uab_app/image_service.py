"""OpenAI/Azure/Gemini clients, image generation, fetch, and logo compositing."""

from __future__ import annotations

import base64
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
        timeout = float(IMAGE_GEN_TIMEOUT_S)
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


def fetch_image_bytes(image_url_or_data: str) -> bytes:
    if image_url_or_data.startswith("data:"):
        b64 = image_url_or_data.split(",", 1)[1]
        return base64.b64decode(b64)
    req = urllib.request.Request(image_url_or_data, headers={"User-Agent": "UAB-Infographic-Gen/1.0"})
    with urllib.request.urlopen(req, timeout=IMAGE_GEN_TIMEOUT_S) as resp:
        return resp.read()


def composite_logo_footer(image_bytes: bytes, logo_path: Path) -> bytes:
    """Paste approved logo onto a clean white footer strip (exact pixels from file)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    w, h = img.size
    footer_h = max(int(h * 0.11), 48)
    bar = Image.new("RGBA", (w, footer_h), (255, 255, 255, 255))
    lw, lh = logo.size
    pad = int(footer_h * 0.15)
    max_logo_h = footer_h - 2 * pad
    scale = min((w - 2 * pad) / lw, max_logo_h / lh, 1.0)
    new_w, new_h = int(lw * scale), int(lh * scale)
    logo_r = logo.resize((new_w, new_h), Image.Resampling.LANCZOS)
    lx = (w - new_w) // 2
    ly = footer_h - new_h - pad
    if ly < pad:
        ly = pad
    bar.paste(logo_r, (lx, ly), logo_r)
    out = Image.new("RGBA", (w, h + footer_h), (255, 255, 255, 255))
    out.paste(img, (0, 0))
    out.paste(bar, (0, h))
    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
