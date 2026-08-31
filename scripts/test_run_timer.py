#!/usr/bin/env python3
"""Tests for run_timer.py — coarse step timing bridged across separate
process invocations via a scratch file.

Run: python3 scripts/test_run_timer.py
"""
import time
import unittest
from unittest import mock

import run_timer


class RunTimerTests(unittest.TestCase):
    def setUp(self):
        self.skill = f"test-skill-{id(self)}"
        self.path = run_timer.timer_path(self.skill)
        if self.path.exists():
            self.path.unlink()

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_finish_with_no_start_returns_empty_dict(self):
        self.assertEqual(run_timer.finish(self.skill), {})

    def test_start_then_finish_reports_duration(self):
        with mock.patch("time.time", side_effect=[100.0, 100.5]):
            run_timer.start(self.skill)
            result = run_timer.finish(self.skill)
        self.assertAlmostEqual(result["duration_s"], 0.5)
        self.assertNotIn("steps", result)  # no marks recorded

    def test_marks_attribute_duration_to_the_step_that_finished(self):
        # t0=0, mark "a" at t=5 (a took 5s), mark "b" at t=8 (b took 3s),
        # finish at t=8 (no trailing unlabeled time recorded).
        with mock.patch("time.time", side_effect=[0.0, 5.0, 8.0, 8.0]):
            run_timer.start(self.skill)
            run_timer.mark(self.skill, "a")
            run_timer.mark(self.skill, "b")
            result = run_timer.finish(self.skill)
        self.assertEqual(result["steps"], {"a": 5.0, "b": 3.0})
        self.assertAlmostEqual(result["duration_s"], 8.0)

    def test_mark_with_no_active_timer_does_not_raise(self):
        run_timer.mark(self.skill, "orphan")  # no start() called
        self.assertFalse(self.path.exists())

    def test_finish_deletes_the_scratch_file(self):
        run_timer.start(self.skill)
        self.assertTrue(self.path.exists())
        run_timer.finish(self.skill)
        self.assertFalse(self.path.exists())

    def test_finish_on_corrupt_file_returns_empty_dict(self):
        self.path.write_text("not json")
        self.assertEqual(run_timer.finish(self.skill), {})

    def test_skill_names_are_sanitized_to_a_safe_filename(self):
        path = run_timer.timer_path("job-apply:job-apply")
        self.assertNotIn(":", path.name)
        self.assertTrue(path.name.startswith("resume-builder-run-"))

    def test_path_scoped_by_session_id_when_set(self):
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "abc-123"}):
            path = run_timer.timer_path(self.skill)
        self.assertIn("abc-123", path.name)

    def test_path_falls_back_to_skill_only_without_session_id(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            path = run_timer.timer_path(self.skill)
        self.assertEqual(path.name, f"resume-builder-run-{self.skill}.json")

    def test_concurrent_runs_of_same_skill_do_not_collide_across_sessions(self):
        # Two "forks" running the same skill concurrently must not share a
        # scratch file — this is the exact race from parallel-fork dispatch
        # in acting-on-results.md (several forks each running tailor-resume/
        # apply at once).
        path_a = None
        path_b = None
        try:
            with mock.patch("time.time", side_effect=[0.0, 100.0]):
                with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "fork-a"}):
                    run_timer.start(self.skill)
                    path_a = run_timer.timer_path(self.skill)
                with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "fork-b"}):
                    run_timer.start(self.skill)
                    path_b = run_timer.timer_path(self.skill)

            self.assertNotEqual(path_a, path_b)

            with mock.patch("time.time", return_value=100.0):
                with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "fork-a"}):
                    result_a = run_timer.finish(self.skill)
            with mock.patch("time.time", return_value=1100.0):
                with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "fork-b"}):
                    result_b = run_timer.finish(self.skill)

            self.assertAlmostEqual(result_a["duration_s"], 100.0)
            self.assertAlmostEqual(result_b["duration_s"], 1000.0)
        finally:
            for p in (path_a, path_b):
                if p and p.exists():
                    p.unlink()

    def test_two_skills_do_not_interfere(self):
        other_skill = self.skill + "-other"
        other_path = run_timer.timer_path(other_skill)
        try:
            with mock.patch("time.time", side_effect=[0.0, 0.0, 10.0, 10.0]):
                run_timer.start(self.skill)
                run_timer.start(other_skill)
                result_a = run_timer.finish(self.skill)
                result_b = run_timer.finish(other_skill)
            self.assertAlmostEqual(result_a["duration_s"], 10.0)
            self.assertAlmostEqual(result_b["duration_s"], 10.0)
        finally:
            if other_path.exists():
                other_path.unlink()


if __name__ == "__main__":
    unittest.main()
