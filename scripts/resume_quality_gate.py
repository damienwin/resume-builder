#!/usr/bin/env python3
"""Combine deterministic evidence audits with an adversarial LLM verdict."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Optional

from eval_audits import audit_stage, issue


DIMENSIONS = ("scanability", "clarity", "specificity", "naturalness", "confidence")


def quality_gate(artifact_path: Path, case_path: Path, knowledge_root: Path,
                 judge_path: Optional[Path] = None, minimum_mean: float = 4.0,
                 minimum_dimension: int = 3, minimum_confidence: int = 4) -> dict[str, Any]:
    deterministic = audit_stage(artifact_path, case_path, knowledge_root)
    checks = [issue("deterministic_audit", deterministic["ok"],
                    "all deterministic evidence and schema checks must pass")]
    judge = None
    if judge_path is not None:
        judge = json.loads(judge_path.read_text(encoding="utf-8"))
        ratings = judge.get("ratings") if isinstance(judge, dict) else None
        factuality = judge.get("factuality_pass") if isinstance(judge, dict) else None
        ratings_ok = (isinstance(ratings, dict)
                      and set(ratings) == set(DIMENSIONS)
                      and all(
            isinstance(ratings.get(name), int)
            and not isinstance(ratings.get(name), bool)
            and 1 <= ratings[name] <= 5
            for name in DIMENSIONS
        ))
        checks.append(issue("judge_schema", ratings_ok and isinstance(factuality, bool),
                            "judge must provide every 1-5 rating and factuality_pass"))
        checks.append(issue("semantic_factuality", factuality is True,
                            "adversarial judge must pass semantic factuality"))
        if ratings_ok:
            values = [ratings[name] for name in DIMENSIONS]
            mean = statistics.mean(values)
            checks.append(issue("readability_mean", mean >= minimum_mean,
                                f"judge mean {mean:.2f} must be >= {minimum_mean:.2f}"))
            checks.append(issue("readability_floor",
                                min(values) >= minimum_dimension,
                                f"every judge dimension must be >= {minimum_dimension}"))
            checks.append(issue("confidence", ratings["confidence"] >= minimum_confidence,
                                f"judge confidence must be >= {minimum_confidence}"))
    return {
        "artifact": str(artifact_path),
        "judge": str(judge_path) if judge_path else None,
        "ok": all(check["ok"] or check["severity"] != "hard" for check in checks),
        "checks": checks,
        "deterministic": deterministic,
        "judge_verdict": judge,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--case-path", required=True, type=Path)
    parser.add_argument("--knowledge-root", required=True, type=Path)
    parser.add_argument("--judge", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--minimum-mean", type=float, default=4.0)
    parser.add_argument("--minimum-dimension", type=int, default=3)
    parser.add_argument("--minimum-confidence", type=int, default=4)
    args = parser.parse_args()
    report = quality_gate(args.artifact, args.case_path, args.knowledge_root,
                          args.judge, args.minimum_mean, args.minimum_dimension,
                          args.minimum_confidence)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
