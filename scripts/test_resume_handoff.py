#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from resume_handoff import (build_manifest, normalization_degenerate,
                            normalize_jd_text, normalized_sha256_text,
                            verify_manifest)


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

    # -- jd-hash-stale-check ------------------------------------------------

    def test_jd_hash_unchanged_verifies(self):
        # (1) Unchanged JD, same path -> verify passes.
        self._write()
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=self.jd)
        self.assertTrue(result["valid"], result["reasons"])

    def test_jd_content_changed_same_path_invalidates(self):
        # (2) JD file content changed since write time (same posting URL,
        # same path) -> verify must now fail / fall through to re-tailor.
        self._write()
        self.jd.write_text("Requires distributed systems and Kubernetes experience.")
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=self.jd)
        self.assertFalse(result["valid"])
        self.assertTrue(any("JD content changed" in r for r in result["reasons"]),
                        result["reasons"])

    def test_jd_fresh_fetch_whitespace_and_volatile_diff_still_verifies(self):
        # (3) Fresh-fetch JD text with only whitespace/volatile-line
        # differences -> verify still passes (normalized hash).
        self.jd.write_text(
            "Backend Engineer\n"
            "Requires performance engineering.\n"
            "Apply at https://boards.example.com/job?gh_jid=1&utm_source=li\n"
        )
        self._write()
        fresh = self.root / "jd_fresh.txt"
        fresh.write_text(
            "  BACKEND   Engineer  \n"
            "\n"
            "Posted 3 days ago\n"
            "  Requires   performance engineering.  \n"
            "Apply at https://boards.example.com/job?utm_source=newsletter&utm_medium=email\n"
            "Applicants: 214\n"
        )
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=fresh)
        self.assertTrue(result["valid"], result["reasons"])

    def test_jd_fresh_fetch_genuine_change_invalidates(self):
        # (4) Fresh-fetch JD text with a genuine content change -> verify
        # fails.
        self._write()
        fresh = self.root / "jd_fresh_changed.txt"
        fresh.write_text(
            "Requires performance engineering and 5+ years of Rust.\n"
            "Posted 3 days ago\n"
        )
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=fresh)
        self.assertFalse(result["valid"])
        self.assertTrue(any("JD content changed" in r for r in result["reasons"]),
                        result["reasons"])

    def test_missing_jd_file_arg_falls_back_and_logs(self):
        # (5) Missing --jd-file (no path recorded, or caller not yet
        # updated) -> verify falls back to today's posting-URL-only check
        # rather than crashing, with the fallback logged.
        self._write()
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job")
        self.assertTrue(result["valid"], result["reasons"])
        self.assertIn("warnings", result)
        self.assertTrue(any("falling back to posting-URL-only" in w
                            for w in result["warnings"]), result["warnings"])

    def test_manifest_missing_normalized_hash_falls_back_and_logs(self):
        # Older manifest (no jd_sha256_normalized) + --jd-file passed:
        # should not crash, should fall back with a logged warning rather
        # than silently treating it as a match.
        self._write()
        manifest = json.loads(self.manifest_path.read_text())
        del manifest["jd_sha256_normalized"]
        self.manifest_path.write_text(json.dumps(manifest))
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=self.jd)
        self.assertTrue(result["valid"], result["reasons"])
        self.assertIn("warnings", result)
        self.assertTrue(any("falling back to posting-URL-only" in w
                            for w in result["warnings"]), result["warnings"])

    def test_manifest_records_jd_path(self):
        manifest = self._write()
        self.assertEqual(manifest["jd_path"], str(self.jd.resolve()))
        self.assertIn("jd_sha256_normalized", manifest)
        self.assertFalse(manifest["jd_normalization_degenerate"])

    def test_jd_file_not_found_invalidates(self):
        self._write()
        result = verify_manifest(self.manifest_path,
                                 posting_url="https://example.com/job",
                                 jd_file=self.root / "missing.txt")
        self.assertFalse(result["valid"])
        self.assertTrue(any("jd file not found" in r for r in result["reasons"]),
                        result["reasons"])

    # -- normalization must not eat substance (review probes) ---------------

    def test_tracking_url_strips_token_not_line(self):
        text = ("Requirements: 3+ yrs Python. Apply at "
                "https://x.com/j?source=li. Must know Go.")
        norm = normalize_jd_text(text)
        self.assertIn("3+ yrs python", norm)
        self.assertIn("must know go", norm)
        self.assertNotIn("source=li", norm)
        self.assertFalse(normalization_degenerate(text))

    def test_applicants_prefix_substantive_line_kept(self):
        text = "Applicants: 5+ years of experience required"
        self.assertEqual(normalize_jd_text(text),
                         "applicants: 5+ years of experience required")
        # ...while a bare counter line is still dropped.
        self.assertEqual(normalize_jd_text("Applicants: 214"), "")

    def test_apply_before_deadline_change_detected(self):
        a = "Build APIs in Go.\nApply before: 2026-10-01\n"
        b = "Build APIs in Go.\nApply before: 2026-11-15\n"
        self.assertIn("apply before: 2026-10-01", normalize_jd_text(a))
        self.assertNotEqual(normalized_sha256_text(a), normalized_sha256_text(b))

    def test_single_line_jd_with_tracking_url_detects_change(self):
        # fetch_urls.py --strip-tags output is often one long line.
        v1 = ("Software Engineer. Requirements: 3+ yrs Python, Postgres. "
              "Apply at https://x.com/j?source=li&utm_campaign=a. Posted 2 days ago")
        v1_refetch = ("Software Engineer. Requirements: 3+ yrs Python, Postgres. "
                      "Apply at https://x.com/j?utm_campaign=zz&source=tw. Posted 5 days ago")
        v2 = ("Software Engineer. Requirements: 5+ yrs Rust, Kafka. "
              "Apply at https://x.com/j?source=li&utm_campaign=a. Posted 2 days ago")
        self.assertTrue(normalize_jd_text(v1))
        self.assertEqual(normalized_sha256_text(v1), normalized_sha256_text(v1_refetch))
        self.assertNotEqual(normalized_sha256_text(v1), normalized_sha256_text(v2))

        self.jd.write_text(v1)
        self._write()
        fresh = self.root / "fresh.txt"
        fresh.write_text(v2)
        result = verify_manifest(self.manifest_path, jd_file=fresh)
        self.assertFalse(result["valid"])
        fresh.write_text(v1_refetch)
        result = verify_manifest(self.manifest_path, jd_file=fresh)
        self.assertTrue(result["valid"], result["reasons"])

    def test_degenerate_normalization_fails_closed(self):
        self._write()
        fresh = self.root / "fresh.txt"
        # Almost all of this is noise the normalizer removes -> < 50% kept.
        fresh.write_text("Posted 3 days ago\nApplicants: 214\n1,204 views\nGo.\n")
        self.assertTrue(normalization_degenerate(fresh.read_text()))
        result = verify_manifest(self.manifest_path, jd_file=fresh)
        self.assertFalse(result["valid"])
        self.assertTrue(any("degenerate" in r for r in result["reasons"]),
                        result["reasons"])
        # Empty normalized text is always degenerate.
        self.assertTrue(normalization_degenerate("Posted 1 day ago\n\n"))

    def test_degenerate_at_write_time_fails_closed(self):
        self.jd.write_text("Posted 3 days ago\nApplicants: 12\n")
        self._write()
        result = verify_manifest(self.manifest_path, jd_file=self.jd)
        self.assertFalse(result["valid"])

    # -- rotating widgets: counters and trailing related-jobs blocks -----------

    BODY = ("Software Engineer. Build distributed systems in Go and Python. "
            "Requirements: Bachelor's degree in Computer Science; 2+ years of "
            "experience with Kubernetes. Salary $150,000. Location: Austin. ") * 6

    def test_result_counter_phrases_stripped_in_place(self):
        a = ("Jobs search results 3,356 jobs matched " + self.BODY +
             "Showing 1 to 20 of 3356 rows 1&#8209;20 of 3356 navigate_next Go")
        b = a.replace("3,356", "3,327").replace("3356", "3327")
        self.assertNotEqual(a, b)
        self.assertEqual(normalized_sha256_text(a), normalized_sha256_text(b))
        self.assertIn("kubernetes", normalize_jd_text(a))

    def test_counter_whole_lines_stripped(self):
        self.assertEqual(normalize_jd_text("1,204 results\n42 jobs found\nBuild Go."),
                         "build go.")

    def test_counter_words_inside_requirement_text_kept(self):
        text = ("Requirements: publish 3 results in peer-reviewed venues. "
                "Showing 2 of your projects in a portfolio is a plus. "
                "You will triage 40 jobs found in the queue. Own 5 positions.\n"
                "3 positions\n")
        norm = normalize_jd_text(text)
        for frag in ("3 results in peer-reviewed", "showing 2 of your projects",
                     "40 jobs found in the queue", "own 5 positions", "3 positions"):
            self.assertIn(frag, norm)

    def test_counter_change_still_detected_when_not_a_counter_phrase(self):
        a = "Showing 1 to 20 of 3356 rows\n" + self.BODY
        b = a.replace("Kubernetes", "Terraform", 1)
        self.assertNotEqual(normalized_sha256_text(a), normalized_sha256_text(b))

    def test_trailing_related_jobs_block_stripped(self):
        tail1 = ("Related Jobs Software Engineer, Creator San Mateo, CA View Job "
                 "Software Engineer, Engine San Mateo, CA View Job")
        tail2 = ("Related Jobs Software Engineer, Data San Mateo, CA View Job "
                 "Software Engineer, UX San Mateo, CA View Job Research Engineer, ML View Job")
        a = self.BODY + "Equal opportunity employer. " + tail1
        b = self.BODY + "Equal opportunity employer. " + tail2
        self.assertEqual(normalized_sha256_text(a), normalized_sha256_text(b))
        self.assertIn("equal opportunity employer", normalize_jd_text(a))
        self.assertNotIn("related jobs", normalize_jd_text(a))
        # multi-line form: heading on its own line, list items one per line
        c = self.BODY + "\nOpen positions\nFPGA Intern Chicago\nTrader Chicago\n"
        d = self.BODY + "\nOpen positions\nAI Engineer New York\n"
        self.assertEqual(normalized_sha256_text(c), normalized_sha256_text(d))

    def test_related_jobs_words_inside_requirement_text_kept(self):
        # Heading-like words, but in the back half of genuine prose.
        text = (self.BODY + "Experience in related jobs or open positions in "
                "fintech is a plus. You will mentor interns and own the roadmap. "
                "Must be authorized to work in the US.")
        norm = normalize_jd_text(text)
        self.assertIn("related jobs or open positions", norm)
        self.assertIn("authorized to work in the us", norm)
        # Heading at a block start but followed by prose -> kept.
        text2 = (self.BODY + "Other open roles. We hire in teams that ship weekly. "
                 "Candidates must hold a PhD. Apply by Friday.")
        self.assertIn("candidates must hold a phd", normalize_jd_text(text2))

    def test_related_jobs_heading_in_front_half_kept(self):
        text = "Related Jobs Go Rust View Job Apply now. " + self.BODY
        self.assertIn("related jobs", normalize_jd_text(text))

    def test_related_jobs_huge_tail_kept(self):
        # Tail longer than 40% of the text is not treated as a widget.
        text = "Intro line. Related Jobs " + "Role A View Job " * 50
        self.assertIn("related jobs", normalize_jd_text(text))

    def test_edit_before_widget_still_detected(self):
        tail = "Related Jobs Software Engineer, Creator San Mateo, CA View Job "
        a = self.BODY + tail
        b = self.BODY.replace("Bachelor's", "Master's", 1) + tail
        self.assertNotEqual(normalized_sha256_text(a), normalized_sha256_text(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
