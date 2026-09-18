#!/usr/bin/env python3
"""Finalize a tailored resume in one fail-closed call.

Collapses the end-of-run sequence — final quality gate, archive copy, review
routing record, and the resume_tailor metric — into a single tool call.

The ordering is the safety property, not an implementation detail: nothing is
archived, recorded, or logged unless the final gate passes. The archive folder
doubles as job-scan's already-applied ledger, so an unverified PDF landing
there costs a real posting on the next scan.

Exit status: 0 gate passed and side effects applied; 1 gate failed (no side
effects); 2 usage or missing input.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import run_timer
from log_metric import append_event
from resume_handoff import build_manifest
from resume_quality_gate import quality_gate
from review_routing import append_metric


def _load_json(path: Path | None):
    """Load an optional JSON input, tolerating a missing/unreadable file.

    ``--review-telemetry`` and ``--plan`` are best-effort: a run may have no
    reviewer telemetry to attach, and the route plan is informational here.
    Raising on a missing optional file used to abort the whole finalize (no
    archive, metric, or handoff) and cost the caller a debugging detour.
    """
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def finalize(bullets: Path, case_path: Path, knowledge_root: Path,
             quality_out: Path | None = None, judge: Path | None = None,
             plan: Path | None = None, review_telemetry: Path | None = None,
             repair_count: int = 0, routing_metrics: Path | None = None,
             archive_pdf: Path | None = None, archive_dir: Path | None = None,
             archive_name: str | None = None, archive_overwrite: bool = False,
             metric_event: str | None = None,
             metric_json: str | None = None, metrics_path: Path | None = None,
             handoff_out: Path | None = None, handoff_posting_url: str | None = None,
             handoff_jd_file: Path | None = None, handoff_tex: Path | None = None,
             handoff_reviewer: str | None = None,
             timer_skill: str | None = None, timer_scope: str = "",
             timer_label: str = "final_review") -> dict:
    report = quality_gate(bullets, case_path, knowledge_root, judge)
    if quality_out:
        quality_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if timer_skill:
        run_timer.mark(timer_skill, timer_label, timer_scope)

    result = {"ok": report["ok"], "quality_out": str(quality_out) if quality_out else None,
              "archived": None, "routing_recorded": False, "metric_logged": False,
              "handoff": None, "timer": None}
    if not report["ok"]:
        return result

    if archive_dir and archive_pdf:
        archive_dir = Path(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / (archive_name or archive_pdf.name)
        # The archive is append-only history: one PDF per posting, and the
        # filenames double as job-scan's already-applied ledger. Clobbering
        # one destroys a prior application's resume with no undo (the folder
        # is not under version control), so a collision is a hard error the
        # caller resolves by picking a role-distinguishing --archive-name.
        # --archive-overwrite is only for re-running the *same* posting.
        if dest.exists() and not archive_overwrite:
            raise FileExistsError(
                f"refusing to overwrite existing archived resume: {dest}\n"
                f"a different role already uses this name -- pass a "
                f"role-distinguishing --archive-name (e.g. "
                f"'Acme Battlespace Radar Damien Nguyen.pdf'), or "
                f"--archive-overwrite to replace it deliberately"
            )
        shutil.copy2(archive_pdf, dest)
        result["archived"] = str(dest)

    if plan and routing_metrics:
        plan_data = _load_json(plan)
        if isinstance(plan_data, dict):
            event = dict(plan_data)
            event.update({
                "repair_count": repair_count,
                "review_telemetry": _load_json(review_telemetry),
                "final_gate": report,
            })
            append_metric(Path(routing_metrics), event)
            result["routing_recorded"] = True

    if metric_json and metric_event:
        fields = json.loads(metric_json)
        # Close the timer only now that the gate has passed, and fold its
        # duration/steps into the metric so Step 9's separate finish+log calls
        # collapse into this one. An explicit value in the caller's JSON wins.
        if timer_skill:
            finished = run_timer.finish(timer_skill, timer_scope)
            result["timer"] = finished
            if finished.get("duration_s") is not None:
                fields.setdefault("duration_s", finished["duration_s"])
            if finished.get("steps"):
                fields.setdefault("steps", finished["steps"])
        target = Path(metrics_path) if metrics_path else \
            Path(__file__).resolve().parent.parent / "knowledge" / "metrics.jsonl"
        append_event(target, metric_event, fields)
        result["metric_logged"] = True

    # A verified handoff lets a later `apply` for the same posting skip its
    # own tailoring. Written only after the gate passed, and only when every
    # input the manifest hashes is present.
    if handoff_out and handoff_posting_url and handoff_jd_file and handoff_tex \
            and handoff_reviewer and archive_pdf and quality_out:
        handoff_pdf = Path(result["archived"]) if result["archived"] else archive_pdf
        manifest = build_manifest(handoff_posting_url, handoff_jd_file, handoff_pdf,
                                  handoff_tex, quality_out, handoff_reviewer)
        handoff_out.parent.mkdir(parents=True, exist_ok=True)
        handoff_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        result["handoff"] = str(handoff_out)

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bullets", type=Path)
    ap.add_argument("--case-path", required=True, type=Path)
    ap.add_argument("--knowledge-root", required=True, type=Path)
    ap.add_argument("--quality-out", type=Path)
    ap.add_argument("--judge", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--review-telemetry", type=Path)
    ap.add_argument("--repair-count", type=int, default=0)
    ap.add_argument("--routing-metrics", type=Path)
    ap.add_argument("--archive-pdf", type=Path)
    ap.add_argument("--archive-dir", type=Path)
    ap.add_argument("--archive-name")
    ap.add_argument("--archive-overwrite", action="store_true",
                    help="replace an existing archived PDF of the same name "
                         "(only for re-running the same posting)")
    ap.add_argument("--metric-event")
    ap.add_argument("--metric-json")
    ap.add_argument("--metrics-path", type=Path)
    ap.add_argument("--handoff-out", type=Path)
    ap.add_argument("--handoff-posting-url")
    ap.add_argument("--handoff-jd-file", type=Path)
    ap.add_argument("--handoff-tex", type=Path)
    ap.add_argument("--handoff-reviewer")
    ap.add_argument("--timer-skill")
    ap.add_argument("--timer-scope", default="")
    ap.add_argument("--timer-label", default="final_review")
    args = ap.parse_args()

    if not args.bullets.exists():
        print(f"error: {args.bullets} does not exist", file=sys.stderr)
        return 2
    if not args.case_path.exists() or not args.knowledge_root.is_dir():
        print("error: --case-path must exist and --knowledge-root must be a directory",
              file=sys.stderr)
        return 2
    if args.archive_dir and not args.archive_pdf:
        print("error: --archive-dir requires --archive-pdf", file=sys.stderr)
        return 2
    if args.metric_json:
        try:
            if not isinstance(json.loads(args.metric_json), dict):
                raise ValueError("metric JSON must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"error: --metric-json invalid: {exc}", file=sys.stderr)
            return 2

    try:
        result = finalize(
            args.bullets, args.case_path, args.knowledge_root, args.quality_out,
            args.judge, args.plan, args.review_telemetry, args.repair_count,
            args.routing_metrics, args.archive_pdf, args.archive_dir, args.archive_name,
            args.archive_overwrite, args.metric_event, args.metric_json, args.metrics_path,
            args.handoff_out, args.handoff_posting_url, args.handoff_jd_file,
            args.handoff_tex, args.handoff_reviewer,
            args.timer_skill, args.timer_scope, args.timer_label,
        )
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
