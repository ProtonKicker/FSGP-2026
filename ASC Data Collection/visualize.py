#!/usr/bin/env python3
"""
ASC Trajectory Visualization

Generates an interactive HTML archive explorer for American Solar Challenge
tracking data with:
- animated route replay
- team route highlighting
- route-quality anomaly overlays
- checkpoint/hazard pins
- archive-style summary analytics
"""

import argparse
import hashlib
import json
import math
import time as std_time
import webbrowser
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from http.server import SimpleHTTPRequestHandler

DEFAULT_DATA_DIR = "data"
DEFAULT_OUTPUT = "asc_trajectory.html"
SNAPSHOT_INTERVAL = 30
MOVING_SPEED_KPH = 3.0
STOP_GAP_SEC = 180
DATA_GAP_SEC = 600
ANOMALY_SEGMENT_SPEED_KPH = 130.0

COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9A6324", "#fffac8", "#800000",
    "#aaffc3", "#808000", "#ffd8b1", "#000075", "#a9a9a9",
]


def color_for(name):
    h = hashlib.md5(name.encode()).hexdigest()
    return COLORS[int(h[:8], 16) % len(COLORS)]


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def clean_path_points(points):
    """Drop duplicate samples and very small parked-car GPS jitter."""
    cleaned = []
    for point in points:
        lat = point.get("lat")
        lng = point.get("lng")
        if lat is None or lng is None:
            continue

        if not cleaned:
            cleaned.append(point)
            continue

        prev = cleaned[-1]
        if (
            point.get("sample_unix") == prev.get("sample_unix")
            and lat == prev.get("lat")
            and lng == prev.get("lng")
        ):
            continue

        jump_km = haversine_km(prev["lat"], prev["lng"], lat, lng)
        prev_speed = prev.get("speed") or 0
        curr_speed = point.get("speed") or 0
        if jump_km < 0.03 and prev_speed <= 1 and curr_speed <= 1:
            continue

        cleaned.append(point)
    return cleaned


def compute_team_metrics(points):
    if not points:
        return {
            "sample_count": 0,
            "elapsed_sec": 0,
            "moving_time_sec": 0,
            "idle_time_sec": 0,
            "distance_km": 0.0,
            "straight_distance_km": 0.0,
            "route_efficiency_pct": None,
            "average_speed_kph": None,
            "moving_average_speed_kph": None,
            "max_speed_kph": None,
            "stop_count": 0,
            "gap_count": 0,
            "largest_gap_sec": 0,
            "average_sample_gap_sec": None,
            "active_ratio_pct": None,
            "anomaly_count": 0,
            "anomalies": [],
            "start_time": "",
            "end_time": "",
            "start_sample_unix": None,
            "end_sample_unix": None,
        }

    total_distance_km = 0.0
    moving_time_sec = 0.0
    stop_count = 0
    gap_count = 0
    largest_gap_sec = 0
    anomaly_segments = []
    max_speed_kph = 0.0
    in_stop = False

    for point in points:
        speed = point.get("speed")
        if speed is not None:
            max_speed_kph = max(max_speed_kph, float(speed))

    for idx in range(1, len(points)):
        prev = points[idx - 1]
        curr = points[idx]
        prev_ts = prev.get("sample_unix") or 0
        curr_ts = curr.get("sample_unix") or 0
        dt = max(0.0, float(curr_ts) - float(prev_ts))
        distance_km = haversine_km(prev["lat"], prev["lng"], curr["lat"], curr["lng"])
        total_distance_km += distance_km

        if dt > 0:
            largest_gap_sec = max(largest_gap_sec, dt)
            if dt >= DATA_GAP_SEC:
                gap_count += 1

            segment_speed_kph = distance_km / (dt / 3600.0) if dt else 0.0
            max_speed_kph = max(max_speed_kph, segment_speed_kph)
            prev_speed = prev.get("speed") or 0.0
            curr_speed = curr.get("speed") or 0.0
            moving = (
                prev_speed >= MOVING_SPEED_KPH
                or curr_speed >= MOVING_SPEED_KPH
                or distance_km >= 0.08
            )

            if moving:
                moving_time_sec += dt
                in_stop = False
            elif dt >= STOP_GAP_SEC and not in_stop:
                stop_count += 1
                in_stop = True

            if segment_speed_kph >= ANOMALY_SEGMENT_SPEED_KPH or (distance_km >= 4 and dt <= 300):
                anomaly_segments.append({
                    "from_lat": prev["lat"],
                    "from_lng": prev["lng"],
                    "to_lat": curr["lat"],
                    "to_lng": curr["lng"],
                    "distance_km": round(distance_km, 2),
                    "gap_sec": round(dt),
                    "segment_speed_kph": round(segment_speed_kph, 1),
                    "time": curr.get("t") or "",
                    "sample_unix": curr.get("sample_unix"),
                    "reason": (
                        f"Segment implies {segment_speed_kph:.1f} km/h"
                        if segment_speed_kph >= ANOMALY_SEGMENT_SPEED_KPH
                        else "Large coordinate jump over short time window"
                    ),
                })

    elapsed_sec = 0.0
    if len(points) >= 2:
        elapsed_sec = max(
            0.0,
            float(points[-1].get("sample_unix") or 0) - float(points[0].get("sample_unix") or 0),
        )

    straight_distance_km = 0.0
    if len(points) >= 2:
        straight_distance_km = haversine_km(
            points[0]["lat"],
            points[0]["lng"],
            points[-1]["lat"],
            points[-1]["lng"],
        )

    idle_time_sec = max(0.0, elapsed_sec - moving_time_sec)
    route_efficiency_pct = None
    if total_distance_km > 0:
        route_efficiency_pct = round((straight_distance_km / total_distance_km) * 100.0, 1)

    average_speed_kph = None
    if elapsed_sec > 0:
        average_speed_kph = round(total_distance_km / (elapsed_sec / 3600.0), 1)

    moving_average_speed_kph = None
    if moving_time_sec > 0:
        moving_average_speed_kph = round(total_distance_km / (moving_time_sec / 3600.0), 1)

    average_sample_gap_sec = None
    if len(points) >= 2 and elapsed_sec > 0:
        average_sample_gap_sec = round(elapsed_sec / (len(points) - 1), 1)

    active_ratio_pct = None
    if elapsed_sec > 0:
        active_ratio_pct = round((moving_time_sec / elapsed_sec) * 100.0, 1)

    return {
        "sample_count": len(points),
        "elapsed_sec": round(elapsed_sec),
        "moving_time_sec": round(moving_time_sec),
        "idle_time_sec": round(idle_time_sec),
        "distance_km": round(total_distance_km, 1),
        "straight_distance_km": round(straight_distance_km, 1),
        "route_efficiency_pct": route_efficiency_pct,
        "average_speed_kph": average_speed_kph,
        "moving_average_speed_kph": moving_average_speed_kph,
        "max_speed_kph": round(max_speed_kph, 1) if max_speed_kph else None,
        "stop_count": stop_count,
        "gap_count": gap_count,
        "largest_gap_sec": round(largest_gap_sec),
        "average_sample_gap_sec": average_sample_gap_sec,
        "active_ratio_pct": active_ratio_pct,
        "anomaly_count": len(anomaly_segments),
        "anomalies": anomaly_segments,
        "start_time": points[0].get("t") or "",
        "end_time": points[-1].get("t") or "",
        "start_sample_unix": points[0].get("sample_unix"),
        "end_sample_unix": points[-1].get("sample_unix"),
    }


