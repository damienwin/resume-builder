#!/usr/bin/env python3
"""Compute the --days fallback bound for a --since-last-scan job-scan run.

Marker-based lookup (state file's per-category/section URL) breaks whenever
that marker's posting scrolls off the board before the next run - closed,
filled, or just pushed down by a fast-moving table like speedyapply's. When
that happens the parser falls through to a flat --days bound instead of a
true "since last scan" cutoff.

Historically that bound was a hardcoded 14 days regardless of how recently
the board was actually scanned, which re-surfaces up to two weeks of
postings - including most of what a same-day or next-day scan already
showed - the moment a single marker ages off.

This computes a bound instead from the board's recorded `last_scan_at`
timestamp: elapsed time since that scan, plus a fixed grace period (so a
scan run slightly early, or a slow board update, doesn't undershoot), capped
at a safety ceiling for boards that haven't been scanned in a long time or
have never been scanned at all.

Usage:
    scan_fallback_days.py --state-file knowledge/job_scan_state.json \
        --board new-grad [--now 2026-09-24T21:08:30+00:00] \
        [--grace-days 2] [--min-days 1] [--max-days 14]

Prints a single float to stdout.
"""
import argparse
import json
import os
from datetime import datetime, timezone


def compute_fallback_days(state, board, now, grace_days=2.0, min_days=1.0, max_days=14.0):
    last_scan_at = state.get(board, {}).get("last_scan_at")
    if not last_scan_at:
        return max_days

    last = datetime.fromisoformat(last_scan_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    elapsed_days = (now - last).total_seconds() / 86400.0
    return min(max(elapsed_days + grace_days, min_days), max_days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-file", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--now", help="ISO timestamp to use as 'now' (default: current UTC time)")
    ap.add_argument("--grace-days", type=float, default=2.0)
    ap.add_argument("--min-days", type=float, default=1.0)
    ap.add_argument("--max-days", type=float, default=14.0)
    args = ap.parse_args()

    state = {}
    if os.path.exists(args.state_file):
        with open(args.state_file, encoding="utf-8") as f:
            state = json.load(f)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    days = compute_fallback_days(
        state, args.board, now,
        grace_days=args.grace_days, min_days=args.min_days, max_days=args.max_days,
    )
    print(days)


if __name__ == "__main__":
    main()
