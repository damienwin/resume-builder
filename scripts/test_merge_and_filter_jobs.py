#!/usr/bin/env python3
"""Tests for merge_and_filter_jobs.py — the deterministic dedupe and
already-applied logic the job-scan skill relies on.

Run: python3 scripts/test_merge_and_filter_jobs.py
"""
import datetime
import json
import os
import tempfile
import unittest

from merge_and_filter_jobs import (
    archive_index,
    canonical_url,
    dedupe_cross_source,
    filter_already_applied,
    roles_match,
)

UTC = datetime.timezone.utc


def days_ago(n):
    return datetime.datetime.now(tz=UTC) - datetime.timedelta(days=n)


def entry(company, role, url=None, source="simplify"):
    return {"company": company, "role": role, "apply_url": url, "source": source}


class CanonicalUrlTests(unittest.TestCase):
    def test_strips_tracking_params(self):
        a = canonical_url("https://job-boards.greenhouse.io/x/jobs/123?utm_source=Simplify&ref=Simplify")
        b = canonical_url("https://job-boards.greenhouse.io/x/jobs/123")
        self.assertEqual(a, b)

    def test_gh_jid_is_preserved(self):
        # On company-hosted Greenhouse pages the path is a shared careers page
        # and gh_jid is the only posting identifier. Stripping it collapsed 3
        # distinct live IXL postings into one.
        a = canonical_url("https://www.ixl.com/company/jobs?gh_jid=8662881002")
        b = canonical_url("https://www.ixl.com/company/jobs?gh_jid=8662882002")
        self.assertNotEqual(a, b)

    def test_keeps_meaningful_query_params(self):
        a = canonical_url("https://example.com/apply?jobId=7")
        b = canonical_url("https://example.com/apply?jobId=8")
        self.assertNotEqual(a, b)

    def test_trailing_slash_and_case_normalized(self):
        self.assertEqual(
            canonical_url("https://Example.COM/Apply/"),
            canonical_url("https://example.com/Apply"),
        )

    def test_none_url(self):
        self.assertIsNone(canonical_url(None))


class RolesMatchTests(unittest.TestCase):
    def test_identical_titles_match(self):
        self.assertTrue(roles_match("Backend Engineer, Payments", "Payments Backend Engineer"))

    def test_distinctive_keyword_overlap_matches(self):
        self.assertTrue(
            roles_match(
                "Software Engineer - Identity and Network Access",
                "Identity and Network Access",
            )
        )

    def test_different_teams_do_not_match(self):
        self.assertFalse(
            roles_match(
                "Software Engineer - Identity and Network Access",
                "Software Engineer - Officepy",
            )
        )

    def test_single_shared_token_is_not_a_match(self):
        # Real collapses seen on live board data under a min() denominator.
        self.assertFalse(roles_match("Data Analyst - Dashboard Developer", "Data Engineer 1"))
        self.assertFalse(
            roles_match("Wi-Fi Software Engineer - Starlink",
                        "Software Engineer New Grad - Software - Starlink")
        )

    def test_shared_graduation_year_is_not_a_match(self):
        self.assertFalse(
            roles_match("Systems Software Engineer - New College Grad 2026",
                        "Applied Machine Learning Engineer - New College Grad 2026 - Circuit Design")
        )

    def test_boilerplate_only_title_is_not_a_match(self):
        # Nothing distinctive survives stopword removal, so there is no
        # evidence either way — must not claim a match.
        self.assertFalse(roles_match("Software Engineer I", "New Grad Software Engineer"))

    def test_empty_role_never_matches(self):
        self.assertFalse(roles_match("Backend Engineer", ""))


class DedupeTests(unittest.TestCase):
    def test_same_url_collapses_and_simplify_wins(self):
        entries = [
            entry("Acme", "SWE New Grad", "https://x.com/j/1?utm_source=a", "speedyapply"),
            entry("Acme", "SWE New Grad", "https://x.com/j/1", "simplify"),
        ]
        kept, dropped = dedupe_cross_source(entries)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "simplify")

    def test_same_company_and_role_collapses_without_url(self):
        entries = [
            entry("Acme", "Backend Engineer, Payments", None, "speedyapply"),
            entry("Acme", "Payments Backend Engineer", None, "simplify"),
        ]
        kept, dropped = dedupe_cross_source(entries)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept[0]["source"], "simplify")

    def test_similar_roles_from_same_source_are_both_kept(self):
        # One company can run two similar openings on the same board; fuzzy
        # matching must not collapse them.
        entries = [
            entry("Acme", "Backend Engineer, Payments", None, "simplify"),
            entry("Acme", "Payments Backend Engineer", None, "simplify"),
        ]
        kept, dropped = dedupe_cross_source(entries)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 2)

    def test_same_source_entries_sharing_an_apply_url_are_both_kept(self):
        # A company pointing every posting at one careers page must not lose
        # all but one of them.
        entries = [
            entry("IXL", "Software Engineer New Grad", "https://ixl.com/company/jobs", "simplify"),
            entry("IXL", "Associate Product Manager New Grad", "https://ixl.com/company/jobs", "simplify"),
        ]
        kept, dropped = dedupe_cross_source(entries)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 2)

    def test_distinct_roles_at_same_company_both_kept(self):
        entries = [
            entry("Acme", "Compiler Engineer", "https://x.com/j/1"),
            entry("Acme", "Frontend Growth Engineer", "https://x.com/j/2"),
        ]
        kept, dropped = dedupe_cross_source(entries)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 2)


