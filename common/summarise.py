"""Gemini summarisation transport, shared by every bot that summarises.

This module owns the parts that are the same everywhere — the endpoint, the
payload shape, retry, and how a response is unwrapped. It deliberately owns
none of the parts that differ: each bot passes its own system prompt and token
budget, because those differences are the bot's editorial voice, not accidents.
"""
import os

from common import http

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.3   # consistent, factual summaries
DEFAULT_TIMEOUT = 30


def summarise(
    text,
    *,
    system_prompt,
    max_tokens,
    model=DEFAULT_MODEL,
    temperature=DEFAULT_TEMPERATURE,
    api_key=None,
    timeout=DEFAULT_TIMEOUT,
    **retry_kwargs,
):
    """Summarise `text` under `system_prompt`, returning the generated string.

    Args:
        text:          The content to summarise. Callers clean and truncate it
                       to suit their source before passing it in.
        system_prompt: The bot's own instruction to the model.
        max_tokens:    The bot's own output budget.
        model:         Gemini model id.
        temperature:   Sampling temperature.
        api_key:       Defaults to the GOOGLE_API_KEY env var.
        timeout:       Per-request timeout in seconds.
        **retry_kwargs: Forwarded to common.http — retries, backoff, sleep,
                        session.

    Returns:
        The generated text, or "" when the model returned no candidates (it
        declined, or filtered its own output). An empty string means "nothing
        to say", never "the call failed" — a failed call raises.

    Raises:
        RuntimeError if no API key is available.
        requests.HTTPError / requests.RequestException if the call fails, after
        retries are exhausted.
    """
    api_key = api_key or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY environment variable is not set.")

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    response = http.post(
        _ENDPOINT.format(model=model),
        params={"key": api_key},
        json=payload,
        timeout=timeout,
        **retry_kwargs,
    )

    candidates = response.json().get("candidates", [])
    if not candidates:
        return ""

    parts = candidates[0].get("content", {}).get("parts", [])
    return parts[0].get("text", "").strip() if parts else ""
