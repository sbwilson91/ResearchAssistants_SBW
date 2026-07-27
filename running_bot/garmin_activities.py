"""
running_bot/garmin_activities.py — Garmin Connect activity fetching + metric
computation. Replaces strava.py after losing Strava API access.

Activities are re-shaped into the same field names the rest of the bot
(report.py, insights.py, speed_sessions.py, durability.py) already expects
from Strava, so downstream consumers needed no structural changes:

    id, name, type ("Run"/"Other"), start_date_local, distance (m),
    moving_time (s), average_speed (m/s), average_heartrate, total_elevation_gain,
    description, effort_load  (Garmin's activityTrainingLoad — replaces
    Strava's suffer_score, since Garmin has no direct equivalent)

Garmin has no separate "auth token" step per request like Strava's OAuth
refresh — _get_client() (garmin.py) handles login/session caching once.
"""

import statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from garmin import _get_client


# ── Fetching + reshaping ──────────────────────────────────────────────────────

def _reshape(a: dict) -> dict:
    """Map a raw Garmin activity summary dict onto the Strava-shaped fields
    the rest of the bot expects."""
    type_key = (a.get("activityType") or {}).get("typeKey", "") or ""
    is_run   = "running" in type_key.lower()

    return {
        "id":                   a.get("activityId"),
        "name":                 a.get("activityName", "Run"),
        "type":                 "Run" if is_run else type_key.replace("_", " ").title() or "Other",
        "start_date_local":     (a.get("startTimeLocal") or "").replace(" ", "T"),
        "distance":             a.get("distance") or 0,
        "moving_time":          a.get("movingDuration") or a.get("duration") or 0,
        "elapsed_time":         a.get("elapsedDuration") or a.get("duration") or 0,
        "average_speed":        a.get("averageSpeed"),
        "average_heartrate":    a.get("averageHR"),
        "total_elevation_gain": a.get("elevationGain") or 0,
        "description":          a.get("description") or "",
        # Garmin has no suffer_score equivalent — nearest analogues are
        # Training Effect (0-5) and Activity Training Load (unbounded, ~0-300).
        # Load is closer in spirit to a per-activity "how hard was this"
        # figure, so it's what feeds the heatmap intensity coloring.
        "effort_load":          a.get("activityTrainingLoad") or 0,
        "aerobic_training_effect": a.get("aerobicTrainingEffect"),
    }


