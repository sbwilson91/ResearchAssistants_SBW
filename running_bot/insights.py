"""
running_bot/insights.py

The key design principle here: every metric block fed to Claude is framed
as an analytical question, not a data dump. Claude is told what each metric
means and what to look for, so the output is interpretation rather than
transcription.
"""

import os
import json
import time
import requests


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-6"
MAX_TOKENS        = 4096
MAX_ATTEMPTS       = 3
RETRYABLE_STATUSES = {429, 500, 502, 503, 529}


def get_claude_insights(data: dict, athlete_context: str, race_data: dict | None = None) -> dict:
    system_prompt = (
        "You are providing weekly running analysis for a specific athlete. "
        "Use the following context to make your insights specific and personal:\n\n"
        + athlete_context
    )
    prompt = _build_prompt(data, race_data)

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key":         os.environ["ANTHROPIC_API_KEY"],
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      MODEL,
                    "max_tokens": MAX_TOKENS,
                    "system":     system_prompt,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=90,
            )
            if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS:
                print(f"⚠  Claude API {resp.status_code}, retrying (attempt {attempt}/{MAX_ATTEMPTS})…")
                time.sleep(5 * attempt)
                continue
            resp.raise_for_status()

            body = resp.json()
            raw = body["content"][0]["text"].strip()
            insights = _parse_insights(raw)

            if body.get("stop_reason") == "max_tokens":
                print("⚠  Claude response hit max_tokens — insights may be truncated.")

            print(f'✓ Insights: "{insights.get("headline","…")}"')
            return insights

        except requests.HTTPError as e:
            last_error = e
            if e.response.status_code not in RETRYABLE_STATUSES:
                print(f"✗  Claude API {e.response.status_code} (non-retryable): {e.response.text[:300]}")
                break
            if attempt < MAX_ATTEMPTS:
                print(f"⚠  Claude call failed ({e}), retrying (attempt {attempt}/{MAX_ATTEMPTS})…")
                time.sleep(5 * attempt)

        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            last_error = e
            if attempt < MAX_ATTEMPTS:
                print(f"⚠  Claude call failed ({e}), retrying (attempt {attempt}/{MAX_ATTEMPTS})…")
                time.sleep(5 * attempt)

    raise RuntimeError(f"Claude insights failed after {MAX_ATTEMPTS} attempts: {last_error}")


