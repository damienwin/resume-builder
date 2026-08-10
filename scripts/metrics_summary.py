#!/usr/bin/env python3
"""Summarize knowledge/metrics.jsonl for a quick sanity check.

Usage:
    python3 scripts/metrics_summary.py [--event TYPE] [--since YYYY-MM-DD]
"""
import argparse
import json
from collections import Counter
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", help="filter to one event type")
    parser.add_argument("--since", help="only records at/after this ISO date")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    metrics_path = repo_root / "knowledge" / "metrics.jsonl"
    if not metrics_path.exists():
        print("No metrics logged yet.")
        return

    records = []
    with metrics_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    if args.event:
        records = [r for r in records if r.get("event") == args.event]
    if args.since:
        records = [r for r in records if r.get("timestamp", "") >= args.since]

    print(f"{len(records)} record(s) matched.\n")
    counts = Counter(r.get("event", "unknown") for r in records)
    for event, n in counts.most_common():
        print(f"  {event}: {n}")

    print()
    for r in records[-20:]:
        print(json.dumps(r))

if __name__ == "__main__":
    main()
