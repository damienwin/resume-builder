#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from resume_quality_gate import quality_gate


class ResumeQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        (self.knowledge / "source.md").write_text("Reduced latency by 40%.")
        self.case = self.root / "jd.txt"
        self.case.write_text("Requires performance engineering.")
        self.artifact = self.root / "artifact.json"
        self.artifact.write_text(json.dumps({
            "stage": "review", "requirements": [], "bullets": [],
            "claims": [{"text": "Reduced latency by 40%.",
                        "source_path": "source.md",
                        "source_quote": "Reduced latency by 40%."}] * 3,
            "unsupported_requirements": [],
        }))

    def tearDown(self):
        self.temp.cleanup()

    def write_judge(self, factuality=True, confidence=4, mean_rating=4):
        path = self.root / "judge.json"
        path.write_text(json.dumps({
            "ratings": {"scanability": mean_rating, "clarity": mean_rating,
                        "specificity": mean_rating, "naturalness": mean_rating,
                        "confidence": confidence},
            "factuality_pass": factuality, "findings": [],
        }))
        return path

    def test_passes_deterministic_and_readability_gates(self):
        self.assertTrue(quality_gate(
            self.artifact, self.case, self.knowledge, self.write_judge()
        )["ok"])

    def test_factuality_failure_is_hard(self):
        report = quality_gate(
            self.artifact, self.case, self.knowledge,
            self.write_judge(factuality=False),
        )
        self.assertFalse(report["ok"])

    def test_low_confidence_or_readability_fails(self):
        low_confidence = quality_gate(
            self.artifact, self.case, self.knowledge,
            self.write_judge(confidence=2),
        )
        self.assertFalse(low_confidence["ok"])
        low_readability = quality_gate(
            self.artifact, self.case, self.knowledge,
            self.write_judge(mean_rating=3),
        )
        self.assertFalse(low_readability["ok"])

    def test_without_judge_runs_deterministic_gate_only(self):
        report = quality_gate(self.artifact, self.case, self.knowledge)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["checks"]), 1)

    def test_rejects_extra_dimensions_and_non_boolean_factuality(self):
        judge = self.write_judge()
        verdict = json.loads(judge.read_text())
        verdict["ratings"]["bonus"] = 5
        verdict["factuality_pass"] = "true"
        judge.write_text(json.dumps(verdict))
        report = quality_gate(self.artifact, self.case, self.knowledge, judge)
        self.assertFalse(report["ok"])
        self.assertFalse(next(
            check for check in report["checks"] if check["check"] == "judge_schema"
        )["ok"])

    def test_rejects_boolean_rating_even_though_bool_is_an_int_subclass(self):
        judge = self.write_judge()
        verdict = json.loads(judge.read_text())
        verdict["ratings"]["clarity"] = True
        judge.write_text(json.dumps(verdict))
        report = quality_gate(self.artifact, self.case, self.knowledge, judge)
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
