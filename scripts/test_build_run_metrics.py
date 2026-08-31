#!/usr/bin/env python3
"""Tests for build_run_metrics.py — joins knowledge/metrics.jsonl anchors
against cc_transcripts.py turns into knowledge/runs.jsonl.

Run: python3 scripts/test_build_run_metrics.py
"""
import unittest

from build_run_metrics import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_1H_MULTIPLIER,
    CACHE_WRITE_5M_MULTIPLIER,
    PRICING,
    build_runs,
    cost_for_turn,
    scan_type_label,
)
from cc_transcripts import Turn


def turn(session_id="s1", timestamp="2026-08-12T00:00:00.000Z", skill="tailor-resume",
         model="claude-opus-5", input_tokens=0, output_tokens=100,
         cache_write_5m=0, cache_write_1h=0, cache_read=0, request_id=None):
    return Turn(
        session_id=session_id, timestamp=timestamp, skill=skill, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_write_5m=cache_write_5m, cache_write_1h=cache_write_1h,
        cache_read=cache_read, request_id=request_id, is_sidechain=False,
    )


def anchor(event="resume_tailor", timestamp="2026-08-12T00:05:00.000Z", **extra):
    a = {"event": event, "timestamp": timestamp, "_skill": {
        "resume_tailor": "tailor-resume", "job_scan": "job-scan",
        "job_apply_e2e": "apply", "ats_score": "ats-score",
    }[event]}
    a.update(extra)
    return a


class CostForTurnTests(unittest.TestCase):
    def test_known_model_computes_expected_cost(self):
        t = turn(model="claude-opus-5", input_tokens=1_000_000, output_tokens=0)
        self.assertAlmostEqual(cost_for_turn(t), PRICING["claude-opus-5"]["input"])

    def test_output_tokens_priced_at_output_rate(self):
        t = turn(model="claude-sonnet-5", input_tokens=0, output_tokens=1_000_000)
        self.assertAlmostEqual(cost_for_turn(t), PRICING["claude-sonnet-5"]["output"])

    def test_cache_write_and_read_multipliers(self):
        t = turn(model="claude-opus-5", output_tokens=0, cache_write_5m=1_000_000,
                  cache_write_1h=1_000_000, cache_read=1_000_000)
        rate = PRICING["claude-opus-5"]["input"]
        expected = (rate * CACHE_WRITE_5M_MULTIPLIER
                    + rate * CACHE_WRITE_1H_MULTIPLIER
                    + rate * CACHE_READ_MULTIPLIER)
        self.assertAlmostEqual(cost_for_turn(t), expected)

    def test_unknown_model_returns_none_not_zero(self):
        t = turn(model="some-future-model", input_tokens=1000)
        self.assertIsNone(cost_for_turn(t))

    def test_haiku_dated_model_id_is_priced(self):
        # message.model in a real transcript is the API's resolved ID, which
        # for Haiku 4.5 is dated ("claude-haiku-4-5-20251001"), not the bare
        # alias. A turn using that dated ID must be costed, not silently
        # dropped to uncosted.
        t = turn(model="claude-haiku-4-5-20251001", input_tokens=1_000_000, output_tokens=0)
        self.assertAlmostEqual(cost_for_turn(t), PRICING["claude-haiku-4-5-20251001"]["input"])

    def test_fable_5_is_priced(self):
        t = turn(model="claude-fable-5", input_tokens=1_000_000, output_tokens=0)
        self.assertAlmostEqual(cost_for_turn(t), PRICING["claude-fable-5"]["input"])


class ScanTypeLabelTests(unittest.TestCase):
    def test_board_and_categories(self):
        self.assertEqual(
            scan_type_label({"board": "new-grad", "categories": ["swe", "dsa"]}),
            "new-grad:swe+dsa",
        )

    def test_no_categories_falls_back_to_board_only(self):
        self.assertEqual(scan_type_label({"board": "internship", "categories": []}), "internship")

    def test_missing_board_has_placeholder(self):
        self.assertEqual(scan_type_label({}), "unknown-board")


