# ASC 2026 archival dataset

This folder contains a Git-friendly, lossless archive of the local ASC tracker capture.

- `raw/updates/*.jsonl.gz`: raw location updates, one gzip file per day
- `raw/snapshots/*.jsonl.gz`: full tracker snapshots consolidated per day
- `raw/weather/*.jsonl.gz`: weather samples per day
- `raw/pins/*.json`: approved route pins per day
- `raw/logs/*.log.gz`: compressed collector log archive
- `manifest.json`: package-level inventory, counts, and source integrity hashes
- `summary.json`: quick machine-readable overview for teammates or scripts
- `daily_coverage.csv`: one row per race day
- `team_metrics.csv`: one row per team with route metrics and quality signals

The original runtime `data/` folder can stay ignored for live collection. This dataset is the tracked archive meant for Git.
If the local raw folder is gone, run `python3 restore_dataset.py` from the ASC Data Collection directory to recreate a working copy from this archive.
