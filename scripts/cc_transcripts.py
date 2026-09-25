#!/usr/bin/env python3
"""Read per-turn token usage out of Claude Code's own session transcripts.

Claude Code writes every session to ~/.claude/projects/<slug>/<session>.jsonl,
one JSON object per line. Assistant lines carry the billing data we need —
`message.usage` (input/output/cache tokens), `message.model`, `requestId`, and
`attributionSkill`, the skill that turn belongs to. Nothing in this repo has to
be instrumented to collect it; it is already on disk.

This module only reads. It never writes to a transcript.

Usage as a CLI (prints a per-skill summary):
    cc_transcripts.py [--all-projects] [--transcript-root DIR] [--rebuild]

Usage as a library:
    from cc_transcripts import load_turns
    turns = load_turns()          # list[Turn], sorted by timestamp

Parsing 285 MB of transcripts on every run is slow enough that nobody would run
it, so results are cached per file in knowledge/.cc_transcript_cache.json and
only bytes appended since the last pass are parsed. --rebuild forces a full
re-read.

## Subagent transcripts and attribution

A subagent (a fork Claude Code dispatches mid-session — a review subagent,
job-scan's fan-out, etc.) writes its own turns to
<slug>/<session>/subagents/agent-*.jsonl, entirely separate from the parent
session's own .jsonl. find_transcripts() now reads these files too, so their
tokens are no longer invisible to load_turns()/the CLI summary above.

Those turns carry `attributionSkill: None` (subagents don't get the outer
session's skill tag), so they land in the "(none)" bucket here and are NOT
attributed to any specific skill's run by scripts/build_run_metrics.py's
skill-matching join (which requires `turn.skill == skill`). This was a
deliberate choice over attempting time-window + originating-file heuristics
to guess which run a subagent turn belongs to: a wrong guess would silently
misattribute cost to the wrong skill, which is worse than an honest "not
attributed." A Phase 4 comparison that needs subagent cost/tokens counted
against a specific run must either (a) drive that run from a top-level
session only (no subagent fan-out) so every turn keeps the run's skill tag,
or (b) treat the subagent total as a repo-wide addendum, not a per-run
figure, until a skill-tag-on-subagent mechanism exists upstream.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "knowledge" / ".cc_transcript_cache.json"

DEFAULT_TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"


def slugify_path(path: Path) -> str:
    """Claude Code's actual project-directory slugify rule: every
    non-alphanumeric character (not just '/') maps to '-'.

    A previous hardcoded slug here was stale — it named a directory from an
    earlier checkout location. That stale directory still exists on disk, so
    a "does the directory exist" check alone would never have caught the
    bug: it silently read a different checkout's transcripts, not zero
    transcripts. Deriving the slug from REPO_ROOT keeps it correct across
    checkout moves/renames instead of needing a manual update each time.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


# The transcript directory for this repo, derived the same way Claude Code
# derives it from the cwd it was launched in.
THIS_PROJECT_SLUG = slugify_path(REPO_ROOT)

# Turns the model didn't actually produce (interrupts, local errors). They
# carry no real usage and would pollute both latency and token totals.
SYNTHETIC_MODEL = "<synthetic>"

CACHE_VERSION = 1


@dataclass
class Turn:
    """One assistant turn, reduced to the fields we bill and time on."""
    session_id: str
    timestamp: str
    skill: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_5m: int
    cache_write_1h: int
    cache_read: int
    request_id: str | None
    is_sidechain: bool

    @property
    def dt(self) -> datetime:
        return parse_ts(self.timestamp)

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h