class BuildRunsTests(unittest.TestCase):
    def test_simple_run_gets_tokens_and_duration(self):
        turns = [
            turn(timestamp="2026-08-12T00:00:00.000Z", skill="tailor-resume", output_tokens=50, request_id="r1"),
            turn(timestamp="2026-08-12T00:02:00.000Z", skill="tailor-resume", output_tokens=60, request_id="r2"),
        ]
        anchors = [anchor(timestamp="2026-08-12T00:02:00.000Z")]
        runs = build_runs(anchors, turns)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["turns"], 2)
        self.assertEqual(run["tokens"]["output"], 110)
        self.assertAlmostEqual(run["duration_s"], 120.0)

    def test_gap_boundary_excludes_older_unrelated_run(self):
        turns = [
            # an earlier, unrelated tailor-resume burst > 10 min before the anchor
            turn(timestamp="2026-08-12T00:00:00.000Z", skill="tailor-resume", output_tokens=999, request_id="old"),
            turn(timestamp="2026-08-12T00:20:00.000Z", skill="tailor-resume", output_tokens=50, request_id="r1"),
            turn(timestamp="2026-08-12T00:21:00.000Z", skill="tailor-resume", output_tokens=60, request_id="r2"),
        ]
        anchors = [anchor(timestamp="2026-08-12T00:21:00.000Z")]
        runs = build_runs(anchors, turns)
        run = runs[0]
        self.assertEqual(run["turns"], 2)
        self.assertEqual(run["tokens"]["output"], 110)

    def test_no_matching_turns_yields_null_tokens(self):
        anchors = [anchor(timestamp="2026-08-12T00:05:00.000Z")]
        runs = build_runs(anchors, [])
        self.assertIsNone(runs[0]["tokens"])
        self.assertIsNone(runs[0]["duration_s"])

    def test_apply_absorbs_nested_tailor_resume_without_double_counting(self):
        turns = [
            turn(timestamp="2026-08-12T00:00:00.000Z", skill="apply", output_tokens=10, request_id="a1"),
            # apply delegates to tailor-resume mid-run — turns stay tagged tailor-resume
            turn(timestamp="2026-08-12T00:01:00.000Z", skill="tailor-resume", output_tokens=20, request_id="t1"),
            turn(timestamp="2026-08-12T00:02:00.000Z", skill="tailor-resume", output_tokens=30, request_id="t2"),
            turn(timestamp="2026-08-12T00:03:00.000Z", skill="apply", output_tokens=40, request_id="a2"),
        ]
        anchors = [
            anchor(event="resume_tailor", timestamp="2026-08-12T00:02:00.000Z"),
            anchor(event="job_apply_e2e", timestamp="2026-08-12T00:03:00.000Z"),
        ]
        runs = build_runs(anchors, turns)
        tailor_run = next(r for r in runs if r["skill"] == "tailor-resume")
        apply_run = next(r for r in runs if r["skill"] == "apply")

        # apply's own totals absorb the nested tailor-resume turns
        self.assertEqual(apply_run["tokens"]["output"], 10 + 20 + 30 + 40)
        # the standalone tailor-resume record is flagged, not silently ok
        self.assertEqual(tailor_run["nested_in"], "apply")

    def test_batch_fired_run_gets_honest_nested_in_not_fabricated_tokens(self):
        # job-scan's acting-on-results step fires several tailor-resume
        # anchors back to back, but Claude Code never relabels those turns —
        # they stay tagged job-scan the whole time.
        turns = [
            turn(timestamp="2026-08-12T00:00:00.000Z", skill="job-scan", output_tokens=100, request_id="j1"),
            turn(timestamp="2026-08-12T00:00:05.000Z", skill="job-scan", output_tokens=100, request_id="j2"),
        ]
        anchors = [anchor(event="resume_tailor", timestamp="2026-08-12T00:00:07.000Z", company="PathAI")]
        runs = build_runs(anchors, turns)
        run = runs[0]
        self.assertIsNone(run["tokens"])  # never fabricate a per-company split
        self.assertEqual(run["nested_in"], "job-scan")

    def test_unknown_model_turn_counts_as_uncosted(self):
        turns = [turn(model="some-future-model", timestamp="2026-08-12T00:00:00.000Z", request_id="r1")]
        anchors = [anchor(timestamp="2026-08-12T00:00:00.000Z")]
        runs = build_runs(anchors, turns)
        run = runs[0]
        self.assertEqual(run["uncosted_turns"], 1)
        self.assertEqual(run["costed_turns"], 0)
        self.assertIsNone(run["cost_usd"])

    def test_job_scan_run_gets_scan_type_label(self):
        turns = [turn(timestamp="2026-08-12T00:00:00.000Z", skill="job-scan", request_id="r1")]
        anchors = [anchor(event="job_scan", timestamp="2026-08-12T00:00:00.000Z",
                           board="new-grad", categories=["swe", "quant"])]
        runs = build_runs(anchors, turns)
        self.assertEqual(runs[0]["scan_type"], "new-grad:swe+quant")


if __name__ == "__main__":
    unittest.main()
