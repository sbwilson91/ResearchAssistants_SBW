"""
utils/ai_logic.py

Citation abstract summarisation. The Gemini transport, retry and error handling
live in common.summarise; the system prompt and token budget stay here because
they are this bot's own editorial voice.

Interface is unchanged: get_ai_summary(prompt, max_tokens).

Secret required: GOOGLE_API_KEY (free, no credit card — get at aistudio.google.com)
"""

from common.summarise import summarise

_SYSTEM_PROMPT = (
    "You are a scientific literature analyst. "
    "Summarize the provided abstract in 4-5 plain-English sentences. "
    "Cover: (1) the biological or scientific question addressed, "
    "(2) the key methods or approach used, "
    "(3) the main findings, and "
    "(4) the significance or implications of the work. "
    "Never invent information not present in the abstract."
)


def get_ai_summary(prompt: str, max_tokens: int = 1024) -> str:
    """
    Generate a summary using Gemini 2.5 Flash.

    Args:
        prompt:     The abstract text to summarize
        max_tokens: Maximum output tokens (default 1024)

    Returns:
        Generated text string

    Raises:
        requests.HTTPError on non-2xx responses (after retries)
    """
    return summarise(
        prompt,
        system_prompt=_SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
