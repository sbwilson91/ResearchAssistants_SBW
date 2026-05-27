"""
running_bot/strava.py — Strava API auth, data fetching, and metric computation.
"""

import os
import statistics
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict


def refresh_access_token():
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type":    "refresh_token",
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    print(f"✓ Strava auth OK, token expires {datetime.fromtimestamp(data['expires_at'])}")
    new_refresh = data["refresh_token"]
    return data["access_token"], (new_refresh if new_refresh != os.environ["STRAVA_REFRESH_TOKEN"] else None)


def _get_activities(token, after_ts, before_ts=None, per_page=100):
    headers, activities, page = {"Authorization": f"Bearer {token}"}, [], 1
    while True:
        params = {"after": int(after_ts), "per_page": per_page, "page": page}
        if before_ts:
            params["before"] = int(before_ts)
        resp = requests.get("https://www.strava.com/api/v3/athlete/activities",
                            headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return activities


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


def _fetch_activity_hr_zones(token, activity_id):
    """Return list of HR zone buckets (dicts with 'time' in seconds) for one activity."""
    resp = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}/zones",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return []
    for zone in resp.json():
        if zone.get("type") == "heartrate":
            return zone.get("distribution_buckets", [])
    return []


def _hr_zones(activities, token, max_hr=185):
    """Compute total seconds in each HR zone across all runs, using Strava's per-activity
    zone data. Falls back to moving_time-weighted average HR if zones aren't available."""
    zones = {"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0}
    z_keys = list(zones.keys())
    runs = [a for a in activities if a.get("type") == "Run"]

    for a in runs:
        buckets = _fetch_activity_hr_zones(token, a["id"])
        if buckets:
            # Buckets are sorted low→high by Strava; map to Z1-Z5.
            # If Strava returns 6 buckets, merge the last two into Z5.
            merged = buckets[:4] + [{"time": sum(b.get("time", 0) for b in buckets[4:])}]
            for i, bucket in enumerate(merged):
                zones[z_keys[i]] += bucket.get("time", 0)
        else:
            # Fallback: classify by average HR, weight by moving_time
            hr = a.get("average_heartrate")
            mt = a.get("moving_time", 0)
            if hr and mt:
                pct = hr / max_hr * 100
                z = "Z1" if pct<60 else "Z2" if pct<70 else "Z3" if pct<80 else "Z4" if pct<90 else "Z5"
                zones[z] += mt
    return zones


def _daily_activities(activities: list) -> dict:
    """Return {date_str: {dist_km, suffer_score, type}} for heatmap rendering."""
    daily = {}
    for a in activities:
        d = _dt(a).strftime("%Y-%m-%d")
        t = a.get("type", "Other")
        if d not in daily:
            daily[d] = {"dist_km": 0.0, "suffer_score": 0, "type": t}
        if t == "Run":
            daily[d]["dist_km"]     += round(a.get("distance", 0) / 1000, 1)
            daily[d]["suffer_score"] += a.get("suffer_score") or 0
            daily[d]["type"]          = "Run"
    return daily


def build_report_data(token, history_weeks=16):
    now           = datetime.now(timezone.utc)
    week_start    = now - timedelta(days=7)
    week_end      = now
    history_start = week_start - timedelta(weeks=history_weeks)

    print(f"Fetching {history_start.date()} → {week_end.date()}…")
    all_acts = _get_activities(token,
                               after_ts=history_start.timestamp(),
                               before_ts=week_end.timestamp())
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
        "zone_dist":        _hr_zones(this_week, token),
        "current_streak":   _detect_streak(all_acts),
        "total_activities": len(all_acts),
        "daily_activities": _daily_activities(all_acts),
        "speed_sessions":   [],              # populated by running_bot.py after stream fetch
    }
