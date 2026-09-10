#!/usr/bin/env python3
"""Append a single event to knowledge/metrics.jsonl for later reference.

Usage:
    python3 scripts/log_metric.py <event_type> '<json object of fields>'

Example:
    python3 scripts/log_metric.py job_scan '{"board": "new-grad", "surfaced": 20}'

Set RESUME_BUILDER_VARIANT in the environment to tag every record written in
that shell with a "variant" field, for A/B comparisons (see
scripts/metrics_summary.py --ab). An explicit "variant" key in the fields
JSON always wins over the environment variable.
"""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

def append_event(metrics_path, event_type, fields, variant=None):
    """Build the record and append it under an exclusive lock.

    Reused by the phase wrappers so they can collapse "log the metric" into the
    same tool call as the gate/archive without duplicating the locked-write
    logic. ``variant`` is ignored when the caller supplies one in ``fields``.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event_type,
    }
    env_variant = variant if variant is not None else os.environ.get("RESUME_BUILDER_VARIANT")
    if env_variant:
        record["variant"] = env_variant
    record.update(fields)  # an explicit "variant" field in fields wins

    metrics_path = Path(metrics_path)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    # Parallel forks (job-scan's fan-out) log to the same file concurrently.
    # An unlocked append can interleave partial lines; lock + fsync, matching
    # review_routing.append_metric, so a reader never sees a torn record.
    with metrics_path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return record


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    event_type, fields_json = sys.argv[1], sys.argv[2]
    try:
        fields = json.loads(fields_json)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON for fields: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(fields, dict):
        print("Fields must be a JSON object", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    metrics_path = repo_root / "knowledge" / "metrics.jsonl"
    append_event(metrics_path, event_type, fields)
    print(f"Logged {event_type} event to {metrics_path}")

if __name__ == "__main__":
    main()
