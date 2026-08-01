#!/usr/bin/env python3
"""
ASC Tracker Data Export Utility

Converts collected data to CSV, GeoJSON, and GPX formats.
Reconstructs team trajectories from the continuous update log.

Usage:
    python export.py list                     # List all snapshot files
    python export.py csv --latest             # Latest snapshot to CSV
    python export.py csv --all                # All snapshots to CSV
    python export.py summary                  # Summary table

    python export.py path --team "Stanford"   # Team trajectory → CSV
    python export.py path --team "16" --geojson  # → GeoJSON
    python export.py paths --geojson          # All teams → GeoJSON

    python export.py updates --tail 50        # Recent updates from log
    python export.py status                   # Latest known positions
"""

import argparse
import csv
import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

DEFAULT_DATA_DIR = "data"


def find_snapshots(data_dir):
    path = Path(data_dir)
    if not path.is_dir():
        return []
    return sorted(path.glob("traces_*.json"))


def find_updates(data_dir):
    path = Path(data_dir)
    if not path.is_dir():
        return []
    return sorted(path.glob("updates_*.jsonl"))


def load_snapshot(path):
    with open(path) as f:
        return json.load(f)


def load_updates(paths):
    for p in paths:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def snapshot_to_rows(record):
    timestamp = record.get("timestamp", "")
    rows = []
    for t in record.get("trackers", []):
        rows.append({
            "snapshot_time": timestamp,
            "name": t.get("name", ""),
            "serial": t.get("serial", ""),
            "latitude": t.get("latitude"),
            "longitude": t.get("longitude"),
            "altitude_m": t.get("altitude_m"),
            "speed_kph": t.get("speed_kph"),
            "course_deg": t.get("course_deg"),
            "sample_time": t.get("sample_time", ""),
            "sample_unix": t.get("sample_unix"),
            "satellites": t.get("satellites"),
            "pdop": t.get("pdop"),
            "fix_mode": t.get("fix_mode"),
            "color": t.get("color", ""),
        })
    return rows


def cmd_list(args):
    snapshots = find_snapshots(args.dir)
    if not snapshots:
        print("No snapshot files found in", args.dir)
        return
    print(f"{'File':50} {'Trackers':>9}  Timestamp")
    print("-" * 80)
    for p in snapshots:
        try:
            rec = load_snapshot(p)
            print(f"{p.name:50} {rec.get('tracker_count', '?'):>9}  {rec.get('timestamp', '?')}")
        except Exception:
            print(f"{p.name:50} {'ERR':>9}")


def cmd_csv(args):
    snapshots = find_snapshots(args.dir)
    if args.latest:
        if not snapshots:
            print("No snapshot files found")
            return
        snapshots = [snapshots[-1]]
    elif args.all:
        pass
    else:
        filenames = args.files
        if not filenames:
            filenames = [snapshots[-1].name] if snapshots else []
        snapshots = [p for p in snapshots if p.name in filenames]
        if not snapshots:
            print("No matching files found")
            return

    all_rows = []
    for p in snapshots:
        try:
            rec = load_snapshot(p)
            all_rows.extend(snapshot_to_rows(rec))
        except Exception as e:
            print(f"Error reading {p.name}: {e}", file=sys.stderr)

    if not all_rows:
        print("No data to export")
        return

    fields = [
        "snapshot_time", "name", "serial", "latitude", "longitude",
        "altitude_m", "speed_kph", "course_deg", "sample_time",
        "sample_unix", "satellites", "pdop", "fix_mode", "color",
    ]

    if args.output:
        output_path = Path(args.output)
    else:
        if len(snapshots) == 1:
            stem = snapshots[0].stem
        else:
            stem = f"asc_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_path = Path(args.dir) / f"{stem}.csv"

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Exported {len(all_rows)} rows to {output_path}")


def cmd_summary(args):
    snapshots = find_snapshots(args.dir)
    if not snapshots:
        print("No snapshot files found in", args.dir)
        return

    print(f"{'Snapshot':30} {'Trackers':>9}  {'Moving':>7}  {'Stopped':>8}  Oldest Update")
    print("-" * 80)
    for p in snapshots:
        try:
            rec = load_snapshot(p)
            trackers = rec.get("trackers", [])
            total = len(trackers)
            moving = sum(1 for t in trackers if (t.get("speed_kph") or 0) > 0)
            stopped = total - moving
            sample_times = [
                t.get("sample_time", "") for t in trackers if t.get("sample_time")
            ]
            oldest = min(sample_times) if sample_times else "N/A"
            print(f"{p.name:30} {total:>9}  {moving:>7}  {stopped:>8}  {oldest}")
        except Exception as e:
            print(f"{p.name:30}  Error: {e}")


def cmd_path(args):
    update_files = find_updates(args.dir)
    if not update_files:
        print("No update log files found in", args.dir)
        return

    team_filter = args.team.lower()
    entries = []

    for entry in load_updates(update_files):
        name = entry.get("name", "").lower()
        serial = entry.get("serial", "").lower()
        if team_filter in name or team_filter in serial:
            if "lat" in entry and "lon" in entry:
                entries.append(entry)

    if not entries:
        print(f"No entries found for team matching '{args.team}'")
        return

    entries.sort(key=lambda e: e.get("sample_unix", 0) or e.get("_t", ""))

    if args.geojson:
        output = path_to_geojson(entries, args.team)
        out_path = args.output or f"path_{args.team.replace(' ', '_')}.geojson"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Exported {len(entries)} points to {out_path}")
    else:
        fields = [
            "t", "serial", "name", "lat", "lon", "speed",
            "heading", "alt", "sample_time", "sample_unix",
            "satellites", "pdop",
        ]
        out_path = args.output or f"path_{args.team.replace(' ', '_')}.csv"
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(entries)
        print(f"Exported {len(entries)} points to {out_path}")


