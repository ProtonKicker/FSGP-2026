#!/usr/bin/env python3
"""
Generate a single comprehensive ASC handoff page.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import dashboard
import data_access
import portfolio

DEFAULT_OUTPUT = "asc_comprehensive.html"
DEFAULT_MANIFEST = "dataset/manifest.json"


def unit_value(metric, imperial):
    return (
        f'<span class="unit-value" data-metric="{html.escape(metric)}" '
        f'data-imperial="{html.escape(imperial)}">{html.escape(metric)}</span>'
    )


def render_overview_cards(manifest, snapshots, teams, pins, log_summary):
    archive_bytes = manifest["totals"]["archived_bytes"] if manifest else 0
    original_bytes = manifest["totals"]["original_bytes"] if manifest else 0
    cards = [
        ("Tracked archive", portfolio.fmt_bytes(archive_bytes)),
        ("Original source", portfolio.fmt_bytes(original_bytes)),
        ("Snapshot records", portfolio.fmt_int(sum(item["count"] for item in snapshots))),
        ("Teams", portfolio.fmt_int(len(teams))),
        ("Pins", portfolio.fmt_int(len(pins))),
        ("Log lines", portfolio.fmt_int(log_summary["line_count"])),
    ]
    return "".join(
        f"""
        <article class="stat-card">
          <div class="eyebrow">{html.escape(label)}</div>
          <div class="stat-value">{html.escape(value)}</div>
        </article>
        """
        for label, value in cards
    )


def render_daily_rows(daily_counts):
    peak_updates = max((row["updates"] for row in daily_counts.values()), default=1)
    rows = []
    for day, counts in daily_counts.items():
        rows.append(
            f"""
            <tr>
              <td>{html.escape(day)}</td>
              <td>
                <div class="bar-cell">
                  <span>{portfolio.fmt_int(counts['updates'])}</span>
                  <div class="bar"><div style="width:{(counts['updates'] / peak_updates) * 100:.1f}%"></div></div>
                </div>
              </td>
              <td>{portfolio.fmt_int(counts['snapshots'])}</td>
              <td>{portfolio.fmt_int(counts['weather'])}</td>
              <td>{portfolio.fmt_int(counts['pins'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_weather_rows(weather_daily):
    rows = []
    for row in weather_daily:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(row['day'])}</td>
              <td>{portfolio.fmt_int(row['samples'])}</td>
              <td>{unit_value(f"{portfolio.fmt_float(row['temp_min'])} C", f"{portfolio.fmt_float(portfolio.f_temp(row['temp_min']))} F") if row['temp_min'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{portfolio.fmt_float(row['temp_avg'])} C", f"{portfolio.fmt_float(portfolio.f_temp(row['temp_avg']))} F") if row['temp_avg'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{portfolio.fmt_float(row['temp_max'])} C", f"{portfolio.fmt_float(portfolio.f_temp(row['temp_max']))} F") if row['temp_max'] is not None else 'N/A'}</td>
              <td>{unit_value(f"{portfolio.fmt_float(row['wind_avg'])} km/h", f"{portfolio.fmt_float(portfolio.mph(row['wind_avg']))} mph") if row['wind_avg'] is not None else 'N/A'}</td>
              <td>{html.escape(row['top_condition'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_archive_rows(manifest):
    if not manifest:
        return ""
    rows = []
    for package in sorted(manifest.get("packages", []), key=lambda item: (item["kind"], item["date"])):
        rows.append(
            f"""
            <tr>
              <td>{html.escape(package['kind'])}</td>
              <td>{html.escape(package['date'])}</td>
              <td><code>{html.escape(package['output_file'])}</code></td>
              <td>{portfolio.fmt_int(package['record_count'])}</td>
              <td>{portfolio.fmt_bytes(package['original_bytes'])}</td>
              <td>{portfolio.fmt_bytes(package['archived_bytes'])}</td>
            </tr>
            """
        )
    return "".join(rows)


def render_capture_mix(daily_counts, log_summary):
    totals = {
        "GPS updates": sum(counts["updates"] for counts in daily_counts.values()),
        "Snapshots": sum(counts["snapshots"] for counts in daily_counts.values()),
        "Weather": sum(counts["weather"] for counts in daily_counts.values()),
        "Pins": sum(counts["pins"] for counts in daily_counts.values()),
    }
    core_total = sum(totals.values()) or 1
    max_core = max(totals.values()) or 1
    cards = [
        ("GPS updates", totals["GPS updates"], "Route points"),
        ("Snapshots", totals["Snapshots"], "Whole field states"),
        ("Weather", totals["Weather"], "Conditions"),
        ("Pins", totals["Pins"], "Hazards + notes"),
    ]
    rows = []
    for label, value, note in cards:
        share = (value / core_total) * 100
        width = max(10, (value / max_core) * 100) if value else 0
        rows.append(
            f"""
            <article class="capture-card">
              <div class="capture-topline">
                <span class="eyebrow">{html.escape(label)}</span>
                <span class="capture-share">{share:.1f}%</span>
              </div>
              <div class="capture-value">{portfolio.fmt_int(value)}</div>
              <div class="capture-note">{html.escape(note)}</div>
              <div class="capture-meter"><div style="width:{width:.1f}%"></div></div>
            </article>
            """
        )
    rows.append(
        f"""
        <article class="capture-card capture-card-log">
          <div class="capture-topline">
            <span class="eyebrow">Collector log</span>
            <span class="capture-share">always on</span>
          </div>
          <div class="capture-value">{portfolio.fmt_int(log_summary["line_count"])}</div>
          <div class="capture-note">Reconnects, polling, write activity</div>
          <div class="capture-meter"><div style="width:100%"></div></div>
        </article>
        """
    )
    return "".join(rows)


def render_activity_heatmap(daily_counts):
    kinds = [
        ("GPS", "updates"),
        ("Snap", "snapshots"),
        ("Weather", "weather"),
        ("Pins", "pins"),
    ]
    days = list(daily_counts.keys())
    max_by_kind = {
        key: max((daily_counts[day][key] for day in days), default=1) or 1
        for _, key in kinds
    }

    header = "".join(
        f'<div class="heatmap-header-cell">{html.escape(day[5:].replace("-", "/"))}</div>'
        for day in days
    )
    rows = []
    for label, key in kinds:
        cells = []
        for day in days:
            value = daily_counts[day][key]
            strength = value / max_by_kind[key] if max_by_kind[key] else 0
            cells.append(
                f"""
                <div class="heatmap-cell" style="--strength:{strength:.4f}">
                  <span class="heatmap-count">{portfolio.fmt_int(value)}</span>
                </div>
                """
            )
        rows.append(
            f"""
            <div class="heatmap-row-label">{html.escape(label)}</div>
            {''.join(cells)}
            """
        )
    return f"""
    <div class="heatmap-grid">
      <div class="heatmap-corner"></div>
      {header}
      {''.join(rows)}
    </div>
    """


def render_route_leaders(route_teams):
    ranked = sorted(
        route_teams,
        key=lambda team: team["stats"].get("distance_km", 0),
        reverse=True,
    )[:8]
    peak = max((team["stats"].get("distance_km", 0) for team in ranked), default=1) or 1
    rows = []
    for team in ranked:
        stats = team["stats"]
        distance_km = stats.get("distance_km", 0)
        anomaly_count = stats.get("anomaly_count", 0)
        sample_count = stats.get("sample_count", 0)
        width = (distance_km / peak) * 100 if peak else 0
        rows.append(
            f"""
            <article class="leader-row">
              <div class="leader-main">
                <div class="leader-name">
                  <span class="leader-dot" style="background:{html.escape(team['color'])}"></span>
                  <span>{html.escape(team['name'])}</span>
                </div>
                <div class="leader-distance">{unit_value(f"{portfolio.fmt_float(distance_km)} km", f"{portfolio.fmt_float(distance_km / 1.609344)} mi")}</div>
              </div>
              <div class="leader-bar"><div style="width:{width:.1f}%; background:{html.escape(team['color'])}"></div></div>
              <div class="leader-meta">
                <span>{portfolio.fmt_int(sample_count)} samples</span>
                <span>{portfolio.fmt_int(anomaly_count)} alerts</span>
              </div>
            </article>
            """
        )
    return "".join(rows)


def generate_html(data_dir, manifest_path):
    manifest = portfolio.load_manifest(manifest_path)
    route_teams = data_access.load_updates(data_dir)
    snapshots = data_access.load_snapshots(data_dir)
    pins = data_access.load_pins(data_dir)
    weather_records = list(data_access.iter_weather_records(data_dir))
    pin_records = list(data_access.iter_pin_snapshots(data_dir))
    daily_counts = portfolio.summarize_daily_counts(data_dir, snapshots, weather_records, pin_records)
    weather_daily = portfolio.summarize_weather(weather_records)
    latest_teams, _, _ = dashboard.load_latest(data_dir)
    latest_weather = dashboard.load_latest_weather(data_dir)
    log_summary = portfolio.summarize_log(Path(data_dir) / "logs" / "collector_2026-07-26_to_2026-08-01.log.gz")

    route_payload = [
        {
            "serial": team["serial"],
            "name": team["name"],
            "color": team["color"],
            "points": portfolio.simplify_points(team["points"]),
            "stats": team["stats"],
        }
        for team in route_teams
    ]

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASC Comprehensive</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ box-sizing: border-box; }}
:root {{
  color-scheme: light;
  --bg: #F7F5F1;
  --bg-warm: #F3EFE8;
  --card: rgba(255, 255, 255, 0.72);
  --card-solid: #FFFFFF;
  --card-soft: rgba(255, 255, 255, 0.56);
  --line: rgba(28, 27, 31, 0.08);
  --line-soft: rgba(28, 27, 31, 0.05);
  --text: #1C1B1F;
  --muted: #6B6966;
  --muted-soft: #9B9994;
  --accent: #5B7FFF;
  --accent-soft: rgba(91, 127, 255, 0.12);
  --danger: #C85F56;
  --shadow-soft: 0 2px 8px rgba(20, 20, 30, 0.04),
                 0 10px 32px rgba(20, 20, 30, 0.06);
  --shadow-hover: 0 4px 16px rgba(20, 20, 30, 0.06),
                  0 18px 48px rgba(20, 20, 30, 0.09);
  --radius-xl: 28px;
  --radius-lg: 22px;
  --radius-md: 16px;
  --ease-spring: cubic-bezier(0.16, 1, 0.3, 1);
}}
html, body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
body {{
  padding: 22px 0 40px;
  min-height: 100vh;
  overflow-x: hidden;
  position: relative;
}}
.bg-aurora {{
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}}
.blob {{
  position: absolute;
  border-radius: 50%;
  filter: blur(88px);
  opacity: 0.5;
  mix-blend-mode: multiply;
}}
.blob-1 {{
  width: 520px;
  height: 520px;
  top: -170px;
  left: -130px;
  background: radial-gradient(circle at 30% 30%, #E8DFFB, transparent 60%);
}}
.blob-2 {{
  width: 460px;
  height: 460px;
  right: -90px;
  bottom: -150px;
  background: radial-gradient(circle at 70% 40%, #FBE7D5, transparent 60%);
}}
.blob-3 {{
  width: 390px;
  height: 390px;
  left: 50%;
  top: 42%;
  transform: translate(-50%, -50%);
  background: radial-gradient(circle at 50% 50%, #DBE8FF, transparent 60%);
  opacity: 0.35;
}}
.page {{
  position: relative;
  z-index: 1;
  width: min(1380px, calc(100% - 28px));
  margin: 0 auto;
}}
.topbar, .hero, .section {{
  background: var(--card);
  border: 1px solid rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(18px) saturate(1.15);
  -webkit-backdrop-filter: blur(18px) saturate(1.15);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-soft);
}}
.topbar {{
  padding: 18px 20px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}}
.brand {{ display: grid; gap: 4px; }}
.eyebrow {{
  color: var(--muted-soft);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-size: 0.72rem;
  font-weight: 600;
}}
.title {{
  font-size: 1.2rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}}
.top-actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}}
.chip, .link-chip, .toggle button, select, button {{
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.68);
  color: var(--text);
  border-radius: 999px;
  padding: 10px 14px;
  font: inherit;
  text-decoration: none;
  transition: transform 320ms var(--ease-spring), box-shadow 320ms var(--ease-spring), border-color 220ms ease, background 220ms ease;
}}
.link-chip:hover, button:hover, select:hover {{
  transform: translateY(-1px);
  box-shadow: var(--shadow-soft);
}}
.link-chip:hover, button:hover, select:hover, .team-chip:hover, .anomaly-item:hover, .file-link:hover {{
  border-color: rgba(91, 127, 255, 0.18);
}}
.toggle {{
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.68);
}}
.toggle button {{
  border: none;
  background: transparent;
  color: var(--muted);
  padding: 8px 12px;
}}
.toggle button.active {{
  background: var(--accent-soft);
  color: var(--accent);
}}
.hero {{
  padding: 30px;
  margin-bottom: 18px;
}}
.hero-grid {{
  display: grid;
  grid-template-columns: 1.45fr 1fr;
  gap: 20px;
}}
.hero h1 {{
  margin: 10px 0 14px;
  font-size: clamp(2.35rem, 5.3vw, 4.7rem);
  font-weight: 800;
  line-height: 0.93;
  letter-spacing: -0.05em;
  max-width: 12ch;
}}
.hero p, .section-copy {{
  margin: 0;
  color: var(--muted);
  line-height: 1.68;
  max-width: 62ch;
}}
.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-top: 18px;
}}
.stat-card {{
  padding: 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255,255,255,0.86), rgba(255,255,255,0.52));
}}
.stat-value {{
  margin-top: 8px;
  font-size: 1.3rem;
  font-weight: 700;
  letter-spacing: -0.03em;
}}
.hero-links {{
  display: grid;
  gap: 10px;
}}
.file-link {{
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  color: var(--text);
  text-decoration: none;
  background: linear-gradient(180deg, rgba(255,255,255,0.8), rgba(255,255,255,0.56));
  transition: transform 320ms var(--ease-spring), box-shadow 320ms var(--ease-spring), border-color 220ms ease;
}}
.file-link span:first-child {{
  font-weight: 600;
  letter-spacing: -0.01em;
}}
.file-link span:last-child {{ color: var(--muted); white-space: nowrap; }}
.section {{
  padding: 24px;
  margin-top: 18px;
}}
.section-header {{
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 12px;
  margin-bottom: 14px;
}}
.section-title {{
  margin: 0;
  font-size: 1.28rem;
  font-weight: 650;
  letter-spacing: -0.02em;
}}
.insight-grid {{
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 16px;
}}
.visual-panel {{
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  background: rgba(255,255,255,0.62);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4);
  padding: 16px;
}}
.capture-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}}
.capture-card {{
  padding: 16px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255,255,255,0.84), rgba(255,255,255,0.58));
}}
.capture-card-log {{
  grid-column: 1 / -1;
}}
.capture-topline, .leader-main, .leader-meta {{
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: baseline;
}}
.capture-share {{
  color: var(--muted-soft);
  font-size: 0.82rem;
  font-weight: 600;
}}
.capture-value {{
  margin-top: 10px;
  font-size: clamp(1.35rem, 2.4vw, 2rem);
  font-weight: 750;
  letter-spacing: -0.04em;
}}
.capture-note {{
  margin-top: 4px;
  color: var(--muted);
  font-size: 0.84rem;
}}
.capture-meter, .leader-bar {{
  height: 10px;
  margin-top: 14px;
  border-radius: 999px;
  background: #ece9e1;
  overflow: hidden;
  }}
