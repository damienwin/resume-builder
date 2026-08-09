#!/usr/bin/env python3
"""Parse a Simplify Jobs README (New-Grad-Positions or Summer-Internships
format) into structured, filtered JSON. Used by the job-scan skill so
Claude never has to hand-parse thousands of raw HTML table rows.

Usage:
    parse_simplify_jobs.py <readme_path> --categories swe,pm,dsa,quant --days 7

Output: JSON to stdout — {"entries": [...], "scanned": N, "closed_excluded": N}
Each entry: company, role, location, age_raw, category, faang, adv_degree,
no_sponsor, us_citizen, apply_url. Only closed (locked) rows and rows older
than --days are excluded; everything else in the requested categories is
included — filtering decisions beyond that (offer comparison, relevance)
are left to the caller.
"""
import argparse
import datetime
import json
import re
import sys

SECTION_TO_CATEGORY = {
    "Software Engineering": "swe",
    "Product Management": "pm",
    "Data Science, AI & Machine Learning": "dsa",
    "Quantitative Finance": "quant",
    "Hardware Engineering": "hw",
}

ROW_RE = re.compile(
    r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>",
    re.S,
)
HEADING_RE = re.compile(r"^## .*? ([A-Za-z][A-Za-z, &]*?) (?:New Grad|Internship) Roles", re.M)


def age_to_days(age_raw: str) -> float:
    m = re.match(r"(\d+)(h|d|mo)", age_raw.strip())
    if not m:
        return 0.0
    n, unit = int(m.group(1)), m.group(2)
    return {"h": n / 24, "d": float(n), "mo": n * 30.0}[unit]


def strip_tags(html: str) -> str:
    html = re.sub(r"<details>.*?<summary>(.*?)</summary>.*?</details>", r"\1", html, flags=re.S)
    return re.sub(r"<[^>]+>", "", html).strip()


def parse(readme_text: str, wanted_categories: set[str], max_days: float):
    heads = [(m.start(), m.group(1)) for m in HEADING_RE.finditer(readme_text)]
    heads.append((len(readme_text), None))

    entries = []
    scanned = 0
    closed_excluded = 0

    for i in range(len(heads) - 1):
        section_name = heads[i][1]
        category = SECTION_TO_CATEGORY.get(section_name)
        if category is None or category not in wanted_categories:
            continue
        chunk = readme_text[heads[i][0]:heads[i + 1][0]]
        last_company = None
        for company_html, role_html, loc_html, apply_html, age in ROW_RE.findall(chunk):
            scanned += 1
            blob = company_html + role_html
            # The closed marker replaces the whole apply-links cell with a
            # bare lock emoji (<td>🔒</td>) rather than appearing inline in
            # company/role text, so it must be checked separately.
            closed = "\U0001F512" in apply_html  # 🔒
            faang = "\U0001F525" in blob  # 🔥
            adv_degree = "\U0001F393" in blob  # 🎓
            no_sponsor = "\U0001F6C2" in blob  # 🛂
            us_citizen = "\U0001F1FA\U0001F1F8" in blob  # 🇺🇸

            if "↳" in company_html:  # ↳
                company = last_company
            else:
                m = re.search(r">([^<>]+)</a>", company_html)
                raw_name = m.group(1) if m else strip_tags(company_html)
                company = re.sub(r"^[\U0001F300-\U0001FAFF\s]+", "", raw_name).strip()
                last_company = company

            if closed:
                closed_excluded += 1
                continue

            age_days = age_to_days(age.strip())
            if age_days > max_days:
                continue

            apply_urls = re.findall(r'<a href="([^"]+)"', apply_html)
            apply_url = apply_urls[0] if apply_urls else None

            entries.append({
                "category": category,
                "company": company,
                "role": strip_tags(role_html),
                "location": strip_tags(loc_html),
                "age_raw": age.strip(),
                "faang": faang,
                "adv_degree": adv_degree,
                "no_sponsor": no_sponsor,
                "us_citizen": us_citizen,
                "apply_url": apply_url,
            })

    return {"entries": entries, "scanned": scanned, "closed_excluded": closed_excluded}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("readme_path")
    ap.add_argument("--categories", required=True, help="comma-separated: swe,pm,dsa,quant")
    ap.add_argument("--days", type=float, default=7)
    args = ap.parse_args()

    with open(args.readme_path, encoding="utf-8") as f:
        text = f.read()

    wanted = {c.strip() for c in args.categories.split(",") if c.strip()}
    result = parse(text, wanted, args.days)
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
