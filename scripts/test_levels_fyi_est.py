"""Tests for levels_fyi_est: slug guesses, description parsing, 404 fallback."""
import unittest

import levels_fyi_est as lf


def page(desc):
    return f'<html><head><meta property="og:description" content="{desc}"/></head></html>'


class SlugTests(unittest.TestCase):
    def test_basic_and_suffix_variants(self):
        c = lf.slug_candidates("Acme Holdings Inc.")
        self.assertEqual(c[0], "acme-holdings-inc")
        self.assertIn("acme-holdings", c)
        self.assertNotIn("acme", c)  # first-word guesses risk matching a different company

    def test_ampersand(self):
        self.assertIn("church-and-dwight", lf.slug_candidates("Church & Dwight"))

    def test_the_prefix_and_parenthetical(self):
        c = lf.slug_candidates("The Walt Disney Company (US)")
        self.assertIn("walt-disney", c)

    def test_no_duplicates(self):
        c = lf.slug_candidates("Snap")
        self.assertEqual(c, ["snap"])


class ParseTests(unittest.TestCase):
    def test_us_range(self):
        r = lf.parse_description(page(
            "The average Software Engineer total compensation in United States at Viant ranges from $133K to $189K per year. View"))
        self.assertEqual(r["est_text"], "~$133K-$189K (est.)")

    def test_us_level_range_uses_entry(self):
        r = lf.parse_description(page(
            "Software Engineer compensation in United States at Amazon ranges from $182K per year for L4 to $1.43M per year for L10. The median"))
        self.assertEqual(r["est_text"], "~$182K (est.)")

    def test_median_only(self):
        r = lf.parse_description(page(
            "The median Software Engineer compensation package at iPipeline in United States totals $75K per year."))
        self.assertEqual(r["est_text"], "~$75K (est.)")

    def test_foreign_currency_kept_and_labelled(self):
        r = lf.parse_description(page(
            "The average Software Engineer total compensation in Canada at Intact ranges from CA$113K to CA$164K per year."))
        self.assertEqual(r["est_text"], "~CA$113K-CA$164K (est., Canada)")

    def test_euro_and_czk(self):
        r = lf.parse_description(page(
            "Software Engineer compensation in France at Thales ranges from €40K per year for LR6 to €38.5K per year for LR12."))
        self.assertEqual(r["est_text"], "~€40K (est., France)")
        r = lf.parse_description(page(
            "The median Software Engineer compensation package at Jamf in Czech Republic totals CZK 1.12M per year."))
        self.assertEqual(r["est_text"], "~CZK 1.12M (est., Czech Republic)")

    def test_no_figure_page(self):
        r = lf.parse_description(page("View the base salary, stock, and bonus breakdowns for TerraClear's total compensation packages."))
        self.assertIsNone(r["est_text"])

    def test_garbage_and_empty(self):
        self.assertIsNone(lf.parse_description("")["est_text"])
        self.assertIsNone(lf.parse_description("<html>no meta</html>")["est_text"])


class LookupTests(unittest.TestCase):
    def test_slug_fallback_on_404(self):
        calls = []

        def fetcher(url, timeout, ua):
            calls.append(url)
            if "/acme-holdings-inc/" in url:
                return 404, "text/html", b""
            if "/acme-holdings/" in url:
                return 200, "text/html", page(
                    "Software Engineer compensation in United States at Acme Holdings ranges from $90K per year for L1 to $200K per year for L5.").encode()
            return 404, "text/html", b""

        res = lf.lookup(["Acme Holdings Inc"], fetcher=fetcher)
        self.assertEqual(res["Acme Holdings Inc"]["est_text"], "~$90K (est.)")
        self.assertEqual(res["Acme Holdings Inc"]["slug"], "acme-holdings")
        self.assertEqual(len(calls), 2)

    def test_all_404_is_none_not_crash(self):
        res = lf.lookup(["Nowhere Corp"], fetcher=lambda u, t, ua: (404, "text/html", b""))
        self.assertIsNone(res["Nowhere Corp"]["est_text"])

    def test_fetch_exception_is_none(self):
        def boom(u, t, ua):
            raise OSError("down")
        res = lf.lookup(["Acme"], fetcher=boom)
        self.assertIsNone(res["Acme"]["est_text"])


if __name__ == "__main__":
    unittest.main()