def load_updates(data_dir):
    path = Path(data_dir)
    raw = defaultdict(list)
    for f in sorted(path.glob("updates_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("lat") is None or entry.get("lon") is None:
                    continue
                raw[entry.get("serial", "?")].append(entry)

    teams = []
    for serial, entries in sorted(raw.items()):
        entries.sort(key=lambda e: e.get("sample_unix", 0) or 0)
        name = entries[0].get("name", serial) or serial
        points = [
            {
                "lat": entry["lat"],
                "lng": entry["lon"],
                "speed": entry.get("speed"),
                "heading": entry.get("heading"),
                "alt": entry.get("alt"),
                "satellites": entry.get("satellites"),
                "pdop": entry.get("pdop"),
                "sample_unix": entry.get("sample_unix"),
                "t": entry.get("_t", ""),
            }
            for entry in entries
        ]
        points = clean_path_points(points)
        metrics = compute_team_metrics(points)
        teams.append({
            "serial": serial,
            "name": name,
            "color": color_for(name),
            "points": points,
            "stats": metrics,
        })
    return teams


def load_snapshots(data_dir):
    path = Path(data_dir)
    snaps = []
    for f in sorted(path.glob("traces_*.json")):
        with open(f) as fh:
            data = json.load(fh)
            snaps.append({
                "ts": data.get("timestamp", ""),
                "ts_unix": data.get("timestamp_unix", 0),
                "count": data.get("tracker_count", 0),
            })
    return snaps


def load_pins(data_dir):
    path = Path(data_dir)
    deduped = {}
    for f in sorted(path.glob("pins_*.json")):
        with open(f) as fh:
            data = json.load(fh)
        for pin in data.get("pins", []):
            if pin.get("approved") is False:
                continue
            pin_id = pin.get("id") or f"{pin.get('latitude')}|{pin.get('longitude')}|{pin.get('message')}"
            deduped[pin_id] = {
                "id": pin_id,
                "lat": pin.get("latitude"),
                "lng": pin.get("longitude"),
                "color": pin.get("color") or "orange",
                "message": pin.get("message") or "Pin",
                "submitted_at": pin.get("submitted_at", ""),
                "approved_at": pin.get("approved_at", ""),
            }
    pins = list(deduped.values())
    pins.sort(key=lambda pin: pin.get("approved_at") or pin.get("submitted_at") or "")
    return pins


def generate_html(teams, snapshots, pins=None, team_filter=None, title=None):
    if team_filter:
        team_filter_lower = team_filter.lower()
        teams = [
            team for team in teams
            if team_filter_lower in team["name"].lower() or team_filter_lower in team["serial"].lower()
        ]

    pins = pins or []
    all_lats = [point["lat"] for team in teams for point in team["points"] if point["lat"] is not None]
    all_lngs = [point["lng"] for team in teams for point in team["points"] if point["lng"] is not None]

    default_center = [39.5, -98.35]
    default_zoom = 4
    if all_lats and all_lngs:
        default_center = [
            (min(all_lats) + max(all_lats)) / 2,
            (min(all_lngs) + max(all_lngs)) / 2,
        ]

    teams_json = json.dumps(teams, default=str)
    snaps_json = json.dumps(snapshots, default=str)
    pins_json = json.dumps(pins, default=str)
    tile_attribution = json.dumps(
        '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> '
        '&copy; <a href="https://carto.com/">CARTO</a>'
    )
    page_title = title or "ASC 2026 Archive Explorer"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; margin: 0; }}