def _parse_insights(raw: str) -> dict:
    """Strip markdown fences / stray preamble text and parse the JSON object."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]

    return json.loads(text)


def _secs_to_time(secs: int) -> str:
    if not secs: return "–"
    h, rem = divmod(int(secs), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _pace(ms) -> str:
    if not ms or ms <= 0: return "–"
    p = 1000 / ms / 60
    return f"{int(p)}:{int((p % 1) * 60):02d}"


# ── Analytical data blocks ────────────────────────────────────────────────────
# Each block explains what the metric means and what to look for,
# so Claude produces analysis rather than reportage.

def _training_load_block(analytics: dict) -> str:
    ts = analytics.get("training_status", {})
    if not ts:
        return ""

    ratio     = ts.get("load_ratio")
    acute     = ts.get("acute_load")
    chronic   = ts.get("chronic_load")
    status    = ts.get("status_label", "unknown")
    rec_hours = ts.get("recovery_time_hours")

    ratio_interpretation = ""
    if ratio:
        if ratio > 1.5:
            ratio_interpretation = "DANGER ZONE — acute load is 50%+ above chronic baseline. Injury risk is high."
        elif ratio > 1.3:
            ratio_interpretation = "CAUTION — pushing above sustainable load. Monitor recovery signals closely."
        elif ratio >= 0.8:
            ratio_interpretation = "PRODUCTIVE ZONE — load is appropriately above chronic baseline."
        elif ratio >= 0.6:
            ratio_interpretation = "MAINTAINING — load roughly matches chronic baseline. Fitness will hold but not build."
        else:
            ratio_interpretation = "DETRAINING RISK — acute load well below chronic. Fitness may start declining."

    lines = ["TRAINING LOAD ANALYSIS:"]
    lines.append(f"  Status label:   {status}")
    if acute:   lines.append(f"  Acute load (7d):   {acute}")
    if chronic: lines.append(f"  Chronic load (28d): {chronic}")
    if ratio:
        lines.append(f"  Acute:chronic ratio: {ratio} — {ratio_interpretation}")
    if rec_hours:
        lines.append(f"  Recovery time needed: {rec_hours}h")
    lines.append(
        "\n  ANALYTICAL NOTE: The acute:chronic ratio is the primary injury risk signal. "
        "Reference it explicitly. A ratio >1.3 warrants a warning even if the athlete "
        "feels fine — overuse injuries typically lag the overload by 1–2 weeks."
    )
    return "\n".join(lines)


def _vo2max_block(analytics: dict) -> str:
    trend = analytics.get("vo2max_trend", [])
    if not trend:
        return ""

    lines = ["VO₂ MAX TREND (last 5 weeks):"]
    for pt in trend:
        lines.append(f"  {pt['date']}: {pt['vo2max']} ml/kg/min")

    if len(trend) >= 2:
        delta = round(trend[-1]["vo2max"] - trend[0]["vo2max"], 1)
        direction = "↑ improving" if delta > 0 else "↓ declining" if delta < 0 else "→ stable"
        lines.append(f"  Trend: {direction} ({'+' if delta >= 0 else ''}{delta} over {len(trend)} weeks)")

    current = trend[-1]["vo2max"] if trend else None
    lines.append(
        "\n  ANALYTICAL NOTE: VO₂ max changes slowly (weeks to months). "
        "A decline after heavy racing or travel is normal. "
        f"Current value of {current} ml/kg/min — contextualise against "
        "where this athlete has come from and what their targets imply. "
        "Sub-3:20 marathon typically requires VO₂ max ~55+; sub-1:30 HM ~58+."
    )
    return "\n".join(lines)


def _race_predictions_block(analytics: dict) -> str:
    preds = analytics.get("race_predictions", {})
    if not preds:
        return ""

    # Athlete's actual PBs and targets (hardcoded here, also in athlete_context)
    pbs = {
        "5k":           "19:52",
        "10k":          "43:34",
        "half_marathon": "1:32:55",
        "marathon":     "3:30:45",
    }
    targets = {
        "half_marathon": "1:30:00",
        "marathon":      "3:20:00",
    }

    lines = ["RACE PREDICTIONS vs PBs vs TARGETS:"]
    for dist, secs in preds.items():
        pred_str = _secs_to_time(secs)
        pb_str   = pbs.get(dist, "–")
        tgt_str  = targets.get(dist, "")

        gap_to_pb = ""
        if pb_str != "–":
            # Parse PB
            try:
                pb_parts = pb_str.split(":")
                if len(pb_parts) == 2:
                    pb_secs = int(pb_parts[0]) * 60 + int(pb_parts[1])
                else:
                    pb_secs = int(pb_parts[0]) * 3600 + int(pb_parts[1]) * 60 + int(pb_parts[2])
                diff = secs - pb_secs
                sign = "+" if diff > 0 else ""
                gap_to_pb = f" ({sign}{_secs_to_time(abs(diff))} {'behind' if diff > 0 else 'ahead of'} PB)"
            except Exception:
                pass

        gap_to_target = ""
        if tgt_str:
            try:
                tp = tgt_str.split(":")
                if len(tp) == 2:
                    tgt_secs = int(tp[0]) * 60 + int(tp[1])
                else:
                    tgt_secs = int(tp[0]) * 3600 + int(tp[1]) * 60 + int(tp[2])
                diff = secs - tgt_secs
                sign = "+" if diff > 0 else ""
                gap_to_target = f", {sign}{_secs_to_time(abs(diff))} {'from' if diff > 0 else 'inside'} target"
            except Exception:
                pass

        label = dist.replace("_", " ").title()
        lines.append(f"  {label}: predicted {pred_str}{gap_to_pb}{gap_to_target}")

    lines.append(
        "\n  ANALYTICAL NOTE: These are Garmin model predictions, not guarantees. "
        "Compare them against actual race results to calibrate how well the model "
        "tracks this athlete. If predictions consistently over or underestimate, "
        "note the pattern. The gap-to-target for HM and marathon is the most "
        "actionable number — is the athlete on track, ahead of schedule, or behind?"
    )
    return "\n".join(lines)


def _hrv_block(analytics: dict) -> str:
    hrv = analytics.get("hrv", {})
    if not hrv:
        return ""

    weekly_avg = hrv.get("weekly_avg")
    last_night = hrv.get("last_night")
    status     = hrv.get("status", "UNKNOWN")
    deviation  = hrv.get("deviation_from_baseline")
    bal_low    = hrv.get("baseline_balanced_low")
    bal_high   = hrv.get("baseline_balanced_high")

    lines = ["HRV STATUS:"]
    lines.append(f"  Status:         {status}")
    if weekly_avg: lines.append(f"  7-day avg:      {weekly_avg} ms")
    if last_night: lines.append(f"  Last night:     {last_night} ms")
    if bal_low and bal_high:
        lines.append(f"  Personal baseline: {bal_low}–{bal_high} ms")
    if deviation is not None:
        direction = "above" if deviation > 0 else "below"
        lines.append(f"  Deviation:      {abs(deviation)} ms {direction} baseline midpoint")

    status_meaning = {
        "BALANCED":   "recovery is adequate, nervous system is coping with training load",
        "UNBALANCED": "nervous system showing strain — worth monitoring, reduce intensity if it persists",
        "LOW":        "significant stress signal — consider an easy day or rest regardless of how the athlete feels",
        "POOR":       "red flag — acute overreach, illness, or major life stress likely present",
    }.get(status.upper(), "status unclear")

    lines.append(f"\n  Status meaning: {status_meaning}")
    lines.append(
        "  ANALYTICAL NOTE: HRV should be read alongside training load, not in isolation. "
        "Low HRV + high load ratio = high priority warning. "
        "Low HRV + low load = likely non-training stressor (sleep, illness, travel, life stress). "
        "Reference which scenario applies to this athlete this week."
    )
    return "\n".join(lines)


def _readiness_block(analytics: dict) -> str:
    r = analytics.get("readiness", {})
    if not r or r.get("score") is None:
        return ""

    score = r["score"]
    level = r.get("level", "")

    # Which factor is dragging the score down?
    factors = {
        "HRV":          r.get("hrv_factor"),
        "Sleep":        r.get("sleep_factor"),
        "Recovery time": r.get("recovery_factor"),
        "Acute load":   r.get("acute_load_factor"),
    }
    low_factors = [k for k, v in factors.items() if v is not None and v < 50]

    lines = ["TRAINING READINESS:"]
    lines.append(f"  Score: {score}/100  ({level})")
    if low_factors:
        lines.append(f"  Dragged down by: {', '.join(low_factors)}")
    for k, v in factors.items():
        if v is not None:
            lines.append(f"  {k} factor: {v}/100")

    lines.append(
        "\n  ANALYTICAL NOTE: Readiness predicts capacity for today's training, "
        "not the week. A score <50 before a key quality session (Tuesday MRC, "
        "Thursday intervals) is a genuine warning — adaptation from a hard session "
        "requires adequate readiness going in. Identify which factor is lowest and "
        "explain what it suggests the athlete should prioritise."
    )
    return "\n".join(lines)


def _running_dynamics_block(analytics: dict) -> str:
    rd = analytics.get("running_dynamics", {})
    if not rd:
        return ""

    cadence    = rd.get("cadence_spm")
    n_intervals = rd.get("cadence_n_intervals")
    vert_osc   = rd.get("vert_osc_cm")
    gct        = rd.get("ground_contact_ms")
    vert_rat   = rd.get("vert_ratio_pct")

    lines = ["RUNNING FORM METRICS:"]
    if cadence:
        gap    = round(cadence - 170, 1)
        status = "AT TARGET" if cadence >= 170 else f"{abs(gap)} spm below 170 target"
        lines.append(
            f"  Cadence (threshold efforts, {n_intervals} interval"
            f"{'s' if n_intervals != 1 else ''}): {cadence} spm — {status}"
        )
    if vert_osc:
        status = "AT TARGET" if vert_osc <= 8.0 else f"{round(vert_osc - 8.0, 1)}cm above 8.0cm target"
        lines.append(f"  Vertical oscillation (weekly avg): {vert_osc} cm — {status}")
    if gct:
        lines.append(f"  Ground contact time (weekly avg):  {gct} ms")
    if vert_rat:
        lines.append(f"  Vertical ratio (weekly avg):       {vert_rat}%")

    lines.append(
        "\n  ANALYTICAL NOTE: Cadence here is measured only from sustained fast "
        "efforts (intervals/threshold work), not blended with easy-run cadence — "
        "easy runs are intentionally lower and don't reflect the athlete's ceiling. "
        "This athlete's known limiters: cadence 163–170 spm on easy days but "
        "180+ during quality work (target 170–180 even under load), vertical "
        "oscillation ~92mm (target <80mm). If no cadence figure is present, no "
        "threshold/quality session was detected this week — don't assume cadence "
        "regressed, there's simply no qualifying data. If cadence during this "
        "week's quality work moved versus prior weeks, explain why that matters "
        "for economy and what it might indicate about fatigue or focus."
    )
    return "\n".join(lines)


def _sleep_block(analytics: dict) -> str:
    sleep = analytics.get("sleep", {})
    if not sleep:
        return ""

    avg_score = sleep.get("avg_score")
    min_score = sleep.get("min_score")
    avg_hours = sleep.get("avg_hours")
    min_hours = sleep.get("min_hours")

    lines = ["SLEEP (7-day):"]
    if avg_hours: lines.append(f"  Avg duration: {avg_hours}h")
    if min_hours: lines.append(f"  Worst night:  {min_hours}h")
    if avg_score: lines.append(f"  Avg score:    {avg_score}/100")
    if min_score: lines.append(f"  Worst score:  {min_score}/100")

    lines.append(
        "\n  ANALYTICAL NOTE: Only flag sleep if it's genuinely poor "
        "(<6.5h average or score <60) AND it correlates with something else — "
        "low HRV, low readiness, unexpected HR elevation during sessions. "
        "A single bad night is noise. A pattern across the week during a heavy "
        "training block is signal, especially for an athlete managing a demanding "
        "research career alongside training."
    )
    return "\n".join(lines)


def _speed_session_block(sessions: list[dict]) -> str:
    if not sessions:
        return "No speed sessions this week."
    lines = []
    for s in sessions:
        lines.append(f"\n{s['date']} — {s['name']} ({s['dist_km']} km)")
        lines.append(f"  {s['n_intervals']} intervals | Best {s['best_pace']}/km | "
                     f"Avg effort {s['avg_effort_pace']}/km")
        if s.get("session_peak_hr"):
            lines.append(f"  Peak HR {s['session_peak_hr']} bpm | "
                         f"Avg session HR {s.get('session_avg_hr','–')} bpm")
        for i, iv in enumerate(s["intervals"], 1):
            dur = f"{iv['duration_s']//60}:{iv['duration_s']%60:02d}"
            hr  = f", HR {iv['mean_hr']}" if iv.get("mean_hr") else ""
            cad = f", {iv['mean_cad']} spm" if iv.get("mean_cad") else ""
            lines.append(f"  #{i}: {dur} @ {iv['mean_pace']}/km (peak {iv['peak_pace']}/km){hr}{cad}")
        for i, rec in enumerate(s.get("recoveries", []), 1):
            if rec.get("mean_ms"):
                rdur = f"{rec['duration_s']//60}:{rec['duration_s']%60:02d}"
                rhr  = f", HR {rec['mean_hr']}" if rec.get("mean_hr") else ""
                lines.append(f"  Recovery {i}: {rdur} @ {_pace(rec['mean_ms'])}/km{rhr}")
    return "\n".join(lines)


def _race_context_block(race_data: dict) -> str:
    if not race_data or not race_data.get("races"):
        return ""

    lines = ["RACE COUNTDOWN & TRAINING PHASE:"]
    phase        = race_data.get("phase", "Base")
    phase_detail = race_data.get("phase_detail", "")
    lines.append(f"  Current phase: {phase} — {phase_detail}")
    lines.append("")

    for r in race_data["races"]:
        goal_str = f" (goal: {r['goal']})" if r.get("goal") else ""
        primary_tag = " ★ PRIMARY" if r.get("primary") else ""
        lines.append(f"  {r['name']} [{r['type_label']}]{primary_tag}")
        lines.append(f"    Date: {r['date_str']}  |  {r['days_until']} days away ({r['weeks_until']} weeks){goal_str}")
        lines.append(f"    Phase: {r['phase']} — {r['phase_detail']}")

    primary = race_data.get("primary")
    if primary:
        lines.append(
            f"\n  ANALYTICAL NOTE: The athlete is {primary['weeks_until']} weeks from {primary['name']}. "
            f"Phase is '{primary['phase']}'. Frame your analysis in terms of what this phase demands — "
            f"what kind of training stimulus is appropriate, what adaptations should be occurring, "
            f"and whether this week's load and recovery picture supports or undermines that. "
            f"If a race goal exists ({primary.get('goal') or 'none stated'}), assess whether "
            f"current fitness trajectory (VO₂, race predictions) is on track to achieve it."
        )
    return "\n".join(lines)


def _garmin_plan_block(garmin: dict) -> str:
    if not garmin.get("available"):
        return "Garmin calendar not available."
    lines = []

    last_week = garmin.get("last_week", [])
    if last_week:
        lines.append("LAST WEEK — PLAN VS ACTUAL:")
        for w in last_week:
            icon = "✓" if w["status"] == "completed" else "✗"
            lines.append(f"\n  {icon} {w['date']} — {w['workout_name']} [{w['status'].upper()}]")
            if w["steps_text"] != "(no structured steps)":
                lines.append(f"  Planned:\n{w['steps_text']}")
            if w["status"] == "completed" and w.get("actual"):
                act = w["actual"]
                dist = round(act.get("distance", 0) / 1000, 1)
                lines.append(f"  Actual: {dist}km @ {_pace(act.get('average_speed'))}/km, "
                            f"avg HR {act.get('average_heartrate','–')} bpm")
                if act.get("description"):
                    lines.append(f"  Note: \"{act['description'][:200]}\"")
            elif w["status"] == "skipped":
                lines.append("  → No matching run found for this date")

    next_week = garmin.get("next_week", [])
    if next_week:
        lines.append("\nNEXT WEEK — UPCOMING:")
        for w in next_week:
            lines.append(f"\n  {w['date']} — {w['workout_name']}")
            if w["steps_text"] != "(no structured steps)":
                lines.append(f"  {w['steps_text']}")

    return "\n".join(lines)


# ── Main prompt builder ───────────────────────────────────────────────────────

def _build_prompt(data: dict, race_data: dict | None = None) -> str:
    tw        = data.get("this_week") or {}
    aeff      = data.get("aero_eff_now")
    aeff_prev = data.get("aero_eff_prev")
    garmin    = data.get("garmin", {})
    analytics = garmin.get("analytics", {}) if garmin.get("available") else {}

    # Weekly summary
    aeff_line = ""
    if aeff:
        m, s = int(aeff), int((aeff % 1) * 60)
        aeff_line = f"Aerobic efficiency: {m}:{s:02d}/km at 130–145 bpm"
        if aeff_prev:
            diff_s = round((aeff_prev - aeff) * 60)
            aeff_line += f" ({abs(diff_s)}s/km {'faster' if diff_s > 0 else 'slower'} vs prior 8 wks)"

    wk_lines = [
        f"  {w['week']}: {w['dist_km']}km, {w['runs']} runs"
        + (f", HR {w['avg_hr']}" if w.get("avg_hr") else "")
        for w in data.get("weekly_series", [])[-8:]
    ]

    notable_lines = []
    for n in data.get("notable", [])[:8]:
        line = f"  {n['date']} — {n['name']} ({n['dist_km']}km, {n['pace']}/km"
        if n.get("hr"): line += f", HR {n['hr']}"
        line += ")"
        if n.get("desc"): line += f'\n    Note: "{n["desc"][:200]}"'
        notable_lines.append(line)

    pr_lines = []
    for p in data.get("all_parkruns", [])[-5:]:
        m, s = int(p["time_min"]), int((p["time_min"] % 1) * 60)
        pr_lines.append(f"  {p['date']}: {m}:{s:02d}" + (f", HR {p['hr']}" if p["hr"] else ""))
    bp = data.get("best_parkrun")
    if bp:
        bm, bs = int(bp["time_min"]), int((bp["time_min"] % 1) * 60)
        pr_lines.append(f"  All-time PB: {bm}:{bs:02d} ({bp['date']})")

    zones   = data.get("zone_dist", {})
    tz_tot  = sum(zones.values()) or 1
    z_lines = [f"  {z}: {round(v/tz_tot*100)}%" for z, v in zones.items() if v > 0]

    # Build analytical blocks
    load_block     = _training_load_block(analytics)
    vo2_block      = _vo2max_block(analytics)
    pred_block     = _race_predictions_block(analytics)
    hrv_block      = _hrv_block(analytics)
    ready_block    = _readiness_block(analytics)
    dynamics_block = _running_dynamics_block(analytics)
    sleep_block    = _sleep_block(analytics)
    speed_block    = _speed_session_block(data.get("speed_sessions", []))
    plan_block     = _garmin_plan_block(garmin)
    race_block     = _race_context_block(race_data or {})

    return f"""WEEK: {data['week_label']}

