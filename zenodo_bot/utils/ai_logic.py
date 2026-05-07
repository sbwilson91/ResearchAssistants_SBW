"""
utils/ai_logic.py

Drop-in replacement for the HuggingFace summarisation backend.
Swaps to Gemini 2.5 Flash via the native Gemini API using only `requests`
(already in requirements.txt — no new dependencies needed).

Interface is identical to the original: get_ai_summary(prompt, max_tokens)
so nothing else in the bot needs to change.

Secret required: GOOGLE_API_KEY (free, no credit card — get at aistudio.google.com)
"""

import os
import re
import time
import requests


_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

_SYSTEM_PROMPT = (
    "You are a scientific data analyst reviewing research datasets on Zenodo. "
    "Summarise the provided dataset description in 3-4 plain-English sentences. "
    "Cover: (1) what data or resource is provided, "
    "(2) the biological system, species, or conditions studied, "
    "(3) the methods or assays used to generate the data, and "
    "(4) its potential utility for single-cell or kidney/organoid research. "
    "Never invent information not present in the description."
)


def get_ai_summary(prompt: str, max_tokens: int = 500) -> str:
    """
    Generate a summary using Gemini 2.5 Flash.

    Args:
        prompt:     The full prompt string (same as before)
        max_tokens: Maximum output tokens (default 500)

    Returns:
        Generated text string

    Raises:
        requests.HTTPError on non-2xx responses (after retries)
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")

    clean_prompt = re.sub(r"<[^<]+?>", "", prompt).strip()[:1500]

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": clean_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.3,   # consistent, factual summaries
        },
    }

    # Simple retry with exponential backoff for transient 429s / 503s
    for attempt in range(3):
        resp = requests.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
            timeout=30,
        )

        if resp.status_code in (429, 503):
            wait = 2 ** attempt * 5   # 5s, 10s, 20s
            print(f"  Rate limited ({resp.status_code}), retrying in {wait}s…")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        break

    candidates = resp.json().get("candidates", [])
    if not candidates:
        return ""

    parts = candidates[0].get("content", {}).get("parts", [])
    return parts[0].get("text", "").strip() if parts else ""
