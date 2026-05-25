# scraper/report.py
from datetime import datetime, date
from .feeds import Paper
from collections import defaultdict
import re, os

def _matches_watchlist(paper: Paper, watchlist: list[str]) -> bool:
    """C1 — Return True if title or abstract contains any watchlist term."""
    haystack = (paper.title + " " + paper.abstract).lower()
    return any(term.lower() in haystack for term in watchlist)

def format_intel_section(intel_entries: list) -> str:
    if not intel_entries:
        return ""
    lines = ["---\n## 🧫 Organoid Intelligence\n"]
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


def generate_report(papers: list[Paper], config: dict, output_path: str, intel_entries=None) -> None:
    """
    Build the markdown digest.
    Watchlist-matching papers appear first under ## ⭐ Featured Papers,
    then the rest follow in their usual category sections.
    """
    watchlist = config.get("watchlist", [])
    featured = [
        p for p in papers
        if _matches_watchlist(p, watchlist) and _is_research_article(p)
    ]
    rest = [p for p in papers if p not in featured]

    today = date.today().isoformat()
    lines = [f"# Weekly Journal Digest — {today}\n",
             f"**{len(papers)} new papers** across "
             f"{len({p.journal for p in papers})} journals.\n"]

    # --- Organoid Intelligence section ---
    if intel_entries:
        lines.append(format_intel_section(intel_entries))

    # --- Featured section (C1) ---
    if featured:
        lines.append("---\n## ⭐ Featured Papers\n")
        lines.append(
            f"> Matched watchlist terms: "
            f"{', '.join(f'`{t}`' for t in watchlist)}\n"
        )
        for paper in featured:
            lines.append(_format_paper(paper))

    # --- Remaining papers by category ---
    clusters: dict[str, list[Paper]] = defaultdict(list)
    for paper in rest:
        label = paper.cluster_label or "General"
        clusters[label].append(paper)

    for label in sorted(clusters.keys()):
        lines.append(f"---\n## {label}\n")
        for paper in clusters[label]:
            lines.append(_format_paper(paper))

    content = "\n".join(lines)          # assign content first
    with open(output_path, "w") as f:
        f.write(content)
    print(f"  Report written to {output_path}")


def _format_paper(paper: Paper) -> str:
    """Render a single paper entry — matches your existing digest format."""
    sig_icon = {"High": "🔴 **High impact**",
                "Medium": "🟡 Medium",
                "Low": "⚪ Routine"}.get(
        getattr(paper, "significance", "Medium"), "🟡 Medium"
    )
    repos_line = ""
    if paper.repos:
        links = " · ".join(f"[repo]({r})" for r in paper.repos)
        repos_line = f"\n**Code/Data:** {links}"

    accession_line = ""
    acc = getattr(paper, "accession", "")
    if acc and str(acc).lower() not in ("null", "none", ""):
        accession_line = f"\n**Accession:** {acc}"

    topics_line = ""
    if paper.categories:
        topics_line = "\n**Topics:** " + " · ".join(
            f"`{c}`" for c in paper.categories
        )

    return (
        f"### {paper.title}\n"
        f"**{paper.journal}** | {paper.authors}  \n"
        f"*Published: {paper.published.strftime('%-d %b %Y')}* | {sig_icon}  \n"
        f"[Full paper]({paper.url})\n"
        f"\n> {paper.summary or 'Summary unavailable.'}\n"
        f"{topics_line}{repos_line}{accession_line}\n\n---\n"
    )


def update_archive_index(digest_path: str, paper_count: int) -> None:
    """
    C2 — Append a line to digests/index.md for the new digest.
    Creates the file with a header if it doesn't exist yet.
    """
    index_path = os.path.join(os.path.dirname(digest_path), "index.md")
    digest_name = os.path.basename(digest_path)
    today = date.today().isoformat()

    header = (
        "# Weekly Digest Archive\n\n"
        "| Date | Papers | File |\n"
        "|------|--------|------|\n"
    )

    html_name = digest_name.replace(".md", ".html")
    new_row = f"| {today} | {paper_count} | [{digest_name}]({html_name}) |\n"

    if not os.path.isfile(index_path):
        with open(index_path, "w") as f:
            f.write(header)

    with open(index_path, "a") as f:
        f.write(new_row)

    print(f"  Archive index updated: {index_path}")

# Patterns that indicate non-original-research content
_NON_RESEARCH_PATTERNS = re.compile(
    r"^(author correction|editorial|correction:|retraction|review:|"
    r"daily briefing|books in brief|news|comment|correspondence|"
    r"matters arising|research highlight|reply to|expression of concern|"
    r"briefing chat|audio long read|opinion|perspective|news feature|spotlight)",
    re.IGNORECASE
)

def _is_research_article(paper: Paper) -> bool:
    """Return False for reviews, corrections, news, editorials etc."""
    return not _NON_RESEARCH_PATTERNS.match(paper.title.strip())










