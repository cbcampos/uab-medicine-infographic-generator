"""Quick test script to find optimal Azure image generation parameters."""
import os
import time
from pathlib import Path
from openai import AzureOpenAI
from dotenv import load_dotenv

# Load from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()

print(f"Loaded API key: {api_key[:15]}...")
print(f"Loaded endpoint: {endpoint}")

# Strip /v1 if present
if endpoint.endswith("/v1"):
    endpoint = endpoint[:-3]
endpoint = endpoint.rstrip("/")

print(f"Using endpoint: {endpoint}")
print(f"API key: {api_key[:10]}...")

client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-12-01-preview",
    azure_endpoint=endpoint,
    timeout=180,
)

# Test different sizes
sizes = ["1024x1024", "1792x1024", "1024x1792"]
qualities = ["high", "medium"]

test_prompt = "A simple bar chart showing quarterly revenue: Q1 $50K, Q2 $75K, Q3 $60K, Q4 $90K. Clean white background."

for size in sizes:
    for quality in qualities:
        print(f"\n--- Testing size={size}, quality={quality} ---")
        try:
            start = time.time()
            resp = client.images.generate(
                model="gpt-image-2",
                prompt=test_prompt,
                size=size,
                quality=quality,
                n=1,
            )
            elapsed = time.time() - start
            print(f"SUCCESS! Time: {elapsed:.1f}s")
            if resp.data:
                img = resp.data[0]
                if hasattr(img, 'url'):
                    print(f"URL: {img.url[:80]}...")
                elif hasattr(img, 'b64_json'):
                    print(f"Got b64_json ({len(img.b64_json)} chars)")
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {str(e)[:200]}")