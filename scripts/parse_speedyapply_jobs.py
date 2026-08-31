#!/usr/bin/env python3
"""Parse a speedyapply-format board (2027-SWE-College-Jobs or
2027-AI-College-Jobs) NEW_GRAD_USA.md into the same structured JSON shape as
parse_simplify_jobs.py, so job-scan can merge multiple sources uniformly.

Usage:
    parse_speedyapply_jobs.py <md_path> --categories swe,pm,dsa,quant --days 7 \
        [--source-name speedyapply] [--default-category swe]

Output: JSON to stdout — {"entries": [...], "scanned": N, "closed_excluded": N}
Each entry: company, role, location, age_raw, salary, category, faang,
adv_degree, no_sponsor, us_citizen, apply_url, source. `salary` is the raw
stated figure (e.g. "$150k/yr") or None for rows from the "Other" section,
which has no Salary column. This source has no closed-posting marker
(closed_excluded is always 0) and no no_sponsor/us_citizen signal (always
False) — those two exist only for JSON-shape parity with
parse_simplify_jobs.py. `adv_degree` is a weak title-only heuristic here
(see ADV_DEGREE_TITLE_RE below) — it can be True but is frequently a false
negative, unlike Simplify's hand-curated 🎓 marker.

`--source-name` stamps the `source` field (must be distinct per board fed
into merge_and_filter_jobs.py — dedupe there only ever collapses entries
from *different* `source` values, so two speedyapply-format boards sharing
one name would never dedupe against each other).

`--default-category` is the fallback for a row that matches no
CATEGORY_KEYWORDS and isn't analyst-family (see OTHER_FAMILY_KEYWORDS below).
The AI/ML board's unmatched rows skew data-science, not general SWE, so it
passes `dsa` here; the general SWE board keeps the `swe` default.

Rows that read as analyst/consultant/GTM-family (e.g. "Data Analyst",
"Management Consultant - AI Strategy Evaluation", "AI Enablement
Specialist") classify to category "other", which callers never include in
`--categories` — this keeps those rows out of the surfaced table without a
company-based denylist. A row that also matches quant/pm/dsa/hw keywords
(e.g. "Quantitative Risk Management - Summer Analyst") is caught by
CATEGORY_KEYWORDS first and never reaches this check, since real quant
analyst roles must not be dropped as noise.

Format notes (verified against the live file): three sections delimited by
HTML comments (<!-- TABLE_FAANG_START/END -->, <!-- TABLE_QUANT_START/END -->,
and the unlabeled <!-- TABLE_START/END --> for "Other"), each a markdown pipe
table. FAANG+/Quant rows have 6 cells (Company, Position, Location, Salary,
Posting, Age); Other rows have 5 (no Salary).
"""
import argparse
import json
import re
import sys

SECTION_MARKERS = [
    ("faang", re.compile(r"<!--\s*TABLE_FAANG_START\s*-->(.*?)<!--\s*TABLE_FAANG_END\s*-->", re.S)),
    ("quant", re.compile(r"<!--\s*TABLE_QUANT_START\s*-->(.*?)<!--\s*TABLE_QUANT_END\s*-->", re.S)),
    ("other", re.compile(r"<!--\s*TABLE_START\s*-->(.*?)<!--\s*TABLE_END\s*-->", re.S)),
]

CATEGORY_KEYWORDS = [
    ("pm", ("product manager", "product management", "product operations", "program manager - technical")),
    ("dsa", ("data scientist", "machine learning", "applied scientist", "research scientist", "ai research", "mle ")),
    ("hw", ("hardware", "firmware", "asic", "fpga", "silicon engineer", "rf engineer")),
    ("quant", ("quant", "quantitative", "trading")),
]

# Analyst/consultant/GTM-family roles that aren't a real match for any of
# swe/pm/dsa/quant/hw. Checked only after CATEGORY_KEYWORDS finds no match,
# so a role like "Quantitative Risk Management - Summer Analyst" is caught
# by the "quantitative" keyword above and never reaches this list.
OTHER_FAMILY_KEYWORDS = ("analyst", "consultant", "enablement", "gtm")

