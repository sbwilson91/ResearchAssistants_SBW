#!/usr/bin/env python3
"""
build_dashboard.py  (repo root)

Reads the latest output from every bot and generates docs/index.html —
a unified hub page served via GitHub Pages.

Run by .github/workflows/build_dashboard.yml after any bot completes.
Also called locally with: python build_dashboard.py
"""

import re
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT  = Path(__file__).parent
DOCS       = REPO_ROOT / "docs"
DOCS.mkdir(exist_ok=True)

# ── helpers ──────────────────────────────────────────────────────────────────

def fmt_age(date_str: str) -> tuple[str, str]:
    """Return (human label, css colour) for a YYYY-MM-DD date string."""
    try:
        d   = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - d).days
        if   days == 0: return "today",            "#22c55e"
        elif days == 1: return "yesterday",         "#22c55e"
        elif days <= 7: return f"{days} days ago",  "#22c55e"
        elif days <= 14: return f"{days} days ago", "#f59e0b"
        else:            return f"{days} days ago", "#ef4444"
    except Exception:
        return date_str, "#64748b"


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def _inline_md(t: str) -> str:
    """Apply inline markdown formatting (bold, code, links)."""
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', t)
    return t


def _md_to_journal_html(md: str, title: str = "Weekly Journal Digest") -> str:
    """Convert a journal digest .md file to a standalone HTML page."""
    out, in_list = [], False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append('</ul>')
            in_list = False

    for line in md.splitlines():
        s = line.strip()
        if s.startswith('# ') and not s.startswith('##'):
            close_list(); out.append(f'<h1>{_inline_md(s[2:])}</h1>')
        elif s.startswith('## ') and not s.startswith('###'):
            close_list(); out.append(f'<h2>{_inline_md(s[3:])}</h2>')
        elif s.startswith('### '):
            close_list(); out.append(f'<h3>{_inline_md(s[4:])}</h3>')
        elif s == '---':
            close_list(); out.append('<hr>')
        elif s.startswith('> '):
            close_list(); out.append(f'<blockquote>{_inline_md(s[2:])}</blockquote>')
        elif s.startswith('- '):
            if not in_list:
                out.append('<ul>'); in_list = True
            out.append(f'<li>{_inline_md(s[2:])}</li>')
        elif s == '':
            close_list()
        else:
            close_list()
            css = 'meta' if re.match(r'^\*\*[^*]+\*\*', s) else ''
            if s:
                out.append(f'<p class="{css}">{_inline_md(s)}</p>')

    close_list()
    body = '\n'.join(out)

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title>'
        '<style>'
        '*{box-sizing:border-box;margin:0;padding:0}'
        'body{background:#f4f1ec;font-family:Georgia,serif;font-size:16px;line-height:1.7;color:#1a1a1a;padding:32px 16px 64px}'
        '.wrapper{max-width:700px;margin:0 auto}'
        '.masthead{border-top:4px solid #1a1a1a;border-bottom:1px solid #1a1a1a;padding:28px 0 20px;margin-bottom:40px}'
        '.label{font-family:"Courier New",monospace;font-size:10px;letter-spacing:.25em;text-transform:uppercase;color:#666;margin-bottom:8px}'
        '.title{font-family:"Courier New",monospace;font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#444}'
        'h1{font-size:28px;font-weight:normal;letter-spacing:-.02em;line-height:1.2;margin:48px 0 16px;padding-bottom:12px;border-bottom:2px solid #1a1a1a}'
        'h2{font-size:11px;font-family:"Courier New",monospace;font-weight:normal;letter-spacing:.2em;text-transform:uppercase;color:#fff;background:#1a1a1a;display:inline-block;padding:4px 12px;margin:40px 0 20px}'
        'h3{font-size:19px;font-weight:normal;line-height:1.35;color:#1a1a1a;margin:28px 0 10px}'
        'p{margin-bottom:10px;color:#2a2a2a}'
        'p.meta{font-family:"Courier New",monospace;font-size:12px;color:#555;margin:6px 0}'
        'blockquote{border-left:3px solid #c8b89a;margin:14px 0;padding:10px 16px;background:#faf8f5;font-style:italic;color:#3a3a3a;font-size:15px}'
        'ul{padding-left:0;list-style:none;margin:8px 0}'
        'ul li{font-family:"Courier New",monospace;font-size:12px;color:#444;padding:3px 0 3px 16px;position:relative}'
        'ul li::before{content:"\\2192";position:absolute;left:0;color:#c8b89a}'
        'a{color:#1a1a1a;text-decoration-color:#c8b89a}'
        'code{font-family:"Courier New",monospace;font-size:12px;background:#f0ede8;padding:1px 5px;border-radius:2px}'
        'hr{border:none;border-top:1px solid #ddd;margin:32px 0}'
        'strong{font-weight:600}'
        '</style></head><body><div class="wrapper">'
        '<div class="masthead">'
        '<div class="label">Automated Research Intelligence</div>'
        f'<div class="title">{title}</div>'
        '</div>'
        + body +
        '</div></body></html>'
    )


