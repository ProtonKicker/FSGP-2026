#!/usr/bin/env python3
"""
ASC Live Tracker Data Collector

Automatically records tracker data during American Solar Challenge race hours.
Runs on macOS with caffeinate to prevent sleep during recording.

Race: July 25 - August 1, 2026
Hours: 7:00 - 20:00 Central Time (CDT, UTC-5)
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import time as std_time
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets

logger = logging.getLogger("asc-collector")

WS_URL = "wss://vps-dc69d253.vps.ovh.us"
DEFAULT_RACE_TZ_NAME = "America/Chicago"
DEFAULT_RACE_DATES_SPEC = "2026-07-25:2026-08-01"
DEFAULT_RACE_START = "07:00"
DEFAULT_RACE_END = "20:00"
DEFAULT_INTERVAL = 30
DEFAULT_DATA_DIR = "data"
DEFAULT_ENV_FILE = ".env"


@dataclass(frozen=True)
class RaceSchedule:
    tz: ZoneInfo
    dates: frozenset
    start_time: dt_time
    end_time: dt_time


def parse_date_value(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def parse_time_value(value):
    cleaned = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Invalid time '{value}'. Use HH:MM or HH:MM:SS.")


def expand_date_range(start_date, end_date):
    if end_date < start_date:
        raise ValueError(
            f"Invalid race date range '{start_date}:{end_date}': end is before start."
        )
    span_days = (end_date - start_date).days + 1
    return [start_date.fromordinal(start_date.toordinal() + offset) for offset in range(span_days)]


def parse_dates_spec(spec):
    if not spec or not spec.strip():
        raise ValueError("Race dates cannot be empty.")

    dates = set()
    for chunk in spec.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if ":" in piece:
            start_raw, end_raw = [part.strip() for part in piece.split(":", 1)]
            start_date = parse_date_value(start_raw)
            end_date = parse_date_value(end_raw)
            dates.update(expand_date_range(start_date, end_date))
        else:
            dates.add(parse_date_value(piece))

    if not dates:
        raise ValueError("Race dates cannot be empty.")
    return frozenset(sorted(dates))


def build_schedule(race_tz_name, race_dates_spec, race_start, race_end):
    try:
        tz = ZoneInfo(race_tz_name)
    except Exception as exc:
        raise ValueError(f"Invalid race timezone '{race_tz_name}'.") from exc

    dates = parse_dates_spec(race_dates_spec)
    start_time = parse_time_value(race_start)
    end_time = parse_time_value(race_end)
    if end_time <= start_time:
        raise ValueError(
            f"Race end time '{race_end}' must be after start time '{race_start}'."
        )

    return RaceSchedule(
        tz=tz,
        dates=dates,
        start_time=start_time,
        end_time=end_time,
    )


ACTIVE_SCHEDULE = build_schedule(
    DEFAULT_RACE_TZ_NAME,
    DEFAULT_RACE_DATES_SPEC,
    DEFAULT_RACE_START,
    DEFAULT_RACE_END,
)


def get_schedule(schedule=None):
    return schedule or ACTIVE_SCHEDULE


def set_active_schedule(schedule):
    global ACTIVE_SCHEDULE
    ACTIVE_SCHEDULE = schedule


def schedule_summary(schedule=None):
    sched = get_schedule(schedule)
    first_day = min(sched.dates)
    last_day = max(sched.dates)
    return (
        f"{first_day.isoformat()} to {last_day.isoformat()}, "
        f"{sched.start_time.strftime('%H:%M')} - {sched.end_time.strftime('%H:%M')} "
        f"{sched.tz.key}"
    )


def now_ct(schedule=None):
    return datetime.now(get_schedule(schedule).tz)


def iso_local(dt, schedule=None):
    return dt.astimezone(get_schedule(schedule).tz).isoformat()


def unix_timestamp(dt):
    return dt.astimezone(timezone.utc).timestamp()


def is_race_day(d, schedule=None):
    return d in get_schedule(schedule).dates


def is_race_time(now=None, schedule=None):
    sched = get_schedule(schedule)
    if now is None:
        now = now_ct(sched)
    return is_race_day(now.date(), sched) and sched.start_time <= now.time() < sched.end_time


def seconds_until(target_dt, schedule=None):
    diff = target_dt - now_ct(schedule)
    return max(0, diff.total_seconds())


def next_race_start(schedule=None):
    sched = get_schedule(schedule)
    n = now_ct(sched)
    for d in sorted(sched.dates):
        cand = datetime.combine(d, sched.start_time, tzinfo=sched.tz)
        if cand > n:
            return cand
    return None


def race_is_over(schedule=None):
    sched = get_schedule(schedule)
    last = max(sched.dates)
    return now_ct(sched) > datetime.combine(last, sched.end_time, tzinfo=sched.tz)


def positive_float(value):
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than 0.")
    return parsed


class CaffeinateGuard:
    def __init__(self):
        self._proc = None

    def start(self):
        if self._proc is not None:
            return
        if sys.platform != "darwin":
            return
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-i", "-m"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("caffeinate started (sleep prevented)")
        except FileNotFoundError:
            logger.debug("caffeinate not found")
            self._proc = None

    def stop(self):
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        logger.info("caffeinate stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


class TrackerStore:
    def __init__(self):
        self.trackers = {}
        self.message_count = 0

    def apply_snapshot(self, trackers):
        self.trackers.clear()
        for serial, data in trackers.items():
            data["serial"] = serial
            self.trackers[serial] = data

    def apply_update(self, trackers, removed):
        for serial, data in trackers.items():
            data["serial"] = serial
            existing = self.trackers.get(serial)
            if existing:
                existing.update(data)
            else:
                self.trackers[serial] = data
        for serial in removed:
            self.trackers.pop(serial, None)

    @property
    def tracker_list(self):
        return sorted(
            self.trackers.values(),
            key=lambda t: t.get("name", t.get("serial", "")),
        )

    @property
    def tracker_count(self):
        return len(self.trackers)


class UpdateLog:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def write_entry(self, entry):
        now = now_ct()
        filename = f"updates_{now.strftime('%Y-%m-%d')}.jsonl"
        path = self.data_dir / filename
        entry["_t"] = iso_local(now)
        entry["_t_unix"] = unix_timestamp(now)
        line = json.dumps(entry, default=str) + "\n"
        with open(path, "a") as f:
            f.write(line)

    def write_tracker(self, serial, data):
        self.write_entry({
            "serial": serial,
            "name": data.get("name", ""),
            "lat": data.get("latitude"),
            "lon": data.get("longitude"),
            "speed": data.get("speed_kph"),
            "heading": data.get("course_deg"),
            "alt": data.get("altitude_m"),
            "sample_time": data.get("sample_time", ""),
            "sample_unix": data.get("sample_unix"),
            "satellites": data.get("satellites"),
            "pdop": data.get("pdop"),
            "fix_mode": data.get("fix_mode"),
            "color": data.get("color", ""),
        })

    def write_all(self, store):
        """Write all trackers in the store to the update log (periodic sync)."""
        for serial, data in store.trackers.items():
            self.write_tracker(serial, data)


WEATHER_LAT = 43.5
WEATHER_LON = -91.5
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,"
    "apparent_temperature,precipitation,weather_code,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
    "&temperature_unit=celsius&wind_speed_unit=kmh"
)


WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def weather_description(code):
    return WEATHER_CODES.get(code, f"Unknown ({code})")


def fetch_weather(lat=WEATHER_LAT, lon=WEATHER_LON):
    """Fetch current weather from Open-Meteo (free, no API key). Returns dict or None."""
    url = WEATHER_URL.format(lat=lat, lon=lon)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        current = data.get("current", {})
        return {
            "temp_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_kph": current.get("wind_speed_10m"),
            "wind_dir_deg": current.get("wind_direction_10m"),
            "wind_gust_kph": current.get("wind_gusts_10m"),
            "precip_mm": current.get("precipitation"),
            "weather_code": current.get("weather_code"),
            "weather_desc": weather_description(current.get("weather_code")),
            "lat": lat,
            "lon": lon,
        }
    except Exception as e:
        logger.warning("Weather fetch failed: %s", e)
        return None


class WeatherLog:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def write(self, weather):
        if weather is None:
            return
        now = now_ct()
        filename = f"weather_{now.strftime('%Y-%m-%d')}.jsonl"
        path = self.data_dir / filename
        weather["_recorded"] = iso_local(now)
        weather["_recorded_unix"] = unix_timestamp(now)
        line = json.dumps(weather, default=str) + "\n"
        with open(path, "a") as f:
            f.write(line)


class PinsLog:
    """Stores pins (route/checkpoint/finish) data from the server."""
    def __init__(self, data_dir, tz=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._written = set()
        self._tz = tz

    def write(self, pins, store_ts):
        if not pins:
            return
        local_ts = store_ts.astimezone(self._tz or ZoneInfo("America/Chicago"))
        key = local_ts.strftime("%Y-%m-%d")
        if key in self._written:
            return
        self._written.add(key)
        filename = f"pins_{key}.json"
        path = self.data_dir / filename
        record = {
            "timestamp": iso_local(store_ts),
            "timestamp_unix": unix_timestamp(store_ts),
            "pin_count": len(pins),
            "pins": pins,
        }
        with open(path, "w") as f:
            json.dump(record, f, indent=2, default=str)
        logger.info("Pins saved: %s (%d pins)", path.name, len(pins))


class SnapshotDumper:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def dump(self, store):
        now = now_ct()
        filename = f"traces_{now.strftime('%Y-%m-%dT%H-%M-%S')}.json"
        path = self.data_dir / filename
        captured_time = iso_local(now)
        captured_unix = unix_timestamp(now)
        trackers = []
        for tracker in store.tracker_list:
            tracker_copy = dict(tracker)
            tracker_copy["captured_time"] = captured_time
            tracker_copy["captured_unix"] = captured_unix
            sample_unix = tracker_copy.get("sample_unix")
            if sample_unix is not None:
                try:
                    tracker_copy["sample_age_sec"] = max(0.0, captured_unix - float(sample_unix))
                except (TypeError, ValueError):
                    tracker_copy["sample_age_sec"] = None
            else:
                tracker_copy["sample_age_sec"] = None
            trackers.append(tracker_copy)
        record = {
            "timestamp": captured_time,
            "timestamp_unix": captured_unix,
            "tracker_count": store.tracker_count,
            "trackers": trackers,
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(record, f, indent=2, default=str)
        tmp.rename(path)
        logger.info("Snapshot: %s (%d trackers)", path.name, store.tracker_count)


class DailyDataPacker:
    """
    Builds a daily sanitized archive for sharing.
    - Removes raw serial identifiers.
    - Adds stable pseudonymous tracker_id.
    - Rounds coordinates to reduce precision leakage.
    """
    SENSITIVE_KEYS = {"serial", "serial_hex"}
    LAT_KEYS = {"lat", "latitude"}
    LON_KEYS = {"lon", "longitude"}

    def __init__(self, data_dir, archive_dir, salt="", coord_precision=3):
        self.data_dir = Path(data_dir)
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.salt = salt
        self.coord_precision = coord_precision

    def _tracker_id(self, name, serial):
        base = f"{name or ''}|{serial or ''}"
        digest = hashlib.sha256(f"{self.salt}|{base}".encode("utf-8")).hexdigest()[:12]
        return f"team_{digest}"

    def _round_coord(self, value):
        try:
            return round(float(value), self.coord_precision)
        except (TypeError, ValueError):
            return value

    def _sanitize(self, obj):
        if isinstance(obj, dict):
            name = obj.get("name")
            serial = obj.get("serial")
            out = {}
            if name is not None:
                out["name"] = name
            if name is not None or serial is not None:
                out["tracker_id"] = self._tracker_id(name, serial)

            for key, value in obj.items():
                if key in self.SENSITIVE_KEYS or key == "name":
                    continue
                if key in self.LAT_KEYS:
                    out[key] = self._round_coord(value)
                elif key in self.LON_KEYS:
                    out[key] = self._round_coord(value)
                else:
                    out[key] = self._sanitize(value)
            return out
        if isinstance(obj, list):
            return [self._sanitize(item) for item in obj]
        return obj

    def _load_jsonl(self, path):
        records = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    # Preserve malformed lines as-is in a wrapper to avoid data loss.
                    records.append({"_raw_line": line})
        return records

    def _dump_jsonl(self, records):
        return "".join(json.dumps(rec, default=str) + "\n" for rec in records)

    def _daily_sources(self, day):
        key = day.strftime("%Y-%m-%d")
        sources = []
        sources.extend(sorted(self.data_dir.glob(f"traces_{key}T*.json")))
        for exact in (
            self.data_dir / f"updates_{key}.jsonl",
            self.data_dir / f"weather_{key}.jsonl",
            self.data_dir / f"pins_{key}.json",
        ):
            if exact.exists():
                sources.append(exact)
        return key, sources

    def pack_day(self, day):
        key, sources = self._daily_sources(day)
        if not sources:
            return None

        out_path = self.archive_dir / f"asc_public_{key}.zip"
        latest_src_mtime = max(src.stat().st_mtime for src in sources)
        if out_path.exists() and out_path.stat().st_mtime >= latest_src_mtime:
            return out_path

        tmp_path = out_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                if src.suffix == ".jsonl":
                    records = self._load_jsonl(src)
                    sanitized = [self._sanitize(rec) for rec in records]
                    zf.writestr(src.name, self._dump_jsonl(sanitized))
                elif src.suffix == ".json":
                    with open(src, "r") as f:
                        payload = json.load(f)
                    zf.writestr(src.name, json.dumps(self._sanitize(payload), indent=2, default=str))

            manifest = {
                "date": key,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sanitization": {
                    "removed_keys": sorted(self.SENSITIVE_KEYS),
                    "added_key": "tracker_id",
                    "coord_precision": self.coord_precision,
                },
                "source_files": [src.name for src in sources],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        tmp_path.replace(out_path)
        logger.info("Packed public archive: %s (%d files)", out_path.name, len(sources))
        return out_path

    def pack_recent_days(self, now_dt):
        packed = []
        for day in (now_dt.date(), (now_dt - timedelta(days=1)).date()):
            archive = self.pack_day(day)
            if archive is not None:
                packed.append(archive)
        return packed


async def record_loop(ws_url, snapshot_interval, data_dir, schedule):
    store = TrackerStore()
    store._last_pins = []
    snapshotter = SnapshotDumper(data_dir)
    updatelog = UpdateLog(data_dir)
    weatherlog = WeatherLog(data_dir)
    pinslog = PinsLog(data_dir, tz=schedule.tz)

    active = True

    def shutdown():
        nonlocal active
        active = False

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutdown)
    loop.add_signal_handler(signal.SIGTERM, shutdown)

    reconnect_delay = 1.0
    grace_start = None
    MAX_GRACE_SECS = 7200  # 2 hours max extension past race end

    def any_moving():
        return any(
            (t.get("speed_kph") or 0) > 0
            for t in store.tracker_list
        )

    def fleet_center():
        coords = [
            (t.get("latitude"), t.get("longitude"))
            for t in store.tracker_list
            if t.get("latitude") is not None and t.get("longitude") is not None
        ]
        if not coords:
            return WEATHER_LAT, WEATHER_LON
        avg_lat = sum(lat for lat, _ in coords) / len(coords)
        avg_lon = sum(lon for _, lon in coords) / len(coords)
        return avg_lat, avg_lon

    def periodic_dump():
        snapshotter.dump(store)
        updatelog.write_all(store)
        lat, lon = fleet_center()
        weather = fetch_weather(lat, lon)
        weatherlog.write(weather)
        if weather:
            logger.info("Weather: %.1f°C, %s, wind %.1f km/h",
                        weather["temp_c"], weather["weather_desc"], weather["wind_kph"])

    while active:
        try:
            logger.info("Connecting to WebSocket ...")
            async with websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=15,
                max_size=2 ** 24,
            ) as ws:
                logger.info("Connected")
                reconnect_delay = 1.0
                next_snapshot = std_time.time() + snapshot_interval

                while active:
                    if not is_race_time(schedule=schedule):
                        if grace_start is None:
                            grace_start = std_time.time()
                            logger.info("Race hours ended, but vehicles still moving — extending...")
                        elapsed = std_time.time() - grace_start
                        if elapsed > MAX_GRACE_SECS:
                            logger.info("Grace period expired (%.0f min), stopping", elapsed / 60)
                            break
                        if not any_moving():
                            logger.info("All vehicles stopped, ending extended recording")
                            break

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if (
                            store.message_count > 0
                            and std_time.time() >= next_snapshot
                        ):
                            periodic_dump()
                            next_snapshot = std_time.time() + snapshot_interval
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON: %.200s", raw)
                        continue

                    msg_type = msg.get("type", "unknown")
                    store.message_count += 1

                    if msg_type == "snapshot":
                        trackers = msg.get("trackers", {})
                        extra_msg = [k for k in msg if k not in ("type","trackers","pins")]
                        if extra_msg:
                            logger.debug("Snapshot extra top-level keys: %s", extra_msg)
                        store.apply_snapshot(trackers)
                        logger.info(
                            "Snapshot: %d trackers", store.tracker_count
                        )
                        periodic_dump()
                        next_snapshot = std_time.time() + snapshot_interval

                    elif msg_type == "update":
                        trackers = msg.get("trackers", {})
                        removed = msg.get("removed", [])
                        store.apply_update(trackers, removed)
                        for serial, data in trackers.items():
                            extra = [k for k in data if k not in (
                                "name","latitude","longitude","speed_kph","course_deg",
                                "altitude_m","satellites","pdop","fix_mode","color",
                                "sample_time","sample_unix","serial","serial_hex",
                            )]
                            if extra:
                                logger.debug("Tracker %s extra fields: %s", serial, extra)
                            updatelog.write_tracker(serial, data)
                        for serial in removed:
                            updatelog.write_entry({
                                "serial": serial,
                                "removed": True,
                            })

                    elif msg_type == "redirect":
                        new_url = msg.get("url")
                        if new_url:
                            logger.info("Redirect to %s", new_url)
                            ws_url = new_url
                            break

                    elif msg_type == "reload":
                        delay = msg.get("delay_seconds", 0)
                        logger.info("Server reload in %ds", delay)
                        await asyncio.sleep(delay)
                        break

                    elif msg_type == "heartbeat":
                        pass

                    elif msg_type == "pins_snapshot":
                        pins = msg.get("pins", [])
                        store._last_pins = pins
                        pinslog.write(pins, datetime.now(timezone.utc))
                        logger.info("Pins snapshot: %d pins", len(pins))

                    else:
                        logger.debug("Unknown msg: %s", msg_type)

                    if std_time.time() >= next_snapshot:
                        periodic_dump()
                        next_snapshot = std_time.time() + snapshot_interval

        except websockets.WebSocketException as e:
            logger.warning("WebSocket error: %s", e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Unexpected error: %s", e)

        if not active:
            break

        if is_race_time(schedule=schedule) or (grace_start is not None and any_moving() and std_time.time() - grace_start < MAX_GRACE_SECS):
            logger.info("Reconnecting in %.1fs ...", reconnect_delay)
            try:
                await asyncio.sleep(reconnect_delay)
            except asyncio.CancelledError:
                break
            reconnect_delay = min(reconnect_delay * 2, 60)
        else:
            if grace_start is not None:
                logger.info("Extended recording session ended")
                grace_start = None
            break


async def main_loop(ws_url, snapshot_interval, data_dir, schedule, packer=None):
    logger.info("ASC Data Collector starting")
    logger.info("Race timezone: %s", schedule.tz.key)
    logger.info("Race schedule: %s", schedule_summary(schedule))
    logger.info("Your system time: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"))
    logger.info("Current race-local time: %s", now_ct(schedule).strftime("%Y-%m-%d %H:%M:%S %Z"))

    guard = CaffeinateGuard()

    while True:
        if race_is_over(schedule):
            logger.info("Race window is over. Exiting.")
            break

        if is_race_time(schedule=schedule):
            logger.info("=== RACE HOURS ===")
            with guard:
                await record_loop(ws_url, snapshot_interval, data_dir, schedule)
            logger.info("=== RACE HOURS ENDED ===")
            if packer is not None:
                packer.pack_recent_days(now_ct(schedule))
        else:
            if packer is not None:
                packer.pack_recent_days(now_ct(schedule))
            nxt = next_race_start(schedule)
            if nxt is None:
                logger.info("No more configured race dates remain. Exiting.")
                break
            wait = seconds_until(nxt, schedule)
            logger.info(
                "Outside race hours. Sleeping %.0f min until %s ...",
                wait / 60,
                nxt.strftime("%Y-%m-%d %H:%M %Z"),
            )
            try:
                await asyncio.sleep(min(wait, 3600))
            except asyncio.CancelledError:
                break

    logger.info("Collector stopped")


def acquire_lock(lock_path: Path):
    """Ensure only one collector instance runs at a time."""
    fp = open(lock_path, "w")
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()
        return fp
    except IOError:
        sys.exit(f"ERROR: Another collector instance is already running (locked {lock_path}).")

def load_dotenv(env_path: Path):
    """Load simple .env file into os.environ."""
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                os.environ[parts[0].strip()] = parts[1].strip()


def resolve_env_file(argv):
    for idx, arg in enumerate(argv):
        if arg.startswith("--env-file="):
            return Path(arg.split("=", 1)[1]).expanduser()
        if arg == "--env-file" and idx + 1 < len(argv):
            return Path(argv[idx + 1]).expanduser()
    return Path(DEFAULT_ENV_FILE)

def main():
    env_file = resolve_env_file(sys.argv[1:])
    load_dotenv(env_file)

    parser = argparse.ArgumentParser(
        description="ASC Live Tracker Data Collector"
    )
    parser.add_argument(
        "--ws", default=WS_URL,
        help="WebSocket URL (default: %(default)s)",
    )
    parser.add_argument(
        "--interval", type=positive_float, default=DEFAULT_INTERVAL,
        help="Snapshot interval in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--dir", default=DEFAULT_DATA_DIR,
        help="Data output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--env-file",
        default=str(env_file),
        help="Optional .env file to load before parsing other options (default: %(default)s)",
    )
    parser.add_argument(
        "--lock-file",
        help="Optional lock file path (default: <data-dir>/.collector.lock)",
    )
    parser.add_argument(
        "--race-tz",
        default=os.environ.get("ASC_RACE_TZ", DEFAULT_RACE_TZ_NAME),
        help="Race timezone, e.g. America/Chicago (default: %(default)s)",
    )
    parser.add_argument(
        "--race-dates",
        default=os.environ.get("ASC_RACE_DATES", DEFAULT_RACE_DATES_SPEC),
        help="Race dates as YYYY-MM-DD or ranges like YYYY-MM-DD:YYYY-MM-DD, comma-separated",
    )
    parser.add_argument(
        "--race-start",
        default=os.environ.get("ASC_RACE_START", DEFAULT_RACE_START),
        help="Race start time in HH:MM or HH:MM:SS (default: %(default)s)",
    )
    parser.add_argument(
        "--race-end",
        default=os.environ.get("ASC_RACE_END", DEFAULT_RACE_END),
        help="Race end time in HH:MM or HH:MM:SS (default: %(default)s)",
    )
    parser.add_argument(
        "--user-tz",
        help="Your timezone (default: auto-detect from system)",
    )
    parser.add_argument(
        "--no-caffeinate", action="store_true",
        help="Disable caffeinate (allow Mac sleep during recording)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Debug logging",
    )
    parser.add_argument(
        "--log-file",
        help="Write logs to file",
    )
    parser.add_argument(
        "--dry-run-schedule", action="store_true",
        help="Print the current schedule state and exit without connecting",
    )
    parser.add_argument(
        "--archive-dir",
        default=os.environ.get("ASC_ARCHIVE_DIR", "archives"),
        help="Directory for sanitized daily archives (default: %(default)s)",
    )
    parser.add_argument(
        "--pack-salt",
        default=os.environ.get("ASC_PACK_SALT", ""),
        help="Optional salt used to derive stable public tracker_id values",
    )
    parser.add_argument(
        "--pack-coord-precision",
        type=int,
        default=int(os.environ.get("ASC_PACK_COORD_PRECISION", "3")),
        help="Decimal places kept for lat/lon in packed public archives (default: %(default)s)",
    )

    args = parser.parse_args()
    try:
        schedule = build_schedule(
            race_tz_name=args.race_tz,
            race_dates_spec=args.race_dates,
            race_start=args.race_start,
            race_end=args.race_end,
        )
    except ValueError as exc:
        parser.error(str(exc))

    set_active_schedule(schedule)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        filename=args.log_file,
        force=True,
    )
    if not args.log_file:
        logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

    if args.no_caffeinate:
        global CaffeinateGuard
        class CaffeinateGuard:
            def start(self): pass
            def stop(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

    if args.user_tz:
        try:
            user_zone = ZoneInfo(args.user_tz)
            logger.info("User timezone: %s", user_zone)
        except Exception:
            logger.warning("Invalid timezone '%s', using system", args.user_tz)

    data_dir = os.path.abspath(args.dir)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    archive_dir = os.path.abspath(args.archive_dir)
    Path(archive_dir).mkdir(parents=True, exist_ok=True)
    lock_path = Path(args.lock_file).expanduser() if args.lock_file else Path(data_dir) / ".collector.lock"
    packer = DailyDataPacker(
        data_dir=data_dir,
        archive_dir=archive_dir,
        salt=args.pack_salt,
        coord_precision=args.pack_coord_precision,
    )

    if args.dry_run_schedule:
        now = now_ct(schedule)
        nxt = next_race_start(schedule)
        state = "within race hours" if is_race_time(schedule=schedule) else "outside race hours"
        print(f"Schedule: {schedule_summary(schedule)}")
        print(f"Current race-local time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Current state: {state}")
        print(f"Race over: {'yes' if race_is_over(schedule) else 'no'}")
        print(f"Next race start: {nxt.strftime('%Y-%m-%d %H:%M:%S %Z') if nxt else 'none'}")
        print(f"Data directory: {data_dir}")
        print(f"Archive directory: {archive_dir}")
        print(f"Lock file: {lock_path}")
        return

    lock_fp = acquire_lock(lock_path)
    try:
        asyncio.run(main_loop(
            ws_url=args.ws,
            snapshot_interval=args.interval,
            data_dir=data_dir,
            schedule=schedule,
            packer=packer,
        ))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        lock_fp.close()


if __name__ == "__main__":
    main()