body {{
    background: #0d1117;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
#app {{
    display: grid;
    grid-template-columns: 360px 1fr;
    width: 100%;
    height: 100vh;
}}
#sidebar {{
    background: #11161d;
    border-right: 1px solid #30363d;
    overflow-y: auto;
    padding: 18px 16px 22px;
}}
#sidebar h1 {{
    font-size: 1.08rem;
    margin: 0 0 6px;
}}
#subtitle {{
    color: #8b949e;
    font-size: 0.78rem;
    line-height: 1.4;
    margin-bottom: 18px;
}}
.section {{
    margin-bottom: 18px;
    padding: 14px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
}}
.section-title {{
    color: #8b949e;
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 10px;
}}
.control-grid {{
    display: grid;
    gap: 10px;
}}
label {{
    display: block;
    color: #c9d1d9;
    font-size: 0.78rem;
    margin-bottom: 4px;
}}
select, button, input[type="range"] {{
    width: 100%;
}}
select, button {{
    border: 1px solid #30363d;
    border-radius: 8px;
    background: #21262d;
    color: #e6edf3;
    padding: 8px 10px;
    font-size: 0.84rem;
}}
button {{
    cursor: pointer;
    font-weight: 600;
}}
button:hover {{
    background: #30363d;
}}
.button-row {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
}}
.summary-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
}}
.summary-card {{
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 10px;
}}
.summary-label {{
    color: #8b949e;
    font-size: 0.68rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 6px;
}}
.summary-value {{
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.2;
}}
.summary-note {{
    color: #8b949e;
    font-size: 0.72rem;
    margin-top: 4px;
}}
#team-roster {{
    display: grid;
    gap: 6px;
    max-height: 280px;
    overflow-y: auto;
}}
.team-chip {{
    display: grid;
    grid-template-columns: 12px 1fr auto;
    gap: 8px;
    align-items: center;
    padding: 8px 10px;
    border-radius: 9px;
    border: 1px solid #30363d;
    background: #0d1117;
    cursor: pointer;
}}
.team-chip.active {{
    border-color: #58a6ff;
    box-shadow: inset 0 0 0 1px #58a6ff;
}}
.team-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
}}
.team-meta {{
    color: #8b949e;
    font-size: 0.72rem;
}}
#anomaly-list {{
    display: grid;
    gap: 8px;
    max-height: 240px;
    overflow-y: auto;
}}
.anomaly-item {{
    padding: 10px;
    border: 1px solid rgba(248, 81, 73, 0.35);
    background: rgba(248, 81, 73, 0.08);
    border-radius: 10px;
}}
.anomaly-title {{
    color: #ffb3ad;
    font-size: 0.78rem;
    font-weight: 700;
    margin-bottom: 4px;
}}
.anomaly-meta {{
    color: #c9d1d9;
    font-size: 0.75rem;
    line-height: 1.4;
}}
#map-wrap {{
    display: grid;
    grid-template-rows: auto 1fr auto;
    min-width: 0;
}}
#topbar {{
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 16px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
}}
.topbar-block {{
    display: flex;
    flex-direction: column;
    gap: 2px;
}}
.topbar-label {{
    color: #8b949e;
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}}
.topbar-value {{
    font-size: 0.86rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}}
