"""
utils/ai_logic.py

Zenodo dataset summarisation. The Gemini transport, retry and error handling
live in common.summarise; what stays here is what makes this bot's summaries
its own — the system prompt, the token budget, and stripping the HTML that
Zenodo descriptions arrive wrapped in.

Interface is unchanged: get_ai_summary(prompt, max_tokens).

Secret required: GOOGLE_API_KEY (free, no credit card — get at aistudio.google.com)
"""

import re

from common.summarise import summarise

_SYSTEM_PROMPT = (
    "You are a scientific data analyst reviewing research datasets on Zenodo. "
    "Summarise the provided dataset description in 3-4 plain-English sentences. "
    "Cover: (1) what data or resource is provided, "
    "(2) the biological system, species, or conditions studied, "
    "(3) the methods or assays used to generate the data, and "
    "(4) its potential utility for single-cell or kidney/organoid research. "
    "Never invent information not present in the description."
)

_MAX_DESCRIPTION_CHARS = 1500


def get_ai_summary(prompt: str, max_tokens: int = 500) -> str:
    """
    Generate a summary using Gemini 2.5 Flash.

    Args:
        prompt:     The dataset description, HTML and all
        max_tokens: Maximum output tokens (default 500)

    Returns:
        Generated text string

    Raises:
        requests.HTTPError on non-2xx responses (after retries)
    """
    clean_prompt = re.sub(r"<[^<]+?>", "", prompt).strip()[:_MAX_DESCRIPTION_CHARS]

    return summarise(
        clean_prompt,
        system_prompt=_SYSTEM_PROMPT,
        max_tokens=max_tokens,
    )
