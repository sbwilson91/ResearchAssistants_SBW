"""bioRxiv REST API fetcher for preprint_digest.

Fetches recent preprints via the public bioRxiv API (no authentication required).
API docs: https://api.biorxiv.org/
"""
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta


BIORXIV_API = "https://api.biorxiv.org/details/biorxiv/{start}/{end}/{cursor}"
PAGE_SIZE = 30


@dataclass
class Preprint:
    title:    str
    authors:  str
    doi:      str
    url:      str
    abstract: str
    date:     datetime
    category: str
    summary:       str = ""
    organ:         str = "General"
    matched_topic: str = ""


def fetch_recent(days_back=7) -> list:
    """Fetch all bioRxiv preprints posted in the past `days_back` days.

    Returns a list of Preprint objects sorted newest-first.
    """
    end_date   = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)
    start_str  = start_date.strftime("%Y-%m-%d")
    end_str    = end_date.strftime("%Y-%m-%d")

    print(f"  Fetching bioRxiv preprints from {start_str} to {end_str}...")

    preprints = []
    cursor = 0

    while True:
        url = BIORXIV_API.format(start=start_str, end=end_str, cursor=cursor)
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  bioRxiv API error at cursor {cursor}: {e}")
            break

        collection = data.get("collection", [])
        if not collection:
            break

        for item in collection:
            doi = item.get("doi", "")
            try:
                pub_date = datetime.strptime(item.get("date", "1970-01-01"), "%Y-%m-%d")
            except ValueError:
                pub_date = datetime(1970, 1, 1)

            preprints.append(Preprint(
                title    = item.get("title", "").strip(),
                authors  = item.get("authors", "").strip(),
                doi      = doi,
                url      = f"https://doi.org/{doi}" if doi else "",
                abstract = item.get("abstract", "").strip(),
                date     = pub_date,
                category = item.get("category", "").strip(),
            ))

        total = data.get("messages", [{}])[0].get("total", "?")
        print(f"  Fetched {len(preprints)} / {total} preprints...")

        if len(collection) < PAGE_SIZE:
            break
        cursor += PAGE_SIZE

    preprints.sort(key=lambda p: p.date, reverse=True)
    print(f"  Total preprints fetched: {len(preprints)}")
    return preprints


def filter_by_watchlist(preprints, topics) -> dict:
    """Filter preprints by watchlist topics.

    Each preprint is matched against title + abstract (case-insensitive substring).
    A preprint is assigned to the first matching topic only (deduplication).

    Returns:
        dict mapping topic -> list of matching Preprint objects.
    """
    matched: dict = {topic: [] for topic in topics}
    seen_dois: set = set()

    for topic in topics:
        term = topic.lower()
        for p in preprints:
            if p.doi in seen_dois:
                continue
            searchable = (p.title + " " + p.abstract).lower()
            if term in searchable:
                matched[topic].append(p)
                seen_dois.add(p.doi)

    # Remove topics with no matches
    return {t: ps for t, ps in matched.items() if ps}
