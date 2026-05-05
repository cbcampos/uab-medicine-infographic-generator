import os
import time
from pathlib import Path
from dotenv import load_dotenv
from openai import AzureOpenAI

# Load environment
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()

print(f"[DEBUG] API key loaded: {len(api_key)} chars")
print(f"[DEBUG] Endpoint loaded: {endpoint}")

# Normalize endpoint - remove /v1 suffix
if endpoint.endswith("/v1"):
    endpoint = endpoint[:-3]
endpoint = endpoint.rstrip("/")
print(f"[DEBUG] Normalized endpoint: {endpoint}")

# Create client
print("[DEBUG] Creating AzureOpenAI client...")
client = AzureOpenAI(
    api_key=api_key,
    api_version="2024-12-01-preview",
    azure_endpoint=endpoint,
    timeout=30.0,  # 30 second timeout
)
print("[DEBUG] Client created")

# Make a simple request with minimal prompt
print("[DEBUG] Making images.generate request...")
start_time = time.time()
try:
    response = client.images.generate(
        model="gpt-image-2",
        prompt="test",
        size="1024x1024",
        quality="high",
        n=1,
    )
    elapsed = time.time() - start_time
    print(f"[SUCCESS] Request completed in {elapsed:.2f} seconds")
    print(f"[DEBUG] Response type: {type(response)}")
    if hasattr(response, 'data') and response.data:
        print(f"[DEBUG] Got {len(response.data)} image(s)")
        if len(response.data) > 0:
            img = response.data[0]
            if hasattr(img, 'b64_json') and img.b64_json:
                print(f"[DEBUG] Image b64_json length: {len(img.b64_json)}")
            elif hasattr(img, 'url') and img.url:
                print(f"[DEBUG] Image URL: {img.url[:100]}...")
    else:
        print("[DEBUG] No data in response")
except Exception as e:
    elapsed = time.time() - start_time
    print(f"[ERROR] Request failed after {elapsed:.2f} seconds: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()