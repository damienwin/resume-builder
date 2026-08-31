#!/usr/bin/env python3
"""Tests for cc_transcripts.py — the Claude Code transcript parser that
scripts/build_run_metrics.py joins against knowledge/metrics.jsonl.

Run: python3 scripts/test_cc_transcripts.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from cc_transcripts import (
    dedupe,
    find_transcripts,
    load_turns,
    parse_transcript,
    turn_from_record,
)


def assistant_line(session_id="s1", timestamp="2026-08-12T00:00:00.000Z",
                    skill="tailor-resume", model="claude-opus-5",
                    request_id="req_1", input_tokens=10, output_tokens=20,
                    cache_write_5m=0, cache_write_1h=0, cache_read=0):
    return json.dumps({
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "attributionSkill": skill,
        "requestId": request_id,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": cache_write_5m,
                    "ephemeral_1h_input_tokens": cache_write_1h,
                },
            },
        },
    })


class TurnFromRecordTests(unittest.TestCase):
    def test_normal_assistant_turn(self):
        record = json.loads(assistant_line())
        turn = turn_from_record(record)
        self.assertIsNotNone(turn)
        self.assertEqual(turn.skill, "tailor-resume")
        self.assertEqual(turn.model, "claude-opus-5")
        self.assertEqual(turn.input_tokens, 10)

    def test_skips_synthetic_model(self):
        record = json.loads(assistant_line())
        record["message"]["model"] = "<synthetic>"
        self.assertIsNone(turn_from_record(record))

    def test_skips_non_assistant_type(self):
        record = json.loads(assistant_line())
        record["type"] = "user"
        self.assertIsNone(turn_from_record(record))

    def test_missing_attribution_skill_is_none_not_a_crash(self):
        record = json.loads(assistant_line())
        del record["attributionSkill"]
        turn = turn_from_record(record)
        self.assertIsNotNone(turn)
        self.assertIsNone(turn.skill)

    def test_missing_timestamp_returns_none(self):
        record = json.loads(assistant_line())
        del record["timestamp"]
        self.assertIsNone(turn_from_record(record))

    def test_unsplit_cache_creation_total_falls_back_to_5m(self):
        record = json.loads(assistant_line())
        del record["message"]["usage"]["cache_creation"]
        record["message"]["usage"]["cache_creation_input_tokens"] = 500
        turn = turn_from_record(record)
        self.assertEqual(turn.cache_write_5m, 500)
        self.assertEqual(turn.cache_write_1h, 0)
        self.assertEqual(turn.cache_write, 500)


class ParseTranscriptTests(unittest.TestCase):
    def test_parses_multiple_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(assistant_line(request_id="req_1") + "\n"
                             + assistant_line(request_id="req_2") + "\n")
            turns, offset = parse_transcript(path)
            self.assertEqual(len(turns), 2)
            self.assertEqual(offset, path.stat().st_size)

    def test_truncated_final_line_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            complete = assistant_line(request_id="req_1")
            partial = '{"type": "assistant", "sessionId"'  # no trailing newline
            path.write_bytes((complete + "\n" + partial).encode())
            turns, offset = parse_transcript(path)
            self.assertEqual(len(turns), 1)
            # offset stops before the partial line, not at EOF
            self.assertLess(offset, path.stat().st_size)

    def test_corrupt_json_line_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text("not json at all\n" + assistant_line() + "\n")
            turns, offset = parse_transcript(path)
            self.assertEqual(len(turns), 1)

    def test_incremental_parse_from_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            first = assistant_line(request_id="req_1")
            path.write_text(first + "\n")
            turns1, offset1 = parse_transcript(path)
            self.assertEqual(len(turns1), 1)

            second = assistant_line(request_id="req_2")
            with path.open("a") as f:
                f.write(second + "\n")
            turns2, offset2 = parse_transcript(path, start_offset=offset1)
            self.assertEqual(len(turns2), 1)
            self.assertEqual(turns2[0].request_id, "req_2")


class DedupeTests(unittest.TestCase):
    def test_drops_repeated_request_id(self):
        turns = [
            turn_from_record(json.loads(assistant_line(request_id="req_1", timestamp="2026-08-12T00:00:00.000Z"))),
            turn_from_record(json.loads(assistant_line(request_id="req_1", timestamp="2026-08-12T00:00:01.000Z"))),
            turn_from_record(json.loads(assistant_line(request_id="req_2", timestamp="2026-08-12T00:00:02.000Z"))),
        ]
        out = dedupe(turns)
        self.assertEqual(len(out), 2)
        self.assertEqual([t.request_id for t in out], ["req_1", "req_2"])

    def test_turns_without_request_id_all_kept(self):
        turns = [
            turn_from_record(json.loads(assistant_line(request_id=None, timestamp="2026-08-12T00:00:00.000Z"))),
            turn_from_record(json.loads(assistant_line(request_id=None, timestamp="2026-08-12T00:00:01.000Z"))),
        ]
        # requestId None -> json.dumps writes null -> record.get("requestId") is None
        out = dedupe(turns)
        self.assertEqual(len(out), 2)

    def test_sorted_by_timestamp(self):
        turns = [
            turn_from_record(json.loads(assistant_line(request_id="req_2", timestamp="2026-08-12T00:00:05.000Z"))),
            turn_from_record(json.loads(assistant_line(request_id="req_1", timestamp="2026-08-12T00:00:01.000Z"))),
        ]
        out = dedupe(turns)
        self.assertEqual([t.request_id for t in out], ["req_1", "req_2"])


class FindTranscriptsTests(unittest.TestCase):
    def test_missing_root_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            self.assertEqual(find_transcripts(missing, all_projects=False), [])

    def test_finds_only_this_project_slug_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from cc_transcripts import THIS_PROJECT_SLUG
            (root / THIS_PROJECT_SLUG).mkdir()
            (root / "some-other-project").mkdir()
            (root / THIS_PROJECT_SLUG / "a.jsonl").write_text("")
            (root / "some-other-project" / "b.jsonl").write_text("")
            found = find_transcripts(root, all_projects=False)
            self.assertEqual(len(found), 1)
            self.assertTrue(str(found[0]).endswith("a.jsonl"))

    def test_all_projects_finds_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "proj-a").mkdir()
            (root / "proj-b").mkdir()
            (root / "proj-a" / "a.jsonl").write_text("")
            (root / "proj-b" / "b.jsonl").write_text("")
            found = find_transcripts(root, all_projects=True)
            self.assertEqual(len(found), 2)


class LoadTurnsCacheTests(unittest.TestCase):
    def test_incremental_cache_only_parses_new_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            from cc_transcripts import THIS_PROJECT_SLUG
            proj_dir = root / THIS_PROJECT_SLUG
            proj_dir.mkdir(parents=True)
            transcript = proj_dir / "session.jsonl"
            transcript.write_text(assistant_line(request_id="req_1") + "\n")

            # point the cache at a temp location so this test can't clobber
            # the repo's real cache file
            import cc_transcripts
            original_cache = cc_transcripts.CACHE_PATH
            cc_transcripts.CACHE_PATH = Path(tmp) / "cache.json"
            try:
                turns = load_turns(root=root)
                self.assertEqual(len(turns), 1)

                with transcript.open("a") as f:
                    f.write(assistant_line(request_id="req_2") + "\n")
                turns2 = load_turns(root=root)
                self.assertEqual(len(turns2), 2)
                self.assertTrue(cc_transcripts.CACHE_PATH.exists())
            finally:
                cc_transcripts.CACHE_PATH = original_cache

    def test_rebuild_ignores_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            from cc_transcripts import THIS_PROJECT_SLUG
            proj_dir = root / THIS_PROJECT_SLUG
            proj_dir.mkdir(parents=True)
            transcript = proj_dir / "session.jsonl"
            transcript.write_text(assistant_line(request_id="req_1") + "\n")

            import cc_transcripts
            original_cache = cc_transcripts.CACHE_PATH
            cc_transcripts.CACHE_PATH = Path(tmp) / "cache.json"
            try:
                turns = load_turns(root=root)
                self.assertEqual(len(turns), 1)
                turns_rebuilt = load_turns(root=root, rebuild=True)
                self.assertEqual(len(turns_rebuilt), 1)
            finally:
                cc_transcripts.CACHE_PATH = original_cache


if __name__ == "__main__":
    unittest.main()
