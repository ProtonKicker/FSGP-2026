# ASC Data Collection

This folder now has two layers:

- `data/` and `collector.log`: the original local runtime capture, still useful for live collection and local forensics
- `dataset/`: the tracked Git-friendly archive of that capture, compressed losslessly and organized by component

Key outputs:

- `asc_data_portfolio.html`: portfolio-style overview of the dataset, capture cadence, weather, pins, routes, and log behavior
- `asc_trajectory.html`: interactive route replay across all teams
- `asc_dashboard.html`: compact latest-state dashboard

To rebuild the tracked archive and HTML outputs:

```bash
python3 build_dataset.py
python3 portfolio.py
python3 visualize.py -o asc_trajectory.html
python3 dashboard.py -o asc_dashboard.html
```
