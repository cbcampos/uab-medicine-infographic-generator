from openai import AzureOpenAI
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()

print(f"Key: {api_key[:10]}...")
print(f"Endpoint: {endpoint}")

# Normalize endpoint
if endpoint.endswith("/v1"):
    endpoint = endpoint[:-3]
endpoint = endpoint.rstrip("/")
print(f"Normalized: {endpoint}")

client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-12-01-preview",
    azure_endpoint=endpoint,
    timeout=60,
)

print("Making request...")
try:
    resp = client.images.generate(
        model="gpt-image-2",
        prompt="A simple red square on white background",
        size="1024x1024",
        quality="high",
        n=1,
    )
    print(f"SUCCESS! Response: {resp}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")