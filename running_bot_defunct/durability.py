"""
running_bot/durability.py — aerobic decoupling & fade analysis for long efforts.

Durability (fatigue resistance) is the "fourth pillar" of endurance — how much of
your fresh fitness survives to the back half of a long run or race. It does NOT
show up in weekly averages; it shows up *within* a single effort, as the pace:HR
relationship drifts over the distance.

For each qualifying long run / race we trim the warm-up, split the effort at its
distance midpoint, and compare speed-per-heartbeat in each half:

    EF          = speed / HR          (metres per second per bpm)
    decoupling% = (EF_first - EF_second) / EF_first * 100

Positive decoupling = the engine drifting (HR rising relative to pace, or pace
sagging at the same HR) — i.e. fading. Rough reads: <5% strong, 5-8% moderate,
>8% fades. We also report first- vs second-half pace — the positive split the
athlete actually feels in the last 5 km.

API cost: one stream GET per analysed effort, bounded by MAX_LONG_RUNS + the race
anchors. A one-off deep-dive stays well inside Strava's 100 req / 15 min limit.
"""

import time
import statistics
from datetime import date, timedelta

from strava import _dt, fmt_duration, pace_from_speed
from speed_sessions import _fetch_streams


# ── Config ────────────────────────────────────────────────────────────────────

LONG_RUN_MIN_KM = 18.0     # a run must be at least this long to be "durability-relevant"
WARMUP_SECS     = 300      # trim the first 5 min (HR-lag ramp) before analysing
MAX_LONG_RUNS   = 8        # cap stream fetches for long runs
RECENT_DAYS     = 210      # only analyse long runs from the last ~7 months
FETCH_SLEEP_S   = 1.0      # be polite to the Strava rate limiter

# Distance bands used to pin race efforts near each anchor date
RACE_DIST_BANDS = {
    "half_marathon": (19.5, 22.5),
    "marathon":      (40.0, 44.0),
}


# ── Core computation ──────────────────────────────────────────────────────────

def _half_stats(group: list[tuple]) -> dict | None:
    """group = list of (t_s, dist_m, hr) sorted by time. Returns speed/HR/pace."""
    d_span = group[-1][1] - group[0][1]
    t_span = group[-1][0] - group[0][0]
    if t_span <= 0 or d_span <= 0:
        return None
    speed = d_span / t_span                       # m/s
    hr    = statistics.mean(g[2] for g in group)
    return {"speed": speed, "avg_hr": round(hr), "pace": pace_from_speed(speed)}


def compute_decoupling(streams: dict, warmup_s: int = WARMUP_SECS) -> dict | None:
    """
    Split a run at its (post-warm-up) distance midpoint and measure how much
    speed-per-heartbeat decays between the two halves. Returns None if the run
    lacks HR, is too short, or has too little usable moving data.
    """
    def data(key):
        s = streams.get(key) or {}
        return s.get("data") or []

    t  = data("time")
    d  = data("distance")
    hr = data("heartrate")
    v  = data("velocity_smooth")
    n  = min(len(t), len(d), len(hr), len(v))
    if n < 100 or not hr:
        return None

    # Keep moving samples with a valid HR, after the warm-up window.
    samples = []
    for i in range(n):
        ti, di, hri, vi = t[i], d[i], hr[i], v[i]
        if None in (ti, di, hri, vi):
            continue
        if ti < warmup_s or vi < 0.8 or hri < 60:   # 0.8 m/s ≈ walking floor
            continue
        samples.append((ti, di, hri))
    if len(samples) < 60:
        return None

    usable_m = samples[-1][1] - samples[0][1]
    if usable_m < 3000:                              # need ≥3 km of clean data
        return None

    mid_d  = samples[0][1] + usable_m / 2
    first  = [s for s in samples if s[1] <= mid_d]
    second = [s for s in samples if s[1] >  mid_d]
    if len(first) < 20 or len(second) < 20:
        return None

    h1 = _half_stats(first)
    h2 = _half_stats(second)
    if not h1 or not h2:
        return None

    ef1 = h1["speed"] / h1["avg_hr"]
    ef2 = h2["speed"] / h2["avg_hr"]
    decoupling = round((ef1 - ef2) / ef1 * 100, 1)

    # Positive split = second half slower (pace value larger, in min/km)
    fade_s_per_km = round((1000 / h2["speed"] - 1000 / h1["speed"]), 1)

    label = "strong" if decoupling < 5 else "moderate" if decoupling <= 8 else "fades"
    return {
        "decoupling_pct": decoupling,
        "label":          label,
        "usable_km":      round(usable_m / 1000, 1),
        "first_half":     {"pace": h1["pace"], "avg_hr": h1["avg_hr"]},
        "second_half":    {"pace": h2["pace"], "avg_hr": h2["avg_hr"]},
        "fade_sec_per_km": fade_s_per_km,       # +ve = slowed in the back half
    }