THIS WEEK:
  Distance:  {tw.get('dist_km', 0)} km  |  Runs: {tw.get('runs', 0)}
  Avg pace:  {tw.get('avg_pace', '–')}/km  |  Avg HR: {tw.get('avg_hr', '–')} bpm
  Elevation: {tw.get('elev_m', 0)} m  |  8-wk rolling avg: {data['rolling_avg_km']} km
  vs rolling avg: {round(tw.get('dist_km', 0) - data['rolling_avg_km'], 1)} km
  Streak: {data['current_streak']} days
  {aeff_line}

WEEKLY VOLUME TREND (last 8 weeks):
{chr(10).join(wk_lines)}

HR ZONES THIS WEEK:
{chr(10).join(z_lines)}

NOTABLE ACTIVITIES:
{chr(10).join(notable_lines) if notable_lines else "  None"}

PARKRUNS:
{chr(10).join(pr_lines) if pr_lines else "  None recently"}

━━━ GARMIN ANALYTICS ━━━

{load_block}

{vo2_block}

{pred_block}

{hrv_block}

{ready_block}

{dynamics_block}

{sleep_block}

━━━ SPEED SESSION DATA (Garmin streams) ━━━
{speed_block}

━━━ TRAINING PLAN ━━━
{plan_block}

━━━ RACE SCHEDULE ━━━
{race_block}

