#!/usr/bin/env python3
"""Deterministic levels.fyi comp estimates for job-scan Step 3.

Replaces the model-driven "fetch levels.fyi and read the figure" fallback with
a script: one parallel batch of page fetches, a regex over each page's meta
description, and a slug-fallback pass for 404s. Used only for postings whose
JD and board row state no pay (see extract_comp.py), so every figure it emits
is an *estimate* and is rendered `~... (est.)`.

Figures stay in the currency levels.fyi shows (CA$, EUR, CZK, ...) and non-US
pages are labelled with their country. A company with no levels.fyi page, or a
page without a figure, yields `est_text: null` (render `-`); nothing is ever
invented.

Meta-description shapes handled:
  "...compensation in <Country> at <Co> ranges from X per year for L4 to Y per year for L10."
        -> entry-level figure X
  "...total compensation in <Country> at <Co> ranges from X to Y per year."
        -> range X-Y
  "...package at <Co> in <Country> totals X per year."  -> X

CLI:
    levels_fyi_est.py --companies "Viant" "Thales" [--companies-file f.txt] --json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

import fetch_urls

BASE = "https://www.levels.fyi/companies/{slug}/salaries/software-engineer"
MAX_ROUNDS = 3

MONEY = r"(?:[A-Z]{1,3}\$|[$€£¥₹₩]|[A-Z]{3}\s?)\d[\d.,]*\s?[KMkm]?"
LEVEL_RANGE_RE = re.compile(rf"ranges from ({MONEY}) per year for [^.]*? to ({MONEY}) per year", re.I)
SIMPLE_RANGE_RE = re.compile(rf"ranges from ({MONEY}) to ({MONEY}) per year", re.I)
MEDIAN_RE = re.compile(rf"\btotals ({MONEY}) per year", re.I)
COUNTRY_AT_RE = re.compile(r"compensation in ([A-Z][\w .&'’-]*?) at ")
COUNTRY_IN_RE = re.compile(r" at [^.]*? in ([A-Z][\w .&'’-]*?) (?:totals|ranges)")
META_RE = re.compile(r'<meta[^>]+(?:og:description|name="description")[^>]*>', re.I)
CONTENT_RE = re.compile(r'content="([^"]+)"')

LEGAL_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "ltd", "company", "group", "the", "plc", "lp"}


def slugify(text: str) -> str:
    text = text.lower().replace("&", " and ")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-")


def slug_candidates(company: str) -> list[str]:
    """Ordered, de-duplicated levels.fyi slug guesses for a company name."""
    name = re.sub(r"\(.*?\)", " ", company).strip()
    words = [w for w in re.split(r"[\s,]+", name) if w]
    kept = [w for w in words if w.lower().strip(".") not in LEGAL_SUFFIXES]
    cands = [slugify(name)]
    if kept and kept != words:
        cands.append(slugify(" ".join(kept)))
    if "&" in name or " and " in name.lower():
        cands.append(slugify(re.sub(r"\s*(?:&|and)\s*", " ", name, flags=re.I)))
    if words and words[0].lower() == "the":
        cands.append(slugify(" ".join(words[1:])))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def parse_description(page_html: str) -> dict:
    """Extract an estimate from a levels.fyi page. Never raises."""
    empty = {"status": "none", "est_text": None, "country": None}
    try:
        meta = META_RE.search(page_html)
        content = CONTENT_RE.search(meta.group(0)) if meta else None
        desc = html.unescape(content.group(1)) if content else ""
    except Exception:  # noqa: BLE001
        return empty
    desc = re.sub(r"\s+", " ", desc)
    if not desc:
        return empty

    country = None
    m = COUNTRY_AT_RE.search(desc) or COUNTRY_IN_RE.search(desc)
    if m:
        country = m.group(1).strip()

    figure = None
    m = LEVEL_RANGE_RE.search(desc)
    if m:
        figure = ("entry", m.group(1).strip(), None)
    else:
        m = SIMPLE_RANGE_RE.search(desc)
        if m:
            figure = ("range", m.group(1).strip(), m.group(2).strip())
        else:
            m = MEDIAN_RE.search(desc)
            if m:
                figure = ("median", m.group(1).strip(), None)
    if not figure:
        return {**empty, "country": country}

    kind, low, high = figure
    text = f"~{low}-{high}" if high else f"~{low}"
    label = "est."
    if country and country.lower() not in ("united states", "usa", "us"):
        label = f"est., {country}"
    return {"status": "estimated", "kind": kind, "low": low, "high": high,
            "country": country, "est_text": f"{text} ({label})"}


def lookup(companies: list[str], concurrency: int = 8, timeout: float = 20.0,
           fetcher=None, out_dir: Path | None = None) -> dict:
    """Return {company: result}. 404s are retried with the next slug guess."""
    own_tmp = None
    if out_dir is None:
        own_tmp = tempfile.TemporaryDirectory()
        out_dir = Path(own_tmp.name)
    try:
        cands = {c: slug_candidates(c) for c in companies}
        results: dict[str, dict] = {c: {"status": "none", "est_text": None, "country": None,
                                        "slug": None} for c in companies}
        pending = {c: 0 for c in companies if cands[c]}
        rounds = 0
        while pending and rounds < MAX_ROUNDS:
            entries = []
            for c, i in pending.items():
                slug = cands[c][i]
                entries.append((BASE.format(slug=slug), f"{c}|{slug}"))
            batch = fetch_urls.fetch_all(
                [(u, re.sub(r"[^A-Za-z0-9_.-]+", "_", n) + ".html") for u, n in entries],
                out_dir, concurrency, timeout, fetch_urls.DEFAULT_USER_AGENT, False, fetcher)
            nxt: dict[str, int] = {}
            for (c, i), r in zip(list(pending.items()), batch["results"]):
                if r.get("ok") and r.get("path"):
                    parsed = parse_description(Path(r["path"]).read_text(encoding="utf-8", errors="replace"))
                    results[c] = {**parsed, "slug": cands[c][i]}
                elif r.get("status") == 404 and i + 1 < len(cands[c]):
                    nxt[c] = i + 1
            pending = nxt
            rounds += 1
        return results
    finally:
        if own_tmp:
            own_tmp.cleanup()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--companies", nargs="*", default=[])
    ap.add_argument("--companies-file")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = ap.parse_args(argv)
    companies = list(args.companies)
    if args.companies_file:
        companies += [l.strip() for l in Path(args.companies_file).read_text().splitlines() if l.strip()]
    companies = list(dict.fromkeys(companies))
    json.dump({"results": lookup(companies, args.concurrency)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
