#!/usr/bin/env python3
"""One fail-closed call for a resume stage gate: audit -> route -> timer mark.

The selection and bullet-writing stages each currently take three separate
tool calls (resume_quality_gate.py, review_routing.py plan, run_timer.py mark).
Each of those is a full model turn. This wrapper does all three in one
subprocess, which is the latency win — not a change in what is checked.

It is deliberately one wrapper *per stage*, not a single all-phases runner:
the bullet artifact does not exist until selection has passed, and the final
gate needs a completed judge verdict. Collapsing those boundaries would let a
later phase's inputs be missing while an earlier one reports success.

Fail-closed behavior:
  - a deterministic hard failure writes the audit and exits 1 WITHOUT running
    the router, so no paid review is ever authorized by a failed gate;
  - the timer mark is still written on failure (it only records elapsed time,
    never a pass), so a failed gate's duration is measurable.

Exit status:
  0  deterministic gate passed and a route plan was written
  1  deterministic gate failed (repair the artifact, then rerun)
  2  usage or missing input
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import run_timer
from resume_quality_gate import quality_gate
from review_routing import detect_capabilities, load_capabilities, review_plan


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def gate_stage(artifact: Path, case_path: Path, knowledge_root: Path, stage: str,
               audit_out: Path | None = None, route_out: Path | None = None,
               mode: str = "auto", capabilities_path: Path | None = None,
               timer_skill: str | None = None, timer_scope: str = "",
               timer_label: str | None = None) -> dict:
    """Run the deterministic gate, write its audit, and (only if it passes)
    produce a review-route plan. Returns a small result dict; the caller maps
    it to an exit code."""
    report = quality_gate(artifact, case_path, knowledge_root)
    if audit_out:
        audit_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if timer_skill and timer_label:
        run_timer.mark(timer_skill, timer_label, timer_scope)

    result = {"stage": stage, "artifact": str(artifact), "ok": report["ok"],
              "audit_out": str(audit_out) if audit_out else None,
              "route_out": None, "review_required": None}

    if not report["ok"]:
        result["skip_reason"] = "deterministic_hard_failure"
        return result

    capabilities = detect_capabilities(load_capabilities(capabilities_path))
    plan = review_plan(mode, stage, capabilities,
                       audit=report, artifact=_read_json(artifact))
    if route_out:
        route_out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    result["route_out"] = str(route_out) if route_out else None
    result["review_required"] = plan.get("review_required")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("artifact", type=Path)
    ap.add_argument("--case-path", required=True, type=Path)
    ap.add_argument("--knowledge-root", required=True, type=Path)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--audit-out", type=Path)
    ap.add_argument("--route-out", type=Path)
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--capabilities", type=Path)
    ap.add_argument("--timer-skill")
    ap.add_argument("--timer-scope", default="")
    ap.add_argument("--timer-label")
    args = ap.parse_args()

    for path in (args.artifact, args.case_path):
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2
    if not args.knowledge_root.is_dir():
        print(f"error: {args.knowledge_root} is not a directory", file=sys.stderr)
        return 2

    result = gate_stage(
        args.artifact, args.case_path, args.knowledge_root, args.stage,
        args.audit_out, args.route_out, args.mode, args.capabilities,
        args.timer_skill, args.timer_scope, args.timer_label,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