# Title-level advanced-degree signal (e.g. TikTok's "... - 2027 Start - PhD"
# suffix). This is a much weaker signal than Simplify's 🎓 marker, which the
# board's maintainers curate by hand from the JD itself — this source has no
# such curation, so a role requiring a PhD in its JD but not saying so in the
# title (verified live: Iambic Therapeutics, Applied Intuition, Lila
# Sciences, Flow Traders, Axon all required a PhD with zero title signal)
# will still come through as adv_degree: False. Step 2.6's JD-level check
# only ever runs on adv_degree: true rows, so those still slip past the scan
# table undetected — the acting-on-results.md fork-level eligibility check
# (which reads the actual JD) remains the real safety net for this source,
# not this flag. Treat adv_degree here as "definitely advanced-degree
# titled," never as "definitely not."
ADV_DEGREE_TITLE_RE = re.compile(r"\b(ph\.?d|doctorate|postdoc(?:toral)?)\b", re.I)


def age_to_days(age_raw: str) -> float:
    m = re.match(r"(\d+)(h|d|mo)", age_raw.strip())
    if not m:
        return 0.0
    n, unit = int(m.group(1)), m.group(2)
    return {"h": n / 24, "d": float(n), "mo": n * 30.0}[unit]


def classify_category(section: str, role: str, default_category: str = "swe") -> str:
    if section == "quant":
        return "quant"
    role_lower = role.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in role_lower for kw in keywords):
            return category
    if any(kw in role_lower for kw in OTHER_FAMILY_KEYWORDS):
        return "other"
    return default_category


def split_row(line: str):
    line = line.strip()
    if not line.startswith("|"):
        return None
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) not in (5, 6):
        return None
    if cells[0] in ("Company", "---"):
        return None
    return cells


def parse(md_text: str, wanted_categories: set[str], max_days: float, since=None,
          source_name: str = "speedyapply", default_category: str = "swe"):
    entries = []
    scanned = 0
    # Topmost row in each table, independent of --days/category filters -
    # see the matching comment in parse_simplify_jobs.py.
    section_top = {}

    for section, marker_re in SECTION_MARKERS:
        m = marker_re.search(md_text)
        if not m:
            continue
        chunk = m.group(1)
        for line in chunk.splitlines():
            cells = split_row(line)
            if cells is None:
                continue
            scanned += 1

            company_cell = cells[0]
            role = cells[1]
            location = cells[2]
            if len(cells) == 6:
                salary, apply_cell, age = cells[3], cells[4], cells[5]
            else:
                salary, apply_cell, age = None, cells[3], cells[4]

            name_m = re.search(r"<strong>([^<]+)</strong>", company_cell)
            company = name_m.group(1).strip() if name_m else re.sub(r"<[^>]+>", "", company_cell).strip()

            url_m = re.search(r'<a href="([^"]+)"', apply_cell)
            apply_url = url_m.group(1) if url_m else None

            category = classify_category(section, role, default_category)

            if apply_url and section not in section_top:
                section_top[section] = apply_url

            # Each of the three tables (faang/quant/other) is independently
            # newest-first, but they are NOT one merged chronological list
            # across tables - so the marker boundary must be per table
            # (section), not per category. Hitting last run's top row for
            # this section means everything below in this section was
            # already seen.
            if since and since.get(section) and apply_url == since[section]:
                break

            if category not in wanted_categories:
                continue

            age_days = age_to_days(age)
            if age_days > max_days:
                continue

            entries.append({
                "category": category,
                "section": section,
                "company": company,
                "role": role,
                "location": location,
                "age_raw": age,
                "salary": salary,
                "adv_degree": bool(ADV_DEGREE_TITLE_RE.search(role)),
                "faang": section == "faang",
                "no_sponsor": False,
                "us_citizen": False,
                "apply_url": apply_url,
                "source": source_name,
            })

    return {"entries": entries, "scanned": scanned, "closed_excluded": 0, "section_top": section_top}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md_path")
    ap.add_argument("--categories", required=True, help="comma-separated: swe,pm,dsa,quant,hw")
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument(
        "--since-json",
        help="JSON object mapping section (faang/quant/other, NOT category "
        "- each table mixes categories) -> apply_url of that table's "
        "newest row seen last run. --days still applies as a fallback "
        "bound for sections with no marker.",
    )
    ap.add_argument(
        "--source-name", default="speedyapply",
        help="value stamped into each entry's `source` field. Must be "
        "distinct per speedyapply-format board merged in the same run.",
    )
    ap.add_argument(
        "--default-category", default="swe",
        help="fallback category for a row matching no CATEGORY_KEYWORDS "
        "and no OTHER_FAMILY_KEYWORDS (e.g. `dsa` for an AI/ML board).",
    )
    args = ap.parse_args()

    with open(args.md_path, encoding="utf-8") as f:
        text = f.read()

    wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
    since = json.loads(args.since_json) if args.since_json else None
    result = parse(text, wanted, args.days, since,
                    source_name=args.source_name, default_category=args.default_category)
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