class AlreadyAppliedTests(unittest.TestCase):
    def test_recent_company_match_drops_without_role_check(self):
        entries = [entry("Acme", "Anything At All")]
        archive = [("Acme", "", days_ago(1))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, [])

    def test_older_match_with_matching_role_drops(self):
        entries = [entry("Acme", "Software Engineer - Identity and Network Access")]
        archive = [("Acme", "Identity and Network Access", days_ago(10))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 1)

    def test_older_match_with_different_role_is_kept_with_note(self):
        entries = [entry("Acme", "Compiler Engineer")]
        archive = [("Acme", "Frontend Growth Engineer", days_ago(10))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 0)
        self.assertEqual(len(kept), 1)
        self.assertIn("role not confirmed", kept[0]["applied_note"])

    def test_beyond_applied_window_is_ignored_entirely(self):
        entries = [entry("Acme", "Compiler Engineer")]
        archive = [("Acme", "Compiler Engineer", days_ago(60))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 0)
        self.assertNotIn("applied_note", kept[0])

    def test_similarly_named_companies_are_not_confused(self):
        # Live false positives under a substring test: an archived
        # "Citadel Securities" must not hide "Citadel", etc.
        for posting, archived in (
            ("Citadel", "Citadel Securities"),
            ("Intel", "IntelliGenesis"),
            ("KLA", "Klaviyo"),
            ("Intuit", "Applied Intuition"),
        ):
            entries = [entry(posting, "Software Engineer New Grad")]
            archive = [(archived, "", days_ago(1))]
            kept, dropped = filter_already_applied(entries, archive, 30, 3)
            self.assertEqual(dropped, 0, f"{archived!r} wrongly hid {posting!r}")

    def test_company_match_ignores_case_and_punctuation(self):
        entries = [entry("Acme, Inc.", "Compiler Engineer")]
        archive = [("acme inc", "", days_ago(1))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 1)

    def test_unrelated_company_untouched(self):
        entries = [entry("Globex", "Compiler Engineer")]
        archive = [("Acme", "Compiler Engineer", days_ago(1))]
        kept, dropped = filter_already_applied(entries, archive, 30, 3)
        self.assertEqual(dropped, 0)
        self.assertNotIn("applied_note", kept[0])


class ArchiveIndexBoardScopingTests(unittest.TestCase):
    """A resume tailored for one board must not suppress the other board's
    postings for the same company."""

    def write_metrics(self, records):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for r in records:
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def setUp(self):
        self.metrics = self.write_metrics([
            {"event": "resume_tailor", "timestamp": "2026-08-20T00:00:00+00:00",
             "company": "Acme", "role": "New Grad SWE",
             "output_path": "/Users/x/Desktop/Tailored Resumes/New Grad 27/Acme Resume.pdf"},
            {"event": "resume_tailor", "timestamp": "2026-08-20T00:00:00+00:00",
             "company": "Globex", "role": "SWE Intern",
             "output_path": "/Users/x/Desktop/Tailored Resumes/Summer 26/Globex Resume.pdf"},
            {"event": "job_scan", "timestamp": "2026-08-20T00:00:00+00:00"},
        ])

    def test_only_matching_board_events_are_indexed(self):
        idx = archive_index("/Users/x/Desktop/Tailored Resumes/New Grad 27", self.metrics)
        self.assertEqual([c for c, _, _ in idx], ["Acme"])

        idx = archive_index("/Users/x/Desktop/Tailored Resumes/Summer 26", self.metrics)
        self.assertEqual([c for c, _, _ in idx], ["Globex"])

    def test_no_archive_dir_uses_all_events(self):
        idx = archive_index(None, self.metrics)
        self.assertEqual(sorted(c for c, _, _ in idx), ["Acme", "Globex"])

    def test_non_tailor_events_ignored(self):
        idx = archive_index(None, self.metrics)
        self.assertEqual(len(idx), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