.capture-meter > div, .leader-bar > div {{
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #8CA9FF);
  }}
.heatmap-grid {{
  display: grid;
  grid-template-columns: 72px repeat(8, minmax(0, 1fr));
  gap: 8px;
  align-items: stretch;
}}
.heatmap-corner {{
  min-height: 44px;
}}
.heatmap-header-cell {{
  color: var(--muted-soft);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  text-align: center;
  align-self: end;
  padding-bottom: 4px;
  }}
.heatmap-row-label {{
  color: var(--muted);
  font-size: 0.82rem;
  font-weight: 600;
  align-self: center;
  }}
.heatmap-cell {{
  min-height: 74px;
  border-radius: 18px;
  border: 1px solid rgba(91,127,255,0.10);
  background:
    linear-gradient(180deg, rgba(255,255,255,0.75), rgba(255,255,255,0.55)),
    linear-gradient(180deg, rgba(91,127,255,calc(var(--strength) * 0.34)), rgba(91,127,255,calc(var(--strength) * 0.14)));
  display: grid;
  place-items: center;
  padding: 8px;
  text-align: center;
  }}
.heatmap-count {{
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  }}
.leaders-panel {{
  margin-top: 16px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  background: rgba(255,255,255,0.62);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4);
  padding: 16px;
  }}
