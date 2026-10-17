import gzip
import json
import tempfile
import unittest
from pathlib import Path

import restore_dataset


class RestoreDatasetTests(unittest.TestCase):
    def test_restore_rebuilds_raw_shapes_from_archive(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-restore-"))
        dataset_dir = temp_dir / "dataset"
        raw_dir = dataset_dir / "raw"
        (raw_dir / "updates").mkdir(parents=True)
        (raw_dir / "weather").mkdir(parents=True)
        (raw_dir / "pins").mkdir(parents=True)
        (raw_dir / "snapshots").mkdir(parents=True)
        (raw_dir / "logs").mkdir(parents=True)

        with gzip.open(raw_dir / "updates" / "updates_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write('{"serial":"1","name":"Team","lat":1,"lon":2}\n')
        with gzip.open(raw_dir / "weather" / "weather_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write('{"temp_c":25,"_recorded":"2030-01-02T08:00:00-06:00"}\n')
        with open(raw_dir / "pins" / "pins_2030-01-02.json", "w", encoding="utf-8") as handle:
            json.dump({"pins": [{"id": "p1"}]}, handle)
        with gzip.open(raw_dir / "snapshots" / "snapshots_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": "2030-01-02T08:30:00-06:00",
                "timestamp_unix": 1893565800,
                "tracker_count": 1,
                "trackers": [],
            }) + "\n")
        with gzip.open(raw_dir / "logs" / "collector_2030.log.gz", "wt", encoding="utf-8") as handle:
            handle.write("hello log\n")

        output_dir = temp_dir / "restored"
        restored = restore_dataset.restore_dataset(dataset_dir, output_dir)

        self.assertEqual(restored["updates"], 1)
        self.assertEqual(restored["weather"], 1)
        self.assertEqual(restored["pins"], 1)
        self.assertEqual(restored["snapshots"], 1)
        self.assertEqual(restored["logs"], 1)
        self.assertTrue((output_dir / "data" / "updates_2030-01-02.jsonl").exists())
        self.assertTrue((output_dir / "data" / "weather_2030-01-02.jsonl").exists())
        self.assertTrue((output_dir / "data" / "pins_2030-01-02.json").exists())
        self.assertTrue((output_dir / "data" / "traces_2030-01-02T08-30-00.json").exists())
        self.assertEqual((output_dir / "collector_2030.log").read_text(encoding="utf-8"), "hello log\n")


if __name__ == "__main__":
    unittest.main()
