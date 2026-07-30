#!/usr/bin/env python3
"""
running_bot/deep_dive.py — ONE-OFF investigation collector (not the weekly bot).

Fetches real data from the same sources the weekly bot uses (Strava full history,
Garmin current physiology) and the artefacts the bot has already produced (weekly
HTML reports, monthly logs, athlete context), aggregates everything into a single
rich JSON, and writes it to running_bot/reports/deep_dive_data.json.

It does NOT call Claude. The analytical synthesis + feasibility verdict + the
shareable Artifact are produced separately (in a Claude Code session) from the
JSON this script commits. Keeping Claude out of this path avoids the retired-model
404 that has repeatedly broken the weekly bot.

Run in GitHub Actions via .github/workflows/deep_dive_run.yml (workflow_dispatch),
which supplies the Strava/Garmin secrets. Locally you can do a parsing-only dry run:

    python running_bot/deep_dive.py --dry-run   # no secrets needed
"""

import os
import sys
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import strava
from strava import (
    refresh_access_token, _get_activities, _dt, pace_val, pace_from_speed,
    fmt_duration, _parkruns, _aerobic_efficiency, _weekly_stats,
)
from speed_sessions import is_speed_session, get_speed_sessions

HERE        = Path(__file__).parent
REPORTS_DIR = HERE / "reports"
OUT_PATH    = REPORTS_DIR / "deep_dive_data.json"

# Lookback for the long-term trajectory (2.5 years ≈ 130 weeks).
HISTORY_WEEKS = 130

# Race anchors for the targeted training-block case studies. Each block is the
# 12 weeks *before* the race. `approx_date` only seeds a search window — the exact
# race date + finish time are pinned from the matching Strava activity. Edit freely.
CASE_STUDY_ANCHORS = [
    {"key": "cph_half_2024",   "name": "Copenhagen Half Marathon 2024", "approx_date": "2024-09-15", "type": "half_marathon", "outcome": "reference"},
    {"key": "berlin_half_2025","name": "Berlin Half Marathon 2025",     "approx_date": "2025-04-06", "type": "half_marathon", "outcome": "strong (HM PB 1:32:55)"},
    {"key": "cph_marathon_2025","name": "Copenhagen Marathon 2025",     "approx_date": "2025-05-11", "type": "marathon",      "outcome": "strong (M PB 3:31:06)"},
    {"key": "cph_half_2025",   "name": "Copenhagen Half Marathon 2025", "approx_date": "2025-09-14", "type": "half_marathon", "outcome": "reference"},
    {"key": "valencia_half_2025","name": "Valencia Half Marathon 2025", "approx_date": "2025-10-26", "type": "half_marathon", "outcome": "less-than-ideal"},
]

# The current goal build, described with the SAME block metrics for comparison.
CURRENT_BUILD = {"key": "cph_half_2026_current", "name": "Copenhagen Half 2026 (current sub-90 build)",
                 "approx_date": "2026-09-20", "type": "half_marathon", "outcome": "in-progress / goal"}

TODAY = date.today()


# ── small helpers ──────────────────────────────────────────────────────────────

def _runs(acts):
    return [a for a in acts if a.get("type") == "Run"]


def _week_key(d: date) -> str:
    """Monday of the ISO week containing d."""
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _in_window(acts, start: date, end: date):
    return [a for a in acts if start <= _dt(a).date() <= end]


def _window_profile(acts, start: date, end: date) -> dict:
    """Uniform summary of a date window used for the year-over-year comparison."""
    win = _in_window(acts, start, end)
    runs = _runs(win)
    ws = _weekly_stats(win) or {}
    n_weeks = max(1, round((end - start).days / 7))
    hr_vals = [a["average_heartrate"] for a in runs if a.get("average_heartrate")]
    long_run = max((a.get("distance", 0) / 1000 for a in runs), default=0)
    quality = [a for a in runs if is_speed_session(a)]
    return {
        "start": start.isoformat(), "end": end.isoformat(), "weeks": n_weeks,
        "runs": len(runs),
        "total_km": round(sum(a.get("distance", 0) for a in runs) / 1000, 1),
        "km_per_week": round(sum(a.get("distance", 0) for a in runs) / 1000 / n_weeks, 1),
        "avg_hr": round(statistics.mean(hr_vals)) if hr_vals else None,
        "avg_pace": ws.get("avg_pace", "–"),
        "aerobic_efficiency_pace": _aerobic_efficiency(runs),  # pace @ 130-145 bpm
        "longest_run_km": round(long_run, 1),
        "quality_sessions": len(quality),
        "quality_paces": sorted(
            pace_from_speed(a.get("average_speed")) for a in quality if a.get("average_speed")
        ),
    }