.leader-list {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}}
.leader-row {{
  padding: 14px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line-soft);
  background: linear-gradient(180deg, rgba(255,255,255,0.82), rgba(255,255,255,0.58));
}}
.leader-name {{
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-weight: 650;
}}
.leader-dot {{
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: 0 0 auto;
}}
.leader-distance {{
  font-weight: 700;
  letter-spacing: -0.02em;
}}
.leader-meta {{
  margin-top: 8px;
  color: var(--muted);
  font-size: 0.78rem;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
}}
.route-layout {{
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) 360px;
  gap: 16px;
}}
.route-main {{
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 12px;
}}
.route-controls, .route-panel {{
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-lg);
  background: rgba(255,255,255,0.62);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4);
}}
.route-controls {{
  padding: 14px;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  align-items: end;
}}
.route-controls .field {{
  display: grid;
  gap: 6px;
}}
.route-controls label {{
  color: var(--muted);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.route-buttons {{
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}}
.route-buttons button, .route-controls select {{
  width: 100%;
  border-radius: 14px;
}}
#route-map {{
  min-height: 620px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--line-soft);
  background:
    linear-gradient(rgba(28,27,31,0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(28,27,31,0.04) 1px, transparent 1px),
    radial-gradient(circle at top, rgba(91,127,255,0.08), transparent 56%),
    radial-gradient(circle at 80% 0%, rgba(251,231,213,0.42), transparent 38%),
    #fbf9f5;
  background-size: 48px 48px, 48px 48px, auto, auto;
}}
.route-bottom {{
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 10px;
  align-items: center;
}}
#time-slider {{ width: 100%; }}
.route-sidebar {{
  display: grid;
  gap: 12px;
  align-content: start;
}}
.route-panel {{ padding: 14px; }}
.panel-title {{
  margin: 0 0 10px;
  color: var(--muted-soft);
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
}}
.summary-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}}
.summary-card {{
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 10px;
  background: rgba(255,255,255,0.75);
}}
.summary-label {{
  color: var(--muted-soft);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.summary-value {{
  margin-top: 6px;
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}}
.summary-note {{
  margin-top: 4px;
  color: var(--muted);
  font-size: 0.74rem;
}}
#team-roster, #anomaly-list {{
  display: grid;
  gap: 8px;
  max-height: 270px;
  overflow-y: auto;
}}
.team-chip, .anomaly-item {{
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: rgba(255,255,255,0.75);
  padding: 10px;
  cursor: pointer;
  transition: transform 260ms var(--ease-spring), box-shadow 260ms var(--ease-spring), border-color 220ms ease;
}}
.team-chip.active {{
  border-color: rgba(91, 127, 255, 0.45);
  box-shadow: 0 0 0 1px rgba(91,127,255,0.12);
}}
.team-chip {{
  display: grid;
  grid-template-columns: 12px 1fr auto;
  gap: 8px;
  align-items: center;
}}
.team-dot {{
  width: 10px;
  height: 10px;
  border-radius: 50%;
}}
.team-meta, .anomaly-meta, .muted-text {{
  color: var(--muted);
  font-size: 0.76rem;
}}
.anomaly-item {{ border-color: rgba(200,95,86,0.20); }}
.anomaly-title {{ font-weight: 650; color: var(--danger); margin-bottom: 4px; }}
.topline {{
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}}
.topline-item {{
  display: grid;
  gap: 2px;
}}
.topline-item span:first-child {{
  color: var(--muted-soft);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.topline-item span:last-child {{
  font-weight: 650;
  letter-spacing: -0.02em;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}}
th, td {{
  padding: 12px 10px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: top;
}}
th {{
  color: var(--muted-soft);
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
tr:hover td {{ background: rgba(91,127,255,0.04); }}
.bar-cell {{
  display: grid;
  grid-template-columns: 84px 1fr;
  gap: 10px;
  align-items: center;
}}
.bar {{
  height: 7px;
  border-radius: 999px;
  background: #ece9e1;
  overflow: hidden;
}}
.bar > div {{
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), #8CA9FF);
}}
.tracker-marker {{
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid rgba(255,255,255,0.95);
  box-shadow: 0 0 0 1px rgba(35,31,24,0.22);
}}
.tracker-marker.focused {{
  width: 20px;
  height: 20px;
  border-width: 3px;
  box-shadow: 0 0 0 2px rgba(35,31,24,0.22);
}}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
@media (max-width: 1180px) {{
  .route-layout, .hero-grid, .two-col, .insight-grid, .leader-list {{ grid-template-columns: 1fr; }}
  .route-controls {{ grid-template-columns: 1fr 1fr; }}
  #route-map {{ min-height: 460px; }}
}}
@media (max-width: 760px) {{
  body {{ padding: 14px 0 28px; }}
  .page {{ width: min(100% - 16px, 1380px); }}
  .route-controls {{ grid-template-columns: 1fr; }}
  .summary-grid {{ grid-template-columns: 1fr; }}
  .capture-grid {{ grid-template-columns: 1fr; }}
  .heatmap-grid {{
    grid-template-columns: 56px repeat(8, minmax(0, 1fr));
    gap: 6px;
  }}
  .heatmap-cell {{
    min-height: 62px;
    padding: 6px;
  }}
  .file-link {{
    flex-direction: column;
    align-items: flex-start;
  }}
}}
</style>
</head>
<body>
<div class="bg-aurora" aria-hidden="true">
  <div class="blob blob-1"></div>
  <div class="blob blob-2"></div>
  <div class="blob blob-3"></div>
</div>
<div class="page">
  <header class="topbar">
    <div class="brand">
      <div class="eyebrow">ASC 2026 archive</div>
      <div class="title">Comprehensive handoff</div>
    </div>
    <div class="top-actions">
      <span class="chip">Portable archive</span>
      <a class="link-chip" href="dataset/TEAMMATE_GUIDE.md">Guide</a>
      <a class="link-chip" href="restore_dataset.py">Restore</a>
      <div class="toggle" aria-label="Unit system">
        <button id="metric-toggle" class="active" type="button">Metric</button>
        <button id="imperial-toggle" type="button">Imperial</button>
      </div>
    </div>
  </header>

  <section class="hero">
    <div class="hero-grid">
      <div>
        <div class="eyebrow">ASC data collection</div>
        <h1>Archive, field status, and replay in one calm page.</h1>
        <p>A light handoff view for the ASC tracker data, with the archive, current state, and replay in one place.</p>
        <div class="stats">{render_overview_cards(manifest, snapshots, route_teams, pins, log_summary)}</div>
      </div>
      <div class="hero-links">
        <a class="file-link" href="asc_data_portfolio.html"><span>Portfolio view</span><span>asc_data_portfolio.html</span></a>
        <a class="file-link" href="asc_dashboard.html"><span>Dashboard view</span><span>asc_dashboard.html</span></a>
        <a class="file-link" href="asc_trajectory.html"><span>Trajectory view</span><span>asc_trajectory.html</span></a>
        <a class="file-link" href="dataset/summary.json"><span>Summary</span><span>dataset/summary.json</span></a>
        <a class="file-link" href="dataset/team_metrics.csv"><span>Team metrics CSV</span><span>dataset/team_metrics.csv</span></a>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">What Got Logged</h2>
        <p class="section-copy">A quick visual read of the collection shape.</p>
      </div>
    </div>
    <div class="insight-grid">
      <div class="visual-panel">
        <div class="panel-title">Capture mix</div>
        <div class="capture-grid">{render_capture_mix(daily_counts, log_summary)}</div>
      </div>
      <div class="visual-panel">
        <div class="panel-title">Daily rhythm</div>
        {render_activity_heatmap(daily_counts)}
      </div>
    </div>
    <div class="leaders-panel">
      <div class="panel-title">Biggest route traces</div>
      <div class="leader-list">{render_route_leaders(route_teams)}</div>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Route Explorer</h2>
        <p class="section-copy">Replay, focus, compare, inspect.</p>
      </div>
    </div>
    <div class="route-layout">
      <div class="route-main">
        <div class="route-controls">
          <div class="field">
            <label for="focus-select">Focus team</label>
            <select id="focus-select"></select>
          </div>
          <div class="field">
            <label for="speed-select">Replay speed</label>
            <select id="speed-select">
              <option value="0.5">0.5x</option>
              <option value="1" selected>1x</option>
              <option value="2">2x</option>
              <option value="5">5x</option>
              <option value="10">10x</option>
              <option value="25">25x</option>
            </select>
          </div>
          <div class="field">
            <label>Playback</label>
            <div class="route-buttons">
              <button id="play-btn" type="button">Play</button>
            </div>
          </div>
          <div class="field">
            <label>Reset</label>
            <div class="route-buttons">
              <button id="reset-btn" type="button">Reset</button>
            </div>
          </div>
          <div class="field">
            <label>Frame</label>
            <div class="route-buttons">
              <button id="fit-all-btn" type="button">Fit all</button>
            </div>
          </div>
          <div class="field">
            <label>Focus</label>
            <div class="route-buttons">
              <button id="fit-focus-btn" type="button">Fit focus</button>
            </div>
          </div>
        </div>
        <div class="route-panel">
          <div class="topline">
            <div class="topline-item"><span>Loaded teams</span><span id="loaded-teams">--</span></div>
            <div class="topline-item"><span>Replay time</span><span id="anim-time-display">--</span></div>
            <div class="topline-item"><span>Coverage</span><span id="coverage-window">--</span></div>
            <div class="topline-item"><span>Pins</span><span id="pin-count">--</span></div>
            <div class="topline-item"><span>Samples</span><span id="sample-count">--</span></div>
          </div>
          <div id="route-map"></div>
        </div>
        <div class="route-bottom">
          <input type="range" id="time-slider" min="0" max="100" value="100" step="0.1">
          <span id="slider-time-label">--</span>
          <span id="status-pill">Archive replay ready</span>
        </div>
      </div>
      <div class="route-sidebar">
        <div class="route-panel">
          <div class="panel-title">Summary</div>
          <div class="summary-grid" id="summary-grid"></div>
        </div>
        <div class="route-panel">
          <div class="panel-title">Team roster</div>
          <div id="team-roster"></div>
        </div>
        <div class="route-panel">
          <div class="panel-title">Route alerts</div>
          <div id="anomaly-list"></div>
        </div>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="section-header">
      <div>
        <h2 class="section-title">Current Field Status</h2>
        <p class="section-copy">Latest snapshot plus latest archived weather.</p>
      </div>
    </div>
    <div class="topline" id="weather-inline"></div>
    <table>
      <thead>
        <tr>
          <th>Team</th>
          <th>Speed</th>
          <th>GPS path</th>
          <th>Geo finish distance</th>
          <th>Samples</th>
          <th>Heading</th>
          <th>Altitude</th>
          <th>Sat</th>
          <th>Position</th>
          <th>Snapshot / GPS</th>
        </tr>
      </thead>
      <tbody id="field-table-body"></tbody>
    </table>
  </section>

  <section class="section two-col">
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Daily Coverage</h2>
          <p class="section-copy">Coverage by day.</p>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Day</th><th>Updates</th><th>Snapshots</th><th>Weather</th><th>Pins</th></tr>
        </thead>
        <tbody>{render_daily_rows(daily_counts)}</tbody>
      </table>
    </div>
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Weather Summary</h2>
          <p class="section-copy">Daily weather.</p>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Day</th><th>Samples</th><th>Min</th><th>Avg</th><th>Max</th><th>Wind</th><th>Sky</th></tr>
        </thead>
        <tbody>{render_weather_rows(weather_daily)}</tbody>
      </table>
    </div>
  </section>

  <section class="section two-col">
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Portable Files</h2>
          <p class="section-copy">The files teammates actually need.</p>
        </div>
      </div>
      <div class="hero-links">
        <a class="file-link" href="dataset/TEAMMATE_GUIDE.md"><span>Teammate guide</span><span>dataset/TEAMMATE_GUIDE.md</span></a>
        <a class="file-link" href="dataset/summary.json"><span>Summary JSON</span><span>dataset/summary.json</span></a>
        <a class="file-link" href="dataset/daily_coverage.csv"><span>Daily coverage CSV</span><span>dataset/daily_coverage.csv</span></a>
        <a class="file-link" href="dataset/team_metrics.csv"><span>Team metrics CSV</span><span>dataset/team_metrics.csv</span></a>
        <a class="file-link" href="restore_dataset.py"><span>Restore script</span><span>restore_dataset.py</span></a>
      </div>
    </div>
    <div>
      <div class="section-header">
        <div>
          <h2 class="section-title">Collection Health</h2>
          <p class="section-copy">High-level collector stats only.</p>
        </div>
      </div>
      <div class="stats">
        <article class="stat-card"><div class="eyebrow">First log line</div><div class="stat-value">{html.escape(log_summary["first_ts"] or "N/A")}</div></article>
        <article class="stat-card"><div class="eyebrow">Last log line</div><div class="stat-value">{html.escape(log_summary["last_ts"] or "N/A")}</div></article>
        <article class="stat-card"><div class="eyebrow">Reconnects</div><div class="stat-value">{portfolio.fmt_int(log_summary["reconnects"])}</div></article>
        <article class="stat-card"><div class="eyebrow">Snapshot logs</div><div class="stat-value">{portfolio.fmt_int(log_summary["snapshot_logs"])}</div></article>
      </div>
      <div style="margin-top:12px" class="muted-text">Full log detail remains in the archive, not in the main handoff page.</div>
    </div>
  </section>
</div>

<script>
const ROUTE_TEAMS = {json.dumps(route_payload)};
const PINS = {json.dumps(pins)};
const LATEST_TEAMS = {json.dumps(latest_teams, default=str)};
const LATEST_WEATHER = {json.dumps(latest_weather, default=str) if latest_weather else "null"};
const SNAPSHOTS = {json.dumps(snapshots, default=str)};
let unitSystem = "metric";
let currentTime = 0;
let isPlaying = false;
let playbackSpeed = 1;
let lastFrameTime = 0;
let animationFrameId = null;
let focusedTeamKey = null;

const map = L.map("route-map", {{
  zoomControl: true,
  attributionControl: false,
}});
const routeLayer = L.layerGroup().addTo(map);
const markerLayer = L.layerGroup().addTo(map);
const pinLayer = L.layerGroup().addTo(map);
const anomalyLayer = L.layerGroup().addTo(map);
const teamLayers = new Map();

function formatDistance(value) {{
  if (value == null || Number.isNaN(value)) return "N/A";
  return unitSystem === "metric"
    ? `${{Number(value).toFixed(1)}} km`
    : `${{(value / 1.609344).toFixed(1)}} mi`;
}}

function formatSpeed(value) {{
  if (value == null || Number.isNaN(value)) return "N/A";
  return unitSystem === "metric"
    ? `${{Number(value).toFixed(1)}} km/h`
    : `${{(value / 1.609344).toFixed(1)}} mph`;
}}

function formatAltitude(value) {{
  if (value == null || Number.isNaN(value)) return "N/A";
  return unitSystem === "metric"
    ? `${{Number(value).toFixed(0)}} m`
    : `${{(value * 3.28084).toFixed(0)}} ft`;
}}

function formatTemp(value) {{
  if (value == null || Number.isNaN(value)) return "N/A";
  return unitSystem === "metric"
    ? `${{Number(value).toFixed(1)}} C`
    : `${{((value * 9 / 5) + 32).toFixed(1)}} F`;
}}

function secondsToDuration(seconds) {{
  if (seconds == null) return "N/A";
  const total = Math.max(0, Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours === 0 ? `${{minutes}} min` : `${{hours}}h ${{minutes}}m`;
}}

function formatTime(ts) {{
  if (!ts) return "No data";
  return new Date(ts * 1000).toLocaleString([], {{
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }});
}}

function formatIso(value) {{
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {{
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }});
}}

function getTeamKey(team) {{
  return team.serial || team.name;
}}

function applyUnitToggle() {{
  document.getElementById("metric-toggle").classList.toggle("active", unitSystem === "metric");
  document.getElementById("imperial-toggle").classList.toggle("active", unitSystem === "imperial");
  document.querySelectorAll(".unit-value").forEach(node => {{
    node.textContent = node.dataset[unitSystem];
  }});
  buildFieldTable();
  buildWeatherInline();
  buildRoster();
  renderSummaryCards();
  renderAnomalyList();
  updateMarkers(currentTime);
}}

document.getElementById("metric-toggle").addEventListener("click", () => {{
  unitSystem = "metric";
  applyUnitToggle();
}});
document.getElementById("imperial-toggle").addEventListener("click", () => {{
  unitSystem = "imperial";
  applyUnitToggle();
}});

function buildWeatherInline() {{
  const root = document.getElementById("weather-inline");
  if (!LATEST_WEATHER) {{
    root.innerHTML = '<span class="muted-text">No archived weather sample found.</span>';
    return;
  }}
  root.innerHTML = `
    <div class="topline-item"><span>Condition</span><span>${{LATEST_WEATHER.weather_desc || "N/A"}}</span></div>
    <div class="topline-item"><span>Temperature</span><span>${{formatTemp(LATEST_WEATHER.temp_c)}}</span></div>
    <div class="topline-item"><span>Feels like</span><span>${{formatTemp(LATEST_WEATHER.feels_like_c)}}</span></div>
    <div class="topline-item"><span>Wind</span><span>${{formatSpeed(LATEST_WEATHER.wind_kph)}}</span></div>
    <div class="topline-item"><span>Humidity</span><span>${{LATEST_WEATHER.humidity_pct != null ? LATEST_WEATHER.humidity_pct + "%" : "N/A"}}</span></div>
  `;
}}

function effectiveSpeed(team) {{
  const age = team.sample_age_sec;
  if ((age != null && age >= 300) || (team.speed != null && team.speed < 1.609)) {{
    return 0;
  }}
  return team.speed;
}}

function freshnessBadge(team) {{
  const age = team.sample_age_sec;
  if (age == null) return '<span class="muted-text">Unknown</span>';
  if (age >= 1800) return '<span class="muted-text">Old</span>';
  if (age >= 300) return '<span class="muted-text">Stale</span>';
  return '<span class="muted-text">Live</span>';
}}

function buildFieldTable() {{
  const body = document.getElementById("field-table-body");
  body.innerHTML = "";
  LATEST_TEAMS.forEach(team => {{
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${{team.name}}</td>
      <td>${{formatSpeed(effectiveSpeed(team))}}</td>
      <td>${{formatDistance(team.distance_km)}}</td>
      <td>${{formatDistance(team.to_finish_km)}}</td>
      <td>${{team.points}}</td>
      <td>${{team.heading != null ? team.heading + "°" : "N/A"}}</td>
      <td>${{formatAltitude(team.alt)}}</td>
      <td>${{team.satellites != null ? team.satellites : "N/A"}}</td>
      <td><code>${{team.lat != null && team.lon != null ? team.lat.toFixed(4) + ", " + team.lon.toFixed(4) : "N/A"}}</code></td>
      <td>
        <div>${{formatIso(team.captured_time)}}</div>
        <div class="muted-text">GPS ${{formatIso(team.sample_time)}}</div>
        <div>${{freshnessBadge(team)}}</div>
      </td>
    `;
    body.appendChild(row);
  }});
}}

const allTimes = [];
ROUTE_TEAMS.forEach(team => {{
  team.points.forEach(point => {{
    point.sample_unix = Number(point.sample_unix || 0);
    allTimes.push(point.sample_unix);
  }});
}});
const MIN_TIME = allTimes.length ? Math.min(...allTimes) : 0;
const MAX_TIME = allTimes.length ? Math.max(...allTimes) : 0;
const TIME_SPAN = Math.max(1, MAX_TIME - MIN_TIME);
currentTime = MAX_TIME;

function getPositionAtTime(team, time) {{
  const pts = team.points;
  if (!pts.length) return null;
  if (time <= pts[0].sample_unix) return pts[0];
  if (time >= pts[pts.length - 1].sample_unix) return pts[pts.length - 1];

  let lo = 0;
  let hi = pts.length - 1;
  while (lo < hi - 1) {{
    const mid = (lo + hi) >> 1;
    if (pts[mid].sample_unix <= time) lo = mid;
    else hi = mid;
  }}
  const p1 = pts[lo];
  const p2 = pts[hi];
  const t1 = p1.sample_unix;
  const t2 = p2.sample_unix;
  if (t1 >= t2) return p1;
  const fraction = (time - t1) / (t2 - t1);
  return {{
    lat: p1.lat + (p2.lat - p1.lat) * fraction,
    lng: p1.lng + (p2.lng - p1.lng) * fraction,
    speed: p2.speed != null ? p2.speed : p1.speed,
    sample_unix: time,
  }};
}}

function buildMap() {{
  ROUTE_TEAMS.forEach(team => {{
    const key = getTeamKey(team);
    const latlngs = team.points.map(point => [point.lat, point.lng]);
    if (!latlngs.length) return;
    const poly = L.polyline(latlngs, {{
      color: team.color,
      weight: 2.4,
      opacity: 0.72,
      smoothFactor: 1,
    }}).addTo(routeLayer);
    poly.on("click", () => setFocusedTeam(key, true));
    const marker = L.marker(latlngs[0], {{
      icon: L.divIcon({{
        className: "",
        html: `<div class="tracker-marker" style="background:${{team.color}}"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      }}),
      zIndexOffset: 1000,
    }}).addTo(markerLayer);
    marker.on("click", () => setFocusedTeam(key, true));
    teamLayers.set(key, {{ team, poly, marker, bounds: L.latLngBounds(latlngs) }});
  }});
  PINS.forEach(pin => {{
    if (pin.lat == null || pin.lng == null) return;
    const marker = L.circleMarker([pin.lat, pin.lng], {{
      radius: 6,
      color: pin.color || "orange",
      fillColor: pin.color || "orange",
      fillOpacity: 0.85,
      weight: 2,
    }});
    marker.bindPopup(`<div><strong>${{pin.message || "Pin"}}</strong></div>`);
    marker.addTo(pinLayer);
  }});
  fitAll();
}}