#map {{
    width: 100%;
    height: 100%;
}}
#bottom-bar {{
    display: grid;
    grid-template-columns: auto auto 1fr auto auto;
    gap: 10px;
    align-items: center;
    padding: 12px 16px;
    background: #161b22;
    border-top: 1px solid #30363d;
}}
#time-slider {{
    accent-color: #58a6ff;
}}
#status-pill {{
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(88, 166, 255, 0.12);
    color: #8ec7ff;
    font-size: 0.75rem;
    font-weight: 600;
}}
.tracker-marker {{
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid rgba(255,255,255,0.85);
    box-shadow: 0 0 0 1px rgba(0,0,0,0.5), 0 1px 4px rgba(0,0,0,0.35);
}}
.tracker-marker.focused {{
    width: 22px;
    height: 22px;
    border-width: 3px;
    box-shadow: 0 0 0 2px rgba(255,255,255,0.65), 0 0 16px rgba(88,166,255,0.45);
}}
.pin-badge {{
    padding: 2px 6px;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    color: white;
}}
.muted {{
    color: #8b949e;
}}
@media (max-width: 1200px) {{
    #app {{
        grid-template-columns: 320px 1fr;
    }}
}}
@media (max-width: 980px) {{
    #app {{
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr;
    }}
    #sidebar {{
        max-height: 42vh;
        border-right: none;
        border-bottom: 1px solid #30363d;
    }}
}}
</style>
</head>
<body>
<div id="app">
    <aside id="sidebar">
        <h1>ASC 2026 Archive Explorer</h1>
        <div id="subtitle">
            Replay every route, spotlight a single team, and surface suspicious route
            segments that may need manual checking.
        </div>

        <div class="section">
            <div class="section-title">Replay Controls</div>
            <div class="control-grid">
                <div>
                    <label for="focus-select">Focus Team</label>
                    <select id="focus-select"></select>
                </div>
                <div>
                    <label for="speed-select">Replay Speed</label>
                    <select id="speed-select">
                        <option value="0.5">0.5x</option>
                        <option value="1" selected>1x</option>
                        <option value="2">2x</option>
                        <option value="5">5x</option>
                        <option value="10">10x</option>
                        <option value="25">25x</option>
                    </select>
                </div>
                <div class="button-row">
                    <button id="play-btn">Play</button>
                    <button id="reset-btn">Reset</button>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Tour Summary</div>
            <div class="summary-grid" id="summary-grid"></div>
        </div>

        <div class="section">
            <div class="section-title">Team Roster</div>
            <div id="team-roster"></div>
        </div>

        <div class="section">
            <div class="section-title">Route Alerts</div>
            <div id="anomaly-list"></div>
        </div>
    </aside>

    <main id="map-wrap">
        <div id="topbar">
            <div class="topbar-block">
                <span class="topbar-label">Loaded Teams</span>
                <span class="topbar-value" id="loaded-teams">--</span>
            </div>
            <div class="topbar-block">
                <span class="topbar-label">Replay Time</span>
                <span class="topbar-value" id="anim-time-display">--</span>
            </div>
            <div class="topbar-block">
                <span class="topbar-label">Coverage Window</span>
                <span class="topbar-value" id="coverage-window">--</span>
            </div>
            <div class="topbar-block">
                <span class="topbar-label">Pins</span>
                <span class="topbar-value" id="pin-count">--</span>
            </div>
            <div class="topbar-block">
                <span class="topbar-label">Total Samples</span>
                <span class="topbar-value" id="sample-count">--</span>
            </div>
        </div>
        <div id="map"></div>
        <div id="bottom-bar">
            <button id="fit-all-btn">Fit All</button>
            <button id="fit-focus-btn">Fit Focus</button>
            <input type="range" id="time-slider" min="0" max="100" value="100" step="0.1">
            <span class="topbar-value" id="slider-time-label">--</span>
            <span id="status-pill">Archive replay ready</span>
        </div>
    </main>
</div>

<script>
const TRAJECTORIES = {teams_json};
const SNAPSHOTS = {snaps_json};
const PINS = {pins_json};
const SNAPSHOT_INTERVAL = {SNAPSHOT_INTERVAL};

const map = L.map("map", {{
    zoomControl: true,
    attributionControl: false,
}});

L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
    maxZoom: 19,
    attribution: {tile_attribution},
}}).addTo(map);

const teamLayers = new Map();
const routeLayer = L.layerGroup().addTo(map);
const markerLayer = L.layerGroup().addTo(map);
const pinLayer = L.layerGroup().addTo(map);
const anomalyLayer = L.layerGroup().addTo(map);

const allTimes = [];
TRAJECTORIES.forEach(team => {{
    team.points.forEach(point => {{
        const ts = Number(point.sample_unix || 0);
        point.sample_unix = ts;
        allTimes.push(ts);
    }});
}});

const MIN_TIME = allTimes.length ? Math.min(...allTimes) : 0;
const MAX_TIME = allTimes.length ? Math.max(...allTimes) : 0;
const TIME_SPAN = Math.max(1, MAX_TIME - MIN_TIME);
let currentTime = MAX_TIME;
let isPlaying = false;
let playbackSpeed = 1;
let lastFrameTime = 0;
let animationFrameId = null;
let focusedTeamKey = null;

function kmToMi(value) {{
    if (value == null || Number.isNaN(value)) return "N/A";
    return (value / 1.609344).toFixed(1) + " mi";
}}

