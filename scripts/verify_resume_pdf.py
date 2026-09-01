#!/usr/bin/env python3
"""Verify a compiled resume PDF actually renders everything its .tex asked for.

## Why this exists

`pdfinfo | grep Pages` reporting "1" does NOT mean the resume is complete.
Observed live on 2026-09-01 (Idler tailoring run): an over-full document
pushed the third project — and part of the second — past the bottom of the
page WITHOUT triggering a page break. The PDF was genuinely one page, every
other check in `tailor-resume` Step 6 passed, and an entire project was
simply absent from the render. It was caught only by hand-diffing the .tex
against `pdftotext` output.

That is the whole point of this script: a page-count check cannot
distinguish "fits on one page" from "silently fell off the page." This
compares what the .tex declares against what the PDF actually renders, and
fails loudly when they disagree.

## What it checks

  1. page_count      — exactly one page
  2. completeness    — every \\resumeSubheading / \\resumeProjectHeading title
                       and every \\resumeItem bullet in the .tex appears in
                       the extracted text (THE check above)
  3. fill            — last text baseline within the target band (default
                       740-755 of 792; below `--min-fill` 720 is a failure)
  4. overfull        — no "Overfull \\hbox" in the tectonic log, if given
  5. placeholders    — no surviving <<PLACEHOLDER>> in the .tex
  6. header          — first extracted lines are non-empty

Exit status is 0 only when every check passes; 1 otherwise. `--json` prints
a machine-readable report. Needs `pdftotext` and `pdfinfo` (poppler).

Usage:
    verify_resume_pdf.py build/<slug>.tex build/<slug>.pdf \\
        [--log build/<slug>.tectonic.log] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

PAGE_HEIGHT = 792.0
FILL_TARGET = (740.0, 755.0)


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def strip_tex(s: str) -> str:
    r"""Reduce a LaTeX fragment to the plain words the PDF will show.

    Drops \href{url}{shown} down to `shown`, unwraps the common formatting
    macros, removes the rest, and normalizes whitespace and dashes so a
    comparison against pdftotext output is about content, not encoding.
    """
    s = re.sub(r"\\href\{[^}]*\}\{((?:[^{}]|\{[^}]*\})*)\}", r"\1", s)
    for macro in ("textbf", "textit", "emph", "underline", "small", "textsc"):
        s = re.sub(r"\\%s\{((?:[^{}]|\{[^}]*\})*)\}" % macro, r"\1", s)
    s = re.sub(r"\$\\vert\$|\$\|\$", "|", s)  # renders as a literal | in the PDF
    # Simple math mode the skill's own ATS rules say IS allowed and extracts
    # flattened: $R^2$ -> R2, $p_{50}$ -> p50 (superscript/subscript glued
    # to the base with no space or caret/underscore surviving).
    s = re.sub(r"\$([A-Za-z0-9]+)[\^_]\{?([A-Za-z0-9]+)\}?\$", r"\1\2", s)
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", s)   # any remaining control sequence
    s = s.replace("~", " ").replace("\\&", "&").replace("\\%", "%")
    s = s.replace("\\$", "$").replace("\\_", "_").replace("\\#", "#")
    s = re.sub(r"[{}]", " ", s)
    return normalize(s)


def normalize(s: str) -> str:
    """Fold the differences pdftotext introduces but a reader would not see."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # every dash/quote variant to ASCII, so en-dashes don't cause false alarms
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"[\u2018\u2019\u02bc]", "'", s)
    s = re.sub(r"[\u201c\u201d]", '"', s)
    s = s.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def declared_content(tex: str) -> tuple[list[str], list[str]]:
    """Titles and bullets the .tex declares, as normalized plain text.

    Titles come from the FIRST argument of each \\resumeSubheading /
    \\resumeProjectHeading (the role or project name). Bullets come from
    \\resumeItem. Both are what must survive into the PDF.
    """
    # Scan only the document body: the preamble DEFINES \resumeItem and
    # friends in terms of #1/#2, and those templates are not content.
    start = tex.find(r"\begin{document}")
    body = tex[start:] if start != -1 else tex
    body = re.sub(r"(?m)(?<!\\)%.*$", "", body)  # strip comments, keep \%

    titles: list[str] = []
    for m in re.finditer(r"\\resume(?:Subheading|ProjectHeading)\s*", body):
        first = _balanced_arg(body, m.end())
        if first:
            t = strip_tex(first)
            if t:
                titles.append(t)

    bullets: list[str] = []
    for m in re.finditer(r"\\resumeItem\s*", body):
        arg = _balanced_arg(body, m.start() + len("\\resumeItem"))
        if arg:
            b = strip_tex(arg)
            if b:
                bullets.append(b)
    return titles, bullets


