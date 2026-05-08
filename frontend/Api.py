import base64
import os
from typing import Any

import requests

DEFAULT_TIMEOUT = 300

BACKEND_HOST = os.environ.get("BACKEND_HOST", "localhost")
BACKEND_PORT = os.environ.get("BACKEND_PORT", "8000")
BASE_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"


def encode_file(file_content: bytes) -> str:
    return base64.b64encode(file_content).decode("utf-8")


def decode_base64(encoded_data: str) -> bytes:
    return base64.b64decode(encoded_data)


def post_request(
    path: str,
    payload: dict[str, Any],
    api_key: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[dict | None, str]:
    try:
        url = f"{BASE_URL.rstrip('/')}{path}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()

        return response.json(), ""

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return None, "Authentication failed: Invalid API key"
        elif e.response.status_code == 403:
            return None, "Access forbidden: Check your API key permissions"
        elif e.response.status_code == 422:
            return None, f"Validation error: {e.response.text}"
        return None, f"API error: {e}"
    except Exception as exc:
        return None, f"API error: {exc}"


def generate_infographic(
    file_content: bytes,
    file_extension: str,
    openai_compatible_endpoint: str,
    openai_compatible_model: str,
    api_key: str,
    style: str = "uab-craft-handmade",
    audience: str = "academic",
    user_context: str = "",
    size: str = "1792x1024",
    quality: str = "high",
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bytes | None, str, str]:
    payload = {
        "file_encoded": encode_file(file_content),
        "extension": file_extension,
        "style": style,
        "audience": audience,
        "user_context": user_context,
        "size": size,
        "quality": quality,
        "openai_compatible_endpoint": openai_compatible_endpoint,
        "openai_compatible_model": openai_compatible_model,
    }
    resp_json, error = post_request(
        "/v01/generate", payload, api_key, timeout=timeout
    )
    if resp_json is None:
        return None, "", error

    image_b64 = resp_json.get("image_b64")
    prompt_used = resp_json.get("prompt_used", "")
    if not image_b64:
        return None, "", "No image data received from the API."

    image_bytes = base64.b64decode(image_b64)

    if not image_bytes[:4] == b"\x89PNG":
        return None, "", "API returned invalid image data."

    return image_bytes, prompt_used, ""