function secondsToDuration(seconds) {{
    if (seconds == null) return "N/A";
    const total = Math.max(0, Number(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours === 0) return `${{minutes}} min`;
    return `${{hours}}h ${{minutes}}m`;
}}

function formatTime(ts) {{
    if (!ts) return "No data";
    return new Date(ts * 1000).toLocaleString([], {{
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }});
}}

function formatShortTime(ts) {{
    if (!ts) return "No data";
    return new Date(ts * 1000).toLocaleTimeString([], {{
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }});
}}

function getTeamKey(team) {{
    return team.serial || team.name;
}}

function getPositionAtTime(team, time) {{
    const pts = team.points;
    if (!pts.length) return null;
    if (time <= pts[0].sample_unix) return pts[0];
    if (time >= pts[pts.length - 1].sample_unix) return pts[pts.length - 1];

    let lo = 0;
    let hi = pts.length - 1;
    while (lo < hi - 1) {{
        const mid = (lo + hi) >> 1;
        if (pts[mid].sample_unix <= time) lo = mid;
        else hi = mid;
    }}

    const p1 = pts[lo];
    const p2 = pts[hi];
    const t1 = p1.sample_unix;
    const t2 = p2.sample_unix;
    if (t1 >= t2) return p1;

    const fraction = (time - t1) / (t2 - t1);
    return {{
        lat: p1.lat + (p2.lat - p1.lat) * fraction,
        lng: p1.lng + (p2.lng - p1.lng) * fraction,
        speed: p2.speed != null ? p2.speed : p1.speed,
        heading: p2.heading != null ? p2.heading : p1.heading,
        alt: p2.alt != null ? p2.alt : p1.alt,
        satellites: p2.satellites != null ? p2.satellites : p1.satellites,
        sample_unix: time,
        t: formatTime(time),
    }};
}}

function buildMapLayers() {{
    TRAJECTORIES.forEach(team => {{
        const key = getTeamKey(team);
        const latlngs = team.points.map(point => [point.lat, point.lng]);
        if (!latlngs.length) return;

        const poly = L.polyline(latlngs, {{
            color: team.color,
            weight: 2.4,
            opacity: 0.72,
            smoothFactor: 1,
        }}).addTo(routeLayer);

        poly.on("click", () => setFocusedTeam(key, true));
        poly.bindTooltip(team.name, {{ sticky: true }});

        const marker = L.marker(latlngs[0], {{
            icon: L.divIcon({{
                className: "",
                html: `<div class="tracker-marker" style="background:${{team.color}}"></div>`,
                iconSize: [14, 14],
                iconAnchor: [7, 7],
            }}),
            zIndexOffset: 1000,
        }}).addTo(markerLayer);

        marker.on("click", () => setFocusedTeam(key, true));
        teamLayers.set(key, {{
            team,
            poly,
            marker,
            bounds: L.latLngBounds(latlngs),
        }});
    }});

    if (teamLayers.size) {{
        const bounds = L.latLngBounds([]);
        teamLayers.forEach(layer => bounds.extend(layer.bounds));
        if (bounds.isValid()) {{
            map.fitBounds(bounds.pad(0.08));
        }}
    }} else {{
        map.setView({default_center}, {default_zoom});
    }}
}}

function buildPins() {{
    PINS.forEach(pin => {{
        if (pin.lat == null || pin.lng == null) return;
        const color = pin.color || "orange";
        const marker = L.circleMarker([pin.lat, pin.lng], {{
            radius: 6,
            color: color,
            fillColor: color,
            fillOpacity: 0.85,
            weight: 2,
        }});
        marker.bindPopup(`
            <div style="min-width:220px">
                <div style="font-weight:700;margin-bottom:6px">${{pin.message || "Pin"}}</div>
                <div class="muted">Submitted: ${{pin.submitted_at || "N/A"}}</div>
                <div class="muted">Approved: ${{pin.approved_at || "N/A"}}</div>
            </div>
        `);
        marker.addTo(pinLayer);
    }});
}}

function updateMarkers(time) {{
    teamLayers.forEach(layer => {{
        const position = getPositionAtTime(layer.team, time);
        if (!position || position.lat == null || position.lng == null) return;
        layer.marker.setLatLng([position.lat, position.lng]);
        const speed = position.speed != null ? `${{Number(position.speed).toFixed(1)}} km/h` : "N/A";
        layer.marker.bindTooltip(`<b>${{layer.team.name}}</b><br>${{speed}}`, {{ direction: "top" }});
    }});
}}

function updateSlider() {{
    const pct = ((currentTime - MIN_TIME) / TIME_SPAN) * 100;
    document.getElementById("time-slider").value = Math.max(0, Math.min(100, pct));
}}

function updateReplayLabels() {{
    document.getElementById("anim-time-display").textContent = formatTime(currentTime);
    document.getElementById("slider-time-label").textContent = formatShortTime(currentTime);
}}

function updateTopSummary() {{
    const totalSamples = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.sample_count || 0), 0);
    document.getElementById("loaded-teams").textContent = `${{TRAJECTORIES.length}} teams`;
    document.getElementById("pin-count").textContent = `${{PINS.length}} saved pins`;
    document.getElementById("sample-count").textContent = totalSamples.toLocaleString();
    if (MIN_TIME && MAX_TIME) {{
        document.getElementById("coverage-window").textContent =
            `${{formatShortTime(MIN_TIME)}} -> ${{formatShortTime(MAX_TIME)}}`;
    }} else {{
        document.getElementById("coverage-window").textContent = "No timing data";
    }}
}}

