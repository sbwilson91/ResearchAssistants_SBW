"""
running_bot/monthly_summary.py

Standalone script: fetches last month's Strava activities, computes stats,
asks Claude for a narrative month-in-review, saves Markdown, emails it.

Run via: python monthly_summary.py
Or triggered by the monthly_summary.yml workflow on the 1st of each month.
"""
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yaml

HERE       = Path(__file__).parent
LOGS_DIR   = HERE / "monthly_logs"
CONFIG     = HERE / "config.yaml"
ATHLETE_MD = HERE / "athlete_context.md"

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL             = "claude-sonnet-4-6"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def _secs_to_hms(s: int) -> str:
    h, r = divmod(int(s), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _pace(ms) -> str:
    if not ms or ms <= 0: return "–"
    p = 1000 / ms / 60
    return f"{int(p)}:{int((p % 1) * 60):02d}"


# ── Strava ────────────────────────────────────────────────────────────────────

def _refresh_token() -> str:
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
        "grant_type":    "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_month(token: str, year: int, month: int) -> list:
    from calendar import monthrange
    _, last_day = monthrange(year, month)
    after  = datetime(year, month, 1,  tzinfo=timezone.utc).timestamp()
    before = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc).timestamp()

    acts, page = [], 1
    while True:
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"after": int(after), "before": int(before), "per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        acts.extend(batch)
        page += 1
    return acts


# ── Stats ─────────────────────────────────────────────────────────────────────

def _compute_stats(acts: list, cfg: dict) -> dict:
    runs = [a for a in acts if a.get("type") == "Run"]
    if not runs:
        return {}

    total_km    = round(sum(a.get("distance", 0) for a in runs) / 1000, 1)
    total_elev  = round(sum(a.get("total_elevation_gain", 0) for a in runs))
    total_time  = sum(a.get("moving_time", 0) for a in runs)
    avg_hr_runs = [a["average_heartrate"] for a in runs if a.get("average_heartrate")]
    avg_hr      = round(sum(avg_hr_runs) / len(avg_hr_runs)) if avg_hr_runs else None

    # Longest run
    longest = max(runs, key=lambda a: a.get("distance", 0))
    longest_km = round(longest.get("distance", 0) / 1000, 1)

    # Best pace run (≥5km, excluding parkruns for noise)
    pace_candidates = [a for a in runs if a.get("distance", 0) >= 5000
                       and a.get("average_speed") and a.get("average_speed") > 0]
    fastest = min(pace_candidates, key=lambda a: 1000 / a["average_speed"],
                  default=None) if pace_candidates else None

    # Parkruns
    pr_min  = cfg.get("parkrun_distance_min_km", 4.8) * 1000
    pr_max  = cfg.get("parkrun_distance_max_km", 5.3) * 1000
    parkruns = [a for a in runs if pr_min <= a.get("distance", 0) <= pr_max]
    best_pr  = min(parkruns, key=lambda a: a.get("moving_time", 9999), default=None)

    # Weekly breakdown
    weekly: dict[str, float] = {}
    for a in runs:
        d = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
        wk = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        weekly[wk] = weekly.get(wk, 0) + a.get("distance", 0) / 1000
    weekly_kms = sorted(weekly.items())

    # Notable activities (long or named)
    notable = []
    for a in runs:
        km = a.get("distance", 0) / 1000
        name = a.get("name", "")
        if km >= 15 or any(k in name.lower() for k in ("race", "parkrun", "tempo", "interval", "long")):
            notable.append({
                "date":    a["start_date_local"][:10],
                "name":    name,
                "dist_km": round(km, 1),
                "pace":    _pace(a.get("average_speed")),
                "hr":      a.get("average_heartrate"),
                "desc":    a.get("description", ""),
            })

    return {
        "total_km":    total_km,
        "total_runs":  len(runs),
        "total_elev":  total_elev,
        "total_time":  _secs_to_hms(total_time),
        "avg_hr":      avg_hr,
        "longest_km":  longest_km,
        "longest_date": longest.get("start_date_local", "")[:10],
        "fastest_pace": _pace(fastest.get("average_speed")) if fastest else None,
        "fastest_date": fastest.get("start_date_local", "")[:10] if fastest else None,
        "best_pr_time": _secs_to_hms(best_pr["moving_time"]) if best_pr else None,
        "best_pr_date": best_pr["start_date_local"][:10] if best_pr else None,
        "weekly_kms":  weekly_kms,
        "notable":     notable[:8],
        "run_types":   len(set(a.get("workout_type") for a in runs)),
    }


# ── Claude ────────────────────────────────────────────────────────────────────

