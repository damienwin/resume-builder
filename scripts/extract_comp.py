#!/usr/bin/env python3
"""Deterministic stated-compensation extraction from cached JD text.

Replaces the model-driven, per-posting scan in job-scan Step 3 ("scan the
cached JD for a stated salary/range, and note bonus/equity/RSU language")
with a regex-only function: no network, no model, same input -> same output.

For each JD text file it reports:

    base       The stated base-pay range, or null. {"low", "high", "period",
               "raw", "multi", "annualized_low", "annualized_high"}. `low` /
               `high` are the numbers as written (dollars; `134k` -> 134000),
               `period` is "yr" / "hr" / null, and `raw` is the matched text
               verbatim. Hourly figures are annualized (x2080) ONLY in the
               separate `annualized_*` fields. With several location-tiered
               ranges, `multi` is true and low/high are the envelope (min low,
               max high) of the ranges sharing the first range's period; `raw`
               is the first range as written.
    bonus      True only when the JD states bonus language (referral bonuses
               excluded).
    equity     True only when the JD states equity/RSU/stock-comp language in
               a compensation sense (not "diversity, equity and inclusion",
               "private equity", "equity markets", ...).
    comp_text  Ready-to-render TC cell, e.g. "$134k-$168k + bonus + equity",
               or null when nothing is stated. Bonus/equity are appended even
               with no base figure ("bonus + equity") since the JD states them.
    status     "stated" if a base range or bonus/equity language was found,
               else "none".

A dollar figure counts as base pay only when it carries a pay-period marker
("/yr", "per hour", ...) or sits near salary language ("base salary", "pay
range", ...), and is not near sign-on / relocation / stipend / funding
language, and has no M/B suffix. Nothing is ever invented: when nothing
qualifies the fields are null/false and the caller falls back to levels.fyi.

CLI:
    extract_comp.py JD.txt [JD.txt ...] --json
Prints {"results": [{"jd_path", "base", "bonus", "equity", "comp_text",
"status"}, ...]}. Exit 0 always; an unreadable/empty file is reported as
status "none".
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

HOURS_PER_YEAR = 2080

AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SUFFIX = r"[kKmMbB]\b|\s?(?:million|billion)\b"
MONEY = rf"(?:\$\s?)?(?P<{{n}}>{AMOUNT})\s?(?P<{{s}}>{SUFFIX})?"
SEP = r"\s*(?:-|–|—|to|and)\s*"

RANGE_RE = re.compile(
    r"(?<![\w$])\$\s?(?P<a>" + AMOUNT + r")\s?(?P<as>" + SUFFIX + r")?"
    r"(?:(?:\s?USD)?(?:\s?/\s?(?:yr|year|hr|hour)\b\.?)?"
    + SEP + r"(?:USD\s?)?\$?\s?(?P<b>" + AMOUNT + r")\s?(?P<bs>" + SUFFIX + r")?)?",
    re.IGNORECASE,
)
# Bare "134k-168k" (no dollar signs) -- accepted only with salary context.
BARE_RANGE_RE = re.compile(
    r"(?<![\w$.,])(?P<a>\d{2,3})\s?(?P<as>k)" + SEP + r"(?P<b>\d{2,3})\s?(?P<bs>k)\b",
    re.IGNORECASE,
)

HOURLY_RE = re.compile(
    r"^\s*(?:USD\s*)?(?:/|per\s+|an?\s+|each\s+)?\s*(?:hr\b|hour\b|hourly)"
    r"|^\s*(?:USD\s*)?/\s*h\b|^\s*(?:USD\s*)?hourly",
    re.IGNORECASE,
)
YEARLY_RE = re.compile(
    r"^\s*(?:USD\s*)?(?:/|per\s+|an?\s+|each\s+)?\s*(?:yr\b|year\b|annum\b|annual|annually)",
    re.IGNORECASE,
)
PERIOD_BEFORE_RE = re.compile(r"(?:hourly|per\s+hour|hour(?:ly)?\s+(?:rate|pay|wage))\W{0,20}$", re.IGNORECASE)

SALARY_CONTEXT = re.compile(
    r"\b(?:salary|salaries|base|pay|paid|compensation|comp|wage|wages|remuneration|"
    r"earn|earning|earnings|range|ranges|rate|annual|annually|hourly|per\s+year|"
    r"per\s+hour|ote|tc)\b",
    re.IGNORECASE,
)
# Dollar amounts near these are NOT base pay.
EXCLUDE_BEFORE = re.compile(
    r"(?:sign[\s-]?on|signing|relocation|relo|stipend|housing|tuition|"
    r"referral|reimburse\w*|allowance|401\(?k\)?|match(?:ing)?|"
    r"raised|raise[sd]?|funding|funded|valuation|valued|revenue|series\s+[a-z]|"
    r"investment|invested|backed|bonus\s+of|bonus\s+up\s+to|up\s+to)\W{0,25}$",
    re.IGNORECASE,
)
EXCLUDE_AFTER = re.compile(
    r"^[\s,]{0,3}(?:in\s+)?(?:funding|raised|revenue|valuation|in\s+(?:sign|reloc|stipend|funding|revenue)|"
    r"(?:sign[\s-]?on|signing|relocation|stipend|housing|referral)\b|"
    r"(?:to|for)\s+(?:relocat|cover|offset)|(?:million|billion))",
    re.IGNORECASE,
)

BONUS_RE = re.compile(r"\bbonus(?:es)?\b", re.IGNORECASE)
BONUS_EXCLUDE = re.compile(r"(?:referral|employee\s+referral|no)\s+$", re.IGNORECASE)
NEGATED_BONUS = re.compile(r"\bno\s+(?:\w+\s+){0,2}bonus", re.IGNORECASE)

EQUITY_STRONG = re.compile(
    r"\b(?:RSUs?|restricted\s+stock(?:\s+units?)?|stock\s+(?:options?|grants?|awards?|units?|compensation)|"
    r"equity\s+(?:grants?|awards?|compensation|packages?|incentives?|participation|refresh)|"
    r"(?:employee|company)\s+stock|stock\s+purchase)\b",
    re.IGNORECASE,
)
EQUITY_WEAK = re.compile(r"\b(?:equity|stock)\b", re.IGNORECASE)
EQUITY_COMP_CONTEXT = re.compile(
    r"\b(?:compensation|salary|package|offer|bonus|eligible|incentives?|benefits|"
    r"total\s+rewards|grant|vest\w*|ownership|plus|including|includes?)\b|\+",
    re.IGNORECASE,
)
EQUITY_EXCLUDE = re.compile(
    r"(?:diversity|inclusion|inclusive|belonging|private|home|brand|shareholder|"
    r"fixed[\s-]income|capital|derivatives?|cash)[\s,&/-]*(?:and\s+|&\s*)?$|"
    r"^\s*(?:and\s+|&\s*)?(?:inclusion|inclusive|belonging|diversity|markets?|trading|"
    r"derivatives?|research|index|options|volatility|capital|financing|funds?|"
    r"analysts?|desk|sales|portfolio|investments?|quant|strateg\w*|"
    r"exchange|exchanges|prices?|pricing|market)\b|"
    r"^\s*(?:stock\s+)?(?:market|exchange|trading|price)\b",
    re.IGNORECASE,
)


def _scale(num: str, suffix: str | None) -> float | None:
    """Parse a number+suffix into dollars. None for M/B (never a salary)."""
    value = float(num.replace(",", ""))
    s = (suffix or "").strip().lower()
    if s in ("m", "b", "million", "billion"):
        return None
    if s == "k":
        value *= 1000
    return value


def _window(text: str, start: int, end: int, before: int = 80, after: int = 60):
    return text[max(0, start - before):start], text[end:end + after]


def _period(after: str, before: str) -> str | None:
    if HOURLY_RE.match(after):
        return "hr"
    if YEARLY_RE.match(after):
        return "yr"
    if PERIOD_BEFORE_RE.search(before):
        return "hr"
    return None


def _plausible(low: float, high: float, period: str | None) -> bool:
    if period == "hr":
        return 8 <= low <= 1000 and 8 <= high <= 1000
    # Yearly (explicit or inferred): reject tiny/huge figures.
    return 20_000 <= low <= 2_000_000 and 20_000 <= high <= 2_000_000


def _candidates(text: str):
    """Yield (start, low, high, period, raw) for each base-pay-looking range."""
    seen_spans: list[tuple[int, int]] = []
    matches = [(m, False) for m in RANGE_RE.finditer(text)]
    matches += [(m, True) for m in BARE_RANGE_RE.finditer(text)]
    matches.sort(key=lambda t: t[0].start())
    for m, bare in matches:
        start, end = m.start(), m.end()
        if any(s <= start < e for s, e in seen_spans):
            continue
        a_suf, b_suf = m.group("as"), m.group("bs")
        a = _scale(m.group("a"), a_suf)
        if a is None:
            continue
        b_raw = m.group("b")
        b = _scale(b_raw, b_suf or (a_suf if a_suf and a_suf.lower() == "k" else None)) if b_raw else a
        if b is None:
            continue
        if b_raw and not b_suf and a_suf and a_suf.lower() == "k" and b < 1000:
            b *= 1000  # "$134k-168" shorthand
        # "$100 and ..." with no real second figure is a single value.
        if b_raw and b < a * 0.5 and not b_suf:
            b, b_raw = a, None
        if b_raw is None and bare:
            continue
        low, high = (a, b) if a <= b else (b, a)
        before, after = _window(text, start, end)
        if EXCLUDE_BEFORE.search(before) or EXCLUDE_AFTER.match(after):
            continue
        # Plain numbers like "$5" / "$25" need a period marker; checked below.
        period = _period(after, before)
        has_ctx = bool(SALARY_CONTEXT.search(before)) or bool(SALARY_CONTEXT.search(after[:40]))
        if period is None and not has_ctx:
            continue
        if period is None:
            period = "hr" if high < 1000 else "yr"
        if period == "hr" and high >= 1000:
            period = "yr"
        if not _plausible(low, high, period):
            continue
        # A lone figure with only weak context (no range, no period marker)
        # is too ambiguous: "$150,000 in funding"-style hits slip through.
        if b_raw is None and not (HOURLY_RE.match(after) or YEARLY_RE.match(after)
                                  or re.search(r"\b(?:salary|base|pay|compensation)\b", before[-40:], re.I)):
            continue
        seen_spans.append((start, end))
        yield start, low, high, period, text[start:end].strip()


def extract_base(text: str) -> dict | None:
    cands = list(_candidates(text))
    if not cands:
        return None
    _, low, high, period, raw = cands[0]
    same = [c for c in cands if c[3] == period]
    multi = len({(c[1], c[2]) for c in same}) > 1
    if multi:
        low = min(c[1] for c in same)
        high = max(c[2] for c in same)
    base = {
        "low": int(low),
        "high": int(high),
        "period": period,
        "raw": raw,
        "multi": multi,
    }
    if period == "hr":
        base["annualized_low"] = int(round(low * HOURS_PER_YEAR))
        base["annualized_high"] = int(round(high * HOURS_PER_YEAR))
    return base


def has_bonus(text: str) -> bool:
    for m in BONUS_RE.finditer(text):
        before = text[max(0, m.start() - 30):m.start()]
        if BONUS_EXCLUDE.search(before):
            continue
        if NEGATED_BONUS.search(text[max(0, m.start() - 30):m.end()]):
            continue
        return True
    return False


def has_equity(text: str) -> bool:
    if EQUITY_STRONG.search(text):
        return True
    for m in EQUITY_WEAK.finditer(text):
        before = text[max(0, m.start() - 25):m.start()]
        after = text[m.end():m.end() + 30]
        if EQUITY_EXCLUDE.search(before) or EQUITY_EXCLUDE.search(after):
            continue
        around = text[max(0, m.start() - 70):m.end() + 70]
        if EQUITY_COMP_CONTEXT.search(around):
            return True
    return False


def _fmt_k(v: int) -> str:
    return f"${v // 1000}k" if v % 1000 == 0 else f"${v:,}"


def _fmt_raw(base: dict) -> str:
    if base.get("multi"):
        # Tiered ranges: show the envelope so the cell matches low/high.
        return f"{_fmt_k(base['low'])}-{_fmt_k(base['high'])}" + ("/hr" if base["period"] == "hr" else "")
    raw = re.sub(r"\s+", " ", base["raw"])
    raw = re.sub(r"\s*(?:USD)\s*$", "", raw, flags=re.I).strip()
    return raw + ("/hr" if base["period"] == "hr" else "")


_TAG_RE = re.compile(r"<[^>]{0,300}>")
_UNI_ESC_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _clean(text: str) -> str:
    """Strip leftover HTML (tags, entities, JSON \\uXXXX escapes) that
    fetch_urls --strip-tags can leave behind, so "<bdi>$85,600</bdi> -
    <bdi>$128,400</bdi>" reads as one range."""
    if "<" not in text and "&" not in text and "\\u" not in text:
        return text
    text = _UNI_ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = html.unescape(_TAG_RE.sub(" ", text)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text)


def extract_comp(text: str) -> dict:
    text = _clean(text)
    base = extract_base(text)
    bonus = has_bonus(text)
    equity = has_equity(text)
    parts: list[str] = []
    if base:
        parts.append(_fmt_raw(base))
    if bonus:
        parts.append("bonus")
    if equity:
        parts.append("equity")
    return {
        "base": base,
        "bonus": bonus,
        "equity": equity,
        "comp_text": " + ".join(parts) if parts else None,
        "status": "stated" if parts else "none",
    }


def extract_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    result = extract_comp(text) if text.strip() else extract_comp("")
    return {"jd_path": path, **result}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jd_files", nargs="+")
    ap.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = ap.parse_args(argv)
    results = [extract_file(p) for p in args.jd_files]
    json.dump({"results": results}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
