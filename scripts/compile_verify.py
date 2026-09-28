#!/usr/bin/env python3
"""Compile a resume .tex and verify the PDF in one fail-closed call.

Replaces the `tectonic ... | tee ... | tail` + verify_resume_pdf.py pair (two
tool calls, and a shell pipeline whose exit status is tail's, not tectonic's)
with one subprocess that:

  * runs tectonic with no shell pipe, so its real return code is authoritative;
  * writes the combined stdout/stderr to the tectonic log the verifier reads;
  * runs the same verify_resume_pdf.verify() checks;
  * emits one combined JSON report and exits nonzero if either step failed.

Nothing about the checks changes — this only removes turns and the
pipe-masks-tectonic-failure hazard.

Exit status: 0 all checks passed; 1 compile or verification failed; 2 usage.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from verify_resume_pdf import verify


def failing_checks(report: dict) -> list[str]:
    """Names of the checks that failed on this one compile attempt.

    Includes "compile" itself (tectonic non-zero exit / timeout / missing
    binary) ahead of any verify-stage check names, so a compile-error attempt
    still logs something instead of an empty list.
    """
    names: list[str] = []
    compile_info = report.get("compile")
    if compile_info is not None and not compile_info.get("ok"):
        names.append("compile")
    for check in report.get("checks", []):
        if not check.get("ok") and check.get("check") not in names:
            names.append(check["check"])
    return names


def record_repair_log(log_path: Path, report: dict) -> dict:
    """Append this attempt's failing-check names to `log_path`, accumulating.

    `build/$SLUG.verify.json` (the `--json-out` file) is overwritten on every
    recompile within a run, so it can never answer "which checks have failed
    across this run's repair loop" — only "which failed on the LAST attempt".
    This mirrors run_timer.py's fix for the same shape of bug (a later event
    silently clobbering an earlier one instead of accumulating): read the
    existing attempts list, append one record for THIS attempt, write the
    whole list back. A missing or corrupt file starts a fresh list rather
    than raising — logging must never fail the compile/verify call itself.
    """
    try:
        attempts = json.loads(log_path.read_text(encoding="utf-8"))
        if not isinstance(attempts, list):
            attempts = []
    except (OSError, json.JSONDecodeError):
        attempts = []

    names = failing_checks(report)
    attempts.append({
        "attempt": len(attempts) + 1,
        "ok": bool(report.get("ok")),
        "failing_checks": names,
    })
    log_path.write_text(json.dumps(attempts, indent=2) + "\n", encoding="utf-8")
    return {"attempts": attempts}


def compile_and_verify(tex: Path, pdf: Path, log: Path, min_fill: float = 720.0,
                       tectonic: str = "tectonic", timeout: float = 180.0) -> dict:
    if shutil.which(tectonic) is None:
        return {"ok": False, "error": f"{tectonic} not found on PATH",
                "checks": [{"check": "tectonic", "ok": False,
                            "detail": f"{tectonic} not found on PATH"}]}

    try:
        proc = subprocess.run([tectonic, str(tex)], capture_output=True,
                              text=True, timeout=timeout)
        combined = proc.stdout + proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        combined = f"tectonic timed out after {timeout}s\n"
        returncode = -1
    except OSError as exc:  # e.g. permission denied
        return {"ok": False, "error": str(exc),
                "checks": [{"check": "tectonic", "ok": False, "detail": str(exc)}]}

    log.write_text(combined, encoding="utf-8")
    compile_ok = returncode == 0

    if pdf.exists():
        report = verify(tex, pdf, log, min_fill)
    else:
        report = {"ok": False, "tex": str(tex), "pdf": str(pdf), "checks": [
            {"check": "extraction", "ok": False,
             "detail": "compile failed; no PDF produced"}]}

    return {
        "ok": bool(compile_ok and report.get("ok")),
        "tex": str(tex),
        "pdf": str(pdf),
        "compile": {"ok": compile_ok, "returncode": returncode, "log": str(log)},
        "verify": report,
        "checks": report.get("checks", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tex", type=Path)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--min-fill", type=float, default=720.0)
    ap.add_argument("--tectonic", default="tectonic")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--repair-log", type=Path,
                    help="Path to accumulate this run's per-attempt failing "
                         "check names into (appends; survives recompiles). "
                         "Typically build/$SLUG.repair_log.json.")
    ap.add_argument("--timer-skill")
    ap.add_argument("--timer-scope", default="")
    ap.add_argument("--timer-label", default="compile_verify")
    args = ap.parse_args()

    if not args.tex.exists():
        print(f"error: {args.tex} does not exist", file=sys.stderr)
        return 2
    log = args.log or args.tex.with_suffix(".tectonic.log")

    report = compile_and_verify(args.tex, args.pdf, log, args.min_fill,
                                args.tectonic, args.timeout)

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.repair_log:
        record_repair_log(args.repair_log, report)
    if args.timer_skill:
        import run_timer
        run_timer.mark(args.timer_skill, args.timer_label, args.timer_scope)

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