def get_activities(start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Fetch all activities in [start_dt, end_dt), reshaped to Strava-like dicts."""
    client = _get_client()
    raw = client.get_activities_by_date(
        start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    )
    return [_reshape(a) for a in raw]


# ── Below: verbatim ports of strava.py's pure functions ──────────────────────
# (unchanged logic — they only operate on the reshaped dicts above, so no
# Strava-specific assumptions leak through)

def _dt(a):
    return datetime.fromisoformat(a["start_date_local"].replace("Z", ""))


def pace_from_speed(speed_ms):
    if not speed_ms or speed_ms <= 0:
        return "–"
    p = 1000 / speed_ms / 60
    return f"{int(p)}:{int((p - int(p)) * 60):02d}"


def pace_val(speed_ms):
    if not speed_ms or speed_ms <= 0:
        return None
    return 1000 / speed_ms / 60


def fmt_duration(seconds):
    if not seconds:
        return "–"
    h, rem = divmod(int(seconds), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _is_parkrun(a):
    dist = a.get("distance", 0) / 1000
    return a.get("type") == "Run" and _dt(a).weekday() == 5 and 4.8 <= dist <= 5.3


def _weekly_stats(activities):
    runs = [a for a in activities if a.get("type") == "Run"]
    if not runs:
        return None
    hr_vals  = [a["average_heartrate"] for a in runs if a.get("average_heartrate")]
    spd_vals = [a["average_speed"]     for a in runs if a.get("average_speed")]
    return {
        "runs":       len(runs),
        "dist_km":    round(sum(a.get("distance", 0)             for a in runs) / 1000, 1),
        "time_s":     sum(a.get("moving_time", 0)                for a in runs),
        "elev_m":     round(sum(a.get("total_elevation_gain", 0) for a in runs)),
        "avg_pace":   pace_from_speed(statistics.mean(spd_vals)) if spd_vals else "–",
        "avg_hr":     round(statistics.mean(hr_vals))            if hr_vals  else None,
        "activities": runs,
    }


def _aerobic_efficiency(activities):
    vals = [
        pace_val(a["average_speed"])
        for a in activities
        if  a.get("type") == "Run"
        and a.get("average_heartrate") and a.get("average_speed")
        and 130 <= a["average_heartrate"] <= 145
        and pace_val(a["average_speed"])
        and 4 < pace_val(a["average_speed"]) < 9
    ]
    return round(statistics.mean(vals), 2) if vals else None


def _parkruns(activities):
    return sorted([
        {"date": a["start_date_local"][:10],
         "time_s": a.get("moving_time", 0),
         "time_min": round(a.get("moving_time", 0) / 60, 2),
         "hr": a.get("average_heartrate"),
         "name": a.get("name", "Parkrun")}
        for a in activities if _is_parkrun(a)
    ], key=lambda x: x["date"])


def _notable(activities):
    keywords = {"marathon","half","ultra","race","parkrun","10k","10km","5k","5km",
                "runstreak","pb","fartlek","interval","tempo","mrc","sprint",
                "intervals","mikkeler","speed","track"}
    out = []
    for a in activities:
        dist_km = a.get("distance", 0) / 1000
        if dist_km >= 15 or any(k in a.get("name","").lower() for k in keywords):
            desc = (a.get("description") or "").strip()
            out.append({
                "name":    a.get("name", "Run"),
                "date":    a["start_date_local"][:10],
                "dist_km": round(dist_km, 1),
                "time":    fmt_duration(a.get("moving_time")),
                "pace":    pace_from_speed(a.get("average_speed")),
                "hr":      a.get("average_heartrate"),
                "desc":    desc[:300] if desc else "",
            })
    return sorted(out, key=lambda x: x["date"], reverse=True)


def _detect_streak(activities):
    run_dates = sorted(set(_dt(a).date() for a in activities if a.get("type") == "Run"))
    streak, check = 0, datetime.now(timezone.utc).date()
    for d in reversed(run_dates):
        if d >= check - timedelta(days=1):
            streak += 1
            check = d
        else:
            break
    return streak


def _hr_zones(activities, max_hr=185):
    """Total seconds in each HR zone across all runs, using Garmin's per-activity
    hrTimeInZones data. Falls back to moving_time-weighted average HR if a
    specific activity has no zone data (e.g. no HR strap for that run)."""
    client = _get_client()

    zones = {"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0}
    z_keys = list(zones.keys())
    runs = [a for a in activities if a.get("type") == "Run"]

    for a in runs:
        buckets = []
        try:
            buckets = client.get_activity_hr_in_timezones(a["id"]) or []
        except Exception:
            buckets = []

        if buckets:
            # Garmin returns zoneNumber 1-5 with secsInZone; map directly.
            by_zone = {b.get("zoneNumber"): b.get("secsInZone", 0) for b in buckets}
            for i, key in enumerate(z_keys, start=1):
                zones[key] += by_zone.get(i, 0)
        else:
            hr = a.get("average_heartrate")
            mt = a.get("moving_time", 0)
            if hr and mt:
                pct = hr / max_hr * 100
                z = "Z1" if pct<60 else "Z2" if pct<70 else "Z3" if pct<80 else "Z4" if pct<90 else "Z5"
                zones[z] += mt
    return zones


def _daily_activities(activities: list) -> dict:
    """Return {date_str: {dist_km, suffer_score, type}} for heatmap rendering.

    'suffer_score' here is Garmin's activityTrainingLoad (effort_load) — the
    closest available analogue to Strava's proprietary relative-effort metric.
    """
    daily = {}
    for a in activities:
        d = _dt(a).strftime("%Y-%m-%d")
        t = a.get("type", "Other")
        if d not in daily:
            daily[d] = {"dist_km": 0.0, "suffer_score": 0, "type": t}
        if t == "Run":
            daily[d]["dist_km"]     += round(a.get("distance", 0) / 1000, 1)
            daily[d]["suffer_score"] += a.get("effort_load") or 0
            daily[d]["type"]          = "Run"
    return daily


def build_report_data(history_weeks=16):
    now           = datetime.now(timezone.utc)
    week_start    = now - timedelta(days=7)
    week_end      = now
    history_start = week_start - timedelta(weeks=history_weeks)

    print(f"Fetching {history_start.date()} → {week_end.date()}…")
    all_acts = get_activities(history_start, week_end)
    print(f"  → {len(all_acts)} activities")

    this_week = [a for a in all_acts if _dt(a) >= week_start.replace(tzinfo=None)]

    buckets = defaultdict(list)
    for a in all_acts:
        d    = _dt(a)
        wkey = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        buckets[wkey].append(a)

    weekly_series = [
        {"week": wk,
         "dist_km": round(sum(a.get("distance",0) for a in acts if a.get("type")=="Run")/1000,1),
         "runs": sum(1 for a in acts if a.get("type")=="Run"),
         "avg_hr": (round(statistics.mean(
             [a["average_heartrate"] for a in acts if a.get("type")=="Run" and a.get("average_heartrate")]
         )) if any(a.get("average_heartrate") for a in acts if a.get("type")=="Run") else None)}
        for wk, acts in sorted(buckets.items())
    ]

    past_8  = [w for w in weekly_series if w["week"] < week_start.strftime("%Y-%m-%d")][-8:]
    rolling = round(statistics.mean(w["dist_km"] for w in past_8), 1) if past_8 else 0

    cutoff_8 = (week_start - timedelta(weeks=8)).replace(tzinfo=None)
    recent   = [a for a in all_acts if _dt(a) >= cutoff_8]
    older    = [a for a in all_acts if _dt(a) <  cutoff_8]

    all_prs = _parkruns(all_acts)

    type_counts = defaultdict(int)
    for a in this_week:
        type_counts[a.get("type", "Other")] += 1

    return {
        "generated_at":     now.strftime("%A %d %B %Y, %H:%M UTC"),
        "week_label":       f"{week_start.strftime('%d %b')} – {now.strftime('%d %b %Y')}",
        "week_start":       week_start.strftime("%Y-%m-%d"),
        "this_week":        _weekly_stats(this_week),
        "this_week_all":    this_week,       # ← raw list for speed_sessions.py
        "rolling_avg_km":   rolling,
        "weekly_series":    weekly_series[-16:],
        "aero_eff_now":     _aerobic_efficiency(recent),
        "aero_eff_prev":    _aerobic_efficiency(older),
        "all_parkruns":     all_prs[-20:],
        "best_parkrun":     min(all_prs, key=lambda x: x["time_s"]) if all_prs else None,
        "notable":          _notable(this_week),
        "zone_dist":        _hr_zones(this_week),
        "current_streak":   _detect_streak(all_acts),
        "total_activities": len(all_acts),
        "daily_activities": _daily_activities(all_acts),
        "speed_sessions":   [],              # populated by running_bot.py after detail fetch
    }