function fitAll() {{
  const bounds = L.latLngBounds([]);
  teamLayers.forEach(layer => bounds.extend(layer.bounds));
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.08));
}}

function fitFocus() {{
  if (focusedTeamKey && teamLayers.has(focusedTeamKey)) {{
    map.fitBounds(teamLayers.get(focusedTeamKey).bounds.pad(0.18));
  }} else {{
    fitAll();
  }}
}}

function updateMarkers(time) {{
  teamLayers.forEach(layer => {{
    const position = getPositionAtTime(layer.team, time);
    if (!position || position.lat == null || position.lng == null) return;
    layer.marker.setLatLng([position.lat, position.lng]);
    const speed = position.speed != null ? formatSpeed(position.speed) : "N/A";
    layer.marker.bindTooltip(`<b>${{layer.team.name}}</b><br>${{speed}}`, {{ direction: "top" }});
  }});
}}

function updateReplayLabels() {{
  document.getElementById("anim-time-display").textContent = formatTime(currentTime);
  document.getElementById("slider-time-label").textContent = formatTime(currentTime);
}}

function updateTopSummary() {{
  const totalSamples = ROUTE_TEAMS.reduce((sum, team) => sum + (team.stats.sample_count || 0), 0);
  document.getElementById("loaded-teams").textContent = `${{ROUTE_TEAMS.length}} teams`;
  document.getElementById("pin-count").textContent = `${{PINS.length}} pins`;
  document.getElementById("sample-count").textContent = totalSamples.toLocaleString();
  document.getElementById("coverage-window").textContent =
    MIN_TIME && MAX_TIME ? `${{formatTime(MIN_TIME)}} -> ${{formatTime(MAX_TIME)}}` : "No timing data";
}}

