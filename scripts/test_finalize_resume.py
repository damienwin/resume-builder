#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from finalize_resume import finalize
from resume_handoff import verify_manifest


class FinalizeResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        (self.knowledge / "source.md").write_text("Reduced latency by 40%.")
        self.case = self.root / "jd.txt"
        self.case.write_text("Requires performance engineering.")
        self.bullets = self.root / "bullets.json"
        self.bullets.write_text(json.dumps({
            "stage": "bullet-writing", "expected_bullet_count": 1,
            "requirements": [], "claims": [],
            "bullets": [{"text": "Reduced latency by 40%.",
                         "source_path": "source.md",
                         "source_quote": "Reduced latency by 40%."}],
            "unsupported_requirements": [],
        }))
        self.judge = self.root / "judge.json"
        self.judge.write_text(json.dumps({
            "ratings": {"scanability": 4, "clarity": 4, "specificity": 4,
                        "naturalness": 4, "confidence": 4},
            "factuality_pass": True, "findings": [],
        }))
        self.plan = self.root / "route.json"
        self.plan.write_text(json.dumps({"mode": "auto", "stage": "review",
                                         "review_required": True}))
        self.pdf = self.root / "tailored.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")
        self.tex = self.root / "tailored.tex"
        self.tex.write_text("\\resumeItem{Reduced latency by 40\\%}")
        self.handoff = self.root / "handoff.json"
        self.routing = self.root / "review_routing_runs.jsonl"
        self.metrics = self.root / "metrics.jsonl"
        self.archive = self.root / "archive"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, judge=None):
        return finalize(
            self.bullets, self.case, self.knowledge,
            quality_out=self.root / "quality.json",
            judge=judge or self.judge,
            plan=self.plan, repair_count=1, routing_metrics=self.routing,
            archive_pdf=self.pdf, archive_dir=self.archive,
            archive_name="Acme Resume.pdf",
            metric_event="resume_tailor",
            metric_json=json.dumps({"company": "Acme", "role": "SWE"}),
            metrics_path=self.metrics,
            handoff_out=self.handoff,
            handoff_posting_url="https://example.com/job",
            handoff_jd_file=self.case,
            handoff_tex=self.tex,
            handoff_reviewer="claude/sonnet",
        )

    def test_passing_gate_applies_all_side_effects(self):
        result = self._run()
        self.assertTrue(result["ok"])
        self.assertTrue((self.archive / "Acme Resume.pdf").exists())
        self.assertTrue(self.routing.exists())
        self.assertTrue(self.metrics.exists())
        routing = json.loads(self.routing.read_text().strip())
        self.assertEqual(routing["repair_count"], 1)
        self.assertTrue(routing["final_gate"]["ok"])
        metric = json.loads(self.metrics.read_text().strip())
        self.assertEqual(metric["company"], "Acme")
        handoff = verify_manifest(self.handoff, posting_url="https://example.com/job")
        self.assertTrue(handoff["valid"], handoff["reasons"])

    def test_missing_optional_telemetry_does_not_abort_finalize(self):
        result = finalize(
            self.bullets, self.case, self.knowledge,
            quality_out=self.root / "quality.json", judge=self.judge,
            plan=self.plan, review_telemetry=self.root / "does-not-exist.json",
            routing_metrics=self.routing, archive_pdf=self.pdf,
            archive_dir=self.archive, archive_name="Acme Resume.pdf",
            metric_event="resume_tailor",
            metric_json=json.dumps({"company": "Acme", "role": "SWE"}),
            metrics_path=self.metrics,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["routing_recorded"])
        self.assertTrue(result["metric_logged"])
        self.assertTrue((self.archive / "Acme Resume.pdf").exists())

    def test_missing_plan_still_archives_and_logs(self):
        result = finalize(
            self.bullets, self.case, self.knowledge,
            quality_out=self.root / "quality.json", judge=self.judge,
            plan=self.root / "no-plan.json",
            routing_metrics=self.routing, archive_pdf=self.pdf,
            archive_dir=self.archive, archive_name="Acme Resume.pdf",
            metric_event="resume_tailor",
            metric_json=json.dumps({"company": "Acme", "role": "SWE"}),
            metrics_path=self.metrics,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["routing_recorded"])
        self.assertTrue(result["metric_logged"])

    def test_failed_gate_applies_no_side_effects(self):
        bad_judge = self.root / "bad-judge.json"
        bad_judge.write_text(json.dumps({
            "ratings": {"scanability": 4, "clarity": 4, "specificity": 4,
                        "naturalness": 4, "confidence": 4},
            "factuality_pass": False, "findings": [],
        }))
        result = self._run(judge=bad_judge)
        self.assertFalse(result["ok"])
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.routing.exists())
        self.assertFalse(self.metrics.exists())
        self.assertFalse(self.handoff.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
