#!/usr/bin/env python3
"""
Generate a portfolio-style HTML overview for the ASC 2026 dataset.
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
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def day_key_from_iso(value):
    return (value or "")[:10]


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
    summary = defaultdict(lambda: {
        "updates": 0,
        "snapshots": 0,
        "weather": 0,
        "pins": 0,
    })

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
            "temp_avg": sum(temps) / len(temps) if temps else None,
            "wind_avg": sum(winds) / len(winds) if winds else None,
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
        for message, count in summary["unexpected_errors"].most_common(8)
    ]
    return summary


def render_cards(cards):
    return "".join(
        f"""
        <div class="card">
            <div class="card-label">{html.escape(card['label'])}</div>
            <div class="card-value">{html.escape(card['value'])}</div>
            <div class="card-note">{html.escape(card.get('note', ''))}</div>
        </div>
        """
        for card in cards
    )


def render_daily_rows(daily_counts):
    peak_updates = max((row["updates"] for row in daily_counts.values()), default=1)
    peak_snapshots = max((row["snapshots"] for row in daily_counts.values()), default=1)
    rows = []
    for day, counts in daily_counts.items():
        rows.append(
            f"""
            <tr>
                <td>{html.escape(day)}</td>
                <td>
                    <div class="bar-cell"><span>{fmt_int(counts['updates'])}</span>
                    <div class="bar"><div style="width:{(counts['updates'] / peak_updates) * 100:.1f}%"></div></div></div>
                </td>
                <td>
                    <div class="bar-cell"><span>{fmt_int(counts['snapshots'])}</span>
                    <div class="bar"><div style="width:{(counts['snapshots'] / peak_snapshots) * 100:.1f}%"></div></div></div>
                </td>
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
                <td>{fmt_float(stats['distance_km'])}</td>
                <td>{fmt_duration(stats['elapsed_sec'])}</td>
                <td>{fmt_float(stats['active_ratio_pct'] or 0)}%</td>
                <td>{fmt_float(stats['max_speed_kph'] or 0)}</td>
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
                <td>{fmt_float(row['temp_min']) if row['temp_min'] is not None else 'N/A'} C</td>
                <td>{fmt_float(row['temp_avg']) if row['temp_avg'] is not None else 'N/A'} C</td>
                <td>{fmt_float(row['temp_max']) if row['temp_max'] is not None else 'N/A'} C</td>
                <td>{fmt_float(row['wind_avg']) if row['wind_avg'] is not None else 'N/A'} km/h</td>
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
                <td>{html.escape(package['output_file'])}</td>
                <td>{fmt_int(package['record_count'])}</td>
                <td>{fmt_bytes(package['original_bytes'])}</td>
                <td>{fmt_bytes(package['archived_bytes'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_log_errors(log_summary):
    if not log_summary["unexpected_errors"]:
        return '<div class="muted-block">No unexpected error lines found in the archived collector log.</div>'
    return "".join(
        f"""
        <div class="error-item">
            <div class="error-count">{fmt_int(item['count'])}</div>
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
        {
            "name": team["name"],
            "color": team["color"],
            "points": simplify_points(team["points"]),
        }
        for team in teams
    ]
    pin_payload = pins

    log_path = Path(data_dir) / "logs" / "collector_2026-07-26_to_2026-08-01.log.gz"
    log_summary = summarize_log(log_path)

    sample_update = next(data_access.iter_update_records(data_dir), {})
    sample_weather = next(iter(weather_records), {})
    sample_pin = (pin_records[0]["pins"][0] if pin_records and pin_records[0].get("pins") else {})
    sample_snapshot = next(data_access.iter_snapshot_records(data_dir), {})
    sample_tracker = (sample_snapshot.get("trackers") or [{}])[0]

    first_time = min((team["stats"]["start_sample_unix"] for team in teams if team["stats"]["start_sample_unix"]), default=0)
    last_time = max((team["stats"]["end_sample_unix"] for team in teams if team["stats"]["end_sample_unix"]), default=0)
    coverage = (
        f"{datetime.fromtimestamp(first_time).isoformat(sep=' ', timespec='minutes')} "
        f"to {datetime.fromtimestamp(last_time).isoformat(sep=' ', timespec='minutes')}"
        if first_time and last_time else "N/A"
    )

    cards = [
        {"label": "Tracked dataset size", "value": fmt_bytes(manifest["totals"]["archived_bytes"] if manifest else 0), "note": "Lossless archive committed to Git"},
        {"label": "Updates", "value": fmt_int(sum(row["updates"] for row in daily_counts.values())), "note": "Location updates across all teams"},
        {"label": "Snapshots", "value": fmt_int(sum(row["snapshots"] for row in daily_counts.values())), "note": "Full tracker snapshots"},
        {"label": "Weather samples", "value": fmt_int(sum(row["weather"] for row in daily_counts.values())), "note": "Open-Meteo observations"},
        {"label": "Teams seen", "value": fmt_int(len(teams)), "note": "Unique trackers with route history"},
        {"label": "Pins captured", "value": fmt_int(len(pins)), "note": "Approved detours, hazards, and notes"},
        {"label": "Coverage window", "value": coverage, "note": "From first to last team sample"},
        {"label": "Collector log", "value": fmt_int(log_summary["line_count"]), "note": "Archived log lines for debugging"},
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
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #0b0f14;
  color: #e6edf3;
}}
.page {{
  width: min(1400px, calc(100% - 40px));
  margin: 0 auto;
  padding: 28px 0 48px;
}}
.hero {{
  padding: 24px;
  border: 1px solid #30363d;
  border-radius: 18px;
  background: linear-gradient(140deg, rgba(88,166,255,0.14), rgba(99,46,160,0.18));
}}
.hero h1 {{ margin: 0 0 8px; font-size: 2rem; }}
.hero p {{ margin: 0; color: #c9d1d9; line-height: 1.6; max-width: 960px; }}
.section {{ margin-top: 22px; padding: 20px; border: 1px solid #30363d; border-radius: 18px; background: #11161d; }}
.section h2 {{ margin: 0 0 14px; font-size: 1.2rem; }}
.grid {{ display: grid; gap: 14px; }}
.cards {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
.card {{
  padding: 16px;
  border-radius: 14px;
  background: #0d1117;
  border: 1px solid #30363d;
}}
.card-label {{ font-size: 0.78rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.06em; }}
.card-value {{ margin-top: 8px; font-size: 1.3rem; font-weight: 700; line-height: 1.3; }}
.card-note {{ margin-top: 6px; font-size: 0.82rem; color: #8b949e; }}
.muted {{ color: #8b949e; }}
.muted-block {{
  padding: 14px;
  border: 1px dashed #30363d;
  border-radius: 12px;
  color: #8b949e;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}}
th, td {{
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid #21262d;
  vertical-align: top;
}}
th {{
  color: #8b949e;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.bar-cell {{
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 10px;
  align-items: center;
}}
.bar {{
  height: 8px;
  border-radius: 999px;
  background: #1f2937;
  overflow: hidden;
}}
.bar > div {{
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #58a6ff, #3fb950);
}}
.dot {{
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}}
#map {{ height: 540px; border-radius: 16px; overflow: hidden; }}
.two-col {{
  display: grid;
  grid-template-columns: 1.5fr 1fr;
  gap: 18px;
}}
.schema-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}}
.schema-card {{
  padding: 14px;
  border-radius: 12px;
  background: #0d1117;
  border: 1px solid #30363d;
}}
.schema-card code {{
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
  color: #9ecbff;
  font-size: 0.8rem;
}}
.error-item {{
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(248,81,73,0.3);
  background: rgba(248,81,73,0.08);
  margin-bottom: 10px;
}}
.error-count {{
  font-size: 1.1rem;
  font-weight: 700;
  color: #ffb3ad;
}}
@media (max-width: 980px) {{
  .page {{ width: min(100% - 20px, 1400px); }}
  .two-col {{ grid-template-columns: 1fr; }}
  #map {{ height: 420px; }}
}}
</style>
</head>
<body>
<div class="page">
  <section class="hero">
    <h1>ASC 2026 Data Portfolio</h1>
    <p>
      This page turns the full American Solar Challenge capture into something browsable: the
      tracked archival dataset, team route coverage, weather context, approved road pins, capture
      cadence by day, and the collector log that explains how the scrape behaved while the race
      was live.
    </p>
  </section>

  <section class="section">
    <h2>Archive Overview</h2>
    <div class="grid cards">{render_cards(cards)}</div>
  </section>

  <section class="section">
    <h2>Route Overview</h2>
    <div id="map"></div>
  </section>

  <section class="section">
    <h2>Daily Coverage</h2>
    <table>
      <thead>
        <tr><th>Day</th><th>Updates</th><th>Snapshots</th><th>Weather</th><th>Pins</th></tr>
      </thead>
      <tbody>{render_daily_rows(daily_counts)}</tbody>
    </table>
  </section>

  <section class="section">
    <h2>Team Route Metrics</h2>
    <table>
      <thead>
        <tr><th>Team</th><th>Samples</th><th>Distance km</th><th>Elapsed</th><th>Active ratio</th><th>Max speed</th><th>Anomalies</th></tr>
      </thead>
      <tbody>{render_team_rows(teams)}</tbody>
    </table>
  </section>

  <section class="section two-col">
    <div>
      <h2>Weather Context</h2>
      <table>
        <thead>
          <tr><th>Day</th><th>Samples</th><th>Min</th><th>Avg</th><th>Max</th><th>Avg wind</th><th>Typical sky</th></tr>
        </thead>
        <tbody>{render_weather_rows(weather_daily)}</tbody>
      </table>
    </div>
    <div>
      <h2>Collector Log Signals</h2>
      <div class="grid cards">
        {render_cards([
            {"label": "First log line", "value": log_summary["first_ts"] or "N/A", "note": "Collector startup"},
            {"label": "Last log line", "value": log_summary["last_ts"] or "N/A", "note": "Archive packaging tail"},
            {"label": "Reconnect attempts", "value": fmt_int(log_summary["reconnects"]), "note": "Websocket reconnect loops"},
            {"label": "Snapshot log entries", "value": fmt_int(log_summary["snapshot_logs"]), "note": "Saved trace snapshots"},
            {"label": "Weather log entries", "value": fmt_int(log_summary["weather_logs"]), "note": "Weather fetch cadence"},
            {"label": "Log levels", "value": ", ".join(f"{k}:{v}" for k, v in sorted(log_summary["levels"].items())) or "N/A", "note": "INFO / DEBUG / ERROR mix"},
        ])}
      </div>
      <div style="margin-top:14px">
        <h2 style="font-size:1rem;margin-bottom:10px">Unexpected Error Samples</h2>
        {render_log_errors(log_summary)}
      </div>
    </div>
  </section>

  <section class="section">
    <h2>Data Components</h2>
    <div class="schema-grid">
      <div class="schema-card"><div class="card-label">Update record fields</div><code>{html.escape(json.dumps(sorted(sample_update.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="card-label">Snapshot record fields</div><code>{html.escape(json.dumps(sorted(sample_snapshot.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="card-label">Snapshot tracker fields</div><code>{html.escape(json.dumps(sorted(sample_tracker.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="card-label">Weather record fields</div><code>{html.escape(json.dumps(sorted(sample_weather.keys()), indent=2))}</code></div>
      <div class="schema-card"><div class="card-label">Pin fields</div><code>{html.escape(json.dumps(sorted(sample_pin.keys()), indent=2))}</code></div>
    </div>
  </section>

  <section class="section">
    <h2>Tracked Archive Inventory</h2>
    <table>
      <thead>
        <tr><th>Kind</th><th>Date</th><th>Archive file</th><th>Records</th><th>Original size</th><th>Archive size</th></tr>
      </thead>
      <tbody>{render_manifest_rows(manifest)}</tbody>
    </table>
  </section>
</div>

<script>
const MAP_TEAMS = {json.dumps(map_payload)};
const MAP_PINS = {json.dumps(pin_payload)};
const map = L.map("map", {{ zoomControl: true }});
L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>'
}}).addTo(map);

const bounds = L.latLngBounds([]);
MAP_TEAMS.forEach(team => {{
  const latlngs = team.points.map(point => [point.lat, point.lng]);
  if (!latlngs.length) return;
  L.polyline(latlngs, {{
    color: team.color,
    weight: 3,
    opacity: 0.8
  }}).bindTooltip(team.name, {{sticky: true}}).addTo(map);
  latlngs.forEach(latlng => bounds.extend(latlng));
}});
MAP_PINS.forEach(pin => {{
  if (pin.lat == null || pin.lng == null) return;
  const marker = L.circleMarker([pin.lat, pin.lng], {{
    radius: 6,
    color: pin.color || "orange",
    fillColor: pin.color || "orange",
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
