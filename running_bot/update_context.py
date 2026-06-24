#!/usr/bin/env python3
"""
running_bot/update_context.py

Monthly job that reads the last 4 weekly reports, extracts key data,
and asks Claude to update athlete_context.md with anything that has changed
— new PBs, shifting patterns, updated goals, notable events.

The existing context file is preserved as the base; Claude only updates
what the evidence supports. Run date and source reports are appended as
a changelog at the bottom of the file.

Triggered by .github/workflows/update_athlete_context.yml on the 1st of each month.
"""

import os
import re
import json
import glob
import requests
from datetime import datetime
from pathlib import Path


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-6"
REPORTS_DIR       = Path(__file__).parent / "reports"
CONTEXT_FILE      = Path(__file__).parent / "athlete_context.md"


# ── Extract readable content from weekly report HTML ─────────────────────────

def extract_from_report(html_path: Path) -> dict:
    """
    Pull the headline, narrative, key signals, and hero stats from a
    weekly report HTML file. Uses simple regex — no HTML parser needed
    since we control the output format.
    """
    text = html_path.read_text(encoding="utf-8")
    date_str = html_path.stem.replace("report_", "")

    # AI headline (inside .ai-headline-text div)
    headline = ""
    m = re.search(r'class="ai-headline-text"[^>]*>(.*?)</div>', text, re.DOTALL)
    if m:
        headline = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # Narrative paragraphs (inside .narrative div)
    narrative = ""
    m = re.search(r'class="narrative"[^>]*>(.*?)</div>\s*<div class="signals"', text, re.DOTALL)
    if m:
        raw = m.group(1)
        # Extract text from each <p> tag
        paras = re.findall(r"<p>(.*?)</p>", raw, re.DOTALL)
        narrative = "\n\n".join(
            re.sub(r"<[^>]+>", "", p).strip() for p in paras
        )

    # Key signals (signal-label + signal-detail)
    signals = []
    for sm in re.finditer(
        r'class="signal-label">(.*?)</span>.*?class="signal-detail">(.*?)</div>',
        text, re.DOTALL
    ):
        label  = re.sub(r"<[^>]+>", "", sm.group(1)).strip()
        detail = re.sub(r"<[^>]+>", "", sm.group(2)).strip()
        if label and detail:
            signals.append(f"{label}: {detail}")

    # Next-week focus
    focus = ""
    m = re.search(r'class="nf-body"[^>]*>(.*?)</div>', text, re.DOTALL)
    if m:
        focus = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # Hero stats (hs-v values with their labels)
    stats = {}
    for hm in re.finditer(
        r'class="hs-l"[^>]*>(.*?)</span>.*?class="hs-v[^"]*"[^>]*>(.*?)</span>',
        text, re.DOTALL
    ):
        label = re.sub(r"<[^>]+>", "", hm.group(1)).strip()
        value = re.sub(r"<[^>]+>", "", hm.group(2)).strip()
        if label and value:
            stats[label] = value

    return {
        "date":      date_str,
        "headline":  headline,
        "narrative": narrative,
        "signals":   signals,
        "focus":     focus,
        "stats":     stats,
    }


def get_recent_reports(n: int = 4) -> list[dict]:
    """Return the n most recent weekly reports as extracted dicts."""
    files = sorted(REPORTS_DIR.glob("report_*.html"), reverse=True)
    # Exclude index.html
    files = [f for f in files if f.stem != "index"][:n]
    if not files:
        print("No report files found in running_bot/reports/")
        return []
    reports = []
    for f in files:
        try:
            data = extract_from_report(f)
            reports.append(data)
            print(f"  ✓ Extracted {f.name}")
        except Exception as e:
            print(f"  ⚠ Could not parse {f.name}: {e}")
    return reports


# ── Build update prompt ───────────────────────────────────────────────────────

def build_update_prompt(current_context: str, reports: list[dict]) -> str:
    report_summaries = []
    for r in reports:
        summary = f"### Week of {r['date']}\n"
        summary += f"**Headline:** {r['headline']}\n\n"
        if r["stats"]:
            summary += "**Stats:** " + " · ".join(
                f"{k}: {v}" for k, v in r["stats"].items()
            ) + "\n\n"
        if r["narrative"]:
            summary += f"**Narrative:**\n{r['narrative']}\n\n"
        if r["signals"]:
            summary += "**Key signals:**\n" + "\n".join(
                f"- {s}" for s in r["signals"]
            ) + "\n\n"
        if r["focus"]:
            summary += f"**Focus set:** {r['focus']}\n"
        report_summaries.append(summary)

    reports_text = "\n---\n".join(report_summaries)

    return f"""You maintain an athlete context file used to personalise weekly AI running analysis.

Below is the CURRENT athlete context file, followed by the LAST {len(reports)} WEEKLY REPORTS.

Your job is to produce an UPDATED version of the athlete context file that:
1. Reflects any new personal bests mentioned in the reports
2. Updates the training patterns section if volume or structure has clearly shifted
3. Updates the current goals if progress or new targets are evident from the reports
4. Adds or updates known limiters if recurring issues appear across multiple reports
5. Keeps everything that hasn't changed exactly as it is
6. Does NOT invent or speculate — only update what the reports explicitly support

At the very bottom of the file, append a new entry to the ## Update Log section (create it if it doesn't exist) in this format:
- **{datetime.now().strftime("%Y-%m-%d")}**: Updated from reports {", ".join(r["date"] for r in reports)}

Return ONLY the complete updated markdown file. No preamble, no explanation, no fences.

---

## CURRENT ATHLETE CONTEXT

{current_context}

---

## LAST {len(reports)} WEEKLY REPORTS

{reports_text}"""


# ── Call Claude ───────────────────────────────────────────────────────────────

def update_context_with_claude(current_context: str, reports: list[dict]) -> str:
    print("Calling Claude to update athlete context...")

    prompt = build_update_prompt(current_context, reports)

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key":         os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      MODEL,
            "max_tokens": 2000,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )
    resp.raise_for_status()
    updated = resp.json()["content"][0]["text"].strip()

    # Strip fences if present
    if updated.startswith("```"):
        updated = updated.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    return updated


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Athlete Context Updater — {datetime.now().strftime('%Y-%m-%d')} ===")

    if not CONTEXT_FILE.exists():
        print(f"✗ Context file not found: {CONTEXT_FILE}")
        return

    current_context = CONTEXT_FILE.read_text(encoding="utf-8")
    print(f"✓ Loaded {CONTEXT_FILE} ({len(current_context)} chars)")

    reports = get_recent_reports(n=4)
    if not reports:
        print("No reports to process — skipping update")
        return

    print(f"✓ Processing {len(reports)} reports")

    updated_context = update_context_with_claude(current_context, reports)

    # Sanity check — updated file should be at least 80% the length of the original
    if len(updated_context) < len(current_context) * 0.8:
        print(f"⚠ Updated context is suspiciously short ({len(updated_context)} vs "
              f"{len(current_context)} chars) — aborting to be safe")
        return

    CONTEXT_FILE.write_text(updated_context, encoding="utf-8")
    print(f"✓ athlete_context.md updated ({len(updated_context)} chars)")

    # Print the diff summary (lines changed)
    old_lines = set(current_context.splitlines())
    new_lines = set(updated_context.splitlines())
    added   = new_lines - old_lines
    removed = old_lines - new_lines
    print(f"  {len(added)} lines added, {len(removed)} lines changed/removed")


if __name__ == "__main__":
    main()
