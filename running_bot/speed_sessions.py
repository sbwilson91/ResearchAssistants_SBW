"""
running_bot/speed_sessions.py

Fetches raw Strava stream data for speed sessions and detects individual
intervals from the velocity signal. Produces structured per-effort stats
that feed into both the HTML report and the Claude insights prompt.

Speed sessions are identified as:
  - Any run on Tuesday or Thursday (MRC / interval days)
  - Any run with keywords: fartlek, interval, mrc, mikkeler, speed,
    track, tempo, sprint, intervals in the activity name

API cost: one extra GET per qualifying session (up to MAX_SESSIONS).
Strava rate limit is 100 req/15 min — a typical week with 2 sessions
costs 2 extra calls on top of the ~3 for activity listing.
"""

import os
import time
import statistics
import requests
from datetime import datetime


# ── Config ────────────────────────────────────────────────────────────────────

# Pace threshold: efforts faster than this are counted as intervals
# 4:45/km = 3.509 m/s — well above easy pace (~5:30+) for this athlete
FAST_THRESHOLD_MS   = 1000 / 60 / 4.75  # m/s equivalent of 4:45/km

# Minimum continuous duration to count as an effort (seconds)
MIN_EFFORT_SECS     = 20

# Minimum gap between efforts to count as separate (seconds)
MIN_RECOVERY_SECS   = 15

# Max sessions to fetch streams for (keeps API calls bounded)
MAX_SESSIONS        = 5

# Keywords that mark a session as a speed workout regardless of day
SPEED_KEYWORDS = {
    "fartlek", "interval", "intervals", "mrc", "mikkeler",
    "speed", "track", "tempo", "sprint", "vo2", "threshold",
    "tuesday", "thursday", "quality", "effort", "reps",
}


# ── Strava streams API ────────────────────────────────────────────────────────

def _fetch_streams(token: str, activity_id: int) -> dict:
    """
    Fetch velocity, HR, cadence, time, distance streams for one activity.
    Returns dict keyed by stream type, or empty dict on error.
    """
    url  = f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"keys": "velocity_smooth,heartrate,cadence,time,distance",
                "key_by_type": "true"},
        timeout=20,
    )
    if resp.status_code == 429:
        print(f"    ⚠ Rate limited fetching streams for {activity_id}, skipping")
        return {}
    if resp.status_code != 200:
        print(f"    ⚠ Stream fetch failed ({resp.status_code}) for {activity_id}")
        return {}
    return resp.json()


# ── Interval detection ────────────────────────────────────────────────────────

def _detect_intervals(velocity: list[float], timestamps: list[int]) -> list[dict]:
    """
    Identify sustained fast efforts from a per-second velocity signal.
    Returns a list of dicts: {start_s, end_s, duration_s, mean_ms, peak_ms}
    """
    if not velocity or not timestamps:
        return []

    # Build boolean mask: True = fast sample
    fast = [v >= FAST_THRESHOLD_MS for v in velocity]

    intervals = []
    in_effort  = False
    effort_start = 0

    for i, is_fast in enumerate(fast):
        t = timestamps[i]
        if is_fast and not in_effort:
            in_effort    = True
            effort_start = t
        elif not is_fast and in_effort:
            duration = t - effort_start
            if duration >= MIN_EFFORT_SECS:
                # Collect velocity samples in this effort window
                effort_vels = [
                    velocity[j]
                    for j in range(len(timestamps))
                    if effort_start <= timestamps[j] < t
                ]
                if effort_vels:
                    intervals.append({
                        "start_s":   effort_start,
                        "end_s":     t,
                        "duration_s": duration,
                        "mean_ms":   statistics.mean(effort_vels),
                        "peak_ms":   max(effort_vels),
                    })
            in_effort = False

    # Close any open effort at end of run
    if in_effort:
        t = timestamps[-1]
        duration = t - effort_start
        if duration >= MIN_EFFORT_SECS:
            effort_vels = [
                velocity[j]
                for j in range(len(timestamps))
                if timestamps[j] >= effort_start
            ]
            if effort_vels:
                intervals.append({
                    "start_s":    effort_start,
                    "end_s":      t,
                    "duration_s": duration,
                    "mean_ms":    statistics.mean(effort_vels),
                    "peak_ms":    max(effort_vels),
                })

    # Merge intervals separated by less than MIN_RECOVERY_SECS
    merged = []
    for iv in sorted(intervals, key=lambda x: x["start_s"]):
        if merged and (iv["start_s"] - merged[-1]["end_s"]) < MIN_RECOVERY_SECS:
            prev = merged[-1]
            merged[-1] = {
                "start_s":    prev["start_s"],
                "end_s":      iv["end_s"],
                "duration_s": prev["duration_s"] + iv["duration_s"],
                "mean_ms":    (prev["mean_ms"] + iv["mean_ms"]) / 2,
                "peak_ms":    max(prev["peak_ms"], iv["peak_ms"]),
            }
        else:
            merged.append(iv)

    return merged


