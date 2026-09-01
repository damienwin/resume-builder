#!/usr/bin/env python3
"""Tests for run_timer.py — coarse step timing bridged across separate
process invocations via a scratch file.

Run: python3 scripts/test_run_timer.py
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
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

    def test_parallel_forks_sharing_a_session_id_need_scope(self):
        """The 2026-09-01 fan-out bug: six forks, one session id, one file.

        Session scoping alone does not separate parallel forks — they were
        observed sharing a CLAUDE_CODE_SESSION_ID. Without --scope the first
        finish() unlinks the shared file and every later fork gets {}.
        """
        same_session = {"CLAUDE_CODE_SESSION_ID": "shared-session"}
        paths = []
        try:
            with mock.patch.dict("os.environ", same_session):
                paths = [run_timer.timer_path(self.skill) for _ in range(2)]
                self.assertEqual(paths[0], paths[1])  # the collision itself

                with mock.patch("time.time", return_value=1000.0):
                    run_timer.start(self.skill)
                with mock.patch("time.time", return_value=1010.0):
                    run_timer.start(self.skill)
                with mock.patch("time.time", return_value=1100.0):
                    first = run_timer.finish(self.skill)
                with mock.patch("time.time", return_value=1200.0):
                    second = run_timer.finish(self.skill)

            self.assertIn("duration_s", first)
            self.assertEqual(second, {})  # the data loss this fix prevents
        finally:
            for p in paths:
                if p.exists():
                    p.unlink()

    def test_scope_isolates_forks_that_share_a_session_id(self):
        """Same shared session id, but each fork passes its own job slug."""
        same_session = {"CLAUDE_CODE_SESSION_ID": "shared-session"}
        path_a = path_b = None
        try:
            with mock.patch.dict("os.environ", same_session):
                path_a = run_timer.timer_path(self.skill, "zoox-resume")
                path_b = run_timer.timer_path(self.skill, "snap-resume")
                self.assertNotEqual(path_a, path_b)

                with mock.patch("time.time", return_value=1000.0):
                    run_timer.start(self.skill, "zoox-resume")
                with mock.patch("time.time", return_value=1010.0):
                    run_timer.start(self.skill, "snap-resume")
                with mock.patch("time.time", return_value=1050.0):
                    run_timer.mark(self.skill, "compile", "zoox-resume")
                with mock.patch("time.time", return_value=1100.0):
                    result_a = run_timer.finish(self.skill, "zoox-resume")
                with mock.patch("time.time", return_value=1210.0):
                    result_b = run_timer.finish(self.skill, "snap-resume")

            self.assertAlmostEqual(result_a["duration_s"], 100.0)
            self.assertEqual(result_a["steps"], {"compile": 50.0})
            self.assertAlmostEqual(result_b["duration_s"], 200.0)
            self.assertNotIn("steps", result_b)  # zoox's mark stayed in zoox
        finally:
            for p in (path_a, path_b):
                if p is not None and p.exists():
                    p.unlink()

    def test_scope_survives_unset_session_id(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        path_a = path_b = None
        try:
            with mock.patch.dict("os.environ", env, clear=True):
                path_a = run_timer.timer_path(self.skill, "job-a")
                path_b = run_timer.timer_path(self.skill, "job-b")
                self.assertNotEqual(path_a, path_b)
                with mock.patch("time.time", return_value=0.0):
                    run_timer.start(self.skill, "job-a")
                    run_timer.start(self.skill, "job-b")
                with mock.patch("time.time", return_value=5.0):
                    a = run_timer.finish(self.skill, "job-a")
                with mock.patch("time.time", return_value=9.0):
                    b = run_timer.finish(self.skill, "job-b")
            self.assertAlmostEqual(a["duration_s"], 5.0)
            self.assertAlmostEqual(b["duration_s"], 9.0)
        finally:
            for p in (path_a, path_b):
                if p is not None and p.exists():
                    p.unlink()

    def test_scope_sanitized_into_filename(self):
        path = run_timer.timer_path(self.skill, "acme corp/resume v2")
        self.assertNotIn("/", path.name)
        self.assertNotIn(" ", path.name)
        self.assertIn("acme_corp_resume_v2", path.name)

    def test_cli_parses_scope_flag_in_any_position(self):
        import subprocess

        script = str(Path(run_timer.__file__))
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID="cli-test")
        # resolve the path under the SAME env the subprocess will see
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "cli-test"}):
            scoped = run_timer.timer_path("cli-skill", "slug-1")
        try:
            subprocess.run(
                [sys.executable, script, "start", "cli-skill", "--scope", "slug-1"],
                check=True, env=env, capture_output=True,
            )
            self.assertTrue(scoped.exists())
            subprocess.run(
                [sys.executable, script, "mark", "cli-skill", "compile",
                 "--scope", "slug-1"],
                check=True, env=env, capture_output=True,
            )
            state = json.loads(scoped.read_text())
            self.assertEqual([m["label"] for m in state["marks"]], ["compile"])

            # an unscoped finish must NOT find (or delete) the scoped run
            out = subprocess.run(
                [sys.executable, script, "finish", "cli-skill"],
                check=True, env=env, capture_output=True, text=True,
            )
            self.assertEqual(json.loads(out.stdout), {})
            self.assertTrue(scoped.exists())

            out = subprocess.run(
                [sys.executable, script, "finish", "cli-skill", "--scope", "slug-1"],
                check=True, env=env, capture_output=True, text=True,
            )
            self.assertIn("duration_s", json.loads(out.stdout))
            self.assertFalse(scoped.exists())
        finally:
            if scoped.exists():
                scoped.unlink()


if __name__ == "__main__":
    unittest.main()