function buildFocusSelect() {{
  const select = document.getElementById("focus-select");
  select.innerHTML = ['<option value="">All teams</option>'].concat(
    ROUTE_TEAMS.map(team => `<option value="${{getTeamKey(team)}}">${{team.name}}</option>`)
  ).join("");
  select.addEventListener("change", () => setFocusedTeam(select.value || null, true));
}}

function buildRoster() {{
  const root = document.getElementById("team-roster");
  root.innerHTML = [...ROUTE_TEAMS]
    .sort((a, b) => (b.stats.distance_km || 0) - (a.stats.distance_km || 0))
    .map(team => {{
      const key = getTeamKey(team);
      return `
        <div class="team-chip" data-team-key="${{key}}">
          <div class="team-dot" style="background:${{team.color}}"></div>
          <div>
            <div>${{team.name}}</div>
            <div class="team-meta">${{formatDistance(team.stats.distance_km)}} route · ${{team.stats.sample_count}} points</div>
          </div>
          <div class="team-meta">${{team.stats.anomaly_count || 0}} alerts</div>
        </div>
      `;
    }}).join("");
  root.querySelectorAll(".team-chip").forEach(element => {{
    element.addEventListener("click", () => setFocusedTeam(element.dataset.teamKey, true));
    element.classList.toggle("active", element.dataset.teamKey === focusedTeamKey);
  }});
}}

