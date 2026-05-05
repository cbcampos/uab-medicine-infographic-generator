import os
from pathlib import Path
from dotenv import load_dotenv

# Check .env loading
env_path = Path(__file__).parent / ".env"
print(f"Env file exists: {env_path.exists()}")

load_dotenv(env_path)
api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")

print(f"API key: {api_key[:10] if api_key else 'EMPTY'}...")
print(f"Endpoint: {endpoint[:50] if endpoint else 'EMPTY'}...")

# Check if we can import openai
try:
    from openai import AzureOpenAI
    print("OpenAI imported successfully")
except Exception as e:
    print(f"Failed to import openai: {e}")