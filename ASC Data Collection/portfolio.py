#!/usr/bin/env python3
"""
Generate a portable, teammate-friendly HTML overview for the ASC 2026 dataset.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import data_access

DEFAULT_OUTPUT = "asc_data_portfolio.html"
DEFAULT_MANIFEST = "dataset/manifest.json"
LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+) "
    r"\[(?P<level>[A-Z]+)\] (?P<logger>[^:]+): (?P<message>.*)$"
)


def fmt_int(value):
    return f"{int(value):,}"


def fmt_float(value, digits=1):
    return f"{float(value):,.{digits}f}"


def fmt_bytes(value):
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def miles(km):
    return None if km is None else km / 1.609344


def mph(kph):
    return None if kph is None else kph / 1.609344


def feet(meters):
    return None if meters is None else meters * 3.28084


def f_temp(celsius):
    return None if celsius is None else (celsius * 9.0 / 5.0) + 32.0


def day_key_from_iso(value):
    return (value or "")[:10]


def unit_value(metric, imperial):
    return (
        f'<span class="unit-value" data-metric="{html.escape(metric)}" '
        f'data-imperial="{html.escape(imperial)}">{html.escape(metric)}</span>'
    )


def simplify_points(points, max_points=240):
    if len(points) <= max_points:
        return [{"lat": p["lat"], "lng": p["lng"]} for p in points]
    step = max(1, math.ceil(len(points) / max_points))
    simplified = [{"lat": p["lat"], "lng": p["lng"]} for p in points[::step]]
    last = {"lat": points[-1]["lat"], "lng": points[-1]["lng"]}
    if simplified[-1] != last:
        simplified.append(last)
    return simplified


def load_manifest(path):
    manifest_path = Path(path)
    if not manifest_path.exists():
        return None
    with open(manifest_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_daily_counts(data_dir, snapshots, weather_records, pin_records):
    summary = defaultdict(lambda: {"updates": 0, "snapshots": 0, "weather": 0, "pins": 0})

    for entry in data_access.iter_update_records(data_dir):
        summary[day_key_from_iso(entry.get("_t"))]["updates"] += 1
    for snapshot in snapshots:
        summary[day_key_from_iso(snapshot.get("ts"))]["snapshots"] += 1
    for weather in weather_records:
        summary[day_key_from_iso(weather.get("_recorded"))]["weather"] += 1
    for record in pin_records:
        summary[day_key_from_iso(record.get("timestamp"))]["pins"] += len(record.get("pins", []))
    return dict(sorted(summary.items()))


def summarize_weather(weather_records):
    grouped = defaultdict(list)
    for record in weather_records:
        grouped[day_key_from_iso(record.get("_recorded"))].append(record)

    daily = []
    for day, items in sorted(grouped.items()):
        temps = [item.get("temp_c") for item in items if item.get("temp_c") is not None]
        winds = [item.get("wind_kph") for item in items if item.get("wind_kph") is not None]
        conditions = Counter(item.get("weather_desc") or "Unknown" for item in items)
        daily.append({
            "day": day,
            "samples": len(items),
            "temp_min": min(temps) if temps else None,
            "temp_max": max(temps) if temps else None,
            "temp_avg": (sum(temps) / len(temps)) if temps else None,
            "wind_avg": (sum(winds) / len(winds)) if winds else None,
            "top_condition": conditions.most_common(1)[0][0] if conditions else "Unknown",
        })
    return daily


def summarize_log(log_path):
    summary = {
        "line_count": 0,
        "first_ts": "",
        "last_ts": "",
        "levels": Counter(),
        "unexpected_errors": Counter(),
        "reconnects": 0,
        "snapshot_logs": 0,
        "weather_logs": 0,
    }
    if not log_path.exists():
        return summary

    with gzip.open(log_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            summary["line_count"] += 1
            match = LOG_LINE_RE.match(line.rstrip())
            if not match:
                continue
            summary["first_ts"] = summary["first_ts"] or match.group("ts")
            summary["last_ts"] = match.group("ts")
            level = match.group("level")
            message = match.group("message")
            summary["levels"][level] += 1
            if message.startswith("Unexpected error:"):
                summary["unexpected_errors"][message] += 1
            if message.startswith("Reconnecting in"):
                summary["reconnects"] += 1
            if message.startswith("Snapshot: traces_"):
                summary["snapshot_logs"] += 1
            if message.startswith("Weather:"):
                summary["weather_logs"] += 1

    summary["levels"] = dict(summary["levels"])
    summary["unexpected_errors"] = [
        {"message": message, "count": count}
        for message, count in summary["unexpected_errors"].most_common(6)
    ]
    return summary


def render_cards(cards):
    return "".join(
        f"""
        <article class="stat-card">
          <div class="eyebrow">{html.escape(card['label'])}</div>
          <div class="stat-value">{card['value']}</div>
          <div class="stat-note">{html.escape(card.get('note', ''))}</div>
        </article>
        """
        for card in cards
    )


def render_daily_rows(daily_counts):
    peak_updates = max((row["updates"] for row in daily_counts.values()), default=1)
    rows = []
    for day, counts in daily_counts.items():
        rows.append(
            f"""
            <tr>
              <td>{html.escape(day)}</td>
              <td>
                <div class="bar-cell">
                  <span>{fmt_int(counts['updates'])}</span>
                  <div class="bar"><div style="width:{(counts['updates'] / peak_updates) * 100:.1f}%"></div></div>
                </div>
              </td>
              <td>{fmt_int(counts['snapshots'])}</td>
              <td>{fmt_int(counts['weather'])}</td>
              <td>{fmt_int(counts['pins'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_team_rows(teams):
    rows = []
    for team in sorted(teams, key=lambda item: item["stats"]["distance_km"], reverse=True):
        stats = team["stats"]
        rows.append(
            f"""
            <tr>
              <td><span class="dot" style="background:{html.escape(team['color'])}"></span>{html.escape(team['name'])}</td>
              <td>{fmt_int(stats['sample_count'])}</td>
              <td>{unit_value(f"{fmt_float(stats['distance_km'])} km", f"{fmt_float(miles(stats['distance_km']))} mi")}</td>
              <td>{unit_value(f"{fmt_float(stats['moving_average_speed_kph'] or 0)} km/h", f"{fmt_float(mph(stats['moving_average_speed_kph'] or 0))} mph") if stats['moving_average_speed_kph'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{fmt_float(stats['max_speed_kph'] or 0)} km/h", f"{fmt_float(mph(stats['max_speed_kph'] or 0))} mph") if stats['max_speed_kph'] is not None else 'N/A'}</td>
              <td>{fmt_duration(stats['elapsed_sec'])}</td>
              <td>{fmt_float(stats['active_ratio_pct'] or 0)}%</td>
              <td>{fmt_int(stats['anomaly_count'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_weather_rows(weather_daily):
    rows = []
    for row in weather_daily:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(row['day'])}</td>
              <td>{fmt_int(row['samples'])}</td>
              <td>{unit_value(f"{fmt_float(row['temp_min'])} C", f"{fmt_float(f_temp(row['temp_min']))} F") if row['temp_min'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{fmt_float(row['temp_avg'])} C", f"{fmt_float(f_temp(row['temp_avg']))} F") if row['temp_avg'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{fmt_float(row['temp_max'])} C", f"{fmt_float(f_temp(row['temp_max']))} F") if row['temp_max'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{fmt_float(row['wind_avg'])} km/h", f"{fmt_float(mph(row['wind_avg']))} mph") if row['wind_avg'] is not None else 'N/A'}</td>
              <td>{html.escape(row['top_condition'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_manifest_rows(manifest):
    if not manifest:
        return ""
    rows = []
    for package in sorted(manifest.get("packages", []), key=lambda item: (item["kind"], item["date"])):
        rows.append(
            f"""
            <tr>
              <td>{html.escape(package['kind'])}</td>
              <td>{html.escape(package['date'])}</td>
              <td><code>{html.escape(package['output_file'])}</code></td>
              <td>{fmt_int(package['record_count'])}</td>
              <td>{fmt_bytes(package['original_bytes'])}</td>
              <td>{fmt_bytes(package['archived_bytes'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_log_errors(log_summary):
    if not log_summary["unexpected_errors"]:
        return '<div class="callout muted-callout">No unexpected error lines found in the archived collector log.</div>'
    return "".join(
        f"""
        <div class="log-item">
          <div class="log-count">{fmt_int(item['count'])}</div>
          <div>{html.escape(item['message'])}</div>
        </div>
        """
        for item in log_summary["unexpected_errors"]
    )


def generate_html(data_dir, manifest_path):
    manifest = load_manifest(manifest_path)
    teams = data_access.load_updates(data_dir)
    snapshots = data_access.load_snapshots(data_dir)
    pins = data_access.load_pins(data_dir)
    weather_records = list(data_access.iter_weather_records(data_dir))
    pin_records = list(data_access.iter_pin_snapshots(data_dir))
    daily_counts = summarize_daily_counts(data_dir, snapshots, weather_records, pin_records)
    weather_daily = summarize_weather(weather_records)

    map_payload = [
        {"name": team["name"], "color": team["color"], "points": simplify_points(team["points"])}
        for team in teams
    ]
    pin_payload = pins

    log_path = Path(data_dir) / "logs" / "collector_2026-07-26_to_2026-08-01.log.gz"
    log_summary = summarize_log(log_path)

    sample_update = next(data_access.iter_update_records(data_dir), {})
    sample_weather = next(iter(weather_records), {})
    sample_pin = pin_records[0]["pins"][0] if pin_records and pin_records[0].get("pins") else {}
    sample_snapshot = next(data_access.iter_snapshot_records(data_dir), {})
    sample_tracker = (sample_snapshot.get("trackers") or [{}])[0]

    first_time = min(
        (team["stats"]["start_sample_unix"] for team in teams if team["stats"]["start_sample_unix"]),
        default=0,
    )
    last_time = max(
        (team["stats"]["end_sample_unix"] for team in teams if team["stats"]["end_sample_unix"]),
        default=0,
    )
    coverage = (
        f"{datetime.fromtimestamp(first_time).isoformat(sep=' ', timespec='minutes')} "
        f"to {datetime.fromtimestamp(last_time).isoformat(sep=' ', timespec='minutes')}"
        if first_time and last_time
        else "N/A"
    )

    archive_bytes = manifest["totals"]["archived_bytes"] if manifest else 0
    original_bytes = manifest["totals"]["original_bytes"] if manifest else 0
    compression_ratio = (archive_bytes / original_bytes) if original_bytes else 0

    cards = [
        {"label": "Tracked archive", "value": fmt_bytes(archive_bytes), "note": "Git-portable source of truth"},
        {"label": "Original source volume", "value": fmt_bytes(original_bytes), "note": "Before compression and packaging"},
        {"label": "Compression ratio", "value": f"{compression_ratio:.1%}" if compression_ratio else "N/A", "note": "Archive size divided by original size"},
        {"label": "Teams seen", "value": fmt_int(len(teams)), "note": "Unique trackers with route history"},
        {"label": "Coverage window", "value": coverage, "note": "From first to last recorded team sample"},
        {"label": "Approved pins", "value": fmt_int(len(pins)), "note": "Hazards, detours, and route notes"},
        {"label": "Snapshot records", "value": fmt_int(sum(item["count"] for item in snapshots)), "note": "Full tracker snapshots"},
        {"label": "Collector log lines", "value": fmt_int(log_summary["line_count"]), "note": "Archived debugging trail"},
    ]

    top_team = max(teams, key=lambda item: item["stats"]["distance_km"]) if teams else None
    sections = [
        {
            "title": "Portable",
            "body": "Git has the archive. `restore_dataset.py` rebuilds the raw layout.",
        },
        {
            "title": "Start here",
            "body": "`summary.json`, `daily_coverage.csv`, and `team_metrics.csv` give the fast read.",
        },
        {
            "title": "Longest route",
            "body": (
                f"{top_team['name']}: {fmt_float(top_team['stats']['distance_km'])} km / "
                f"{fmt_float(miles(top_team['stats']['distance_km']))} mi."
                if top_team else "No team route data available."
            ),
        },
    ]

    quick_links = [
        ("Trajectory explorer", "asc_trajectory.html"),
        ("Compact dashboard", "asc_dashboard.html"),
        ("Dataset README", "dataset/README.md"),
        ("Teammate guide", "dataset/TEAMMATE_GUIDE.md"),
        ("Restore script", "restore_dataset.py"),
    ]

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASC 2026 Data Portfolio</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ box-sizing: border-box; }}
:root {{
  color-scheme: dark;
  --page: #0b0e13;
  --panel: #11151c;
  --panel-2: #171c24;
  --line: #242b36;
  --line-soft: #1b222c;
  --text: #e7ebf0;
  --muted: #97a3b6;
  --accent: #d3b77a;
  --accent-soft: rgba(211, 183, 122, 0.14);
  --danger: #ef8c86;
  --map: #9ecbff;
}}
html, body {{
  margin: 0;
  background: var(--page);
  color: var(--text);
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
body {{ padding: 24px 0 40px; }}
.page {{
  width: min(1360px, calc(100% - 28px));
  margin: 0 auto;
}}
.topbar {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 18px;
}}
.brand {{
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.eyebrow {{
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 0.72rem;
}}
.top-actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}}
.chip, .link-chip, .toggle button {{
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--text);
  border-radius: 999px;
  padding: 10px 14px;
  font: inherit;
  text-decoration: none;
}}
.link-chip:hover, .toggle button:hover {{ background: var(--panel-2); }}
.toggle {{
  display: inline-flex;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel);
}}
.toggle button {{
  border: none;
  padding: 8px 12px;
  background: transparent;
  color: var(--muted);
}}
.toggle button.active {{
  background: var(--accent-soft);
  color: var(--text);
}}
.hero {{
  display: grid;
  grid-template-columns: 1.6fr 1fr;
  gap: 18px;
}}
.hero-main, .hero-side, .section {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 22px;
}}
.hero-main {{
  padding: 30px;
  min-height: 280px;
}}
.hero-main h1 {{
  margin: 10px 0 14px;
  font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  font-size: clamp(2.1rem, 5vw, 4rem);
  line-height: 1.02;
  font-weight: 600;
  letter-spacing: -0.03em;
}}
.hero-main p {{
  max-width: 52rem;
  color: var(--muted);
  font-size: 1.02rem;
  line-height: 1.7;
  margin: 0;
}}
.hero-side {{
  padding: 24px;
  display: grid;
  gap: 12px;
  align-content: start;
}}
.callout {{
  padding: 16px 18px;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(255,255,255,0.02), transparent);
  border: 1px solid var(--line-soft);
}}
.callout strong {{
  display: block;
  margin-bottom: 6px;
  font-size: 0.98rem;
}}
.muted-callout {{
  color: var(--muted);
  border-style: dashed;
}}
.section {{
  margin-top: 18px;
  padding: 22px;
}}
.section-header {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: end;
  margin-bottom: 14px;
}}
.section-title {{
  margin: 0;
  font-size: 1.2rem;
}}
.section-copy {{
  color: var(--muted);
  max-width: 48rem;
  line-height: 1.6;
  margin: 0;
}}
.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
}}
.stat-card {{
  border: 1px solid var(--line-soft);
  border-radius: 18px;
  padding: 18px;
  background: linear-gradient(180deg, rgba(255,255,255,0.02), transparent);
}}
.stat-value {{
  margin-top: 10px;
  font-size: 1.45rem;
  line-height: 1.25;
  font-weight: 650;
}}
.stat-note {{
  margin-top: 8px;
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.5;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1.35fr 1fr;
  gap: 16px;
}}
.three-col {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}}
#map {{
  height: 560px;
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  background:
    linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px),
    radial-gradient(circle at top, rgba(211, 183, 122, 0.10), transparent 55%),
    #0d1117;
  background-size: 48px 48px, 48px 48px, auto, auto;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}}