function buildFocusSelect() {{
    const select = document.getElementById("focus-select");
    const options = ['<option value="">All teams</option>'];
    TRAJECTORIES.forEach(team => {{
        options.push(`<option value="${{getTeamKey(team)}}">${{team.name}}</option>`);
    }});
    select.innerHTML = options.join("");
    select.addEventListener("change", () => setFocusedTeam(select.value || null, true));
}}

function buildRoster() {{
    const roster = document.getElementById("team-roster");
    const teams = [...TRAJECTORIES].sort((a, b) => (b.stats.distance_km || 0) - (a.stats.distance_km || 0));
    roster.innerHTML = teams.map(team => {{
        const key = getTeamKey(team);
        return `
            <div class="team-chip" data-team-key="${{key}}">
                <div class="team-dot" style="background:${{team.color}}"></div>
                <div>
                    <div>${{team.name}}</div>
                    <div class="team-meta">${{kmToMi(team.stats.distance_km)}} route · ${{team.stats.sample_count}} points</div>
                </div>
                <div class="team-meta">${{team.stats.anomaly_count || 0}} alerts</div>
            </div>
        `;
    }}).join("");

    roster.querySelectorAll(".team-chip").forEach(element => {{
        element.addEventListener("click", () => setFocusedTeam(element.dataset.teamKey, true));
    }});
}}

function fleetSummary() {{
    const totalDistanceKm = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.distance_km || 0), 0);
    const totalMovingTimeSec = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.moving_time_sec || 0), 0);
    const totalAlerts = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.anomaly_count || 0), 0);
    const totalStops = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.stop_count || 0), 0);
    const totalGaps = TRAJECTORIES.reduce((sum, team) => sum + (team.stats.gap_count || 0), 0);
    const longestGap = Math.max(0, ...TRAJECTORIES.map(team => team.stats.largest_gap_sec || 0));
    return [
        {{ label: "Fleet Route", value: kmToMi(totalDistanceKm), note: "Combined path distance" }},
        {{ label: "Moving Time", value: secondsToDuration(totalMovingTimeSec), note: "Across all teams" }},
        {{ label: "Route Alerts", value: String(totalAlerts), note: "Suspicious route segments" }},
        {{ label: "Stops", value: String(totalStops), note: "Long idle periods" }},
        {{ label: "Data Gaps", value: String(totalGaps), note: longestGap ? `Largest gap ${{secondsToDuration(longestGap)}}` : "No major gaps" }},
        {{ label: "Saved Pins", value: String(PINS.length), note: "Checkpoints and hazards" }},
    ];
}}

function teamSummary(team) {{
    const stats = team.stats || {{}};
    return [
        {{ label: "Route", value: kmToMi(stats.distance_km), note: `Straight-line ${{kmToMi(stats.straight_distance_km)}}` }},
        {{ label: "Moving Time", value: secondsToDuration(stats.moving_time_sec), note: `Idle ${{secondsToDuration(stats.idle_time_sec)}}` }},
        {{ label: "Tour Pace", value: stats.moving_average_speed_kph != null ? `${{stats.moving_average_speed_kph.toFixed(1)}} km/h` : "N/A", note: "Average while moving" }},
        {{ label: "Top Speed", value: stats.max_speed_kph != null ? `${{stats.max_speed_kph.toFixed(1)}} km/h` : "N/A", note: "Highest observed or implied" }},
        {{ label: "Stops", value: String(stats.stop_count || 0), note: `Active ${{stats.active_ratio_pct != null ? stats.active_ratio_pct.toFixed(1) + "%" : "N/A"}}` }},
        {{ label: "Data Quality", value: String(stats.anomaly_count || 0), note: stats.gap_count ? `${{stats.gap_count}} gaps, largest ${{secondsToDuration(stats.largest_gap_sec)}}` : "No large gaps" }},
        {{ label: "Sample Density", value: String(stats.sample_count || 0), note: stats.average_sample_gap_sec != null ? `Avg gap ${{Math.round(stats.average_sample_gap_sec)}}s` : "Single point only" }},
        {{ label: "Route Efficiency", value: stats.route_efficiency_pct != null ? `${{stats.route_efficiency_pct.toFixed(1)}}%` : "N/A", note: "Straight-line / actual route" }},
    ];
}}

function renderSummaryCards() {{
    const grid = document.getElementById("summary-grid");
    const team = focusedTeamKey ? TRAJECTORIES.find(item => getTeamKey(item) === focusedTeamKey) : null;
    const cards = team ? teamSummary(team) : fleetSummary();
    grid.innerHTML = cards.map(card => `
        <div class="summary-card">
            <div class="summary-label">${{card.label}}</div>
            <div class="summary-value">${{card.value}}</div>
            <div class="summary-note">${{card.note}}</div>
        </div>
    `).join("");
}}

