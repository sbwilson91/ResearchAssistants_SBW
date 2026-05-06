"""Zenodo scRNA-seq dataset discovery bot."""
import os
import re
import datetime
from pathlib import Path

import requests

from utils.email_logic import send_email
from utils.ai_logic import get_ai_summary

LOOKBACK_PERIOD = os.environ.get("LOOKBACK_PERIOD", "week")

HERE        = Path(__file__).parent
REPORTS_DIR = HERE / "reports"


def get_date_query(period):
    """Build a Zenodo date-range query string."""
    days = {"week": 7, "month": 30, "6months": 180}.get(period, 7)
    start_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    return f"[{start_date} TO *]"


def get_quick_stats(meta, files):
    """Extract species, tissue, cell counts from Zenodo metadata fields."""
    stats = []
    desc = re.sub(r"<[^<]+?>", "", meta.get("description", "")).lower()
    keywords = [k.lower() for k in meta.get("keywords", [])]

    species_patterns = {
        "Human": r"\b(human|homo sapiens|patient|hg38|grch38|pbmc)\b",
        "Mouse": r"\b(mouse|mus musculus|murine|mm10|mm39)\b",
        "Zebrafish": r"\b(zebrafish|danio rerio)\b",
        "Drosophila": r"\b(drosophila|fruit fly)\b",
        "Rat": r"\b(rat|rattus)\b",
    }
    for species, pattern in species_patterns.items():
        if re.search(pattern, desc) or any(re.search(pattern, k) for k in keywords):
            stats.append(f"Species: {species}")
            break

    cell_match = re.search(
        r"([\d,\.]+)\s*(million|thousand|k)?\s*(cells|nuclei|transcriptomes|samples)", desc
    )
    if cell_match:
        stats.append(f"Scale: ~{cell_match.group(0).strip()}")

    tissues = [
        "bone marrow", "brain", "lung", "liver", "kidney", "blood",
        "tumor", "skin", "heart", "intestine", "pbmc", "retina",
        "pancreas", "spleen", "thymus", "colon", "breast", "ovary",
    ]
    for tissue in tissues:
        if tissue in desc or tissue in " ".join(keywords):
            stats.append(f"Tissue: {tissue.title()}")
            break

    extensions = {}
    for f in files:
        ext = "." + f.get("key", "").rsplit(".", 1)[-1] if "." in f.get("key", "") else "other"
        extensions[ext] = extensions.get(ext, 0) + 1
    if extensions:
        file_summary = ", ".join(
            f"{count}x {ext}" for ext, count in sorted(extensions.items(), key=lambda x: -x[1])
        )
        stats.append(f"Files: {file_summary}")

    raw_keywords = meta.get("keywords", [])
    if raw_keywords:
        stats.append(f"Tags: {', '.join(raw_keywords[:5])}")

    return " | ".join(stats) if stats else "No metadata stats found."


def _wrap_standalone_html(body: str, title: str) -> str:
    """Wrap an email-style HTML fragment in a self-contained page for the dashboard."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 820px; margin: 32px auto; padding: 0 20px;
         color: #1f2937; background: #f9fafb; line-height: 1.55; }}
  h2 {{ color: #1e3a8a; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; }}
  h3 {{ color: #111827; margin: 6px 0; }}
  a  {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def run():
    """Query Zenodo for recent scRNA-seq datasets and email a report."""
    date_range = get_date_query(LOOKBACK_PERIOD)
    query = f"q=single cell RNA AND publication_date:{date_range} AND type:dataset"

    try:
        res = requests.get(
            f"https://zenodo.org/api/records?{query}&sort=mostrecent&size=10", timeout=20
        )
        res.raise_for_status()
        content_type = res.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            print(f"Zenodo returned non-JSON response (Content-Type: {content_type})")
            return
        hits = res.json().get("hits", {}).get("hits", [])
    except Exception as e:
        print(f"Zenodo API connection error: {e}")
        return

    if not hits:
        print("No new datasets found.")
        return

    n_datasets = len(hits)
    html_content = f"<h2>scRNA-seq Zenodo Report ({LOOKBACK_PERIOD})</h2>"
    html_content += f"<p><b>{n_datasets}</b> new dataset{'s' if n_datasets != 1 else ''} found.</p>"

    for hit in hits:
        meta = hit.get("metadata", {})
        links = hit.get("links", {})
        files = hit.get("files", [])

        record_url = links.get("html", f"https://zenodo.org/records/{hit.get('id', '')}")
        title = meta.get("title", "Untitled Dataset")
        stats = get_quick_stats(meta, files)
        summary = get_ai_summary(meta.get("description", ""))
        total_size = sum(f.get("size", 0) for f in files) / 1e6

        html_content += f"""
        <div style='border-bottom:1px solid #eee; padding:10px; margin-bottom:10px;'>
            <a href='{record_url}'><h3>{title}</h3></a>
            <p><b>AI Summary:</b> {summary}</p>
            <p><b>Quick Stats:</b> {stats}</p>
            <p><b>Details:</b> {len(files)} files | Total Size: {total_size:.1f} MB</p>
        </div>"""

    today = datetime.date.today().isoformat()

    # Persist HTML report so the dashboard can link to it
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{today}-zenodo.html"
    page = _wrap_standalone_html(html_content, f"Zenodo Report — {today}")
    report_path.write_text(page, encoding="utf-8")
    print(f"Report saved → {report_path}")

    # Email
    subject = f"scRNA-seq Data Alert: {today}"
    try:
        send_email(subject, html_content)
    except Exception as e:
        print(f"Email send failed: {e}")


if __name__ == "__main__":
    run()
