# Teammate guide

Start here if you just cloned the repo and want to understand the ASC capture quickly.

## Fastest files to open

1. `summary.json` for a quick machine-readable overview
2. `daily_coverage.csv` to see how much data exists per day
3. `team_metrics.csv` to compare team routes and data quality
4. `../asc_data_portfolio.html` for the visual overview
5. `../asc_trajectory.html` for the route replay

## Source of truth

- Everything under `raw/` is the portable archive committed to Git.
- The ignored `../data/` folder is only the local runtime shape used during live collection.
- `restore_dataset.py` can rebuild a working raw folder from the tracked archive.

## Notes

- Update, weather, pin, and log content are preserved losslessly.
- Snapshot objects are preserved losslessly, though restored JSON formatting may not match the original pretty-printed files byte-for-byte.
- Distances in derived CSVs are local calculations from captured coordinates, not official ASC progress values.