def _monthly_series(acts) -> list[dict]:
    buckets = defaultdict(list)
    for a in _runs(acts):
        buckets[_dt(a).strftime("%Y-%m")].append(a)
    out = []
    for ym, runs in sorted(buckets.items()):
        hr = [a["average_heartrate"] for a in runs if a.get("average_heartrate")]
        spd = [a["average_speed"] for a in runs if a.get("average_speed")]
        out.append({
            "month": ym,
            "runs": len(runs),
            "dist_km": round(sum(a.get("distance", 0) for a in runs) / 1000, 1),
            "elev_m": round(sum(a.get("total_elevation_gain", 0) for a in runs)),
            "avg_hr": round(statistics.mean(hr)) if hr else None,
            "avg_pace": pace_from_speed(statistics.mean(spd)) if spd else "–",
            "longest_km": round(max((a.get("distance", 0) / 1000 for a in runs), default=0), 1),
        })
    return out


def _weekly_volume(acts, start: date, end: date) -> list[dict]:
    buckets = defaultdict(list)
    for a in _in_window(_runs(acts), start, end):
        buckets[_week_key(_dt(a).date())].append(a)
    out = []
    for wk in sorted(buckets):
        runs = buckets[wk]
        hr = [a["average_heartrate"] for a in runs if a.get("average_heartrate")]
        out.append({
            "week": wk,
            "dist_km": round(sum(a.get("distance", 0) for a in runs) / 1000, 1),
            "runs": len(runs),
            "avg_hr": round(statistics.mean(hr)) if hr else None,
            "longest_km": round(max((a.get("distance", 0) / 1000 for a in runs), default=0), 1),
            "quality_sessions": sum(1 for a in runs if is_speed_session(a)),
        })
    return out


def _find_race_activity(acts, approx: date, race_type: str):
    """Pin the actual race near `approx` (±10 days). Prefer the longest/fastest
    effort of the expected distance; return its date, finish time, pace, HR."""
    lo, hi = approx - timedelta(days=10), approx + timedelta(days=10)
    dist_lo, dist_hi = {
        "half_marathon": (19.5, 22.5),
        "marathon":      (40.0, 44.0),
    }.get(race_type, (0, 1e9))
    cands = [
        a for a in _runs(acts)
        if lo <= _dt(a).date() <= hi and dist_lo <= a.get("distance", 0) / 1000 <= dist_hi
    ]
    if not cands:
        return None
    # The race is the fastest qualifying effort in the window.
    best = min(cands, key=lambda a: a.get("moving_time", 1e12))
    return {
        "date": _dt(best).date().isoformat(),
        "name": best.get("name", ""),
        "dist_km": round(best.get("distance", 0) / 1000, 2),
        "time": fmt_duration(best.get("moving_time")),
        "time_s": best.get("moving_time"),
        "pace": pace_from_speed(best.get("average_speed")),
        "avg_hr": best.get("average_heartrate"),
    }


def extract_block(acts, anchor: dict) -> dict:
    """12-week training block ending at a race anchor, described with uniform metrics."""
    approx = date.fromisoformat(anchor["approx_date"])
    race = _find_race_activity(acts, approx, anchor.get("type", "half_marathon"))
    end = date.fromisoformat(race["date"]) if race else min(approx, TODAY)
    start = end - timedelta(weeks=12)

    weekly = _weekly_volume(acts, start, end)
    profile = _window_profile(acts, start, end)
    peak = max(weekly, key=lambda w: w["dist_km"], default=None)
    # Taper = last 3 weeks before race vs the peak.
    taper = weekly[-3:] if len(weekly) >= 3 else weekly
    return {
        "key": anchor["key"], "name": anchor["name"], "type": anchor.get("type"),
        "outcome_label": anchor.get("outcome"),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "race": race,
        "summary": profile,
        "peak_week": peak,
        "weekly_volume": weekly,
        "taper_weeks": taper,
    }


# ── narrative artefacts already produced by the bot ────────────────────────────

