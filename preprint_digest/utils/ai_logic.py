"""Gemini 2.5 Flash AI summarisation utility.

Drop-in replacement for the previous Ollama backend. The public function
`get_ai_summary(text, hf_token=None, model_url=None)` is unchanged so the
rest of the preprint_digest package continues to work without modification.

Secret required: GOOGLE_API_KEY (free — aistudio.google.com)
"""
import os
import re
import time
import requests

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)
MAX_RETRIES = 3

SYSTEM_PROMPT = (
    "You are a scientific literature analyst. "
    "Summarise the provided abstract in 4-5 plain-English sentences. "
    "Cover: (1) the biological or scientific question addressed, "
    "(2) the key methods or approach used, "
    "(3) the main findings, and "
    "(4) the significance or implications of the work. "
    "Never invent information not present in the abstract."
)


def _call_gemini(text: str) -> str:
    """Call Gemini 2.5 Flash with retry on transient errors."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")

    prompt = f"{SYSTEM_PROMPT}\n\nAbstract:\n{text[:1500]}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1024,
            "temperature": 0.2,
        },
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _GEMINI_URL,
                params={"key": api_key},
                json=payload,
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            last_error = f"Network error: {e}"
            wait = 5 * attempt
            print(f"  Gemini network error — waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                # Often a safety-filter block; surface it instead of silently failing.
                feedback = data.get("promptFeedback", {})
                raise RuntimeError(f"Gemini returned no candidates. Feedback: {feedback}")
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                finish_reason = candidates[0].get("finishReason", "unknown")
                raise RuntimeError(f"Gemini returned empty content (finishReason={finish_reason}).")
            return parts[0].get("text", "").strip()

        if resp.status_code in (429, 503):
            wait = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s
            print(f"  Gemini rate-limited ({resp.status_code}) — waiting {wait}s "
                  f"(attempt {attempt}/{MAX_RETRIES})...")
            time.sleep(wait)
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            continue

        # Non-retryable error
        raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text[:200]}")

    raise RuntimeError(f"Gemini failed after {MAX_RETRIES} attempts. Last error: {last_error}")


def get_ai_summary(text, hf_token=None, model_url=None):
    """Summarise text using Gemini 2.5 Flash.

    Args:
        text:      Raw text (HTML tags will be stripped). Truncated to 1500 chars.
        hf_token:  Ignored (kept for backwards-compatible signature).
        model_url: Ignored (kept for backwards-compatible signature).

    Returns:
        Summary string, or a descriptive fallback message on failure.
    """
    clean_text = re.sub(r"<[^<]+?>", "", text).strip()
    if len(clean_text) < 50:
        return "Description too short for summary."

    try:
        return _call_gemini(clean_text)
    except RuntimeError as e:
        print(f"  Gemini summary failed: {e}")

    return "AI summary unavailable."
