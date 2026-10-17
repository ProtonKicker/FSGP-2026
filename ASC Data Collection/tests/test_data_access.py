import gzip
import json
import tempfile
import unittest
from pathlib import Path

import data_access


class DataAccessTests(unittest.TestCase):
    def test_resolve_data_dir_finds_archive_inside_project_root(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-data-access-"))
        archive_dir = temp_dir / "dataset" / "raw"
        archive_dir.mkdir(parents=True)
        (archive_dir / "updates_2030-01-02.jsonl.gz").write_bytes(b"")
        resolved = data_access.resolve_data_dir(temp_dir)
        self.assertEqual(resolved, archive_dir)

    def test_iterators_support_gzipped_archive_files(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="asc-data-access-"))
        archive_dir = temp_dir / "dataset" / "raw"
        archive_dir.mkdir(parents=True)

        with gzip.open(archive_dir / "updates_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write(json.dumps({"serial": "1", "name": "Team One", "lat": 1, "lon": 2}) + "\n")

        with gzip.open(archive_dir / "snapshots_2030-01-02.jsonl.gz", "wt", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": "2030-01-02T08:00:00-06:00", "timestamp_unix": 1893564000, "tracker_count": 1, "trackers": []}) + "\n")

        records = list(data_access.iter_update_records(archive_dir))
        snapshots = list(data_access.iter_snapshot_records(archive_dir))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "Team One")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["tracker_count"], 1)


if __name__ == "__main__":
    unittest.main()