function renderAnomalyList() {{
    const root = document.getElementById("anomaly-list");
    const team = focusedTeamKey ? TRAJECTORIES.find(item => getTeamKey(item) === focusedTeamKey) : null;
    if (!team) {{
        const topTeams = [...TRAJECTORIES]
            .filter(item => (item.stats.anomaly_count || 0) > 0)
            .sort((a, b) => (b.stats.anomaly_count || 0) - (a.stats.anomaly_count || 0))
            .slice(0, 8);
        root.innerHTML = topTeams.length ? topTeams.map(item => `
            <div class="anomaly-item">
                <div class="anomaly-title">${{item.name}}</div>
                <div class="anomaly-meta">
                    ${{item.stats.anomaly_count}} suspicious segments,
                    ${{item.stats.gap_count || 0}} major gaps,
                    longest gap ${{secondsToDuration(item.stats.largest_gap_sec || 0)}}
                </div>
            </div>
        `).join("") : '<div class="muted">No suspicious route segments detected in the current archive.</div>';
        return;
    }}

    const anomalies = (team.stats.anomalies || []).slice(0, 30);
    root.innerHTML = anomalies.length ? anomalies.map((anomaly, index) => `
        <div class="anomaly-item" data-anomaly-index="${{index}}">
            <div class="anomaly-title">${{anomaly.reason}}</div>
            <div class="anomaly-meta">
                ${{anomaly.time || "Time unavailable"}}<br>
                Segment ${{anomaly.distance_km}} km in ${{secondsToDuration(anomaly.gap_sec)}}<br>
                Implied speed ${{anomaly.segment_speed_kph}} km/h
            </div>
        </div>
    `).join("") : '<div class="muted">No suspicious segments found for this team.</div>';

    root.querySelectorAll(".anomaly-item[data-anomaly-index]").forEach(element => {{
        element.addEventListener("click", () => {{
            const anomaly = anomalies[Number(element.dataset.anomalyIndex)];
            if (!anomaly) return;
            const bounds = L.latLngBounds([
                [anomaly.from_lat, anomaly.from_lng],
                [anomaly.to_lat, anomaly.to_lng],
            ]);
            map.fitBounds(bounds.pad(0.8));
        }});
    }});
}}

function renderAnomalyOverlays() {{
    anomalyLayer.clearLayers();
    if (!focusedTeamKey) return;
    const team = TRAJECTORIES.find(item => getTeamKey(item) === focusedTeamKey);
    if (!team) return;

    (team.stats.anomalies || []).forEach(anomaly => {{
        const line = L.polyline(
            [
                [anomaly.from_lat, anomaly.from_lng],
                [anomaly.to_lat, anomaly.to_lng],
            ],
            {{
                color: "#f85149",
                weight: 5,
                opacity: 0.9,
                dashArray: "8, 8",
            }}
        );
        line.bindPopup(`
            <div style="min-width:220px">
                <div style="font-weight:700;margin-bottom:6px">${{anomaly.reason}}</div>
                <div class="muted">${{anomaly.time || "Time unavailable"}}</div>
                <div class="muted">${{anomaly.distance_km}} km over ${{secondsToDuration(anomaly.gap_sec)}}</div>
                <div class="muted">${{anomaly.segment_speed_kph}} km/h implied</div>
            </div>
        `);
        line.addTo(anomalyLayer);
    }});
}}

function applyFocusStyling() {{
    teamLayers.forEach((layer, key) => {{
        const focused = !focusedTeamKey || key === focusedTeamKey;
        const isPrimary = focusedTeamKey && key === focusedTeamKey;
        layer.poly.setStyle({{
            opacity: focused ? (isPrimary ? 1 : 0.28) : 0.14,
            weight: isPrimary ? 5.5 : 2.2,
        }});

        const markerElement = layer.marker.getElement();
        if (markerElement) {{
            const markerDot = markerElement.querySelector(".tracker-marker");
            if (markerDot) {{
                markerDot.classList.toggle("focused", isPrimary);
                markerElement.style.opacity = focused ? "1" : "0.25";
            }}
        }}

        if (isPrimary) {{
            layer.poly.bringToFront();
            layer.marker.setZIndexOffset(12000);
        }} else {{
            layer.marker.setZIndexOffset(1000);
        }}
    }});

    document.querySelectorAll(".team-chip").forEach(element => {{
        element.classList.toggle("active", element.dataset.teamKey === focusedTeamKey);
    }});
}}

function setFocusedTeam(teamKey, fitToBounds) {{
    focusedTeamKey = teamKey || null;
    document.getElementById("focus-select").value = focusedTeamKey || "";
    applyFocusStyling();
    renderSummaryCards();
    renderAnomalyList();
    renderAnomalyOverlays();
    if (fitToBounds) {{
        if (focusedTeamKey && teamLayers.has(focusedTeamKey)) {{
            map.fitBounds(teamLayers.get(focusedTeamKey).bounds.pad(0.18));
        }} else {{
            fitAll();
        }}
    }}
}}

function fitAll() {{
    const bounds = L.latLngBounds([]);
    teamLayers.forEach(layer => bounds.extend(layer.bounds));
    if (bounds.isValid()) {{
        map.fitBounds(bounds.pad(0.08));
    }}
}}

function fitFocus() {{
    if (focusedTeamKey && teamLayers.has(focusedTeamKey)) {{
        map.fitBounds(teamLayers.get(focusedTeamKey).bounds.pad(0.18));
    }} else {{
        fitAll();
    }}
}}