def _copy_archive(src_files: list[Path], dest_subdir: str, limit: int = 12) -> list[dict]:
    """Copy the latest N files into docs/<dest_subdir>/ and return archive entries."""
    arc_dir = DOCS / dest_subdir
    arc_dir.mkdir(exist_ok=True)
    entries = []
    for f in src_files[:limit]:
        shutil.copy2(f, arc_dir / f.name)
        # Use the leading YYYY-MM-DD if present, else the stem
        date_label = f.stem[:10] if re.match(r"\d{4}-\d{2}-\d{2}", f.stem) else f.stem
        entries.append({"date": date_label, "file": f"{dest_subdir}/{f.name}"})
    return entries


# ── per-bot extractors ────────────────────────────────────────────────────────

def extract_running_bot() -> dict:
    reports = sorted(
        (REPO_ROOT / "running_bot" / "reports").glob("report_*.html"),
        reverse=True
    )
    if not reports:
        return {"available": False}

    f    = reports[0]
    date = f.stem.replace("report_", "")
    text = f.read_text(encoding="utf-8")

    headline = ""
    m = re.search(r'class="ai-headline-text"[^>]*>(.*?)</div>', text, re.DOTALL)
    if m:
        headline = strip_tags(m.group(1))

    stats = {}
    for hm in re.finditer(
        r'class="hs-l"[^>]*>(.*?)</span>.*?class="hs-v[^"]*"[^>]*>(.*?)</span>',
        text, re.DOTALL
    ):
        k = strip_tags(hm.group(1))
        v = strip_tags(hm.group(2))
        if k and v and k not in stats:
            stats[k] = v

    # Copy latest report to docs/ for Pages serving
    shutil.copy(f, DOCS / "running.html")
    archive = _copy_archive(reports, "running_archive")

    return {
        "available": True,
        "date":      date,
        "headline":  headline,
        "stats":     stats,
        "link":      "running.html",
        "archive":   archive,
    }


def extract_journal_digest() -> dict:
    # Exclude index.md by only matching date-prefixed files
    digests = sorted(
        (REPO_ROOT / "journal_digest" / "digests").glob("[0-9][0-9][0-9][0-9]-*.md"),
        reverse=True
    )
    if not digests:
        return {"available": False}

    f    = digests[0]
    date = f.stem[:10]
    text = f.read_text(encoding="utf-8")

    # Papers use ### headings; ## headings are category sections
    paper_count = len(re.findall(r"^### ", text, re.MULTILINE))

    title_m = re.search(r"^# (.+)$", text, re.MULTILINE)
    title   = title_m.group(1).strip() if title_m else "Weekly Digest"

    paras   = [p.strip() for p in text.split("\n\n") if p.strip() and not p.startswith("#")]
    raw_preview = paras[0] if paras else ""
    preview = re.sub(r'\*\*(.+?)\*\*', r'\1', raw_preview)[:200] + "…" if raw_preview else ""

    # Convert latest digest to HTML for the dashboard link
    html_candidate = f.with_suffix(".html")
    if html_candidate.exists():
        shutil.copy(html_candidate, DOCS / "journal.html")
    else:
        (DOCS / "journal.html").write_text(
            _md_to_journal_html(text, title), encoding="utf-8"
        )

    # Convert all archived .md files to .html so archive links are browseable
    arc_dir = DOCS / "journal_digests"
    arc_dir.mkdir(exist_ok=True)
    entries = []
    for src in digests[:12]:
        dest_name = src.stem + ".html"
        dest = arc_dir / dest_name
        src_text = src.read_text(encoding="utf-8")
        title_m2 = re.search(r"^# (.+)$", src_text, re.MULTILINE)
        title2 = title_m2.group(1).strip() if title_m2 else "Weekly Digest"
        dest.write_text(_md_to_journal_html(src_text, title2), encoding="utf-8")
        entries.append({"date": src.stem[:10], "file": f"journal_digests/{dest_name}"})

    return {
        "available":   True,
        "date":        date,
        "title":       title,
        "paper_count": paper_count,
        "preview":     preview,
        "link":        "journal.html",
        "archive":     entries,
    }


