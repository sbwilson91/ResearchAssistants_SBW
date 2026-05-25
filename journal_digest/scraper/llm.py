# scraper/llm.py
# Central LLM gateway — swap provider here when moving to a local model.
import os
import time
import requests

GEMINI_SLEEP_S = 6   # summarise loop
INTEL_SLEEP_S  = 12  # organoid intel loop — no rush, avoids rate limit burst

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def call_gemini(prompt: str, max_tokens: int = 600) -> str:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2,
        },
    }

    for attempt in range(3):
        resp = requests.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
            timeout=30,
        )
        if resp.status_code in (429, 503):
            wait = 2 ** attempt * 5
            print(f"  Gemini rate limit ({resp.status_code}), retry in {wait}s…")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    candidates = resp.json().get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return parts[0].get("text", "").strip() if parts else ""
