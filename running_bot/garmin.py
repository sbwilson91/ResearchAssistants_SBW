"""
running_bot/garmin.py

Fetches two categories of data from Garmin Connect:

1. CALENDAR — scheduled workouts (last week vs actual activities, next week preview)
2. ANALYTICS — metrics that enable real training analysis:
     - Training load (acute / chronic / ratio)
     - Training status label
     - VO₂ max trend (current + 4-week history)
     - Race time predictions vs athlete PBs
     - HRV status (7-day avg, baseline, balance label)
     - Training readiness score
     - Running dynamics (cadence, vertical oscillation, GCT, vertical ratio)
     - Sleep quality weekly average

Each metric is fetched independently with its own try/except so a single
endpoint failure doesn't break the whole integration.
"""

import os
import json
import tempfile
import statistics
from datetime import datetime, timedelta, timezone, date
from pathlib import Path


# ── Auth ─────────────────────────────────────────────────────────────────────

_client_cache = None  # process-wide singleton — one login per run, everything reuses it


def _get_client():
    """Return a single shared, authenticated Garmin client for this process.

    Constructing a fresh `Garmin(...)` and calling `login()` per call (as this
    used to do) hammers Garmin's login endpoint on every activity/stream fetch
    in a run and reliably trips their IP rate limiter — every caller across
    this module, garmin_activities.py, speed_sessions.py, and durability.py
    MUST go through this cached instance instead of constructing their own.
    """
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    try:
        from garminconnect import Garmin
    except ImportError:
        raise ImportError("Add 'garminconnect' to requirements.txt")

    email    = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")
    if not email or not password:
        raise ValueError("GARMIN_EMAIL and GARMIN_PASSWORD secrets required")

    token_file = Path(tempfile.gettempdir()) / "garmin_tokens.json"
    client = Garmin(email, password)

    if token_file.exists():
        try:
            client.load_tokens(str(token_file))
            _client_cache = client
            return client
        except Exception:
            pass

    client.login()
    try:
        client.store_tokens(str(token_file))
    except Exception:
        pass

    print("✓ Garmin: authenticated")
    _client_cache = client
    return client


# ── Safe fetch wrapper ────────────────────────────────────────────────────────

def _safe(fn, label, default=None):
    """Call fn(), return result or default on any exception."""
    try:
        result = fn()
        print(f"  ✓ Garmin: {label}")
        return result
    except Exception as e:
        print(f"  ⚠ Garmin {label}: {e}")
        return default


# ── Analytics fetchers ────────────────────────────────────────────────────────

def _fetch_training_status(client, today: str) -> dict:
    """
    Training status label + acute/chronic load.
    Acute load (7-day) vs chronic load (28-day) — the ratio is the key signal.
    >1.3 = overreach risk, 0.8–1.3 = productive zone, <0.8 = detraining.
    """
    raw = _safe(lambda: client.get_training_status(today),
                "training status", {})
    if not raw:
        return {}

    ts = {
        "status_label": "unknown",
        "acute_load": None,
        "chronic_load": None,
        "load_ratio": None,
        "recovery_time_hours": None,
    }

    # Garmin nests the real values per device under
    #   mostRecentTrainingStatus.latestTrainingStatusData.{deviceId}.…
    # so the old flat raw.get("acuteLoad") always returned None. Walk into the
    # per-device record (pick the most recently synced one) and read the load
    # DTO from there. Flat lookups are kept as a fallback for schema drift.
    mrts = raw.get("mostRecentTrainingStatus", raw) or {}
    per_device = mrts.get("latestTrainingStatusData") or {}
    device = _latest_device_record(per_device)

    # Status label — prefer the descriptive feedback phrase (e.g.
    # "PRODUCTIVE_2", "RECOVERY_1"), normalised to a lowercase word.
    phrase = (device.get("trainingStatusFeedbackPhrase")
              or mrts.get("trainingStatusFeedbackPhrase"))
    if phrase:
        ts["status_label"] = str(phrase).split("_")[0].lower()
    elif device.get("trainingStatus") is not None:
        ts["status_label"] = str(device.get("trainingStatus"))

    # Acute/chronic load + ACWR live in the acuteTrainingLoadDTO.
    load = device.get("acuteTrainingLoadDTO") or {}
    ts["acute_load"] = (
        load.get("dailyTrainingLoadAcute")
        or raw.get("acuteLoad") or raw.get("acuteTrainingLoad"))
    ts["chronic_load"] = (
        load.get("dailyTrainingLoadChronic")
        or raw.get("chronicLoad") or raw.get("chronicTrainingLoad"))

    # Garmin already computes the ratio; use it when present, else derive it.
    ratio = load.get("dailyAcuteChronicWorkloadRatio")
    if ratio:
        ts["load_ratio"] = round(float(ratio), 2)
    elif ts["acute_load"] and ts["chronic_load"] and ts["chronic_load"] > 0:
        ts["load_ratio"] = round(ts["acute_load"] / ts["chronic_load"], 2)

    ts["recovery_time_hours"] = (
        device.get("recoveryTime") or raw.get("recoveryTime"))

    return ts