def _ms_to_pace(ms: float) -> str:
    """Convert m/s to 'M:SS/km' string."""
    if not ms or ms <= 0:
        return "–"
    p = 1000 / ms / 60
    return f"{int(p)}:{int((p - int(p)) * 60):02d}"


# ── Per-session analysis ──────────────────────────────────────────────────────

def analyse_session(token: str, activity: dict) -> dict | None:
    """
    Fetch streams and compute interval statistics for one activity.
    Returns None if no meaningful interval data found.
    """
    aid  = activity.get("id")
    name = activity.get("name", "Run")
    date = activity["start_date_local"][:10]
    print(f"  Fetching streams: {date} — {name}")

    streams = _fetch_streams(token, aid)
    if not streams:
        return None

    vel  = streams.get("velocity_smooth", {}).get("data", [])
    hr   = streams.get("heartrate",       {}).get("data", [])
    cad  = streams.get("cadence",         {}).get("data", [])
    ts   = streams.get("time",            {}).get("data", [])
    dist = streams.get("distance",        {}).get("data", [])

    if not vel or not ts:
        return None

    intervals = _detect_intervals(vel, ts)
    if not intervals:
        return None

    # Compute recovery windows (between efforts)
    recoveries = []
    for i in range(1, len(intervals)):
        gap_start = intervals[i-1]["end_s"]
        gap_end   = intervals[i]["start_s"]
        if gap_end > gap_start:
            rec_vels = [
                vel[j] for j in range(len(ts))
                if gap_start <= ts[j] < gap_end
            ]
            rec_hrs  = [
                hr[j] for j in range(len(ts))
                if hr and gap_start <= ts[j] < gap_end and hr[j] > 40
            ]
            recoveries.append({
                "duration_s": gap_end - gap_start,
                "mean_ms":    statistics.mean(rec_vels) if rec_vels else None,
                "mean_hr":    round(statistics.mean(rec_hrs)) if rec_hrs else None,
            })

    # HR stats per interval
    enriched_intervals = []
    for iv in intervals:
        iv_hrs = [
            hr[j] for j in range(len(ts))
            if hr and iv["start_s"] <= ts[j] < iv["end_s"] and hr[j] > 40
        ]
        iv_cads = [
            cad[j] * 2 for j in range(len(ts))  # per-leg → spm
            if cad and iv["start_s"] <= ts[j] < iv["end_s"] and cad[j] > 0
        ]
        enriched_intervals.append({
            **iv,
            "mean_pace":  _ms_to_pace(iv["mean_ms"]),
            "peak_pace":  _ms_to_pace(iv["peak_ms"]),
            "mean_hr":    round(statistics.mean(iv_hrs))  if iv_hrs  else None,
            "mean_cad":   round(statistics.mean(iv_cads)) if iv_cads else None,
        })

    # Whole-session stats
    all_hr  = [h for h in hr  if h > 40] if hr else []
    all_vel = [v for v in vel if v > 0]

    # Build velocity profile for chart (sampled every 15 seconds)
    profile = []
    step    = 15
    i       = 0
    while i < len(ts):
        profile.append({
            "t":     ts[i],
            "pace":  round(1000 / vel[i] / 60, 2) if vel[i] > 0 else None,
            "hr":    hr[i] if hr and i < len(hr) else None,
        })
        i += step

    return {
        "activity_id":  aid,
        "name":         name,
        "date":         date,
        "dist_km":      round(activity.get("distance", 0) / 1000, 1),
        "intervals":    enriched_intervals,
        "recoveries":   recoveries,
        "n_intervals":  len(enriched_intervals),
        "best_pace":    _ms_to_pace(max(iv["peak_ms"] for iv in intervals)),
        "avg_effort_pace": _ms_to_pace(
            statistics.mean(iv["mean_ms"] for iv in intervals)
        ),
        "session_avg_hr": round(statistics.mean(all_hr)) if all_hr else None,
        "session_peak_hr": max(all_hr) if all_hr else None,
        "profile":      profile,  # for the pace chart
        "moving_time_s": activity.get("moving_time", 0),
    }


