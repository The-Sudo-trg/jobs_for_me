import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.discover_jobs as discover_jobs


class JobDiscoveryTests(unittest.TestCase):
    def test_keeps_rust_role_and_relevant_internship_only(self):
        rust_role = discover_jobs.make_job(
            "Test", "Senior Rust Engineer", "Example", "https://example.com/rust",
            "Remote", "2026-10-01T08:00:00Z", ["Rust"], "1",
        )
        internship = discover_jobs.make_job(
            "Test", "Software Developer Intern", "Example", "https://example.com/intern",
            "Remote", "2026-10-01T08:00:00Z", [], "2",
        )
        unrelated = discover_jobs.make_job(
            "Test", "Marketing Intern", "Example", "https://example.com/marketing",
            "Remote", "2026-10-01T08:00:00Z", [], "3",
        )
        self.assertEqual(rust_role["match"], "Rust-related")
        self.assertEqual(internship["match"], "Internship / early-career software")
        self.assertEqual(unrelated, {})

    def test_remoteok_skips_metadata_object(self):
        jobs = discover_jobs.normalize_remoteok([
            {"legal": "attribution"},
            {"id": 1, "position": "Rust Developer", "company": "Example",
             "slug": "rust-developer", "tags": ["rust"], "date": "2026-10-01"},
        ])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "Remote OK")

    def test_tracker_adds_only_unseen_jobs_and_writes_timestamp(self):
        sample = {
            "key": "Test:1", "title": "Rust Engineer", "company": "Example",
            "url": "https://example.com/rust", "location": "Remote",
            "published": "2026-10-01T08:00:00+00:00", "source": "Test",
            "match": "Rust-related",
        }
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "seen.json"
            tracker_path = Path(temporary) / "tracker.md"
            with patch.object(discover_jobs, "STATE_PATH", state_path), \
                 patch.object(discover_jobs, "TRACKER_PATH", tracker_path), \
                 patch.object(discover_jobs, "collect_jobs", return_value=[sample]):
                timestamp = dt.datetime(2026, 10, 3, 2, 30, tzinfo=dt.timezone.utc)
                self.assertEqual(discover_jobs.update_tracker(timestamp), 1)
                self.assertEqual(discover_jobs.update_tracker(timestamp), 0)
            state = __import__("json").loads(state_path.read_text())
            self.assertEqual(state["last_checked"], timestamp.isoformat())
            self.assertIn("https://example.com/rust", tracker_path.read_text())


if __name__ == "__main__":
    unittest.main()
