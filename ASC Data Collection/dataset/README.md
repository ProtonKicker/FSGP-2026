# ASC 2026 archival dataset

This folder contains a Git-friendly, lossless archive of the local ASC tracker capture.

- `raw/updates/*.jsonl.gz`: raw location updates, one gzip file per day
- `raw/snapshots/*.jsonl.gz`: full tracker snapshots consolidated per day
- `raw/weather/*.jsonl.gz`: weather samples per day
- `raw/pins/*.json`: approved route pins per day
- `raw/logs/*.log.gz`: compressed collector log archive
- `manifest.json`: package-level inventory, counts, and source integrity hashes

The original runtime `data/` folder can stay ignored for live collection. This dataset is the tracked archive meant for Git.
