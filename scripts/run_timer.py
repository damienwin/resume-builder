#!/usr/bin/env python3
"""Coarse step timing for a skill run, bridged across turns via a scratch file.

A skill's steps happen across many separate tool calls (and thus many
separate Bash invocations), so nothing in-process can hold a stopwatch for
the whole run. This script persists {t0, marks} to a temp file keyed by skill
name and lets each step call `mark` independently.

Usage:
    run_timer.py start <skill>              # begin timing, e.g. Step 1
    run_timer.py mark <skill> <label>        # record a step boundary
    run_timer.py finish <skill>              # print {"duration_s", "steps"}, clean up

`finish` on a missing/corrupt timer file prints "{}" rather than raising —
a run's own log_metric.py call must never fail because timing broke.

The scratch file is additionally scoped by `$CLAUDE_CODE_SESSION_ID` (stable
across separate Bash tool calls within one session/fork, distinct across
concurrent sessions/forks — verified live: a bare PID-based scheme doesn't
work here since each Bash tool invocation gets a fresh shell process, so
os.getppid() differs between `start` and the `mark`/`finish` that follow it
in the very same run). Without this, two concurrent runs of the same skill —
exactly what `acting-on-results.md`'s parallel-fork dispatch produces when
several forks each run `tailor-resume`/`apply` at once — raced on one shared
file: whichever fork's `start` ran last won, and every fork's `mark`/
`finish` then read/wrote that same file, corrupting timing for the others.
Falls back to skill-name-only scoping when the env var is unset (e.g. run
outside Claude Code), which keeps single-run local testing working
unchanged but reintroduces the same race if run concurrently that way.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def timer_path(skill: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in skill)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if session:
        safe_session = "".join(c if c.isalnum() or c in "-_" else "_" for c in session)
        safe = f"{safe}-{safe_session}"
    return Path(tempfile.gettempdir()) / f"resume-builder-run-{safe}.json"


def start(skill: str) -> None:
    path = timer_path(skill)
    path.write_text(json.dumps({"skill": skill, "t0": time.time(), "marks": []}))


def mark(skill: str, label: str) -> None:
    path = timer_path(skill)
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return  # no active timer for this skill — nothing to record against
    state["marks"].append({"label": label, "t": time.time()})
    path.write_text(json.dumps(state))


def finish(skill: str) -> dict:
    path = timer_path(skill)
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    t0 = state.get("t0")
    marks = state.get("marks", [])
    now = time.time()
    result: dict = {}
    if isinstance(t0, (int, float)):
        result["duration_s"] = round(now - t0, 2)
        # Each mark's label names the step that just finished — its duration
        # is the time since t0 (or the previous mark). A mark only records a
        # finish, so the FIRST mark's interval [t0, mark] is that mark's own
        # step duration, not a dangling unlabeled span.
        steps = {}
        prev_t = t0
        for m in marks:
            label, t = m.get("label"), m.get("t")
            if not isinstance(t, (int, float)) or not label:
                continue
            steps[label] = round(t - prev_t, 2)
            prev_t = t
        if steps:
            result["steps"] = steps

    try:
        path.unlink()
    except OSError:
        pass
    return result


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd, skill = sys.argv[1], sys.argv[2]
    if cmd == "start":
        start(skill)
    elif cmd == "mark":
        if len(sys.argv) < 4:
            print("mark requires a label", file=sys.stderr)
            sys.exit(1)
        mark(skill, sys.argv[3])
    elif cmd == "finish":
        print(json.dumps(finish(skill)))
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
