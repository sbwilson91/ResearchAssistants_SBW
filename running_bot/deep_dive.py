#!/usr/bin/env python3
"""
running_bot/deep_dive.py — ONE-OFF investigation collector (not the weekly bot).

Fetches real data from the same sources the weekly bot uses (Garmin full
history + current physiology) and the artefacts the bot has already produced
(weekly HTML reports, monthly logs, athlete context), aggregates everything
into a single rich JSON, and writes it to running_bot/reports/deep_dive_data.json.

It does NOT call Claude. The analytical synthesis + feasibility verdict + the
shareable Artifact are produced separately (in a Claude Code session) from the
JSON this script commits. Keeping Claude out of this path avoids the retired-model
404 that has repeatedly broken the weekly bot.

Run in GitHub Actions via .github/workflows/deep_dive_run.yml (workflow_dispatch),
which supplies the Garmin secrets. Locally you can do a parsing-only dry run:

    python running_bot/deep_dive.py --dry-run   # no secrets needed
"""

import os
import sys
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

from garmin_activities import (
    get_activities, _dt, pace_val, pace_from_speed,
    fmt_duration, _parkruns, _aerobic_efficiency, _weekly_stats,
)
from speed_sessions import is_speed_session, get_speed_sessions

HERE        = Path(__file__).parent
REPORTS_DIR = HERE / "reports"
OUT_PATH    = REPORTS_DIR / "deep_dive_data.json"

# Lookback for the long-term trajectory (2.5 years ≈ 130 weeks). Only used by the
# legacy full-Garmin path; the hybrid path never re-fetches this far back.
HISTORY_WEEKS = 130

# ── Hybrid mode ────────────────────────────────────────────────────────────────
# The frozen Strava snapshot is the immutable historical spine. Garmin lost the
# manual run names/notes and the deep per-second stream history that Strava held,
# so we NEVER re-fetch 2.5 years from Garmin. Instead we keep the annotation-rich,
# deep-history sections from this base and let Garmin only "fill forward" from the
# base's `generated` date. If the base is absent we fall back to full Garmin.
BASE_PATH = REPORTS_DIR / "deep_dive_strava_base.json"

# How much recent Garmin history the hybrid path pulls to recompute the moving
# windows. The widest recomputed window is recent-4-months (16 wk); 20 wk gives
# headroom past the boundary without dragging in shallow/old Garmin history.
RECENT_FETCH_WEEKS = 20

# Race anchors for the targeted training-block case studies. Each block is the
# 12 weeks *before* the race. `approx_date` only seeds a search window — the exact
# race date + finish time are pinned from the matching Garmin activity. Edit freely.
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

# Static context markers for the recent block (reused by both collection paths).
_DISRUPTIONS = [
    {"date": "2026-04-23", "event": "Left for Australia (holiday)"},
    {"date": "2026-05-18", "event": "Returned from Australia"},
    {"date": "2026-05-24", "event": "Light ligament strain — days off"},
    {"date": "2026-03", "event": "884-day run streak ended after Barcelona Marathon"},
]


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
        payload["note"] = "DRY RUN — Garmin not fetched; narrative artefacts only."
        _write(payload)
        return payload

    base = _load_base()
    if base is None:
        print("⚠  No frozen Strava base found → full Garmin collection (legacy path).")
        return _run_full_garmin(payload)
    return _run_hybrid(payload, base)


def _write(payload: dict):
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"✓ wrote {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")


