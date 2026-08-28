#!/usr/bin/env python3
"""Tests for update_scan_state.py — persists the --since-last-scan markers
(`category_top` from the simplify parser, `section_top` from the speedyapply
parser) into job-scan's state file after a run.

Run: python3 scripts/test_update_scan_state.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "update_scan_state.py")


def write_json(tmpdir, name, data):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def run_update(args):
    result = subprocess.run(
        [sys.executable, SCRIPT] + args,
        capture_output=True, text=True,
    )
    return result


class FreshStateFileTests(unittest.TestCase):
    def test_creates_state_file_with_simplify_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            simplify_json = write_json(tmp, "simplify.json", {
                "entries": [], "scanned": 0, "closed_excluded": 0,
                "category_top": {"swe": "https://a.com/swe1", "pm": "https://a.com/pm1"},
            })
            state_file = os.path.join(tmp, "state.json")
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state, {
                "new-grad": {"simplify": {"swe": "https://a.com/swe1", "pm": "https://a.com/pm1"}}
            })

    def test_creates_state_file_with_simplify_and_speedyapply(self):
        with tempfile.TemporaryDirectory() as tmp:
            simplify_json = write_json(tmp, "simplify.json", {
                "entries": [], "scanned": 0, "closed_excluded": 0,
                "category_top": {"swe": "https://a.com/swe1"},
            })
            speedyapply_json = write_json(tmp, "speedyapply.json", {
                "entries": [], "scanned": 0, "closed_excluded": 0,
                "section_top": {"other": "https://a.com/other1"},
            })
            state_file = os.path.join(tmp, "state.json")
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--speedyapply", speedyapply_json, "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["new-grad"]["simplify"], {"swe": "https://a.com/swe1"})
            self.assertEqual(state["new-grad"]["speedyapply"], {"other": "https://a.com/other1"})

    def test_creates_parent_directory_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            simplify_json = write_json(tmp, "simplify.json", {
                "category_top": {"swe": "https://a.com/swe1"},
            })
            state_file = os.path.join(tmp, "nested", "dir", "state.json")
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.exists(state_file))


class ExistingStateFileUpdateTests(unittest.TestCase):
    def test_untouched_categories_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump({
                    "new-grad": {"simplify": {"swe": "https://a.com/old-swe",
                                               "pm": "https://a.com/old-pm"}}
                }, f)

            # This run only produced a new marker for "swe"; "pm" had zero
            # rows above the fold (e.g. filtered out entirely) so it's
            # absent from category_top and must be left alone.
            simplify_json = write_json(tmp, "simplify.json", {
                "category_top": {"swe": "https://a.com/new-swe"},
            })
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["new-grad"]["simplify"], {
                "swe": "https://a.com/new-swe",
                "pm": "https://a.com/old-pm",
            })

    def test_other_boards_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump({
                    "internship": {"simplify": {"swe": "https://a.com/intern-swe"}}
                }, f)

            simplify_json = write_json(tmp, "simplify.json", {
                "category_top": {"swe": "https://a.com/new-grad-swe"},
            })
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["internship"]["simplify"], {"swe": "https://a.com/intern-swe"})
            self.assertEqual(state["new-grad"]["simplify"], {"swe": "https://a.com/new-grad-swe"})

    def test_omitting_speedyapply_leaves_existing_speedyapply_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w") as f:
                json.dump({
                    "new-grad": {
                        "simplify": {"swe": "https://a.com/old-swe"},
                        "speedyapply": {"other": "https://a.com/old-other"},
                    }
                }, f)

            simplify_json = write_json(tmp, "simplify.json", {
                "category_top": {"swe": "https://a.com/new-swe"},
            })
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["new-grad"]["simplify"], {"swe": "https://a.com/new-swe"})
            self.assertEqual(state["new-grad"]["speedyapply"], {"other": "https://a.com/old-other"})


class RoundtripTests(unittest.TestCase):
    def test_reads_category_top_and_section_top_keys_specifically(self):
        # A parser JSON also contains "entries" and "scanned" - make sure
        # those aren't mistaken for the marker keys.
        with tempfile.TemporaryDirectory() as tmp:
            simplify_json = write_json(tmp, "simplify.json", {
                "entries": [{"company": "ShouldBeIgnored", "apply_url": "https://a.com/entry"}],
                "scanned": 5,
                "closed_excluded": 1,
                "category_top": {"swe": "https://a.com/swe-top"},
            })
            speedyapply_json = write_json(tmp, "speedyapply.json", {
                "entries": [{"company": "AlsoIgnored", "apply_url": "https://a.com/entry2"}],
                "scanned": 3,
                "closed_excluded": 0,
                "section_top": {"faang": "https://a.com/faang-top"},
            })
            state_file = os.path.join(tmp, "state.json")
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--speedyapply", speedyapply_json, "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["new-grad"]["simplify"], {"swe": "https://a.com/swe-top"})
            self.assertEqual(state["new-grad"]["speedyapply"], {"faang": "https://a.com/faang-top"})

    def test_missing_category_top_key_results_in_empty_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            simplify_json = write_json(tmp, "simplify.json", {"entries": [], "scanned": 0})
            state_file = os.path.join(tmp, "state.json")
            result = run_update([
                "--board", "new-grad", "--simplify", simplify_json,
                "--state-file", state_file,
            ])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(state_file) as f:
                state = json.load(f)
            self.assertEqual(state["new-grad"]["simplify"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
