"""bioRxiv preprint digest bot.

Fetches recent bioRxiv preprints, filters by a user-defined watchlist,
generates AI summaries, groups papers by primary organ/tissue system,
writes a Markdown digest, converts it to HTML (persisted in digests/ for
the dashboard, plus /tmp for email), and emails the result.
"""
import os
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from fetcher import fetch_recent, filter_by_watchlist
from organ_classifier import ORGAN_KEYWORDS, classify_organ
from utils.ai_logic import get_ai_summary

# Pull in shared organoid intel module from the journal digest scraper
sys.path.insert(0, str(Path(__file__).parent.parent / "journal_digest" / "scraper"))
from organoid_intel import (  # noqa: E402
    is_organoid_relevant, extract_organoid_intel, update_intel_log, format_intel_section,
)
from llm import INTEL_SLEEP_S  # noqa: E402

HERE        = Path(__file__).parent
DIGESTS_DIR = HERE / "digests"
WATCHLIST   = HERE / "watchlist.txt"
DAYS_BACK   = int(os.environ.get("DAYS_BACK", 7))
EMAIL_HTML  = Path("/tmp/digest_email.html")

_PREPRINT_LOG_PATH  = HERE / "preprint_organoid_intel" / "log.json"
_PREPRINT_SEEN_PATH = HERE / "preprint_organoid_intel" / "seen_dois.json"


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


def build_digest(all_papers: list, today: str, total_fetched: int, intel_entries=None) -> str:
    by_organ: dict = {}
    for p in all_papers:
        by_organ.setdefault(p.organ, []).append(p)

    for papers in by_organ.values():
        papers.sort(key=lambda p: p.date, reverse=True)

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

    if intel_entries:
        lines += [
            "## 🧬 Preprint Intelligence",
            "",
            "> Sourced from bioRxiv / medRxiv. Findings are not yet peer-reviewed — "
            "treat as directional signals, not established results.",
            "> Low-confidence extractions are logged but not shown here.",
            "",
            format_intel_section(intel_entries),
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


def send_email(html_body: str, subject: str) -> None:
    """Send the digest via Gmail SMTP."""
    sender   = os.environ["EMAIL_SENDER"]
    receiver = os.environ["EMAIL_RECEIVER"]
    password = os.environ["EMAIL_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"Email sent → {receiver}")


def run():
    topics = load_watchlist()
    if not topics:
        print("No topics in watchlist. Exiting.")
        return

    print(f"Watchlist: {topics}")

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
        n_papers = 0
    else:
        all_papers = []
        for topic, papers in matched.items():
            for p in papers:
                p.matched_topic = topic
                all_papers.append(p)

        print(f"Matched {len(all_papers)} preprints. Summarising...")

        for i, p in enumerate(all_papers):
            if len(p.abstract) >= 50:
                p.summary = get_ai_summary(p.abstract)
            if i < len(all_papers) - 1:
                time.sleep(6)

        for p in all_papers:
            p.organ = classify_organ(p.title, p.abstract)

        from collections import Counter
        organ_dist = Counter(p.organ for p in all_papers)
        print(f"Organ distribution: {dict(organ_dist)}")

        # Organoid intelligence extraction
        print("Extracting preprint organoid intelligence...")
        intel_entries = []
        try:
            relevant = [p for p in all_papers if is_organoid_relevant(p)]
            print(f"  {len(relevant)} organoid-relevant preprint(s) found")
            if relevant:
                print("  Waiting 60s for RPM window to reset after summarise…")
                time.sleep(60)
            for i, p in enumerate(relevant):
                entry = extract_organoid_intel(p)
                if entry:
                    entry.update({
                        "title":   p.title,
                        "url":     p.url,
                        "doi":     p.doi or "",
                        "journal": p.category or "bioRxiv",
                        "source":  "bioRxiv",
                    })
                    intel_entries.append(entry)
                if i < len(relevant) - 1:
                    time.sleep(INTEL_SLEEP_S)
            all_intel = intel_entries
            if all_intel:
                update_intel_log(
                    all_intel, today,
                    log_path=_PREPRINT_LOG_PATH,
                    seen_path=_PREPRINT_SEEN_PATH,
                )
            # Filter low-confidence for digest display only
            intel_entries = [e for e in all_intel if e.get("confidence") != "low"]
            print(f"  → {len(all_intel)} extracted, {len(intel_entries)} medium/high shown")
        except Exception as e:
            print(f"  ⚠ Preprint intel step failed: {e} — continuing without intel")
            intel_entries = []

        content  = build_digest(all_papers, today, len(preprints), intel_entries=intel_entries)
        n_papers = len(all_papers)

    # Save Markdown digest
    digest_path = DIGESTS_DIR / f"{today}-preprint-digest.md"
    digest_path.write_text(content, encoding="utf-8")
    print(f"Digest saved → {digest_path}")

    # Convert to HTML (writes to /tmp/digest_email.html)
    html_script = HERE / "utils" / "md_to_html_email.py"
    result = subprocess.run(
        [sys.executable, str(html_script), str(digest_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"HTML conversion failed: {result.stderr}")
        return
    print(result.stdout.strip())

    # Persist HTML alongside the markdown so the dashboard can link to it
    if EMAIL_HTML.exists():
        html_archive = DIGESTS_DIR / f"{today}-preprint-digest.html"
        shutil.copy2(EMAIL_HTML, html_archive)
        print(f"HTML archived → {html_archive}")
    else:
        print(f"Expected HTML at {EMAIL_HTML} but file not found — skipping email.")
        return

    # Email the digest
    html_body = EMAIL_HTML.read_text(encoding="utf-8")
    subject = (
        f"Preprint Digest — {today} ({n_papers} paper{'s' if n_papers != 1 else ''})"
        if n_papers else f"Preprint Digest — {today} (no matches)"
    )

    try:
        send_email(html_body, subject)
    except KeyError as e:
        print(f"Email skipped — missing env var: {e}")
    except Exception as e:
        print(f"Email send failed: {e}")


if __name__ == "__main__":
    run()
