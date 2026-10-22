import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.discover_jobs as discover_jobs
import scripts.tailor_applications as tailor


RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Acme Corp: Rust Backend Engineer</title>
<link>https://example.com/rust-job</link>
<pubDate>Wed, 07 Oct 2026 17:30:55 +0000</pubDate>
<description>&lt;p&gt;Build a &lt;b&gt;Rust&lt;/b&gt; trading backend&lt;/p&gt;</description></item>
<item><title>Senior DevOps Engineer</title>
<link>https://example.com/devops</link>
<pubDate>Thu, 08 Oct 2026 10:00:00 +0000</pubDate>
<description>Kubernetes and Terraform work</description></item>
</channel></rss>"""


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


class NewSourceTests(unittest.TestCase):
    def test_parse_rss_extracts_items(self):
        items = discover_jobs.parse_rss(RSS_SAMPLE)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["link"], "https://example.com/rust-job")
        self.assertIn("Rust", items[0]["description"])

    def test_rss_normalizer_splits_company_and_matches_rust_in_description(self):
        items = discover_jobs.parse_rss(RSS_SAMPLE)
        jobs = discover_jobs.normalize_wwr(items)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Acme Corp")
        self.assertEqual(jobs[0]["title"], "Rust Backend Engineer")
        self.assertEqual(jobs[0]["source"], "We Work Remotely")
        self.assertEqual(jobs[0]["match"], "Rust-related")
        self.assertNotIn("<p>", jobs[0]["description"])

    def test_normalize_remotive_keeps_description(self):
        jobs = discover_jobs.normalize_remotive({
            "jobs": [{
                "id": 7, "title": "Backend Intern (Rust)", "company_name": "Example",
                "url": "https://remotive.com/jobs/7", "candidate_required_location": "Remote",
                "publication_date": "2026-10-01T08:00:00Z", "tags": ["rust"],
                "description": "<p>Work in Rust</p>",
            }],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "Remotive")
        self.assertIn("Work in Rust", jobs[0]["description"])

    def test_normalize_jobicy(self):
        jobs = discover_jobs.normalize_jobicy({
            "jobs": [{
                "id": 3, "jobTitle": "Blockchain Developer Intern", "companyName": "ChainCo",
                "url": "https://jobicy.com/jobs/3", "jobGeo": "Remote",
                "pubDate": "2026-10-01T08:00:00Z", "tags": ["blockchain"],
                "jobExcerpt": "Smart contracts",
            }],
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "ChainCo")
        self.assertEqual(jobs[0]["match"], "Internship / early-career software")

    def test_to_iso_parses_rfc822(self):
        self.assertEqual(
            discover_jobs.to_iso("Wed, 07 Oct 2026 17:30:55 +0000"),
            "2026-10-07T17:30:55+00:00",
        )


class TailorHelperTests(unittest.TestCase):
    def test_escape_latex_neutralizes_specials(self):
        escaped = tailor.escape_latex("Rust & Go_100% {x} $5 #tag ~^")
        self.assertEqual(escaped, r"Rust \& Go\_100\% \{x\} \$5 \#tag \textasciitilde{}\textasciicircum{}")

    def test_extract_json_from_noisy_output(self):
        text = 'Sure! Here is the JSON:\n{"score": 72, "verdict": "apply"}\nHope that helps.'
        self.assertEqual(tailor.extract_json(text)["score"], 72)

    def test_extract_json_returns_none_for_garbage(self):
        self.assertIsNone(tailor.extract_json("no json here"))

    def test_slugify(self):
        self.assertEqual(tailor.slugify("Acme Corp -- Senior Rust Dev!"), "acme-corp-senior-rust-dev")

    def test_select_candidates_caps_and_skips_processed(self):
        state = {"jobs": {
            "a": {"key": "a", "title": "T1", "company": "C1", "first_found": "2026-10-02"},
            "b": {"key": "b", "title": "T2", "company": "C2", "first_found": "2026-10-03"},
            "c": {"key": "c", "title": "T3", "company": "C3", "first_found": "2026-10-01"},
        }}
        with patch.object(tailor, "MAX_JOBS_PER_RUN", 1):
            picked = tailor.select_candidates(state, {"b": {"folder": "x"}})
        self.assertEqual([job["key"] for job in picked], ["a"])


if __name__ == "__main__":
    unittest.main()
