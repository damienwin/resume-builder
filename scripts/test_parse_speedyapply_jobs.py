#!/usr/bin/env python3
"""Tests for parse_speedyapply_jobs.py — the speedyapply/2027-SWE-College-Jobs
markdown-table parser the job-scan skill relies on, including the
--since-last-scan `section_top` marker (keyed by table SECTION, not
category — a bug fixed mid-development).

Run: python3 scripts/test_parse_speedyapply_jobs.py
"""
import unittest

from parse_speedyapply_jobs import ADV_DEGREE_TITLE_RE, classify_category, parse

SECTION_START = {
    "faang": "<!-- TABLE_FAANG_START -->",
    "quant": "<!-- TABLE_QUANT_START -->",
    "other": "<!-- TABLE_START -->",
}
SECTION_END = {
    "faang": "<!-- TABLE_FAANG_END -->",
    "quant": "<!-- TABLE_QUANT_END -->",
    "other": "<!-- TABLE_END -->",
}


def row(company, role, location="Remote", apply_url="https://apply.example.com/1",
        age="0d", salary=None):
    apply_cell = f'<a href="{apply_url}">apply</a>'
    company_cell = f"<strong>{company}</strong>"
    if salary is not None:
        return f"| {company_cell} | {role} | {location} | {salary} | {apply_cell} | {age} |"
    return f"| {company_cell} | {role} | {location} | {apply_cell} | {age} |"


def header_row(has_salary):
    if has_salary:
        return "| Company | Position | Location | Salary | Application/Link | Age |"
    return "| Company | Position | Location | Application/Link | Age |"


def sep_row(has_salary):
    return "| --- | --- | --- | --- | --- | --- |" if has_salary else "| --- | --- | --- | --- | --- |"


def section(name, rows, has_salary=None):
    if has_salary is None:
        has_salary = name in ("faang", "quant")
    body = "\n".join([header_row(has_salary), sep_row(has_salary)] + rows)
    return f"{SECTION_START[name]}\n{body}\n{SECTION_END[name]}\n"


def md(sections_dict):
    # sections_dict: {name: [rows]} — always emits all three markers so the
    # regexes find their bounds, empty tables otherwise.
    out = []
    for name in ("faang", "quant", "other"):
        rows = sections_dict.get(name, [])
        out.append(section(name, rows))
    return "# Title\n\n" + "\n".join(out)


class ClassifyCategoryTests(unittest.TestCase):
    def test_quant_section_always_quant(self):
        self.assertEqual(classify_category("quant", "Software Engineer"), "quant")

    def test_pm_keyword_match(self):
        self.assertEqual(classify_category("other", "Product Manager, Growth"), "pm")

    def test_dsa_keyword_match(self):
        self.assertEqual(classify_category("faang", "Machine Learning Engineer"), "dsa")

    def test_hw_keyword_match(self):
        self.assertEqual(classify_category("other", "Firmware Engineer"), "hw")

    def test_defaults_to_swe(self):
        self.assertEqual(classify_category("faang", "Backend Software Engineer"), "swe")

    def test_default_category_override(self):
        self.assertEqual(
            classify_category("faang", "Backend Software Engineer", default_category="dsa"),
            "dsa",
        )

    def test_analyst_role_classifies_other(self):
        self.assertEqual(classify_category("other", "Data Analyst"), "other")
        self.assertEqual(classify_category("other", "Management Consultant - AI Strategy"), "other")
        self.assertEqual(classify_category("other", "AI Enablement Specialist"), "other")
        self.assertEqual(classify_category("other", "GTM Data Analytics Engineer"), "other")

    def test_quant_analyst_role_still_classifies_quant(self):
        # A real quant role must not be caught by the analyst-family filter
        # just because its title also contains "Analyst".
        self.assertEqual(
            classify_category("other", "Quantitative Risk Management - Summer Analyst"),
            "quant",
        )

    def test_product_operations_classifies_pm(self):
        # Live false negative: "Product Operations - AI Revenue Systems" (Ramp)
        # matched no keyword and fell through to swe, despite being a PM/ops
        # role with a 1-3 years product experience requirement, not SWE.
        self.assertEqual(classify_category("other", "Product Operations - AI Revenue Systems"), "pm")