def _latest_device_record(per_device: dict) -> dict:
    """Pick the most recently synced device record from a {deviceId: {...}} map.

    Garmin returns one record per paired device; the freshest is the one with
    the latest timestamp/calendar date. Falls back to any record, then {}.
    """
    if not isinstance(per_device, dict) or not per_device:
        return {}
    records = [r for r in per_device.values() if isinstance(r, dict)]
    if not records:
        return {}

    def _recency(r: dict):
        return (r.get("timestamp") or r.get("sinceDate")
                or r.get("calendarDate") or "")

    return max(records, key=_recency)


def _fetch_vo2max_trend(client, today_dt: date, weeks: int = 5) -> list[dict]:
    """
    VO₂ max values for the past `weeks` weeks.
    Returns list of {date, vo2max} dicts, newest last.
    """
    points = []
    for w in range(weeks - 1, -1, -1):
        check_date = (today_dt - timedelta(weeks=w)).strftime("%Y-%m-%d")
        raw = _safe(
            lambda d=check_date: client.get_max_metrics(d),
            f"VO₂ max {check_date}", None
        )
        if raw:
            # Response is a list of metrics; find VO2 max for running
            if isinstance(raw, list):
                for entry in raw:
                    if entry.get("sport", "").lower() in ("running", "generic", ""):
                        v = entry.get("generic", {}).get("vo2MaxPreciseValue") or \
                            entry.get("running", {}).get("vo2MaxPreciseValue")
                        if v:
                            points.append({"date": check_date, "vo2max": round(float(v), 1)})
                            break
            elif isinstance(raw, dict):
                v = (raw.get("generic",  {}).get("vo2MaxPreciseValue") or
                     raw.get("running",  {}).get("vo2MaxPreciseValue") or
                     raw.get("vo2MaxPreciseValue"))
                if v:
                    points.append({"date": check_date, "vo2max": round(float(v), 1)})

    return points


def _fetch_race_predictions(client, today: str) -> dict:
    """
    Garmin's predicted race finish times (5k, 10k, HM, marathon).
    Returns dict of {distance: seconds}.
    """
    raw = _safe(lambda: client.get_race_predictions(), "race predictions", None)
    if not raw:
        return {}

    preds = {}
    if isinstance(raw, list):
        raw = raw[0] if raw else {}

    key_map = {
        "time5K":       "5k",
        "time10K":      "10k",
        "timeHalfMarathon": "half_marathon",
        "timeMarathon": "marathon",
    }
    for garmin_key, our_key in key_map.items():
        val = raw.get(garmin_key)
        if val:
            preds[our_key] = int(val)  # seconds

    return preds