def _balanced_arg(s: str, i: int) -> str:
    """Read one {...} group starting at or after index i, honoring nesting."""
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i >= len(s) or s[i] != "{":
        return ""
    depth, start = 0, i
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i]
        i += 1
    return ""


def bullet_present(bullet: str, text: str) -> bool:
    """Is this bullet rendered? Tolerant of line-wrap and hyphenation.

    A bullet can legitimately wrap across lines, so an exact substring test
    over normalized text is right for most, but long bullets may also be
    hyphen-split. Fall back to requiring a long, distinctive head of the
    bullet plus its tail, which a truncated/absent bullet cannot satisfy.
    """
    if bullet in text:
        return True
    squashed_bullet = bullet.replace(" ", "").replace("-", "")
    squashed_text = text.replace(" ", "").replace("-", "")
    if squashed_bullet in squashed_text:
        return True
    # partial render (fell off the page mid-bullet) must still fail
    head = squashed_bullet[:60]
    tail = squashed_bullet[-40:]
    return bool(head) and head in squashed_text and tail in squashed_text


def measure_fill(pdf: Path) -> float | None:
    try:
        out = _run(["pdftotext", "-bbox", str(pdf), "-"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    vals = [float(v) for v in re.findall(r'yMax="([0-9.]+)"', out)]
    return max(vals) if vals else None


def page_count(pdf: Path) -> int | None:
    try:
        out = _run(["pdfinfo", str(pdf)])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    m = re.search(r"^Pages:\s*(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def verify(tex_path: Path, pdf_path: Path, log_path: Path | None,
           min_fill: float) -> dict:
    tex = tex_path.read_text(encoding="utf-8", errors="replace")
    raw_text = _run(["pdftotext", str(pdf_path), "-"])
    text = normalize(raw_text)

    checks: list[dict] = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    pages = page_count(pdf_path)
    add("page_count", pages == 1, f"{pages} page(s)" if pages else "unreadable")

    titles, bullets = declared_content(tex)
    missing_titles = [t for t in titles if t not in text]
    missing_bullets = [b for b in bullets if not bullet_present(b, text)]
    missing = missing_titles + missing_bullets
    add(
        "completeness",
        not missing,
        f"{len(titles)} headings + {len(bullets)} bullets declared; "
        + (
            "all rendered"
            if not missing
            else f"{len(missing)} MISSING from the PDF: "
            + "; ".join(repr(m[:70]) for m in missing[:5])
        ),
    )

    fill = measure_fill(pdf_path)
    if fill is None:
        add("fill", False, "could not measure")
    else:
        in_band = FILL_TARGET[0] <= fill <= FILL_TARGET[1]
        add(
            "fill",
            fill >= min_fill,
            f"{fill:.1f}/{PAGE_HEIGHT:.0f}"
            + ("" if in_band else f" (target {FILL_TARGET[0]:.0f}-{FILL_TARGET[1]:.0f})"),
        )

    if log_path and log_path.exists():
        overfull = [
            ln.strip()
            for ln in log_path.read_text(errors="replace").splitlines()
            if "overfull" in ln.lower() and "hbox" in ln.lower()
        ]
        add("overfull", not overfull,
            "none" if not overfull else f"{len(overfull)}: {overfull[0][:90]}")

    placeholders = re.findall(r"<<[A-Z_]+>>", tex)
    add("placeholders", not placeholders,
        "none" if not placeholders else f"{len(placeholders)}: {sorted(set(placeholders))}")

    header = [ln for ln in raw_text.splitlines()[:4] if ln.strip()]
    add("header", len(header) >= 2, f"{len(header)} non-empty lines")

    return {
        "ok": all(c["ok"] for c in checks),
        "tex": str(tex_path),
        "pdf": str(pdf_path),
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tex", type=Path)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--log", type=Path, default=None,
                    help="tectonic log, for the overfull-hbox check")
    ap.add_argument("--min-fill", type=float, default=720.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for tool in ("pdftotext", "pdfinfo"):
        if not shutil.which(tool):
            print(f"error: {tool} not found (brew install poppler)", file=sys.stderr)
            return 2
    for p in (args.tex, args.pdf):
        if not p.exists():
            print(f"error: {p} does not exist", file=sys.stderr)
            return 2

    report = verify(args.tex, args.pdf, args.log, args.min_fill)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']:<14} {c['detail']}")
        print("\n" + ("ALL CHECKS PASSED" if report["ok"] else "FAILED — fix the .tex and recompile"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