class AdvDegreeTitleRegexTests(unittest.TestCase):
    def test_phd_suffix_matches(self):
        self.assertTrue(ADV_DEGREE_TITLE_RE.search("ML Engineer Graduate - 2027 Start - PhD"))

    def test_ph_dot_d_matches(self):
        self.assertTrue(ADV_DEGREE_TITLE_RE.search("Research Scientist, Ph.D."))

    def test_postdoc_matches(self):
        self.assertTrue(ADV_DEGREE_TITLE_RE.search("Postdoctoral Research Associate"))

    def test_plain_title_does_not_match(self):
        # This is the known false-negative case: a title with no degree
        # wording gives no signal even when the JD itself requires a PhD
        # (verified live for Iambic, Applied Intuition, Lila Sciences, Flow
        # Traders, Axon) - Step 2.6 / acting-on-results.md's JD-level check
        # is what actually has to catch those, not this regex.
        self.assertIsNone(ADV_DEGREE_TITLE_RE.search("Research Scientist - Humanoid Robotics"))


class SectionParsingTests(unittest.TestCase):
    def test_adv_degree_true_when_title_names_phd(self):
        text = md({"other": [row("Acme", "ML Engineer Graduate - 2027 Start - PhD")]})
        result = parse(text, {"swe"}, max_days=7)
        self.assertTrue(result["entries"][0]["adv_degree"])

    def test_adv_degree_false_when_title_has_no_degree_wording(self):
        # Known false negative - see AdvDegreeTitleRegexTests.
        text = md({"other": [row("Acme", "Research Scientist - Humanoid Robotics")]})
        result = parse(text, {"dsa"}, max_days=7)
        self.assertFalse(result["entries"][0]["adv_degree"])

    def test_faang_rows_carry_salary(self):
        text = md({"faang": [row("Acme", "Software Engineer New Grad", salary="$150k/yr")]})
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"][0]["salary"], "$150k/yr")
        self.assertTrue(result["entries"][0]["faang"])

    def test_quant_rows_carry_salary_and_category(self):
        text = md({"quant": [row("Jane Street", "Quant Trader", salary="$200k/yr")]})
        result = parse(text, {"quant"}, max_days=7)
        self.assertEqual(result["entries"][0]["salary"], "$200k/yr")
        self.assertEqual(result["entries"][0]["category"], "quant")

    def test_other_rows_have_no_salary_column(self):
        text = md({"other": [row("Initech", "Software Engineer New Grad")]})
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"][0]["salary"], None)
        self.assertFalse(result["entries"][0]["faang"])

    def test_wanted_category_filter(self):
        text = md({
            "other": [
                row("Initech", "Software Engineer New Grad", apply_url="https://a.com/1"),
                row("Globex", "Product Manager, Growth", apply_url="https://a.com/2"),
            ],
        })
        result = parse(text, {"swe"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Initech"])

    def test_scanned_counts_all_rows_regardless_of_category(self):
        text = md({
            "other": [
                row("Initech", "Software Engineer New Grad"),
                row("Globex", "Product Manager, Growth"),
            ],
        })
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["scanned"], 2)

    def test_closed_excluded_always_zero(self):
        text = md({"other": [row("Initech", "Software Engineer New Grad")]})
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["closed_excluded"], 0)

    def test_source_name_stamped_on_entries(self):
        text = md({"other": [row("Initech", "Software Engineer New Grad")]})
        result = parse(text, {"swe"}, max_days=7, source_name="speedyapply_ai")
        self.assertEqual(result["entries"][0]["source"], "speedyapply_ai")

    def test_default_source_name_is_speedyapply(self):
        text = md({"other": [row("Initech", "Software Engineer New Grad")]})
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"][0]["source"], "speedyapply")

    def test_default_category_flows_through_parse(self):
        text = md({"other": [row("Initech", "Backend Engineer")]})
        result = parse(text, {"dsa"}, max_days=7, default_category="dsa")
        self.assertEqual(result["entries"][0]["category"], "dsa")

    def test_analyst_family_rows_excluded_from_wanted_categories(self):
        text = md({
            "other": [
                row("Initech", "Data Analyst", apply_url="https://a.com/1"),
                row("Globex", "Software Engineer New Grad", apply_url="https://a.com/2"),
            ],
        })
        result = parse(text, {"swe", "pm", "dsa", "quant", "hw"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Globex"])


class AgeCutoffTests(unittest.TestCase):
    def test_rows_older_than_max_days_excluded(self):
        text = md({
            "other": [
                row("Fresh", "Software Engineer New Grad", age="0d", apply_url="https://a.com/f"),
                row("Old", "Software Engineer New Grad", age="10d", apply_url="https://a.com/o"),
            ],
        })
        result = parse(text, {"swe"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Fresh"])


class SectionKeyedSinceTests(unittest.TestCase):
    """`since` must be keyed by table SECTION (faang/quant/other), not by
    category — a single table mixes multiple categories and is only
    independently newest-first per table."""

    def test_section_keyed_since_stops_that_table(self):
        text = md({
            "other": [
                row("New1", "Software Engineer New Grad", apply_url="https://a.com/n1"),
                row("SeenBefore", "Software Engineer New Grad", apply_url="https://a.com/seen"),
                row("Old", "Software Engineer New Grad", apply_url="https://a.com/old"),
            ],
        })
        since = {"other": "https://a.com/seen"}
        result = parse(text, {"swe"}, max_days=365, since=since)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["New1"])

    def test_category_keyed_since_does_not_stop_the_table(self):
        # Guards against regressing to the category-keyed bug: a table that
        # mixes swe and pm rows must not stop early just because a since
        # marker happens to be keyed "swe" — only a "other"/"faang"/"quant"
        # key can stop it.
        text = md({
            "other": [
                row("New1", "Software Engineer New Grad", apply_url="https://a.com/n1"),
                row("SeenBefore", "Software Engineer New Grad", apply_url="https://a.com/seen"),
                row("Old", "Software Engineer New Grad", apply_url="https://a.com/old"),
            ],
        })
        wrongly_keyed_since = {"swe": "https://a.com/seen"}
        result = parse(text, {"swe"}, max_days=365, since=wrongly_keyed_since)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["New1", "SeenBefore", "Old"])

    def test_since_spans_multiple_categories_within_one_table(self):
        # A single "other" table mixes swe and pm rows; the marker being the
        # newest row in the WHOLE table (regardless of that row's own
        # category) must still stop everything below it, across categories.
        text = md({
            "other": [
                row("NewPM", "Product Manager, Growth", apply_url="https://a.com/pm1"),
                row("SeenSWE", "Software Engineer New Grad", apply_url="https://a.com/seen"),
                row("OldPM", "Product Manager, Growth", apply_url="https://a.com/pm2"),
            ],
        })
        since = {"other": "https://a.com/seen"}
        result = parse(text, {"swe", "pm"}, max_days=365, since=since)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["NewPM"])


class SectionTopTests(unittest.TestCase):
    def test_section_top_set_even_when_days_excludes_every_row(self):
        text = md({
            "other": [row("Acme", "Software Engineer New Grad", age="30d",
                           apply_url="https://a.com/a")],
        })
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["section_top"], {"other": "https://a.com/a"})

    def test_section_top_is_only_the_first_row_in_that_table(self):
        text = md({
            "other": [
                row("First", "Software Engineer New Grad", apply_url="https://a.com/first"),
                row("Second", "Software Engineer New Grad", apply_url="https://a.com/second"),
            ],
        })
        result = parse(text, {"swe"}, max_days=365)
        self.assertEqual(result["section_top"], {"other": "https://a.com/first"})

    def test_section_top_independent_per_table(self):
        text = md({
            "faang": [row("Faang1", "Software Engineer New Grad", salary="$150k/yr",
                           apply_url="https://a.com/faang1")],
            "other": [row("Other1", "Software Engineer New Grad",
                           apply_url="https://a.com/other1")],
        })
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["section_top"], {
            "faang": "https://a.com/faang1",
            "other": "https://a.com/other1",
        })

    def test_section_top_captured_before_category_filter(self):
        # First row of the table is a category NOT in wanted_categories -
        # section_top must still record it, since it's about table position,
        # not about which categories the caller asked for.
        text = md({
            "other": [
                row("PMFirst", "Product Manager, Growth", apply_url="https://a.com/pm1"),
                row("SweSecond", "Software Engineer New Grad", apply_url="https://a.com/swe1"),
            ],
        })
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["section_top"], {"other": "https://a.com/pm1"})
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["SweSecond"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