# ── Orchestration ─────────────────────────────────────────────────────────────

def _analyse(token: str, activity: dict, kind: str) -> dict | None:
    streams = _fetch_streams(token, activity["id"])
    if not streams:
        return None
    metrics = compute_decoupling(streams)
    if not metrics:
        return None
    dist_km = round(activity.get("distance", 0) / 1000, 1)
    return {
        "kind":    kind,                          # "race" | "long_run"
        "name":    activity.get("name", "Run"),
        "date":    activity["start_date_local"][:10],
        "dist_km": dist_km,
        "time":    fmt_duration(activity.get("moving_time")),
        **metrics,
    }


def _find_race_activity(acts: list, anchor: dict):
    approx = date.fromisoformat(anchor["approx_date"])
    lo, hi = approx - timedelta(days=10), approx + timedelta(days=10)
    lo_km, hi_km = RACE_DIST_BANDS.get(anchor.get("type"), (0, 1e9))
    cands = [
        a for a in acts
        if a.get("type") == "Run"
        and lo <= _dt(a).date() <= hi
        and lo_km <= a.get("distance", 0) / 1000 <= hi_km
    ]
    return min(cands, key=lambda a: a.get("moving_time", 1e12)) if cands else None


def get_durability(token: str, acts: list, anchors: list,
                   today: date | None = None, max_long: int = MAX_LONG_RUNS) -> dict:
    """
    Analyse decoupling/fade for the case-study races + recent long runs.
    Returns {"races": [...], "long_runs": [...], "note": ...}.
    """
    today = today or date.today()
    runs  = [a for a in acts if a.get("type") == "Run"]
    print("\nAnalysing durability (decoupling)…")

    races = []
    seen_ids = set()
    for anchor in anchors:
        act = _find_race_activity(acts, anchor)
        if not act or act["id"] in seen_ids:
            continue
        seen_ids.add(act["id"])
        res = _analyse(token, act, "race")
        if res:
            res["race_label"] = anchor["name"]
            races.append(res)
            print(f"  ✓ race {anchor['name']}: decoupling {res['decoupling_pct']}% ({res['label']})")
        time.sleep(FETCH_SLEEP_S)

    cutoff = today - timedelta(days=RECENT_DAYS)
    long_runs_all = sorted(
        [a for a in runs
         if a.get("distance", 0) / 1000 >= LONG_RUN_MIN_KM
         and _dt(a).date() >= cutoff
         and a["id"] not in seen_ids],
        key=lambda a: a["start_date_local"], reverse=True,
    )[:max_long]

    long_runs = []
    for act in long_runs_all:
        res = _analyse(token, act, "long_run")
        if res:
            long_runs.append(res)
            print(f"  ✓ long run {res['date']} {res['dist_km']}km: "
                  f"decoupling {res['decoupling_pct']}% ({res['label']})")
        time.sleep(FETCH_SLEEP_S)

    note = None
    if not races and not long_runs:
        note = ("No usable decoupling data — long efforts may lack heart-rate "
                "streams, or none were long enough.")
    return {"races": races, "long_runs": long_runs, "note": note}
