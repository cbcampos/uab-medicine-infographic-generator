"""Comprehensive Azure/LiteLLM image endpoint diagnostics.

Goal: identify a request shape/model that successfully returns an image payload.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


def normalize_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip().rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3].rstrip("/")
    return endpoint


def print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def looks_like_image_model(model_id: str) -> bool:
    m = (model_id or "").lower()
    image_markers = ("image", "dall", "gpt-image", "vision-image")
    return any(marker in m for marker in image_markers)


def extract_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            mid = item.get("id")
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
    return ids


def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")
    endpoint = normalize_endpoint(os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    env_model = os.getenv("AZURE_OPENAI_IMAGE_MODEL", "").strip()

    print_section("Config")
    print(f"endpoint: {endpoint}")
    print(f"api_key_len: {len(api_key)}")
    print(f"env_model: {env_model or '(not set)'}")

    if not endpoint or not api_key:
        print("Missing endpoint or API key in .env.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(connect=8.0, read=25.0, write=8.0, pool=8.0)

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        print_section("Host/Route Probes")
        route_checks = [
            ("GET /", "GET", f"{endpoint}/", None),
            ("GET /openapi.json", "GET", f"{endpoint}/openapi.json", None),
            ("GET /models", "GET", f"{endpoint}/models", headers),
            ("GET /v1/models", "GET", f"{endpoint}/v1/models", headers),
        ]
        models_from_api: list[str] = []

        for name, method, url, req_headers in route_checks:
            t0 = time.time()
            try:
                resp = client.request(method, url, headers=req_headers)
                dt = round(time.time() - t0, 2)
                print(f"{name:18} -> {resp.status_code} ({dt}s)")
                if "models" in name and resp.status_code == 200:
                    payload = parse_json(resp.text)
                    ids = extract_model_ids(payload)
                    if ids:
                        print(f"  models_count: {len(ids)}")
                        preview = ", ".join(ids[:8])
                        print(f"  models_preview: {preview}")
                        models_from_api.extend(ids)
            except Exception as exc:
                dt = round(time.time() - t0, 2)
                print(f"{name:18} -> ERROR {type(exc).__name__} ({dt}s): {exc}")

        print_section("Image Generation Matrix")
        base_models = [
            env_model,
            "gpt-image-2",
            "gpt-image-1",
            "dall-e-3",
            "image-gen",
            "proxied-model-router",
        ]
        model_candidates: list[str] = []
        for m in base_models + models_from_api:
            if isinstance(m, str) and m.strip():
                model_candidates.append(m.strip())

        # Keep insertion order and de-duplicate.
        deduped_models: list[str] = []
        seen: set[str] = set()
        for model_id in model_candidates:
            if model_id not in seen:
                deduped_models.append(model_id)
                seen.add(model_id)

        likely_image_models = [m for m in deduped_models if looks_like_image_model(m)]
        if not likely_image_models:
            likely_image_models = deduped_models[:8]

        routes = ["/images/generations", "/v1/images/generations"]
        qualities = ["low", "medium", "high"]
        sizes = ["1024x1024"]

        successful: list[dict[str, Any]] = []
        tested_count = 0
        max_tests = 24

        for route in routes:
            for model_id in likely_image_models:
                for quality in qualities:
                    for size in sizes:
                        if tested_count >= max_tests:
                            break
                        tested_count += 1
                        body = {
                            "model": model_id,
                            "prompt": "A simple red square centered on a white background.",
                            "size": size,
                            "quality": quality,
                            "n": 1,
                        }
                        url = endpoint + route
                        t0 = time.time()
                        try:
                            resp = client.post(url, headers=headers, json=body)
                            dt = round(time.time() - t0, 2)
                            payload = parse_json(resp.text)
                            msg = None
                            if isinstance(payload, dict):
                                err = payload.get("error")
                                if isinstance(err, dict):
                                    msg = err.get("message") or err.get("code")
                            ok = 200 <= resp.status_code < 300
                            if ok:
                                has_data = isinstance(payload, dict) and isinstance(payload.get("data"), list) and len(payload.get("data")) > 0
                                first = payload.get("data")[0] if has_data else {}
                                has_b64 = isinstance(first, dict) and bool(first.get("b64_json"))
                                has_url = isinstance(first, dict) and bool(first.get("url"))
                                print(
                                    f"OK   {resp.status_code} {dt:>5}s route={route} "
                                    f"model={model_id} quality={quality} b64={has_b64} url={has_url}"
                                )
                                successful.append(
                                    {
                                        "route": route,
                                        "model": model_id,
                                        "quality": quality,
                                        "status": resp.status_code,
                                        "elapsed_s": dt,
                                        "has_b64": has_b64,
                                        "has_url": has_url,
                                    }
                                )
                            else:
                                msg_short = (msg or resp.text[:120]).replace("\n", " ")
                                print(
                                    f"FAIL {resp.status_code} {dt:>5}s route={route} "
                                    f"model={model_id} quality={quality} msg={msg_short[:140]}"
                                )
                        except Exception as exc:
                            dt = round(time.time() - t0, 2)
                            print(
                                f"ERR  {type(exc).__name__} {dt:>5}s route={route} "
                                f"model={model_id} quality={quality} msg={str(exc)[:140]}"
                            )
                    if tested_count >= max_tests:
                        break
                if tested_count >= max_tests:
                    break
            if tested_count >= max_tests:
                break

        print_section("Summary")
        print(f"tested_cases: {tested_count}")
        print(f"successful_cases: {len(successful)}")
        if successful:
            best = min(successful, key=lambda x: x["elapsed_s"])
            print("best_success:", json.dumps(best, indent=2))
        else:
            print("No successful image responses found.")
            print(
                "Most likely root cause: proxy->Azure deployment mapping for image models "
                "is missing or not image-capable."
            )


if __name__ == "__main__":
    main()
