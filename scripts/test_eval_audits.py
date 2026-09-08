#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from eval_audits import audit_stage


class AuditStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        (self.knowledge / "project.md").write_text("Built a queue that reduced latency by 40%.")
        self.jd = self.root / "jd.txt"
        self.jd.write_text("Requires Python and distributed systems experience.")

    def tearDown(self):
        self.temp.cleanup()

    def audit(self, payload):
        artifact = self.root / "artifact.json"
        artifact.write_text(json.dumps(payload))
        return audit_stage(artifact, self.jd, self.knowledge)

    def test_grounded_artifact_passes(self):
        report = self.audit({"stage": "review", "requirements": [
            {"jd_quote": "Python and distributed systems"}], "claims": [{
            "text": "Built a queue that reduced latency by 40%.",
            "source_path": "project.md", "source_quote": "reduced latency by 40%",
        }] * 3, "unsupported_requirements": []})
        self.assertTrue(report["ok"])

    def test_invented_or_outside_evidence_fails(self):
        report = self.audit({"stage": "bullet-writing", "requirements": [
            {"jd_quote": "Kubernetes"}], "claims": [{
            "text": "Invented result", "source_path": "../secret.txt", "source_quote": "secret",
        }]})
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(sum(not check["ok"] for check in report["checks"]), 2)

    def test_bullets_are_audited_when_claims_is_an_empty_list(self):
        report = self.audit({"stage": "bullet-writing", "requirements": [], "claims": [],
                             "bullets": [{"text": "Invented result",
                                          "source_path": "missing.md",
                                          "source_quote": "not real"}],
                             "unsupported_requirements": []})
        self.assertFalse(report["ok"])
        self.assertTrue(any(check["check"] == "claim_grounding" and not check["ok"]
                            for check in report["checks"]))

    def test_numeric_claim_must_appear_in_exact_source_quote(self):
        (self.knowledge / "project.md").write_text(
            "Built a neural network. Achieved 95% accuracy."
        )
        report = self.audit({"stage": "bullet-writing", "requirements": [],
                             "claims": [], "bullets": [{
                                 "text": "Built a neural network with 95% accuracy.",
                                 "source_path": "project.md",
                                 "source_quote": "Built a neural network.",
                             }], "unsupported_requirements": []})
        self.assertFalse(report["ok"])
        checks = [check for check in report["checks"]
                  if check["check"] == "numeric_grounding"]
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0]["ok"])
        self.assertIn("95", checks[0]["detail"])

    def test_selection_requires_six_items_mapped_to_exact_jd_spans(self):
        report = self.audit({"stage": "selection", "requirements": [],
                             "claims": [{
                                 "text": "Built a queue that reduced latency by 40%.",
                                 "source_path": "project.md",
                                 "source_quote": "reduced latency by 40%",
                                 "requirement_quote": "not in the JD",
                             }], "bullets": [], "unsupported_requirements": []})
        self.assertFalse(report["ok"])
        self.assertTrue(any(check["check"] == "selection_count" and not check["ok"]
                            for check in report["checks"]))
        self.assertTrue(any(check["check"] == "selection_requirement_grounding"
                            and not check["ok"] for check in report["checks"]))

    def test_review_requires_final_bullets_and_nonempty_claim_text(self):
        empty = self.audit({"stage": "review", "requirements": [], "claims": [],
                            "bullets": [], "unsupported_requirements": []})
        self.assertFalse(empty["ok"])
        missing_text = self.audit({"stage": "review", "requirements": [], "claims": [{
            "source_path": "project.md", "source_quote": "reduced latency by 40%",
        }] * 3, "bullets": [], "unsupported_requirements": []})
        self.assertFalse(missing_text["ok"])
        self.assertTrue(any(check["check"] == "claim_text" and not check["ok"]
                            for check in missing_text["checks"]))

    def test_malformed_gap_list_and_boolean_expected_count_fail(self):
        malformed_gaps = self.audit({"stage": "jd-extraction", "requirements": [{
            "jd_quote": "Requires Python"
        }], "unsupported_requirements": "none"})
        self.assertFalse(malformed_gaps["ok"])
        boolean_count = self.audit({"stage": "bullet-writing", "requirements": [],
                                    "claims": [], "bullets": [],
                                    "expected_bullet_count": False,
                                    "unsupported_requirements": []})
        self.assertFalse(boolean_count["ok"])


if __name__ == "__main__":
    unittest.main()