function stepReplay(timestamp) {{
    if (!isPlaying) return;
    if (!lastFrameTime) lastFrameTime = timestamp;
    const deltaSec = ((timestamp - lastFrameTime) / 1000) * playbackSpeed;
    lastFrameTime = timestamp;
    currentTime += deltaSec;
    if (currentTime >= MAX_TIME) currentTime = MIN_TIME;
    updateMarkers(currentTime);
    updateSlider();
    updateReplayLabels();
    animationFrameId = requestAnimationFrame(stepReplay);
}}

function startPlayback() {{
    if (isPlaying) return;
    if (currentTime >= MAX_TIME) currentTime = MIN_TIME;
    isPlaying = true;
    lastFrameTime = 0;
    document.getElementById("play-btn").textContent = "Pause";
    document.getElementById("status-pill").textContent = "Replay running";
    animationFrameId = requestAnimationFrame(stepReplay);
}}

function stopPlayback() {{
    isPlaying = false;
    if (animationFrameId) {{
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
    }}
    document.getElementById("play-btn").textContent = "Play";
    document.getElementById("status-pill").textContent = focusedTeamKey
        ? "Focused route inspection"
        : "Archive replay ready";
}}

function resetReplay() {{
    stopPlayback();
    currentTime = MAX_TIME;
    updateMarkers(currentTime);
    updateSlider();
    updateReplayLabels();
}}

document.getElementById("play-btn").addEventListener("click", () => {{
    if (isPlaying) stopPlayback();
    else startPlayback();
}});
document.getElementById("reset-btn").addEventListener("click", resetReplay);
document.getElementById("fit-all-btn").addEventListener("click", fitAll);
document.getElementById("fit-focus-btn").addEventListener("click", fitFocus);
document.getElementById("speed-select").addEventListener("change", event => {{
    playbackSpeed = Number(event.target.value || 1);
}});
document.getElementById("time-slider").addEventListener("input", event => {{
    const pct = Number(event.target.value || 0) / 100;
    currentTime = MIN_TIME + (TIME_SPAN * pct);
    updateMarkers(currentTime);
    updateReplayLabels();
}});
document.addEventListener("keydown", event => {{
    if (event.code === "Space") {{
        event.preventDefault();
        if (isPlaying) stopPlayback();
        else startPlayback();
    }}
}});

buildMapLayers();
buildPins();
buildFocusSelect();
buildRoster();
updateTopSummary();
setFocusedTeam(null, false);
resetReplay();
</script>
</body>
</html>"""
    return html


def serve_live(data_dir, port=8899):
    """Simple live server that re-generates HTML on each request."""
    import socketserver

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                teams = load_updates(data_dir)
                snaps = load_snapshots(data_dir)
                pins = load_pins(data_dir)
                html = generate_html(teams, snaps, pins=pins, title="ASC 2026 Archive Explorer")
                self.wfile.write(html.encode("utf-8"))
            else:
                super().do_GET()

        def log_message(self, fmt, *args):
            pass

    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"Live server at http://localhost:{port}")
        print("Press Ctrl+C to stop")
        webbrowser.open(f"http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")


def main():
    parser = argparse.ArgumentParser(description="ASC Trajectory Visualization")
    parser.add_argument(
        "--dir", default=DEFAULT_DATA_DIR,
        help="Data directory (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT,
        help="Output HTML file (default: %(default)s)",
    )
    parser.add_argument(
        "--team",
        help="Filter to a specific team (name or serial)",
    )
    parser.add_argument(
        "--open", "-p", action="store_true",
        help="Open the generated HTML in the browser",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Start a live web server that serves the latest data",
    )
    parser.add_argument(
        "--port", type=int, default=8899,
        help="Port for live server (default: %(default)s)",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Watch mode: regenerate HTML every 30 seconds",
    )

    args = parser.parse_args()

    if args.live:
        serve_live(args.dir, args.port)
        return

    if args.watch:
        output_path = Path(args.output)
        print(f"Watch mode enabled. Regenerating every 30s to {output_path}")
        while True:
            teams = load_updates(args.dir)
            snaps = load_snapshots(args.dir)
            pins = load_pins(args.dir)
            html = generate_html(teams, snaps, pins=pins, team_filter=args.team)
            output_path.write_text(html)
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] Refreshed: {len(teams)} teams, {len(snaps)} snapshots, {len(pins)} pins")
            std_time.sleep(30)
        return

    teams = load_updates(args.dir)
    snaps = load_snapshots(args.dir)
    pins = load_pins(args.dir)
    html = generate_html(teams, snaps, pins=pins, team_filter=args.team)

    output_path = Path(args.output)
    output_path.write_text(html)
    print(f"Generated {output_path}")
    print(f"  {len(teams)} teams, {sum(len(team['points']) for team in teams)} trajectory points")
    print(f"  {len(snaps)} snapshots, {len(pins)} pins")

    if args.open:
        webbrowser.open(output_path.resolve().as_uri())
        print("  Opened in browser")


if __name__ == "__main__":
    main()
