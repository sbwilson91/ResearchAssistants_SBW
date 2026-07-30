"""
running_bot/speed_sessions.py

Fetches per-second Garmin activity detail streams for speed sessions and
detects individual intervals from the velocity signal. Produces structured
per-effort stats that feed into both the HTML report and the Claude insights
prompt.

Speed sessions are identified as:
  - Any run on Tuesday or Thursday (MRC / interval days)
  - Any run with keywords: fartlek, interval, mrc, mikkeler, speed,
    track, tempo, sprint, intervals in the activity name

Migrated from Strava streams (lost API access) to Garmin Connect's
get_activity_details(), confirmed via a live probe call to return per-second
samples keyed directSpeed/directHeartRate/directRunCadence/sumDistance/
sumElapsedDuration (see garmin_activities.py's _reshape for the equivalent
summary-level field mapping).
"""

import time
import statistics
from datetime import datetime

from garmin import _get_client


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


# ── Garmin activity details ("streams" equivalent) ────────────────────────────

def _fetch_streams(activity_id: int) -> dict:
    """
    Fetch velocity, HR, cadence, time, distance streams for one activity from
    Garmin Connect. Returns a dict of equal-length lists keyed the same way
    the rest of this module (and durability.py) expect:
        {"velocity_smooth": [...], "heartrate": [...], "cadence": [...],
         "time": [...], "distance": [...]}
    Returns empty dict on error or if the activity has no detail samples.
    """
    client = _get_client()
    try:
        details = client.get_activity_details(activity_id)
    except Exception as e:
        print(f"    ⚠ Stream fetch failed for {activity_id}: {e}")
        return {}

    try:
        from garminconnect import parse_activity_detail_metrics
        samples = parse_activity_detail_metrics(details)
    except ImportError:
        samples = _parse_metrics_fallback(details)

    if not samples:
        return {}

    t0 = samples[0].get("sumElapsedDuration") or 0
    velocity, hr, cadence, t, dist = [], [], [], [], []
    for s in samples:
        velocity.append(s.get("directSpeed") or 0.0)
        hr.append(s.get("directHeartRate"))
        # directRunCadence is PER-LEG steps/min (confirmed against the activity
        # summary: maxDoubleCadence == maxRunningCadenceInStepsPerMinute, so
        # "Double" is the full-body figure and plain directRunCadence is half
        # of it) — same ×2 convention Strava's raw cadence stream needed.
        cadence.append((s.get("directRunCadence") or 0.0) * 2)
        t.append(int((s.get("sumElapsedDuration") or 0) - t0))
        dist.append(s.get("sumDistance") or 0.0)

    return {
        "velocity_smooth": {"data": velocity},
        "heartrate":       {"data": hr},
        "cadence":          {"data": cadence},
        "time":            {"data": t},
        "distance":        {"data": dist},
    }


def _parse_metrics_fallback(details: dict) -> list[dict]:
    """Hand-rolled equivalent of parse_activity_detail_metrics, in case the
    installed garminconnect version doesn't export it."""
    descriptors = details.get("metricDescriptors", [])
    by_index = {d["metricsIndex"]: d["key"] for d in descriptors if d.get("metricsIndex") is not None}
    samples = []
    for row in details.get("activityDetailMetrics", []):
        metrics = row.get("metrics", [])
        sample = {}
        for idx, key in by_index.items():
            if idx < len(metrics) and metrics[idx] is not None:
                sample[key] = metrics[idx]
        samples.append(sample)
    return samples


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

def analyse_session(activity: dict) -> dict | None:
    """
    Fetch streams and compute interval statistics for one activity.
    Returns None if no meaningful interval data found.
    """
    aid  = activity.get("id")
    name = activity.get("name", "Run")
    date = activity["start_date_local"][:10]
    print(f"  Fetching streams: {date} — {name}")

    streams = _fetch_streams(aid)
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
                if hr and gap_start <= ts[j] < gap_end and hr[j] and hr[j] > 40
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
            if hr and iv["start_s"] <= ts[j] < iv["end_s"] and hr[j] and hr[j] > 40
        ]
        iv_cads = [
            cad[j] for j in range(len(ts))  # already spm — see _fetch_streams
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
    all_hr  = [h for h in hr  if h and h > 40] if hr else []
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

def get_speed_sessions(activities: list[dict]) -> list[dict]:
    """
    From a list of this week's activities, identify speed sessions,
    fetch their streams, and return analysed session dicts.

    Args:
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
            result = analyse_session(a)
            if result and result["n_intervals"] > 0:
                sessions.append(result)
                print(f"    ✓ {result['date']} — {result['n_intervals']} intervals, "
                      f"best {result['best_pace']}/km")
            # Small delay to be polite to Garmin's (undocumented) rate limits
            time.sleep(1)
        except Exception as e:
            print(f"    ⚠ Error analysing session: {e}")

    return sessions