def _fetch_hrv_status(client, today: str) -> dict:
    """
    HRV 7-day average vs personal baseline.
    The gap and the direction matter more than the raw number.
    """
    raw = _safe(lambda: client.get_hrv_data(today), "HRV status", None)
    if not raw:
        return {}

    hrv = {}
    summary = raw.get("hrvSummary", raw)

    hrv["weekly_avg"]   = summary.get("weeklyAvg")
    hrv["last_night"]   = summary.get("lastNight")
    hrv["baseline_low"] = summary.get("lastNight5MinHigh")   # Garmin's naming is confusing
    hrv["baseline_balanced_low"]  = summary.get("balancedLow")
    hrv["baseline_balanced_high"] = summary.get("balancedHigh")
    hrv["status"]       = summary.get("status", "UNKNOWN")   # BALANCED / UNBALANCED / LOW / POOR

    # Compute deviation from baseline midpoint if we have both bounds
    if hrv["baseline_balanced_low"] and hrv["baseline_balanced_high"] and hrv["weekly_avg"]:
        midpoint = (hrv["baseline_balanced_low"] + hrv["baseline_balanced_high"]) / 2
        hrv["deviation_from_baseline"] = round(hrv["weekly_avg"] - midpoint, 1)
    else:
        hrv["deviation_from_baseline"] = None

    return hrv


def _fetch_training_readiness(client, today: str) -> dict:
    """
    Garmin's composite 0–100 training readiness score for today.
    Synthesises HRV, sleep, recovery time, and acute load.
    """
    raw = _safe(lambda: client.get_training_readiness(today),
                "training readiness", None)
    if not raw:
        return {}

    if isinstance(raw, list):
        raw = raw[-1] if raw else {}

    return {
        "score":              raw.get("score"),
        "level":              raw.get("level", raw.get("feedbackPhrase", "")),
        "hrv_factor":         raw.get("hrvAcclimatizationFactor"),
        "sleep_factor":       raw.get("sleepHistoryFactor"),
        "recovery_factor":    raw.get("recoveryTimeFactor"),
        "acute_load_factor":  raw.get("acuteLoadFactor"),
    }


def _fetch_running_dynamics(client, today_dt: date) -> dict:
    """
    Weekly average running dynamics: vertical oscillation, ground contact
    time, vertical ratio, from Garmin's weekly aggregates.

    Cadence is deliberately not computed here — running_bot.py overrides it
    with a threshold-effort-only figure (see speed_sessions.threshold_cadence),
    since blending in easy-run cadence would dilute the 170-180 spm target.
    """
    week_start = (today_dt - timedelta(days=today_dt.weekday())).strftime("%Y-%m-%d")
    raw = _safe(
        lambda: client.get_activities_by_date(week_start, today_dt.strftime("%Y-%m-%d"), "running"),
        "running dynamics", None
    )

    dynamics = {}

    if raw and isinstance(raw, list):
        voscs, gcts, vrats = [], [], []
        for act in raw:
            if act.get("avgVerticalOscillation"):
                voscs.append(act["avgVerticalOscillation"])
            if act.get("avgGroundContactTime"):
                gcts.append(act["avgGroundContactTime"])
            if act.get("avgVerticalRatio"):
                vrats.append(act["avgVerticalRatio"])

        if voscs:    dynamics["vert_osc_cm"]     = round(statistics.mean(voscs), 1)
        if gcts:     dynamics["ground_contact_ms"] = round(statistics.mean(gcts), 1)
        if vrats:    dynamics["vert_ratio_pct"]  = round(statistics.mean(vrats), 2)

    return dynamics


def _fetch_sleep_week(client, today_dt: date) -> dict:
    """
    Average sleep score and duration for the past 7 days.
    """
    scores, durations = [], []
    for i in range(7):
        d = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        raw = _safe(lambda dt=d: client.get_sleep_data(dt), f"sleep {d}", None)
        if raw:
            daily = raw.get("dailySleepDTO", raw)
            score = daily.get("sleepScores", {}).get("overall", {}).get("value") or \
                    daily.get("sleepScore")
            dur   = daily.get("sleepTimeSeconds")
            if score: scores.append(score)
            if dur:   durations.append(dur / 3600)  # convert to hours

    result = {}
    if scores:
        result["avg_score"]  = round(statistics.mean(scores), 1)
        result["min_score"]  = min(scores)
    if durations:
        result["avg_hours"]  = round(statistics.mean(durations), 1)
        result["min_hours"]  = round(min(durations), 1)

    return result


# ── Calendar fetchers (unchanged from previous version) ───────────────────────