---

IMPORTANT: Your job is ANALYSIS, not reporting. Do not list metrics back. Every
number you mention should be in service of explaining something — a pattern,
a risk, a readiness state, a gap between where the athlete is and where they
want to be. The metrics are evidence; the analysis is the verdict.

Cross-reference aggressively: a low HRV week combined with a high load ratio
and a cadence drop is a richer story than any single metric. Look for
combinations. Flag things that look contradictory (high readiness but low HRV,
fast intervals but declining VO₂ trend).

Respond with a single JSON object — no markdown fences:

{{
  "headline": "One punchy sentence (max 15 words). Should capture the actual story of the week, not just the mileage.",

  "week_narrative": "2–3 paragraphs. What happened this week and why it matters. Reference patterns in the data. Name sessions specifically. Be direct — do not hedge everything.",

  "physiological_analysis": "2–3 paragraphs synthesising the Garmin analytics. This is the key analytical section. Discuss: what the load ratio means for this athlete RIGHT NOW, what the VO₂ trend implies about fitness trajectory, how HRV and readiness combine to paint a recovery picture, whether race predictions are moving toward or away from stated targets (sub-1:30 HM, sub-3:20 marathon). Connect dots across metrics — don't treat each one as a separate paragraph.",

  "speed_analysis": "1–2 paragraphs on quality sessions from the Garmin stream data. Reference actual paces against this athlete's benchmarks (parkrun PB = 3:58/km, HM avg = 4:24/km). Discuss recovery quality between intervals and what peak HRs indicate about effort level and fitness.",

  "form_analysis": "1–2 paragraphs on running form. Cadence is measured from this week's threshold/quality efforts only (target 170–180 spm under load; easy-run cadence is intentionally lower and irrelevant here — if no quality session was detected, say so rather than implying a regression). Vertical oscillation target is <80mm (currently ~92mm, weekly average). Explain the mechanism: how do these link to each other, to economy, and to the athlete's injury history (calves). If dynamics improved or worsened this week, say why that matters.",

  "plan_vs_actual": "1 paragraph comparing Garmin's scheduled workouts to what was actually run. What was planned, what was done, what was skipped. If skipped: was it appropriate given the load ratio and HRV picture? Don't moralize about missed sessions — evaluate whether the choice made physiological sense.",

  "next_week_preview": "2–3 sentences. What's on the Garmin schedule, and given this week's load ratio, HRV, and readiness trend, what should the athlete's approach be going in? Specific — name the key session and what target paces would represent appropriate effort.",

  "key_signals": [
    {{"signal": "short label (5 words max)", "detail": "1–2 sentences of specific, evidence-based analysis. No generic advice.", "type": "positive|warning|neutral"}},
    ... (4–6 signals. At least one must cross-reference two or more metrics.)
  ],

  "next_week_focus": "1–2 sentences. The single most important thing to execute well next week, grounded in this week's data."
}}"""