function fleetSummary() {{
  const totalDistanceKm = ROUTE_TEAMS.reduce((sum, team) => sum + (team.stats.distance_km || 0), 0);
  const totalMovingTimeSec = ROUTE_TEAMS.reduce((sum, team) => sum + (team.stats.moving_time_sec || 0), 0);
  const totalAlerts = ROUTE_TEAMS.reduce((sum, team) => sum + (team.stats.anomaly_count || 0), 0);
  return [
    {{ label: "Fleet route", value: formatDistance(totalDistanceKm), note: "Combined path distance" }},
    {{ label: "Moving time", value: secondsToDuration(totalMovingTimeSec), note: "Across all teams" }},
    {{ label: "Alerts", value: String(totalAlerts), note: "Suspicious route segments" }},
    {{ label: "Pins", value: String(PINS.length), note: "Approved hazards and notes" }},
  ];
}}

function teamSummary(team) {{
  const stats = team.stats || {{}};
  return [
    {{ label: "Route", value: formatDistance(stats.distance_km), note: `Straight ${{formatDistance(stats.straight_distance_km)}}` }},
    {{ label: "Moving time", value: secondsToDuration(stats.moving_time_sec), note: `Idle ${{secondsToDuration(stats.idle_time_sec)}}` }},
    {{ label: "Pace", value: stats.moving_average_speed_kph != null ? formatSpeed(stats.moving_average_speed_kph) : "N/A", note: "Average while moving" }},
    {{ label: "Top speed", value: stats.max_speed_kph != null ? formatSpeed(stats.max_speed_kph) : "N/A", note: "Highest observed" }},
    {{ label: "Stops", value: String(stats.stop_count || 0), note: `Active ${{stats.active_ratio_pct != null ? stats.active_ratio_pct.toFixed(1) + "%" : "N/A"}}` }},
    {{ label: "Data gaps", value: String(stats.gap_count || 0), note: `Largest ${{secondsToDuration(stats.largest_gap_sec)}}` }},
  ];
}}