def extract_preprint_digest() -> dict:
    """Look in preprint_digest/digests/ for both .md (count, title) and .html (link)."""
    digest_dir = REPO_ROOT / "preprint_digest" / "digests"

    md_files   = sorted(digest_dir.glob("*-preprint-digest.md"), reverse=True)
    html_files = sorted(digest_dir.glob("*-preprint-digest.html"), reverse=True)

    if not md_files and not html_files:
        return {"available": False}

    # Date and metadata come from the markdown if available
    if md_files:
        f    = md_files[0]
        date = f.stem[:10] if len(f.stem) >= 10 else f.stem
        text = f.read_text(encoding="utf-8")

        # Papers are ### headings; ## are organ section headings
        paper_count = len(re.findall(r"^### ", text, re.MULTILINE))

        title_m = re.search(r"^# (.+)$", text, re.MULTILINE)
        title   = title_m.group(1).strip() if title_m else "Preprint Digest"
    else:
        f    = html_files[0]
        date = f.stem[:10] if len(f.stem) >= 10 else f.stem
        title = "Preprint Digest"
        paper_count = 0

    # Prefer the HTML version for the dashboard link
    link = None
    if html_files:
        shutil.copy(html_files[0], DOCS / "preprint.html")
        link = "preprint.html"

    archive = _copy_archive(html_files or md_files, "preprint_digests")

    return {
        "available":   True,
        "date":        date,
        "title":       title,
        "paper_count": paper_count,
        "link":        link,
        "archive":     archive,
    }


def extract_zenodo_bot() -> dict:
    reports = sorted(
        (REPO_ROOT / "zenodo_bot" / "reports").glob("*-zenodo.html"),
        reverse=True
    )
    if not reports:
        return {"available": False}

    f    = reports[0]
    date = f.stem[:10] if len(f.stem) >= 10 else f.stem
    text = f.read_text(encoding="utf-8")

    # Extract dataset count from the "<b>N</b> new dataset" pattern we write
    n = 0
    m = re.search(r"<b>\s*(\d+)\s*</b>\s*new dataset", text)
    if m:
        n = int(m.group(1))
    else:
        # Fallback: count <h3> tags (one per dataset)
        n = len(re.findall(r"<h3>", text))

    shutil.copy(f, DOCS / "zenodo.html")
    archive = _copy_archive(reports, "zenodo_archive")

    return {
        "available":    True,
        "date":         date,
        "dataset_count": n,
        "link":         "zenodo.html",
        "archive":      archive,
    }


def extract_citation_bot() -> dict:
    reports = sorted(
        (REPO_ROOT / "citation_bot" / "reports").glob("*-citation.html"),
        reverse=True
    )
    if not reports:
        return {"available": False}

    f    = reports[0]
    date = f.stem[:10] if len(f.stem) >= 10 else f.stem
    text = f.read_text(encoding="utf-8")

    # Extract citation count from "(<b>N</b> found)" pattern we write
    n = 0
    m = re.search(r"\(\s*<b>\s*(\d+)\s*</b>\s*found\s*\)", text)
    if m:
        n = int(m.group(1))
    else:
        n = len(re.findall(r"<h3>", text))

    shutil.copy(f, DOCS / "citation.html")
    archive = _copy_archive(reports, "citation_archive")

    return {
        "available":      True,
        "date":           date,
        "citation_count": n,
        "link":           "citation.html",
        "archive":        archive,
    }


