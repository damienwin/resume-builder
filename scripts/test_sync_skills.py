#!/usr/bin/env python3
"""Parity test: `.agents/skills` must match a fresh generation from `.claude/skills`.

This is the guard against the drift that let a blind `claude` -> `Codex`
substitution corrupt model ids and home paths in the Codex-facing mirror.
"""
import subprocess
import sys
import unittest
from pathlib import Path

from sync_skills import CANONICAL, MIRROR, generate

SCRIPT = Path(__file__).with_name("sync_skills.py")


class SyncSkillsTests(unittest.TestCase):
    def test_mirror_is_in_sync_with_canonical(self):
        expected = generate()
        actual = {
            str(p.relative_to(MIRROR)): p.read_text(encoding="utf-8")
            for p in sorted(MIRROR.rglob("*")) if p.is_file()
        }
        self.assertEqual(sorted(actual), sorted(expected))
        for rel, content in expected.items():
            self.assertEqual(actual[rel], content, f"drift in {rel}")

    def test_no_dead_codex_paths_survive(self):
        mirror_text = "\n".join(
            p.read_text(encoding="utf-8") for p in MIRROR.rglob("*.md")
        )
        for needle in (".Codex/skills", "Codex-haiku", ".Codex/plugins", ".Codex/projects"):
            self.assertNotIn(needle, mirror_text)

    def test_check_cli_passes(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_is_the_source_of_truth(self):
        self.assertTrue(CANONICAL.is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
