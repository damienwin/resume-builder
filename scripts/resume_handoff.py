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

JD freshness check: the manifest records both the JD file's resolved path
(`jd_path`) and a *normalized* content hash (`jd_sha256_normalized`) at write
time — not just the raw-byte hash. `verify --jd-file <path>` re-hashes that
path's current content the same normalized way (whitespace collapsed,
case-folded, tracking query params and "posted N days ago" phrases removed
in place, standalone view/applicant counter lines dropped) and compares. If
normalization leaves nothing, or under half the raw text, verify fails closed
rather than trusting a hash of a near-empty string. Raw-byte hashing was rejected on purpose: a fresh page
fetch is rarely byte-stable, so it would force a false mismatch on almost
every standalone `apply` run and silently defeat the whole handoff
optimization. When `--jd-file` is *not* passed, `verify` falls back to
today's posting-URL-only check (fail-open by construction) and logs that the
JD itself was not re-verified — callers should always pass `--jd-file` when a
JD path is available (the manifest's own `jd_path`, or the job-scan fan-out's
cached `<scratchpad>/jds/<name>.txt`) so this fallback isn't silently relied
on forever.

Usage:
    resume_handoff.py write --out build/<slug>.handoff.json \\
        --posting-url <url> --jd-file <jd.txt> --pdf build/<slug>.pdf \\
        --tex build/<slug>.tex --gate build/<slug>.final-quality.json \\
        --reviewer <provider/model>
    resume_handoff.py verify --manifest build/<slug>.handoff.json \\
        [--posting-url <url>] [--pdf <path>] [--jd-file <jd.txt>]

Exit status: write 0 on success; verify 0 when valid, 1 when not, 2 on usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-fetch noise that doesn't reflect a change to the JD's substance. Only the
# matched *token or phrase* is removed — never the surrounding line — because
# a JD fetched as one long line (`fetch_urls.py --strip-tags` output often is)
# would otherwise normalize to nothing and hash-match every later version.
_TRACKING_PARAM = re.compile(
    r'([?&])(utm_[a-z0-9_]+|trk|ref|gh_src|gh_jid|source)=[^\s&#]*', re.I)
_POSTED_AGO = re.compile(
    r'\bposted\s+\d+\+?\s*(minutes?|hours?|days?|weeks?|months?)\s+ago\b', re.I)
# Whole-line-only patterns: anchored so they drop a short standalone counter
# line ("Applicants: 214") but never a substantive line that merely starts
# with the same word ("Applicants: 5+ years of experience required").
# Deliberately absent: "apply before <date>" — a deadline change is real.
_VOLATILE_WHOLE_LINES = [
    re.compile(r'^\d+\+?\s*(minutes?|hours?|days?|weeks?|months?)\s+ago$', re.I),
    re.compile(r'^(views?|applicants?)\s*:?\s*\d[\d,]*\+?$', re.I),
    re.compile(r'^\d[\d,]*\+?\s+(views?|applicants?)$', re.I),
]

# Fail-closed threshold: if normalization removed more than half of the raw
# text's non-whitespace characters, the normalized form no longer represents
# the JD, so its hash can't vouch for freshness. Legit noise (a posted-ago
# phrase, tracking params, a counter line) is a few dozen chars against a JD
# that runs to hundreds or thousands, so it never gets near 50%; losing more
# than half means the normalizer is eating substance.
MIN_KEPT_RATIO = 0.5


def _clean_query_separators(line: str) -> str:
    # After removing tracking params, fix "?&x=1" -> "?x=1" and drop a
    # dangling "?" / "&" left at the end of a URL.
    line = re.sub(r'\?&+', '?', line)
    line = re.sub(r'&{2,}', '&', line)
    return re.sub(r'[?&](?=[\s)\].,;]|$)', '', line)


