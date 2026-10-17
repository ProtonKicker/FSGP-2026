#!/usr/bin/env python3
"""
Shared readers for legacy ASC raw captures and the tracked archival dataset.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

DEFAULT_LEGACY_DIR = "data"
DEFAULT_ARCHIVE_DIR = "dataset/raw"
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
    digest = hashlib.md5(name.encode()).hexdigest()
    return COLORS[int(digest[:8], 16) % len(COLORS)]


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

        if dt <= 0:
            continue

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


def resolve_data_dir(path=None):
    base_dir = Path(__file__).resolve().parent
    candidates = []
    if path:
        candidates.extend([Path(path), base_dir / path])
    else:
        candidates.extend([
            base_dir / DEFAULT_ARCHIVE_DIR,
            base_dir / DEFAULT_LEGACY_DIR,
        ])

    for candidate in candidates:
        if candidate.is_dir():
            archive_dir = candidate / DEFAULT_ARCHIVE_DIR
            legacy_dir = candidate / DEFAULT_LEGACY_DIR
            if archive_dir.is_dir() and contains_supported_data(archive_dir):
                return archive_dir
            if legacy_dir.is_dir() and contains_supported_data(legacy_dir):
                return legacy_dir
        if candidate.is_dir() and contains_supported_data(candidate):
            return candidate

    fallback = base_dir / DEFAULT_ARCHIVE_DIR
    if fallback.is_dir():
        return fallback
    return base_dir / DEFAULT_LEGACY_DIR


def contains_supported_data(path):
    patterns = (
        "updates_*.jsonl",
        "updates_*.jsonl.gz",
        "snapshots_*.jsonl.gz",
        "snapshots_*.jsonl",
        "traces_*.json",
        "pins_*.json",
        "pins_*.json.gz",
        "weather_*.jsonl",
        "weather_*.jsonl.gz",
    )
    return any(list_matching_files(path, pattern) for pattern in patterns)


def list_matching_files(path, pattern):
    matches = set(path.glob(pattern))
    matches.update(path.glob(f"**/{pattern}"))
    return sorted(matches)


def open_text(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def iter_jsonl(path):
    with open_text(path) as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_update_records(data_dir):
    data_path = resolve_data_dir(data_dir)
    for path in list_matching_files(data_path, "updates_*.jsonl"):
        yield from iter_jsonl(path)
    for path in list_matching_files(data_path, "updates_*.jsonl.gz"):
        yield from iter_jsonl(path)


def iter_weather_records(data_dir):
    data_path = resolve_data_dir(data_dir)
    for path in list_matching_files(data_path, "weather_*.jsonl"):
        yield from iter_jsonl(path)
    for path in list_matching_files(data_path, "weather_*.jsonl.gz"):
        yield from iter_jsonl(path)


def iter_pin_snapshots(data_dir):
    data_path = resolve_data_dir(data_dir)
    for path in list_matching_files(data_path, "pins_*.json"):
        with open(path, "r", encoding="utf-8") as handle:
            yield json.load(handle)
    for path in list_matching_files(data_path, "pins_*.json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield json.load(handle)


def iter_snapshot_records(data_dir):
    data_path = resolve_data_dir(data_dir)
    for path in list_matching_files(data_path, "traces_*.json"):
        with open(path, "r", encoding="utf-8") as handle:
            yield json.load(handle)
    for path in list_matching_files(data_path, "snapshots_*.jsonl"):
        yield from iter_jsonl(path)
    for path in list_matching_files(data_path, "snapshots_*.jsonl.gz"):
        yield from iter_jsonl(path)


def load_updates(data_dir):
    raw = defaultdict(list)
    for entry in iter_update_records(data_dir):
        if entry.get("lat") is None or entry.get("lon") is None:
            continue
        raw[entry.get("serial", "?")].append(entry)

    teams = []
    for serial, entries in sorted(raw.items()):
        entries.sort(key=lambda item: item.get("sample_unix", 0) or 0)
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
        teams.append({
            "serial": serial,
            "name": name,
            "color": color_for(name),
            "points": points,
            "stats": compute_team_metrics(points),
        })
    return teams


def load_snapshots(data_dir):
    snapshots = []
    for record in iter_snapshot_records(data_dir):
        snapshots.append({
            "ts": record.get("timestamp", ""),
            "ts_unix": record.get("timestamp_unix", 0),
            "count": record.get("tracker_count", 0),
        })
    return snapshots


def load_pins(data_dir):
    deduped = {}
    for record in iter_pin_snapshots(data_dir):
        for pin in record.get("pins", []):
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
    pins.sort(key=lambda item: item.get("approved_at") or item.get("submitted_at") or "")
    return pins
