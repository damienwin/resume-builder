#!/usr/bin/env python3
"""Aggregate knowledge/metrics.jsonl into the data blob the dashboard reads.

Reads the template at knowledge/dashboard_template.html (design source,
tracked-in-spirit but gitignored with the rest of knowledge/), replaces the
%%METRICS_DATA%% placeholder with a fresh aggregation, and writes the final
standalone page to knowledge/dashboard.html — that path is what gets
published via the Artifact tool, and re-running this script + republishing
is how the dashboard picks up new metrics.

Usage:
    python3 scripts/build_metrics_dashboard.py
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = REPO_ROOT / "knowledge" / "metrics.jsonl"
RUNS_PATH = REPO_ROOT / "knowledge" / "runs.jsonl"
TEMPLATE_PATH = REPO_ROOT / "knowledge" / "dashboard_template.html"
OUTPUT_PATH = REPO_ROOT / "knowledge" / "dashboard.html"

WEEKS_OF_HISTORY = 10


def load_records():
    if not METRICS_PATH.exists():
        return []
    records = []
    with METRICS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("timestamp", ""))
    return records


def load_runs():
    """knowledge/runs.jsonl — the metrics.jsonl anchors joined against Claude
    Code transcript turns by scripts/build_run_metrics.py. Absent on a fresh
    clone or before that script has been run; treated as "no perf data yet",
    not an error."""
    if not RUNS_PATH.exists():
        return []
    runs = []
    with RUNS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    return runs


def week_start(dt):
    monday = dt - timedelta(days=dt.weekday())
    return monday.date().isoformat()


def build_weekly_series(records):
    today = datetime.now(timezone.utc)
    week_labels = []
    cursor = today - timedelta(weeks=WEEKS_OF_HISTORY - 1)
    for _ in range(WEEKS_OF_HISTORY):
        week_labels.append(week_start(cursor))
        cursor += timedelta(weeks=1)

    counts = {
        "resume_tailor": defaultdict(int),
        "job_scan": defaultdict(int),
        "job_apply_e2e": defaultdict(int),
    }
    for r in records:
        event = r.get("event")
        if event not in counts:
            continue
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except (KeyError, ValueError):
            continue
        counts[event][week_start(ts)] += 1

    return {
        "labels": week_labels,
        "resume_tailor": [counts["resume_tailor"].get(w, 0) for w in week_labels],
        "job_scan": [counts["job_scan"].get(w, 0) for w in week_labels],
        "job_apply_e2e": [counts["job_apply_e2e"].get(w, 0) for w in week_labels],
    }


def format_recent(records, limit=25):
    out = []
    for r in reversed(records[-limit:]):
        event = r.get("event", "unknown")
        ts = r.get("timestamp", "")
        if event == "resume_tailor":
            company = r.get("company", "Unknown company")
            role = r.get("role", "")
            title = f"{company} — {role}" if role else company
            detail = r.get("jd_source", "")
            covered = r.get("required_keywords_covered")
            total = r.get("required_keywords_total")
            meta = f"{covered}/{total} required keywords" if covered is not None and total else ""
        elif event == "job_scan":
            board = r.get("board", "scan")
            title = f"{board} scan"
            cats = r.get("categories") or []
            days = r.get("days")
            detail = f"{', '.join(cats)} · {days}d window" if cats else f"{days}d window"
            surfaced = r.get("surfaced")
            dropped = r.get("already_applied_dropped")
            parts = []
            if surfaced is not None:
                parts.append(f"{surfaced} surfaced")
            if dropped:
                parts.append(f"{dropped} already-applied caught")
            meta = " · ".join(parts)
        elif event == "job_apply_e2e":
            company = r.get("company", "Unknown company")
            role = r.get("role", "")
            title = f"{company} — {role}" if role else company
            detail = r.get("ats_platform", "")
            meta = r.get("status", "")
        else:
            title = event
            detail = ""
            meta = ""
        out.append({
            "event": event,
            "timestamp": ts,
            "title": title,
            "detail": detail,
            "meta": meta,
        })
    return out


def build_totals(records):
    totals = defaultdict(int)
    surfaced_total = 0
    already_applied_total = 0
    for r in records:
        event = r.get("event")
        totals[event] += 1
        if event == "job_scan":
            surfaced_total += r.get("surfaced") or 0
            already_applied_total += r.get("already_applied_dropped") or 0
    return {
        "resume_tailor": totals.get("resume_tailor", 0),
        "job_scan": totals.get("job_scan", 0),
        "job_apply_e2e": totals.get("job_apply_e2e", 0),
        "postings_surfaced": surfaced_total,
        "already_applied_caught": already_applied_total,
    }


def _percentile(values, q):
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * q))
    return values[idx]


def build_performance(runs):
    """Latency, token, and cost aggregates for the dashboard's performance
    panels. Returns has_data: false (rather than raising) when runs.jsonl
    doesn't exist yet or nothing in it has token coverage — a fresh clone or
    a repo that hasn't run build_run_metrics.py must still render."""
    costed = [r for r in runs if r.get("cost_usd") is not None]
    timed = [r for r in runs if r.get("duration_s") is not None]
    hit_rates = [r["cache_hit_rate"] for r in runs if r.get("cache_hit_rate") is not None]
    scan_durations = [r["duration_s"] for r in timed if r["skill"] == "job-scan"]
    tailor_runs = [r for r in costed if r["skill"] == "tailor-resume"]

    if not timed:
        return {"has_data": False}

    cost_to_date = sum(r["cost_usd"] for r in costed)
    cost_per_resume = (sum(r["cost_usd"] for r in tailor_runs) / len(tailor_runs)) if tailor_runs else None

    by_skill_latency = defaultdict(list)
    by_skill_tokens = defaultdict(lambda: defaultdict(int))
    for r in timed:
        by_skill_latency[r["skill"]].append(r["duration_s"])
    for r in runs:
        tokens = r.get("tokens")
        if not tokens:
            continue
        bucket = by_skill_tokens[r["skill"]]
        for key in ("input", "output", "cache_write", "cache_read"):
            bucket[key] += tokens.get(key, 0)

    latency = [
        {
            "skill": skill,
            "runs": len(durations),
            "p50_s": round(_percentile(durations, 0.5), 1),
            "p95_s": round(_percentile(durations, 0.95), 1),
        }
        for skill, durations in sorted(by_skill_latency.items(), key=lambda kv: -len(kv[1]))
    ]

    # job-scan runs also carry a scan_type ("new-grad:swe+dsa") — break those
    # out separately so "SWE new-grad scans" and "quant internship scans"
    # don't get averaged together into one job-scan number.
    by_scan_type = defaultdict(list)
    for r in timed:
        if r.get("scan_type"):
            by_scan_type[r["scan_type"]].append(r["duration_s"])
    scan_breakdown = [
        {
            "scan_type": scan_type,
            "runs": len(durations),
            "p50_s": round(_percentile(durations, 0.5), 1),
            "p95_s": round(_percentile(durations, 0.95), 1),
        }
        for scan_type, durations in sorted(by_scan_type.items(), key=lambda kv: -len(kv[1]))
    ]

    token_mix = [
        {"skill": skill, **counts}
        for skill, counts in sorted(by_skill_tokens.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    return {
        "has_data": True,
        "cost_to_date_usd": round(cost_to_date, 2),
        "cost_per_resume_usd": round(cost_per_resume, 2) if cost_per_resume is not None else None,
        "cache_hit_rate": round(sum(hit_rates) / len(hit_rates), 3) if hit_rates else None,
        "median_scan_latency_s": round(_percentile(scan_durations, 0.5), 1) if scan_durations else None,
        "latency": latency,
        "token_mix": token_mix,
        "scan_breakdown": scan_breakdown,
        "covered_runs": len([r for r in runs if r.get("tokens") is not None]),
        "total_runs": len(runs),
    }


def main():
    records = load_records()
    runs = load_runs()
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "record_count": len(records),
        "totals": build_totals(records),
        "weekly": build_weekly_series(records),
        "recent": format_recent(records),
        "performance": build_performance(runs),
    }

    if not TEMPLATE_PATH.exists():
        raise SystemExit(f"Missing template: {TEMPLATE_PATH}")

    template = TEMPLATE_PATH.read_text()
    placeholder = "%%METRICS_DATA%%"
    if placeholder not in template:
        raise SystemExit(f"Template missing {placeholder} marker")

    final_html = template.replace(placeholder, json.dumps(data))
    OUTPUT_PATH.write_text(final_html)
    print(f"Wrote {OUTPUT_PATH} ({len(records)} record(s) aggregated)")


if __name__ == "__main__":
    main()
