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
  7. date_ranges     — every date range in the extracted text is split by an
                       ASCII hyphen, not an en/em dash (`--` in LaTeX), which
                       Workday-style resume importers fail to parse
  8. clean_extraction — every numeric token in a declared bullet appears in
                       the RAW `-layout` extraction (not the normalize()'d
                       text used elsewhere), since normalize() folds
                       dashes/quotes and would hide exactly the font/kerning
                       garbling this check exists to catch
  9. reading_order   — section headings (Education, Experience, Projects,
                       Technical Skills) appear in that template order in
                       the extracted text
 10. link_visibility — every `\\href{...}{DISPLAY}` display string (project
                       `repo:`/`demo:` links AND the header's mailto/github/
                       linkedin/website links) appears verbatim
                       and unbroken in the raw `-layout` extraction — not
                       hyphen-split across a wrap, not glued to neighboring
                       text
 11. keyword_list    — (only if `--keywords PATH` is passed) the agent's
                       JD-keyword selection file exists and is non-empty.
                       Keyword *selection* stays agent judgment — this only
                       confirms the audit trail exists, it never picks or
                       checks coverage of the keywords itself.

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


def bullet_present(snippet: str, text: str) -> bool:
    """Is this title/bullet rendered? Tolerant of line-wrap and hyphenation.

    Declared text can legitimately wrap across lines, so an exact substring
    test over normalized text is right for most, but long titles/bullets may
    also be hyphen-split. Fall back to requiring a long, distinctive head of
    the snippet plus its tail, which a truncated/absent one cannot satisfy.
    Used for both title and bullet checks — a project heading with a long
    tech-stack list wraps and hyphenates exactly like a bullet does.
    """
    if snippet in text:
        return True
    squashed_snippet = snippet.replace(" ", "").replace("-", "")
    squashed_text = text.replace(" ", "").replace("-", "")
    if squashed_snippet in squashed_text:
        return True
    # partial render (fell off the page mid-snippet) must still fail
    head = squashed_snippet[:60]
    tail = squashed_snippet[-40:]
    return bool(head) and head in squashed_text and tail in squashed_text


_DATE = r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?\d{4}"
_BAD_DATE_RANGE = re.compile(
    _DATE + r"\s*[‐-―−]\s*(?:" + _DATE + r"|Present|Current|Now)\b"
)


def non_ascii_date_ranges(raw_text: str) -> list[str]:
    """Date ranges in the raw extracted text joined by a Unicode dash.

    Must run on raw pdftotext output: normalize() folds dashes to ASCII.
    """
    return [re.sub(r"\s+", " ", m.group(0)) for m in _BAD_DATE_RANGE.finditer(raw_text)]


_SECTION_ORDER = ["Education", "Experience", "Projects", "Technical Skills"]


def reading_order_issues(raw_text: str) -> list[str]:
    """Section headings must appear, in template order, in the extracted text.

    Checked against the plain (no `-layout`) extraction, mirroring the
    manual instruction this replaces ("the no-`-layout` output reads
    header -> education -> experience -> projects -> skills").

    Each heading is matched only on a line consisting of the heading alone
    (surrounding whitespace allowed), so the same word inside an earlier
    bullet ("...side Projects in Rust") can't cause a false fail or hide a
    real reorder. Matching is case-insensitive: older templates set
    headings in capitals ("EDUCATION").
    """
    positions: list[tuple[int, str]] = []
    for name in _SECTION_ORDER:
        m = re.search(r"(?mi)^[ \t]*" + re.escape(name) + r"[ \t]*$", raw_text)
        positions.append((m.start() if m else -1, name))
    missing = [name for idx, name in positions if idx == -1]
    if missing:
        return [f"heading(s) not found in extracted text: {missing}"]
    found_order = [name for _, name in sorted(positions)]
    if found_order != _SECTION_ORDER:
        return [f"out of order: found {found_order}, expected {_SECTION_ORDER}"]
    return []


def _bounded_present(needle: str, haystack: str, is_boundary_char) -> bool:
    """`needle` appears in `haystack` as a contiguous substring, not glued.

    "Glued" means a boundary-class character (e.g. alnum for a URL, digit
    for a number) sits immediately before/after the match — that would mean
    the needle is stuck to unrelated neighboring text rather than standing
    on its own, exactly what "verbatim and unbroken" rules out. A wrap or
    hyphen-split inserted *inside* the needle already fails the plain
    substring test, since it breaks contiguity.
    """
    if not needle:
        return False
    for m in re.finditer(re.escape(needle), haystack):
        i, j = m.start(), m.end()
        left_ok = i == 0 or not is_boundary_char(haystack[i - 1])
        right_ok = j == len(haystack) or not is_boundary_char(haystack[j])
        if left_ok and right_ok:
            return True
    return False


