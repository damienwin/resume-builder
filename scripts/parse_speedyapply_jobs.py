#!/usr/bin/env python3
"""Parse speedyapply/2027-SWE-College-Jobs' NEW_GRAD_USA.md into the same
structured JSON shape as parse_simplify_jobs.py, so job-scan can merge both
sources uniformly.

Usage:
    parse_speedyapply_jobs.py <md_path> --categories swe,pm,dsa,quant --days 7

Output: JSON to stdout — {"entries": [...], "scanned": N, "closed_excluded": N}
Each entry: company, role, location, age_raw, salary, category, faang,
adv_degree, no_sponsor, us_citizen, apply_url, source. `salary` is the raw
stated figure (e.g. "$150k/yr") or None for rows from the "Other" section,
which has no Salary column. This source has no closed-posting marker
(closed_excluded is always 0) and no adv_degree/no_sponsor/us_citizen
signal (always False) — those fields exist only for JSON-shape parity with
parse_simplify_jobs.py.

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
    ("pm", ("product manager", "product management", "program manager - technical")),
    ("dsa", ("data scientist", "machine learning", "applied scientist", "research scientist", "ai research", "mle ")),
    ("hw", ("hardware", "firmware", "asic", "fpga", "silicon engineer", "rf engineer")),
    ("quant", ("quant", "quantitative", "trading")),
]


def age_to_days(age_raw: str) -> float:
    m = re.match(r"(\d+)(h|d|mo)", age_raw.strip())
    if not m:
        return 0.0
    n, unit = int(m.group(1)), m.group(2)
    return {"h": n / 24, "d": float(n), "mo": n * 30.0}[unit]


def classify_category(section: str, role: str) -> str:
    if section == "quant":
        return "quant"
    role_lower = role.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in role_lower for kw in keywords):
            return category
    return "swe"


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


def parse(md_text: str, wanted_categories: set[str], max_days: float):
    entries = []
    scanned = 0

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

            age_days = age_to_days(age)
            if age_days > max_days:
                continue

            name_m = re.search(r"<strong>([^<]+)</strong>", company_cell)
            company = name_m.group(1).strip() if name_m else re.sub(r"<[^>]+>", "", company_cell).strip()

            url_m = re.search(r'<a href="([^"]+)"', apply_cell)
            apply_url = url_m.group(1) if url_m else None

            category = classify_category(section, role)
            if category not in wanted_categories:
                continue

            entries.append({
                "category": category,
                "company": company,
                "role": role,
                "location": location,
                "age_raw": age,
                "salary": salary,
                "faang": section == "faang",
                "adv_degree": False,
                "no_sponsor": False,
                "us_citizen": False,
                "apply_url": apply_url,
                "source": "speedyapply",
            })

    return {"entries": entries, "scanned": scanned, "closed_excluded": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md_path")
    ap.add_argument("--categories", required=True, help="comma-separated: swe,pm,dsa,quant,hw")
    ap.add_argument("--days", type=float, default=7)
    args = ap.parse_args()

    with open(args.md_path, encoding="utf-8") as f:
        text = f.read()

    wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
    result = parse(text, wanted, args.days)
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
