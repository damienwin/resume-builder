#!/usr/bin/env python3
"""Generate the Codex-facing `.agents/skills` mirror from canonical skills.

`.claude/skills` is the single source of truth (AGENTS.md). `.agents/skills`
exists so Codex/opencode can discover the same skills, but it had drifted: a
blind `claude` -> `Codex` substitution had corrupted real identifiers and paths
(`Codex-haiku-4-5` instead of the actual model id, `~/.Codex/plugins` instead
of `~/.claude/plugins`) and left dead `.Codex/skills/...` references.

The only intended difference is the skill-tree path: internal
`.claude/skills/<name>/SKILL.md` references are rewritten to
`.agents/skills/<name>/SKILL.md` so they resolve inside the mirror. Every other
byte is copied verbatim, because a model id, a home-directory path, and a
product name are facts, not formatting.

Usage:
    sync_skills.py [--check]
    --check: exit 1 if the mirror is out of date; change nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = REPO_ROOT / ".claude" / "skills"
MIRROR = REPO_ROOT / ".agents" / "skills"

PATH_SUBSTITUTIONS = (((".claude/skills/", ".agents/skills/")),)

# `.agents`-only thin redirects created by the slash-command migration. They are
# generated here too so a hand edit cannot reintroduce a dead path.
SOURCE_COMMANDS = {
    "source-command-start": {
        "description": "Guided entry point — scan Simplify's job boards, then optionally tailor and/or apply to what looks worth pursuing",
        "target": "start",
        "body": (
            "Run the **start** skill (`.agents/skills/start/SKILL.md`). It runs the\n"
            "`job-scan` skill's interactive checklist first, then offers to tailor and/or\n"
            "apply to whatever the user selects from the results. Follow that skill's\n"
            "steps exactly — never skip its confirmation prompts."
        ),
    },
    "source-command-app-profile-sync": {
        "description": "Sync knowledge/ facts into the job-apply plugin's autofill store (~/.job-apply)",
        "target": "app-profile-sync",
        "body": (
            "Run the **app-profile-sync** skill (`.agents/skills/app-profile-sync/SKILL.md`)\n"
            "to merge frontmatter facts from `knowledge/` into the job-apply plugin's local\n"
            "store via the plugin's own helper script. Follow that skill's steps exactly."
        ),
    },
}


def _render(text: str) -> str:
    for old, new in PATH_SUBSTITUTIONS:
        text = text.replace(old, new)
    return text


def generate() -> dict[str, str]:
    """Return {relative_path: content} for every file the mirror should hold."""
    files: dict[str, str] = {}
    for skill_dir in sorted(p for p in CANONICAL.iterdir() if p.is_dir()):
        for source in sorted(skill_dir.rglob("*")):
            if not source.is_file():
                continue
            rel = source.relative_to(CANONICAL)
            files[str(rel)] = _render(source.read_text(encoding="utf-8"))

    for name, spec in SOURCE_COMMANDS.items():
        files[f"{name}/SKILL.md"] = (
            "---\n"
            f'name: "{name}"\n'
            f'description: "{spec["description"]}"\n'
            "---\n\n"
            f"# {name}\n\n"
            "Use this skill when the user asks to run the migrated source command "
            f'`{spec["target"]}`.\n\n'
            "## Command Template\n\n"
            f"{spec['body']}\n"
        )
    return files


def _existing() -> dict[str, str]:
    if not MIRROR.exists():
        return {}
    return {
        str(p.relative_to(MIRROR)): p.read_text(encoding="utf-8")
        for p in sorted(MIRROR.rglob("*")) if p.is_file()
    }


def check() -> list[str]:
    expected, actual = generate(), _existing()
    problems = []
    for rel, content in expected.items():
        if rel not in actual:
            problems.append(f"missing: {rel}")
        elif actual[rel] != content:
            problems.append(f"out of date: {rel}")
    for rel in actual:
        if rel not in expected:
            problems.append(f"unexpected: {rel}")
    return problems


def sync() -> int:
    expected = generate()
    for rel, content in expected.items():
        dest = MIRROR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    for rel in _existing():
        if rel not in expected:
            (MIRROR / rel).unlink()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the mirror is stale; write nothing")
    args = ap.parse_args()
    if args.check:
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    sync()
    print(f"Synced {MIRROR} from {CANONICAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
