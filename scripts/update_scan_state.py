#!/usr/bin/env python3
"""Update job-scan's "since last scan" state file after a successful run.

Reads each parser's `category_top` (simplify) / `section_top` (speedyapply)
output - the topmost row physically present in each category section /
table, independent of the --days filter used for that run - and records it
as the new marker.

This must NOT be derived from the (already --days-filtered) `entries` list:
a category/section with zero rows inside that run's day window would then
get no marker at all, and the next --since-last-scan run would fall back to
re-scanning that whole category from scratch instead of picking up where
this run's board state actually left off.

Usage:
    update_scan_state.py --board new-grad --simplify s.json \
        [--speedyapply p.json] --state-file knowledge/job_scan_state.json
"""
import argparse
import json
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True, help="new-grad or internship")
    ap.add_argument("--simplify", required=True, help="path to simplify parser's JSON output")
    ap.add_argument("--speedyapply", help="path to speedyapply parser's JSON output, if run")
    ap.add_argument("--state-file", required=True)
    args = ap.parse_args()

    state = {}
    if os.path.exists(args.state_file):
        with open(args.state_file, encoding="utf-8") as f:
            state = json.load(f)

    board_state = state.setdefault(args.board, {})

    with open(args.simplify, encoding="utf-8") as f:
        simplify_top = json.load(f).get("category_top", {})
    board_state.setdefault("simplify", {}).update(simplify_top)

    if args.speedyapply:
        with open(args.speedyapply, encoding="utf-8") as f:
            speedyapply_top = json.load(f).get("section_top", {})
        board_state.setdefault("speedyapply", {}).update(speedyapply_top)

    os.makedirs(os.path.dirname(args.state_file) or ".", exist_ok=True)
    with open(args.state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")

    print(f"Updated {args.state_file} for board={args.board}")


if __name__ == "__main__":
    main()
