#!/usr/bin/env python3
"""Coarse step timing for a skill run, bridged across turns via a scratch file.

A skill's steps happen across many separate tool calls (and thus many
separate Bash invocations), so nothing in-process can hold a stopwatch for
the whole run. This script persists {t0, marks} to a temp file keyed by skill
name and lets each step call `mark` independently.

Usage:
    run_timer.py start  <skill> [--scope TOKEN]
    run_timer.py mark   <skill> <label> [--scope TOKEN]
    run_timer.py finish <skill> [--scope TOKEN]   # prints {"duration_s","steps"}

`finish` on a missing/corrupt timer file prints "{}" rather than raising —
a run's own log_metric.py call must never fail because timing broke.

## Scoping the scratch file

The file name is keyed by skill, then by `--scope` if given, then by
`$CLAUDE_CODE_SESSION_ID`.

**`--scope` is what makes concurrent runs of the same skill safe, and the
session id is NOT sufficient on its own.** Session scoping was added first
and is not enough: parallel forks dispatched by
`job-scan/references/acting-on-results.md` — one per posting, each running
the full `tailor-resume` and `apply` skills — do not reliably get distinct
`CLAUDE_CODE_SESSION_ID` values. Observed live on 2026-09-01: six forks ran
`tailor-resume` concurrently, all six `start` calls wrote the same path, the
first fork to `finish` unlinked it, and the later forks' `finish` returned
`{}` — so those `resume_tailor` records shipped with no `duration_s`/`steps`
at all. Whichever forks did get a number got someone else's `t0`.

So any skill that can run in a parallel fork MUST pass a `--scope` token
unique to its unit of work. `tailor-resume` and `apply` pass the per-job
slug, the same token that already namespaces their `build/<slug>.*` working
files; that slug is unique per posting, which is exactly the granularity a
fan-out runs at. `job-scan` and `ats-score` run once in the main
conversation and can omit it.

Falls back to skill-name-only scoping when neither `--scope` nor the env var
is present (e.g. run outside Claude Code), which keeps single-run local
testing working unchanged but reintroduces the race if run concurrently that
way.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def _sanitize(token: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in token)


def timer_path(skill: str, scope: str = "") -> Path:
    safe = _sanitize(skill)
    if scope:
        safe = f"{safe}-{_sanitize(scope)}"
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if session:
        safe = f"{safe}-{_sanitize(session)}"
    return Path(tempfile.gettempdir()) / f"resume-builder-run-{safe}.json"


def start(skill: str, scope: str = "") -> None:
    path = timer_path(skill, scope)
    path.write_text(
        json.dumps({"skill": skill, "scope": scope, "t0": time.time(), "marks": []})
    )


def mark(skill: str, label: str, scope: str = "") -> None:
    path = timer_path(skill, scope)
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return  # no active timer for this skill — nothing to record against
    state["marks"].append({"label": label, "t": time.time()})
    path.write_text(json.dumps(state))


def finish(skill: str, scope: str = "") -> dict:
    path = timer_path(skill, scope)
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
    argv = sys.argv[1:]
    scope = ""
    if "--scope" in argv:
        i = argv.index("--scope")
        if i + 1 >= len(argv):
            print("--scope requires a token", file=sys.stderr)
            sys.exit(1)
        scope = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]

    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    cmd, skill = argv[0], argv[1]
    if cmd == "start":
        start(skill, scope)
    elif cmd == "mark":
        if len(argv) < 3:
            print("mark requires a label", file=sys.stderr)
            sys.exit(1)
        mark(skill, argv[2], scope)
    elif cmd == "finish":
        print(json.dumps(finish(skill, scope)))
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
