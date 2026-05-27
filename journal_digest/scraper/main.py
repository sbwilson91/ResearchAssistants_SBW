# scraper/main.py

import os
from datetime import datetime
from .feeds import load_config, fetch_papers
from .extract_repos import extract_all_repos
from .summarise import summarise_papers
from .report import generate_report, update_archive_index
from .trends import log_tag_counts, maybe_write_monthly_report
from .manifest import update_manifest
from .cluster import cluster_papers


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
    output_path  = f"digests/{date_str}-weekly-digest.md"

    print(f"\nStep 4/4: Building report → {output_path}")
    os.makedirs("digests", exist_ok=True)
    generate_report(papers, config, output_path)
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
