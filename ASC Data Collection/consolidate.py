#!/usr/bin/env python3
"""
consolidate.py — Flatten ASC 2026 collected data into readable formats.

The raw collection is stored as thousands of tiny per-30-second JSON snapshot
files, split across daily zip archives and backup zips. This script reads every
source and rewrites the data as tidy CSV tables plus GeoJSON route files under
`data/`. Nothing is deleted or modified in place.

Sources read (read-only):
  - archives/asc_public_YYYY-MM-DD.zip   -> sanitized public data
  - backups/asc_raw_data_final_*.zip     -> raw data (includes serial numbers)
  - loose traces_*.json in repo root     -> extra raw traces from 07-25

Outputs written under data/:
  sanitized/  flattened tables + routes built from the public archives
  raw/        flattened tables + routes built from the raw backup (+ loose files)
  manifest.json  summary of what was produced
  README.md   layout description
"""

import csv
import io
import json
import math
import re
import sys
import zipfile
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVES_DIR = ROOT / "archives"
BACKUPS_DIR = ROOT / "backups"
OUT_DIR = ROOT / "data"

DATE_RE = re.compile(r"(2026-\d{2}-\d{2})")
TEAM_SLUG_RE = re.compile(r"[^a-z0-9_]+")

TRACE_FIELDS = [
    "timestamp", "timestamp_unix", "tracker_count",
    "serial", "serial_hex", "tracker_id", "name",
    "latitude", "longitude", "altitude_m", "speed_kph", "course_deg",
    "pdop", "satellites", "fix_mode",
    "sample_unix", "sample_time", "color",
    "captured_time", "captured_unix", "sample_age_sec",
]

UPDATE_FIELDS = [
    "_t", "_t_unix",
    "serial", "tracker_id", "name",
    "lat", "lon", "speed", "heading", "alt",
    "sample_time", "sample_unix", "satellites", "pdop", "fix_mode", "color",
    "removed",
]

WEATHER_FIELDS = [
    "_recorded", "_recorded_unix",
    "temp_c", "feels_like_c", "humidity_pct",
    "wind_kph", "wind_dir_deg", "wind_gust_kph", "precip_mm",
    "weather_code", "weather_desc", "lat", "lon",
]

PIN_FIELDS = [
    "snapshot_time", "snapshot_unix", "pin_count",
    "id", "latitude", "longitude", "color", "message",
    "submitted_at", "approved", "approved_at",
]


def progress(msg):
    print(msg, file=sys.stderr)


