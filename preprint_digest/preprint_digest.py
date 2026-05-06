"""bioRxiv preprint digest bot.

Fetches recent bioRxiv preprints, filters by a user-defined watchlist,
generates AI summaries, groups papers by primary organ/tissue system,
writes a Markdown digest, and converts it to HTML for email delivery.
"""
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from fetcher import fetch_recent, filter_by_watchlist
from organ_classifier import ORGAN_KEYWORDS, classify_organ
from utils.ai_logic import get_ai_summary

HERE        = Path(__file__).parent
DIGESTS_DIR = HERE / "digests"
WATCHLIST   = HERE / "watchlist.txt"
DAYS_BACK   = int(os.environ.get("DAYS_BACK", 7))


def load_watchlist(path=WATCHLIST) -> list:
    """Read watchlist file; skip blank lines and # comments."""
    if not path.exists():
        print(f"Warning: {path} not found. Using empty watchlist.")
        return []
    topics = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            topics.append(stripped)
    return topics


def _format_paper(p) -> str:
    """Format a single Preprint as a Markdown paper card."""
    date_str   = p.date.strftime("%Y-%m-%d") if p.date.year > 1970 else "Date unknown"
    title_link = f"[{p.title}]({p.url})" if p.url else p.title
    authors_short = p.authors[:120] + "..." if len(p.authors) > 120 else p.authors
    category   = p.category.title() if p.category else "Preprint"
    summary    = p.summary if p.summary else "Summary unavailable."
    topic_tag  = f" | `{p.matched_topic}`" if p.matched_topic else ""

    return (
        f"### {title_link}\n"
        f"**{authors_short}** | {category} | {date_str}{topic_tag}\n\n"
        f"> {summary}\n"
    )


def build_digest(all_papers: list, today: str, total_fetched: int) -> str:
    """Build a Markdown digest grouped by organ/tissue system."""
    # Group papers by organ
    by_organ: dict = {}
    for p in all_papers:
        by_organ.setdefault(p.organ, []).append(p)

    # Sort within each organ newest-first
    for papers in by_organ.values():
        papers.sort(key=lambda p: p.date, reverse=True)

    # Render organs in ORGAN_KEYWORDS order, "General" last
    organ_order = list(ORGAN_KEYWORDS.keys()) + ["General"]
    organ_count = len(by_organ)

    lines = [
        f"# Preprint Digest — {today}",
        f"_{len(all_papers)} paper{'s' if len(all_papers) != 1 else ''} across "
        f"{organ_count} organ system{'s' if organ_count != 1 else ''} "
        f"({total_fetched} preprints scanned)_",
        "",
        "---",
        "",
    ]

    for organ in organ_order:
        if organ not in by_organ:
            continue
        papers = by_organ[organ]
        lines.append(f"## {organ}")
        lines.append("")
        for p in papers:
            lines.append(_format_paper(p))
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def run():
    topics = load_watchlist()
    if not topics:
        print("No topics in watchlist. Exiting.")
        return

    print(f"Watchlist: {topics}")

    # Fetch and filter
    preprints = fetch_recent(days_back=DAYS_BACK)
    matched   = filter_by_watchlist(preprints, topics)

    DIGESTS_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()

    if not matched:
        print("No preprints matched any watchlist topic.")
        content = (
            f"# Preprint Digest — {today}\n\n"
            f"_No preprints matched any watchlist topic this week "
            f"({len(preprints)} scanned)._\n"
        )
    else:
        # Flatten {topic: [papers]} → flat list, tagging each paper's matched_topic
        all_papers = []
        for topic, papers in matched.items():
            for p in papers:
                p.matched_topic = topic
                all_papers.append(p)

        print(f"Matched {len(all_papers)} preprints. Summarising...")

        # AI summarise
        for p in all_papers:
            if len(p.abstract) >= 50:
                p.summary = get_ai_summary(p.abstract)

        # Classify organ/tissue
        for p in all_papers:
            p.organ = classify_organ(p.title, p.abstract)

        from collections import Counter
        organ_dist = Counter(p.organ for p in all_papers)
        print(f"Organ distribution: {dict(organ_dist)}")

        content = build_digest(all_papers, today, len(preprints))

    # Save Markdown digest
    digest_path = DIGESTS_DIR / f"{today}-preprint-digest.md"
    digest_path.write_text(content, encoding="utf-8")
    print(f"Digest saved → {digest_path}")

    # Convert to HTML for email
    html_script = Path(__file__).parent / "utils" / "md_to_html_email.py"
    result = subprocess.run(
        [sys.executable, str(html_script), str(digest_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"HTML conversion failed: {result.stderr}")
    else:
        print(result.stdout.strip())


if __name__ == "__main__":
    run()