def _parse_target(step: dict) -> str:
    target = step.get("targetType") or {}
    t_key  = target.get("workoutTargetTypeKey", "")

    def pace_from_ms(ms):
        if not ms or ms <= 0: return None
        p = 1000 / ms / 60
        return f"{int(p)}:{int((p % 1) * 60):02d}/km"

    if t_key == "pace.zone":
        lo, hi = step.get("targetValueOne"), step.get("targetValueTwo")
        lo_s, hi_s = pace_from_ms(lo), pace_from_ms(hi)
        if lo_s and hi_s: return f"{hi_s}–{lo_s}/km"
        return lo_s or hi_s or "pace zone"
    elif t_key == "heart.rate.zone":
        lo, hi = step.get("targetValueOne"), step.get("targetValueTwo")
        if lo and hi: return f"{int(lo)}–{int(hi)} bpm"
        return "HR zone"
    elif t_key in ("open", "no.target", ""): return "open"
    return t_key.replace(".", " ")


def _parse_duration(step: dict) -> str:
    dur_type = (step.get("endCondition") or {}).get("conditionTypeKey", "")
    dur_val  = step.get("endConditionValue")
    if dur_type == "time" and dur_val:
        m, s = divmod(int(dur_val), 60)
        return f"{m}:{s:02d} min" if s else f"{m} min"
    if dur_type == "distance" and dur_val:
        metres = float(dur_val)
        return f"{metres/1000:.1f} km" if metres >= 1000 else f"{int(metres)} m"
    if dur_type == "lap.button": return "lap button"
    return dur_type or "–"


def _parse_steps(workout: dict) -> list[dict]:
    steps = []
    def _parse_one(step):
        step_type = ((step.get("stepType") or {}).get("stepTypeKey", "")
                     or step.get("type", "")).lower()
        return {"type": step_type, "duration": _parse_duration(step),
                "target": _parse_target(step)}

    for seg in workout.get("workoutSegments", []):
        for step in seg.get("workoutSteps", []):
            if step.get("type") in ("RepeatGroupDTO", "repeat"):
                repeats = step.get("numberOfIterations", 1)
                steps.append({"type": "repeat", "repeats": int(repeats),
                               "sub_steps": [_parse_one(s) for s in step.get("workoutSteps", [])]})
            else:
                steps.append(_parse_one(step))
    return steps


