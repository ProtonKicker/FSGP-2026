import gzip
import json
import tempfile
import unittest
from pathlib import Path

import build_dataset


class BuildDatasetTests(unittest.TestCase):
    def test_existing_archived_snapshots_are_preserved_when_source_files_are_missing(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-build-dataset-"))
        base_dir = temp_dir / "asc"
        dataset_dir = base_dir / "dataset"
        raw_snapshot_dir = dataset_dir / "raw" / "snapshots"
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True)
        raw_snapshot_dir.mkdir(parents=True)

        existing_records = [
            {"timestamp": "2030-01-02T08:00:00-06:00", "tracker_count": 1, "trackers": []},
            {"timestamp": "2030-01-02T08:30:00-06:00", "tracker_count": 1, "trackers": []},
        ]
        with gzip.open(raw_snapshot_dir / "snapshots_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            for record in existing_records:
                handle.write(json.dumps(record) + "\n")

        current_source = {
            "timestamp": "2030-01-02T09:00:00-06:00",
            "timestamp_unix": 1893567600,
            "tracker_count": 1,
            "trackers": [],
        }
        with open(data_dir / "traces_2030-01-02T09-00-00.json", "w", encoding="utf-8") as handle:
            json.dump(current_source, handle)

        build_dataset.build_dataset(base_dir, dataset_dir)

        rebuilt = []
        with gzip.open(raw_snapshot_dir / "snapshots_2030-01-02.jsonl.gz", "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rebuilt.append(json.loads(line))

        self.assertEqual(len(rebuilt), 3)
        self.assertEqual(
            [record["timestamp"] for record in rebuilt],
            [
                "2030-01-02T08:00:00-06:00",
                "2030-01-02T08:30:00-06:00",
                "2030-01-02T09:00:00-06:00",
            ],
        )


if __name__ == "__main__":
    unittest.main()
