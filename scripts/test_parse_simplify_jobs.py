#!/usr/bin/env python3
"""Tests for parse_simplify_jobs.py — the SimplifyJobs README table parser
the job-scan skill relies on, including the --since-last-scan `category_top`
marker.

Run: python3 scripts/test_parse_simplify_jobs.py
"""
import unittest

from parse_simplify_jobs import parse

SECTION_HEADINGS = {
    "swe": "## 💻 Software Engineering New Grad Roles",
    "pm": "## 📋 Product Management New Grad Roles",
    "dsa": "## 🤖 Data Science, AI & Machine Learning New Grad Roles",
    "quant": "## 📈 Quantitative Finance New Grad Roles",
    "hw": "## 🔧 Hardware Engineering New Grad Roles",
}


def row(company, role, location="Remote", apply_url="https://apply.example.com/1",
        age="0d", closed=False, company_html=None):
    company_cell = company_html if company_html is not None else (
        f'<a href="https://simplify.jobs/c/{company.lower()}">{company}</a>'
    )
    apply_cell = "\U0001F512" if closed else f'<a href="{apply_url}">apply</a>'
    return (
        f"<tr><td>{company_cell}</td><td>{role}</td><td>{location}</td>"
        f"<td>{apply_cell}</td><td>{age}</td></tr>"
    )


def ditto_row(role, location="Remote", apply_url="https://apply.example.com/x",
              age="0d", closed=False):
    # "↳" ditto marker rows re-use the previous row's company.
    return row("IGNORED", role, location, apply_url, age, closed,
               company_html="↳")


def section(category, rows):
    heading = SECTION_HEADINGS[category]
    return heading + "\n\n<table>\n" + "\n".join(rows) + "\n</table>\n\n"


def readme(sections):
    return "# Title\n\n" + "".join(sections)


class CategoryFilteringTests(unittest.TestCase):
    def test_only_wanted_categories_are_returned(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad")]),
            section("pm", [row("Globex", "PM New Grad")]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        categories = {e["category"] for e in result["entries"]}
        self.assertEqual(categories, {"swe"})
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["company"], "Acme")

    def test_multiple_wanted_categories(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad")]),
            section("pm", [row("Globex", "PM New Grad")]),
            section("hw", [row("Initech", "HW New Grad")]),
        ])
        result = parse(text, {"swe", "pm"}, max_days=7)
        companies = {e["company"] for e in result["entries"]}
        self.assertEqual(companies, {"Acme", "Globex"})


