#!/usr/bin/env python3
"""Tests for prewarm_scan.py and install_prewarm_schedule.py. No network:
every subprocess is stubbed.

Run: python3 scripts/test_prewarm_scan.py
"""
import fcntl
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import install_prewarm_schedule as inst
import prewarm_scan as pw


ALLOWED_SCRIPTS = {"fetch_urls.py", "parse_simplify_jobs.py", "parse_speedyapply_jobs.py",
                   "merge_and_filter_jobs.py", "extract_comp.py", "check_eligibility.py",
                   "levels_fyi_est.py"}


def make_runner(entries, calls, jd_ok=True, fail=()):
    """Stub runner keyed on which script is invoked."""
    def runner(cmd, env):
        script = Path(cmd[1]).name
        calls.append(cmd)
        if script in fail:
            return 1, ""
        if script == "fetch_urls.py":
            urls = [l.split("\t")[0] for l in Path(cmd[cmd.index("--urls-file") + 1]).read_text().splitlines()]
            out = Path(cmd[cmd.index("--out-dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            results = []
            for i, u in enumerate(urls):
                if "raw.githubusercontent" in u:
                    name = Path(cmd[cmd.index("--urls-file") + 1]).read_text().splitlines()[i].split("\t")[1]
                    (out / name).write_text("x")
                    results.append({"url": u, "ok": True, "path": str(out / name)})
                else:
                    ok = jd_ok and "bad" not in u
                    r = {"url": u, "ok": ok}
                    if ok:
                        t = out / f"{i}.txt"
                        t.write_text("jd")
                        r["text_path"] = str(t)
                    results.append(r)
            m = {"results": results}
            if "--cache" in cmd:
                m.update(cache_hits=3, cache_misses=2)
            return 0, json.dumps(m)
        if script.startswith("parse_"):
            return 0, json.dumps({"entries": [], "scanned": 1})
        if script == "merge_and_filter_jobs.py":
            return 0, json.dumps({"entries": entries})
        if script == "extract_comp.py":
            paths = [c for c in cmd[2:] if c.endswith(".txt")]
            return 0, json.dumps({"results": [{"jd_path": p, "status": "none"} for p in paths]})
        return 0, "{}"
    return runner


ENTRIES = [
    {"company": "Acme", "apply_url": "https://a.example/1", "adv_degree": True, "salary": None},
    {"company": "Beta", "apply_url": "https://b.example/bad", "adv_degree": False, "salary": None},
    {"company": "Gamma", "apply_url": "https://c.example/3", "adv_degree": False, "salary": "$150k/yr"},
]


class PrewarmTests(unittest.TestCase):
    def run_prewarm(self, **kw):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            summary = pw.prewarm("new-grad", "swe,dsa,quant", 3, Path(d),
                                 runner=make_runner(kw.pop("entries", ENTRIES), calls, **kw),
                                 env={}, archive="/tmp/arch")
        return summary, calls

    def scripts(self, calls):
        return [Path(c[1]).name for c in calls]

    def test_happy_path_flags_and_subsets(self):
        summary, calls = self.run_prewarm()
        self.assertEqual(summary["postings"], 3)
        self.assertEqual((summary["cache_hits"], summary["cache_misses"]), (3, 2))
        self.assertEqual(summary["jd_failed"], 1)
        jd_fetch = [c for c in calls if "--strip-tags" in c][0]
        self.assertIn("--cache", jd_fetch)
        board_fetch = calls[0]
        self.assertNotIn("--cache", board_fetch)
        for s in ("extract_comp.py", "check_eligibility.py", "levels_fyi_est.py"):
            self.assertIn("--cache", [c for c in calls if Path(c[1]).name == s][0])
        # eligibility only for the one hatted row
        elig = [c for c in calls if Path(c[1]).name == "check_eligibility.py"][0]
        self.assertEqual(len([a for a in elig if a.endswith(".txt")]), 1)

    def test_levels_only_for_unstated_no_salary_companies(self):
        cap = {}
        real = pw.Path.write_text

        def spy(self, text, *a, **k):
            if self.name == "companies.txt":
                cap["c"] = text
            return real(self, text, *a, **k)
        with mock.patch.object(pw.Path, "write_text", spy):
            self.run_prewarm()
        # Beta's JD failed to fetch, Gamma has a salary -> only Acme
        self.assertEqual(cap["c"].split(), ["Acme"])

    def test_unsafe_urls_dropped_before_write(self):
        bad = ["https://x.example/a\tb", "https://x.example/a\nhttps://evil.example", "https://x.example/a b",
               "file:///etc/passwd", "ftp://x.example/1", "https://x.example/ok\n"]
        ents = [{"company": "Ok", "apply_url": "https://ok.example/1"}] + \
               [{"company": f"B{i}", "apply_url": u} for i, u in enumerate(bad)]
        seen = {}
        base = make_runner(ents, [])

        def runner(cmd, env):
            if "--strip-tags" in cmd:
                seen["urls"] = Path(cmd[cmd.index("--urls-file") + 1]).read_text()
            return base(cmd, env)
        with tempfile.TemporaryDirectory() as d:
            summary = pw.prewarm("new-grad", "swe", 3, Path(d), runner=runner, env={}, archive="/a")
        self.assertEqual(seen["urls"], "https://ok.example/1\n")
        self.assertEqual(summary["urls_dropped"], len(bad))

    def test_archive_failure_retries_without_archive(self):
        calls = []
        base = make_runner(ENTRIES, calls)

        def runner(cmd, env):
            if Path(cmd[1]).name == "merge_and_filter_jobs.py" and "--archive" in cmd:
                calls.append(cmd)
                return 1, ""
            return base(cmd, env)
        with tempfile.TemporaryDirectory() as d:
            summary = pw.prewarm("new-grad", "swe", 3, Path(d), runner=runner, env={}, archive="/a")
        merges = [c for c in calls if Path(c[1]).name == "merge_and_filter_jobs.py"]
        self.assertEqual(len(merges), 2)
        self.assertNotIn("--archive", merges[1])
        self.assertEqual(summary["archive"], "skipped")
        self.assertEqual(summary["postings"], 3)

    def test_merge_failure_without_archive_still_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_prewarm(fail=("merge_and_filter_jobs.py",))

    def test_main_never_touches_scan_state_or_knowledge(self):
        """Real main() + real prewarm() against a tmp repo and tmp cache root."""
        with tempfile.TemporaryDirectory() as d:
            repo, cache = Path(d) / "repo", Path(d) / "cache"
            (repo / "knowledge").mkdir(parents=True)
            (repo / "knowledge" / "profile.md").write_text("secret")
            (repo / "knowledge" / "rules.md").write_text("`/nonexistent/Tailored Resumes/` x")
            (repo / ".env").write_text("TYPESAFE_API_KEY=fake-from-env-file\n")

            def snap():
                return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
            before = snap()
            calls, envs = [], []
            base = make_runner(ENTRIES, calls)

            def runner(cmd, env):
                envs.append(env)
                return base(cmd, env)
            clean_env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
            clean_env["RESUME_BUILDER_CACHE"] = str(cache)
            with mock.patch.dict(os.environ, clean_env, clear=True), \
                 mock.patch.object(pw, "REPO_ROOT", repo), mock.patch.object(pw, "run_cmd", runner), \
                 mock.patch.object(pw, "log_metric"), mock.patch("builtins.print"):
                self.assertEqual(pw.main([]), 0)
            self.assertEqual(snap(), before)  # knowledge/, .env untouched; nothing new in repo
            self.assertFalse(list(Path(d).rglob("job_scan_state.json")))
            self.assertFalse(list(Path(d).rglob("last_scan_at*")))
            self.assertIn("/nonexistent/Tailored Resumes/New Grad 27",
                          " ".join(" ".join(c) for c in calls))  # tmp rules.md, not the real one
            self.assertTrue(all(e.get("TYPESAFE_API_KEY") == "fake-from-env-file" for e in envs))
            self.assertNotIn("job_scan_state", " ".join(" ".join(c) for c in calls))
            self.assertLessEqual({Path(c[1]).name for c in calls}, ALLOWED_SCRIPTS)
            self.assertEqual([p.name for p in cache.iterdir() if p.is_dir()], [])  # scratch cleaned

    def test_every_flag_passed_exists_in_real_script_help(self):
        _, calls = self.run_prewarm()
        called = {Path(c[1]).name for c in calls}
        self.assertEqual(called, ALLOWED_SCRIPTS)
        flags = {}
        for c in calls:
            flags.setdefault(Path(c[1]).name, set()).update(a for a in c[2:] if a.startswith("--"))
        for script, fl in flags.items():
            r = subprocess.run([sys.executable, str(pw.SCRIPTS / script), "--help"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")
            for f in fl:
                self.assertIn(f, r.stdout, f"{script} no longer accepts {f}")

    def test_simplify_fetch_failure_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_prewarm(fail=("fetch_urls.py",))

    def test_bad_jd_urls_dont_abort(self):
        summary, _ = self.run_prewarm(jd_ok=False)
        self.assertEqual(summary["jd_failed"], 3)

    def test_extract_comp_failure_tolerated(self):
        summary, _ = self.run_prewarm(fail=("extract_comp.py", "check_eligibility.py", "levels_fyi_est.py"))
        self.assertEqual(summary["postings"], 3)

    def test_zero_entries_skips_jd_fetch(self):
        summary, calls = self.run_prewarm(entries=[])
        self.assertEqual(summary["postings"], 0)
        self.assertFalse([c for c in calls if "--strip-tags" in c])


class LogAndLockTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def test_log_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "prewarm.log"
            for i in range(12):
                pw.append_log(p, f"line{i}", keep=5)
            self.assertEqual(p.read_text().splitlines(), [f"line{i}" for i in range(7, 12)])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": d}):
            self.assertEqual(pw.main(["--dry-run"]), 0)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_held_lock_exits_quietly(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": d}):
            held = open(Path(d) / "prewarm.lock", "a")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            with mock.patch.object(pw, "prewarm", side_effect=AssertionError("ran")):
                self.assertEqual(pw.main([]), 0)
            self.assertFalse((Path(d) / "prewarm.log").exists())
            held.close()

    def test_main_failure_exit_and_log(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": d}):
            with mock.patch.object(pw, "prewarm", side_effect=RuntimeError("boom")), \
                 mock.patch.object(pw, "log_metric") as lm:
                self.assertEqual(pw.main([]), 1)
                lm.assert_not_called()
            self.assertIn("error=boom", (Path(d) / "prewarm.log").read_text())

    def test_main_success_logs_metric(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": d}):
            s = {"postings": 4, "cache_hits": 1, "cache_misses": 2}
            with mock.patch.object(pw, "prewarm", return_value=s), mock.patch.object(pw, "log_metric") as lm:
                self.assertEqual(pw.main([]), 0)
                lm.assert_called_once()
            self.assertIn("postings=4 cache_hits=1 cache_misses=2", (Path(d) / "prewarm.log").read_text())
            self.assertEqual([p.name for p in Path(d).iterdir() if p.is_dir()], [])


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def env(self, d):
        e = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        e["RESUME_BUILDER_CACHE"] = d
        return mock.patch.dict(os.environ, e, clear=True)

    def test_unusable_cache_root_nonzero_no_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("x")
            with self.env(str(blocker / "sub")):
                self.assertEqual(pw.main([]), 1)

    def test_log_write_failure_still_nonzero_and_metric_failure_ignored(self):
        with tempfile.TemporaryDirectory() as d, self.env(d), \
             mock.patch.object(pw, "prewarm", return_value={"postings": 1, "cache_hits": 0, "cache_misses": 0}), \
             mock.patch.object(pw, "append_log", side_effect=OSError("disk full")), \
             mock.patch.object(pw, "log_metric", side_effect=OSError("disk full")):
            self.assertEqual(pw.main([]), 0)  # run itself succeeded; no traceback
        with tempfile.TemporaryDirectory() as d, self.env(d), \
             mock.patch.object(pw, "prewarm", side_effect=RuntimeError("x")), \
             mock.patch.object(pw, "append_log", side_effect=OSError("disk full")):
            self.assertEqual(pw.main([]), 1)

    def test_stale_scratch_removed_at_start(self):
        with tempfile.TemporaryDirectory() as d, self.env(d):
            stale = Path(d) / "prewarm-old"
            stale.mkdir()
            (stale / "x").write_text("y")
            keep = Path(d) / "other-dir"
            keep.mkdir()
            seen = {}

            def fake(*a, **k):
                seen["stale"] = stale.exists()
                return {"postings": 0, "cache_hits": 0, "cache_misses": 0}
            with mock.patch.object(pw, "prewarm", side_effect=fake), mock.patch.object(pw, "log_metric"):
                pw.main([])
            self.assertFalse(seen["stale"])
            self.assertTrue(keep.exists())

    def test_sigterm_runs_cleanup_and_logs(self):
        with tempfile.TemporaryDirectory() as d, self.env(d):
            def fake(board, cats, days, work, **k):
                (work / "f").write_text("x")
                os.kill(os.getpid(), signal.SIGTERM)
                self.fail("signal not raised")
            before = signal.getsignal(signal.SIGTERM)
            with mock.patch.object(pw, "prewarm", side_effect=fake), mock.patch.object(pw, "log_metric") as lm:
                self.assertEqual(pw.main([]), 1)
                lm.assert_not_called()
            self.assertEqual([p.name for p in Path(d).iterdir() if p.is_dir()], [])
            self.assertIn("error=terminated", (Path(d) / "prewarm.log").read_text())
            self.assertIs(signal.getsignal(signal.SIGTERM), before)


class PlistTests(unittest.TestCase):
    def test_render(self):
        text = inst.render(inst.TEMPLATE.read_text(), "/usr/bin/python3", Path("/repo"), Path("/cache"))
        self.assertNotIn("<<", text)
        d = plistlib.loads(text.encode())
        self.assertEqual(d["Label"], inst.LABEL)
        self.assertEqual(d["ProgramArguments"], ["/usr/bin/python3", "/repo/scripts/prewarm_scan.py"])
        self.assertEqual([(i["Hour"], i["Minute"]) for i in d["StartCalendarInterval"]], [(7, 0), (12, 0)])
        self.assertNotIn("KeepAlive", d)
        self.assertTrue(d["StandardOutPath"].startswith("/cache/"))
        self.assertEqual(list(d["EnvironmentVariables"]), ["PATH"])

    def test_render_environment_variables_escaped(self):
        env = inst.launchd_env({"RESUME_BUILDER_CACHE": "/c&c/<x>", "RESUME_BUILDER_JEV": "off"})
        text = inst.render(inst.TEMPLATE.read_text(), "/py", Path("/r&r"), Path("/c&c/<x>"), env)
        d = plistlib.loads(text.encode())
        self.assertEqual(d["EnvironmentVariables"]["RESUME_BUILDER_CACHE"], "/c&c/<x>")
        self.assertEqual(d["EnvironmentVariables"]["RESUME_BUILDER_JEV"], "off")
        self.assertIn("PATH", d["EnvironmentVariables"])
        self.assertEqual(d["StandardErrorPath"], "/c&c/<x>/prewarm.stderr.log")
        self.assertEqual(d["WorkingDirectory"], "/r&r")

    def test_launchd_env_only_set_vars(self):
        self.assertEqual(list(inst.launchd_env({})), ["PATH"])
        e = inst.launchd_env({"RESUME_BUILDER_CACHE": "~/cc"})
        self.assertEqual(e["RESUME_BUILDER_CACHE"], str(Path("~/cc").expanduser()))
        self.assertNotIn("RESUME_BUILDER_JEV", e)

    def test_install_print_uses_effective_cache_root(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": d, "RESUME_BUILDER_JEV": "off"}):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(inst.main(["install", "--print"]), 0)
            xml = buf.getvalue().split("\n", 1)[1].split("# would run")[0]
            p = plistlib.loads(xml.encode())
            self.assertEqual(p["EnvironmentVariables"]["RESUME_BUILDER_CACHE"], d)
            self.assertEqual(p["EnvironmentVariables"]["RESUME_BUILDER_JEV"], "off")
            self.assertEqual(p["StandardOutPath"], f"{d}/prewarm.stdout.log")

    def test_print_does_not_write_or_launch(self):
        with mock.patch.object(inst, "_launchctl") as lc, mock.patch.object(inst, "plist_path") as pp:
            self.assertEqual(inst.main(["install", "--print"]), 0)
            self.assertEqual(inst.main(["uninstall", "--print"]), 0)
            lc.assert_not_called()
            pp.return_value.write_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
