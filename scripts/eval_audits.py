#!/usr/bin/env python3
"""Deterministic, evidence-grounded audits for staged resume evaluations.

Stage artifacts are JSON. Requirements cite an exact ``jd_quote``; claims and
bullets cite a relative file under knowledge/ and an exact ``source_quote``.
The auditor deliberately rejects unsupported claims rather than attempting to
judge whether a plausible-sounding statement might be true.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def numeric_tokens(value: str) -> set[str]:
    """Return normalized numeric values used by a claim or its evidence."""
    return {token.replace(",", "") for token in re.findall(r"\d+(?:[.,]\d+)*", value)}


def issue(check: str, ok: bool, detail: str, severity: str = "hard") -> dict[str, Any]:
    return {"check": check, "ok": ok, "detail": detail, "severity": severity}


def audit_stage(artifact_path: Path, case_path: Path, knowledge_root: Path) -> dict[str, Any]:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("stage artifact must be a JSON object")
    checks: list[dict[str, Any]] = []
    stage = artifact.get("stage")
    checks.append(issue("stage", isinstance(stage, str) and bool(stage), "stage is required"))
    jd = normalize(case_path.read_text(encoding="utf-8"))

    requirements = artifact.get("requirements", [])
    if not isinstance(requirements, list):
        checks.append(issue("requirements_schema", False, "requirements must be a list"))
        requirements = []
    for index, requirement in enumerate(requirements):
        quote = requirement.get("jd_quote") if isinstance(requirement, dict) else None
        checks.append(issue(
            "jd_grounding",
            isinstance(quote, str) and bool(normalize(quote)) and normalize(quote) in jd,
            f"requirement {index + 1} must quote an exact JD span",
        ))

    claim_items = artifact.get("claims", [])
    bullet_items = artifact.get("bullets", [])
    if not isinstance(claim_items, list):
        checks.append(issue("claims_schema", False, "claims must be a list"))
        claim_items = []
    if not isinstance(bullet_items, list):
        checks.append(issue("bullets_schema", False, "bullets must be a list"))
        bullet_items = []
    if stage == "jd-extraction":
        checks.append(issue("requirement_count", bool(requirements),
                            "jd-extraction must produce at least one requirement"))
    elif stage == "selection":
        checks.append(issue("selection_count", len(claim_items) == 6,
                            "selection must produce exactly six evidence items"))
        for index, claim in enumerate(claim_items):
            requirement_quote = claim.get("requirement_quote") if isinstance(claim, dict) else None
            checks.append(issue(
                "selection_requirement_grounding",
                isinstance(requirement_quote, str)
                and bool(normalize(requirement_quote))
                and normalize(requirement_quote) in jd,
                f"selection {index + 1} must map to an exact JD span",
            ))
    elif stage == "bullet-writing":
        expected_count = artifact.get("expected_bullet_count")
        has_expected_count = isinstance(expected_count, int) and not isinstance(expected_count, bool)
        count_ok = (len(bullet_items) == expected_count
                    if has_expected_count else len(bullet_items) >= 3)
        expectation = (f"exactly {expected_count}" if has_expected_count
                       else "at least three")
        checks.append(issue("bullet_count", count_ok,
                            f"bullet-writing must produce {expectation} bullets"))
    elif stage == "review":
        checks.append(issue("review_count", len(claim_items) + len(bullet_items) >= 3,
                            "review must cite at least three final resume bullets"))
    claims = claim_items + bullet_items
    root = knowledge_root.resolve()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            checks.append(issue("claim_schema", False, f"claim {index + 1} must be an object"))
            continue
        text = claim.get("text")
        checks.append(issue("claim_text", isinstance(text, str) and bool(normalize(text)),
                            f"claim {index + 1} must contain non-empty text"))
        rel_path, quote = claim.get("source_path"), claim.get("source_quote")
        source = (root / rel_path).resolve() if isinstance(rel_path, str) else None
        valid_source = bool(source and source.is_file() and (source == root or root in source.parents))
        quote_found = False
        if valid_source and isinstance(quote, str) and normalize(quote):
            quote_found = normalize(quote) in normalize(source.read_text(encoding="utf-8", errors="replace"))
        checks.append(issue(
            "claim_grounding", valid_source and quote_found,
            f"claim {index + 1} must cite an exact quote in knowledge/",
        ))
        if isinstance(text, str) and text:
            text_numbers = numeric_tokens(text)
            quote_numbers = numeric_tokens(quote) if isinstance(quote, str) else set()
            missing_numbers = sorted(text_numbers - quote_numbers)
            checks.append(issue(
                "numeric_grounding",
                not missing_numbers,
                (f"claim {index + 1} numeric values must occur in its source_quote"
                 + (f"; missing: {', '.join(missing_numbers)}" if missing_numbers else "")),
            ))
            checks.append(issue("bullet_length", len(text) <= 165,
                                f"claim {index + 1} is {len(text)} characters", "warning"))

    unsupported = artifact.get("unsupported_requirements", [])
    checks.append(issue("gaps_schema", isinstance(unsupported, list),
                        "unsupported_requirements must be a list"))
    return {"stage": stage, "artifact": str(artifact_path), "ok": all(
        check["ok"] or check["severity"] != "hard" for check in checks
    ), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--case-path", required=True, type=Path)
    parser.add_argument("--knowledge-root", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = audit_stage(args.artifact, args.case_path, args.knowledge_root)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
