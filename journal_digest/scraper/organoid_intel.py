# scraper/organoid_intel.py
import json
from pathlib import Path

# Supports both package import (journal bot) and sys.path import (preprint bot)
try:
    from .llm import call_gemini
except ImportError:
    from llm import call_gemini  # noqa: F401 — standalone / cross-package

_LOG_PATH  = Path(__file__).parent.parent / "organoid_intel" / "log.json"
_SEEN_PATH = Path(__file__).parent.parent / "organoid_intel" / "seen_dois.json"

ORGANOID_KEYWORDS = [
    "organoid", "iPSC", "iPS cell", "induced pluripotent",
    "stem cell", "pluripotent", "assembloid", "tubuloid",
    "kidney", "podocyte", "proximal tubule", "nephron",
    "differentiation protocol", "scRNA-seq", "single cell",
    "single-cell", "cell atlas", "reference map", "reference atlas",
    "trajectory", "CellRank", "scVI", "scArches",
    "label transfer", "fidelity", "cell type annotation",
    "bioprint", "bioprinting", "organotypic",
]

_PROMPT_CONTEXT = (
    "You are a research assistant specialising in kidney organoids and single-cell "
    "genomics. The researcher's work focuses on the Human Kidney Organoid Cell Atlas "
    "(HKOCA): integrating iPSC-derived kidney organoid datasets using scVI, benchmarking "
    "cell type fidelity against the KPMP Adult and HCA Fetal reference atlases via "
    "scArches, and scoring off-target populations (neural, muscle). Extract knowledge "
    "from the paper abstract that could benefit this work. Respond only with valid JSON, "
    "no preamble."
)


def is_organoid_relevant(paper) -> bool:
    text = f"{paper.title} {paper.abstract}".lower()
    return any(kw.lower() in text for kw in ORGANOID_KEYWORDS)


def extract_organoid_intel(paper) -> dict | None:
    prompt = f"""{_PROMPT_CONTEXT}

Title: {paper.title}

Abstract: {paper.abstract}

Respond ONLY with a JSON object — no markdown fences:
{{
  "finding": "One sentence: what was actually discovered or shown",
  "relevance": "One sentence: why it matters for kidney organoid / HKOCA work",
  "category": "One of: protocol, fidelity, cell_type, atlas, integration_method, trajectory, off_target, bioprinting, other",
  "actionable": true or false,
  "action_detail": "If actionable, what specifically to consider doing — else empty string",
  "confidence": "high, medium, or low"
}}"""

    raw = call_gemini(prompt, max_tokens=500)
    try:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except (json.JSONDecodeError, KeyError):
        print(f"  ⚠ Intel extraction failed: {paper.title[:60]}")
        return None


def format_intel_section(intel_entries: list) -> str:
    """Return Markdown entry cards for intel entries. Caller adds the section header."""
    if not intel_entries:
        return ""
    lines = []
    for entry in intel_entries:
        actionable_line = (
            f"**Actionable:** ✅ {entry['action_detail']}"
            if entry.get("actionable")
            else "**Actionable:** ➖ No immediate pipeline action"
        )
        lines.append(
            f"### [{entry['title']}]({entry['url']}) · `{entry.get('category', 'other')}`\n\n"
            f"**Finding:** {entry.get('finding', '')}\n\n"
            f"**HKOCA relevance:** {entry.get('relevance', '')}\n\n"
            f"{actionable_line}\n\n"
            f"**Confidence:** {entry.get('confidence', 'medium')}\n\n---\n"
        )
    return "\n".join(lines)


def _load_seen(seen_path: Path) -> set:
    if seen_path.exists():
        return set(json.loads(seen_path.read_text()))
    return set()


def _save_seen(seen: set, seen_path: Path) -> None:
    seen_path.write_text(json.dumps(sorted(seen), indent=2))


def update_intel_log(entries: list, date_str: str,
                     log_path: Path = None, seen_path: Path = None) -> None:
    log_path  = log_path  or _LOG_PATH
    seen_path = seen_path or _SEEN_PATH

    seen = _load_seen(seen_path)
    log  = json.loads(log_path.read_text()) if log_path.exists() else []

    new_count = 0
    for entry in entries:
        doi   = entry.get("doi", "") or ""
        title = entry.get("title", "")
        key   = doi if doi else title

        if key in seen:
            continue

        record = {"date": date_str, **entry}
        log.append(record)
        seen.add(key)
        new_count += 1

    log_path.write_text(json.dumps(log, indent=2))
    _save_seen(seen, seen_path)
    print(f"  Log updated: {new_count} new entries ({len(log)} total)")