def path_to_geojson(entries, label):
    coords = []
    for e in entries:
        lat = e.get("lat")
        lon = e.get("lon")
        if lat is not None and lon is not None:
            coords.append([lon, lat])

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": label,
                    "count": len(coords),
                    "source": "ASC Live Tracker",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords,
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "name": f"{label} (updates)",
                    "source": "ASC Live Tracker",
                },
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": coords,
                },
            },
        ],
    }


def cmd_paths(args):
    update_files = find_updates(args.dir)
    if not update_files:
        print("No update log files found")
        return

    teams = OrderedDict()
    last_seen = {}

    for entry in load_updates(update_files):
        serial = entry.get("serial", "?")
        name = entry.get("name", serial)
        team_key = f"{name} ({serial})"
        lat = entry.get("lat")
        lon = entry.get("lon")
        if lat is not None and lon is not None:
            if team_key not in teams:
                teams[team_key] = []
            teams[team_key].append([lon, lat])
            last_seen[team_key] = entry

    if not teams:
        print("No data found")
        return

    features = []
    for team_key, coords in teams.items():
        entry = last_seen[team_key]
        features.append({
            "type": "Feature",
            "properties": {
                "name": team_key,
                "count": len(coords),
                "last_speed": entry.get("speed"),
                "last_heading": entry.get("heading"),
                "last_sample": entry.get("sample_time"),
            },
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        })

    output = {
        "type": "FeatureCollection",
        "features": features,
    }

    out_path = args.output or "all_teams.geojson"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Exported {len(teams)} team paths ({sum(len(c) for c in teams.values())} total points) to {out_path}")


def cmd_updates(args):
    update_files = find_updates(args.dir)
    if not update_files:
        print("No update log files found")
        return

    count = 0
    buf = []
    for entry in load_updates(update_files):
        count += 1
        buf.append(entry)
        if len(buf) > args.tail:
            buf.pop(0)

    print(f"Total entries in log: {count}")
    print(f"Last {len(buf)} updates:")
    print("-" * 60)
    for e in buf:
        lat = e.get("lat", "?")
        lon = e.get("lon", "?")
        speed = e.get("speed", "?")
        name = e.get("name", e.get("serial", "?"))
        ts = e.get("_t", "?")
        lat_s = f"{lat:.4f}" if isinstance(lat, (int, float)) else lat
        lon_s = f"{lon:.4f}" if isinstance(lon, (int, float)) else lon
        speed_s = f"{speed:.1f}" if isinstance(speed, (int, float)) else speed
        print(f"  {ts[:23]:23s} {name:25s} {lat_s:>9s} {lon_s:>9s} {speed_s:>7s}")


def cmd_status(args):
    """Show latest known position of all teams from the update log."""
    update_files = find_updates(args.dir)
    if not update_files:
        print("No update log files found")
        return

    latest = {}
    for entry in load_updates(update_files):
        serial = entry.get("serial", "?")
        if serial in ("?", "") or entry.get("removed"):
            latest.pop(serial, None)
            continue
        latest[serial] = entry

    if not latest:
        print("No trackers found")
        return

    print(f"{'Team':30} {'Lat':>9} {'Lon':>9} {'Speed':>7} {'Heading':>7}  {'Age':>7}")
    print("-" * 80)
    for serial in sorted(latest, key=lambda s: latest[s].get("name", s)):
        e = latest[serial]
        lat = e.get("lat", "?")
        lon = e.get("lon", "?")
        speed = e.get("speed", 0) or 0
        heading = e.get("heading", 0) or 0
        ts = e.get("_t", "")
        lat_s = f"{lat:.4f}" if isinstance(lat, (int, float)) else "?"
        lon_s = f"{lon:.4f}" if isinstance(lon, (int, float)) else "?"
        name = (e.get("name") or serial)[:28]
        print(f"{name:30} {lat_s:>9} {lon_s:>9} {speed:>7.1f} {heading:>7.0f}°  {ts[:19]:>7}")


def main():
    parser = argparse.ArgumentParser(description="ASC Data Export Utility")
    parser.add_argument(
        "--dir", default=DEFAULT_DATA_DIR,
        help="Data directory (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List available snapshot files")

    p_csv = sub.add_parser("csv", help="Export to CSV")
    p_csv.add_argument("files", nargs="*")
    p_csv.add_argument("--latest", action="store_true")
    p_csv.add_argument("--all", action="store_true")
    p_csv.add_argument("--output", "-o")

    sub.add_parser("summary", help="Show summary of all snapshots")

    p_path = sub.add_parser("path", help="Reconstruct a team's trajectory")
    p_path.add_argument("--team", required=True, help="Team name or serial to filter")
    p_path.add_argument("--geojson", action="store_true", help="Output as GeoJSON instead of CSV")
    p_path.add_argument("--output", "-o", help="Output file path")

    p_paths = sub.add_parser("paths", help="All team paths to GeoJSON")
    p_paths.add_argument("--output", "-o", default="all_teams.geojson")

    p_updates = sub.add_parser("updates", help="View recent update log entries")
    p_updates.add_argument("--tail", type=int, default=20)

    sub.add_parser("status", help="Latest known positions of all teams")

    args = parser.parse_args()

    cmd_map = {
        "list": cmd_list,
        "csv": cmd_csv,
        "summary": cmd_summary,
        "path": cmd_path,
        "paths": cmd_paths,
        "updates": cmd_updates,
        "status": cmd_status,
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
