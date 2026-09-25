#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from model_eval import EvalStore, default_repo_root


class ModelEvalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        (self.repo_root / "knowledge").mkdir()
        self.store = EvalStore(self.repo_root)
        self.store.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def start_run(self, model="example-model"):
        case_path = self.store.eval_root / "cases" / "backend.txt"
        case_path.write_text("Example job description")
        return self.store.start_run(
            "backend", "tailor-end-to-end", "example", model, "medium", "pilot-v1", case_path
        )

    def finish_run(self, run_id, artifact_name="resume.pdf"):
        artifact = self.repo_root / artifact_name
        artifact.write_bytes(b"pdf")
        return self.store.finish_run(
            run_id,
            "success",
            [artifact],
            100,
            25,
            5,
            40,
            10,
            None,
            "exact",
            2,
            0,
            1,
            None,
        )

    def test_init_does_not_overwrite_cases(self):
        cases_path = self.store.eval_root / "cases.json"
        cases_path.write_text("custom")
        self.store.initialize()
        self.assertEqual(cases_path.read_text(), "custom")

    def test_unrelated_conductor_root_does_not_redirect_ledger(self):
        with mock.patch.dict(os.environ, {
            "CONDUCTOR_ROOT_PATH": str(self.repo_root / "unrelated"),
        }, clear=True):
            self.assertEqual(default_repo_root(), Path(__file__).resolve().parent.parent)

    def test_start_and_finish_persist_reproducibility_metadata(self):
        run_id = self.start_run()
        event = self.finish_run(run_id)
        self.assertEqual(event["case_id"], "backend")
        self.assertEqual(event["token_source"], "exact")
        self.assertEqual(event["total_input_tokens"], 150)
        self.assertEqual(event["total_tokens"], 175)
        self.assertEqual(event["cache_hit_rate"], 0.2667)
        self.assertTrue(event["case_sha256"])
        self.assertTrue(event["artifacts"][0]["sha256"])
        self.assertFalse((self.store.active_dir / (run_id + ".json")).exists())

    def test_ats_requires_three_scores_and_records_spread(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        with self.assertRaisesRegex(ValueError, "at least three"):
            self.store.add_ats_scores(run_id, [60, 62], "ats")
        event = self.store.add_ats_scores(run_id, [60, 64, 62], "ats")
        self.assertEqual(event["median"], 62)
        self.assertEqual(event["spread"], 4)

    def test_human_scores_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "1 to 5"):
            self.store.add_human_scores("run", 6, 5, 5, 5, 5, "owner", None)
        with self.assertRaisesRegex(ValueError, "1 to 5"):
            self.store.add_human_scores("run", True, 5, 5, 5, 5, "owner", None)

    def test_blind_pack_and_import_hide_model_identity(self):
        first_run = self.start_run("first")
        self.finish_run(first_run, "first.pdf")
        second_run = self.start_run("second")
        self.finish_run(second_run, "second.pdf")
        pack_dir = self.store.create_blind_pack("backend", "tailor-end-to-end", 7)
        review_path = pack_dir / "review.csv"
        contents = review_path.read_text()
        self.assertNotIn("first", contents)
        self.assertNotIn("second", contents)
        lines = contents.splitlines()
        lines[1] = "A,5,4,5,4,5,strong"
        lines[2] = "B,4,4,4,4,4,solid"
        review_path.write_text("\n".join(lines) + "\n")
        self.assertEqual(self.store.import_human_review(review_path, "owner"), 2)

    def test_aggregate_keeps_exact_and_estimated_tokens_separate(self):
        exact_run = self.start_run()
        self.finish_run(exact_run)
        estimated_run = self.start_run()
        artifact = self.repo_root / "estimated.pdf"
        artifact.write_bytes(b"pdf")
        self.store.finish_run(
            estimated_run,
            "success",
            [artifact],
            200,
            50,
            None,
            None,
            None,
            250,
            "estimated",
            1,
            0,
            0,
            None,
        )
        self.store.add_ats_scores(exact_run, [60, 62, 64], "ats")
        self.store.add_human_scores(exact_run, 5, 4, 4, 5, 5, "owner", None)
        rows = self.store.aggregate()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["runs"], 2)
        self.assertEqual(rows[0]["mean_exact_tokens"], 175)
        self.assertEqual(rows[0]["mean_exact_input"], 150)
        self.assertEqual(rows[0]["mean_exact_output"], 25)
        self.assertEqual(rows[0]["mean_cache_hit_rate"], 0.2667)
        self.assertEqual(rows[0]["token_coverage"], 1)
        self.assertEqual(rows[0]["median_ats"], 62)
        self.assertEqual(rows[0]["mean_human"], 4.6)

    def test_events_are_valid_jsonl(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        lines = self.store.events_path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["run_id"], run_id)

    def test_exclusion_preserves_event_but_removes_run_from_aggregate(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        self.store.exclude_run(run_id, "invalid fixture")
        self.assertEqual(self.store.aggregate(), [])
        events = self.store.load_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1]["event"], "model_eval_exclusion")

    def test_audit_and_cross_model_judge_are_aggregated_separately(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        self.store.add_audit(run_id, {"stage": "bullet-writing", "ok": False,
                                      "checks": [{"check": "claim_grounding", "ok": False}]})
        self.store.add_judge(run_id, "other", "judge-model", {
            "ratings": {"scanability": 5, "clarity": 4, "specificity": 4,
                        "naturalness": 5, "confidence": 5},
            "factuality_pass": False, "findings": [],
            "telemetry": {"latency_s": 12.5, "total_tokens": 900, "cost_usd": 0.02},
        })
        row = self.store.aggregate()[0]
        self.assertEqual(row["audit_failures"], 1)
        self.assertEqual(row["mean_judge"], 4.6)
        self.assertEqual(row["mean_judge_latency_s"], 12.5)
        self.assertEqual(row["mean_judge_tokens"], 900)
        self.assertEqual(row["mean_judge_cost_usd"], 0.02)
        self.assertEqual(row["quality_reviewed"], 1)
        self.assertEqual(row["quality_passes"], 0)
        self.assertEqual(row["quality_pass_rate"], 0)

    def test_quality_pass_requires_audit_and_all_judge_thresholds(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        self.store.add_audit(run_id, {
            "stage": "bullet-writing", "checks": [{"check": "grounding", "ok": True}],
        })
        self.store.add_judge(run_id, "other", "judge-model", {
            "ratings": {"scanability": 4, "clarity": 4, "specificity": 4,
                        "naturalness": 4, "confidence": 4},
            "factuality_pass": True, "findings": [],
        })
        row = self.store.aggregate()[0]
        self.assertEqual(row["quality_reviewed"], 1)
        self.assertEqual(row["quality_passes"], 1)
        self.assertEqual(row["quality_pass_rate"], 1)

    def test_failed_audit_decides_gate_without_calling_judge(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        self.store.add_audit(run_id, {
            "stage": "selection", "checks": [{"check": "grounding", "ok": False}],
        })
        row = self.store.aggregate()[0]
        self.assertEqual(row["quality_reviewed"], 1)
        self.assertEqual(row["quality_passes"], 0)
        self.assertEqual(row["quality_pass_rate"], 0)

    def test_judge_requires_complete_bounded_ratings(self):
        with self.assertRaisesRegex(ValueError, "every 1-5"):
            self.store.add_judge("run", "other", "judge", {
                "ratings": {"scanability": 5}, "factuality_pass": True,
            })

    def test_judge_rejects_extra_rating_dimensions(self):
        with self.assertRaisesRegex(ValueError, "exactly every"):
            self.store.add_judge("run", "other", "judge", {
                "ratings": {"scanability": 4, "clarity": 4, "specificity": 4,
                            "naturalness": 4, "confidence": 4, "bonus": 5},
                "factuality_pass": True,
            })

    def test_judge_rejects_boolean_ratings_and_malformed_telemetry(self):
        verdict = {
            "ratings": {"scanability": True, "clarity": 4, "specificity": 4,
                        "naturalness": 4, "confidence": 4},
            "factuality_pass": True,
        }
        with self.assertRaisesRegex(ValueError, "1-5"):
            self.store.add_judge("run", "other", "judge", verdict)
        verdict["ratings"]["scanability"] = 4
        verdict["telemetry"] = {"latency_s": "slow"}
        with self.assertRaisesRegex(ValueError, "non-negative number"):
            self.store.add_judge("run", "other", "judge", verdict)

    def test_path_components_cannot_escape_eval_directories(self):
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.store.finish_run("../../outside", "failed", [], None, None, None,
                                  None, None, None, "unavailable", None, 0, 0, None)
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            self.store.create_blind_pack("../../outside", "tailor-end-to-end", 1)

    def test_finish_rejects_duplicate_artifact_basenames(self):
        run_id = self.start_run()
        first = self.repo_root / "one" / "resume.pdf"
        second = self.repo_root / "two" / "resume.pdf"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        with self.assertRaisesRegex(ValueError, "basenames"):
            self.store.finish_run(run_id, "success", [first, second], None, None, None,
                                  None, None, None, "unavailable", None, 0, 0, None)

    def test_audit_cannot_self_report_past_when_a_hard_check_failed(self):
        event = self.store.add_audit("run", {
            "ok": True, "checks": [{"check": "grounding", "ok": False}],
        })
        self.assertFalse(event["ok"])

    def test_audit_rejects_malformed_boolean_or_severity(self):
        with self.assertRaisesRegex(ValueError, "boolean ok"):
            self.store.add_audit("run", {
                "checks": [{"check": "grounding", "ok": "false"}],
            })
        with self.assertRaisesRegex(ValueError, "hard/warning"):
            self.store.add_audit("run", {
                "checks": [{"check": "grounding", "ok": False, "severity": "info"}],
            })

    def test_failed_run_excluded_from_latency_and_cost_by_default(self):
        success_run = self.start_run()
        self.store.finish_run(
            success_run, "success", [self._pdf("success.pdf")], 100, 25, 5,
            40, 10, None, "exact", 2, 0, 1, None,
            latency_s=50.0, cost_usd=1.0,
        )
        failed_run = self.store.start_run(
            "backend", "tailor-end-to-end", "example", "example-model",
            "medium", "pilot-v1", self.store.eval_root / "cases" / "backend.txt",
        )
        self.store.finish_run(
            failed_run, "failed", [self._pdf("failed.pdf")], None, None, None,
            None, None, None, "unavailable", None, 0, 0, "aborted early",
            latency_s=0.01, cost_usd=0.0001,
        )

        rows_default = self.store.aggregate()
        self.assertEqual(len(rows_default), 1)
        row = rows_default[0]
        # both runs are still counted for success_rate...
        self.assertEqual(row["runs"], 2)
        self.assertEqual(row["success_rate"], 0.5)
        # ...but the failed run's near-zero latency/cost must not drag the
        # median/mean down by default.
        self.assertEqual(row["p50_latency_s"], 50.0)
        self.assertEqual(row["mean_cost_usd"], 1.0)

        rows_included = self.store.aggregate(include_failed=True)
        row_included = rows_included[0]
        self.assertEqual(row_included["p50_latency_s"], 50.0)  # median of [0.01, 50.0]
        self.assertAlmostEqual(row_included["mean_cost_usd"], (1.0 + 0.0001) / 2, places=3)

    def _pdf(self, name):
        path = self.repo_root / name
        path.write_bytes(b"pdf")
        return path

    def test_append_only_latency_correction_changes_aggregate(self):
        run_id = self.start_run()
        self.finish_run(run_id)
        self.store.correct_latency(run_id, 57.0, "background-state", "late timer")
        self.assertEqual(self.store.aggregate()[0]["p50_latency_s"], 57.0)


if __name__ == "__main__":
    unittest.main()
