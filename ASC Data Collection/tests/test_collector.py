import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import collector


class CollectorScheduleTests(unittest.TestCase):
    def setUp(self):
        self.original_schedule = collector.ACTIVE_SCHEDULE

    def tearDown(self):
        collector.set_active_schedule(self.original_schedule)

    def test_parse_dates_spec_supports_ranges_and_single_dates(self):
        dates = collector.parse_dates_spec("2026-07-25:2026-07-27,2026-08-01")
        self.assertEqual(
            list(sorted(dates)),
            [
                collector.parse_date_value("2026-07-25"),
                collector.parse_date_value("2026-07-26"),
                collector.parse_date_value("2026-07-27"),
                collector.parse_date_value("2026-08-01"),
            ],
        )

    def test_build_schedule_rejects_end_before_start(self):
        with self.assertRaises(ValueError):
            collector.build_schedule(
                race_tz_name="America/Chicago",
                race_dates_spec="2026-07-25:2026-08-01",
                race_start="20:00",
                race_end="07:00",
            )

    def test_is_race_time_uses_custom_schedule(self):
        schedule = collector.build_schedule(
            race_tz_name="America/Chicago",
            race_dates_spec="2030-06-10:2030-06-11",
            race_start="09:00",
            race_end="17:00",
        )
        inside = datetime(2030, 6, 10, 12, 0, tzinfo=schedule.tz)
        outside = datetime(2030, 6, 10, 18, 0, tzinfo=schedule.tz)
        self.assertTrue(collector.is_race_time(inside, schedule))
        self.assertFalse(collector.is_race_time(outside, schedule))

    def test_next_race_start_and_race_is_over_follow_schedule(self):
        schedule = collector.build_schedule(
            race_tz_name="America/Chicago",
            race_dates_spec="2030-06-10:2030-06-11",
            race_start="09:00",
            race_end="17:00",
        )
        with mock.patch("collector.now_ct", return_value=datetime(2030, 6, 9, 20, 0, tzinfo=schedule.tz)):
            next_start = collector.next_race_start(schedule)
        self.assertEqual(next_start, datetime(2030, 6, 10, 9, 0, tzinfo=schedule.tz))

        with mock.patch("collector.now_ct", return_value=datetime(2030, 6, 11, 17, 1, tzinfo=schedule.tz)):
            self.assertTrue(collector.race_is_over(schedule))


class CollectorLoggingTests(unittest.TestCase):
    def setUp(self):
        self.original_schedule = collector.ACTIVE_SCHEDULE

    def tearDown(self):
        collector.set_active_schedule(self.original_schedule)

    def test_logs_use_schedule_local_date_and_store_unix_time(self):
        schedule = collector.build_schedule(
            race_tz_name="America/Chicago",
            race_dates_spec="2030-01-02",
            race_start="07:00",
            race_end="20:00",
        )
        collector.set_active_schedule(schedule)
        fixed_dt = datetime(2030, 1, 2, 8, 30, tzinfo=schedule.tz)
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-tests-"))
        store = collector.TrackerStore()
        store.apply_snapshot({
            "abc": {
                "name": "Test Team",
                "latitude": 43.0,
                "longitude": -91.0,
            }
        })

        with mock.patch("collector.now_ct", return_value=fixed_dt):
            collector.UpdateLog(temp_dir).write_entry({"serial": "abc"})
            collector.WeatherLog(temp_dir).write({"temp_c": 20})
            collector.SnapshotDumper(temp_dir).dump(store)

        update_path = temp_dir / "updates_2030-01-02.jsonl"
        weather_path = temp_dir / "weather_2030-01-02.jsonl"
        snapshot_path = temp_dir / "traces_2030-01-02T08-30-00.json"

        self.assertTrue(update_path.exists())
        self.assertTrue(weather_path.exists())
        self.assertTrue(snapshot_path.exists())

        update_record = json.loads(update_path.read_text().strip())
        weather_record = json.loads(weather_path.read_text().strip())
        snapshot_record = json.loads(snapshot_path.read_text())

        self.assertEqual(update_record["_t"], "2030-01-02T08:30:00-06:00")
        self.assertAlmostEqual(update_record["_t_unix"], fixed_dt.timestamp())
        self.assertEqual(weather_record["_recorded"], "2030-01-02T08:30:00-06:00")
        self.assertAlmostEqual(weather_record["_recorded_unix"], fixed_dt.timestamp())
        self.assertEqual(snapshot_record["timestamp"], "2030-01-02T08:30:00-06:00")
        self.assertAlmostEqual(snapshot_record["timestamp_unix"], fixed_dt.timestamp())
        tracker_record = snapshot_record["trackers"][0]
        self.assertEqual(tracker_record["captured_time"], "2030-01-02T08:30:00-06:00")
        self.assertAlmostEqual(tracker_record["captured_unix"], fixed_dt.timestamp())
        self.assertIsNone(tracker_record["sample_age_sec"])


class CollectorConfigTests(unittest.TestCase):
    def test_resolve_env_file_defaults_and_cli_override(self):
        self.assertEqual(collector.resolve_env_file([]), Path(".env"))
        self.assertEqual(
            collector.resolve_env_file(["--env-file", "~/asc.env"]),
            Path("~/asc.env").expanduser(),
        )
        self.assertEqual(
            collector.resolve_env_file(["--env-file=/tmp/asc.env"]),
            Path("/tmp/asc.env"),
        )

    def test_load_dotenv_loads_simple_pairs(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-env-"))
        env_path = temp_dir / ".env"
        env_path.write_text(
            "# comment\nASC_RACE_START=08:00\nASC_RACE_END=19:00\nIGNORED_LINE\n"
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            collector.load_dotenv(env_path)
            self.assertEqual(__import__("os").environ["ASC_RACE_START"], "08:00")
            self.assertEqual(__import__("os").environ["ASC_RACE_END"], "19:00")


if __name__ == "__main__":
    unittest.main()
