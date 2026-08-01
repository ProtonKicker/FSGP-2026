#!/usr/bin/env python3
"""
Simple ASC data dashboard — timer + team list. No map.
"""
import argparse
import json
import math
import socketserver
import webbrowser
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from collections import defaultdict

import visualize

DEFAULT_DATA_DIR = "data"
DEFAULT_OUTPUT = "asc_dashboard.html"
SNAPSHOT_INTERVAL = 30
FINISH_LAT = 35.222
FINISH_LON = -101.831

WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9A6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
]


def color_for(name):
    import hashlib
    h = hashlib.md5(name.encode()).hexdigest()
    return COLORS[int(h[:8], 16) % len(COLORS)]


def load_snapshots(data_dir):
    path = Path(data_dir)
    snaps = []
    for f in sorted(path.glob("traces_*.json")):
        with open(f) as fh:
            d = json.load(fh)
            snaps.append({
                "ts": d.get("timestamp", ""),
                "ts_unix": d.get("timestamp_unix", 0),
                "count": d.get("tracker_count", 0),
            })
    return snaps


def load_latest_weather(data_dir):
    """Load the most recent weather observation."""
    path = Path(data_dir)
    latest = None
    for f in sorted(path.glob("weather_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                latest = json.loads(line)
    return latest


def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km between two lat/lon points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def clean_path_points(points):
    """Drop duplicate samples and obvious GPS jitter before distance math."""
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


def load_distances(data_dir):
    """Calculate cumulative distance traveled per serial from update log."""
    path = Path(data_dir)
    entries = defaultdict(list)
    for f in sorted(path.glob("updates_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if e.get("lat") is not None and e.get("lon") is not None:
                    entries[e.get("serial", "?")].append(e)

    distances = {}
    for serial, pts in entries.items():
        pts.sort(key=lambda p: p.get("sample_unix", 0) or 0)
        pts = clean_path_points(pts)
        total = 0.0
        for i in range(1, len(pts)):
            p1, p2 = pts[i-1], pts[i]
            total += haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        distances[serial] = total
    return distances


def load_point_counts(data_dir):
    """Count update log entries per serial."""
    path = Path(data_dir)
    counts = {}
    for f in sorted(path.glob("updates_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                s = e.get("serial", "?")
                counts[s] = counts.get(s, 0) + 1
    return counts


def load_latest(data_dir):
    """Load the latest snapshot and return team list + timestamp."""
    path = Path(data_dir)
    snaps = sorted(path.glob("traces_*.json"))
    if not snaps:
        return [], None, 0

    latest = snaps[-1]
    with open(latest) as fh:
        d = json.load(fh)

    point_counts = load_point_counts(data_dir)
    distances = load_distances(data_dir)
    teams = []
    for t in d.get("trackers", []):
        serial = t.get("serial", "")
        lat = t.get("latitude")
        lon = t.get("longitude")
        dist_to_finish = round(haversine_km(lat, lon, FINISH_LAT, FINISH_LON), 1) if lat and lon else None
        captured_time = t.get("captured_time") or d.get("timestamp", "")
        captured_unix = t.get("captured_unix", d.get("timestamp_unix", 0))
        teams.append({
            "name": t.get("name", "?"),
            "serial": serial,
            "lat": lat,
            "lon": lon,
            "speed": t.get("speed_kph"),
            "heading": t.get("course_deg"),
            "alt": t.get("altitude_m"),
            "satellites": t.get("satellites"),
            "pdop": t.get("pdop"),
            "color": color_for(t.get("name", "?")),
            "sample_time": t.get("sample_time", ""),
            "sample_unix": t.get("sample_unix"),
            "captured_time": captured_time,
            "captured_unix": captured_unix,
            "sample_age_sec": t.get("sample_age_sec"),
            "points": point_counts.get(serial, 0),
            "distance_km": round(distances.get(serial, 0), 1),
            "to_finish_km": dist_to_finish,
        })

    teams.sort(key=lambda t: t["name"])
    return teams, d.get("timestamp"), d.get("timestamp_unix", 0)


def generate_html(teams, snapshots, ts_str, ts_unix, weather=None):
    teams_json = json.dumps(teams, default=str)
    snaps_json = json.dumps(snapshots)
    weather_json = json.dumps(weather, default=str) if weather else "null"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASC Dashboard</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 100%; height: 100vh; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
body {{ display: flex; flex-direction: column; background: #0d1117; color: #e6edf3; }}
#header {{ flex: 0 0 auto; display: flex; align-items: center; gap: 16px; padding: 10px 16px; background: #161b22; border-bottom: 1px solid #30363d; }}
#header h1 {{ font-size: 1rem; font-weight: 700; }}
.header-right {{ margin-left: auto; display: flex; align-items: center; gap: 16px; font-size: .82rem; }}
.timer-block {{ display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }}
.timer-label {{ font-size: .62rem; color: #8b949e; text-transform: uppercase; letter-spacing: .04em; }}
.timer-value {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
.timer-value.live {{ color: #3fb950; }}
.timer-value.stale {{ color: #d29922; }}
.timer-value.off {{ color: #f85149; }}
#content {{ flex: 1; overflow-y: auto; padding: 12px 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
th {{ text-align: left; padding: 6px 8px; border-bottom: 2px solid #30363d; color: #8b949e; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; position: sticky; top: 0; background: #0d1117; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #21262d; }}
tr:hover {{ background: rgba(255,255,255,.03); }}
.name-col {{ display: flex; align-items: center; gap: 6px; }}
.color-dot {{ width: 8px; height: 8px; border-radius: 50%; flex: none; }}
.speed-val {{ font-weight: 600; }}
.speed-0 {{ color: #8b949e; }}
.speed-slow {{ color: #58a6ff; }}
.speed-fast {{ color: #3fb950; }}
.badge {{ display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: .7rem; font-weight: 600; }}
.badge-green {{ background: rgba(63,185,80,.15); color: #3fb950; }}
.badge-yellow {{ background: rgba(210,153,34,.15); color: #d29922; }}
.badge-red {{ background: rgba(248,81,73,.15); color: #f85149; }}
#metric-note {{ margin: 12px 16px 0 16px; padding: 10px 12px; border: 1px solid #30363d; border-radius: 8px; background: rgba(210,153,34,.08); color: #c9d1d9; font-size: .78rem; }}
#weather-bar {{ flex: 0 0 auto; display: flex; align-items: center; gap: 20px; padding: 8px 16px; background: #161b22; border-bottom: 1px solid #30363d; font-size: .82rem; }}
.weather-item {{ display: flex; align-items: center; gap: 6px; }}
.weather-icon {{ font-size: 1.2rem; }}
.weather-label {{ color: #8b949e; font-size: .68rem; }}
.weather-value {{ font-weight: 600; }}
.weather-value.temp {{ color: #f0883e; }}
.weather-value.wind {{ color: #58a6ff; }}
.weather-value.cond {{ color: #3fb950; }}
#footer {{ flex: 0 0 auto; padding: 6px 16px; font-size: .7rem; color: #484f58; text-align: center; border-top: 1px solid #21262d; }}
</style>
</head>
<body>
<div id="header">
  <h1>☀ ASC Data Dashboard</h1>
  <span style="font-size:.72rem;color:#8b949e" id="team-count"></span>
  <div class="header-right">
    <div class="timer-block">
      <span class="timer-label">Last snapshot</span>
      <span class="timer-value" id="last-snapshot-timer">--</span>
    </div>
    <div class="timer-block">
      <span class="timer-label">Next snapshot</span>
      <span class="timer-value" id="next-snapshot-timer">--</span>
    </div>
    <div class="timer-block">
      <span class="timer-label">Snapshot status</span>
      <span class="timer-value" id="snapshot-status">--</span>
    </div>
  </div>
</div>
<div id="weather-bar"></div>
<div style="display: flex; gap: 16px; padding: 12px 16px 0 16px;">
  <a href="https://tracking.americansolarchallenge.org/" target="_blank" style="color: #58a6ff; text-decoration: none; font-size: 0.85rem; font-weight: 600;">↗ Official ASC Live Tracker</a>
  <a href="asc_trajectory.html" target="_blank" style="color: #58a6ff; text-decoration: none; font-size: 0.85rem; font-weight: 600;">↗ Fullscreen Map</a>
</div>
<div style="padding: 12px 16px 0 16px;">
  <iframe src="asc_trajectory.html" style="width: 100%; height: 400px; border: 1px solid #30363d; border-radius: 8px;"></iframe>
</div>
<div id="metric-note">Custom metrics note: `GPS Path` and `Geo Dist To Finish` are local estimates from collected coordinates. Official ASC route progress on the live tracker is computed differently and is not reproduced here.</div>
<div id="content">
  <table>
    <thead>
      <tr>
        <th>Team</th>
        <th title="Server: km/h">Speed</th>
        <th title="Derived from logged GPS path">GPS Path (mi)</th>
        <th title="Straight-line geographic distance">Geo Dist To Finish (mi)</th>
        <th>Logged Samples</th>
        <th>Heading</th>
        <th title="Server: m">Altitude (ft)</th>
        <th>Satellites</th>
        <th>Position</th>
        <th title="Collector snapshot time / tracker GPS sample time">Snapshot / GPS</th>
      </tr>
    </thead>
    <tbody id="team-table-body">
    </tbody>
  </table>
</div>
<div id="footer">ASC Data Collection — <span id="snap-count">{len(snapshots)}</span> snapshots · <span id="team-count-footer">{len(teams)}</span> teams</div>
<script>
const TEAMS = {teams_json};
const SNAPSHOTS = {snaps_json};
const SNAPSHOT_INTERVAL = {SNAPSHOT_INTERVAL};
const WEATHER = {weather_json};

function formatDate(ts) {{
    if (ts == null || ts === "" || Number.isNaN(ts)) return "N/A";
    const d = new Date(ts * 1000);
    return d.toLocaleString([], {{
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
    }});
}}

function formatIsoLocal(value) {{
    if (!value) return "N/A";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString([], {{
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
    }});
}}

function mph(v) {{ return v != null ? (v / 1.609).toFixed(1) : null; }}
function ft(v) {{ return v != null ? (v * 3.281).toFixed(0) : null; }}
function mi(v) {{ return v != null ? (v / 1.609).toFixed(1) : null; }}

function speedBadge(speed_kph) {{
    if (speed_kph == null) return '<span class="badge badge-red">N/A</span>';
    if (speed_kph === 0) return '<span class="badge badge-green">0</span>';
    const s = mph(speed_kph);
    if (speed_kph < 30) return '<span class="badge badge-yellow">' + s + ' <span style="font-size:.6rem;opacity:.6">(' + speed_kph.toFixed(0) + ')</span></span>';
    return '<span class="badge badge-green">' + s + ' <span style="font-size:.6rem;opacity:.6">(' + speed_kph.toFixed(0) + ')</span></span>';
}}

function headingStr(h) {{
    if (h == null) return "N/A";
    return h + "°";
}}

function windDir(deg) {{
    if (deg == null) return "";
    const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
    return dirs[Math.round(deg / 22.5) % 16];
}}

function weatherIcon(code) {{
    if (code == null) return "❓";
    if (code === 0) return "☀";
    if (code <= 2) return "⛅";
    if (code === 3) return "☁";
    if (code <= 48) return "🌫";
    if (code <= 57) return "🌦";
    if (code <= 67) return "🌧";
    if (code <= 77) return "🌨";
    if (code <= 86) return "🌦";
    return "⛈";
}}

function buildWeather() {{
    const bar = document.getElementById("weather-bar");
    if (!WEATHER) {{
        bar.innerHTML = '<span style="color:#8b949e;font-size:.78rem">Weather: no data</span>';
        return;
    }}
    const w = WEATHER;
    const f = w.temp_c != null ? (w.temp_c * 9/5 + 32).toFixed(0) : null;
    const feelsF = w.feels_like_c != null ? (w.feels_like_c * 9/5 + 32).toFixed(0) : null;
    const temp = f != null ? f + '°F <span style="opacity:.5;font-size:.7rem">(' + w.temp_c.toFixed(1) + '°C)</span>' : "N/A";
    const feels = feelsF != null ? " (feels " + feelsF + "°F)" : "";
    const mphVal = w.wind_kph != null ? (w.wind_kph / 1.609).toFixed(0) : null;
    const gustMph = w.wind_gust_kph != null ? (w.wind_gust_kph / 1.609).toFixed(0) : null;
    const wind = mphVal != null ? mphVal + ' mph <span style="opacity:.5;font-size:.7rem">(' + w.wind_kph.toFixed(0) + ' km/h)</span>' : "N/A";
    const gust = gustMph != null ? " (gusts " + gustMph + " mph)" : "";
    const dir = w.wind_dir_deg != null ? windDir(w.wind_dir_deg) : "";
    const dirDeg = w.wind_dir_deg != null ? w.wind_dir_deg + "°" : "";
    const cond = w.weather_desc || "N/A";
    const icon = weatherIcon(w.weather_code);
    const hum = w.humidity_pct != null ? "Humidity " + w.humidity_pct + "%" : "";
    const precip = w.precip_mm != null && w.precip_mm > 0 ? "Precip " + w.precip_mm.toFixed(1) + " mm" : "";
    bar.innerHTML =
        '<span class="weather-item"><span class="weather-icon">' + icon + '</span><span class="weather-value cond">' + cond + '</span></span>' +
        '<span class="weather-item"><span class="weather-label">Temp</span><span class="weather-value temp">' + temp + '</span>' + feels + '</span>' +
        '<span class="weather-item"><span class="weather-label">Wind</span><span class="weather-value wind">' + wind + '</span> ' + dir + ' ' + dirDeg + gust + '</span>' +
        (hum ? '<span class="weather-item"><span class="weather-label">' + hum + '</span></span>' : "") +
        (precip ? '<span class="weather-item"><span class="weather-label">' + precip + '</span></span>' : "");
}}

function buildTable() {{
    const tbody = document.getElementById("team-table-body");
    const now = Date.now() / 1000;
    TEAMS.forEach(t => {{
        let speedKph = t.speed;
        const altFt = ft(t.alt);
        const altM = t.alt != null ? t.alt.toFixed(0) : null;
        const distMi = mi(t.distance_km);
        const toFinishMi = t.to_finish_km != null ? mi(t.to_finish_km) : null;
        const sat = t.satellites != null ? t.satellites : "N/A";
        const pos = (t.lat != null && t.lon != null)
            ? t.lat.toFixed(4) + ", " + t.lon.toFixed(4)
            : "N/A";
        const captured = t.captured_time ? formatIsoLocal(t.captured_time) : formatDate(t.captured_unix);
        const gps = t.sample_time ? formatIsoLocal(t.sample_time) : formatDate(t.sample_unix);
        const sampleAgeSec = t.sample_age_sec != null
            ? t.sample_age_sec
            : (t.captured_unix && t.sample_unix ? Math.max(0, t.captured_unix - t.sample_unix) : null);
        
        // If the tracker GPS sample is stale (e.g. over 5 minutes old), it's not currently moving.
        // The official tracker zeroes out speed for offline/stale cars, so we match that behavior.
        // We also zero out very low speeds (< 1 mph / 1.6 kph) to eliminate GPS jitter when stopped.
        if ((sampleAgeSec != null && sampleAgeSec >= 300) || speedKph < 1.609) {{
            speedKph = 0;
        }}
        const speedMph = mph(speedKph);

        let freshness = "Live";
        let freshnessClass = "badge-green";
        if (sampleAgeSec != null && sampleAgeSec >= 1800) {{
            freshness = "Old";
            freshnessClass = "badge-red";
        }} else if (sampleAgeSec != null && sampleAgeSec >= 300) {{
            freshness = "Stale";
            freshnessClass = "badge-yellow";
        }}
        const row = document.createElement("tr");
        row.innerHTML = `
            <td><div class="name-col"><div class="color-dot" style="background:${{t.color}}"></div>${{t.name}}</div></td>
            <td class="speed-val">${{speedBadge(speedKph)}}</td>
            <td style="font-variant-numeric:tabular-nums;text-align:right" title="Raw: ${{t.distance_km}} km">${{distMi}}</td>
            <td style="font-variant-numeric:tabular-nums;text-align:right" title="Raw: ${{t.to_finish_km}} km">${{toFinishMi != null ? toFinishMi : "N/A"}}</td>
            <td style="text-align:center;font-variant-numeric:tabular-nums">${{t.points}}</td>
            <td>${{headingStr(t.heading)}}</td>
            <td title="Raw: ${{altM}} m">${{altFt != null ? altFt + "&prime;" : "N/A"}}</td>
            <td>${{sat}}</td>
            <td style="font-family:monospace;font-size:.75rem">${{pos}}</td>
            <td style="font-size:.75rem" title="Collector captured: ${{t.captured_time || "N/A"}}&#10;Tracker GPS sample: ${{t.sample_time || "N/A"}}">
                <div>${{captured}}</div>
                <div style="color:#8b949e;font-size:.68rem">GPS ${{gps}}</div>
                <div><span class="badge ${{freshnessClass}}">${{freshness}}</span></div>
            </td>
        `;
        tbody.appendChild(row);
    }});
    document.getElementById("team-count").textContent = TEAMS.length + " teams";
    document.getElementById("team-count-footer").textContent = TEAMS.length;
}}

function updateTimer() {{
    const now = Date.now() / 1000;
    const past = SNAPSHOTS.filter(s => s.ts_unix <= now);
    const lastSnap = past.length ? past[past.length-1] : null;
    const future = SNAPSHOTS.filter(s => s.ts_unix > now);
    const nextKnown = future.length ? future[0] : null;

    let lastEl = document.getElementById("last-snapshot-timer");
    let nextEl = document.getElementById("next-snapshot-timer");
    let statusEl = document.getElementById("snapshot-status");

    if (lastSnap) {{
        const secs = Math.floor(now - lastSnap.ts_unix);
        lastEl.textContent = secs < 60 ? secs + "s ago" : formatDate(lastSnap.ts_unix);
        lastEl.className = "timer-value" + (secs < 90 ? " live" : secs < 300 ? " stale" : " off");
    }} else {{
        lastEl.textContent = "No data";
        lastEl.className = "timer-value off";
    }}

    if (nextKnown) {{
        const secs = Math.floor(nextKnown.ts_unix - now);
        nextEl.textContent = secs > 0 ? secs + "s" : "Now";
        nextEl.className = "timer-value" + (secs > 0 ? " live" : " live");
        statusEl.textContent = secs > 0 ? "Waiting" : "Due";
        statusEl.className = "timer-value" + (secs > 0 ? " live" : " stale");
    }} else if (lastSnap) {{
        const elapsed = now - lastSnap.ts_unix;
        const nextEst = SNAPSHOT_INTERVAL - (elapsed % SNAPSHOT_INTERVAL);
        nextEl.textContent = "~" + Math.round(nextEst) + "s";
        nextEl.className = "timer-value" + (elapsed < SNAPSHOT_INTERVAL * 2 ? " live" : elapsed < SNAPSHOT_INTERVAL * 5 ? " stale" : " off");
        if (elapsed < SNAPSHOT_INTERVAL * 2) {{
            statusEl.textContent = "Collecting";
            statusEl.className = "timer-value live";
        }} else if (elapsed < SNAPSHOT_INTERVAL * 5) {{
            statusEl.textContent = "Delayed";
            statusEl.className = "timer-value stale";
        }} else {{
            statusEl.textContent = "Stale";
            statusEl.className = "timer-value off";
        }}
    }} else {{
        nextEl.textContent = "--";
        nextEl.className = "timer-value off";
        statusEl.textContent = "Offline";
        statusEl.className = "timer-value off";
    }}
}}

function autoRefreshData() {{
    fetch('/data.json')
        .then(res => {{
            if (!res.ok) throw new Error('Network response was not ok');
            return res.json();
        }})
        .then(data => {{
            if (!data.teams || !data.snapshots) return;
            // Update global state
            TEAMS.length = 0;
            TEAMS.push(...data.teams);
            SNAPSHOTS.length = 0;
            SNAPSHOTS.push(...data.snapshots);
            Object.assign(WEATHER || {{}}, data.weather || {{}});
            
            // Re-render UI
            buildWeather();
            buildTable();
        }})
        .catch(err => console.error("Auto-refresh failed:", err));
}}

buildWeather();
buildTable();
updateTimer();
setInterval(updateTimer, 1000);
// Check for new data every SNAPSHOT_INTERVAL seconds
setInterval(autoRefreshData, SNAPSHOT_INTERVAL * 1000);
</script>
</body>
</html>'''
    return html


def generate_trajectory_html(data_dir):
    teams = visualize.load_updates(data_dir)
    snaps = visualize.load_snapshots(data_dir)
    pins = visualize.load_pins(data_dir)
    return visualize.generate_html(teams, snaps, pins=pins)


def serve_live(data_dir, port=8900):
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                teams, ts_str, ts_unix = load_latest(data_dir)
                snaps = load_snapshots(data_dir)
                weather = load_latest_weather(data_dir)
                html = generate_html(teams, snaps, ts_str, ts_unix, weather)
                self.wfile.write(html.encode("utf-8"))
            elif self.path == "/data.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                teams, ts_str, ts_unix = load_latest(data_dir)
                snaps = load_snapshots(data_dir)
                weather = load_latest_weather(data_dir)
                data = {
                    "teams": teams,
                    "snapshots": snaps,
                    "weather": weather,
                    "ts_str": ts_str,
                    "ts_unix": ts_unix
                }
                self.wfile.write(json.dumps(data, default=str).encode("utf-8"))
            elif self.path == "/asc_trajectory.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = generate_trajectory_html(data_dir)
                self.wfile.write(html.encode("utf-8"))
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
    parser = argparse.ArgumentParser(description="ASC Data Dashboard")
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
    output_path.write_text(html)
    print(f"Generated {output_path}")
    print(f"  {len(teams)} teams, {len(snaps)} snapshots")

    if args.open:
        webbrowser.open(output_path.resolve().as_uri())


if __name__ == "__main__":
    main()