def _steps_to_text(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        if s["type"] == "repeat":
            sub = ", ".join(f"{ss['type']} {ss['duration']} @ {ss['target']}"
                           for ss in s["sub_steps"])
            lines.append(f"  × {s['repeats']}: {sub}")
        else:
            t = s["target"]
            if t and t != "open":
                lines.append(f"  {s['type'].capitalize()}: {s['duration']} @ {t}")
            else:
                lines.append(f"  {s['type'].capitalize()}: {s['duration']}")
    return "\n".join(lines) if lines else "(no structured steps)"


def _fetch_calendar_workouts(client, start_date: str, end_date: str) -> list[dict]:
    """Fetch scheduled workouts overlapping [start_date, end_date].

    get_scheduled_workouts(year, month) returns Garmin's raw calendar payload
    for that month — historically shaped as {"calendarItems": [...]} rather
    than a bare list, so both shapes are handled defensively here.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end   = datetime.strptime(end_date,   "%Y-%m-%d")
    items, months_seen = [], set()
    for d in [start, end]:
        key = (d.year, d.month)
        if key in months_seen: continue
        months_seen.add(key)
        batch = _safe(lambda y=d.year, m=d.month: client.get_scheduled_workouts(y, m),
                      f"calendar {d.year}-{d.month:02d}", {})
        if isinstance(batch, dict):
            batch = batch.get("calendarItems", [])
        items.extend(batch or [])

    return [
        item for item in items
        if start_date <= item.get("date", "")[:10] <= end_date
        and (item.get("itemType", "").lower() in ("workout", "scheduledworkout")
             or item.get("workoutId"))
    ]


def _match_to_activity(calendar_item: dict, week_acts: list[dict]):
    planned_date = calendar_item.get("date", "")[:10]
    for act in week_acts:
        if act.get("start_date_local", "")[:10] == planned_date and act.get("type") == "Run":
            return act
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def get_garmin_data(week_activities: list[dict], now: datetime | None = None) -> dict:
    """
    Fetches all Garmin data needed for the weekly report:
      - Analytics metrics (load, VO₂, HRV, readiness, dynamics, sleep)
      - Calendar (last week plan vs actual, next week preview)

    Gracefully degrades: if any individual metric fails, the rest still work.
    Returns a single dict with 'available', 'analytics', 'last_week', 'next_week'.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    today_dt    = now.date()
    today_str   = today_dt.strftime("%Y-%m-%d")

    days_since_monday = now.weekday()
    this_monday  = today_dt - timedelta(days=days_since_monday)
    last_monday  = this_monday - timedelta(weeks=1)
    last_sunday  = this_monday - timedelta(days=1)
    next_monday  = this_monday + timedelta(weeks=1)
    next_sunday  = next_monday + timedelta(days=6)

    last_week_start = last_monday.strftime("%Y-%m-%d")
    last_week_end   = last_sunday.strftime("%Y-%m-%d")
    next_week_start = next_monday.strftime("%Y-%m-%d")
    next_week_end   = next_sunday.strftime("%Y-%m-%d")

    print("\nFetching Garmin data…")

    try:
        client = _get_client()
    except Exception as e:
        print(f"  ✗ Garmin auth failed: {e}")
        return {"available": False, "error": str(e),
                "analytics": {}, "last_week": [], "next_week": []}

    # ── Analytics ─────────────────────────────────────────────────────────────
    analytics = {}

    analytics["training_status"] = _fetch_training_status(client, today_str)
    analytics["vo2max_trend"]     = _fetch_vo2max_trend(client, today_dt, weeks=5)
    analytics["race_predictions"] = _fetch_race_predictions(client, today_str)
    analytics["hrv"]              = _fetch_hrv_status(client, today_str)
    analytics["readiness"]        = _fetch_training_readiness(client, today_str)
    analytics["running_dynamics"] = _fetch_running_dynamics(client, today_dt)
    analytics["sleep"]            = _fetch_sleep_week(client, today_dt)

    # ── Calendar: last week ────────────────────────────────────────────────────
    last_week_cal = _fetch_calendar_workouts(client, last_week_start, last_week_end)
    print(f"  Last week calendar: {len(last_week_cal)} scheduled workouts")

    last_week = []
    for item in last_week_cal:
        wid   = item.get("workoutId")
        name  = item.get("title", item.get("workoutName", "Workout"))
        date  = item.get("date", "")[:10]
        steps, steps_text = [], "(no structured steps)"
        if wid:
            detail = _safe(lambda w=wid: client.get_workout_by_id(w), f"workout detail {wid}", None)
            if detail:
                steps      = _parse_steps(detail)
                steps_text = _steps_to_text(steps)
        actual  = _match_to_activity(item, week_activities)
        status  = "completed" if actual else "skipped"
        last_week.append({
            "workout_id": wid, "workout_name": name, "date": date,
            "steps": steps, "steps_text": steps_text,
            "status": status, "actual": actual, "matched": bool(actual),
        })
        print(f"    {'✓' if actual else '✗'} {date} {name[:40]}")

    # ── Calendar: next week ────────────────────────────────────────────────────
    next_week_cal = _fetch_calendar_workouts(client, next_week_start, next_week_end)
    print(f"  Next week calendar: {len(next_week_cal)} scheduled workouts")

    next_week = []
    for item in next_week_cal:
        wid   = item.get("workoutId")
        name  = item.get("title", item.get("workoutName", "Workout"))
        date  = item.get("date", "")[:10]
        steps, steps_text = [], "(no structured steps)"
        if wid:
            detail = _safe(lambda w=wid: client.get_workout_by_id(w), f"workout detail {wid}", None)
            if detail:
                steps      = _parse_steps(detail)
                steps_text = _steps_to_text(steps)
        next_week.append({
            "workout_id": wid, "workout_name": name, "date": date,
            "steps": steps, "steps_text": steps_text,
        })
        print(f"    → {date} {name[:40]}")

    return {
        "available":  True,
        "error":      None,
        "analytics":  analytics,
        "last_week":  last_week,
        "next_week":  next_week,
    }
