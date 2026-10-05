#!/usr/bin/env python3
"""Tests for extract_comp.py -- deterministic stated-comp extraction for
job-scan Step 3. Regex-only; no test touches the network.

Run: python3 -m pytest scripts/test_extract_comp.py
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

import extract_comp as ec
from extract_comp import extract_comp


def base(text):
    return extract_comp(text)["base"]


class BaseRangeTests(unittest.TestCase):
    def test_comma_range(self):
        b = base("The base salary range is $120,000 - $150,000 per year.")
        self.assertEqual((b["low"], b["high"], b["period"]), (120000, 150000, "yr"))
        self.assertEqual(b["raw"], "$120,000 - $150,000")
        self.assertFalse(b["multi"])

    def test_k_range_comp_text(self):
        r = extract_comp("Pay range: $134k-$168k. Includes bonus and equity.")
        self.assertEqual((r["base"]["low"], r["base"]["high"]), (134000, 168000))
        self.assertEqual(r["comp_text"], "$134k-$168k + bonus + equity")
        self.assertEqual(r["status"], "stated")

    def test_bare_k_range_with_context(self):
        b = base("Salary: 134k-168k USD depending on location.")
        self.assertEqual((b["low"], b["high"]), (134000, 168000))

    def test_bare_k_range_without_context_ignored(self):
        self.assertIsNone(base("We have 10k-20k users."))

    def test_en_dash_and_to(self):
        self.assertEqual(base("Base pay: $90,000 – $110,000 annually")["high"], 110000)
        self.assertEqual(base("Salary range $90,000 to $110,000")["low"], 90000)

    def test_usd_suffix(self):
        b = base("Annual salary range: $100,000 USD - $130,000 USD")
        self.assertEqual((b["low"], b["high"]), (100000, 130000))
        self.assertEqual(ec._fmt_raw(b), "$100,000 USD - $130,000")

    def test_usd_and_period_inside_range(self):
        r = extract_comp("The salary range is USD $80,000.00/Yr. - USD $115,000/Yr. Actual pay varies.")
        b = r["base"]
        self.assertEqual((b["low"], b["high"], b["period"]), (80000, 115000, "yr"))
        self.assertFalse(b["multi"])
        self.assertEqual(b["raw"], "$80,000.00/Yr. - USD $115,000")

    def test_single_figure_with_period(self):
        b = base("Compensation: $140,000 per year")
        self.assertEqual((b["low"], b["high"]), (140000, 140000))


class HourlyTests(unittest.TestCase):
    def test_hourly_annualized_separately(self):
        r = extract_comp("The pay is $60/hr for this role.")
        b = r["base"]
        self.assertEqual((b["low"], b["high"], b["period"]), (60, 60, "hr"))
        self.assertEqual(b["raw"], "$60")
        self.assertEqual(b["annualized_low"], 124800)
        self.assertEqual(r["comp_text"], "$60/hr")

    def test_hourly_range_per_hour(self):
        b = base("Hourly rate: $45 - $60 per hour")
        self.assertEqual((b["low"], b["high"], b["period"]), (45, 60, "hr"))
        self.assertEqual(b["annualized_high"], 124800)

    def test_yearly_has_no_annualized(self):
        self.assertNotIn("annualized_low", base("Salary $100,000 - $120,000/yr"))


class MultiRangeTests(unittest.TestCase):
    def test_location_tiers_envelope(self):
        t = ("Pay range for NYC: $150,000 - $180,000 per year. "
             "Pay range for other US locations: $130,000 - $160,000 per year.")
        b = base(t)
        self.assertTrue(b["multi"])
        self.assertEqual((b["low"], b["high"]), (130000, 180000))
        self.assertEqual(b["raw"], "$150,000 - $180,000")

    def test_repeated_identical_range_not_multi(self):
        t = "Salary $100,000 - $120,000 per year. Again: base salary $100,000 - $120,000."
        self.assertFalse(base(t)["multi"])


class FalsePositiveTests(unittest.TestCase):
    def test_company_funding_ignored(self):
        self.assertIsNone(base("We raised $500M in Series C funding."))
        self.assertIsNone(base("The company has raised $120,000,000 and is valued at $2B."))
        self.assertIsNone(base("Backed by $250 million in funding."))

    def test_signon_relocation_stipend_ignored(self):
        for t in (
            "Sign-on bonus of $10,000 available.",
            "Relocation assistance up to $15,000.",
            "A $5,000 relocation allowance is provided.",
            "Monthly housing stipend: $2,000",
            "Annual learning stipend of $1,500.",
            "Employee referral bonus: $5,000",
        ):
            self.assertIsNone(base(t), t)

    def test_salary_survives_next_to_signon(self):
        t = "Base salary: $120,000 - $140,000. Sign-on bonus of $10,000 available."
        b = base(t)
        self.assertEqual((b["low"], b["high"]), (120000, 140000))

    def test_unrelated_dollar_amounts_without_context(self):
        self.assertIsNone(base("Our product costs $20,000 - $30,000 for enterprise customers."))
        self.assertIsNone(base("Win a $100 gift card."))

    def test_implausible_values(self):
        self.assertIsNone(base("Salary: $5 - $10 per year"))
        self.assertIsNone(base("Base salary $9,000,000 - $9,500,000"))

    def test_million_suffix_not_salary(self):
        self.assertIsNone(base("Compensation pool: $2M - $3M"))


class BonusEquityTests(unittest.TestCase):
    def test_bonus_only(self):
        r = extract_comp("Eligible for an annual performance bonus.")
        self.assertTrue(r["bonus"])
        self.assertFalse(r["equity"])
        self.assertEqual(r["comp_text"], "bonus")
        self.assertEqual(r["status"], "stated")
        self.assertIsNone(r["base"])

    def test_rsu_and_stock(self):
        self.assertTrue(extract_comp("Your package includes sign-on payments and RSUs.")["equity"])
        self.assertTrue(extract_comp("Stock options and a 401(k) match.")["equity"])
        self.assertTrue(extract_comp("Total compensation includes base, bonus + equity.")["equity"])

    def test_dei_equity_not_comp(self):
        r = extract_comp("We value diversity, equity and inclusion at our company.")
        self.assertFalse(r["equity"])
        self.assertEqual(r["status"], "none")

    def test_finance_equity_not_comp(self):
        for t in (
            "You will trade equity derivatives and equity markets.",
            "Experience with private equity or equity research.",
            "Knowledge of the stock market and stock exchange rules.",
        ):
            self.assertFalse(extract_comp(t)["equity"], t)

    def test_referral_and_negated_bonus(self):
        self.assertFalse(extract_comp("Generous employee referral bonus program.")["bonus"])
        self.assertFalse(extract_comp("This role has no bonus.")["bonus"])

    def test_no_comp_language(self):
        r = extract_comp("Build backend services in Go. Collaborate with the team.")
        self.assertEqual(
            (r["base"], r["bonus"], r["equity"], r["comp_text"], r["status"]),
            (None, False, False, None, "none"),
        )

    def test_full_comp_text(self):
        t = "Base salary: $134,000 - $168,000 + bonus + RSUs."
        self.assertEqual(extract_comp(t)["comp_text"], "$134,000 - $168,000 + bonus + equity")

    def test_deterministic(self):
        t = "Pay range $100k-$120k plus bonus and equity."
        self.assertEqual(extract_comp(t), extract_comp(t))


class CliTests(unittest.TestCase):
    def _run(self, paths):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ec.main([*paths, "--json"])
        return rc, json.loads(out.getvalue())

    def test_json_shape_and_robustness(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "good.txt")
            empty = os.path.join(d, "empty.txt")
            missing = os.path.join(d, "missing.txt")
            with open(good, "w") as fh:
                fh.write("Salary range $100,000 - $120,000 per year. Equity included.")
            open(empty, "w").close()
            rc, data = self._run([good, empty, missing])
        self.assertEqual(rc, 0)
        res = data["results"]
        self.assertEqual([r["jd_path"] for r in res], [good, empty, missing])
        self.assertEqual(res[0]["status"], "stated")
        self.assertEqual(res[0]["comp_text"], "$100,000 - $120,000 + equity")
        for r in res[1:]:
            self.assertEqual(r["status"], "none")
            self.assertIsNone(r["base"])
            self.assertFalse(r["bonus"] or r["equity"])
        self.assertEqual(
            set(res[0]), {"jd_path", "base", "bonus", "equity", "comp_text", "status"}
        )

    def test_binary_garbage_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bin.txt")
            with open(p, "wb") as fh:
                fh.write(b"\xff\xfe\x00\x01 salary \x80\x81")
            rc, data = self._run([p])
        self.assertEqual(rc, 0)
        self.assertEqual(data["results"][0]["status"], "none")

    def test_tiered_ranges_comp_text_uses_envelope(self):
        text = "Pay Range: Level 1: $125,000.00 - $165,000.00 Level 2: $145,000.00 - $200,000.00"
        r = extract_comp(text)
        self.assertTrue(r["base"]["multi"])
        self.assertEqual(r["comp_text"], "$125k-$200k")

    def test_html_residue_between_figures(self):
        text = '<p>Compensation Range:</p><p><bdi>$85,600</bdi> - <bdi>$128,400</bdi> <bdi>USD</bdi></p>'
        r = extract_comp(text)
        self.assertEqual(r["base"]["low"], 85600)
        self.assertEqual(r["base"]["high"], 128400)
        self.assertEqual(r["status"], "stated")

    def test_html_entities_and_unicode_escapes(self):
        text = "Pay range:&nbsp;\\u003cb\\u003e$135,000 - $230,000/per year\\u003c/b\\u003e"
        r = extract_comp(text)
        self.assertEqual((r["base"]["low"], r["base"]["high"]), (135000, 230000))

    def test_internal_equity_is_not_stock(self):
        r = extract_comp("Pay depends on skills, experience, internal equity, and market data.")
        self.assertFalse(r["equity"])

    def test_added_bonus_phrase_is_not_comp(self):
        r = extract_comp("Added bonus if you have MLOps exposure. Bonus points for Go.")
        self.assertFalse(r["bonus"])

    def test_espp_alone_is_not_equity(self):
        r = extract_comp("Benefits: 401(k) matching, employee stock purchase program (ESPP).")
        self.assertFalse(r["equity"])

    def test_form_option_rates_are_not_pay(self):
        r = extract_comp("Desired Rate per hour ($) * -- No answer -- $50-$70 $70-$80 $100-$120")
        self.assertIsNone(r["base"])

    def test_between_and_range_renders_as_dash(self):
        r = extract_comp("The salary range is between $175,000 and $225,000 per year.")
        self.assertEqual(r["comp_text"], "$175,000 - $225,000")


if __name__ == "__main__":
    unittest.main()