th, td {{
  padding: 12px 10px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: top;
}}
th {{
  color: var(--muted);
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}}
tr:hover td {{ background: rgba(255,255,255,0.015); }}
.dot {{
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}}
.bar-cell {{
  display: grid;
  grid-template-columns: 84px 1fr;
  gap: 10px;
  align-items: center;
}}
.bar {{
  height: 7px;
  border-radius: 999px;
  background: #1c232d;
  overflow: hidden;
}}
.bar > div {{
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #76a7d8);
}}
.link-list {{
  display: grid;
  gap: 10px;
}}
.file-link {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: 16px;
  color: var(--text);
  text-decoration: none;
  background: rgba(255,255,255,0.015);
}}
.file-link:hover {{ background: var(--panel-2); }}
.file-link span:last-child {{
  color: var(--muted);
  white-space: nowrap;
}}
.schema-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}}
.schema-card {{
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: 16px;
  background: rgba(255,255,255,0.015);
}}
.schema-card code {{
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--map);
  font-size: 0.78rem;
  line-height: 1.5;
}}
.log-item {{
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: 12px;
  padding: 12px 14px;
  margin-bottom: 10px;
  border-radius: 14px;
  border: 1px solid rgba(239, 140, 134, 0.26);
  background: rgba(239, 140, 134, 0.07);
}}
.log-count {{
  color: var(--danger);
  font-size: 1.15rem;
  font-weight: 700;
}}
code {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}}
.footer-note {{
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.6;
}}
@media (max-width: 1080px) {{
  .hero, .two-col, .three-col {{ grid-template-columns: 1fr; }}
  #map {{ height: 440px; }}
}}
</style>
</head>
<body>
<div class="page">
  <header class="topbar">
    <div class="brand">
      <div class="eyebrow">American Solar Challenge 2026</div>
      <div>Git-tracked ASC archive.</div>
    </div>
    <div class="top-actions">
      <span class="chip">Portable archive</span>
      <div class="toggle" aria-label="Unit system">
        <button id="metric-toggle" class="active" type="button">Metric</button>
        <button id="imperial-toggle" type="button">Imperial</button>
      </div>
    </div>
  </header>

  <section class="hero">
    <div class="hero-main">
      <div class="eyebrow">ASC 2026 Data Portfolio</div>
      <h1>One clean handoff for the full race capture.</h1>
      <p>Portable archive, quick summaries, and no map API dependency.</p>
    </div>
    <aside class="hero-side">
      {"".join(f'<div class="callout"><strong>{html.escape(item["title"])}</strong>{html.escape(item["body"])}</div>' for item in sections)}
    </aside>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Archive Snapshot</h2>
        <p class="section-copy">Archive size, coverage, and handoff state.</p>
      </div>
    </div>
    <div class="stats">{render_cards(cards)}</div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Open These First</h2>
        <p class="section-copy">Open these first.</p>
      </div>
    </div>
    <div class="link-list">
      {"".join(f'<a class="file-link" href="{html.escape(link)}"><span>{html.escape(title)}</span><span>{html.escape(link)}</span></a>' for title, link in quick_links)}
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Route Overview</h2>
        <p class="section-copy">Route-only view. No external tiles.</p>
      </div>
    </div>
    <div id="map"></div>
  </section>

  <section class="section two-col">
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Daily Coverage</h2>
        <p class="section-copy">How much data landed each day.</p>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Day</th><th>Updates</th><th>Snapshots</th><th>Weather</th><th>Pins</th></tr>
        </thead>
        <tbody>{render_daily_rows(daily_counts)}</tbody>
      </table>
    </div>
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Weather Context</h2>
          <p class="section-copy">Daily weather summary.</p>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Day</th><th>Samples</th><th>Min</th><th>Avg</th><th>Max</th><th>Wind</th><th>Sky</th></tr>
        </thead>
        <tbody>{render_weather_rows(weather_daily)}</tbody>
      </table>
    </div>
  </section>

  <section class="section two-col">
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Team Route Metrics</h2>
          <p class="section-copy">Derived route math for comparisons.</p>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Team</th><th>Samples</th><th>Route</th><th>Moving pace</th><th>Top speed</th><th>Elapsed</th><th>Active</th><th>Alerts</th></tr>
        </thead>
        <tbody>{render_team_rows(teams)}</tbody>
      </table>
    </div>
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Collector Signals</h2>
          <p class="section-copy">Collection behavior from the archived log.</p>
        </div>
      </div>
      <div class="stats">
        {render_cards([
            {"label": "First log line", "value": html.escape(log_summary["first_ts"] or "N/A"), "note": "Collector startup"},
            {"label": "Last log line", "value": html.escape(log_summary["last_ts"] or "N/A"), "note": "Final archived line"},
            {"label": "Reconnect attempts", "value": fmt_int(log_summary["reconnects"]), "note": "Websocket retries"},
            {"label": "Snapshot logs", "value": fmt_int(log_summary["snapshot_logs"]), "note": "Saved trace snapshots"},
            {"label": "Weather logs", "value": fmt_int(log_summary["weather_logs"]), "note": "Weather fetch cadence"},
            {"label": "Log levels", "value": html.escape(", ".join(f"{k}:{v}" for k, v in sorted(log_summary["levels"].items())) or "N/A"), "note": "INFO / DEBUG / ERROR mix"},
        ])}
      </div>
      <div style="margin-top:14px">
        <div class="eyebrow" style="margin-bottom:10px">Unexpected error samples</div>
        {render_log_errors(log_summary)}
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Data Components</h2>
        <p class="section-copy">Field shapes at a glance.</p>
      </div>
    </div>
    <div class="schema-grid">
      <div class="schema-card"><div class="eyebrow">Update fields</div><code>{html.escape(json.dumps(sorted(sample_update.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="eyebrow">Snapshot fields</div><code>{html.escape(json.dumps(sorted(sample_snapshot.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="eyebrow">Tracker fields</div><code>{html.escape(json.dumps(sorted(sample_tracker.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="eyebrow">Weather fields</div><code>{html.escape(json.dumps(sorted(sample_weather.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="eyebrow">Pin fields</div><code>{html.escape(json.dumps(sorted(sample_pin.keys()), indent=2))}</code></div>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Tracked Archive Inventory</h2>
        <p class="section-copy">Everything committed to Git.</p>
      </div>
    </div>
    <table>
      <thead>
        <tr><th>Kind</th><th>Date</th><th>Archive file</th><th>Records</th><th>Original size</th><th>Archive size</th></tr>
      </thead>
      <tbody>{render_manifest_rows(manifest)}</tbody>
    </table>
    <p class="footer-note" style="margin-top:14px">Snapshot JSON content is preserved.</p>
  </section>
</div>

<script>
const MAP_TEAMS = {json.dumps(map_payload)};
const MAP_PINS = {json.dumps(pin_payload)};
let unitSystem = "metric";

function applyUnits() {{
  document.querySelectorAll(".unit-value").forEach(node => {{
    node.textContent = node.dataset[unitSystem];
  }});
  document.getElementById("metric-toggle").classList.toggle("active", unitSystem === "metric");
  document.getElementById("imperial-toggle").classList.toggle("active", unitSystem === "imperial");
}}

document.getElementById("metric-toggle").addEventListener("click", () => {{
  unitSystem = "metric";
  applyUnits();
}});
document.getElementById("imperial-toggle").addEventListener("click", () => {{
  unitSystem = "imperial";
  applyUnits();
}});

const map = L.map("map", {{ zoomControl: true }});
const bounds = L.latLngBounds([]);
MAP_TEAMS.forEach(team => {{
  const latlngs = team.points.map(point => [point.lat, point.lng]);
  if (!latlngs.length) return;
  L.polyline(latlngs, {{
    color: team.color,
    weight: 2.8,
    opacity: 0.78
  }}).bindTooltip(team.name, {{sticky: true}}).addTo(map);
  latlngs.forEach(latlng => bounds.extend(latlng));
}});

MAP_PINS.forEach(pin => {{
  if (pin.lat == null || pin.lng == null) return;
  const marker = L.circleMarker([pin.lat, pin.lng], {{
    radius: 5.5,
    color: pin.color || "#d3b77a",
    fillColor: pin.color || "#d3b77a",
    fillOpacity: 0.9,
    weight: 2
  }});
  marker.bindPopup(`<strong>${{pin.message || "Pin"}}</strong><br>${{pin.approved_at || pin.submitted_at || ""}}`);
  marker.addTo(map);
  bounds.extend([pin.lat, pin.lng]);
}});

if (bounds.isValid()) {{
  map.fitBounds(bounds.pad(0.08));
}} else {{
  map.setView([39.5, -98.35], 4);
}}

applyUnits();
</script>
</body>
</html>
"""
    return html_out


def main():
    parser = argparse.ArgumentParser(description="Generate an ASC 2026 HTML data portfolio.")
    parser.add_argument("--dir", default=data_access.DEFAULT_ARCHIVE_DIR, help="Archived data directory")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Dataset manifest path")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="Output HTML file")
    args = parser.parse_args()

    html_out = generate_html(args.dir, args.manifest)
    output_path = Path(args.output)
    output_path.write_text(html_out, encoding="utf-8")
    print(f"Generated {output_path.resolve()}")


if __name__ == "__main__":
    main()
