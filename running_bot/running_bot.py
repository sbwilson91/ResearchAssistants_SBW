#!/usr/bin/env python3
"""running_bot/running_bot.py"""

import os
import datetime
from pathlib import Path
import yaml

from strava import refresh_access_token, build_report_data
from speed_sessions import get_speed_sessions
from insights import get_claude_insights
from races import get_race_data
from parkrun_scraper import fetch_parkrun_results, summarise_parkrun
from report import generate_html
from utils.email_logic import send_email


def load_config():
    with open(Path(__file__).parent / "config.yaml") as f:
        return yaml.safe_load(f)


def load_athlete_context():
    return (Path(__file__).parent / "athlete_context.md").read_text(encoding="utf-8")


def run():
    print(f"=== Running Bot — {datetime.date.today()} ===")
    cfg         = load_config()
    output_dir  = Path(__file__).parent / cfg.get("output_dir", "reports")
    output_dir.mkdir(exist_ok=True)
    history_wks = cfg.get("history_weeks", 16)

    athlete_context = load_athlete_context()

    # 1. Strava
    access_token, new_refresh = refresh_access_token()
    if new_refresh:
        Path("new_refresh_token.txt").write_text(new_refresh)

    # 2. Weekly metrics
    data = build_report_data(access_token, history_weeks=history_wks)

    # 3. Speed sessions (Strava stream data)
    print("\nAnalysing speed sessions…")
    data["speed_sessions"] = get_speed_sessions(access_token, data.get("this_week_all", []))

    # 4. Garmin (analytics + calendar)
    garmin_data = {"available": False, "analytics": {}, "last_week": [], "next_week": []}
    if cfg.get("garmin_enabled", True) and os.environ.get("GARMIN_EMAIL"):
        try:
            from garmin import get_garmin_data
            garmin_data = get_garmin_data(data.get("this_week_all", []))
        except Exception as e:
            print(f"⚠  Garmin failed: {e}")
    data["garmin"] = garmin_data

    # 5. parkrun leaderboard
    parkrun_athlete_id = cfg.get("parkrun_athlete_id", "")
    if parkrun_athlete_id:
        try:
            pr_results = fetch_parkrun_results(parkrun_athlete_id)
            data["parkrun_leaderboard"] = summarise_parkrun(pr_results)
        except Exception as e:
            print(f"⚠  parkrun scraper: {e}")
            data["parkrun_leaderboard"] = {}
    else:
        data["parkrun_leaderboard"] = {}

    # 6. Race countdown
    race_data = get_race_data(cfg)
    if race_data["races"]:
        primary = race_data.get("primary")
        print(f"\n  Phase: {race_data['phase']} | Next race: {primary['name']} in {primary['days_until']}d")

    # 6. Claude insights
    try:
        insights = get_claude_insights(data, athlete_context, race_data=race_data)
    except Exception as e:
        print(f"⚠  Claude error: {e}")
        tw = data.get("this_week") or {}
        insights = {
            "headline": f"{data['week_label']} — {tw.get('dist_km','?')} km",
            "week_narrative": "AI insights unavailable.",
            "physiological_analysis": "", "speed_analysis": "",
            "form_analysis": "", "plan_vs_actual": "",
            "next_week_preview": "", "key_signals": [],
            "next_week_focus": "Review charts manually.",
        }

    # 7. Render + save
    html     = generate_html(data, insights, history_weeks=history_wks, race_data=race_data)
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    for path in [output_dir / f"report_{date_str}.html", output_dir / "index.html"]:
        path.write_text(html, encoding="utf-8")
    print(f"✓ report_{date_str}.html")

    # 8. Email
    if cfg.get("send_email", True):
        try:
            tw = data.get("this_week") or {}
            send_email(f"🏃 Weekly Run Report — {data['week_label']} — {tw.get('dist_km','?')} km", html)
            print("✓ Email sent")
        except Exception as e:
            print(f"⚠  Email: {e}")

    # 9. Summary
    tw = data.get("this_week") or {}
    print(f"\n── {data['week_label']} ──────────────────")
    print(f"  {tw.get('dist_km','–')} km · {tw.get('avg_pace','–')}/km · {tw.get('avg_hr','–')} bpm")
    if garmin_data.get("available"):
        ts = garmin_data.get("analytics", {}).get("training_status", {})
        print(f"  Load ratio: {ts.get('load_ratio','–')} | Status: {ts.get('status_label','–')}")
    print(f'\n  "{insights["headline"]}"')


if __name__ == "__main__":
    run()
