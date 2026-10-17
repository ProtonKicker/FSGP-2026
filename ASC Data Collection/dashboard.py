#!/usr/bin/env python3
"""
Portable ASC status dashboard with unit conversion support.
"""

import argparse
import json
import math
import socketserver
import webbrowser
from collections import defaultdict
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

import data_access
import visualize

DEFAULT_DATA_DIR = data_access.DEFAULT_ARCHIVE_DIR
DEFAULT_OUTPUT = "asc_dashboard.html"
SNAPSHOT_INTERVAL = 30
FINISH_LAT = 35.222
FINISH_LON = -101.831


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def clean_path_points(points):
    cleaned = []
    for point in points:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        if not cleaned:
            cleaned.append(point)
            continue
        prev = cleaned[-1]
        if (
            point.get("sample_unix") == prev.get("sample_unix")
            and lat == prev.get("lat")
            and lon == prev.get("lon")
        ):
            continue
        jump_km = haversine_km(prev["lat"], prev["lon"], lat, lon)
        prev_speed = prev.get("speed") or 0
        curr_speed = point.get("speed") or 0
        if jump_km < 0.03 and prev_speed <= 1 and curr_speed <= 1:
            continue
        cleaned.append(point)
    return cleaned


def load_snapshots(data_dir):
    return data_access.load_snapshots(data_dir)


def load_latest_weather(data_dir):
    latest = None
    for record in data_access.iter_weather_records(data_dir):
        latest = record
    return latest


def load_distances(data_dir):
    entries = defaultdict(list)
    for entry in data_access.iter_update_records(data_dir):
        if entry.get("lat") is not None and entry.get("lon") is not None:
            entries[entry.get("serial", "?")].append(entry)

    distances = {}
    for serial, points in entries.items():
        points.sort(key=lambda point: point.get("sample_unix", 0) or 0)
        points = clean_path_points(points)
        total = 0.0
        for idx in range(1, len(points)):
            total += haversine_km(
                points[idx - 1]["lat"],
                points[idx - 1]["lon"],
                points[idx]["lat"],
                points[idx]["lon"],
            )
        distances[serial] = total
    return distances


def load_point_counts(data_dir):
    counts = {}
    for entry in data_access.iter_update_records(data_dir):
        serial = entry.get("serial", "?")
        counts[serial] = counts.get(serial, 0) + 1
    return counts


def load_latest(data_dir):
    records = list(data_access.iter_snapshot_records(data_dir))
    if not records:
        return [], None, 0

    latest = records[-1]
    point_counts = load_point_counts(data_dir)
    distances = load_distances(data_dir)
    teams = []
    for tracker in latest.get("trackers", []):
        serial = tracker.get("serial", "")
        lat = tracker.get("latitude")
        lon = tracker.get("longitude")
        to_finish = round(haversine_km(lat, lon, FINISH_LAT, FINISH_LON), 1) if lat and lon else None
        captured_time = tracker.get("captured_time") or latest.get("timestamp", "")
        captured_unix = tracker.get("captured_unix", latest.get("timestamp_unix", 0))
        teams.append({
            "name": tracker.get("name", "?"),
            "serial": serial,
            "lat": lat,
            "lon": lon,
            "speed": tracker.get("speed_kph"),
            "heading": tracker.get("course_deg"),
            "alt": tracker.get("altitude_m"),
            "satellites": tracker.get("satellites"),
            "pdop": tracker.get("pdop"),
            "sample_time": tracker.get("sample_time", ""),
            "sample_unix": tracker.get("sample_unix"),
            "captured_time": captured_time,
            "captured_unix": captured_unix,
            "sample_age_sec": tracker.get("sample_age_sec"),
            "points": point_counts.get(serial, 0),
            "distance_km": round(distances.get(serial, 0), 1),
            "to_finish_km": to_finish,
        })

    teams.sort(key=lambda item: item["name"])
    return teams, latest.get("timestamp"), latest.get("timestamp_unix", 0)


