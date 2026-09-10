#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import run_timer
from gate_stage import gate_stage

SCRIPT = Path(__file__).with_name("gate_stage.py")


class GateStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        (self.knowledge / "source.md").write_text("Reduced latency by 40%.")
        self.case = self.root / "jd.txt"
        self.case.write_text(
            "Requires performance engineering, distributed systems, Go, "
            "Kubernetes, observability, and testing."
        )
        self.artifact = self.root / "selection.json"
        requirements = ["performance engineering", "distributed systems", "Go",
                        "Kubernetes", "observability", "testing"]
        self.artifact.write_text(json.dumps({
            "stage": "selection", "requirements": [], "bullets": [],
            "claims": [{"text": "Reduced latency by 40%.",
                        "source_path": "source.md",
                        "source_quote": "Reduced latency by 40%.",
                        "requirement_quote": quote} for quote in requirements],
            "unsupported_requirements": [],
        }))
        self.caps = self.root / "caps.json"
        self.caps.write_text(json.dumps({"claude": True, "chatgpt": False}))

    def tearDown(self):
        self.temp.cleanup()

    def test_passing_gate_writes_audit_and_route(self):
        audit = self.root / "audit.json"
        route = self.root / "route.json"
        result = gate_stage(self.artifact, self.case, self.knowledge, "selection",
                            audit, route, capabilities_path=self.caps)
        self.assertTrue(result["ok"])
        self.assertTrue(audit.exists())
        self.assertTrue(route.exists())
        plan = json.loads(route.read_text())
        self.assertEqual(plan["stage"], "selection")
        self.assertFalse(plan["review_required"])  # auto mode, no risk signal

    def test_hard_failure_never_writes_a_route(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"stage": "selection", "requirements": [],
                                   "bullets": [], "claims": [],
                                   "unsupported_requirements": []}))
        route = self.root / "route.json"
        result = gate_stage(bad, self.case, self.knowledge, "selection",
                            self.root / "audit.json", route, capabilities_path=self.caps)
        self.assertFalse(result["ok"])
        self.assertFalse(route.exists())
        self.assertEqual(result["skip_reason"], "deterministic_hard_failure")

    def test_timer_mark_is_written_for_the_stage(self):
        scope = "test-gate-stage"
        run_timer.start("tailor-resume", scope)
        gate_stage(self.artifact, self.case, self.knowledge, "selection",
                   capabilities_path=self.caps,
                   timer_skill="tailor-resume", timer_scope=scope,
                   timer_label="selection_gate")
        finished = run_timer.finish("tailor-resume", scope)
        self.assertIn("selection_gate", finished.get("steps", {}))

    def test_cli_exit_codes(self):
        good = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.artifact),
             "--case-path", str(self.case), "--knowledge-root", str(self.knowledge),
             "--stage", "selection", "--capabilities", str(self.caps)],
            capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
