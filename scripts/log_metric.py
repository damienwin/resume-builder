#!/usr/bin/env python3
"""Append a single event to knowledge/metrics.jsonl for later reference.

Usage:
    python3 scripts/log_metric.py <event_type> '<json object of fields>'

Example:
    python3 scripts/log_metric.py job_scan '{"board": "new-grad", "surfaced": 20}'
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event_type,
        **fields,
    }

    repo_root = Path(__file__).resolve().parent.parent
    metrics_path = repo_root / "knowledge" / "metrics.jsonl"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    with metrics_path.open("a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"Logged {event_type} event to {metrics_path}")

if __name__ == "__main__":
    main()
