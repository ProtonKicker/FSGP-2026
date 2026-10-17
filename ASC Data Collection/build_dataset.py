#!/usr/bin/env python3
"""
Build a tracked, Git-friendly archival dataset from the local ASC raw capture.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


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


def package_snapshots(source_paths, output_dir, manifest):
    grouped = defaultdict(list)
    for source_path in source_paths:
        day = source_path.name.split("_", 1)[1].split("T", 1)[0]
        grouped[day].append(source_path)

    for day, paths in sorted(grouped.items()):
        output_path = output_dir / f"snapshots_{day}.jsonl.gz"
        record_count = 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(output_path, "wt", encoding="utf-8") as handle:
            for source_path in sorted(paths):
                with open(source_path, "r", encoding="utf-8") as source_handle:
                    record = json.load(source_handle)
                handle.write(json.dumps(record, separators=(",", ":")))
                handle.write("\n")
                record_count += 1

        manifest["packages"].append({
            "kind": "snapshots",
            "date": day,
            "source_file_count": len(paths),
            "source_file_first": str(sorted(paths)[0].relative_to(manifest["source_root"])),
            "source_file_last": str(sorted(paths)[-1].relative_to(manifest["source_root"])),
            "output_file": str(output_path.relative_to(manifest["root"])),
            "record_count": record_count,
            "original_bytes": sum(path.stat().st_size for path in paths),
            "archived_bytes": output_path.stat().st_size,
            "source_sha256": aggregate_sha256(paths),
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
    package_snapshots(snapshot_sources, raw_output / "snapshots", manifest)
    package_log(base_dir / "collector.log", raw_output / "logs", manifest)

    manifest_path = output_root / "manifest.json"
    readme_path = output_root / "README.md"
    manifest = summarize_manifest(manifest)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
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
            "",
            "The original runtime `data/` folder can stay ignored for live collection. This dataset is the tracked archive meant for Git.",
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