def link_display_texts(tex: str) -> list[str]:
    """The visible display text of every `\\href{url}{DISPLAY}` in the body.

    Covers EVERY href, not only project `repo:`/`demo:` links — the header's
    mailto/github/linkedin/website links are checked too. The display text
    is run through strip_tex() so `\\_`, `\\textbf{...}` etc. reduce to what
    the PDF shows; the extracted side it's compared against stays raw.
    """
    start = tex.find(r"\begin{document}")
    body = tex[start:] if start != -1 else tex
    body = re.sub(r"(?m)(?<!\\)%.*$", "", body)
    out = []
    for m in re.finditer(r"\\href\{[^}]*\}\{((?:[^{}]|\{[^}]*\})*)\}", body):
        display = strip_tex(m.group(1))
        if display:
            out.append(display)
    return out


def link_visibility_issues(tex: str, layout_text: str) -> list[str]:
    """Every href display string (repo:/demo: link text) rendered verbatim."""
    return [
        display
        for display in link_display_texts(tex)
        if not _bounded_present(display, layout_text, str.isalnum)
    ]


_NUM_TOKEN = re.compile(r"\d[\d,.]*(?:%|x)?")


def clean_extraction_issues(bullets: list[str], layout_text: str) -> list[str]:
    """Every numeric token in a declared bullet must survive raw extraction.

    `layout_text` must be the RAW `-layout` pdftotext output, not
    normalize()'d text — normalization folds dashes/quotes and would hide
    exactly the garbling (glued/split digits, dropped chars) this exists to
    catch.
    """
    missing: list[str] = []
    seen: set[str] = set()
    for bullet in bullets:
        for tok in _NUM_TOKEN.findall(bullet):
            if tok in seen:
                continue
            seen.add(tok)
            if not _bounded_present(tok, layout_text, str.isdigit):
                missing.append(tok)
    return missing


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
           min_fill: float, keywords_path: Path | None = None) -> dict:
    tex = tex_path.read_text(encoding="utf-8", errors="replace")
    try:
        raw_text = _run(["pdftotext", str(pdf_path), "-"])
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return {
            "ok": False,
            "tex": str(tex_path),
            "pdf": str(pdf_path),
            "checks": [{"check": "extraction", "ok": False,
                        "detail": f"pdftotext failed: {e}"}],
        }
    try:
        layout_text = _run(["pdftotext", "-layout", str(pdf_path), "-"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        layout_text = ""
    text = normalize(raw_text)

    checks: list[dict] = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    pages = page_count(pdf_path)
    add("page_count", pages == 1, f"{pages} page(s)" if pages else "unreadable")

    titles, bullets = declared_content(tex)
    missing_titles = [t for t in titles if not bullet_present(t, text)]
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

    bad_dates = non_ascii_date_ranges(raw_text)
    add("date_ranges", not bad_dates,
        "all ASCII-hyphen" if not bad_dates
        else f"{len(bad_dates)} use a Unicode dash (write `-`, not `--`): {bad_dates[:3]}")

    missing_tokens = clean_extraction_issues(bullets, layout_text)
    add(
        "clean_extraction",
        not missing_tokens,
        "all numeric tokens present in raw extraction" if not missing_tokens
        else f"{len(missing_tokens)} numeric token(s) missing/garbled in raw "
             f"-layout extraction: {missing_tokens[:5]}",
    )

    order_issues = reading_order_issues(raw_text)
    add(
        "reading_order",
        not order_issues,
        "header -> education -> experience -> projects -> skills" if not order_issues
        else "; ".join(order_issues),
    )

    link_issues = link_visibility_issues(tex, layout_text)
    n_links = len(link_display_texts(tex))
    add(
        "link_visibility",
        not link_issues,
        (f"no links declared" if n_links == 0
         else f"all {n_links} link(s) verbatim and unbroken") if not link_issues
        else f"{len(link_issues)}/{n_links} link(s) broken/glued/garbled: {link_issues}",
    )

    if keywords_path is not None:
        if not keywords_path.exists():
            add(
                "keyword_list",
                False,
                f"missing: {keywords_path} — write the agent's chosen JD-keyword "
                "list here before verifying (keyword selection stays agent "
                "judgment; this only confirms the audit trail exists)",
            )
        else:
            kw_text = keywords_path.read_text(encoding="utf-8", errors="replace")
            keywords = [ln.strip() for ln in kw_text.splitlines() if ln.strip()]
            add(
                "keyword_list",
                bool(keywords),
                f"{len(keywords)} keyword(s) recorded in {keywords_path}" if keywords
                else f"empty: {keywords_path} — the agent must record its chosen "
                     "JD-keyword list, not leave the file blank",
            )

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
    ap.add_argument("--keywords", type=Path, default=None,
                    help="path to the agent-written JD-keyword-selection file; "
                         "if given, checks it exists and is non-empty (does not "
                         "select or check coverage of keywords itself)")
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

    report = verify(args.tex, args.pdf, args.log, args.min_fill, args.keywords)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for c in report["checks"]:
            print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']:<14} {c['detail']}")
        print("\n" + ("ALL CHECKS PASSED" if report["ok"] else "FAILED — fix the .tex and recompile"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
