"""
running_bot/report.py

The Garmin analytics section is deliberately not a metrics table.
Each card shows the number + Claude's interpretation of that number,
so the reader sees context not just values.
"""

import json


def _pace(ms):
    if not ms or ms <= 0: return "–"
    p = 1000 / ms / 60
    return f"{int(p)}:{int((p % 1) * 60):02d}"


def _secs_to_time(secs):
    if not secs: return "–"
    h, rem = divmod(int(secs), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── Garmin Analytics section ──────────────────────────────────────────────────

def _analytics_section(garmin: dict, insights: dict) -> str:
    if not garmin.get("available"):
        return ""

    analytics = garmin.get("analytics", {})
    if not analytics:
        return ""

    cards = []

    # ── Training Load card ────────────────────────────────────────────────────
    ts = analytics.get("training_status", {})
    if ts:
        ratio   = ts.get("load_ratio")
        acute   = ts.get("acute_load")
        chronic = ts.get("chronic_load")
        status  = ts.get("status_label", "–").replace("_", " ").title()

        if ratio:
            if ratio > 1.5:   ratio_color, ratio_label = "#ef4444", "Danger zone"
            elif ratio > 1.3: ratio_color, ratio_label = "#f97316", "High"
            elif ratio >= 0.8: ratio_color, ratio_label = "#22c55e", "Productive"
            elif ratio >= 0.6: ratio_color, ratio_label = "#f59e0b", "Maintaining"
            else:              ratio_color, ratio_label = "#ef4444", "Detraining risk"
        else:
            ratio_color, ratio_label = "#4a5270", "–"

        cards.append(f"""
      <div class="analytics-card">
        <div class="ac-header">
          <span class="ac-icon">⚡</span>
          <span class="ac-title">Training Load</span>
          <span class="ac-badge" style="color:{ratio_color}">{status}</span>
        </div>
        <div class="ac-metrics">
          <div class="ac-metric">
            <span class="ac-mk">Acute (7d)</span>
            <span class="ac-mv">{acute or "–"}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Chronic (28d)</span>
            <span class="ac-mv">{chronic or "–"}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Ratio</span>
            <span class="ac-mv" style="color:{ratio_color}">{ratio or "–"}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Zone</span>
            <span class="ac-mv" style="color:{ratio_color};font-size:11px;">{ratio_label}</span>
          </div>
        </div>
      </div>""")

    # ── VO₂ Max card ──────────────────────────────────────────────────────────
    vo2_trend = analytics.get("vo2max_trend", [])
    if vo2_trend:
        current = vo2_trend[-1]["vo2max"]
        first   = vo2_trend[0]["vo2max"]
        delta   = round(current - first, 1)
        delta_color = "#22c55e" if delta >= 0 else "#ef4444"
        delta_str   = f"{'+'if delta>=0 else ''}{delta}"

        sparkline = json.dumps([p["vo2max"] for p in vo2_trend])
        labels    = json.dumps([p["date"][5:] for p in vo2_trend])

        cards.append(f"""
      <div class="analytics-card">
        <div class="ac-header">
          <span class="ac-icon">❤️</span>
          <span class="ac-title">VO₂ Max</span>
          <span class="ac-badge" style="color:{delta_color}">{delta_str} ({len(vo2_trend)}wk)</span>
        </div>
        <div class="ac-metrics">
          <div class="ac-metric">
            <span class="ac-mk">Current</span>
            <span class="ac-mv">{current}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">5wk ago</span>
            <span class="ac-mv">{first}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Trend</span>
            <span class="ac-mv" style="color:{delta_color}">{delta_str}</span>
          </div>
        </div>
        <canvas id="chart-vo2" height="50" style="margin-top:10px;"></canvas>
        <script>
        new Chart(document.getElementById('chart-vo2'),{{
          type:'line',
          data:{{labels:{labels},datasets:[{{
            data:{sparkline},borderColor:'{delta_color}',
            backgroundColor:'{delta_color}22',borderWidth:2,
            pointRadius:3,tension:0.3,fill:true
          }}]}},
          options:{{responsive:true,maintainAspectRatio:true,animation:false,
            plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}},
            scales:{{x:{{display:false}},y:{{display:false}}}}
          }}
        }});
        </script>
      </div>""")

    # ── Race Predictions card ─────────────────────────────────────────────────
    preds = analytics.get("race_predictions", {})
    if preds:
        pbs = {"5k": 19*60+52, "10k": 43*60+34,
               "half_marathon": 1*3600+32*60+55, "marathon": 3*3600+30*60+45}
        targets = {"half_marathon": 1*3600+30*60, "marathon": 3*3600+20*60}
        labels_map = {"5k":"5k","10k":"10k","half_marathon":"Half","marathon":"Marathon"}

        rows = ""
        for dist, secs in preds.items():
            pred_str = _secs_to_time(secs)
            pb_secs  = pbs.get(dist)
            tgt_secs = targets.get(dist)

            gap_pb_str = ""
            pb_color   = "#4a5270"
            if pb_secs:
                diff = secs - pb_secs
                sign = "+" if diff > 0 else ""
                gap_pb_str = f"{sign}{_secs_to_time(abs(diff))}"
                pb_color   = "#22c55e" if diff <= 0 else "#ef4444"

            gap_tgt_str = ""
            tgt_color   = "#4a5270"
            if tgt_secs:
                diff = secs - tgt_secs
                sign = "+" if diff > 0 else ""
                gap_tgt_str = f"{sign}{_secs_to_time(abs(diff))}"
                tgt_color   = "#22c55e" if diff <= 0 else "#f97316"

            rows += f"""
          <tr>
            <td class="pred-dist">{labels_map.get(dist, dist)}</td>
            <td class="pred-time">{pred_str}</td>
            <td style="color:{pb_color};font-family:'IBM Plex Mono',monospace;font-size:11px;">{gap_pb_str or "–"}</td>
            <td style="color:{tgt_color};font-family:'IBM Plex Mono',monospace;font-size:11px;">{gap_tgt_str or "–"}</td>
          </tr>"""

        cards.append(f"""
      <div class="analytics-card ac-wide">
        <div class="ac-header">
          <span class="ac-icon">🏁</span>
          <span class="ac-title">Race Predictions</span>
        </div>
        <table class="pred-table">
          <thead><tr>
            <th>Distance</th><th>Predicted</th>
            <th>vs PB</th><th>vs Target</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>""")

    # ── HRV + Readiness card ──────────────────────────────────────────────────
    hrv   = analytics.get("hrv", {})
    ready = analytics.get("readiness", {})
    if hrv or ready:
        hrv_status = hrv.get("status", "")
        hrv_color  = {"BALANCED":"#22c55e","UNBALANCED":"#f97316",
                      "LOW":"#ef4444","POOR":"#ef4444"}.get(hrv_status.upper(), "#4a5270")

        ready_score = ready.get("score")
        ready_color = "#22c55e" if ready_score and ready_score >= 70 \
                      else "#f97316" if ready_score and ready_score >= 50 \
                      else "#ef4444" if ready_score else "#4a5270"

        deviation = hrv.get("deviation_from_baseline")
        dev_str   = ""
        if deviation is not None:
            sign = "+" if deviation >= 0 else ""
            dev_str = f"{sign}{deviation} ms vs baseline"

        cards.append(f"""
      <div class="analytics-card">
        <div class="ac-header">
          <span class="ac-icon">🫀</span>
          <span class="ac-title">Recovery</span>
        </div>
        <div class="ac-metrics">
          <div class="ac-metric">
            <span class="ac-mk">HRV status</span>
            <span class="ac-mv" style="color:{hrv_color};font-size:11px;">{hrv_status.title() or "–"}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">7d avg HRV</span>
            <span class="ac-mv">{hrv.get('weekly_avg', '–')}{' ms' if hrv.get('weekly_avg') else ''}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">vs baseline</span>
            <span class="ac-mv" style="font-size:11px;color:{'#22c55e' if deviation and deviation >= 0 else '#ef4444'}">{dev_str or "–"}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Readiness</span>
            <span class="ac-mv" style="color:{ready_color}">{ready_score or "–"}/100</span>
          </div>
        </div>
      </div>""")

    # ── Running Form card ─────────────────────────────────────────────────────
    rd = analytics.get("running_dynamics", {})
    if rd:
        cadence  = rd.get("cadence_spm")
        vert_osc = rd.get("vert_osc_cm")
        gct      = rd.get("ground_contact_ms")

        cad_color  = "#22c55e" if cadence and cadence >= 170 else "#f97316" if cadence else "#4a5270"
        vosc_color = "#22c55e" if vert_osc and vert_osc <= 8.0 else "#f97316" if vert_osc else "#4a5270"

        cards.append(f"""
      <div class="analytics-card">
        <div class="ac-header">
          <span class="ac-icon">🦾</span>
          <span class="ac-title">Running Form</span>
        </div>
        <div class="ac-metrics">
          <div class="ac-metric">
            <span class="ac-mk">Cadence</span>
            <span class="ac-mv" style="color:{cad_color}">{cadence or '–'}{' spm' if cadence else ''}</span>
            <span class="ac-mk" style="font-size:9px;">target 170–180 · threshold efforts{'' if cadence else ' · none this week'}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Vert. oscillation</span>
            <span class="ac-mv" style="color:{vosc_color}">{vert_osc or '–'}{' cm' if vert_osc else ''}</span>
            <span class="ac-mk" style="font-size:9px;">target &lt;8.0cm · weekly avg</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Ground contact</span>
            <span class="ac-mv">{gct or '–'}{' ms' if gct else ''}</span>
            <span class="ac-mk" style="font-size:9px;">weekly avg</span>
          </div>
        </div>
      </div>""")

    # ── Sleep card ────────────────────────────────────────────────────────────
    sleep = analytics.get("sleep", {})
    if sleep:
        avg_h    = sleep.get("avg_hours")
        min_h    = sleep.get("min_hours")
        avg_s    = sleep.get("avg_score")
        slp_col  = "#22c55e" if avg_h and avg_h >= 7.5 \
                   else "#f97316" if avg_h and avg_h >= 6.5 \
                   else "#ef4444" if avg_h else "#4a5270"

        cards.append(f"""
      <div class="analytics-card">
        <div class="ac-header">
          <span class="ac-icon">😴</span>
          <span class="ac-title">Sleep (7-day)</span>
        </div>
        <div class="ac-metrics">
          <div class="ac-metric">
            <span class="ac-mk">Avg duration</span>
            <span class="ac-mv" style="color:{slp_col}">{avg_h or '–'}{'h' if avg_h else ''}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Worst night</span>
            <span class="ac-mv">{min_h or '–'}{'h' if min_h else ''}</span>
          </div>
          <div class="ac-metric">
            <span class="ac-mk">Avg score</span>
            <span class="ac-mv">{avg_s or '–'}{'/100' if avg_s else ''}</span>
          </div>
        </div>
      </div>""")

    if not cards:
        return ""

    # Claude's physiological analysis narrative
    phys_narrative = ""
    phys_text = insights.get("physiological_analysis", "")
    if phys_text:
        paras = "".join(f"<p>{p.strip()}</p>" for p in phys_text.split("\n\n") if p.strip())
        phys_narrative = f'<div class="narrative" style="margin-top:24px;">{paras}</div>'

    form_narrative = ""
    form_text = insights.get("form_analysis", "")
    if form_text:
        paras = "".join(f"<p>{p.strip()}</p>" for p in form_text.split("\n\n") if p.strip())
        form_narrative = f'<div class="narrative" style="margin-top:12px;">{paras}</div>'

    return f"""
  <div class="section">
    <div class="sh">Garmin Analytics <span class="ai-badge">◆ Claude</span></div>
    <div class="analytics-grid">{"".join(cards)}</div>
    {phys_narrative}
    {form_narrative}
  </div>"""


# ── Speed sessions section ────────────────────────────────────────────────────

def _speed_sessions_html(sessions: list[dict]) -> str:
    if not sessions:
        return ""
    cards = []
    for s in sessions:
        chart_id = f"chart-speed-{s['activity_id']}"
        profile  = s.get("profile", [])
        chart_t  = json.dumps([p["t"]    for p in profile])
        chart_p  = json.dumps([p["pace"] for p in profile])
        chart_hr = json.dumps([p["hr"]   for p in profile])
        bands    = json.dumps([{"start": iv["start_s"], "end": iv["end_s"]}
                               for iv in s["intervals"]])
        rows = ""
        ivs, recs = s["intervals"], s.get("recoveries", [])
        for i, iv in enumerate(ivs):
            dur    = f"{iv['duration_s']//60}:{iv['duration_s']%60:02d}"
            hr_td  = f"<td>{iv['mean_hr']} bpm</td>" if iv.get("mean_hr") else "<td>–</td>"
            cad_td = f"<td>{iv['mean_cad']} spm</td>" if iv.get("mean_cad") else "<td>–</td>"
            rows  += f'<tr class="effort-row"><td class="iv-label">Effort {i+1}</td><td class="iv-pace">{iv["mean_pace"]}/km</td><td class="iv-peak">{iv["peak_pace"]}/km</td><td>{dur}</td>{hr_td}{cad_td}</tr>'
            if i < len(recs):
                rec = recs[i]
                rec_dur  = f"{rec['duration_s']//60}:{rec['duration_s']%60:02d}"
                rec_ms   = rec.get("mean_ms")
                rec_pace = _pace(rec_ms) + "/km" if rec_ms else "–"
                rec_hr   = f"{rec['mean_hr']} bpm" if rec.get("mean_hr") else "–"
                rows += f'<tr class="rec-row"><td class="rec-label">Recovery</td><td class="rec-pace">{rec_pace}</td><td>–</td><td>{rec_dur}</td><td>{rec_hr}</td><td>–</td></tr>'

        cards.append(f"""
      <div class="speed-card">
        <div class="speed-card-header">
          <div><div class="speed-date">{s['date']}</div><div class="speed-name">{s['name']}</div></div>
          <div class="speed-meta">
            <span class="speed-badge">{s['n_intervals']} interval{"s" if s['n_intervals']!=1 else ""}</span>
            <span class="speed-best">Best <strong>{s['best_pace']}/km</strong></span>
            {"<span class='speed-hr'>Peak HR "+str(s['session_peak_hr'])+" bpm</span>" if s.get('session_peak_hr') else ""}
          </div>
        </div>
        <div class="speed-chart-wrap"><canvas id="{chart_id}" height="160"></canvas></div>
        <div class="speed-table-wrap">
          <table class="speed-table"><thead><tr><th>Segment</th><th>Avg pace</th><th>Peak</th><th>Duration</th><th>HR</th><th>Cadence</th></tr></thead>
          <tbody>{rows}</tbody></table>
        </div>
        <script>
        (function(){{
          const ctx=document.getElementById('{chart_id}');
          const times={chart_t},paces={chart_p},hrs={chart_hr},bands={bands};
          const ep={{id:'eb',beforeDraw(chart){{const{{ctx:c,chartArea,scales}}=chart;if(!chartArea)return;c.save();bands.forEach(b=>{{const x1=scales.x.getPixelForValue(b.start),x2=scales.x.getPixelForValue(b.end);c.fillStyle='rgba(249,115,22,0.10)';c.fillRect(x1,chartArea.top,x2-x1,chartArea.height);}});c.restore();}}}};
          new Chart(ctx,{{type:'line',plugins:[ep],data:{{labels:times,datasets:[
            {{label:'Pace',data:paces,borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.06)',borderWidth:1.5,pointRadius:0,tension:0.3,fill:true,yAxisID:'y'}},
            {{label:'HR',data:hrs,borderColor:'rgba(239,68,68,0.6)',borderWidth:1.2,pointRadius:0,tension:0.3,yAxisID:'y2'}}
          ]}},options:{{responsive:true,maintainAspectRatio:true,animation:false,
            plugins:{{legend:{{labels:{{color:'#4a5270',boxWidth:10,font:{{size:10}}}}}}}},
            scales:{{x:{{grid:{{display:false}},ticks:{{display:false}}}},
              y:{{reverse:true,min:3.0,max:8.0,grid:{{color:'rgba(26,32,53,.9)'}},ticks:{{color:'#4a5270',font:{{size:10}},callback:v=>Math.floor(v)+':'+(Math.round((v-Math.floor(v))*60)+'').padStart(2,'0')}}}},
              y2:{{position:'right',min:100,max:200,grid:{{display:false}},ticks:{{color:'rgba(239,68,68,0.5)',font:{{size:10}}}}}}
            }}
          }}}});
        }})();
        </script>
      </div>""")

    speed_analysis_html = ""
    sa = insights_ref.get("speed_analysis", "") if insights_ref else ""
    if sa:
        paras = "".join(f"<p>{p.strip()}</p>" for p in sa.split("\n\n") if p.strip())
        speed_analysis_html = f'<div class="narrative" style="margin-top:20px;">{paras}</div>'

    return f"""
  <div class="section">
    <div class="sh">Speed Sessions <span class="ai-badge">◆ Garmin streams</span></div>
    <div class="speed-grid">{"".join(cards)}</div>
    {speed_analysis_html}
  </div>"""


# ── Plan vs actual section ────────────────────────────────────────────────────

def _plan_actual_html(garmin: dict, insights: dict) -> str:
    if not garmin.get("available"): return ""
    last_week = garmin.get("last_week", [])
    if not last_week: return ""

    rows = ""
    for w in last_week:
        status = w["status"]
        color  = "#22c55e" if status == "completed" else "#ef4444"
        icon   = "✓" if status == "completed" else "✗"
        steps_html = ""
        if w["steps_text"] != "(no structured steps)":
            steps_html = f'<div class="plan-steps">{w["steps_text"].replace(chr(10),"<br>")}</div>'
        actual_html = ""
        if status == "completed" and w.get("actual"):
            act = w["actual"]
            dist = round(act.get("distance", 0) / 1000, 1)
            actual_html = f'<div class="plan-actual"><span>{dist} km</span><span>{_pace(act.get("average_speed"))}/km</span>{"<span>♥ "+str(act.get("average_heartrate"))+" bpm</span>" if act.get("average_heartrate") else ""}</div>'
            if act.get("description"):
                actual_html += f'<div class="plan-note">&ldquo;{act["description"][:200]}&rdquo;</div>'
        elif status == "skipped":
            actual_html = '<div style="font-family:IBM Plex Mono,monospace;font-size:11px;color:#ef4444;margin-top:6px;">No matching run found</div>'

        rows += f"""
      <div class="plan-row plan-{status}">
        <div class="plan-row-header">
          <span class="plan-icon" style="color:{color}">{icon}</span>
          <span class="plan-date">{w['date']}</span>
          <span class="plan-name">{w['workout_name']}</span>
        </div>
        {steps_html}{actual_html}
      </div>"""

    pva_text = insights.get("plan_vs_actual", "")
    pva_html = ""
    if pva_text:
        paras = "".join(f"<p>{p.strip()}</p>" for p in pva_text.split("\n\n") if p.strip())
        pva_html = f'<div class="narrative" style="margin-top:20px;">{paras}</div>'

    return f"""
  <div class="section">
    <div class="sh">Plan vs Actual <span class="ai-badge">◆ Garmin + Claude</span></div>
    <div class="plan-grid">{rows}</div>
    {pva_html}
  </div>"""


# ── Next week section ─────────────────────────────────────────────────────────

def _next_week_html(garmin: dict, insights: dict) -> str:
    next_week = garmin.get("next_week", []) if garmin.get("available") else []
    preview   = insights.get("next_week_preview", "")
    if not next_week and not preview:
        return ""

    sessions_html = ""
    for w in next_week:
        steps_html = ""
        if w["steps_text"] != "(no structured steps)":
            steps_html = f'<div class="upcoming-steps">{w["steps_text"].replace(chr(10),"<br>")}</div>'
        sessions_html += f"""
      <div class="upcoming-card">
        <div class="upcoming-date">{w['date']}</div>
        <div class="upcoming-name">{w['workout_name']}</div>
        {steps_html}
      </div>"""

    preview_html = ""
    if preview:
        preview_html = f'<div class="next-focus"><div class="nf-tag">Claude\'s read on next week</div><div class="nf-body">{preview}</div></div>'

    return f"""
  <div class="section">
    <div class="sh">Next Week <span class="ai-badge">◆ Garmin schedule</span></div>
    {"<div class='upcoming-grid'>"+sessions_html+"</div>" if sessions_html else ""}
    {preview_html}
  </div>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def _heatmap_html(daily: dict, history_weeks: int = 16) -> str:
    """GitHub-style training calendar. Weeks as columns, Mon–Sun as rows."""
    from datetime import date, timedelta

    if not daily:
        return ""

    today      = date.today()
    # Start on the Monday of the earliest week
    start      = today - timedelta(weeks=history_weeks)
    start      = start - timedelta(days=start.weekday())  # back to Monday

    # Build list of (date, cell_html) for every day start → today
    DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"]

    def _color(d_str):
        if d_str not in daily:
            return "#1a2035", ""
        e = daily[d_str]
        km = e.get("dist_km", 0)
        t  = e.get("type", "")
        if t != "Run" or km == 0:
            return "#242c45", ""   # cross-training / rest with activity
        if km < 5:   return "#164e63", f"{km}km"
        if km < 10:  return "#0e7490", f"{km}km"
        if km < 15:  return "#14b8a6", f"{km}km"
        if km < 25:  return "#f59e0b", f"{km}km"
        return "#f97316", f"{km}km"

    # Group into weeks (columns of 7 days)
    weeks = []
    cursor = start
    while cursor <= today:
        week = []
        for _ in range(7):
            week.append(cursor)
            cursor += timedelta(days=1)
        weeks.append(week)

    # Build columns HTML
    cols_html = ""
    for week in weeks:
        cells = ""
        for day in week:
            d_str = day.strftime("%Y-%m-%d")
            color, label = _color(d_str)
            future = "opacity:.25;" if day > today else ""
            tip    = f"{d_str} — {label}" if label else d_str
            cells += (f'<div title="{tip}" style="width:11px;height:11px;border-radius:2px;'
                      f'background:{color};{future}"></div>')
        cols_html += f'<div style="display:flex;flex-direction:column;gap:2px;">{cells}</div>'

    # Day-of-week labels on left
    dow_labels = "".join(
        f'<div style="height:11px;font-family:IBM Plex Mono,monospace;font-size:8px;'
        f'color:#2a3050;line-height:11px;">{l}</div>'
        for l in DAY_LABELS
    )

    # Month labels along top (approximate — one label per ~4 weeks)
    month_labels_html = ""
    seen_months = set()
    for i, week in enumerate(weeks):
        m = week[0].strftime("%b")
        if m not in seen_months:
            seen_months.add(m)
            month_labels_html += (f'<div style="grid-column:{i+1};font-family:IBM Plex Mono,monospace;'
                                   f'font-size:8px;color:#2a3050;white-space:nowrap;">{m}</div>')

    # Legend
    legend = ""
    for color, label in [("#1a2035","rest"), ("#14b8a6","5–15km"), ("#f59e0b","15–25km"), ("#f97316","25km+")]:
        legend += (f'<div style="display:flex;align-items:center;gap:4px;'
                   f'font-family:IBM Plex Mono,monospace;font-size:9px;color:#4a5270;">'
                   f'<div style="width:9px;height:9px;border-radius:2px;background:{color};"></div>'
                   f'{label}</div>')

    return f"""
  <div class="section">
    <div class="sh">Training Calendar</div>
    <div style="overflow-x:auto;padding-bottom:8px;">
      <div style="display:flex;gap:3px;align-items:flex-start;min-width:max-content;">
        <div style="display:flex;flex-direction:column;gap:2px;padding-top:14px;margin-right:2px;">
          {dow_labels}
        </div>
        <div>
          <div style="display:flex;gap:3px;margin-bottom:3px;">{month_labels_html}</div>
          <div style="display:flex;gap:3px;">{cols_html}</div>
        </div>
      </div>
    </div>
    <div style="display:flex;gap:14px;margin-top:10px;flex-wrap:wrap;">{legend}</div>
  </div>"""


def _parkrun_leaderboard_html(lb: dict) -> str:
    if not lb or not lb.get("recent"):
        return ""

    recent = lb["recent"]
    trend  = lb.get("age_grade_trend", "stable")
    trend_icon  = {"improving": "↑", "declining": "↓", "stable": "→"}.get(trend, "→")
    trend_color = {"improving": "#22c55e", "declining": "#ef4444", "stable": "#4a5270"}.get(trend, "#4a5270")

    best_ag  = lb.get("best_age_grade")
    best_pos = lb.get("best_position")
    avg_ag   = lb.get("avg_age_grade_10")
    total    = lb.get("total_runs", 0)

    # Summary bar
    summary_parts = []
    if total:        summary_parts.append(f"{total} runs total")
    if best_pos:     summary_parts.append(f"best position #{best_pos}")
    if best_ag:      summary_parts.append(f"best age grade {best_ag}%")
    if avg_ag:       summary_parts.append(f"10-run avg {avg_ag}%")
    summary_html = (
        f'<div style="font-size:11px;color:#4a5270;margin-bottom:14px;">'
        + " · ".join(summary_parts)
        + f' <span style="color:{trend_color};font-weight:500;">{trend_icon} age grade {trend}</span>'
        + '</div>'
    ) if summary_parts else ""

    # Table rows
    rows = ""
    prev_ag = None
    for r in recent:
        ag      = r.get("age_grade")
        pos     = r.get("position")
        t_str   = r.get("time_str", "–")
        event   = r.get("event", "–")
        # shorten event names
        event_short = event.replace("parkrun", "").replace("Parkrun", "").strip() or event

        ag_color = (
            "#8b5cf6" if ag and ag >= 70 else
            "#22c55e" if ag and ag >= 65 else
            "#f97316" if ag and ag >= 60 else
            "#ef4444" if ag else
            "#4a5270"
        )
        ag_arrow = ""
        if ag and prev_ag:
            diff = ag - prev_ag
            if diff > 0.5:    ag_arrow = f'<span style="color:#22c55e;font-size:9px;"> ↑</span>'
            elif diff < -0.5: ag_arrow = f'<span style="color:#ef4444;font-size:9px;"> ↓</span>'
        prev_ag = ag

        pb_badge = '<span style="color:#f59e0b;font-size:9px;font-weight:600;"> PB</span>' if r.get("is_pb") else ""

        rows += f"""
        <tr>
          <td style="color:#4a5270;">{r['date'][5:]}</td>
          <td style="color:#dde2f0;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{event_short}</td>
          <td style="font-family:'IBM Plex Mono',monospace;color:#f97316;">{t_str}{pb_badge}</td>
          <td style="font-family:'IBM Plex Mono',monospace;color:#4a5270;">{f"#{pos}" if pos else "–"}</td>
          <td style="font-family:'IBM Plex Mono',monospace;color:{ag_color};">{f"{ag}%" if ag else "–"}{ag_arrow}</td>
        </tr>"""

    return f"""
    <div style="margin-bottom:20px;">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:#4a5270;
                  text-transform:uppercase;letter-spacing:.12em;margin-bottom:10px;">Recent Results</div>
      {summary_html}
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead>
            <tr style="border-bottom:1px solid #1a2035;">
              <th style="text-align:left;padding:6px 10px 6px 0;font-family:'IBM Plex Mono',monospace;
                         font-size:9px;font-weight:400;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">Date</th>
              <th style="text-align:left;padding:6px 10px 6px 0;font-family:'IBM Plex Mono',monospace;
                         font-size:9px;font-weight:400;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">Event</th>
              <th style="text-align:left;padding:6px 10px 6px 0;font-family:'IBM Plex Mono',monospace;
                         font-size:9px;font-weight:400;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">Time</th>
              <th style="text-align:left;padding:6px 10px 6px 0;font-family:'IBM Plex Mono',monospace;
                         font-size:9px;font-weight:400;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">Pos</th>
              <th style="text-align:left;padding:6px 0;font-family:'IBM Plex Mono',monospace;
                         font-size:9px;font-weight:400;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">Age Grade</th>
            </tr>
          </thead>
          <tbody>{rows}
          </tbody>
        </table>
      </div>
    </div>"""


def _race_countdown_html(race_data: dict) -> str:
    if not race_data or not race_data.get("races"):
        return ""

    phase        = race_data.get("phase", "Base")
    phase_detail = race_data.get("phase_detail", "")

    PHASE_COLORS = {
        "Race Week": "#ef4444",
        "Taper":     "#f97316",
        "Peak":      "#f59e0b",
        "Build":     "#14b8a6",
        "Base":      "#8b5cf6",
        "Off-Season":"#4a5270",
    }
    phase_color = PHASE_COLORS.get(phase, "#4a5270")

    cards_html = ""
    for r in race_data["races"]:
        primary_style = 'border-top:2px solid #f97316;' if r.get("primary") else ''
        goal_html = f'<div style="font-size:11px;color:#f97316;margin-top:4px;">{r["goal"]}</div>' if r.get("goal") else ""
        race_phase_color = PHASE_COLORS.get(r["phase"], "#4a5270")

        # Progress bar: weeks elapsed out of ~20 week cycle (capped)
        total_weeks = 20
        elapsed = max(0, total_weeks - r["weeks_until"])
        pct = min(100, round(elapsed / total_weeks * 100))

        cards_html += f"""
    <div class="race-card" style="{primary_style}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
        <div>
          <div style="font-family:'Source Serif 4',serif;font-size:15px;font-weight:600;color:#dde2f0;">{r["name"]}</div>
          <div style="font-size:10px;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;margin-top:2px;">{r["type_label"]} · {r["date_str"]}</div>
          {goal_html}
        </div>
        <div style="text-align:right;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:28px;font-weight:500;color:#f97316;line-height:1;">{r["days_until"]}</div>
          <div style="font-size:10px;color:#4a5270;text-transform:uppercase;letter-spacing:.08em;">days</div>
        </div>
      </div>
      <div style="margin-bottom:8px;">
        <div style="height:3px;background:#1a2035;border-radius:2px;">
          <div style="height:3px;width:{pct}%;background:{race_phase_color};border-radius:2px;transition:width .3s;"></div>
        </div>
      </div>
      <div style="font-size:11px;color:{race_phase_color};font-weight:500;">{r["phase"]}</div>
      <div style="font-size:11px;color:#4a5270;margin-top:2px;">{r["phase_detail"]}</div>
    </div>"""

    return f"""
  <div class="section">
    <div class="sh">Race Schedule
      <span style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:{phase_color};
                   background:rgba(139,92,246,.12);padding:3px 8px;border-radius:10px;
                   text-transform:uppercase;letter-spacing:.08em;font-weight:500;">{phase}</span>
    </div>
    <div style="font-size:12px;color:#4a5270;margin-bottom:16px;">{phase_detail}</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;">
      {cards_html}
    </div>
  </div>"""


# Module-level reference so speed session HTML can access insights
insights_ref = None


def generate_html(data: dict, insights: dict, history_weeks: int = 16, race_data: dict | None = None) -> str:
    global insights_ref
    insights_ref = insights

    tw      = data.get("this_week") or {}
    dist    = tw.get("dist_km", 0)
    runs    = tw.get("runs", 0)
    pace    = tw.get("avg_pace", "–")
    hr      = tw.get("avg_hr", "–")
    elev    = tw.get("elev_m", 0)
    rolling = data["rolling_avg_km"]
    vs_avg  = round(dist - rolling, 1)
    vs_str  = (f"+{vs_avg}" if vs_avg >= 0 else str(vs_avg)) + " km"
    vs_col  = "#22c55e" if vs_avg >= 0 else "#ef4444"

    ws       = data.get("weekly_series", [])
    w_labels = json.dumps([w["week"][5:] for w in ws])
    w_dists  = json.dumps([w["dist_km"]  for w in ws])
    w_hrs    = json.dumps([w["avg_hr"]   for w in ws])
    r_line   = json.dumps([rolling]      * len(ws))
    last_idx = len(ws) - 1

    all_prs   = data.get("all_parkruns", [])
    pr_labels = json.dumps([p["date"][5:] for p in all_prs])
    pr_times  = json.dumps([round(p["time_min"], 2) for p in all_prs])
    pr_hrs    = json.dumps([p["hr"] for p in all_prs])

    aeff     = data.get("aero_eff_now")
    aeff_p   = data.get("aero_eff_prev")
    aeff_str = f"{int(aeff)}:{int((aeff%1)*60):02d}/km" if aeff else "–"
    if aeff and aeff_p:
        diff_s           = round((aeff_p - aeff) * 60)
        aeff_delta       = f"{'↑' if diff_s>0 else '↓'} {abs(diff_s)}s/km vs prior 8 wks"
        aeff_delta_color = "#22c55e" if diff_s > 0 else "#ef4444"
    else:
        aeff_delta, aeff_delta_color = "", "#4a5270"

    streak       = data.get("current_streak", 0)
    streak_color = "#22c55e" if streak > 7 else "#f97316" if streak > 0 else "#4a5270"

    bp = data.get("best_parkrun")
    bp_html = ""
    if bp:
        bm, bs = int(bp["time_min"]), int((bp["time_min"] % 1) * 60)
        bp_html = (f'<div style="background:var(--card);border:1px solid rgba(245,158,11,.3);'
                   f'border-left:3px solid var(--a);border-radius:8px;padding:13px 16px;margin-bottom:14px;">'
                   f'<div style="font-family:IBM Plex Mono,monospace;font-size:9px;color:var(--a);'
                   f'text-transform:uppercase;letter-spacing:.12em;">All-Time PB</div>'
                   f'<div style="font-family:IBM Plex Mono,monospace;font-size:22px;color:var(--a);'
                   f'margin-top:4px;">{bm}:{bs:02d} ({bp["date"]})</div></div>')

    zones  = data.get("zone_dist", {})
    tz_tot = sum(zones.values()) or 1
    z_pcts = {z: round(v/tz_tot*100) for z, v in zones.items()}
    z_col  = {"Z1":"#60a5fa","Z2":"#14b8a6","Z3":"#f59e0b","Z4":"#f97316","Z5":"#ef4444"}
    z_name = {"Z1":"Z1 Recovery","Z2":"Z2 Easy","Z3":"Z3 Aerobic","Z4":"Z4 Threshold","Z5":"Z5 Max"}
    zones_html = "".join(
        f'<div class="zr"><div class="zl">{z_name[z]}</div>'
        f'<div class="zb"><div class="zbi" style="width:{z_pcts[z]}%;background:{z_col[z]};"></div></div>'
        f'<div class="zp">{z_pcts[z]}%</div></div>'
        for z in ["Z1","Z2","Z3","Z4","Z5"]
    )

    notable_html = ""
    for n in data.get("notable", [])[:8]:
        hr_span   = f'<span>♥ {n["hr"]} bpm</span>' if n.get("hr") else ""
        desc_html = f'<div class="act-desc">&ldquo;{n["desc"]}&rdquo;</div>' if n.get("desc") else ""
        notable_html += (f'<div class="act-card"><div class="act-meta">{n["date"]}</div>'
                         f'<div class="act-name">{n["name"]}</div>'
                         f'<div class="act-stats"><span>{n["dist_km"]} km</span>'
                         f'<span>{n["time"]}</span><span>{n["pace"]}/km</span>{hr_span}</div>'
                         f'{desc_html}</div>')
    if not notable_html:
        notable_html = '<p class="empty">No notable activities this week.</p>'

    sig_color = {"positive":"#22c55e","warning":"#ef4444","neutral":"#14b8a6"}
    sig_icon  = {"positive":"↑","warning":"⚠","neutral":"→"}
    signals_html = ""
    for sig in insights.get("key_signals", []):
        t = sig.get("type","neutral")
        signals_html += (f'<div class="signal signal-{t}">'
                         f'<div class="signal-header">'
                         f'<span class="signal-icon" style="color:{sig_color.get(t,"#14b8a6")}">{sig_icon.get(t,"→")}</span>'
                         f'<span class="signal-label">{sig.get("signal","")}</span></div>'
                         f'<div class="signal-detail">{sig.get("detail","")}</div></div>')

    narrative_html = "".join(
        f"<p>{p.strip()}</p>"
        for p in insights.get("week_narrative","").split("\n\n") if p.strip()
    )

    headline   = insights.get("headline", f"{data['week_label']} — {dist} km")
    next_focus = insights.get("next_week_focus", "")
    garmin     = data.get("garmin", {})

    analytics_section   = _analytics_section(garmin, insights)
    speed_section       = _speed_sessions_html(data.get("speed_sessions", []))
    plan_actual_section = _plan_actual_html(garmin, insights)
    next_week_section   = _next_week_html(garmin, insights)
    race_section        = _race_countdown_html(race_data or {})
    pr_leaderboard_html = _parkrun_leaderboard_html(data.get("parkrun_leaderboard", {}))
    heatmap_section     = _heatmap_html(data.get("daily_activities", {}), history_weeks)

    aeff_delta_html = (
        f'<div><div style="font-family:IBM Plex Mono,monospace;font-size:9px;color:var(--mu);'
        f'text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;">vs prior 8 weeks</div>'
        f'<div style="font-family:IBM Plex Mono,monospace;font-size:22px;font-weight:500;'
        f'color:{aeff_delta_color};">{aeff_delta}</div></div>'
        if aeff_delta else ""
    )
    hs_v_class = "g" if vs_avg >= 0 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Run Report — {data["week_label"]}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;0,900;1,700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,300;8..60,400;8..60,600&display=swap" rel="stylesheet">
<style>
  :root{{--o:#f97316;--a:#f59e0b;--t:#14b8a6;--r:#ef4444;--g:#22c55e;--p:#8b5cf6;
         --bg:#080b12;--card:#141926;--bdr:#1a2035;--tx:#dde2f0;--mu:#4a5270;--dim:#1e2540;}}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{background:var(--bg);color:var(--tx);font-family:'Source Serif 4',serif;font-weight:300;line-height:1.75;font-size:16px;}}
  .wrap{{max-width:940px;margin:0 auto;padding:0 32px;}}
  @media(max-width:600px){{.wrap{{padding:0 16px;}}}}
  .header{{background:linear-gradient(180deg,#0f1520 0%,#080b12 100%);border-bottom:3px solid var(--o);padding:44px 0 36px;position:relative;overflow:hidden;}}
  .header::before{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 80% 60% at 65% 30%,rgba(249,115,22,.07) 0%,transparent 70%);pointer-events:none;}}
  .header-inner{{position:relative;z-index:1;}}
  .tag{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.18em;color:var(--o);text-transform:uppercase;margin-bottom:8px;}}
  .report-title{{font-family:'Playfair Display',serif;font-size:clamp(28px,5vw,50px);font-weight:900;color:#fff;line-height:1;margin-bottom:6px;}}
  .report-title em{{color:var(--o);font-style:italic;}}
  .report-sub{{font-size:12px;color:var(--mu);font-family:'IBM Plex Mono',monospace;margin-top:6px;}}
  .ai-headline{{background:linear-gradient(135deg,#0a1a0a,#0f1520);border:1px solid rgba(34,197,94,.2);border-left:4px solid var(--g);border-radius:10px;padding:20px 24px;margin:24px 0 0;}}
  .ai-hl-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.16em;color:var(--g);text-transform:uppercase;margin-bottom:8px;display:flex;align-items:center;gap:6px;}}
  .ai-hl-tag::before{{content:'◆';font-size:8px;}}
  .ai-headline-text{{font-family:'Playfair Display',serif;font-size:clamp(18px,3vw,24px);font-style:italic;color:#ccd4e8;line-height:1.4;}}
  .hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px;margin:24px 0;}}
  .hs{{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:14px 16px;}}
  .hs-l{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.12em;color:var(--mu);text-transform:uppercase;display:block;margin-bottom:4px;}}
  .hs-v{{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:500;color:var(--o);display:block;line-height:1;}}
  .hs-v.g{{color:var(--g);}}.hs-v.t{{color:var(--t);}}.hs-v.a{{color:var(--a);}}.hs-v.r{{color:var(--r);}}.hs-v.m{{color:var(--mu);}}
  .hs-delta{{font-family:'IBM Plex Mono',monospace;font-size:11px;margin-top:3px;display:block;}}
  .section{{padding:40px 0 28px;border-top:1px solid var(--bdr);}}
  .sh{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.16em;color:var(--o);text-transform:uppercase;margin-bottom:16px;display:flex;align-items:center;gap:10px;}}
  .sh::after{{content:'';flex:1;height:1px;background:var(--bdr);}}
  .ai-badge{{display:inline-flex;align-items:center;gap:4px;background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);border-radius:4px;padding:2px 7px;font-size:9px;color:var(--g);letter-spacing:.06em;}}
  .narrative{{max-width:720px;}}
  .narrative p{{color:#8090b0;font-size:15px;line-height:1.9;margin-bottom:1.3em;}}
  .narrative p strong{{color:var(--tx);font-weight:600;}}
  .signals{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin:20px 0;}}
  .signal{{background:var(--card);border:1px solid var(--bdr);border-radius:9px;padding:15px 17px;}}
  .signal-positive{{border-left:3px solid var(--g);}}.signal-warning{{border-left:3px solid var(--r);}}.signal-neutral{{border-left:3px solid var(--t);}}
  .signal-header{{display:flex;align-items:center;gap:8px;margin-bottom:6px;}}
  .signal-icon{{font-size:14px;font-weight:700;}}.signal-label{{font-size:13px;font-weight:600;color:var(--tx);}}
  .signal-detail{{font-size:13px;color:var(--mu);line-height:1.65;}}
  .next-focus{{background:linear-gradient(135deg,#0f0f1a,#0f1520);border:1px solid rgba(139,92,246,.25);border-left:4px solid var(--p);border-radius:10px;padding:18px 22px;margin-top:20px;}}
  .nf-tag{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.14em;color:var(--p);text-transform:uppercase;margin-bottom:8px;}}
  .nf-body{{font-size:14px;color:#8090b0;line-height:1.75;}}
  .fig{{background:var(--card);border:1px solid var(--bdr);border-radius:12px;overflow:hidden;margin:18px 0;}}
  .fig-h{{padding:16px 20px 0;display:flex;justify-content:space-between;}}
  .fig-title{{font-size:14px;font-weight:600;color:var(--tx);}}.fig-n{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--o);letter-spacing:.1em;text-transform:uppercase;}}
  .fig-body{{padding:14px 20px;}}.fig-cap{{padding:10px 20px 14px;font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);border-top:1px solid var(--bdr);line-height:1.6;}}
  canvas{{display:block;width:100%!important;}}
  .act-grid{{display:flex;flex-direction:column;gap:8px;}}
  .act-card{{background:var(--bg);border:1px solid var(--bdr);border-radius:8px;padding:12px 16px;}}
  .act-meta{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);}}
  .act-name{{font-size:14px;font-weight:600;color:var(--tx);margin:3px 0 6px;}}
  .act-stats{{display:flex;gap:14px;flex-wrap:wrap;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--t);}}
  .act-desc{{font-size:12px;color:var(--mu);font-style:italic;margin-top:8px;border-left:2px solid var(--dim);padding-left:10px;line-height:1.6;}}
  .empty{{color:var(--mu);font-size:14px;padding:8px 0;}}
  .zones{{display:flex;flex-direction:column;gap:9px;}}
  .zr{{display:flex;align-items:center;gap:10px;}}
  .zl{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);width:90px;flex-shrink:0;}}
  .zb{{flex:1;height:6px;background:var(--dim);border-radius:3px;overflow:hidden;}}
  .zbi{{height:100%;border-radius:3px;opacity:.85;}}
  .zp{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);width:30px;text-align:right;}}
  /* ANALYTICS */
  .analytics-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;}}
  .analytics-card{{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:16px;}}
  .ac-wide{{grid-column:1/-1;}}
  .ac-header{{display:flex;align-items:center;gap:8px;margin-bottom:12px;}}
  .ac-icon{{font-size:16px;}}.ac-title{{font-size:13px;font-weight:600;color:var(--tx);flex:1;}}
  .ac-badge{{font-family:'IBM Plex Mono',monospace;font-size:10px;}}
  .ac-metrics{{display:flex;flex-wrap:wrap;gap:12px;}}
  .ac-metric{{display:flex;flex-direction:column;gap:2px;min-width:70px;}}
  .ac-mk{{font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--mu);text-transform:uppercase;letter-spacing:.08em;}}
  .ac-mv{{font-family:'IBM Plex Mono',monospace;font-size:18px;font-weight:500;color:var(--tx);}}
  .pred-table{{width:100%;border-collapse:collapse;font-size:12px;}}
  .pred-table th{{font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.1em;color:var(--mu);text-transform:uppercase;padding:5px 8px;border-bottom:1px solid var(--bdr);text-align:left;}}
  .pred-table td{{padding:7px 8px;border-bottom:1px solid var(--dim);}}
  .pred-dist{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--mu);}}
  .pred-time{{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:500;color:var(--tx);}}
  /* SPEED */
  .speed-grid{{display:flex;flex-direction:column;gap:20px;}}
  .speed-card{{background:var(--card);border:1px solid var(--bdr);border-radius:12px;overflow:hidden;border-top:3px solid var(--o);}}
  .speed-card-header{{padding:16px 20px;display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;border-bottom:1px solid var(--bdr);}}
  .speed-date{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);}}
  .speed-name{{font-size:15px;font-weight:600;color:var(--tx);margin-top:3px;}}
  .speed-meta{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;}}
  .speed-badge{{font-family:'IBM Plex Mono',monospace;font-size:10px;background:rgba(249,115,22,.12);border:1px solid rgba(249,115,22,.3);color:var(--o);padding:3px 8px;border-radius:4px;}}
  .speed-best{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--mu);}}.speed-best strong{{color:var(--g);}}
  .speed-hr{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--r);}}
  .speed-chart-wrap{{padding:14px 20px 0;}}
  .speed-table-wrap{{padding:14px 20px 20px;overflow-x:auto;}}
  .speed-table{{width:100%;border-collapse:collapse;font-family:'IBM Plex Mono',monospace;font-size:12px;}}
  .speed-table th{{color:var(--mu);text-transform:uppercase;font-size:9px;letter-spacing:.1em;padding:5px 8px;border-bottom:1px solid var(--bdr);text-align:left;}}
  .speed-table td{{padding:7px 8px;border-bottom:1px solid var(--dim);}}
  .effort-row td{{color:var(--tx);}}.iv-label,.iv-pace{{color:var(--o);font-weight:500;}}.iv-peak{{color:var(--g);}}
  .rec-row td{{color:var(--mu);font-size:11px;}}.rec-label,.rec-pace{{color:var(--t);}}
  /* PLAN */
  .plan-grid{{display:flex;flex-direction:column;gap:10px;}}
  .plan-row{{background:var(--card);border:1px solid var(--bdr);border-radius:10px;padding:14px 18px;}}
  .plan-completed{{border-left:3px solid var(--g);}}.plan-skipped{{border-left:3px solid var(--r);}}
  .plan-row-header{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;}}
  .plan-icon{{font-size:14px;font-weight:700;}}.plan-date{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);}}
  .plan-name{{font-size:14px;font-weight:600;color:var(--tx);flex:1;}}
  .plan-steps{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);background:var(--bg);border-radius:6px;padding:10px 12px;margin:8px 0;line-height:1.7;}}
  .plan-actual{{display:flex;gap:12px;flex-wrap:wrap;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--t);margin-top:8px;}}
  .plan-note{{font-size:12px;color:var(--mu);font-style:italic;margin-top:6px;border-left:2px solid var(--dim);padding-left:10px;}}
  /* UPCOMING */
  .upcoming-grid{{display:flex;flex-direction:column;gap:8px;margin-bottom:16px;}}
  .upcoming-card{{background:var(--card);border:1px solid var(--bdr);border-left:3px solid var(--p);border-radius:8px;padding:12px 16px;}}
  .upcoming-date{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);}}
  .upcoming-name{{font-size:14px;font-weight:600;color:var(--tx);margin:3px 0 6px;}}
  .upcoming-steps{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mu);line-height:1.7;}}
  /* FOOTER */
  .race-card{{background:var(--card);border:1px solid var(--bdr);border-radius:12px;padding:18px 20px;}}
  .footer{{border-top:1px solid var(--bdr);padding:32px 0 48px;margin-top:48px;}}
  .footer p{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--mu);line-height:1.9;}}
</style>
</head>
<body>
<div class="header">
  <div class="wrap header-inner">
    <div class="tag">ResearchAssistants_SBW · Running Bot · Garmin + Claude</div>
    <div class="report-title">Week of <em>{data["week_label"].replace("w/c ","")}</em></div>
    <div class="report-sub">Generated {data["generated_at"]}</div>
    <div class="ai-headline">
      <div class="ai-hl-tag">Claude · Weekly Read</div>
      <div class="ai-headline-text">{headline}</div>
    </div>
  </div>
</div>
<div class="wrap">
  <div class="hero" style="margin-top:24px;">
    <div class="hs"><span class="hs-l">This Week</span><span class="hs-v {hs_v_class}">{dist} km</span><span class="hs-delta" style="color:{vs_col}">{vs_str}</span></div>
    <div class="hs"><span class="hs-l">Runs</span><span class="hs-v">{runs}</span></div>
    <div class="hs"><span class="hs-l">Avg Pace</span><span class="hs-v t">{pace}/km</span></div>
    <div class="hs"><span class="hs-l">Avg HR</span><span class="hs-v r">{hr}{" bpm" if hr != "–" else ""}</span></div>
    <div class="hs"><span class="hs-l">Elevation</span><span class="hs-v a">{elev} m</span></div>
    <div class="hs"><span class="hs-l">8-wk Avg</span><span class="hs-v m">{rolling} km</span></div>
    <div class="hs"><span class="hs-l">Streak</span><span class="hs-v" style="color:{streak_color}">{streak}d</span></div>
    <div class="hs"><span class="hs-l">Aero Eff.</span><span class="hs-v t">{aeff_str}</span><span class="hs-delta" style="color:{aeff_delta_color}">{aeff_delta}</span></div>
  </div>
  {race_section}
  <div class="section">
    <div class="sh">Weekly Analysis <span class="ai-badge">◆ Claude</span></div>
    <div class="narrative">{narrative_html}</div>
    <div class="signals">{signals_html}</div>
    <div class="next-focus"><div class="nf-tag">Focus for next week</div><div class="nf-body">{next_focus}</div></div>
  </div>
  {analytics_section}
  {speed_section}
  {plan_actual_section}
  {next_week_section}
  {heatmap_section}
  <div class="section">
    <div class="sh">Volume — Last {history_weeks} Weeks</div>
    <div class="fig">
      <div class="fig-h"><span class="fig-title">Weekly Distance & Rolling Average</span><span class="fig-n">Fig. 1</span></div>
      <div class="fig-body"><canvas id="chart-weekly" height="220"></canvas></div>
      <div class="fig-cap">Weekly distance (bars). Orange = this week. Teal dashed = 8-week rolling average.</div>
    </div>
  </div>
  <div class="section">
    <div class="sh">Notable Activities</div>
    <div class="act-grid">{notable_html}</div>
  </div>
  <div class="section">
    <div class="sh">Heart Rate</div>
    <div class="fig">
      <div class="fig-h"><span class="fig-title">Average HR per Week</span><span class="fig-n">Fig. 2</span></div>
      <div class="fig-body"><canvas id="chart-hr" height="200"></canvas></div>
      <div class="fig-cap">Sustained downward trend at maintained volume = improving aerobic fitness.</div>
    </div>
    <div class="fig">
      <div class="fig-h"><span class="fig-title">HR Zone Distribution — This Week</span><span class="fig-n">Fig. 3</span></div>
      <div class="fig-body"><div class="zones">{zones_html}</div></div>
      <div class="fig-cap">Seconds per zone from Garmin activity data.</div>
    </div>
  </div>
  <div class="section">
    <div class="sh">Aerobic Efficiency</div>
    <div class="fig">
      <div class="fig-h"><span class="fig-title">Pace at 130–145 bpm — 8-Week Rolling</span><span class="fig-n">Fig. 4</span></div>
      <div class="fig-body" style="padding:20px 24px;">
        <div style="display:flex;align-items:flex-end;gap:32px;flex-wrap:wrap;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:9px;color:var(--mu);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px;">Current 8-week avg</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:36px;font-weight:500;color:var(--t);">{aeff_str}</div>
          </div>
          {aeff_delta_html}
        </div>
      </div>
    </div>
  </div>
  <div class="section">
    <div class="sh">Parkrun</div>
    {bp_html}
    {pr_leaderboard_html}
    <div class="fig">
      <div class="fig-h"><span class="fig-title">Parkrun Times & HR</span><span class="fig-n">Fig. 5</span></div>
      <div class="fig-body"><canvas id="chart-parkrun" height="230"></canvas></div>
      <div class="fig-cap">Finish time (bars, left). HR (line, right). Green = sub-20.</div>
    </div>
  </div>
  <div class="footer">
    <p>ResearchAssistants_SBW · running_bot · {data["generated_at"]}<br>
    Garmin Connect (activities + analytics + calendar) · Claude (claude-sonnet-4-6)</p>
  </div>
</div>
<script>
const GC='rgba(26,32,53,.9)',TC='#2a3050';
Chart.defaults.color='#4a5270';Chart.defaults.font.family="'IBM Plex Mono',monospace";Chart.defaults.font.size=11;
const wd={w_dists};
new Chart(document.getElementById('chart-weekly'),{{type:'bar',
  data:{{labels:{w_labels},datasets:[
    {{label:'Weekly km',data:wd,backgroundColor:wd.map((_,i)=>i==={last_idx}?'#f97316':'rgba(249,115,22,.48)'),borderRadius:4,order:2}},
    {{type:'line',label:'8-wk avg',data:{r_line},borderColor:'rgba(20,184,166,.65)',borderWidth:1.5,borderDash:[6,4],pointRadius:0,tension:0,order:1}}
  ]}},
  options:{{responsive:true,maintainAspectRatio:true,
    plugins:{{legend:{{labels:{{color:'#5a6280',boxWidth:12}}}},tooltip:{{callbacks:{{label:c=>` ${{c.parsed.y}} km`}}}}}},
    scales:{{x:{{grid:{{display:false}},ticks:{{color:TC,maxRotation:45,font:{{size:9}}}}}},y:{{grid:{{color:GC}},ticks:{{color:TC,callback:v=>v+'km'}}}}}}}}
}});
const hd={w_hrs};
new Chart(document.getElementById('chart-hr'),{{type:'line',
  data:{{labels:{w_labels},datasets:[{{label:'Avg HR',data:hd,borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,.08)',borderWidth:2.5,pointRadius:4,pointBackgroundColor:hd.map(h=>h&&h<=126?'#22c55e':'#ef4444'),tension:.3,fill:true}}]}},
  options:{{responsive:true,maintainAspectRatio:true,
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>`${{c.parsed.y}} bpm`}}}}}},
    scales:{{x:{{grid:{{color:GC}},ticks:{{color:TC,maxRotation:45,font:{{size:9}}}}}},y:{{grid:{{color:GC}},ticks:{{color:TC,callback:v=>v+' bpm'}}}}}}}}
}});
const prt={pr_times},prh={pr_hrs};
new Chart(document.getElementById('chart-parkrun'),{{type:'bar',
  data:{{labels:{pr_labels},datasets:[
    {{type:'bar',label:'Time',data:prt,backgroundColor:prt.map(t=>t<20?'#22c55e':t<22?'#f97316':t>27?'#1e2540':'#3b82f6'),borderRadius:3,yAxisID:'y',order:2}},
    {{type:'line',label:'HR',data:prh,borderColor:'rgba(239,68,68,.6)',borderWidth:1.5,pointRadius:3,tension:.3,yAxisID:'y2',order:1}}
  ]}},
  options:{{responsive:true,maintainAspectRatio:true,
    plugins:{{legend:{{labels:{{color:'#5a6280',boxWidth:12}}}}}},
    scales:{{x:{{grid:{{display:false}},ticks:{{color:TC,font:{{size:9}}}}}},
      y:{{grid:{{color:GC}},reverse:true,min:18,max:32,ticks:{{color:TC,callback:v=>`${{Math.floor(v)}}:${{String(Math.round((v-Math.floor(v))*60)).padStart(2,'0')}}`}}}},
      y2:{{grid:{{display:false}},min:110,max:190,position:'right',ticks:{{color:'#ef4444'}}}}}}}}
}});
</script>
</body>
</html>"""