def read_status_file(bot_name: str) -> dict:
    status_file = DOCS / "status.json"
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text())
            return status.get(bot_name, {})
        except Exception:
            pass
    return {}


# ── dashboard HTML ─────────────────────────────────────────────────────────────

def bot_card(
    icon: str,
    title: str,
    schedule: str,
    date: str,
    content_html: str,
    link: str | None,
    accent: str,
    archive_html: str = "",
) -> str:
    age_label, age_color = fmt_age(date) if date else ("never", "#ef4444")

    link_btn = (
        f'<a href="{link}" class="card-btn">View latest report →</a>'
        if link else
        '<span class="card-btn-disabled">No report yet</span>'
    )

    return f"""
    <div class="card" style="--accent:{accent}">
      <div class="card-header">
        <div class="card-icon">{icon}</div>
        <div>
          <div class="card-title">{title}</div>
          <div class="card-schedule">{schedule}</div>
        </div>
        <div class="card-badge" style="color:{age_color}">{age_label}</div>
      </div>
      <div class="card-body">{content_html}</div>
      <div class="card-footer">
        {link_btn}
        {archive_html}
      </div>
    </div>"""


def archive_dropdown(items: list[dict], label: str = "Archive") -> str:
    if not items:
        return ""
    opts = "\n".join(
        f'<option value="{i["file"]}">{i["date"]}</option>'
        for i in items
    )
    return f"""
    <select class="archive-select" onchange="if(this.value)window.open(this.value,'_blank')">
      <option value="">{label} ▾</option>
      {opts}
    </select>"""


