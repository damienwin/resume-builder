#!/usr/bin/env python3
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from compile_verify import compile_and_verify, failing_checks, record_repair_log


class CompileVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tex = self.root / "resume.tex"
        self.tex.write_text("\\documentclass{article}\\begin{document}x\\end{document}")
        self.pdf = self.root / "resume.pdf"
        self.log = self.root / "resume.tectonic.log"

    def tearDown(self):
        self.temp.cleanup()

    def _fake_tectonic(self, exit_code=0, touch_pdf=False):
        script = self.root / "tectonic"
        body = "#!/bin/sh\n"
        if touch_pdf:
            body += 'touch "${1%.tex}.pdf"\n'
        body += f"exit {exit_code}\n"
        script.write_text(body)
        script.chmod(0o755)
        return script

    def test_missing_tectonic_fails_closed(self):
        report = compile_and_verify(self.tex, self.pdf, self.log,
                                    tectonic="definitely-not-a-real-binary-xyz")
        self.assertFalse(report["ok"])
        self.assertIn("not found", report["error"])

    def test_compile_failure_is_nonzero_and_records_log(self):
        report = compile_and_verify(self.tex, self.pdf, self.log,
                                    tectonic=str(self._fake_tectonic(exit_code=1)))
        self.assertFalse(report["ok"])
        self.assertFalse(report["compile"]["ok"])
        self.assertTrue(self.log.exists())

    def test_pdf_missing_after_zero_exit_is_a_failure(self):
        report = compile_and_verify(self.tex, self.pdf, self.log,
                                    tectonic=str(self._fake_tectonic(exit_code=0)))
        self.assertFalse(report["ok"])
        self.assertTrue(report["compile"]["ok"])
        self.assertFalse(report["verify"]["ok"])


class RepairLogTests(unittest.TestCase):
    """Per-attempt failing-check logging must accumulate, not overwrite —
    mirrors the run_timer.py label-collision fix for the same failure shape."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp.name) / "slug.repair_log.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_failing_checks_lists_check_names_only_for_failures(self):
        report = {
            "compile": {"ok": True},
            "checks": [
                {"check": "page_count", "ok": True},
                {"check": "fill", "ok": False},
                {"check": "overfull", "ok": False},
            ],
        }
        self.assertEqual(failing_checks(report), ["fill", "overfull"])

    def test_failing_checks_includes_compile_when_compile_failed(self):
        report = {"compile": {"ok": False}, "checks": []}
        self.assertEqual(failing_checks(report), ["compile"])

    def test_failing_checks_empty_when_all_pass(self):
        report = {"compile": {"ok": True}, "checks": [{"check": "fill", "ok": True}]}
        self.assertEqual(failing_checks(report), [])

    def test_record_repair_log_accumulates_across_calls_not_overwrites(self):
        attempt1 = {"ok": False, "compile": {"ok": True},
                    "checks": [{"check": "overfull", "ok": False}]}
        attempt2 = {"ok": False, "compile": {"ok": True},
                    "checks": [{"check": "fill", "ok": False}]}
        attempt3 = {"ok": True, "compile": {"ok": True},
                    "checks": [{"check": "fill", "ok": True}]}

        record_repair_log(self.log_path, attempt1)
        record_repair_log(self.log_path, attempt2)
        result = record_repair_log(self.log_path, attempt3)

        attempts = result["attempts"]
        self.assertEqual(len(attempts), 3)
        self.assertEqual([a["attempt"] for a in attempts], [1, 2, 3])
        self.assertEqual(attempts[0]["failing_checks"], ["overfull"])
        self.assertEqual(attempts[1]["failing_checks"], ["fill"])
        self.assertEqual(attempts[2]["failing_checks"], [])
        self.assertTrue(attempts[2]["ok"])

        on_disk = json.loads(self.log_path.read_text())
        self.assertEqual(on_disk, attempts)

    def test_record_repair_log_survives_corrupt_existing_file(self):
        self.log_path.write_text("not json")
        result = record_repair_log(
            self.log_path,
            {"ok": True, "compile": {"ok": True}, "checks": []},
        )
        self.assertEqual(len(result["attempts"]), 1)

    def test_compile_and_verify_report_feeds_repair_log_end_to_end(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        tex = root / "r.tex"
        tex.write_text("\\documentclass{article}\\begin{document}x\\end{document}")
        pdf = root / "r.pdf"
        log = root / "r.tectonic.log"
        script = root / "tectonic"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)

        report = compile_and_verify(tex, pdf, log, tectonic=str(script))
        result = record_repair_log(self.log_path, report)
        self.assertIn("compile", result["attempts"][0]["failing_checks"])
        temp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