def _load_base() -> dict | None:
    """Load the frozen Strava spine, or None if it's missing/unreadable."""
    if not BASE_PATH.exists():
        return None
    try:
        base = json.loads(BASE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠  Could not read frozen base {BASE_PATH.name}: {e}")
        return None
    print(f"✓ Loaded frozen Strava spine {BASE_PATH.name} (generated {base.get('generated')})")
    return base


def _merge_longs(base_longs: list, new_longs: list, cap: int) -> list:
    """Merge long-run durability entries, de-duped by date. Strava (base) entries
    win on collision because they carry the manual run name. Newest first, capped."""
    by_date = {}
    for lr in base_longs:
        by_date[lr.get("date")] = lr
    for lr in new_longs:
        by_date.setdefault(lr.get("date"), lr)   # keep the Strava-named entry on a clash
    return sorted(by_date.values(), key=lambda x: x.get("date", ""), reverse=True)[:cap]


def _run_hybrid(payload: dict, base: dict) -> dict:
    """Splice: keep the annotation-rich, deep-history sections from the frozen
    Strava spine; fetch only recent Garmin activity and let it fill forward."""
    boundary     = date.fromisoformat(base["generated"])   # e.g. 2026-07-20
    boundary_iso = boundary.isoformat()
    boundary_ym  = boundary.strftime("%Y-%m")

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(weeks=RECENT_FETCH_WEEKS)
    print(f"Hybrid: Strava spine ≤ {boundary_iso}; "
          f"fetching Garmin {start_dt.date()} → {now.date()} to fill forward…")
    g = get_activities(start_dt, now)
    g_new = [a for a in g if _dt(a).date() > boundary]
    print(f"  → {len(g)} recent Garmin activities ({len(g_new)} newer than the boundary)")

    # ── Frozen from the Strava spine (Garmin can't reproduce these) ──
    payload["parkruns"]     = base.get("parkruns", [])
    payload["case_studies"] = base.get("case_studies", [])

    # ── 2.5-yr arc: pre-boundary months from Strava; boundary month onward
    #    (now a complete month) refreshed + extended from Garmin ──
    g_months = _monthly_series(g)
    payload["monthly_series"] = (
        [m for m in base.get("monthly_series", []) if m["month"] < boundary_ym]
        + [m for m in g_months if m["month"] >= boundary_ym]
    )

    # ── Now vs a year ago: 'now' recomputed from Garmin (aggregate only, so the
    #    dropped names don't matter); 'year_ago' stays frozen (Garmin can't reach
    #    2025 within the recent fetch window) ──
    now_start = TODAY - timedelta(weeks=8)
    payload["now_vs_year_ago"] = {
        "now":      _window_profile(g, now_start, TODAY),
        "year_ago": base.get("now_vs_year_ago", {}).get("year_ago"),
    }

    # ── Recent 4 months: aggregates recomputed from Garmin; the *named* speed-
    #    session list keeps the Strava backlog and appends new Garmin sessions ──
    four_mo_start = TODAY - timedelta(weeks=16)
    base_ss = base.get("recent_4_months", {}).get("speed_sessions", [])
    try:
        new_ss = [s for s in get_speed_sessions(_in_window(g, four_mo_start, TODAY))
                  if s.get("date", "") > boundary_iso]
    except Exception as e:
        print(f"⚠  speed session stream analysis failed: {e}")
        new_ss = []
    payload["recent_4_months"] = {
        "window": {"start": four_mo_start.isoformat(), "end": TODAY.isoformat()},
        "weekly_volume": _weekly_volume(g, four_mo_start, TODAY),
        "summary": _window_profile(g, four_mo_start, TODAY),
        "notable_disruptions": base.get("recent_4_months", {}).get("notable_disruptions", _DISRUPTIONS),
        "speed_sessions": base_ss + new_ss,
    }

    # ── Current build: the live block sits entirely inside the Garmin window and
    #    is aggregate-only, so recompute it wholesale from Garmin ──
    payload["current_build"] = extract_block(g, CURRENT_BUILD)

    # ── Durability: race decoupling stays frozen (old Strava streams Garmin no
    #    longer serves); long runs keep the Strava-named backlog + new Garmin runs ──
    base_dur  = base.get("durability", {}) or {}
    new_longs = []
    try:
        from durability import get_durability
        gd = get_durability(g, [], TODAY)   # long-run decoupling on recent Garmin efforts only
        new_longs = [lr for lr in gd.get("long_runs", []) if lr.get("date", "") > boundary_iso]
    except Exception as e:
        print(f"⚠  durability (new long runs) failed: {e}")
    payload["durability"] = {
        "races":     base_dur.get("races", []),
        "long_runs": _merge_longs(base_dur.get("long_runs", []), new_longs, cap=8),
        "note":      base_dur.get("note"),
    }

    # ── Garmin current physiology (fresh) ──
    payload["garmin"] = _collect_garmin()

    # ── Provenance: tell the synthesis step exactly what came from where ──
    payload["hybrid"] = {
        "base_generated": base.get("generated"),
        "boundary": boundary_iso,
        "garmin_fetch_start": start_dt.date().isoformat(),
        "new_garmin_activities": len(g_new),
        "frozen_from_strava": [
            "parkruns", "case_studies", "durability.races",
            "now_vs_year_ago.year_ago", f"monthly_series (< {boundary_ym})",
            "speed_sessions (≤ boundary)", "durability.long_runs (≤ boundary)",
        ],
        "refreshed_from_garmin": [
            "now_vs_year_ago.now", "recent_4_months.*", "current_build",
            f"monthly_series (≥ {boundary_ym})", "garmin",
            "new speed_sessions / long runs",
        ],
    }
    _write(payload)
    return payload


def _run_full_garmin(payload: dict) -> dict:
    """Legacy path: build the whole dossier from Garmin. Used only when no frozen
    Strava base exists — may be shallow, since Garmin lacks the deep history."""
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(weeks=HISTORY_WEEKS)
    print(f"Fetching Garmin {start_dt.date()} → {now.date()}…")
    acts = get_activities(start_dt, now)
    print(f"  → {len(acts)} activities")

    payload["monthly_series"] = _monthly_series(acts)
    payload["parkruns"] = _parkruns(acts)

    now_start = TODAY - timedelta(weeks=8)
    payload["now_vs_year_ago"] = {
        "now":       _window_profile(acts, now_start, TODAY),
        "year_ago":  _window_profile(acts, now_start - timedelta(days=365), TODAY - timedelta(days=365)),
    }

    four_mo_start = TODAY - timedelta(weeks=16)
    payload["recent_4_months"] = {
        "window": {"start": four_mo_start.isoformat(), "end": TODAY.isoformat()},
        "weekly_volume": _weekly_volume(acts, four_mo_start, TODAY),
        "summary": _window_profile(acts, four_mo_start, TODAY),
        "notable_disruptions": _DISRUPTIONS,
    }
    recent_acts = _in_window(acts, four_mo_start, TODAY)
    try:
        payload["recent_4_months"]["speed_sessions"] = get_speed_sessions(recent_acts)
    except Exception as e:
        print(f"⚠  speed session stream analysis failed: {e}")
        payload["recent_4_months"]["speed_sessions"] = []

    payload["case_studies"] = [extract_block(acts, a) for a in CASE_STUDY_ANCHORS]
    payload["current_build"] = extract_block(acts, CURRENT_BUILD)

    try:
        from durability import get_durability
        payload["durability"] = get_durability(acts, CASE_STUDY_ANCHORS, TODAY)
    except Exception as e:
        print(f"⚠  durability analysis failed: {e}")
        payload["durability"] = {"races": [], "long_runs": [], "note": f"error: {e}"}

    payload["garmin"] = _collect_garmin()
    _write(payload)
    return payload


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
