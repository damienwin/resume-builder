#!/usr/bin/env python3
"""A/B harness for scripts/extract_comp.py.

Compares the script's extracted comp against an independent, deliberately
simple baseline (a loose regex over every dollar amount) on a directory of
cached JD `.txt` files, and reports:

  * agreement rate (stated-vs-none, and low/high base when both stated),
  * per-file script time,
  * disagreements for manual review,
  * the historical `compare_offer` step durations from knowledge/metrics.jsonl
    (the "before" timing) and a projected "after" (script time + levels.fyi
    batch estimate).

Usage:
  python3 scripts/ab_extract_comp.py <jd_dir> [--metrics knowledge/metrics.jsonl]
      [--fyi-batch-s 15] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "extract_comp.py"

_AMT = r"\$\s?(\d{1,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]|\d{5,7})"


def _to_num(tok: str) -> float:
    t = tok.replace(",", "").replace(" ", "")
    if t[-1] in "kK":
        return float(t[:-1]) * 1000
    return float(t)


def baseline(text: str) -> dict:
    """Independent baseline: any 'low - high' dollar pair of annual scale."""
    pair = re.compile(_AMT + r"\s*(?:-|–|—|to|and)\s*" + _AMT.replace(r"\$\s?", r"\$?\s?"))
    for m in pair.finditer(text):
        lo, hi = _to_num(m.group(1)), _to_num(m.group(2))
        if 20_000 <= lo <= hi <= 2_000_000:
            return {"status": "stated", "low": lo, "high": hi}
    return {"status": "none", "low": None, "high": None}


def run_script(paths: list[Path]) -> tuple[dict, float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths), "--json"],
        capture_output=True, text=True,
    )
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        sys.exit(f"extract_comp.py failed: {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout), elapsed


def script_view(r: dict) -> dict:
    base = r.get("base") or {}
    return {"status": r.get("status", "none"), "low": base.get("low"), "high": base.get("high")}


def agree(a: dict, b: dict) -> bool:
    if a["status"] != b["status"]:
        return False
    if a["status"] == "none":
        return True
    if a["low"] is None or b["low"] is None:
        return True
    return abs(a["low"] - b["low"]) < 1 and abs((a["high"] or 0) - (b["high"] or 0)) < 1


def history(metrics: Path) -> list[float]:
    out: list[float] = []
    if not metrics.exists():
        return out
    for line in metrics.read_text().splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("event") != "job_scan":
            continue
        v = (d.get("steps") or {}).get("compare_offer")
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def p90(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.9 * len(s)))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jd_dir")
    ap.add_argument("--metrics", default=str(ROOT / "knowledge" / "metrics.jsonl"))
    ap.add_argument("--fyi-batch-s", type=float, default=15.0,
                    help="estimated wall time of the one parallel levels.fyi batch")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(Path(args.jd_dir).glob("*.txt"))
    if args.limit:
        files = files[: args.limit]
    if not files:
        sys.exit(f"no .txt files in {args.jd_dir}")

    data, total_s = run_script(files)
    by_path = {Path(r["jd_path"]).resolve(): r for r in data["results"]}

    agreed = 0
    stated_script = stated_base = 0
    disagreements = []
    per_file = []
    for f in files:
        r = by_path.get(f.resolve())
        if r is None:
            continue
        s, b = script_view(r), baseline(f.read_text(errors="replace"))
        stated_script += s["status"] == "stated"
        stated_base += b["status"] == "stated"
        if agree(s, b):
            agreed += 1
        else:
            disagreements.append((f.name, s, b, r.get("comp_text")))
        # per-file time: re-run alone for the first 5 only (cheap, indicative)
    for f in files[:5]:
        _, t = run_script([f])
        per_file.append((f.name, t))

    n = len(files)
    print(f"files: {n}   batch script time: {total_s:.2f}s ({total_s / n * 1000:.0f} ms/file)")
    print(f"stated: script={stated_script} baseline={stated_base}")
    print(f"agreement: {agreed}/{n} = {agreed / n:.1%}")
    print("single-file timings (incl. interpreter startup):")
    for name, t in per_file:
        print(f"  {name}: {t * 1000:.0f} ms")

    if disagreements:
        print(f"\ndisagreements for manual review ({len(disagreements)}):")
        for name, s, b, text in disagreements:
            print(f"  {name}\n    script  : {s}  text={text!r}\n    baseline: {b}")

    hist = history(Path(args.metrics))
    if hist:
        print(f"\nbefore (compare_offer step, n={len(hist)}): "
              f"mean={statistics.mean(hist):.1f}s median={statistics.median(hist):.1f}s "
              f"p90={p90(hist):.1f}s")
        after = total_s + args.fyi_batch_s
        print(f"projected after: {total_s:.2f}s script + {args.fyi_batch_s:.0f}s levels.fyi batch "
              f"= {after:.1f}s  (vs mean {statistics.mean(hist):.1f}s → "
              f"{statistics.mean(hist) / after:.1f}x)")
    else:
        print("\nno historical compare_offer timings found in metrics")


if __name__ == "__main__":
    main()