def _call_claude(stats: dict, month_label: str, athlete_context: str) -> dict:
    weekly_lines = "\n".join(f"  {wk}: {round(km,1)}km" for wk, km in stats.get("weekly_kms", []))
    notable_lines = "\n".join(
        f"  {n['date']} — {n['name']} ({n['dist_km']}km @ {n['pace']}/km"
        + (f", HR {n['hr']}" if n['hr'] else "") + ")"
        for n in stats.get("notable", [])
    )

    prompt = f"""Month: {month_label}

STATS:
  Total distance:  {stats.get('total_km')} km
  Total runs:      {stats.get('total_runs')}
  Total elevation: {stats.get('total_elev')} m
  Total time:      {stats.get('total_time')}
  Avg HR:          {stats.get('avg_hr', '–')} bpm
  Longest run:     {stats.get('longest_km')} km ({stats.get('longest_date')})
  Fastest pace:    {stats.get('fastest_pace', '–')}/km ({stats.get('fastest_date', '')})
  Best parkrun:    {stats.get('best_pr_time', '–')} ({stats.get('best_pr_date', '')})

WEEKLY BREAKDOWN:
{weekly_lines}

NOTABLE ACTIVITIES:
{notable_lines or '  None'}

---

Write a short monthly running review. Be direct and specific — reference actual numbers.
Don't hedge. Respond with JSON (no markdown fences):

{{
  "headline": "One punchy sentence, max 12 words. Capture the month's story.",
  "narrative": "2–3 paragraphs. What happened, what it means for the athlete's trajectory, honest assessment of progress vs targets (sub-1:30 HM, Backyard Ultra). Name specific sessions. No generic advice.",
  "highlight": "The single best thing about this month. 1–2 sentences.",
  "outlook": "What this month sets up for next month. 1–2 sentences, specific."
}}"""

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key":         os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      MODEL,
            "max_tokens": 1200,
            "system":     (
                "You are providing monthly running analysis for a specific athlete. "
                "Use this context:\n\n" + athlete_context
            ),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


# ── Markdown ──────────────────────────────────────────────────────────────────

def _build_markdown(stats: dict, insights: dict, month_label: str) -> str:
    weekly_table = "\n".join(
        f"| {wk} | {round(km,1)} km |"
        for wk, km in stats.get("weekly_kms", [])
    )

    notable_section = ""
    for n in stats.get("notable", []):
        notable_section += f"- **{n['date']}** {n['name']} — {n['dist_km']}km @ {n['pace']}/km\n"

    return f"""# {month_label} — Monthly Running Summary

> {insights.get('headline', '')}

**{stats.get('total_km')} km · {stats.get('total_runs')} runs · {stats.get('total_elev')}m elevation · {stats.get('total_time')}**

---

{insights.get('narrative', '')}

**Highlight:** {insights.get('highlight', '')}

**Looking ahead:** {insights.get('outlook', '')}

---

## Weekly Breakdown

| Week | Distance |
|------|----------|
{weekly_table}

## Notable Activities

{notable_section or '_None this month._'}

---
*Generated by running\\_bot · {MODEL}*
"""


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_email(subject: str, body_md: str) -> None:
    sender   = os.environ["EMAIL_SENDER"]
    receiver = os.environ["EMAIL_RECEIVER"]
    password = os.environ["EMAIL_PASSWORD"]

    # Convert minimal markdown to HTML for email
    html = (
        "<html><body style='font-family:sans-serif;max-width:680px;margin:auto;"
        "background:#080b12;color:#dde2f0;padding:32px;'>"
        + body_md.replace("\n\n", "<br><br>").replace("**", "<b>").replace("# ", "<h2>").replace("## ", "<h3>")
        + "</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(body_md, "plain", "utf-8"))
    msg.attach(MIMEText(html,    "html",  "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"✓ Email sent → {receiver}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    cfg = _load_config()

    # Last calendar month
    today      = date.today()
    first_this = today.replace(day=1)
    last_month = first_this - timedelta(days=1)
    year, month = last_month.year, last_month.month
    month_label = last_month.strftime("%B %Y")

    print(f"=== Monthly Summary — {month_label} ===")

    athlete_context = ATHLETE_MD.read_text(encoding="utf-8") if ATHLETE_MD.exists() else ""

    token = _refresh_token()
    print(f"Fetching {month_label} activities…")
    acts  = _fetch_month(token, year, month)
    print(f"  → {len(acts)} activities")

    runs = [a for a in acts if a.get("type") == "Run"]
    if not runs:
        print("No runs found. Skipping.")
        return

    stats = _compute_stats(acts, cfg)
    print(f"  {stats['total_runs']} runs · {stats['total_km']}km · {stats['total_elev']}m")

    print("Asking Claude for narrative…")
    try:
        insights = _call_claude(stats, month_label, athlete_context)
        print(f'  "{insights.get("headline","")}"')
    except Exception as e:
        print(f"⚠  Claude failed: {e}")
        insights = {
            "headline":  f"{month_label} — {stats['total_km']}km across {stats['total_runs']} runs",
            "narrative": "AI narrative unavailable.",
            "highlight": "–", "outlook": "–",
        }

    md = _build_markdown(stats, insights, month_label)

    LOGS_DIR.mkdir(exist_ok=True)
    out_path = LOGS_DIR / f"{year}-{month:02d}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"✓ Saved → {out_path}")

    if cfg.get("send_email", True):
        try:
            subject = (f"🏃 {month_label} — {stats['total_km']}km "
                       f"({stats['total_runs']} runs) · {insights['headline']}")
            _send_email(subject, md)
        except KeyError as e:
            print(f"Email skipped — missing env var: {e}")
        except Exception as e:
            print(f"⚠  Email failed: {e}")


if __name__ == "__main__":
    run()
