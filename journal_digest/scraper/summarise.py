"""
journal_digest/scraper/summarise.py

Swaps to Gemini 2.5 Flash. The public interface is unchanged — all other
scraper modules (main.py, report.py, etc.) continue to work without modification.

Secret required: GOOGLE_API_KEY (free — aistudio.google.com)
"""

import os
import time
import requests


_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

# Keywords that flag a paper as high-relevance for this researcher's field.
# Used to boost significance scoring in the prompt.
_WATCHLIST = [
    "organoid", "kidney", "assembloid", "bioprint", "single-cell",
    "scRNA-seq", "iPSC", "spatial transcriptomics", "CRISPR", "nephron",
    "podocyte", "proximal tubule", "vascularisation", "atlas", "ai",
]


def _gemini(prompt: str, max_tokens: int = 600) -> str:
    """
    Call Gemini 2.5 Flash with retry on transient errors.
    Returns generated text or empty string on failure.
    """
    api_key = os.environ["GOOGLE_API_KEY"]
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


def _watchlist_match(text: str) -> list[str]:
    """Return any watchlist terms found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in _WATCHLIST if kw.lower() in text_lower]


def summarise_paper(title: str, abstract: str) -> dict:
    """
    Summarise a single paper and assign a significance score.

    Returns a dict with keys:
        summary      — 2–3 sentence plain-English summary
        significance — "High" | "Medium" | "Low"
        tags         — list of matched watchlist terms
        takeaway     — one-sentence key finding
    """
    tags = _watchlist_match(f"{title} {abstract}")
    watchlist_note = (
        f"\nNOTE: This paper matches high-priority keywords: {', '.join(tags)}. "
        "Weight the significance score accordingly."
        if tags else ""
    )

    prompt = f"""You are a research assistant summarising scientific papers for a kidney organoid and single-cell biology researcher.

Title: {title}

Abstract: {abstract}{watchlist_note}

Respond ONLY with a JSON object — no markdown fences:
{{
  "summary": "2–3 sentence plain-English summary of the paper's contribution",
  "significance": "High|Medium|Low based on relevance to kidney organoids, single-cell RNA-seq, iPSC biology, or vascularisation",
  "takeaway": "One sentence: the single most important finding or method"
}}

Significance criteria:
  High   — directly relevant to kidney organoids, nephron biology, scRNA-seq methods, iPSC differentiation, or spatial transcriptomics
  Medium — relevant adjacent field (other organoids, other single-cell methods, developmental biology)
  Low    — general interest but not directly applicable"""

    import json
    raw = _gemini(prompt, max_tokens=400)

    try:
        # Strip fences if present despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        result["tags"] = tags
        return result
    except (json.JSONDecodeError, KeyError):
        # Fallback: return raw text as summary
        return {
            "summary":      raw[:300] if raw else "Summary unavailable.",
            "significance": "Medium",
            "takeaway":     "",
            "tags":         tags,
        }


def summarise_papers(papers: list) -> list:
    """
    Summarise a list of Paper objects, adding AI fields to each.
    Returns the same list with summary, significance, takeaway, and tags
    set as attributes on each Paper.
    """
    results = []
    for i, paper in enumerate(papers):
        title    = paper.title
        abstract = paper.abstract
        print(f"  [{i+1}/{len(papers)}] Summarising: {title[:60]}…")

        ai = summarise_paper(title, abstract)
        paper.summary      = ai.get("summary", "")
        paper.significance = ai.get("significance", "Medium")
        paper.takeaway     = ai.get("takeaway", "")
        results.append(paper)

        # Small delay between calls to stay comfortably within 10 RPM free tier limit
        if i < len(papers) - 1:
            time.sleep(6)

    return results


# ── Backwards-compatible single-call interface ────────────────────────────────
# The original summarise.py exposed get_ai_summary(prompt) in some bots.
# Keep this alias so any direct callers don't break.

def get_ai_summary(prompt: str, max_tokens: int = 500) -> str:
    return _gemini(prompt, max_tokens=max_tokens)
