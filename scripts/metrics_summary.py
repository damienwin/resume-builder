#!/usr/bin/env python3
"""Summarize knowledge/metrics.jsonl for a quick sanity check.

Usage:
    python3 scripts/metrics_summary.py [--event TYPE] [--since YYYY-MM-DD]
    python3 scripts/metrics_summary.py --perf [--since YYYY-MM-DD]
    python3 scripts/metrics_summary.py --ab VARIANT_A VARIANT_B

--perf reads knowledge/runs.jsonl (build it first with
scripts/build_run_metrics.py) and prints per-skill p50/p95 latency, mean
tokens, cost, and cache hit rate. --ab does the same comparison narrowed to
two variant tags (see RESUME_BUILDER_VARIANT in scripts/log_metric.py).
"""
import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = REPO_ROOT / "knowledge" / "metrics.jsonl"
RUNS_PATH = REPO_ROOT / "knowledge" / "runs.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * q))
    return values[idx]


def print_perf_table(runs: list[dict], label: str = "") -> None:
    if label:
        print(f"\n=== {label} ===")
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("duration_s") is not None:
            by_skill[r["skill"]].append(r)

    if not by_skill:
        print("  no runs with measured duration in this slice")
        return

    header = f"{'skill':22}{'runs':>6}{'p50_s':>9}{'p95_s':>9}{'mean_out_tok':>14}{'mean_cost_usd':>15}{'cache_hit%':>12}"
    print(header)
    for skill, group in sorted(by_skill.items(), key=lambda kv: -len(kv[1])):
        durations = [r["duration_s"] for r in group]
        out_toks = [r["tokens"]["output"] for r in group if r.get("tokens")]
        costs = [r["cost_usd"] for r in group if r.get("cost_usd") is not None]
        hit_rates = [r["cache_hit_rate"] for r in group if r.get("cache_hit_rate") is not None]
        p50 = percentile(durations, 0.5)
        p95 = percentile(durations, 0.95)
        mean_out = statistics.mean(out_toks) if out_toks else None
        mean_cost = statistics.mean(costs) if costs else None
        mean_hit = statistics.mean(hit_rates) * 100 if hit_rates else None
        print(f"{skill:22}{len(group):>6}{p50:>9.0f}{p95:>9.0f}"
              f"{(mean_out if mean_out is not None else 0):>14,.0f}"
              f"{(mean_cost if mean_cost is not None else 0):>15.3f}"
              f"{(mean_hit if mean_hit is not None else 0):>11.1f}%")

    covered = sum(1 for r in runs if r.get("tokens") is not None)
    print(f"\n{covered}/{len(runs)} run(s) had token coverage from Claude Code transcripts.")

    scan_runs = [r for r in runs if r.get("scan_type") and r.get("duration_s") is not None]
    if scan_runs:
        print("\nby scan type (board:categories):")
        by_type: dict[str, list[dict]] = defaultdict(list)
        for r in scan_runs:
            by_type[r["scan_type"]].append(r)
        print(f"  {'scan_type':28}{'runs':>6}{'p50_s':>9}{'p95_s':>9}{'mean_cost_usd':>15}")
        for scan_type, group in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            durations = [r["duration_s"] for r in group]
            costs = [r["cost_usd"] for r in group if r.get("cost_usd") is not None]
            mean_cost = statistics.mean(costs) if costs else 0
            print(f"  {scan_type:28}{len(group):>6}{percentile(durations, 0.5):>9.0f}"
                  f"{percentile(durations, 0.95):>9.0f}{mean_cost:>15.3f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", help="filter to one event type")
    parser.add_argument("--since", help="only records at/after this ISO date")
    parser.add_argument("--perf", action="store_true",
                        help="print latency/token/cost stats from knowledge/runs.jsonl")
    parser.add_argument("--ab", nargs=2, metavar=("VARIANT_A", "VARIANT_B"),
                        help="compare two RESUME_BUILDER_VARIANT tags side by side")
    args = parser.parse_args()

    if args.perf or args.ab:
        runs = load_jsonl(RUNS_PATH)
        if not runs:
            print(f"No {RUNS_PATH} yet — run scripts/build_run_metrics.py first.")
            return
        if args.since:
            runs = [r for r in runs if (r.get("ended_at") or "") >= args.since]
        if args.ab:
            variant_a, variant_b = args.ab
            print_perf_table([r for r in runs if r.get("variant") == variant_a], variant_a)
            print_perf_table([r for r in runs if r.get("variant") == variant_b], variant_b)
        else:
            print_perf_table(runs)
        return

    records = load_jsonl(METRICS_PATH)
    if not records:
        print("No metrics logged yet.")
        return

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
