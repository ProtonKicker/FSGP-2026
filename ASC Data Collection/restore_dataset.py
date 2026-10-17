#!/usr/bin/env python3
"""
Restore a working raw-data folder from the tracked ASC archival dataset.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from pathlib import Path


def gunzip_file(source_path, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(source_path, "rb") as src, open(output_path, "wb") as dst:
        shutil.copyfileobj(src, dst)


def trace_filename_from_timestamp(timestamp):
    if not timestamp:
        raise ValueError("Snapshot record missing timestamp")
    local = timestamp[:19].replace(":", "-")
    return f"traces_{local}.json"


def restore_dataset(dataset_dir, output_dir):
    dataset_dir = Path(dataset_dir).resolve()
    output_dir = Path(output_dir).resolve()
    raw_dir = dataset_dir / "raw"
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    restored = {"updates": 0, "weather": 0, "pins": 0, "snapshots": 0, "logs": 0}

    for source_path in sorted((raw_dir / "updates").glob("*.jsonl.gz")):
        output_path = data_dir / source_path.name.removesuffix(".gz")
        gunzip_file(source_path, output_path)
        restored["updates"] += 1

    for source_path in sorted((raw_dir / "weather").glob("*.jsonl.gz")):
        output_path = data_dir / source_path.name.removesuffix(".gz")
        gunzip_file(source_path, output_path)
        restored["weather"] += 1

    for source_path in sorted((raw_dir / "pins").glob("*.json")):
        output_path = data_dir / source_path.name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, output_path)
        restored["pins"] += 1

    for source_path in sorted((raw_dir / "snapshots").glob("*.jsonl.gz")):
        with gzip.open(source_path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                output_name = trace_filename_from_timestamp(record.get("timestamp"))
                output_path = data_dir / output_name
                with open(output_path, "w", encoding="utf-8") as out:
                    json.dump(record, out, indent=2)
                    out.write("\n")
                restored["snapshots"] += 1

    for source_path in sorted((raw_dir / "logs").glob("*.log.gz")):
        output_path = output_dir / source_path.name.removesuffix(".gz")
        gunzip_file(source_path, output_path)
        restored["logs"] += 1

    return restored


def main():
    parser = argparse.ArgumentParser(description="Restore raw ASC files from the tracked archival dataset.")
    parser.add_argument(
        "--dataset",
        default=Path(__file__).resolve().parent / "dataset",
        help="Path to the tracked dataset directory",
    )
    parser.add_argument(
        "--output",
        default=Path(__file__).resolve().parent / "restored_from_dataset",
        help="Directory where restored raw files should be written",
    )
    args = parser.parse_args()

    restored = restore_dataset(args.dataset, args.output)
    print(f"Restored dataset into {Path(args.output).resolve()}")
    for key, value in restored.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
