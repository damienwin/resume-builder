#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from resume_handoff import build_manifest, verify_manifest


class ResumeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jd = self.root / "jd.txt"
        self.jd.write_text("Requires performance engineering.")
        self.pdf = self.root / "resume.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 tailored")
        self.tex = self.root / "resume.tex"
        self.tex.write_text("\\resumeItem{Reduced latency by 40\\%}")
        self.gate = self.root / "final-quality.json"
        self.gate.write_text(json.dumps({"ok": True, "checks": []}))
        self.manifest_path = self.root / "handoff.json"

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, gate_ok=True, url="https://example.com/job"):
        self.gate.write_text(json.dumps({"ok": gate_ok, "checks": []}))
        manifest = build_manifest(url, self.jd, self.pdf, self.tex, self.gate, "claude/sonnet")
        self.manifest_path.write_text(json.dumps(manifest))
        return manifest

    def test_valid_manifest_verifies(self):
        self._write()
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job", pdf=self.pdf)
        self.assertTrue(result["valid"], result["reasons"])

    def test_failed_gate_invalidates(self):
        self._write(gate_ok=False)
        result = verify_manifest(self.manifest_path)
        self.assertFalse(result["valid"])
        self.assertIn("final gate did not pass", result["reasons"])

    def test_url_mismatch_invalidates(self):
        self._write(url="https://example.com/job")
        result = verify_manifest(self.manifest_path, posting_url="https://example.com/other")
        self.assertFalse(result["valid"])
        self.assertIn("posting URL mismatch", result["reasons"])

    def test_changed_pdf_invalidates(self):
        self._write()
        self.pdf.write_bytes(b"%PDF-1.4 tampered")
        result = verify_manifest(self.manifest_path, pdf=self.pdf)
        self.assertFalse(result["valid"])
        self.assertIn("supplied pdf does not match the tailored PDF", result["reasons"])

    def test_missing_file_invalidates(self):
        self._write()
        self.pdf.unlink()
        result = verify_manifest(self.manifest_path)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
