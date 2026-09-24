#!/usr/bin/env python3
"""Tests for scan_fallback_days.py — computes the --days fallback bound for
a --since-last-scan job-scan run from the board's recorded last_scan_at.

Run: python3 scripts/test_scan_fallback_days.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from scan_fallback_days import compute_fallback_days

SCRIPT = os.path.join(os.path.dirname(__file__), "scan_fallback_days.py")


def run(args):
    return subprocess.run(
        [sys.executable, SCRIPT] + args, capture_output=True, text=True,
    )


class ComputeFallbackDaysTests(unittest.TestCase):
    def test_no_last_scan_at_uses_max_days(self):
        self.assertEqual(
            compute_fallback_days({}, "new-grad", datetime.now(timezone.utc)),
            14.0,
        )

    def test_recent_scan_uses_elapsed_plus_grace(self):
        now = datetime(2026, 9, 24, 21, 8, 30, tzinfo=timezone.utc)
        state = {"new-grad": {"last_scan_at": "2026-09-23T23:26:21+00:00"}}
        days = compute_fallback_days(state, "new-grad", now, grace_days=2.0)
        # ~0.905 elapsed days + 2 day grace
        self.assertAlmostEqual(days, 2.905, places=2)

    def test_result_never_below_min_days(self):
        now = datetime(2026, 9, 24, 0, 0, 1, tzinfo=timezone.utc)
        state = {"new-grad": {"last_scan_at": "2026-09-24T00:00:00+00:00"}}
        days = compute_fallback_days(state, "new-grad", now, grace_days=0.0, min_days=1.0)
        self.assertEqual(days, 1.0)

    def test_result_capped_at_max_days(self):
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        state = {"new-grad": {"last_scan_at": "2026-01-01T00:00:00+00:00"}}
        days = compute_fallback_days(state, "new-grad", now, max_days=14.0)
        self.assertEqual(days, 14.0)

    def test_missing_board_uses_max_days(self):
        state = {"internship": {"last_scan_at": "2026-09-23T00:00:00+00:00"}}
        days = compute_fallback_days(state, "new-grad", datetime.now(timezone.utc))
        self.assertEqual(days, 14.0)

    def test_naive_last_scan_at_treated_as_utc(self):
        now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        state = {"new-grad": {"last_scan_at": "2026-09-24T00:00:00"}}
        days = compute_fallback_days(state, "new-grad", now, grace_days=2.0)
        self.assertAlmostEqual(days, 2.5, places=2)


class CliTests(unittest.TestCase):
    def test_prints_max_days_for_fresh_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump({}, f)
            result = run(["--state-file", state_file, "--board", "new-grad"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(float(result.stdout.strip()), 14.0)

    def test_prints_computed_value_with_explicit_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump({"new-grad": {"last_scan_at": "2026-09-23T23:26:21+00:00"}}, f)
            result = run([
                "--state-file", state_file, "--board", "new-grad",
                "--now", "2026-09-24T21:08:30+00:00",
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(float(result.stdout.strip()), 2.905, places=2)

    def test_missing_state_file_uses_max_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "does-not-exist.json")
            result = run(["--state-file", state_file, "--board", "new-grad"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(float(result.stdout.strip()), 14.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
