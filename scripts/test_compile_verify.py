#!/usr/bin/env python3
import shutil
import tempfile
import unittest
from pathlib import Path

from compile_verify import compile_and_verify


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