def _parse_weekly_reports() -> list[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("⚠  beautifulsoup4 not available — skipping HTML report parse")
        return []
    out = []
    for p in sorted(REPORTS_DIR.glob("report_*.html")):
        try:
            soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
            title = (soup.title.string if soup.title else "") or ""
            # Strip scripts/styles, collapse to readable text.
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
            out.append({
                "file": p.name,
                "date": p.stem.replace("report_", ""),
                "title": title.strip(),
                "text": text[:6000],  # narrative + key metrics; trimmed for size
            })
        except Exception as e:
            print(f"⚠  failed to parse {p.name}: {e}")
    print(f"✓ Parsed {len(out)} weekly report(s)")
    return out


def _read_monthly_logs() -> list[dict]:
    logs_dir = HERE / "monthly_logs"
    out = []
    for p in sorted(logs_dir.glob("*.md")):
        out.append({"file": p.name, "month": p.stem, "text": p.read_text(encoding="utf-8")})
    print(f"✓ Read {len(out)} monthly log(s)")
    return out


def _read_athlete_context() -> str:
    p = HERE / "athlete_context.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── Garmin ─────────────────────────────────────────────────────────────────────

def _collect_garmin() -> dict:
    if not os.environ.get("GARMIN_EMAIL"):
        return {"available": False, "note": "GARMIN_EMAIL not set"}
    try:
        from garmin import get_garmin_data, _get_client, _fetch_vo2max_trend
    except Exception as e:
        return {"available": False, "note": f"garmin import failed: {e}"}

    snapshot = {}
    try:
        snapshot = get_garmin_data([])  # current analytics snapshot; empty week is fine
    except Exception as e:
        print(f"⚠  Garmin snapshot failed: {e}")
        snapshot = {"available": False, "error": str(e)}

    # Sparse historical VO2max: monthly samples back ~18 months (best effort).
    vo2_history = []
    try:
        client = _get_client()
        for months_ago in range(18, -1, -1):
            d = (TODAY - timedelta(days=30 * months_ago))
            pts = _fetch_vo2max_trend(client, d, weeks=1)
            if pts:
                vo2_history.append(pts[-1])
    except Exception as e:
        print(f"⚠  Garmin VO2max history unavailable: {e}")
    # De-dup by date.
    seen, deduped = set(), []
    for pt in vo2_history:
        if pt["date"] not in seen:
            seen.add(pt["date"]); deduped.append(pt)

    snapshot["vo2max_history_sparse"] = deduped
    if not deduped:
        snapshot["vo2max_history_note"] = "Garmin returned no deep VO2max history; treat Garmin as current-state only."
    return snapshot


# ── main ───────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    print(f"=== Deep-dive collector — {TODAY} (dry_run={dry_run}) ===")
    REPORTS_DIR.mkdir(exist_ok=True)

    payload = {
        "generated": TODAY.isoformat(),
        "goal": {"race": "Copenhagen Half Marathon", "date": "2026-09-20",
                 "target": "sub-1:30:00", "target_seconds": 5400, "target_pace": "4:16/km"},
        "athlete_context": _read_athlete_context(),
        "weekly_reports": _parse_weekly_reports(),
        "monthly_logs": _read_monthly_logs(),
    }

    if dry_run:
        payload["note"] = "DRY RUN — Strava/Garmin not fetched; narrative artefacts only."
        OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"✓ (dry run) wrote {OUT_PATH}")
        return payload

    # ── Strava full history ──
    token, new_refresh = refresh_access_token()
    if new_refresh:
        Path("new_refresh_token.txt").write_text(new_refresh)

    now = datetime.now(timezone.utc)
    start_ts = (now - timedelta(weeks=HISTORY_WEEKS)).timestamp()
    print(f"Fetching Strava {datetime.fromtimestamp(start_ts).date()} → {now.date()}…")
    acts = _get_activities(token, after_ts=start_ts, before_ts=now.timestamp())
    print(f"  → {len(acts)} activities")

    # 1. Long-term trajectory
    payload["monthly_series"] = _monthly_series(acts)
    payload["parkruns"] = _parkruns(acts)

    # 2. Now vs 1 year ago (matched 8-week calendar windows)
    now_start = TODAY - timedelta(weeks=8)
    payload["now_vs_year_ago"] = {
        "now":       _window_profile(acts, now_start, TODAY),
        "year_ago":  _window_profile(acts, now_start - timedelta(days=365), TODAY - timedelta(days=365)),
    }

    # 3. Recent 4-month block (16 weeks) + stream-level speed sessions
    four_mo_start = TODAY - timedelta(weeks=16)
    payload["recent_4_months"] = {
        "window": {"start": four_mo_start.isoformat(), "end": TODAY.isoformat()},
        "weekly_volume": _weekly_volume(acts, four_mo_start, TODAY),
        "summary": _window_profile(acts, four_mo_start, TODAY),
        "notable_disruptions": [
            {"date": "2026-04-23", "event": "Left for Australia (holiday)"},
            {"date": "2026-05-18", "event": "Returned from Australia"},
            {"date": "2026-05-24", "event": "Light ligament strain — days off"},
            {"date": "2026-03", "event": "884-day run streak ended after Barcelona Marathon"},
        ],
    }
    recent_acts = _in_window(acts, four_mo_start, TODAY)
    try:
        payload["recent_4_months"]["speed_sessions"] = get_speed_sessions(token, recent_acts)
    except Exception as e:
        print(f"⚠  speed session stream analysis failed: {e}")
        payload["recent_4_months"]["speed_sessions"] = []

    # 4. Targeted training-block case studies + current build (uniform metrics)
    payload["case_studies"] = [extract_block(acts, a) for a in CASE_STUDY_ANCHORS]
    payload["current_build"] = extract_block(acts, CURRENT_BUILD)

    # 4b. Durability — aerobic decoupling / fade on long runs + races (stream-level)
    try:
        from durability import get_durability
        payload["durability"] = get_durability(token, acts, CASE_STUDY_ANCHORS, TODAY)
    except Exception as e:
        print(f"⚠  durability analysis failed: {e}")
        payload["durability"] = {"races": [], "long_runs": [], "note": f"error: {e}"}

    # 5. Garmin current physiology (+ sparse VO2 history if any)
    payload["garmin"] = _collect_garmin()

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"✓ wrote {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")
    return payload


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