def parse_ts(value: str) -> datetime:
    """Transcript timestamps are ISO-8601 with a 'Z' suffix, which
    fromisoformat only learned to accept in 3.11."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def turn_from_record(record: dict) -> Turn | None:
    """Reduce one transcript line to a Turn, or None if it isn't a real
    assistant turn we can bill."""
    if record.get("type") != "assistant":
        return None

    message = record.get("message") or {}
    model = message.get("model")
    if not model or model == SYNTHETIC_MODEL:
        return None

    timestamp = record.get("timestamp")
    if not timestamp:
        return None

    usage = message.get("usage") or {}

    # cache_creation_input_tokens is the total; the per-TTL split lives in a
    # nested `cache_creation` object. The two TTLs bill at different rates, so
    # prefer the split and fall back to treating an unsplit total as 5m.
    creation = usage.get("cache_creation") or {}
    write_5m = creation.get("ephemeral_5m_input_tokens")
    write_1h = creation.get("ephemeral_1h_input_tokens")
    if write_5m is None and write_1h is None:
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0
        write_1h = 0

    return Turn(
        session_id=record.get("sessionId") or record.get("session_id") or "",
        timestamp=timestamp,
        skill=record.get("attributionSkill"),
        model=model,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_write_5m=write_5m or 0,
        cache_write_1h=write_1h or 0,
        cache_read=usage.get("cache_read_input_tokens", 0) or 0,
        request_id=record.get("requestId"),
        is_sidechain=bool(record.get("isSidechain")),
    )


def parse_transcript(path: Path, start_offset: int = 0) -> tuple[list[Turn], int]:
    """Parse one transcript from `start_offset` bytes in.

    Returns (turns, offset_reached). The offset only advances past complete
    lines — a transcript being written to right now can end mid-line, and
    resuming from a byte count inside a partial line would corrupt the next
    pass.
    """
    turns: list[Turn] = []
    offset = start_offset
    with path.open("rb") as f:
        f.seek(start_offset)
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # partial trailing line; leave the offset before it
            offset += len(raw)
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a corrupt line is data loss, not a crash
            turn = turn_from_record(record)
            if turn is not None:
                turns.append(turn)
    return turns, offset


def find_transcripts(root: Path, all_projects: bool) -> list[Path]:
    """Every top-level session transcript, plus every subagent transcript
    nested under it (<slug>/<session>/subagents/agent-*.jsonl).

    Subagent-driven turns (a fork Claude Code dispatches mid-session, e.g.
    job-scan's fan-out or a review subagent) are written to their own file
    under the parent session's directory, entirely separate from the
    session's own top-level .jsonl. The old glob only ever looked at
    <slug>/*.jsonl, so every subagent turn was silently unread — this repo's
    own transcript root has dozens of such files today.
    """
    if not root.exists():
        return []
    if all_projects:
        project_dirs = [p for p in root.iterdir() if p.is_dir()]
    else:
        project_dirs = [root / THIS_PROJECT_SLUG]

    found: list[Path] = []
    for project_dir in project_dirs:
        if not project_dir.exists():
            continue
        found.extend(project_dir.glob("*.jsonl"))
        found.extend(project_dir.glob("*/subagents/agent-*.jsonl"))
    return sorted(found)


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {"version": CACHE_VERSION, "files": {}}
    try:
        cache = json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "files": {}}
    if cache.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "files": {}}
    return cache


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache))


def load_turns(root: Path | None = None, all_projects: bool = False,
               rebuild: bool = False, use_cache: bool = True) -> list[Turn]:
    """Every assistant turn across the selected transcripts, deduped and
    sorted by timestamp."""
    root = root or DEFAULT_TRANSCRIPT_ROOT
    cache = {"version": CACHE_VERSION, "files": {}} if (rebuild or not use_cache) else load_cache()
    files = cache.setdefault("files", {})

    turns: list[Turn] = []
    for path in find_transcripts(root, all_projects):
        key = str(path)
        stat = path.stat()
        entry = files.get(key)

        # A file that shrank was truncated or replaced — the cached offset is
        # meaningless, so start over on it.
        if entry and entry.get("size", 0) <= stat.st_size:
            cached_turns = [Turn(**t) for t in entry["turns"]]
            new_turns, offset = parse_transcript(path, entry.get("offset", 0))
            merged = cached_turns + new_turns
        else:
            merged, offset = parse_transcript(path)

        files[key] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "offset": offset,
            "turns": [asdict(t) for t in merged],
        }
        turns.extend(merged)

    if use_cache:
        save_cache(cache)

    return dedupe(turns)


def dedupe(turns: list[Turn]) -> list[Turn]:
    """Drop repeated requestIds and sort by time.

    A retried or resumed request can be written to more than one transcript
    (or twice within one), and counting it twice inflates both tokens and
    cost. Turns with no requestId can't be deduped, so they're all kept.

    Verified against this repo's own transcripts: a single requestId is
    commonly written as several JSONL lines (one per content block in a
    multi-tool-call response — up to a dozen observed), each carrying the
    *identical* final `usage` object, not an incremental delta. Summing
    every line therefore overcounts by that many multiples; deduping by
    requestId is what makes the total match one billed API request.
    (`npx ccusage@latest session` run against these same transcripts came
    out ~1.9x higher than this module's totals for the same date range and
    session set — consistent with it not deduping the same way, not with
    this module undercounting. Cross-checked 2026-08-28.)
    """
    seen: set[str] = set()
    out: list[Turn] = []
    for turn in turns:
        rid = turn.request_id
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        out.append(turn)
    out.sort(key=lambda t: t.timestamp)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript-root", type=Path, default=DEFAULT_TRANSCRIPT_ROOT)
    ap.add_argument("--all-projects", action="store_true",
                    help="every project, not just this repo's transcripts")
    ap.add_argument("--rebuild", action="store_true", help="ignore the cache")
    args = ap.parse_args()

    turns = load_turns(args.transcript_root, args.all_projects, args.rebuild)
    if not turns:
        print("No transcripts found.", file=sys.stderr)
        return

    by_skill: dict[str, list[Turn]] = {}
    for turn in turns:
        by_skill.setdefault(turn.skill or "(none)", []).append(turn)

    print(f"{len(turns)} assistant turn(s) across {len({t.session_id for t in turns})} session(s)\n")
    header = f"{'skill':24}{'turns':>7}{'output':>12}{'cache_write':>13}{'cache_read':>13}"
    print(header)
    for skill, group in sorted(by_skill.items(), key=lambda kv: -len(kv[1])):
        print(f"{skill:24}{len(group):>7}"
              f"{sum(t.output_tokens for t in group):>12,}"
              f"{sum(t.cache_write for t in group):>13,}"
              f"{sum(t.cache_read for t in group):>13,}")


if __name__ == "__main__":
    main()
