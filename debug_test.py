import sys
import os
sys.path.insert(0, '/Users/ccampos/CascadeProjects/infographic-generation')

print("Starting test...", flush=True)
from openai import AzureOpenAI
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
print(f"Loading .env from: {env_path}", flush=True)
load_dotenv(env_path)

api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()

print(f"Key length: {len(api_key)}", flush=True)
print(f"Endpoint: {endpoint}", flush=True)

# Normalize endpoint
if endpoint.endswith("/v1"):
    endpoint = endpoint[:-3]
endpoint = endpoint.rstrip("/")
print(f"Normalized endpoint: {endpoint}", flush=True)

print("Creating client...", flush=True)
client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-12-01-preview",
    azure_endpoint=endpoint,
    timeout=30,
)

print("Making request...", flush=True)
try:
    resp = client.images.generate(
        model="gpt-image-2",
        prompt="A simple red square",
        size="1024x1024",
        quality="high",
        n=1,
    )
    print(f"SUCCESS! Got response with {len(resp.data) if resp.data else 0} items", flush=True)
    if resp.data:
        img = resp.data[0]
        if hasattr(img, 'b64_json') and img.b64_json:
            print(f"Image data length: {len(img.b64_json)}", flush=True)
        elif hasattr(img, 'url') and img.url:
            print(f"Image URL: {img.url[:100]}...", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)[:200]}", flush=True)

print("Test complete", flush=True)