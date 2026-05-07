"""Citation intelligence bot using OpenAlex API."""
import os
import datetime
from pathlib import Path

import requests

from utils.email_logic import send_email
from utils.ai_logic import get_ai_summary

ORCID = "0000-0002-8994-0781"
LOOKBACK_PERIOD = os.environ.get("LOOKBACK_PERIOD", "week")
OPENALEX_BASE = "https://api.openalex.org"
OPENALEX_EMAIL = os.environ.get("EMAIL_SENDER", "")

HERE        = Path(__file__).parent
REPORTS_DIR = HERE / "reports"


def get_lookback_date(period):
    days = {"week": 7, "month": 30, "6months": 180}.get(period, 7)
    return (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return ""
    words = []
    for word, positions in inverted_index.items():
        for pos in positions:
            words.append((pos, word))
    words.sort(key=lambda x: x[0])
    return " ".join(w for _, w in words)


def get_author_work_ids():
    params = {
        "filter": f"author.orcid:{ORCID}",
        "select": "id,display_name",
        "per_page": 200,
        "mailto": OPENALEX_EMAIL,
    }
    try:
        res = requests.get(f"{OPENALEX_BASE}/works", params=params, timeout=20)
        res.raise_for_status()
        results = res.json().get("results", [])
    except Exception as e:
        print(f"OpenAlex author works query failed: {e}")
        return {}

    work_map = {}
    for work in results:
        full_id = work.get("id", "")
        short_id = full_id.split("/")[-1] if "/" in full_id else full_id
        work_map[short_id] = work.get("display_name", "Unknown")
    return work_map


def get_recent_citations(work_ids, since_date):
    if not work_ids:
        return []

    cites_filter = "|".join(work_ids)
    params = {
        "filter": f"cites:{cites_filter},from_publication_date:{since_date}",
        "select": "id,display_name,doi,publication_date,abstract_inverted_index,"
                  "concepts,topics,keywords,authorships",
        "per_page": 50,
        "sort": "publication_date:desc",
        "mailto": OPENALEX_EMAIL,
    }
    try:
        res = requests.get(f"{OPENALEX_BASE}/works", params=params, timeout=30)
        res.raise_for_status()
        data = res.json()
        total = data.get("meta", {}).get("count", 0)
        print(f"Found {total} new citations since {since_date}")
        return data.get("results", [])
    except Exception as e:
        print(f"OpenAlex citations query failed: {e}")
        return []


def tag_work(work, abstract_text):
    searchable_parts = [abstract_text.lower()]
    for concept in work.get("concepts", []):
        searchable_parts.append(concept.get("display_name", "").lower())
    for topic in work.get("topics", []):
        searchable_parts.append(topic.get("display_name", "").lower())
        for level in ("subfield", "field", "domain"):
            sub = topic.get(level, {})
            if sub:
                searchable_parts.append(sub.get("display_name", "").lower())
    for kw in work.get("keywords", []):
        searchable_parts.append(kw.get("display_name", "").lower())

    combined = " ".join(searchable_parts)

    tags = []
    if "microscopy" in combined or "microscope" in combined or "imaging" in combined:
        tags.append("Microscopy")
    if "transcriptom" in combined or "rna-seq" in combined or "single cell" in combined or "scrna" in combined:
        tags.append("Transcriptomics")

    return tags


def format_authors(authorships, max_authors=3):
    names = []
    for auth in authorships[:max_authors]:
        name = auth.get("author", {}).get("display_name", "Unknown")
        names.append(name)
    author_str = ", ".join(names)
    if len(authorships) > max_authors:
        author_str += " et al."
    return author_str


def get_all_publications() -> list:
    """Fetch all publications for the ORCID with full citation statistics."""
    params = {
        "filter": f"author.orcid:{ORCID}",
        "select": "id,display_name,publication_year,doi,cited_by_count,"
                  "primary_location,open_access,type,authorships",
        "per_page": 200,
        "sort": "cited_by_count:desc",
        "mailto": OPENALEX_EMAIL,
    }
    try:
        res = requests.get(f"{OPENALEX_BASE}/works", params=params, timeout=20)
        res.raise_for_status()
        return res.json().get("results", [])
    except Exception as e:
        print(f"OpenAlex publications query failed: {e}")
        return []


def _calc_h_index(works: list) -> int:
    counts = sorted((w.get("cited_by_count", 0) for w in works), reverse=True)
    h = 0
    for i, c in enumerate(counts):
        if c >= i + 1:
            h = i + 1
        else:
            break
    return h


def build_publications_page(works: list) -> str:
    """Generate a standalone HTML page listing all publications with citation stats."""
    today = datetime.date.today().isoformat()
    total_cites = sum(w.get("cited_by_count", 0) for w in works)
    h_index     = _calc_h_index(works)

    rows = []
    for rank, w in enumerate(works, 1):
        title  = w.get("display_name", "Untitled")
        doi    = w.get("doi", "")
        url    = doi or f"https://openalex.org/{w.get('id','').split('/')[-1]}"
        year   = w.get("publication_year", "")
        cites  = w.get("cited_by_count", 0)

        loc     = (w.get("primary_location") or {}).get("source") or {}
        journal = loc.get("display_name", "")

        oa_url = (w.get("open_access") or {}).get("oa_url", "")
        oa_tag = (f' <a href="{oa_url}" class="oa">OA</a>' if oa_url else "")

        badge_cls = (
            "ch" if cites >= 100 else
            "cm" if cites >= 20  else
            "cl" if cites > 0    else "cz"
        )
        rows.append(
            f'<tr>'
            f'<td class="rk">{rank}</td>'
            f'<td class="ti"><a href="{url}">{title}</a>{oa_tag}'
            f'<div class="jn">{journal}{" · " + str(year) if year else ""}</div></td>'
            f'<td class="ct"><span class="{badge_cls}">{cites}</span></td>'
            f'</tr>'
        )

    table = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Publications — ORCID {ORCID}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#f4f1ec;font-family:Georgia,serif;font-size:15px;line-height:1.65;color:#1a1a1a;padding:32px 16px 64px}}
.wrap{{max-width:860px;margin:0 auto}}
.mast{{border-top:4px solid #1a1a1a;border-bottom:1px solid #1a1a1a;padding:28px 0 20px;margin-bottom:36px}}
.label{{font-family:"Courier New",monospace;font-size:10px;letter-spacing:.25em;text-transform:uppercase;color:#666;margin-bottom:8px}}
.title{{font-family:"Courier New",monospace;font-size:14px;letter-spacing:.1em;text-transform:uppercase;color:#444}}
.orcid{{font-family:"Courier New",monospace;font-size:11px;color:#888;margin-top:4px}}
.orcid a{{color:#888}}
.stats{{display:flex;gap:32px;margin-top:20px;flex-wrap:wrap}}
.stat{{text-align:center}}
.stat-n{{font-family:"Courier New",monospace;font-size:28px;font-weight:700;color:#1a1a1a;display:block}}
.stat-l{{font-family:"Courier New",monospace;font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:#888;display:block;margin-top:2px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th{{font-family:"Courier New",monospace;font-size:9px;letter-spacing:.15em;text-transform:uppercase;color:#888;padding:8px 6px;border-bottom:2px solid #1a1a1a;text-align:left}}
th.ct{{text-align:right}}
td{{padding:12px 6px;border-bottom:1px solid #e5e1d8;vertical-align:top}}
td.rk{{font-family:"Courier New",monospace;font-size:11px;color:#aaa;width:28px;padding-top:14px}}
td.ti a{{color:#1a1a1a;text-decoration:none;font-size:15px;line-height:1.4}}
td.ti a:hover{{text-decoration:underline;color:#92400e}}
.jn{{font-family:"Courier New",monospace;font-size:11px;color:#888;margin-top:4px}}
.oa{{font-family:"Courier New",monospace;font-size:9px;letter-spacing:.1em;background:#d1fae5;color:#065f46;padding:1px 5px;border-radius:3px;text-decoration:none;margin-left:6px;vertical-align:middle}}
td.ct{{text-align:right;width:64px;padding-top:14px}}
.ch{{font-family:"Courier New",monospace;font-size:15px;font-weight:700;color:#92400e}}
.cm{{font-family:"Courier New",monospace;font-size:14px;font-weight:600;color:#b45309}}
.cl{{font-family:"Courier New",monospace;font-size:13px;color:#78716c}}
.cz{{font-family:"Courier New",monospace;font-size:12px;color:#a8a29e}}
.footer{{margin-top:48px;font-family:"Courier New",monospace;font-size:11px;color:#aaa;border-top:1px solid #ddd;padding-top:16px}}
</style>
</head>
<body>
<div class="wrap">
<div class="mast">
  <div class="label">Automated Research Intelligence</div>
  <div class="title">Publications</div>
  <div class="orcid">ORCID <a href="https://orcid.org/{ORCID}">{ORCID}</a></div>
  <div class="stats">
    <div class="stat"><span class="stat-n">{len(works)}</span><span class="stat-l">Papers</span></div>
    <div class="stat"><span class="stat-n">{total_cites:,}</span><span class="stat-l">Citations</span></div>
    <div class="stat"><span class="stat-n">{h_index}</span><span class="stat-l">h-index</span></div>
  </div>
</div>
<table>
  <thead><tr>
    <th></th>
    <th>Title · Journal · Year</th>
    <th class="ct">Cites</th>
  </tr></thead>
  <tbody>
{table}
  </tbody>
</table>
<div class="footer">Data from <a href="https://openalex.org">OpenAlex</a> · Updated {today}</div>
</div>
</body>
</html>"""


def _wrap_standalone_html(body: str, title: str) -> str:
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
  h2 {{ color: #92400e; border-bottom: 2px solid #f59e0b; padding-bottom: 8px; }}
  h3 {{ color: #111827; margin: 6px 0; }}
  a  {{ color: #d97706; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def run():
    since_date = get_lookback_date(LOOKBACK_PERIOD)

    # Build the publications page (always, independent of weekly citations)
    print("Fetching all publications for publications page...")
    all_works = get_all_publications()
    if all_works:
        REPORTS_DIR.mkdir(exist_ok=True)
        pub_path = REPORTS_DIR / "publications.html"
        pub_path.write_text(build_publications_page(all_works), encoding="utf-8")
        print(f"Publications page saved → {pub_path} ({len(all_works)} works)")

    author_works = get_author_work_ids()
    if not author_works:
        print("No works found for the target ORCID.")
        return

    print(f"Tracking {len(author_works)} works by ORCID {ORCID}")

    work_ids = list(author_works.keys())
    citations = get_recent_citations(work_ids, since_date)

    if not citations:
        print("No new citations found.")
        return

    n_citations = len(citations)
    html_content = (
        f"<h2>Citation Intelligence Report</h2>"
        f"<p>New citations of your work since {since_date} "
        f"(<b>{n_citations}</b> found)</p>"
        f"<hr>"
    )

    for work in citations:
        title = work.get("display_name", "Untitled")
        doi = work.get("doi", "")
        doi_link = doi if doi else "#"
        pub_date = work.get("publication_date", "Unknown date")
        authors = format_authors(work.get("authorships", []))

        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
        if abstract:
            summary = get_ai_summary(abstract)
        else:
            summary = "No abstract available."

        tags = tag_work(work, abstract)
        tag_html = ""
        if tags:
            tag_badges = " ".join(
                f"<span style='background:#{'3498db' if t == 'Microscopy' else '27ae60'};"
                f"color:white;padding:2px 8px;border-radius:3px;font-size:12px;'>{t}</span>"
                for t in tags
            )
            tag_html = f"<p>{tag_badges}</p>"

        html_content += f"""
        <div style='border-bottom:1px solid #eee; padding:10px; margin-bottom:10px;'>
            <a href='{doi_link}'><h3>{title}</h3></a>
            <p style='color:#666;'>{authors} | {pub_date}</p>
            {tag_html}
            <p><b>AI Summary:</b> {summary}</p>
        </div>"""

    today = datetime.date.today().isoformat()

    # Persist HTML report so the dashboard can link to it
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{today}-citation.html"
    page = _wrap_standalone_html(html_content, f"Citation Report — {today}")
    report_path.write_text(page, encoding="utf-8")
    print(f"Report saved → {report_path}")

    # Email
    subject = f"Citation Intelligence Report: {today}"
    try:
        send_email(subject, html_content)
    except Exception as e:
        print(f"Email send failed: {e}")


if __name__ == "__main__":
    run()