function renderSummaryCards() {{
  const root = document.getElementById("summary-grid");
  const team = focusedTeamKey ? ROUTE_TEAMS.find(item => getTeamKey(item) === focusedTeamKey) : null;
  const cards = team ? teamSummary(team) : fleetSummary();
  root.innerHTML = cards.map(card => `
    <div class="summary-card">
      <div class="summary-label">${{card.label}}</div>
      <div class="summary-value">${{card.value}}</div>
      <div class="summary-note">${{card.note}}</div>
    </div>
  `).join("");
}}

function renderAnomalyList() {{
  const root = document.getElementById("anomaly-list");
  const team = focusedTeamKey ? ROUTE_TEAMS.find(item => getTeamKey(item) === focusedTeamKey) : null;
  if (!team) {{
    root.innerHTML = [...ROUTE_TEAMS]
      .filter(item => (item.stats.anomaly_count || 0) > 0)
      .sort((a, b) => (b.stats.anomaly_count || 0) - (a.stats.anomaly_count || 0))
      .slice(0, 8)
      .map(item => `
        <div class="anomaly-item">
          <div class="anomaly-title">${{item.name}}</div>
          <div class="anomaly-meta">${{item.stats.anomaly_count}} suspicious segments</div>
        </div>
      `).join("") || '<div class="muted-text">No suspicious route segments.</div>';
    return;
  }}

  const anomalies = (team.stats.anomalies || []).slice(0, 30);
  root.innerHTML = anomalies.map((anomaly, index) => `
    <div class="anomaly-item" data-anomaly-index="${{index}}">
      <div class="anomaly-title">${{anomaly.reason}}</div>
      <div class="anomaly-meta">${{anomaly.time || "Time unavailable"}} · ${{formatDistance(anomaly.distance_km)}} · ${{formatSpeed(anomaly.segment_speed_kph)}}</div>
    </div>
  `).join("") || '<div class="muted-text">No suspicious segments for this team.</div>';

  root.querySelectorAll(".anomaly-item[data-anomaly-index]").forEach(element => {{
    element.addEventListener("click", () => {{
      const anomaly = anomalies[Number(element.dataset.anomalyIndex)];
      if (!anomaly) return;
      map.fitBounds(L.latLngBounds([[anomaly.from_lat, anomaly.from_lng], [anomaly.to_lat, anomaly.to_lng]]).pad(0.8));
    }});
  }});
}}

