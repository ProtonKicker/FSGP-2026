# ASC Data Collection

This folder now has two layers:

- `data/` and `collector.log`: the original local runtime capture, still useful for live collection and local forensics
- `dataset/`: the tracked Git-friendly archive of that capture, compressed losslessly and organized by component

If you delete the local runtime capture, the pushed archive is still enough to keep the information:

- `dataset/raw/` is the Git-tracked source of truth
- `restore_dataset.py` rebuilds a working raw layout from the tracked archive
- `dataset/TEAMMATE_GUIDE.md`, `dataset/summary.json`, `dataset/daily_coverage.csv`, and `dataset/team_metrics.csv` are the quickest handoff files

Key outputs:

- `asc_comprehensive.html`: all-in-one handoff page with archive overview, current status table, and route replay
- `asc_data_portfolio.html`: portfolio-style overview of the dataset, capture cadence, weather, pins, routes, and log behavior
- `asc_trajectory.html`: interactive route replay across all teams
- `asc_dashboard.html`: compact latest-state dashboard

To rebuild the tracked archive and HTML outputs:

```bash
python3 build_dataset.py
python3 restore_dataset.py
python3 comprehensive.py
python3 portfolio.py
python3 visualize.py -o asc_trajectory.html
python3 dashboard.py -o asc_dashboard.html
```
