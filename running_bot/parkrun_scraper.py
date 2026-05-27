"""
Fetch parkrun athlete results (position + age grade) from the public profile page.

Results are cached to data/parkrun_cache.json and refreshed if >12h old.
"""
import json
import re
import time
from datetime import datetime, date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ATHLETE_URL  = "https://www.parkrun.org.uk/parkrunner/{athlete_id}/all/"
CACHE_PATH   = Path(__file__).parent / "data" / "parkrun_cache.json"
CACHE_MAX_AGE_H = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; running-bot/1.0; research use)"}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2))


def _parse_time(t: str) -> int | None:
    """Convert 'MM:SS' or 'H:MM:SS' to seconds."""
    try:
        parts = t.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        pass
    return None


def _parse_date(d: str) -> str | None:
    """Normalise parkrun date formats to YYYY-MM-DD."""
    for fmt in ("%d/%m/%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(d.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_age_grade(ag: str) -> float | None:
    try:
        return round(float(ag.strip().rstrip("%")), 2)
    except Exception:
        return None


def _scrape(athlete_id: str) -> list[dict]:
    url = ATHLETE_URL.format(athlete_id=athlete_id)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"  parkrun fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the results table — parkrun uses <table class="Results-table"> or id="results"
    table = (
        soup.find("table", {"id": "results"})
        or soup.find("table", class_=re.compile(r"Results", re.I))
        or soup.find("table")
    )
    if not table:
        print("  parkrun: no results table found in page")
        return []

    # Identify column indices from header row
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    col = {}
    for i, h in enumerate(headers):
        if "event" in h:           col["event"]     = i
        elif "date" in h:          col["date"]      = i
        elif "pos" in h:           col["position"]  = i
        elif "time" in h:          col["time"]      = i
        elif "age" in h and "g" in h: col["age_grade"] = i
        elif "run" in h or "#" == h:  col["run_num"]   = i

    rows = table.find("tbody").find_all("tr") if table.find("tbody") else table.find_all("tr")[1:]

    results = []
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        def _cell(key):
            idx = col.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        raw_date     = _cell("date")
        raw_time     = _cell("time")
        raw_pos      = _cell("position")
        raw_ag       = _cell("age_grade")
        raw_event    = _cell("event")
        raw_run_num  = _cell("run_num")

        date_str = _parse_date(raw_date)
        if not date_str:
            continue

        entry = {
            "date":       date_str,
            "event":      raw_event or "Unknown",
            "time_s":     _parse_time(raw_time),
            "time_str":   raw_time.strip(),
            "position":   int(re.sub(r"\D", "", raw_pos)) if re.sub(r"\D", "", raw_pos) else None,
            "age_grade":  _parse_age_grade(raw_ag),
            "run_num":    int(re.sub(r"\D", "", raw_run_num)) if re.sub(r"\D", "", raw_run_num) else None,
            "is_pb":      bool(row.find(class_=re.compile(r"pb|personal.best", re.I))
                               or "PB" in cells),
        }
        results.append(entry)

    results.sort(key=lambda r: r["date"], reverse=True)
    return results


def fetch_parkrun_results(athlete_id: str) -> list[dict]:
    """Return cached or freshly scraped parkrun results for the given athlete."""
    cache = _load_cache()
    now   = datetime.utcnow()

    cached_at = cache.get("fetched_at")
    if cached_at and cache.get("athlete_id") == athlete_id:
        age_h = (now - datetime.fromisoformat(cached_at)).total_seconds() / 3600
        if age_h < CACHE_MAX_AGE_H:
            print(f"  parkrun: using cache ({int(age_h)}h old, {len(cache['results'])} runs)")
            return cache["results"]

    print(f"  parkrun: fetching {athlete_id} from parkrun.org.uk…")
    results = _scrape(athlete_id)

    if results:
        _save_cache({
            "athlete_id": athlete_id,
            "fetched_at": now.isoformat(),
            "results":    results,
        })
        print(f"  parkrun: {len(results)} results cached")
    else:
        print("  parkrun: scrape returned no results — using stale cache if available")
        results = cache.get("results", [])

    return results


def summarise_parkrun(results: list[dict]) -> dict:
    """Derive summary stats from the full results list."""
    if not results:
        return {}

    valid_ag  = [r["age_grade"] for r in results if r["age_grade"]]
    valid_pos = [r["position"]  for r in results if r["position"]]
    valid_t   = [r["time_s"]    for r in results if r["time_s"]]

    recent10 = results[:10]

    return {
        "total_runs":       len(results),
        "recent":           results,
        "best_age_grade":   max(valid_ag)  if valid_ag  else None,
        "avg_age_grade_10": round(sum(r["age_grade"] for r in recent10 if r["age_grade"])
                                  / sum(1 for r in recent10 if r["age_grade"]), 2)
                            if any(r["age_grade"] for r in recent10) else None,
        "best_position":    min(valid_pos) if valid_pos else None,
        "best_time_s":      min(valid_t)   if valid_t   else None,
        "age_grade_trend":  _age_grade_trend(recent10),
    }


def _age_grade_trend(recent: list[dict]) -> str:
    """'improving' | 'declining' | 'stable' based on last 5 vs prior 5."""
    ags = [r["age_grade"] for r in recent if r["age_grade"]]
    if len(ags) < 4:
        return "stable"
    mid   = len(ags) // 2
    newer = sum(ags[:mid]) / mid
    older = sum(ags[mid:]) / (len(ags) - mid)
    diff  = newer - older
    if diff > 1.5:   return "improving"
    if diff < -1.5:  return "declining"
    return "stable"