def safe_loads(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def iter_zip_text(zip_path, prefix=""):
    """Yield (relative_name, decoded_text) for every JSON/JSONL member."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith((".json", ".jsonl")):
                continue
            if prefix and not name.startswith(prefix):
                continue
            rel = name[len(prefix):] if prefix else name
            try:
                yield rel, zf.read(name).decode("utf-8")
            except UnicodeDecodeError:
                yield rel, ""


def day_of(name):
    m = DATE_RE.search(name)
    return m.group(1) if m else None


class Dataset:
    """Collects flattened rows + route points for one source into one folder."""

    def __init__(self, label, out_path):
        self.label = label
        self.out_path = out_path
        self.stats = defaultdict(int)
        self.routes = OrderedDict()
        self.snapshot_days = set()

    def add_trace(self, day, record):
        ts = record.get("timestamp", "")
        ts_unix = record.get("timestamp_unix")
        tracker_count = record.get("tracker_count", len(record.get("trackers", [])))
        self.stats["trace_snapshots"] += 1
        self.snapshot_days.add(day)
        for t in record.get("trackers", []):
            row = dict(t)
            row["timestamp"] = ts
            row["timestamp_unix"] = ts_unix
            row["tracker_count"] = tracker_count
            self._queue("traces", day, row)

    def add_update(self, day, entry):
        self.stats["update_entries"] += 1
        self._queue("updates", day, dict(entry))
        if entry.get("removed"):
            return
        lat = entry.get("lat")
        lon = entry.get("lon")
        if lat is None or lon is None:
            return
        team_key = entry.get("tracker_id") or entry.get("serial") or "?"
        name = entry.get("name") or team_key
        t = entry.get("sample_unix") or entry.get("_t_unix") or 0
        route = self.routes.setdefault(team_key, {"name": name, "points": []})
        route["name"] = name
        route["points"].append((t, lon, lat, entry.get("speed")))

    def add_weather(self, day, entry):
        self.stats["weather_entries"] += 1
        self._queue("weather", day, dict(entry))

    def add_pins(self, day, record):
        self.stats["pin_snapshots"] += 1
        ts = record.get("timestamp", "")
        ts_unix = record.get("timestamp_unix")
        pin_count = record.get("pin_count", len(record.get("pins", [])))
        for p in record.get("pins", []):
            row = dict(p)
            row["snapshot_time"] = ts
            row["snapshot_unix"] = ts_unix
            row["pin_count"] = pin_count
            self.stats["pins"] += 1
            self._queue("pins", day, row)

    def _queue(self, kind, day, row):
        self._pending = getattr(self, "_pending", defaultdict(list))
        self._pending[(kind, day)].append(row)

    def _flush_queued(self):
        pending = getattr(self, "_pending", {})
        for (kind, day), rows in pending.items():
            fields = {
                "traces": TRACE_FIELDS,
                "updates": UPDATE_FIELDS,
                "weather": WEATHER_FIELDS,
                "pins": PIN_FIELDS,
            }[kind]
            self._write_csv(kind, day, rows, fields)
        self._pending = defaultdict(list)

    def _write_csv(self, kind, day, rows, fields):
        # combined file
        comb = self.out_path / f"{kind}_all.csv"
        with comb.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", restval="")
            if comb.stat().st_size == 0:
                w.writeheader()
            w.writerows(rows)
        # per-day file
        day_dir = self.out_path / "per_day"
        day_dir.mkdir(parents=True, exist_ok=True)
        dpath = day_dir / f"{day}_{kind}.csv"
        with dpath.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", restval="")
            if dpath.stat().st_size == 0:
                w.writeheader()
            w.writerows(rows)

    def finish(self):
        self._flush_queued()
        self.stats["days"] = len(self.snapshot_days)
        self._write_routes()
        return dict(self.stats)

    def _write_routes(self):
        if not self.routes:
            return
        routes_dir = self.out_path / "routes"
        routes_dir.mkdir(parents=True, exist_ok=True)
        features = []
        for team_key, data in self.routes.items():
            feats = build_route_features(team_key, data)
            features.extend(feats)
            slug = TEAM_SLUG_RE.sub("_", data["name"].lower().replace(" ", "_")).strip("_")
            slug = slug or team_key
            path = routes_dir / f"{slug}_route.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=2))
            self.stats["routes"] += 1
        (routes_dir / "all_teams.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features}, indent=2)
        )


def haversine_km(lon1, lat1, lon2, lat2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_route_features(team_key, data):
    pts = sorted(data["points"], key=lambda p: p[0])
    deduped = []
    last = None
    for p in pts:
        if last is None or (p[1], p[2]) != (last[1], last[2]):
            deduped.append(p)
            last = p
    coords = [[lon, lat] for _, lon, lat, _ in deduped]
    if not coords:
        return []
    dist = 0.0
    for a, b in zip(deduped, deduped[1:]):
        dist += haversine_km(a[1], a[2], b[1], b[2])
    t0 = min(p[0] for p in deduped)
    t1 = max(p[0] for p in deduped)
    properties = {
        "team": data["name"],
        "id": team_key,
        "point_count": len(coords),
        "distance_km": round(dist, 2),
        "first_sample_unix": t0,
        "last_sample_unix": t1,
    }
    return [
        {
            "type": "Feature",
            "properties": dict(properties, kind="line"),
            "geometry": {"type": "LineString", "coordinates": coords},
        },
        {
            "type": "Feature",
            "properties": dict(properties, kind="points"),
            "geometry": {"type": "MultiPoint", "coordinates": coords},
        },
    ]


def ingest_members(dataset, members):
    for rel, text in members:
        day = day_of(rel)
        if rel.startswith("traces_"):
            rec = safe_loads(text)
            if rec:
                dataset.add_trace(day, rec)
        elif rel.startswith("updates_"):
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                entry = safe_loads(line)
                if entry:
                    dataset.add_update(day, entry)
        elif rel.startswith("weather_"):
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                entry = safe_loads(line)
                if entry:
                    dataset.add_weather(day, entry)
        elif rel.startswith("pins_"):
            rec = safe_loads(text)
            if rec:
                dataset.add_pins(day, rec)


def ingest_trace_file(dataset, path):
    try:
        rec = safe_loads(path.read_text())
    except OSError:
        return
    if rec:
        dataset.add_trace(day_of(path.name), rec)


def sanitized_source():
    out = OUT_DIR / "sanitized"
    out.mkdir(parents=True, exist_ok=True)
    ds = Dataset("sanitized", out)
    zips = sorted(ARCHIVES_DIR.glob("asc_public_*.zip"))
    for zp in zips:
        progress(f"  sanitized: reading {zp.name}")
        ingest_members(ds, iter_zip_text(zp))
    return ds


def raw_source():
    out = OUT_DIR / "raw"
    out.mkdir(parents=True, exist_ok=True)
    ds = Dataset("raw", out)
    zips = sorted(BACKUPS_DIR.glob("asc_raw_data_final_*.zip"))
    for zp in zips:
        progress(f"  raw: reading {zp.name}")
        ingest_members(ds, iter_zip_text(zp, prefix="data/"))
    for tp in sorted(ROOT.glob("traces_*.json")):
        progress(f"  raw: reading loose {tp.name}")
        ingest_trace_file(ds, tp)
    return ds


def main():
    manifest = {"created_at": datetime.now(timezone.utc).isoformat()}
    sources = {}
    for label, fn in (("sanitized", sanitized_source), ("raw", raw_source)):
        progress(f"[{label}] building...")
        ds = fn()
        sources[label] = ds.finish()
        progress(f"[{label}] done: {sources[label]}")
    manifest["sources"] = sources
    manifest["fields"] = {
        "traces": TRACE_FIELDS,
        "updates": UPDATE_FIELDS,
        "weather": WEATHER_FIELDS,
        "pins": PIN_FIELDS,
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("Wrote outputs to:", OUT_DIR)


if __name__ == "__main__":
    main()