def generate_html(teams, snapshots, ts_str, ts_unix, weather=None):
    teams_json = json.dumps(teams, default=str)
    snapshots_json = json.dumps(snapshots, default=str)
    weather_json = json.dumps(weather, default=str) if weather else "null"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASC Dashboard</title>
<style>
* {{ box-sizing: border-box; }}
:root {{
  color-scheme: dark;
  --page: #0b0e13;
  --panel: #11151c;
  --panel-2: #171c24;
  --line: #242b36;
  --muted: #97a3b6;
  --text: #e7ebf0;
  --accent: #d3b77a;
}}
html, body {{
  margin: 0;
  background: var(--page);
  color: var(--text);
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
body {{ padding: 18px; }}
.page {{
  width: min(1440px, 100%);
  margin: 0 auto;
}}
.toolbar, .section {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 20px;
}}
.toolbar {{
  padding: 18px 20px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 14px;
}}
.eyebrow {{
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 0.72rem;
}}
.title {{
  margin-top: 6px;
  font-size: 1.65rem;
  font-weight: 650;
}}
.toolbar-right {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}}
.link-chip, .toggle button {{
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--text);
  border-radius: 999px;
  padding: 10px 14px;
  text-decoration: none;
  font: inherit;
}}
.toggle {{
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 999px;
}}
.toggle button {{
  border: none;
  background: transparent;
  color: var(--muted);
  padding: 8px 12px;
}}
.toggle button.active {{
  background: rgba(211, 183, 122, 0.15);
  color: var(--text);
}}
.stats {{
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}}
.stat-card {{
  padding: 16px;
  border-radius: 16px;
  background: var(--panel);
  border: 1px solid var(--line);
}}
.stat-label {{
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.72rem;
}}
.stat-value {{
  margin-top: 8px;
  font-size: 1.2rem;
  font-weight: 650;
}}
.section {{
  margin-top: 16px;
  padding: 18px;
}}
.section h2 {{
  margin: 0 0 6px;
  font-size: 1.1rem;
}}
.section p {{
  margin: 0 0 14px;
  color: var(--muted);
  line-height: 1.6;
}}
iframe {{
  width: 100%;
  height: 420px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #0e1218;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}}
th, td {{
  text-align: left;
  padding: 12px 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  vertical-align: top;
}}
th {{
  color: var(--muted);
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
tr:hover td {{ background: rgba(255,255,255,0.015); }}
.badge {{
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 600;
}}
.live {{ background: rgba(66, 173, 92, 0.16); color: #9ee5af; }}
.stale {{ background: rgba(211, 183, 122, 0.16); color: #e8d6a8; }}
.old {{ background: rgba(239, 140, 134, 0.14); color: #f2aaa5; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }}
.weather-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  color: var(--muted);
}}
.weather-row strong {{ color: var(--text); }}
@media (max-width: 820px) {{
  body {{ padding: 12px; }}
  iframe {{ height: 340px; }}
}}
</style>
</head>
<body>
<div class="page">
  <header class="toolbar">
    <div>
      <div class="eyebrow">ASC 2026 portable dashboard</div>
      <div class="title">Current field status</div>
    </div>
    <div class="toolbar-right">
      <a class="link-chip" href="asc_trajectory.html" target="_blank">Trajectory explorer</a>
      <a class="link-chip" href="asc_data_portfolio.html" target="_blank">Data portfolio</a>
      <div class="toggle" aria-label="Unit system">
        <button id="metric-toggle" class="active" type="button">Metric</button>
        <button id="imperial-toggle" type="button">Imperial</button>
      </div>
    </div>
  </header>

  <div class="stats">
    <article class="stat-card"><div class="stat-label">Teams in latest snapshot</div><div class="stat-value" id="team-count">--</div></article>
    <article class="stat-card"><div class="stat-label">Snapshot records</div><div class="stat-value">{len(snapshots)}</div></article>
    <article class="stat-card"><div class="stat-label">Last snapshot</div><div class="stat-value" id="last-snapshot">--</div></article>
    <article class="stat-card"><div class="stat-label">Status</div><div class="stat-value" id="snapshot-status">--</div></article>
  </div>

  <section class="section">
    <h2>Weather and route context</h2>
    <p>Same archive. Route view plus status table.</p>
    <div class="weather-row" id="weather-row"></div>
    <div style="margin-top:16px"><iframe src="asc_trajectory.html" title="ASC trajectory explorer"></iframe></div>
  </section>

  <section class="section">
    <h2>Latest team table</h2>
    <p>Local route math, not official ASC scoring.</p>
    <table>
      <thead>
        <tr>
          <th>Team</th>
          <th>Speed</th>
          <th>GPS path</th>
          <th>Geo finish distance</th>
          <th>Samples</th>
          <th>Heading</th>
          <th>Altitude</th>
          <th>Sat</th>
          <th>Position</th>
          <th>Snapshot / GPS</th>
        </tr>
      </thead>
      <tbody id="table-body"></tbody>
    </table>
  </section>
</div>

<script>
const TEAMS = {teams_json};
const SNAPSHOTS = {snapshots_json};
const WEATHER = {weather_json};
const SNAPSHOT_INTERVAL = {SNAPSHOT_INTERVAL};
let unitSystem = "metric";

function kmToMi(v) {{ return v == null ? null : v / 1.609344; }}
function kphToMph(v) {{ return v == null ? null : v / 1.609344; }}
function metersToFeet(v) {{ return v == null ? null : v * 3.28084; }}
function cToF(v) {{ return v == null ? null : (v * 9 / 5) + 32; }}

function fmt(value, digits = 1) {{
  return Number(value).toFixed(digits);
}}

function formatDate(ts) {{
  if (!ts) return "N/A";
  return new Date(ts * 1000).toLocaleString([], {{
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
  }});
}}

function formatIso(value) {{
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {{
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
  }});
}}

function formatDistanceKm(km) {{
  if (km == null) return "N/A";
  return unitSystem === "metric" ? `${{fmt(km)}} km` : `${{fmt(kmToMi(km))}} mi`;
}}

function formatSpeedKph(kph) {{
  if (kph == null) return "N/A";
  return unitSystem === "metric" ? `${{fmt(kph)}} km/h` : `${{fmt(kphToMph(kph))}} mph`;
}}

function formatAltitude(meters) {{
  if (meters == null) return "N/A";
  return unitSystem === "metric" ? `${{fmt(meters, 0)}} m` : `${{fmt(metersToFeet(meters), 0)}} ft`;
}}

function formatTemp(celsius) {{
  if (celsius == null) return "N/A";
  return unitSystem === "metric" ? `${{fmt(celsius)}} C` : `${{fmt(cToF(celsius))}} F`;
}}

function applyUnitToggle() {{
  document.getElementById("metric-toggle").classList.toggle("active", unitSystem === "metric");
  document.getElementById("imperial-toggle").classList.toggle("active", unitSystem === "imperial");
  buildWeather();
  buildTable();
}}

document.getElementById("metric-toggle").addEventListener("click", () => {{
  unitSystem = "metric";
  applyUnitToggle();
}});
document.getElementById("imperial-toggle").addEventListener("click", () => {{
  unitSystem = "imperial";
  applyUnitToggle();
}});

function buildWeather() {{
  const root = document.getElementById("weather-row");
  if (!WEATHER) {{
    root.innerHTML = "<span>No archived weather sample found.</span>";
    return;
  }}
  root.innerHTML = `
    <span><strong>Condition</strong> ${{WEATHER.weather_desc || "N/A"}}</span>
    <span><strong>Temperature</strong> ${{formatTemp(WEATHER.temp_c)}}</span>
    <span><strong>Feels like</strong> ${{formatTemp(WEATHER.feels_like_c)}}</span>
    <span><strong>Wind</strong> ${{formatSpeedKph(WEATHER.wind_kph)}}</span>
    <span><strong>Humidity</strong> ${{WEATHER.humidity_pct != null ? WEATHER.humidity_pct + "%" : "N/A"}}</span>
  `;
}}

function freshnessBadge(team) {{
  const age = team.sample_age_sec;
  if (age == null) return '<span class="badge stale">Unknown</span>';
  if (age >= 1800) return '<span class="badge old">Old</span>';
  if (age >= 300) return '<span class="badge stale">Stale</span>';
  return '<span class="badge live">Live</span>';
}}

function effectiveSpeed(team) {{
  const age = team.sample_age_sec;
  if ((age != null && age >= 300) || (team.speed != null && team.speed < 1.609)) {{
    return 0;
  }}
  return team.speed;
}}

function buildTable() {{
  const body = document.getElementById("table-body");
  body.innerHTML = "";
  TEAMS.forEach(team => {{
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${{team.name}}</td>
      <td>${{formatSpeedKph(effectiveSpeed(team))}}</td>
      <td>${{formatDistanceKm(team.distance_km)}}</td>
      <td>${{formatDistanceKm(team.to_finish_km)}}</td>
      <td>${{team.points}}</td>
      <td>${{team.heading != null ? team.heading + "°" : "N/A"}}</td>
      <td>${{formatAltitude(team.alt)}}</td>
      <td>${{team.satellites != null ? team.satellites : "N/A"}}</td>
      <td class="mono">${{team.lat != null && team.lon != null ? team.lat.toFixed(4) + ", " + team.lon.toFixed(4) : "N/A"}}</td>
      <td>
        <div>${{formatIso(team.captured_time)}}</div>
        <div style="color:#97a3b6;font-size:.8rem">GPS ${{formatIso(team.sample_time)}}</div>
        <div style="margin-top:6px">${{freshnessBadge(team)}}</div>
      </td>
    `;
    body.appendChild(row);
  }});
  document.getElementById("team-count").textContent = String(TEAMS.length);
}}

function updateSnapshotStats() {{
  const now = Date.now() / 1000;
  const past = SNAPSHOTS.filter(item => item.ts_unix <= now);
  const last = past.length ? past[past.length - 1] : null;
  document.getElementById("last-snapshot").textContent = last ? formatDate(last.ts_unix) : "No data";
  if (!last) {{
    document.getElementById("snapshot-status").textContent = "Offline";
    return;
  }}
  const age = now - last.ts_unix;
  document.getElementById("snapshot-status").textContent = age < SNAPSHOT_INTERVAL * 2 ? "Collecting" : age < SNAPSHOT_INTERVAL * 5 ? "Delayed" : "Stale";
}}

buildWeather();
buildTable();
updateSnapshotStats();
applyUnitToggle();
</script>
</body>
</html>"""


def generate_trajectory_html(data_dir):
    teams = visualize.load_updates(data_dir)
    snaps = visualize.load_snapshots(data_dir)
    pins = visualize.load_pins(data_dir)
    return visualize.generate_html(teams, snaps, pins=pins)


def serve_live(data_dir, port=8900):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                teams, ts_str, ts_unix = load_latest(data_dir)
                snaps = load_snapshots(data_dir)
                weather = load_latest_weather(data_dir)
                self.wfile.write(generate_html(teams, snaps, ts_str, ts_unix, weather).encode("utf-8"))
            elif self.path == "/asc_trajectory.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(generate_trajectory_html(data_dir).encode("utf-8"))
            else:
                super().do_GET()

        def log_message(self, fmt, *args):
            pass

    class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True

    with ThreadedHTTPServer(("", port), Handler) as httpd:
        print(f"Dashboard live at http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")


def main():
    parser = argparse.ArgumentParser(description="ASC data dashboard")
    parser.add_argument("--dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--open", "-p", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args()

    if args.live:
        serve_live(args.dir, args.port)
        return

    teams, ts_str, ts_unix = load_latest(args.dir)
    snaps = load_snapshots(args.dir)
    weather = load_latest_weather(args.dir)
    html = generate_html(teams, snaps, ts_str, ts_unix, weather)

    output_path = Path(args.output)
    output_path.write_text(html, encoding="utf-8")
    print(f"Generated {output_path}")
    print(f"  {len(teams)} teams, {len(snaps)} snapshots")

    if args.open:
        webbrowser.open(output_path.resolve().as_uri())


if __name__ == "__main__":
    main()
