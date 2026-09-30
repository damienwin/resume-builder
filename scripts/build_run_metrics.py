#!/usr/bin/env python3
"""Join knowledge/metrics.jsonl run records to Claude Code transcript turns,
producing knowledge/runs.jsonl: one record per run with latency, token
counts, cache hit rate, and cost.

metrics.jsonl records are *anchors* — one per run, written at the run's last
step, so its timestamp is the run's end. This script:
  1. loads every anchor and every transcript turn (scripts/cc_transcripts.py)
  2. for each anchor, finds the session it belongs to (the turn nearest
     at-or-before the anchor timestamp)
  3. walks that session backwards collecting turns for the anchor's skill,
     stopping at a >10 minute gap or the previous anchor for that skill
  4. sums tokens, prices them, and writes the joined record

`apply` delegates to `tailor-resume` mid-run, so an `apply` run's turns are
not contiguous — its segment spans from its first turn to the anchor and
absorbs any nested tailor-resume turns in that window. Those nested turns
are still emitted as their own tailor-resume record (flagged nested_in) but
excluded from top-level apply-vs-tailor-resume sums so nothing double counts.

Safe to delete and regenerate: knowledge/runs.jsonl is entirely derived. That
also means a run cannot be flagged "exclude this from perf stats" by editing
a field directly in runs.jsonl — the next --rebuild would silently drop it.
Instead, log a `run_exclusion` event to the append-only knowledge/metrics.jsonl
(scripts/log_metric.py run_exclusion '{"run_id": "...", "reason": "..."}')
and this script joins it back in on every regeneration, same as an anchor.
scripts/metrics_summary.py (--perf/--ab) and scripts/build_metrics_dashboard.py
read the same "excluded"/"exclude_reason" fields this script writes, so all
three reporting paths agree on which runs are excluded.

Usage:
    build_run_metrics.py [--rebuild] [--all-projects]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from cc_transcripts import Turn, load_turns, parse_ts

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = REPO_ROOT / "knowledge" / "metrics.jsonl"
RUNS_PATH = REPO_ROOT / "knowledge" / "runs.jsonl"

# Anchor event -> the attributionSkill its turns are tagged with.
EVENT_TO_SKILL = {
    "job_scan": "job-scan",
    "resume_tailor": "tailor-resume",
    "job_apply_e2e": "apply",
    "ats_score": "ats-score",
}

# A run's turns stop being "this run" once two consecutive turns are farther
# apart than this — covers thinking pauses and user back-and-forth without
# bleeding into an unrelated later run of the same skill.
GAP = timedelta(minutes=10)

# Per-million-token USD rates. Source: the claude-api skill's model table.
# Update here — nothing else in this file hardcodes a rate.
#
# `message.model` in a transcript is the API's fully-resolved model ID, not
# the alias — for every model here except Haiku that's identical to the
# alias (Full ID column is "-" in the claude-api skill's table), but Haiku
# 4.5 resolves to a dated ID ("claude-haiku-4-5-20251001"). Keying on the
# alias there silently priced every Haiku turn as unknown -> None -> counted
# as uncosted instead of priced.
PRICING = {
    "claude-fable-5": {"input": 10.00, "output": 50.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
}
# Cache write multiplies the base input rate; cache read is a fraction of it.
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0
CACHE_READ_MULTIPLIER = 0.1


def load_anchors() -> list[dict]:
    if not METRICS_PATH.exists():
        return []
    anchors = []
    with METRICS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            skill = EVENT_TO_SKILL.get(record.get("event"))
            if skill:
                record["_skill"] = skill
                anchors.append(record)
    anchors.sort(key=lambda r: r["timestamp"])
    return anchors


def load_exclusions() -> dict[str, str]:
    """run_id -> most recent reason, from append-only `run_exclusion` events
    in knowledge/metrics.jsonl.

    Exclusions live here (not as a field written into runs.jsonl) because
    this script fully regenerates runs.jsonl with "w" mode on every run —
    anything stored only in that file would be erased on the next rebuild.
    A later event for the same run_id (e.g. a corrected reason) wins; an
    exclusion is never removed by this loader, only re-logged with a new
    reason — un-excluding a run means the operator stops relying on the
    latest event's reason, not deleting history.
    """
    if not METRICS_PATH.exists():
        return {}
    exclusions: dict[str, str] = {}
    with METRICS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("event") != "run_exclusion":
                continue
            run_id = record.get("run_id")
            if run_id:
                exclusions[run_id] = record.get("reason")
    return exclusions


def scan_type_label(anchor: dict) -> str:
    """e.g. "new-grad:swe+dsa" — the breakdown key for job-scan runs."""
    board = anchor.get("board") or "unknown-board"
    categories = anchor.get("categories") or []
    return f"{board}:{'+'.join(categories)}" if categories else board


def cost_for_turn(turn: Turn) -> float | None:
    rates = PRICING.get(turn.model)
    if rates is None:
        return None
    cost = turn.input_tokens * rates["input"] / 1_000_000
    cost += turn.output_tokens * rates["output"] / 1_000_000
    cost += turn.cache_write_5m * rates["input"] * CACHE_WRITE_5M_MULTIPLIER / 1_000_000
    cost += turn.cache_write_1h * rates["input"] * CACHE_WRITE_1H_MULTIPLIER / 1_000_000
    cost += turn.cache_read * rates["input"] * CACHE_READ_MULTIPLIER / 1_000_000
    return cost


def make_run_id(anchor: dict, seen: dict[str, int]) -> str:
    """Stable id for an anchor, which is what a `run_exclusion` event names.

    Base form is "<event>-<timestamp>". Anchor timestamps have one-second
    precision, so two parallel forks finishing in the same second would
    collide — new records written by scripts/finalize_resume.py carry the
    run's random `run_scope` token (the same $RUN_ID used for run_timer
    --scope), appended here to disambiguate. Older records without it that
    still collide get "#2", "#3", ... in metrics.jsonl order; that suffix
    is deterministic for an unchanged file but is only as stable as the
    order of those same-second records, so prefer excluding by a
    run_scope-bearing id where one exists.
    """
    base = f"{anchor.get('event')}-{anchor['timestamp']}"
    if anchor.get("run_scope"):
        base = f"{base}-{anchor['run_scope']}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}#{seen[base]}"


def _closest_scope(turns_by_session, anchor_dt, skill=None, max_age=None):
    best_session = None
    best_dt = None
    for session_id, turns in turns_by_session.items():
        # turns are sorted ascending; find the last one <= anchor_dt
        candidate = None
        for turn in turns:
            if turn.dt > anchor_dt:
                break
            if skill is None or turn.skill == skill:
                candidate = turn
        if candidate is None:
            continue
        if max_age is not None and anchor_dt - candidate.dt > max_age:
            continue
        if best_dt is None or candidate.dt > best_dt:
            best_dt = candidate.dt
            best_session = session_id
    return best_session


def session_at_or_before(turns_by_session: dict[str, list[Turn]], anchor_dt,
                         skill: str | None = None) -> str | None:
    """The attribution scope (a top-level session, or one subagent file —
    see Turn.attribution_scope) an anchor belongs to.

    With `skill`, prefer the scope whose latest turn *tagged with that skill*
    at-or-before anchor_dt is closest (within GAP): concurrent fan-out forks
    are separate scopes, and without this preference an anchor would go to
    whichever scope merely had any turn last — e.g. the parent session, or a
    sibling fork. Falls back to the scope with the closest turn of any skill
    (which the nested_in fallback in build_runs relies on)."""
    if skill is not None:
        preferred = _closest_scope(turns_by_session, anchor_dt, skill, GAP)
        if preferred is not None:
            return preferred
    return _closest_scope(turns_by_session, anchor_dt)


def collect_run_turns(session_turns: list[Turn], anchor_dt, skill: str,
                       floor_dt) -> list[Turn]:
    """Walk `session_turns` (ascending) backwards from the last turn at or
    before anchor_dt, collecting turns tagged for `skill`, stopping at a >GAP
    pause or at `floor_dt` (the previous anchor for this skill in this
    session — never reach past it)."""
    # index of the last turn at or before the anchor
    end_idx = None
    for i, turn in enumerate(session_turns):
        if turn.dt <= anchor_dt:
            end_idx = i
        else:
            break
    if end_idx is None:
        return []

    collected = []
    prev_dt = None
    i = end_idx
    while i >= 0:
        turn = session_turns[i]
        if turn.dt <= floor_dt:
            break
        if prev_dt is not None and (prev_dt - turn.dt) > GAP:
            break
        if turn.skill == skill:
            collected.append(turn)
        prev_dt = turn.dt
        i -= 1
    collected.reverse()
    return collected


def build_runs(anchors: list[dict], all_turns: list[Turn],
                exclusions: dict[str, str] | None = None) -> list[dict]:
    # Keyed by attribution scope, not raw session id: subagent transcripts
    # share their parent's sessionId, but each subagent file is its own scope
    # so concurrent forks can't absorb each other's turns and a subagent's
    # turns can't bridge a >GAP pause in the parent's list.
    turns_by_session: dict[str, list[Turn]] = defaultdict(list)
    for turn in all_turns:
        turns_by_session[turn.attribution_scope].append(turn)
    for turns in turns_by_session.values():
        turns.sort(key=lambda t: t.timestamp)

    # Track, per (session, skill), the timestamp of the previous anchor —
    # that's the floor a later run of the same skill in the same session
    # must not walk past.
    last_anchor_dt: dict[tuple[str, str], object] = {}

    # Pre-pass: find every tailor-resume turn that an `apply` run will
    # absorb, before building any run record. A tailor-resume anchor is
    # always logged (and therefore processed, since anchors are timestamp-
    # sorted) *before* the apply anchor that delegated to it — so computing
    # this only as we reach each apply anchor in the main pass below would
    # always be too late to flag the tailor-resume run that came first.
    absorbed_turn_ids: set[tuple[str, str, str]] = set()
    apply_floor_dt: dict[str, object] = {}
    for a in anchors:
        if a["_skill"] != "apply":
            continue
        a_dt = parse_ts(a["timestamp"])
        sid = session_at_or_before(turns_by_session, a_dt, "apply")
        if sid is None:
            continue
        floor = apply_floor_dt.get(sid, a_dt - timedelta(days=3650))
        matched = collect_run_turns(turns_by_session[sid], a_dt, "apply", floor)
        apply_floor_dt[sid] = a_dt
        if not matched:
            continue
        start_dt = matched[0].dt
        for t in turns_by_session[sid]:
            if start_dt <= t.dt <= a_dt and t.skill == "tailor-resume":
                absorbed_turn_ids.add((sid, t.timestamp, t.request_id or ""))

    exclusions = exclusions or {}
    seen_run_ids: dict[str, int] = {}
    runs = []
    for idx, anchor in enumerate(anchors):
        skill = anchor["_skill"]
        anchor_dt = parse_ts(anchor["timestamp"])
        session_id = session_at_or_before(turns_by_session, anchor_dt, skill)

        run_id = make_run_id(anchor, seen_run_ids)
        run = {
            "run_id": run_id,
            "excluded": run_id in exclusions,
            "exclude_reason": exclusions.get(run_id),
            "event": anchor.get("event"),
            "skill": skill,
            "ended_at": anchor["timestamp"],
            "company": anchor.get("company"),
            "role": anchor.get("role"),
            "variant": anchor.get("variant"),
            "steps": anchor.get("steps"),
            "duration_from_steps_s": anchor.get("duration_s"),
            # job-scan only: distinguishes a "new-grad swe+dsa" scan from an
            # "internship quant" scan, etc., so perf stats can be broken out
            # by scan type instead of lumping every job-scan run together.
            "board": anchor.get("board"),
            "categories": anchor.get("categories"),
            "scan_type": scan_type_label(anchor) if skill == "job-scan" else None,
        }

        if session_id is None:
            run.update(started_at=None, duration_s=None, turns=0, models={},
                       tokens=None, cache_hit_rate=None, cost_usd=None,
                       costed_turns=0, uncosted_turns=0, nested_in=None)
            runs.append(run)
            continue

        session_turns = turns_by_session[session_id]
        floor_key = (session_id, skill)
        floor_dt = last_anchor_dt.get(floor_key)
        if floor_dt is None:
            floor_dt = anchor_dt - timedelta(days=3650)  # effectively no floor

        matched = collect_run_turns(session_turns, anchor_dt, skill, floor_dt)

        nested_in = None
        if skill == "apply" and matched:
            start_dt = matched[0].dt
            # absorb tailor-resume turns inside [start_dt, anchor_dt] in the
            # same session so apply's own totals aren't undercounted by the
            # nested delegation (absorbed_turn_ids was already computed for
            # this in the pre-pass above).
            nested = [t for t in session_turns
                      if start_dt <= t.dt <= anchor_dt and t.skill == "tailor-resume"]
            matched = sorted(matched + nested, key=lambda t: t.dt)
        elif skill == "tailor-resume":
            if any((session_id, t.timestamp, t.request_id or "") in absorbed_turn_ids
                   for t in matched):
                nested_in = "apply"

        if not matched:
            # attributionSkill only changes at a skill's own top-level
            # invocation — a skill that batch-fires another skill as an
            # internal step (e.g. job-scan's acting-on-results tailoring
            # several resumes in a row) leaves those turns tagged with the
            # *outer* skill. When no turn carries this anchor's own skill
            # tag, look for whatever skill *does* own the turns immediately
            # around the anchor and report that as the parent instead of
            # silently claiming "no data" — the tokens genuinely were spent,
            # just not attributable to this specific sub-run without
            # double-counting the parent's own total.
            window = [t for t in session_turns
                      if floor_dt < t.dt <= anchor_dt and (anchor_dt - t.dt) <= GAP]
            if window:
                tags = [t.skill for t in window if t.skill and t.skill != skill]
                if tags:
                    nested_in = max(set(tags), key=tags.count)

        last_anchor_dt[floor_key] = anchor_dt

        if not matched:
            run.update(started_at=None, duration_s=None, turns=0, models={},
                       tokens=None, cache_hit_rate=None, cost_usd=None,
                       costed_turns=0, uncosted_turns=0, nested_in=nested_in)
            runs.append(run)
            continue

        models = defaultdict(int)
        totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        cost_usd = 0.0
        costed_turns = 0
        uncosted_turns = 0
        for turn in matched:
            models[turn.model] += 1
            totals["input"] += turn.input_tokens
            totals["output"] += turn.output_tokens
            totals["cache_write"] += turn.cache_write
            totals["cache_read"] += turn.cache_read
            turn_cost = cost_for_turn(turn)
            if turn_cost is None:
                uncosted_turns += 1
            else:
                costed_turns += 1
                cost_usd += turn_cost

        cache_total = totals["cache_write"] + totals["cache_read"] + totals["input"]
        cache_hit_rate = (totals["cache_read"] / cache_total) if cache_total else None

        run.update(
            started_at=matched[0].timestamp,
            duration_s=(matched[-1].dt - matched[0].dt).total_seconds(),
            turns=len(matched),
            models=dict(models),
            tokens=totals,
            cache_hit_rate=cache_hit_rate,
            cost_usd=round(cost_usd, 4) if costed_turns else None,
            costed_turns=costed_turns,
            uncosted_turns=uncosted_turns,
            nested_in=nested_in,
        )
        runs.append(run)

    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="ignore the transcript parse cache")
    ap.add_argument("--all-projects", action="store_true",
                    help="join against transcripts from every project, not just this repo")
    args = ap.parse_args()

    anchors = load_anchors()
    turns = load_turns(all_projects=args.all_projects, rebuild=args.rebuild)
    exclusions = load_exclusions()
    runs = build_runs(anchors, turns, exclusions)

    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("w") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")

    covered = sum(1 for r in runs if r.get("tokens") is not None)
    print(f"Wrote {len(runs)} run(s) to {RUNS_PATH} ({covered} with token coverage)")


if __name__ == "__main__":
    main()
