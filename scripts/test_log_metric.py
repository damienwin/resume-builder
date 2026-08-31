#!/usr/bin/env python3
"""Tests for log_metric.py — appends a single event to knowledge/metrics.jsonl.

The script derives its output path from its own file location
(Path(__file__).resolve().parent.parent / "knowledge" / "metrics.jsonl"), so
these tests run a copy of it from a scratch <tmp>/scripts/log_metric.py,
which makes its computed repo root <tmp> — this exercises the real script
via subprocess without touching this repo's actual knowledge/metrics.jsonl.

Run: python3 scripts/test_log_metric.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_SRC = Path(__file__).resolve().parent / "log_metric.py"


def run_in_scratch_repo(args, env_overrides=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scripts_dir = tmp / "scripts"
        scripts_dir.mkdir()
        script_copy = scripts_dir / "log_metric.py"
        shutil.copy(SCRIPT_SRC, script_copy)

        env = os.environ.copy()
        env.pop("RESUME_BUILDER_VARIANT", None)
        if env_overrides:
            env.update(env_overrides)

        result = subprocess.run(
            [sys.executable, str(script_copy)] + args,
            capture_output=True, text=True, env=env,
        )
        metrics_path = tmp / "knowledge" / "metrics.jsonl"
        records = []
        if metrics_path.exists():
            for line in metrics_path.read_text().splitlines():
                if line.strip():
                    records.append(json.loads(line))
        return result, records


class LogMetricTests(unittest.TestCase):
    def test_writes_event_type_and_fields(self):
        result, records = run_in_scratch_repo(
            ["job_scan", '{"board": "new-grad", "surfaced": 20}']
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "job_scan")
        self.assertEqual(records[0]["board"], "new-grad")
        self.assertEqual(records[0]["surfaced"], 20)
        self.assertIn("timestamp", records[0])

    def test_appends_rather_than_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            scripts_dir = tmp / "scripts"
            scripts_dir.mkdir()
            script_copy = scripts_dir / "log_metric.py"
            shutil.copy(SCRIPT_SRC, script_copy)
            env = os.environ.copy()
            env.pop("RESUME_BUILDER_VARIANT", None)

            for i in range(2):
                subprocess.run(
                    [sys.executable, str(script_copy), "job_scan", f'{{"n": {i}}}'],
                    capture_output=True, text=True, env=env,
                )
            metrics_path = tmp / "knowledge" / "metrics.jsonl"
            lines = [l for l in metrics_path.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)

    def test_invalid_json_fields_exits_nonzero(self):
        result, records = run_in_scratch_repo(["job_scan", "not json"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(records, [])

    def test_non_object_fields_exits_nonzero(self):
        result, records = run_in_scratch_repo(["job_scan", "[1, 2, 3]"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(records, [])

    def test_env_variant_tags_the_record(self):
        result, records = run_in_scratch_repo(
            ["job_scan", "{}"], env_overrides={"RESUME_BUILDER_VARIANT": "control"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(records[0]["variant"], "control")

    def test_explicit_variant_field_wins_over_env(self):
        result, records = run_in_scratch_repo(
            ["job_scan", '{"variant": "treatment"}'],
            env_overrides={"RESUME_BUILDER_VARIANT": "control"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(records[0]["variant"], "treatment")

    def test_no_variant_field_when_env_unset(self):
        result, records = run_in_scratch_repo(["job_scan", "{}"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("variant", records[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