def build_html(running: dict, journal: dict, preprint: dict,
               zenodo: dict, citation: dict, generated_at: str) -> str:

    # ── Running Bot card ─────────────────────────────────────────
    if running["available"]:
        stats_html = "".join(
            f'<div class="stat"><span class="stat-k">{k}</span>'
            f'<span class="stat-v">{v}</span></div>'
            for k, v in list(running["stats"].items())[:6]
        )
        r_content = f"""
        <div class="headline">"{running['headline']}"</div>
        <div class="stats-row">{stats_html}</div>"""
        r_archive = archive_dropdown(running.get("archive", []), "Past reports")
        r_card = bot_card("🏃", "Running Bot", "Monday · Strava + Claude",
                          running["date"], r_content, running["link"],
                          "#f97316", r_archive)
    else:
        r_card = bot_card("🏃", "Running Bot", "Monday · Strava + Claude",
                          "", "<p class='no-data'>No reports yet.</p>",
                          None, "#f97316")

    # ── Journal Digest card ───────────────────────────────────────
    if journal["available"]:
        j_content = f"""
        <div class="digest-title">{journal['title']}</div>
        <div class="paper-count">{journal['paper_count']} papers summarised</div>
        <div class="preview">{journal['preview']}</div>"""
        j_archive = archive_dropdown(journal.get("archive", []), "Past digests")
        j_card = bot_card("📰", "Journal Digest", "Friday · Nature, Science, Cell + more",
                          journal["date"], j_content, journal.get("link"),
                          "#14b8a6", j_archive)
    else:
        j_card = bot_card("📰", "Journal Digest", "Friday · RSS feeds + Gemini",
                          "", "<p class='no-data'>No digests yet.</p>",
                          None, "#14b8a6")

    # ── Preprint Digest card ─────────────────────────────────────
    if preprint["available"]:
        p_content = f"""
        <div class="digest-title">{preprint['title']}</div>
        <div class="paper-count">{preprint['paper_count']} preprints reviewed</div>"""
        p_archive = archive_dropdown(preprint.get("archive", []), "Past digests")
        p_card = bot_card("📄", "Preprint Digest", "Thursday · bioRxiv + Gemini",
                          preprint["date"], p_content, preprint.get("link"),
                          "#8b5cf6", p_archive)
    else:
        p_card = bot_card("📄", "Preprint Digest", "Thursday · bioRxiv + Gemini",
                          "", "<p class='no-data'>No digests yet.</p>",
                          None, "#8b5cf6")

    # ── Zenodo Bot card ──────────────────────────────────────────
    if zenodo["available"]:
        z_content = f"""
        <div class="digest-title">scRNA-seq dataset alert</div>
        <div class="paper-count">{zenodo['dataset_count']} new dataset{'s' if zenodo['dataset_count'] != 1 else ''}</div>
        <div class="preview">Recent Zenodo deposits matching single-cell RNA-seq, with AI summaries and metadata stats.</div>"""
        z_archive = archive_dropdown(zenodo.get("archive", []), "Past reports")
        z_card = bot_card("🔬", "Zenodo Bot", "Monday · Zenodo API + Gemini",
                          zenodo["date"], z_content, zenodo["link"],
                          "#3b82f6", z_archive)
    else:
        z_card = bot_card("🔬", "Zenodo Bot", "Monday · Zenodo API + Gemini",
                          "", "<p class='no-data'>No reports yet.</p>",
                          None, "#3b82f6")

    # ── Citation Bot card ────────────────────────────────────────
    if citation["available"]:
        c_content = f"""
        <div class="digest-title">Citation intelligence</div>
        <div class="paper-count">{citation['citation_count']} new citation{'s' if citation['citation_count'] != 1 else ''}</div>
        <div class="preview">Papers citing your work via OpenAlex (ORCID 0000-0002-8994-0781), tagged by Microscopy / Transcriptomics.</div>"""
        c_archive = archive_dropdown(citation.get("archive", []), "Past reports")
        c_card = bot_card("📚", "Citation Bot", "Wednesday · OpenAlex + Gemini",
                          citation["date"], c_content, citation["link"],
                          "#f59e0b", c_archive)
    else:
        c_card = bot_card("📚", "Citation Bot", "Wednesday · OpenAlex + Gemini",
                          "", "<p class='no-data'>No reports yet.</p>",
                          None, "#f59e0b")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ResearchAssistants_SBW · Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,300;8..60,400;8..60,600&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#080b12;--sf:#0f1520;--card:#141926;--bdr:#1a2035;--tx:#dde2f0;--mu:#4a5270;--dim:#1e2540;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);color:var(--tx);font-family:'Source Serif 4',serif;font-weight:300;min-height:100vh;}}

  /* HEADER */
  .header{{background:linear-gradient(180deg,#0f1520 0%,#080b12 100%);border-bottom:3px solid #f97316;padding:44px 40px 36px;position:relative;overflow:hidden;}}
  .header::before{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 70% 60% at 60% 30%,rgba(249,115,22,.07) 0%,transparent 70%);pointer-events:none;}}
  .header-inner{{position:relative;max-width:1100px;margin:0 auto;}}
  .tag{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.18em;color:#f97316;text-transform:uppercase;margin-bottom:8px;}}
  h1{{font-family:'Playfair Display',serif;font-size:clamp(28px,5vw,48px);font-weight:700;color:#fff;line-height:1;margin-bottom:6px;}}
  h1 em{{color:#f97316;font-style:italic;}}
  .generated{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);margin-top:8px;}}

  /* SCHEDULE BAR */
  .schedule-bar{{max-width:1100px;margin:24px auto 0;display:flex;gap:8px;flex-wrap:wrap;}}
  .sched-item{{background:var(--card);border:1px solid var(--bdr);border-radius:6px;padding:6px 12px;font-family:'IBM Plex Mono',monospace;font-size:11px;}}
  .sched-day{{color:#f97316;margin-right:6px;}}
  .sched-bot{{color:var(--mu);}}

  /* GRID */
  .grid{{max-width:1100px;margin:32px auto;padding:0 40px 64px;display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;}}
  @media(max-width:600px){{.grid{{padding:0 16px 48px;}}}}

  /* CARDS */
  .card{{background:var(--card);border:1px solid var(--bdr);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;border-top:3px solid var(--accent,#f97316);}}
  .card-header{{padding:18px 20px 14px;display:flex;align-items:flex-start;gap:12px;border-bottom:1px solid var(--bdr);}}
  .card-icon{{font-size:22px;flex-shrink:0;margin-top:2px;}}
  .card-title{{font-family:'Source Serif 4',serif;font-size:16px;font-weight:600;color:var(--tx);}}
  .card-schedule{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);margin-top:3px;}}
  .card-badge{{font-family:'IBM Plex Mono',monospace;font-size:11px;margin-left:auto;flex-shrink:0;}}
  .card-body{{padding:18px 20px;flex:1;}}
  .card-footer{{padding:12px 20px 16px;border-top:1px solid var(--bdr);display:flex;align-items:center;gap:10px;flex-wrap:wrap;}}

  /* CARD CONTENT */
  .headline{{font-family:'Playfair Display',serif;font-style:italic;font-size:14px;color:#ccd4e8;line-height:1.5;margin-bottom:14px;}}
  .stats-row{{display:flex;flex-wrap:wrap;gap:6px;}}
  .stat{{background:var(--bg);border:1px solid var(--bdr);border-radius:6px;padding:6px 10px;}}
  .stat-k{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;display:block;}}
  .stat-v{{font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:500;color:var(--accent,#f97316);}}
  .digest-title{{font-size:14px;font-weight:600;color:var(--tx);margin-bottom:6px;}}
  .paper-count{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--accent,#14b8a6);margin-bottom:10px;}}
  .preview{{font-size:13px;color:var(--mu);line-height:1.65;}}
  .no-data{{font-size:13px;color:var(--mu);font-style:italic;}}

  /* FOOTER ELEMENTS */
  .card-btn{{background:rgba(249,115,22,.1);border:1px solid rgba(249,115,22,.3);color:#f97316;padding:6px 14px;border-radius:6px;font-family:'IBM Plex Mono',monospace;font-size:11px;text-decoration:none;transition:background .15s;}}
  .card-btn:hover{{background:rgba(249,115,22,.2);}}
  .card-btn-disabled{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);}}
  .archive-select{{background:var(--bg);border:1px solid var(--bdr);color:var(--mu);padding:5px 10px;border-radius:6px;font-family:'IBM Plex Mono',monospace;font-size:11px;cursor:pointer;}}
  .archive-select:hover{{border-color:var(--mu);}}
</style>
</head>
<body>

<div class="header">
  <div class="header-inner">
    <div class="tag">ResearchAssistants_SBW · Automated Intelligence Hub</div>
    <h1>Research <em>Dashboard</em></h1>
    <div class="schedule-bar">
      <div class="sched-item"><span class="sched-day">MON</span><span class="sched-bot">Zenodo · Running</span></div>
      <div class="sched-item"><span class="sched-day">WED</span><span class="sched-bot">Citation</span></div>
      <div class="sched-item"><span class="sched-day">THU</span><span class="sched-bot">Preprint</span></div>
      <div class="sched-item"><span class="sched-day">FRI</span><span class="sched-bot">Journal Digest</span></div>
      <div class="sched-item"><span class="sched-day">1st</span><span class="sched-bot">Athlete Context Update</span></div>
    </div>
    <div class="generated">Dashboard generated {generated_at}</div>
  </div>
</div>

<div class="grid">
  {r_card}
  {j_card}
  {p_card}
  {z_card}
  {c_card}
</div>

</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Building Dashboard — {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} ===")

    running  = extract_running_bot()
    journal  = extract_journal_digest()
    preprint = extract_preprint_digest()
    zenodo   = extract_zenodo_bot()
    citation = extract_citation_bot()

    print(f"  Running bot:      {'✓ ' + running['date']  if running['available']  else '✗ no reports'}")
    print(f"  Journal digest:   {'✓ ' + journal['date']  if journal['available']  else '✗ no digests'}")
    print(f"  Preprint digest:  {'✓ ' + preprint['date'] if preprint['available'] else '✗ no digests'}")
    print(f"  Zenodo bot:       {'✓ ' + zenodo['date']   if zenodo['available']   else '✗ no reports'}")
    print(f"  Citation bot:     {'✓ ' + citation['date'] if citation['available'] else '✗ no reports'}")

    generated_at = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")
    html = build_html(running, journal, preprint, zenodo, citation, generated_at)

    out = DOCS / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n✓ docs/index.html written ({len(html):,} chars)")


if __name__ == "__main__":
    main()