class AgeCutoffTests(unittest.TestCase):
    def test_rows_older_than_max_days_are_excluded(self):
        text = readme([
            section("swe", [
                row("Fresh", "SWE New Grad", age="0d"),
                row("Old", "SWE New Grad", age="10d"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Fresh"])

    def test_rows_exactly_at_cutoff_are_kept(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad", age="7d")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(len(result["entries"]), 1)

    def test_hour_based_age_is_converted_and_kept(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad", age="12h")])])
        result = parse(text, {"swe"}, max_days=1)
        self.assertEqual(len(result["entries"]), 1)

    def test_all_rows_excluded_by_days_returns_empty_entries(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad", age="30d")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"], [])


class ClosedRowTests(unittest.TestCase):
    def test_closed_row_excluded_and_counted(self):
        text = readme([
            section("swe", [
                row("Acme", "SWE New Grad", closed=True),
                row("Globex", "SWE New Grad"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Globex"])
        self.assertEqual(result["closed_excluded"], 1)

    def test_scanned_counts_closed_and_open_rows(self):
        text = readme([
            section("swe", [
                row("Acme", "SWE New Grad", closed=True),
                row("Globex", "SWE New Grad"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["scanned"], 2)


class FlagParsingTests(unittest.TestCase):
    def test_faang_flag(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad \U0001F525")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertTrue(result["entries"][0]["faang"])

    def test_adv_degree_flag(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad \U0001F393")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertTrue(result["entries"][0]["adv_degree"])

    def test_no_sponsor_flag(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad \U0001F6C2")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertTrue(result["entries"][0]["no_sponsor"])

    def test_us_citizen_flag(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad \U0001F1FA\U0001F1F8")])])
        result = parse(text, {"swe"}, max_days=7)
        self.assertTrue(result["entries"][0]["us_citizen"])

    def test_no_flags_all_false(self):
        text = readme([section("swe", [row("Acme", "SWE New Grad")])])
        result = parse(text, {"swe"}, max_days=7)
        e = result["entries"][0]
        self.assertFalse(e["faang"])
        self.assertFalse(e["adv_degree"])
        self.assertFalse(e["no_sponsor"])
        self.assertFalse(e["us_citizen"])


class DittoCompanyTests(unittest.TestCase):
    def test_ditto_row_inherits_previous_company(self):
        text = readme([
            section("swe", [
                row("Acme", "Role A", apply_url="https://apply.example.com/a"),
                ditto_row("Role B", apply_url="https://apply.example.com/b"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Acme", "Acme"])


class SinceBreakTests(unittest.TestCase):
    def test_since_match_stops_section_before_that_row(self):
        text = readme([
            section("swe", [
                row("New1", "SWE New Grad", apply_url="https://apply.example.com/n1"),
                row("New2", "SWE New Grad", apply_url="https://apply.example.com/n2"),
                row("SeenBefore", "SWE New Grad", apply_url="https://apply.example.com/seen"),
                row("Old", "SWE New Grad", apply_url="https://apply.example.com/old"),
            ]),
        ])
        since = {"swe": "https://apply.example.com/seen"}
        result = parse(text, {"swe"}, max_days=365, since=since)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["New1", "New2"])

    def test_since_with_no_matching_row_falls_back_to_days(self):
        text = readme([
            section("swe", [
                row("Fresh", "SWE New Grad", age="0d", apply_url="https://apply.example.com/f"),
                row("Old", "SWE New Grad", age="30d", apply_url="https://apply.example.com/o"),
            ]),
        ])
        since = {"swe": "https://apply.example.com/not-present"}
        result = parse(text, {"swe"}, max_days=7, since=since)
        companies = [e["company"] for e in result["entries"]]
        self.assertEqual(companies, ["Fresh"])

    def test_since_keyed_by_other_category_does_not_affect_this_one(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad", apply_url="https://apply.example.com/a")]),
        ])
        since = {"pm": "https://apply.example.com/a"}
        result = parse(text, {"swe"}, max_days=7, since=since)
        self.assertEqual(len(result["entries"]), 1)


class CategoryTopTests(unittest.TestCase):
    """`category_top` must reflect the true topmost row in the source,
    independent of the --days filter and the since-break — this was the
    exact bug that shipped once before."""

    def test_category_top_set_even_when_days_excludes_every_row(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad", age="30d",
                                 apply_url="https://apply.example.com/a")]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["category_top"], {"swe": "https://apply.example.com/a"})

    def test_category_top_ignores_closed_row_with_no_apply_url(self):
        text = readme([
            section("swe", [
                row("ClosedCo", "SWE New Grad", closed=True),
                row("OpenCo", "SWE New Grad", apply_url="https://apply.example.com/open"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        self.assertEqual(result["category_top"], {"swe": "https://apply.example.com/open"})

    def test_category_top_is_only_the_first_row(self):
        text = readme([
            section("swe", [
                row("First", "SWE New Grad", apply_url="https://apply.example.com/first"),
                row("Second", "SWE New Grad", apply_url="https://apply.example.com/second"),
                row("Third", "SWE New Grad", apply_url="https://apply.example.com/third"),
            ]),
        ])
        result = parse(text, {"swe"}, max_days=365)
        self.assertEqual(result["category_top"], {"swe": "https://apply.example.com/first"})

    def test_category_top_captured_before_since_break(self):
        # Even when the marker row itself is the topmost row (a no-op scan),
        # category_top must still be recorded as that same URL, not omitted
        # because the loop broke immediately.
        text = readme([
            section("swe", [
                row("Acme", "SWE New Grad", apply_url="https://apply.example.com/a"),
                row("Older", "SWE New Grad", apply_url="https://apply.example.com/b"),
            ]),
        ])
        since = {"swe": "https://apply.example.com/a"}
        result = parse(text, {"swe"}, max_days=365, since=since)
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["category_top"], {"swe": "https://apply.example.com/a"})

    def test_category_top_per_category_independent(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad", apply_url="https://apply.example.com/swe1")]),
            section("pm", [row("Globex", "PM New Grad", apply_url="https://apply.example.com/pm1")]),
        ])
        result = parse(text, {"swe", "pm"}, max_days=7)
        self.assertEqual(result["category_top"], {
            "swe": "https://apply.example.com/swe1",
            "pm": "https://apply.example.com/pm1",
        })

    def test_category_top_absent_for_unwanted_category(self):
        text = readme([
            section("swe", [row("Acme", "SWE New Grad")]),
            section("pm", [row("Globex", "PM New Grad")]),
        ])
        result = parse(text, {"swe"}, max_days=7)
        self.assertNotIn("pm", result["category_top"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