function renderAnomalyOverlays() {{
  anomalyLayer.clearLayers();
  if (!focusedTeamKey) return;
  const team = ROUTE_TEAMS.find(item => getTeamKey(item) === focusedTeamKey);
  if (!team) return;
  (team.stats.anomalies || []).forEach(anomaly => {{
    L.polyline(
      [[anomaly.from_lat, anomaly.from_lng], [anomaly.to_lat, anomaly.to_lng]],
      {{ color: "#f85149", weight: 5, opacity: 0.9, dashArray: "8, 8" }}
    ).bindPopup(`<div><strong>${{anomaly.reason}}</strong><div class="muted-text">${{formatDistance(anomaly.distance_km)}} · ${{formatSpeed(anomaly.segment_speed_kph)}}</div></div>`).addTo(anomalyLayer);
  }});
}}

function applyFocusStyling() {{
  teamLayers.forEach((layer, key) => {{
    const focused = !focusedTeamKey || key === focusedTeamKey;
    const primary = focusedTeamKey && key === focusedTeamKey;
    layer.poly.setStyle({{
      opacity: focused ? (primary ? 1 : 0.28) : 0.14,
      weight: primary ? 5.5 : 2.2,
    }});
    const markerElement = layer.marker.getElement();
    if (markerElement) {{
      const markerDot = markerElement.querySelector(".tracker-marker");
      if (markerDot) {{
        markerDot.classList.toggle("focused", primary);
        markerElement.style.opacity = focused ? "1" : "0.25";
      }}
    }}
  }});
  document.querySelectorAll(".team-chip").forEach(element => {{
    element.classList.toggle("active", element.dataset.teamKey === focusedTeamKey);
  }});
}}

function setFocusedTeam(teamKey, fitToBounds) {{
  focusedTeamKey = teamKey || null;
  document.getElementById("focus-select").value = focusedTeamKey || "";
  applyFocusStyling();
  buildRoster();
  renderSummaryCards();
  renderAnomalyList();
  renderAnomalyOverlays();
  if (fitToBounds) {{
    if (focusedTeamKey && teamLayers.has(focusedTeamKey)) {{
      map.fitBounds(teamLayers.get(focusedTeamKey).bounds.pad(0.18));
    }} else {{
      fitAll();
    }}
  }}
}}

function updateSlider() {{
  const pct = ((currentTime - MIN_TIME) / TIME_SPAN) * 100;
  document.getElementById("time-slider").value = Math.max(0, Math.min(100, pct));
}}

function stepReplay(timestamp) {{
  if (!isPlaying) return;
  if (!lastFrameTime) lastFrameTime = timestamp;
  const deltaSec = ((timestamp - lastFrameTime) / 1000) * playbackSpeed;
  lastFrameTime = timestamp;
  currentTime += deltaSec;
  if (currentTime >= MAX_TIME) currentTime = MIN_TIME;
  updateMarkers(currentTime);
  updateSlider();
  updateReplayLabels();
  animationFrameId = requestAnimationFrame(stepReplay);
}}

function startPlayback() {{
  if (isPlaying) return;
  if (currentTime >= MAX_TIME) currentTime = MIN_TIME;
  isPlaying = true;
  lastFrameTime = 0;
  document.getElementById("play-btn").textContent = "Pause";
  document.getElementById("status-pill").textContent = "Replay running";
  animationFrameId = requestAnimationFrame(stepReplay);
}}

function stopPlayback() {{
  isPlaying = false;
  if (animationFrameId) {{
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }}
  document.getElementById("play-btn").textContent = "Play";
  document.getElementById("status-pill").textContent = focusedTeamKey ? "Focused route" : "Archive replay ready";
}}

function resetReplay() {{
  stopPlayback();
  currentTime = MAX_TIME;
  updateMarkers(currentTime);
  updateSlider();
  updateReplayLabels();
}}

document.getElementById("play-btn").addEventListener("click", () => {{
  if (isPlaying) stopPlayback();
  else startPlayback();
}});
document.getElementById("reset-btn").addEventListener("click", resetReplay);
document.getElementById("fit-all-btn").addEventListener("click", fitAll);
document.getElementById("fit-focus-btn").addEventListener("click", fitFocus);
document.getElementById("speed-select").addEventListener("change", event => {{
  playbackSpeed = Number(event.target.value || 1);
}});
document.getElementById("time-slider").addEventListener("input", event => {{
  const pct = Number(event.target.value || 0) / 100;
  currentTime = MIN_TIME + (TIME_SPAN * pct);
  updateMarkers(currentTime);
  updateReplayLabels();
}});

buildMap();
buildFocusSelect();
buildRoster();
buildFieldTable();
buildWeatherInline();
updateTopSummary();
setFocusedTeam(null, false);
resetReplay();
applyUnitToggle();
</script>
</body>
</html>
"""
    return html_out


def main():
    parser = argparse.ArgumentParser(description="Generate a single comprehensive ASC handoff page.")
    parser.add_argument("--dir", default=data_access.DEFAULT_ARCHIVE_DIR, help="Archived data directory")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Dataset manifest path")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help="Output HTML file")
    args = parser.parse_args()

    data_dir = data_access.resolve_data_dir(args.dir)
    html_out = generate_html(data_dir, args.manifest)
    output_path = Path(args.output)
    output_path.write_text(html_out, encoding="utf-8")
    print(f"Generated {output_path.resolve()}")


if __name__ == "__main__":
    main()