def normalize_jd_text(text: str) -> str:
    """Whitespace-insensitive, case-insensitive, re-fetch-noise-stripped form.

    Lenient only toward specific noise (spacing, case, "posted N days ago",
    tracking query params, standalone view/applicant counters). Every other
    word must still match, so a genuine requirements change is caught.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in normalized.split("\n"):
        line = _TRACKING_PARAM.sub(r'\1', raw_line)
        line = _clean_query_separators(line)
        line = _POSTED_AGO.sub(' ', line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if any(p.match(line) for p in _VOLATILE_WHOLE_LINES):
            continue
        lines.append(line.casefold())
    return "\n".join(lines)


def normalization_degenerate(text: str) -> bool:
    """True when the normalized form is empty or kept < MIN_KEPT_RATIO of the
    raw non-whitespace characters — i.e. its hash can't be trusted."""
    raw_chars = len(re.sub(r"\s+", "", text))
    kept_chars = len(re.sub(r"\s+", "", normalize_jd_text(text)))
    if kept_chars == 0:
        return True
    return raw_chars > 0 and kept_chars / raw_chars < MIN_KEPT_RATIO


def normalized_sha256_text(text: str) -> str:
    return hashlib.sha256(normalize_jd_text(text).encode("utf-8")).hexdigest()


def normalized_sha256_file(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return normalized_sha256_text(text)


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
        "jd_path": str(Path(jd_file).resolve()),
        "jd_sha256": sha256_file(jd_file),
        "jd_sha256_normalized": normalized_sha256_file(jd_file),
        "jd_normalization_degenerate": normalization_degenerate(
            Path(jd_file).read_text(encoding="utf-8", errors="replace")),
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
                    pdf: Path | None = None, jd_file: Path | None = None) -> dict:
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "reasons": [f"manifest unreadable: {exc}"]}
    if not isinstance(manifest, dict):
        return {"valid": False, "reasons": ["manifest is not a JSON object"]}

    reasons: list[str] = []
    warnings: list[str] = []
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

    # JD freshness. Fail-closed direction: a real content change must always
    # force re-tailor, so an inability to verify (missing --jd-file, missing
    # manifest fields) never silently upgrades to "valid" on its own — it
    # only widens the fallback surface to today's posting-URL-only check,
    # and that fallback is always logged so it can't go unnoticed.
    if jd_file is not None:
        if not Path(jd_file).exists():
            reasons.append(f"jd file not found: {jd_file}")
        else:
            expected_norm = manifest.get("jd_sha256_normalized")
            if not expected_norm:
                msg = ("manifest has no jd_sha256_normalized (written before "
                       "the jd-hash-stale-check fix, or by an older caller); "
                       "falling back to posting-URL-only check — JD content "
                       "was not re-verified")
                warnings.append(msg)
                print(f"resume_handoff verify: {msg}", file=sys.stderr)
            else:
                current = Path(jd_file).read_text(encoding="utf-8", errors="replace")
                if manifest.get("jd_normalization_degenerate") \
                        or normalization_degenerate(current):
                    # Fail closed: a near-empty normalized form would
                    # hash-match unrelated JDs, so it can't prove freshness.
                    reasons.append(
                        "JD normalization degenerate (normalized text empty or "
                        f"< {int(MIN_KEPT_RATIO * 100)}% of raw); cannot verify "
                        "JD freshness — re-tailor")
                elif normalized_sha256_text(current) != expected_norm:
                    reasons.append("JD content changed since tailoring "
                                   "(normalized hash mismatch)")
    else:
        msg = ("--jd-file not passed; falling back to posting-URL-only "
               "check — JD content was not re-verified")
        warnings.append(msg)
        print(f"resume_handoff verify: {msg}", file=sys.stderr)

    result = {"valid": not reasons, "reasons": reasons, "manifest": manifest}
    if warnings:
        result["warnings"] = warnings
    return result


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
    verify.add_argument("--jd-file", type=Path,
                        help="re-hash this JD file (normalized) and compare "
                             "against the manifest's jd_sha256_normalized; "
                             "omit to fall back to the posting-URL-only "
                             "check (logged)")

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

    result = verify_manifest(args.manifest, args.posting_url, args.pdf, args.jd_file)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
