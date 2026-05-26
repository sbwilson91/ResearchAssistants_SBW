# scraper/main.py

import os
import time
from datetime import datetime
from .feeds import load_config, fetch_papers
from .extract_repos import extract_all_repos
from .summarise import summarise_papers
from .report import generate_report, update_archive_index
from .trends import log_tag_counts, maybe_write_monthly_report
from .manifest import update_manifest
from .cluster import cluster_papers
from .organoid_intel import is_organoid_relevant, extract_organoid_intel, update_intel_log
from .llm import INTEL_SLEEP_S


def main():
    config    = load_config()

    print("Step 1/4: Fetching papers from RSS feeds...")
    papers = fetch_papers(config)

    if not papers:
        print("No new papers found this week. Exiting.")
        return

    print("\nStep 2/4: Extracting repository links from abstracts...")
    extract_all_repos(papers)

    print("\nStep 3/4: Generating summaries via GEMINI...")
    papers = summarise_papers(papers)

    # E1 — semantic clustering
    print("\nStep 3b: Clustering papers by topic...")
    papers = cluster_papers(papers)

    date_str     = datetime.now().strftime("%Y-%m-%d")

    # Step 3c — organoid intelligence extraction
    print("\nStep 3c: Extracting organoid intelligence...")
    intel_entries = []
    try:
        relevant = [p for p in papers if is_organoid_relevant(p)]
        print(f"  {len(relevant)} organoid-relevant paper(s) found")
        if relevant:
            print("  Waiting 60s for RPM window to reset after summarise…")
            time.sleep(60)
        for i, paper in enumerate(relevant):
            entry = extract_organoid_intel(paper)
            if entry:
                entry.update({
                    "title":   paper.title,
                    "url":     paper.url,
                    "doi":     getattr(paper, "doi", "") or "",
                    "journal": paper.journal,
                })
                intel_entries.append(entry)
            if i < len(relevant) - 1:
                time.sleep(INTEL_SLEEP_S)
        if intel_entries:
            update_intel_log(intel_entries, date_str)
            print(f"  → {len(intel_entries)} organoid intel entries extracted")
        else:
            print("  → No organoid-relevant papers extracted this week")
    except Exception as e:
        print(f"  ⚠ Organoid intel step failed: {e} — continuing without intel")
        intel_entries = []

    output_path  = f"digests/{date_str}-weekly-digest.md"

    print(f"\nStep 4/4: Building report → {output_path}")
    os.makedirs("digests", exist_ok=True)
    generate_report(papers, config, output_path, intel_entries=intel_entries)
    update_archive_index(output_path, paper_count=len(papers))

    # E2 — trend tracking
    log_tag_counts(papers, config)
    # E3 — update dashboard manifest
    digest_html_name = f"{date_str}-weekly-digest.html"
    update_manifest(papers, digest_html_name, config)
    
    maybe_write_monthly_report(config)

    print(f"  Done. Digest: {output_path}")
    print(f"  File exists: {os.path.isfile(output_path)}")
    print("\n✓ Done.")
    
if __name__ == "__main__":
    main()
