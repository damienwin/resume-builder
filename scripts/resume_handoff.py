#!/usr/bin/env python3
"""Record and verify a tailored-resume handoff between tailor and apply.

Why this exists: job-scan's fan-out runs `tailor-resume` for a posting and then
runs `apply`, whose first step also runs `tailor-resume`. Executed literally
that tailors the same posting twice, wasting a full tailor run. The old skip
condition was "only if the user explicitly confirms," which cannot be checked
by an agent and so was never used.

A handoff manifest makes the skip machine-verifiable: it binds the posting URL,
the JD content, the compiled PDF, the .tex, and the passing final gate by
sha256. `apply` skips its own tailoring only when every hash still matches.
The hashes are the point — a stale or mismatched manifest is rejected, so a
wrong resume can never be attached to an application.

Usage:
    resume_handoff.py write --out build/<slug>.handoff.json \\
        --posting-url <url> --jd-file <jd.txt> --pdf build/<slug>.pdf \\
        --tex build/<slug>.tex --gate build/<slug>.final-quality.json \\
        --reviewer <provider/model>
    resume_handoff.py verify --manifest build/<slug>.handoff.json \\
        [--posting-url <url>] [--pdf <path>]

Exit status: write 0 on success; verify 0 when valid, 1 when not, 2 on usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(posting_url: str, jd_file: Path, pdf: Path, tex: Path,
                   gate: Path, reviewer: str) -> dict:
    try:
        gate_report = json.loads(Path(gate).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        gate_report = {}
    return {
        "posting_url": posting_url,
        "jd_sha256": sha256_file(jd_file),
        "pdf": str(Path(pdf).resolve()),
        "pdf_sha256": sha256_file(pdf),
        "tex": str(Path(tex).resolve()),
        "tex_sha256": sha256_file(tex),
        "gate": str(Path(gate).resolve()),
        "gate_sha256": sha256_file(gate),
        "gate_ok": bool(gate_report.get("ok")),
        "reviewer": reviewer,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def verify_manifest(manifest_path: Path, posting_url: str | None = None,
                    pdf: Path | None = None) -> dict:
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "reasons": [f"manifest unreadable: {exc}"]}
    if not isinstance(manifest, dict):
        return {"valid": False, "reasons": ["manifest is not a JSON object"]}

    reasons: list[str] = []
    if not manifest.get("gate_ok"):
        reasons.append("final gate did not pass")
    if posting_url is not None and manifest.get("posting_url") != posting_url:
        reasons.append("posting URL mismatch")
    for key, path_key in (("pdf_sha256", "pdf"), ("tex_sha256", "tex"),
                          ("gate_sha256", "gate")):
        expected = manifest.get(key)
        target = manifest.get(path_key)
        if not expected or not target:
            reasons.append(f"missing {path_key}")
            continue
        path = Path(target)
        if not path.exists():
            reasons.append(f"{path_key} not found: {target}")
        elif sha256_file(path) != expected:
            reasons.append(f"{path_key} hash mismatch (changed since tailoring)")
    if pdf is not None:
        if not Path(pdf).exists():
            reasons.append(f"pdf not found: {pdf}")
        elif sha256_file(pdf) != manifest.get("pdf_sha256"):
            reasons.append("supplied pdf does not match the tailored PDF")

    return {"valid": not reasons, "reasons": reasons, "manifest": manifest}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    write = sub.add_parser("write")
    write.add_argument("--out", required=True, type=Path)
    write.add_argument("--posting-url", required=True)
    write.add_argument("--jd-file", required=True, type=Path)
    write.add_argument("--pdf", required=True, type=Path)
    write.add_argument("--tex", required=True, type=Path)
    write.add_argument("--gate", required=True, type=Path)
    write.add_argument("--reviewer", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--posting-url")
    verify.add_argument("--pdf", type=Path)

    args = ap.parse_args()
    if args.command == "write":
        for path in (args.jd_file, args.pdf, args.tex, args.gate):
            if not path.exists():
                print(f"error: {path} does not exist", file=sys.stderr)
                return 2
        manifest = build_manifest(args.posting_url, args.jd_file, args.pdf,
                                  args.tex, args.gate, args.reviewer)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2))
        return 0

    result = verify_manifest(args.manifest, args.posting_url, args.pdf)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
