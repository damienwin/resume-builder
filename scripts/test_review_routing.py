#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from review_routing import append_metric, choose_route, detect_capabilities, review_plan, risk_signals


def capabilities(claude=False, chatgpt=False):
    return {"claude": {"available": claude}, "chatgpt": {"available": chatgpt}}


def passing_audit():
    return {"ok": True, "checks": [{"check": "grounding", "ok": True,
                                      "severity": "hard"}]}


class ReviewRoutingTests(unittest.TestCase):
    def test_detection_uses_config_or_environment_without_running_a_model(self):
        detected = detect_capabilities({"claude": True, "chatgpt": False}, {}, lambda _: None)
        self.assertTrue(detected["claude"]["available"])
        self.assertFalse(detected["chatgpt"]["available"])
        overridden = detect_capabilities({}, {"RESUME_BUILDER_CHATGPT_AVAILABLE": "true"}, lambda _: None)
        self.assertTrue(overridden["chatgpt"]["available"])

    def test_detects_codex_at_known_install_path(self):
        candidate = os.path.expanduser(
            "~/Library/Application Support/com.conductor.app/bin/codex")
        detected = detect_capabilities({}, {}, lambda cmd: cmd if cmd == candidate else None)
        self.assertTrue(detected["chatgpt"]["available"])
        self.assertEqual(detected["chatgpt"]["command"], candidate)

    def test_command_env_override_wins_over_candidates(self):
        detected = detect_capabilities(
            {}, {"RESUME_BUILDER_CHATGPT_COMMAND": "/custom/codex"},
            lambda cmd: cmd if cmd == "/custom/codex" else None)
        self.assertTrue(detected["chatgpt"]["available"])
        self.assertEqual(detected["chatgpt"]["command"], "/custom/codex")

    def test_route_carries_resolved_reviewer_command(self):
        route = choose_route({
            "claude": {"available": True, "command": "claude"},
            "chatgpt": {"available": True, "command": "/opt/codex"},
        })
        self.assertEqual(route["reviewer"]["provider"], "chatgpt")
        self.assertEqual(route["reviewer"]["command"], "/opt/codex")

    def test_all_subscription_matrices_have_a_safe_route(self):
        dual = review_plan("auto", "review", capabilities(True, True), audit=passing_audit())
        self.assertEqual(dual["author"]["provider"], "claude")
        self.assertEqual(dual["reviewer"]["provider"], "chatgpt")
        claude_only = review_plan("auto", "review", capabilities(True, False),
                                  audit=passing_audit())
        self.assertEqual(claude_only["reviewer"]["provider"], "claude")
        self.assertTrue(claude_only["fresh_isolated_context"])
        chatgpt_only = review_plan("auto", "review", capabilities(False, True),
                                   audit=passing_audit())
        self.assertEqual(chatgpt_only["author"]["provider"], "chatgpt")
        neither = review_plan("auto", "review", capabilities(), audit=passing_audit())
        self.assertTrue(neither["human_review_required"])
        self.assertFalse(neither["review_required"])

    def test_auto_reviews_once_at_final_unless_risk_requires_early_review(self):
        normal = review_plan("auto", "selection", capabilities(True, True), audit=passing_audit())
        self.assertFalse(normal["review_required"])
        self.assertEqual(normal["skip_reason"], "no_risk_signal_before_final")
        final = review_plan("auto", "review", capabilities(True, True), audit=passing_audit())
        self.assertTrue(final["review_required"])
        risky = review_plan("auto", "selection", capabilities(True, True),
                            audit=passing_audit(), artifact={
            "claims": [{"source_path": "skills.md", "requirement_quote": "A"},
                       {"source_path": "projects/x.md", "requirement_quote": "A"}], "bullets": []
        })
        self.assertTrue(risky["review_required"])
        self.assertIn("skills_list_evidence", risky["risk_signals"])
        self.assertIn("duplicate_requirement_mapping", risky["risk_signals"])

    def test_modes_and_hard_fail_are_fail_closed(self):
        audit = {"ok": False, "checks": [{"check": "grounding", "ok": False}]}
        hard_fail = review_plan("full-stage-review", "selection", capabilities(True, True), audit=audit)
        self.assertFalse(hard_fail["review_required"])
        self.assertEqual(hard_fail["skip_reason"], "deterministic_hard_failure")
        for mode in ("deterministic-only", "human-review"):
            plan = review_plan(mode, "review", capabilities(True, True), audit=passing_audit())
            self.assertTrue(plan["human_review_required"])
            self.assertFalse(plan["review_required"])
        final_only = review_plan("final-review", "selection", capabilities(True, True),
                                 audit=passing_audit())
        self.assertFalse(final_only["review_required"])
        full = review_plan("full-stage-review", "selection", capabilities(True, True),
                           audit=passing_audit())
        self.assertTrue(full["review_required"])

    def test_missing_or_malformed_audit_never_authorizes_review(self):
        malformed_reports = [
            None,
            {},
            {"ok": "true", "checks": [{"check": "grounding", "ok": True}]},
            {"ok": True, "checks": [{"check": "grounding", "ok": False}]},
            {"ok": True, "checks": [{"check": "grounding", "ok": True,
                                       "severity": "unknown"}]},
        ]
        for audit in malformed_reports:
            with self.subTest(audit=audit):
                plan = review_plan("auto", "review", capabilities(True, True), audit=audit)
                self.assertFalse(plan["review_required"])
                self.assertFalse(plan["deterministic_ok"])

    def test_malformed_artifact_never_authorizes_review(self):
        plan = review_plan("auto", "review", capabilities(True, True),
                           audit=passing_audit(), artifact={"claims": "not-a-list"})
        self.assertFalse(plan["review_required"])
        self.assertEqual(plan["skip_reason"], "malformed_artifact")

    def test_no_subscription_defers_human_review_until_final_without_risk(self):
        early = review_plan("auto", "selection", capabilities(), audit=passing_audit())
        self.assertFalse(early["human_review_required"])
        self.assertEqual(early["skip_reason"], "no_risk_signal_before_final")
        final = review_plan("auto", "review", capabilities(), audit=passing_audit())
        self.assertTrue(final["human_review_required"])

    def test_metrics_preserve_plan_skip_telemetry_repair_and_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "knowledge" / "review_routing_runs.jsonl"
            plan = review_plan("auto", "review", capabilities(True, True),
                               audit=passing_audit())
            plan.update({"repair_count": 2, "review_telemetry": {"latency_s": 3.5, "total_tokens": 99},
                         "final_gate": {"ok": True}})
            append_metric(path, plan)
            saved = json.loads(path.read_text().strip())
            self.assertEqual(saved["repair_count"], 2)
            self.assertEqual(saved["review_telemetry"]["total_tokens"], 99)
            self.assertTrue(saved["final_gate"]["ok"])

    def test_malformed_artifact_fields_are_safe_and_do_not_trigger_routing(self):
        self.assertEqual(risk_signals({"claims": None, "bullets": "not-a-list"}, None), [])
        self.assertEqual(risk_signals({"claims": {"source_path": "skills.md"}}, None), [])

    def test_skills_risk_uses_exact_filename_not_suffix(self):
        self.assertEqual(risk_signals({"claims": [{"source_path": "softskills.md"}]}, None), [])
        self.assertEqual(risk_signals({"claims": [{"source_path": "skills.md"}]}, None),
                         ["skills_list_evidence"])

    def test_malformed_capability_entries_fail_closed_to_no_provider(self):
        self.assertEqual(choose_route(None), {"author": None, "reviewer": None})
        self.assertEqual(choose_route({"claude": True, "chatgpt": "yes"}),
                         {"author": None, "reviewer": None})
        self.assertEqual(choose_route({"claude": {"available": True}, "chatgpt": None})["author"]["provider"],
                         "claude")

    def test_cli_reports_malformed_json_without_traceback(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            handle.write("not-json")
            path = handle.name
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("review_routing.py")),
                 "plan", "--mode", "auto", "--stage", "review", "--capabilities", path],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
