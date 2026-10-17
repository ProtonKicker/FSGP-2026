#!/usr/bin/env python3
"""
Build a tracked, Git-friendly archival dataset from the local ASC raw capture.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import data_access


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def aggregate_sha256(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def ensure_clean_dir(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def count_lines(path):
    line_count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                line_count += 1
    return line_count


def gzip_copy(source_path, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(source_path, "rb") as src, gzip.open(output_path, "wb", compresslevel=9) as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)


def load_existing_snapshot_archive(snapshot_dir):
    existing = defaultdict(dict)
    if not snapshot_dir.exists():
        return existing

    for archive_path in sorted(snapshot_dir.glob("snapshots_*.jsonl.gz")):
        day = archive_path.name.replace("snapshots_", "").replace(".jsonl.gz", "")
        with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                timestamp = record.get("timestamp")
                if timestamp:
                    existing[day][timestamp] = record
    return existing


def package_jsonl_group(source_paths, output_dir, manifest, kind):
    for source_path in source_paths:
        output_path = output_dir / f"{source_path.name}.gz"
        gzip_copy(source_path, output_path)
        manifest["packages"].append({
            "kind": kind,
            "date": source_path.stem.split("_", 1)[1],
            "source_files": [str(source_path.relative_to(manifest["source_root"]))],
            "output_file": str(output_path.relative_to(manifest["root"])),
            "record_count": count_lines(source_path),
            "original_bytes": source_path.stat().st_size,
            "archived_bytes": output_path.stat().st_size,
            "source_sha256": sha256_file(source_path),
        })


def package_pins(source_paths, output_dir, manifest):
    for source_path in source_paths:
        output_path = output_dir / source_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, output_path)
        with open(source_path, "r", encoding="utf-8") as handle:
            pin_record = json.load(handle)
        manifest["packages"].append({
            "kind": "pins",
            "date": source_path.stem.split("_", 1)[1],
            "source_files": [str(source_path.relative_to(manifest["source_root"]))],
            "output_file": str(output_path.relative_to(manifest["root"])),
            "record_count": len(pin_record.get("pins", [])),
            "original_bytes": source_path.stat().st_size,
            "archived_bytes": output_path.stat().st_size,
            "source_sha256": sha256_file(source_path),
        })


def package_snapshots(source_paths, output_dir, manifest, existing_snapshot_records=None):
    grouped = defaultdict(list)
    for source_path in source_paths:
        day = source_path.name.split("_", 1)[1].split("T", 1)[0]
        grouped[day].append(source_path)

    existing_snapshot_records = existing_snapshot_records or defaultdict(dict)
    all_days = sorted(set(grouped.keys()) | set(existing_snapshot_records.keys()))

    for day in all_days:
        paths = grouped.get(day, [])
        output_path = output_dir / f"snapshots_{day}.jsonl.gz"
        merged_records = dict(existing_snapshot_records.get(day, {}))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        for source_path in sorted(paths):
            with open(source_path, "r", encoding="utf-8") as source_handle:
                record = json.load(source_handle)
            timestamp = record.get("timestamp")
            if timestamp:
                merged_records[timestamp] = record

        ordered_records = [merged_records[timestamp] for timestamp in sorted(merged_records)]
        with gzip.open(output_path, "wt", encoding="utf-8") as handle:
            for record in ordered_records:
                handle.write(json.dumps(record, separators=(",", ":")))
                handle.write("\n")

        manifest["packages"].append({
            "kind": "snapshots",
            "date": day,
            "source_file_count": len(paths),
            "source_file_first": str(sorted(paths)[0].relative_to(manifest["source_root"])) if paths else None,
            "source_file_last": str(sorted(paths)[-1].relative_to(manifest["source_root"])) if paths else None,
            "preserved_archive_records": max(0, len(ordered_records) - len(paths)),
            "output_file": str(output_path.relative_to(manifest["root"])),
            "record_count": len(ordered_records),
            "original_bytes": sum(path.stat().st_size for path in paths),
            "archived_bytes": output_path.stat().st_size,
            "source_sha256": aggregate_sha256(paths) if paths else None,
        })


def package_log(log_path, output_dir, manifest):
    if not log_path.exists():
        return
    output_path = output_dir / "collector_2026-07-26_to_2026-08-01.log.gz"
    gzip_copy(log_path, output_path)
    line_count = 0
    with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
        for _ in handle:
            line_count += 1
    manifest["packages"].append({
        "kind": "collector_log",
        "date": "2026-07-26_to_2026-08-01",
        "source_files": [str(log_path.relative_to(manifest["source_root"]))],
        "output_file": str(output_path.relative_to(manifest["root"])),
        "record_count": line_count,
        "original_bytes": log_path.stat().st_size,
        "archived_bytes": output_path.stat().st_size,
        "source_sha256": sha256_file(log_path),
    })


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_daily_coverage_rows(data_dir):
    summary = defaultdict(lambda: {"updates": 0, "snapshots": 0, "weather": 0, "pins": 0})

    for entry in data_access.iter_update_records(data_dir):
        day = (entry.get("_t") or "")[:10]
        summary[day]["updates"] += 1

    for snapshot in data_access.iter_snapshot_records(data_dir):
        day = (snapshot.get("timestamp") or "")[:10]
        summary[day]["snapshots"] += 1

    for record in data_access.iter_weather_records(data_dir):
        day = (record.get("_recorded") or "")[:10]
        summary[day]["weather"] += 1

    for record in data_access.iter_pin_snapshots(data_dir):
        day = (record.get("timestamp") or "")[:10]
        summary[day]["pins"] += len(record.get("pins", []))

    rows = []
    for day, counts in sorted(summary.items()):
        rows.append({
            "date": day,
            "update_records": counts["updates"],
            "snapshot_records": counts["snapshots"],
            "weather_records": counts["weather"],
            "approved_pins": counts["pins"],
        })
    return rows


def build_team_metric_rows(data_dir):
    rows = []
    for team in sorted(
        data_access.load_updates(data_dir),
        key=lambda item: item["stats"]["distance_km"],
        reverse=True,
    ):
        stats = team["stats"]
        rows.append({
            "team_name": team["name"],
            "serial": team["serial"],
            "samples": stats["sample_count"],
            "route_distance_km": stats["distance_km"],
            "route_distance_mi": round((stats["distance_km"] or 0.0) / 1.609344, 1),
            "straight_distance_km": stats["straight_distance_km"],
            "moving_time_hours": round((stats["moving_time_sec"] or 0) / 3600.0, 2),
            "avg_speed_kph": stats["average_speed_kph"],
            "moving_avg_speed_kph": stats["moving_average_speed_kph"],
            "max_speed_kph": stats["max_speed_kph"],
            "active_ratio_pct": stats["active_ratio_pct"],
            "anomaly_count": stats["anomaly_count"],
            "gap_count": stats["gap_count"],
            "largest_gap_sec": stats["largest_gap_sec"],
            "first_sample": stats["start_time"],
            "last_sample": stats["end_time"],
        })
    return rows


def write_teammate_outputs(output_root, raw_output, manifest):
    daily_rows = build_daily_coverage_rows(raw_output)
    team_rows = build_team_metric_rows(raw_output)

    write_csv(
        output_root / "daily_coverage.csv",
        ["date", "update_records", "snapshot_records", "weather_records", "approved_pins"],
        daily_rows,
    )
    write_csv(
        output_root / "team_metrics.csv",
        [
            "team_name",
            "serial",
            "samples",
            "route_distance_km",
            "route_distance_mi",
            "straight_distance_km",
            "moving_time_hours",
            "avg_speed_kph",
            "moving_avg_speed_kph",
            "max_speed_kph",
            "active_ratio_pct",
            "anomaly_count",
            "gap_count",
            "largest_gap_sec",
            "first_sample",
            "last_sample",
        ],
        team_rows,
    )

    summary = {
        "created_at": manifest["created_at"],
        "totals": manifest["totals"],
        "coverage_dates": [row["date"] for row in daily_rows],
        "team_count": len(team_rows),
        "top_teams_by_distance_km": [
            {
                "team_name": row["team_name"],
                "route_distance_km": row["route_distance_km"],
                "anomaly_count": row["anomaly_count"],
            }
            for row in team_rows[:10]
        ],
        "notes": [
            "dataset/raw is the portable source of truth committed to Git.",
            "restore_dataset.py can recreate a working raw folder from the tracked archive.",
            "Snapshot JSON objects are preserved losslessly, though restored formatting may differ from the original pretty-printed files.",
        ],
    }
    with open(output_root / "summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")


def summarize_manifest(manifest):
    totals = defaultdict(int)
    for package in manifest["packages"]:
        totals["package_count"] += 1
        totals["record_count"] += int(package.get("record_count", 0))
        totals["original_bytes"] += int(package.get("original_bytes", 0))
        totals["archived_bytes"] += int(package.get("archived_bytes", 0))
    manifest["totals"] = dict(totals)
    manifest["created_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def build_dataset(base_dir, output_root):
    base_dir = Path(base_dir).resolve()
    output_root = Path(output_root).resolve()
    raw_output = output_root / "raw"
    existing_snapshot_records = load_existing_snapshot_archive(output_root / "raw" / "snapshots")
    ensure_clean_dir(raw_output)

    manifest = {
        "root": str(output_root),
        "source_root": str(base_dir),
        "packages": [],
    }

    data_dir = base_dir / "data"
    package_jsonl_group(sorted(data_dir.glob("updates_*.jsonl")), raw_output / "updates", manifest, "updates")
    package_jsonl_group(sorted(data_dir.glob("weather_*.jsonl")), raw_output / "weather", manifest, "weather")
    package_pins(sorted(data_dir.glob("pins_*.json")), raw_output / "pins", manifest)

    snapshot_sources = sorted(data_dir.glob("traces_*.json")) + sorted(base_dir.glob("traces_*.json"))
    package_snapshots(snapshot_sources, raw_output / "snapshots", manifest, existing_snapshot_records)
    package_log(base_dir / "collector.log", raw_output / "logs", manifest)

    manifest_path = output_root / "manifest.json"
    readme_path = output_root / "README.md"
    guide_path = output_root / "TEAMMATE_GUIDE.md"
    manifest = summarize_manifest(manifest)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    write_teammate_outputs(output_root, raw_output, manifest)
    readme_path.write_text(
        "\n".join([
            "# ASC 2026 archival dataset",
            "",
            "This folder contains a Git-friendly, lossless archive of the local ASC tracker capture.",
            "",
            "- `raw/updates/*.jsonl.gz`: raw location updates, one gzip file per day",
            "- `raw/snapshots/*.jsonl.gz`: full tracker snapshots consolidated per day",
            "- `raw/weather/*.jsonl.gz`: weather samples per day",
            "- `raw/pins/*.json`: approved route pins per day",
            "- `raw/logs/*.log.gz`: compressed collector log archive",
            "- `manifest.json`: package-level inventory, counts, and source integrity hashes",
            "- `summary.json`: quick machine-readable overview for teammates or scripts",
            "- `daily_coverage.csv`: one row per race day",
            "- `team_metrics.csv`: one row per team with route metrics and quality signals",
            "",
            "The original runtime `data/` folder can stay ignored for live collection. This dataset is the tracked archive meant for Git.",
            "If the local raw folder is gone, run `python3 restore_dataset.py` from the ASC Data Collection directory to recreate a working copy from this archive.",
            "",
        ]),
        encoding="utf-8",
    )
    guide_path.write_text(
        "\n".join([
            "# Teammate guide",
            "",
            "Start here if you just cloned the repo and want to understand the ASC capture quickly.",
            "",
            "## Fastest files to open",
            "",
            "1. `summary.json` for a quick machine-readable overview",
            "2. `daily_coverage.csv` to see how much data exists per day",
            "3. `team_metrics.csv` to compare team routes and data quality",
            "4. `../asc_data_portfolio.html` for the visual overview",
            "5. `../asc_trajectory.html` for the route replay",
            "",
            "## Source of truth",
            "",
            "- Everything under `raw/` is the portable archive committed to Git.",
            "- The ignored `../data/` folder is only the local runtime shape used during live collection.",
            "- `restore_dataset.py` can rebuild a working raw folder from the tracked archive.",
            "",
            "## Notes",
            "",
            "- Update, weather, pin, and log content are preserved losslessly.",
            "- Snapshot objects are preserved losslessly, though restored JSON formatting may not match the original pretty-printed files byte-for-byte.",
            "- Distances in derived CSVs are local calculations from captured coordinates, not official ASC progress values.",
            "",
        ]),
        encoding="utf-8",
    )

    return manifest_path


def main():
    parser = argparse.ArgumentParser(description="Build a Git-friendly archival ASC dataset.")
    parser.add_argument(
        "--base-dir",
        default=Path(__file__).resolve().parent,
        help="ASC Data Collection directory (default: script directory)",
    )
    parser.add_argument(
        "--output",
        default=Path(__file__).resolve().parent / "dataset",
        help="Output dataset directory (default: %(default)s)",
    )
    args = parser.parse_args()

    manifest_path = build_dataset(args.base_dir, args.output)
    print(f"Built dataset manifest: {manifest_path}")


if __name__ == "__main__":
    main()