# ── Session identification ────────────────────────────────────────────────────

def is_speed_session(activity: dict) -> bool:
    """Return True if this activity looks like a speed/quality session."""
    if activity.get("type") != "Run":
        return False

    name_lower = activity.get("name", "").lower()
    date       = datetime.fromisoformat(
        activity["start_date_local"].replace("Z", "")
    )
    day = date.weekday()  # 0=Mon, 1=Tue, 4=Thu

    is_key_day = day in (1, 3)  # Tuesday or Thursday
    has_keyword = any(k in name_lower for k in SPEED_KEYWORDS)

    # Exclude very short or very long runs (warm-ups / ultras)
    dist_km = activity.get("distance", 0) / 1000
    is_plausible = 3 <= dist_km <= 25

    return (is_key_day or has_keyword) and is_plausible


def threshold_cadence(sessions: list[dict]) -> dict:
    """
    Average cadence across this week's detected threshold/quality intervals only.

    Easy-run cadence is deliberately lower and shouldn't dilute this — the
    170-180 spm target tracks what the athlete can hit under load, not the
    blended average across an easy + quality week.
    """
    cads = [
        iv["mean_cad"]
        for s in sessions
        for iv in s.get("intervals", [])
        if iv.get("mean_cad")
    ]
    if not cads:
        return {}
    return {
        "cadence_spm":            round(statistics.mean(cads), 1),
        "cadence_source":         "threshold_efforts",
        "cadence_n_intervals":    len(cads),
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def get_speed_sessions(token: str, activities: list[dict]) -> list[dict]:
    """
    From a list of this week's activities, identify speed sessions,
    fetch their streams, and return analysed session dicts.

    Args:
        token:      valid Strava access token
        activities: list of activity summary dicts from the weekly fetch

    Returns:
        List of analysed session dicts (may be empty)
    """
    candidates = [a for a in activities if is_speed_session(a)]
    candidates = sorted(candidates, key=lambda a: a["start_date_local"], reverse=True)
    candidates = candidates[:MAX_SESSIONS]

    if not candidates:
        print("  No speed sessions detected this week")
        return []

    print(f"  Found {len(candidates)} speed session(s) to analyse")
    sessions = []
    for a in candidates:
        try:
            result = analyse_session(token, a)
            if result and result["n_intervals"] > 0:
                sessions.append(result)
                print(f"    ✓ {result['date']} — {result['n_intervals']} intervals, "
                      f"best {result['best_pace']}/km")
            # Small delay to respect rate limits
            time.sleep(1)
        except Exception as e:
            print(f"    ⚠ Error analysing session: {e}")

    return sessions
