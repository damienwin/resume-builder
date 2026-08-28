#!/usr/bin/env python3
"""Process-level regression test for the --since-last-scan flow:
parse_simplify_jobs.py / parse_speedyapply_jobs.py -> update_scan_state.py
-> re-parse with the persisted markers as --since.

This reproduces the exact bug fixed this session: a run whose --days window
excluded a whole category/section used to leave that category markerless in
category_top/section_top, so the NEXT --since-last-scan run had no marker to
break on and fell back to re-surfacing everything in its (wider) fallback
--days window. The fix captures category_top/section_top from the topmost
row in the source, before the --days filter and before the since-break, so
a second run over the SAME unchanged board content must come back with
zero entries.

Run: python3 scripts/test_since_last_scan_flow.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.dirname(__file__)
SIMPLIFY_SCRIPT = os.path.join(SCRIPTS_DIR, "parse_simplify_jobs.py")
SPEEDYAPPLY_SCRIPT = os.path.join(SCRIPTS_DIR, "parse_speedyapply_jobs.py")
UPDATE_SCRIPT = os.path.join(SCRIPTS_DIR, "update_scan_state.py")


def run(script, args):
    result = subprocess.run(
        [sys.executable, script] + args, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"{script} failed: {result.stderr}")
    return json.loads(result.stdout)


def simplify_readme():
    # First run's --days window (narrow) will exclude every row in "pm" -
    # that's the exact scenario that used to produce no category_top marker
    # for pm.
    return (
        "# Title\n\n"
        "## \U0001F4BB Software Engineering New Grad Roles\n\n<table>\n"
        '<tr><td><a href="https://simplify.jobs/c/acme">Acme</a></td>'
        "<td>SWE New Grad</td><td>Remote</td>"
        '<td><a href="https://apply.example.com/swe1">apply</a></td><td>0d</td></tr>\n'
        '<tr><td><a href="https://simplify.jobs/c/globex">Globex</a></td>'
        "<td>SWE New Grad</td><td>Remote</td>"
        '<td><a href="https://apply.example.com/swe2">apply</a></td><td>3d</td></tr>\n'
        "</table>\n\n"
        "## \U0001F4CB Product Management New Grad Roles\n\n<table>\n"
        '<tr><td><a href="https://simplify.jobs/c/initech">Initech</a></td>'
        "<td>PM New Grad</td><td>Remote</td>"
        '<td><a href="https://apply.example.com/pm1">apply</a></td><td>30d</td></tr>\n'
        "</table>\n\n"
    )


def speedyapply_md():
    return (
        "# Title\n\n"
        "<!-- TABLE_FAANG_START -->\n"
        "| Company | Position | Location | Salary | Application/Link | Age |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        '| <strong>Hooli</strong> | Software Engineer New Grad | Remote | $150k/yr | '
        '<a href="https://apply.example.com/faang1">apply</a> | 30d |\n'
        "<!-- TABLE_FAANG_END -->\n\n"
        "<!-- TABLE_QUANT_START -->\n"
        "| Company | Position | Location | Salary | Application/Link | Age |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "<!-- TABLE_QUANT_END -->\n\n"
        "<!-- TABLE_START -->\n"
        "| Company | Position | Location | Application/Link | Age |\n"
        "| --- | --- | --- | --- | --- |\n"
        '| <strong>Piedpiper</strong> | Software Engineer New Grad | Remote | '
        '<a href="https://apply.example.com/other1">apply</a> | 1d |\n'
        "<!-- TABLE_END -->\n"
    )


class SinceLastScanRegressionTests(unittest.TestCase):
    def test_zero_rows_pass_the_day_filter_still_yields_a_marker_and_stops_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme_path = os.path.join(tmp, "readme.md")
            with open(readme_path, "w") as f:
                f.write(simplify_readme())

            md_path = os.path.join(tmp, "speedyapply.md")
            with open(md_path, "w") as f:
                f.write(speedyapply_md())

            state_file = os.path.join(tmp, "state.json")

            # --- Run 1: narrow --days window. The pm row (30d) and the
            # faang row (30d) are excluded entirely by the day cutoff.
            simplify_out1 = run(SIMPLIFY_SCRIPT, [
                readme_path, "--categories", "swe,pm", "--days", "7",
            ])
            speedyapply_out1 = run(SPEEDYAPPLY_SCRIPT, [
                md_path, "--categories", "swe,pm", "--days", "7",
            ])

            # The bug scenario: zero pm entries and zero faang entries make
            # it through the day filter this run...
            self.assertEqual(
                [e for e in simplify_out1["entries"] if e["category"] == "pm"], []
            )
            self.assertEqual(
                [e for e in speedyapply_out1["entries"] if e["section"] == "faang"], []
            )
            # ...but the markers must still be captured for both.
            self.assertEqual(simplify_out1["category_top"]["pm"], "https://apply.example.com/pm1")
            self.assertEqual(simplify_out1["category_top"]["swe"], "https://apply.example.com/swe1")
            self.assertEqual(speedyapply_out1["section_top"]["faang"], "https://apply.example.com/faang1")
            self.assertEqual(speedyapply_out1["section_top"]["other"], "https://apply.example.com/other1")

            simplify_json1 = os.path.join(tmp, "simplify1.json")
            with open(simplify_json1, "w") as f:
                json.dump(simplify_out1, f)
            speedyapply_json1 = os.path.join(tmp, "speedyapply1.json")
            with open(speedyapply_json1, "w") as f:
                json.dump(speedyapply_out1, f)

            update_result = subprocess.run(
                [sys.executable, UPDATE_SCRIPT,
                 "--board", "new-grad", "--simplify", simplify_json1,
                 "--speedyapply", speedyapply_json1, "--state-file", state_file],
                capture_output=True, text=True,
            )
            self.assertEqual(update_result.returncode, 0, update_result.stderr)

            with open(state_file) as f:
                state = json.load(f)

            # --- Run 2: SAME unchanged board content, but now using the
            # persisted markers as --since, with a WIDE fallback --days
            # (simulating a much later scan where the fallback window would
            # otherwise re-surface everything).
            since_simplify = state["new-grad"]["simplify"]
            since_speedyapply = state["new-grad"]["speedyapply"]

            simplify_out2 = run(SIMPLIFY_SCRIPT, [
                readme_path, "--categories", "swe,pm", "--days", "365",
                "--since-json", json.dumps(since_simplify),
            ])
            speedyapply_out2 = run(SPEEDYAPPLY_SCRIPT, [
                md_path, "--categories", "swe,pm", "--days", "365",
                "--since-json", json.dumps(since_speedyapply),
            ])

            self.assertEqual(simplify_out2["entries"], [],
                              "since-marked simplify run must yield zero entries "
                              "on unchanged board content")
            self.assertEqual(speedyapply_out2["entries"], [],
                              "since-marked speedyapply run must yield zero entries "
                              "on unchanged board content")


if __name__ == "__main__":
    unittest.main(verbosity=2)
