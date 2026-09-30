#!/usr/bin/env python3
"""Tests for check_eligibility.py -- the closed-set degree/years extraction
schema, the structural fail-open guarantee for job-scan Step 2.6, and the
optional Jev feature gate.

Run: python3 -m pytest scripts/test_check_eligibility.py
No test makes a real network call: every Jev path mocks urlopen.
"""
import ast
import contextlib
import http.client
import inspect
import io
import itertools
import json
import os
import tempfile
import textwrap
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import check_eligibility as ce
from check_eligibility import (
    DEGREE_VALUES,
    ExtractionResult,
    REQUIREMENT_VALUES,
    build_jev_request,
    call_jev,
    compare_eligibility,
    extract,
    is_hard_degree_mismatch,
    jev_gate,
    parse_user_degree,
    regex_extract,
    sum_years_experience,
)

ORDER = {"none": 0, "BS": 1, "MS": 2, "PhD": 3}

# Synthetic sentinels only -- never real knowledge/ content in this file.
SENTINEL_SCHOOL = "Example State University"
SENTINEL_EMPLOYER = "Acme Corp"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_education(tmp: Path, degree_line: str = "degree: B.S. in Computer Engineering") -> Path:
    path = tmp / "education.md"
    write(path, f"---\nschool: {SENTINEL_SCHOOL}\n{degree_line}\n---\n\n# Education\n")
    return path


def make_experience_dir(tmp: Path, entries=()) -> Path:
    exp_dir = tmp / "experience"
    exp_dir.mkdir(parents=True, exist_ok=True)
    for i, (start, end) in enumerate(entries):
        write(exp_dir / f"job-{i}.md",
              f"---\ncompany: {SENTINEL_EMPLOYER} {i}\ntitle: Intern\nstart: {start}\nend: {end}\n---\n")
    return exp_dir


def fake_urlopen_response(payload) -> mock.MagicMock:
    cm = mock.MagicMock()
    cm.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    cm.__enter__.return_value = cm
    return cm


def jev_payload(degree="MS", degree_req="required", years="3", years_req="preferred", prob=0.95):
    def ans(choice):
        return {"type": "choice", "choice": choice, "probabilities": {choice: prob}}
    return {
        "model": "jev-1.13.0",
        "answers": {
            "degree": ans(degree),
            "degree_requirement": ans(degree_req),
            "years_required": ans(years),
            "years_requirement": ans(years_req),
        },
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


class TmpKnowledge(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.edu = make_education(self.tmp)  # BS candidate
        self.exp = make_experience_dir(self.tmp, [("Aug 2023", "Aug 2024")])

    def tearDown(self):
        self._td.cleanup()


# ---------------------------------------------------------------------------
# 1. The ONE path that can produce "ineligible"
# ---------------------------------------------------------------------------


class IneligiblePathTests(TmpKnowledge):
    def test_required_degree_above_users_with_ok_extraction_is_ineligible(self):
        ex = ExtractionResult(degree="PhD", degree_requirement="required", years_required=5,
                              years_requirement="required", extraction_ok=True, can_drop=True)
        status, reason = compare_eligibility(ex, self.edu, self.exp)
        self.assertEqual(status, "ineligible")
        self.assertIn("PhD", reason)

    def test_low_confidence_required_above_is_not_ineligible(self):
        ex = ExtractionResult(degree="PhD", degree_requirement="required", extraction_ok=True, can_drop=False)
        status, _ = compare_eligibility(ex, self.edu, self.exp)
        self.assertEqual(status, "unverified")

    def test_phd_candidate_phd_required_is_eligible(self):
        edu = make_education(self.tmp / "phd", degree_line="degree: Ph.D. in Computer Science")
        ex = ExtractionResult(degree="PhD", degree_requirement="required", extraction_ok=True, can_drop=True)
        self.assertEqual(compare_eligibility(ex, edu, self.exp)[0], "eligible")


# ---------------------------------------------------------------------------
# 2. Exhaustive fail-open sweep
# ---------------------------------------------------------------------------


class ExhaustiveFailOpenTests(TmpKnowledge):
    def test_ineligible_only_on_the_one_allowed_path(self):
        seen_ineligible = 0
        for degree, dreq, years, yreq, ok, can_drop, nj in itertools.product(
            DEGREE_VALUES, REQUIREMENT_VALUES, [None, 0, 1, 3, 5, 10, 20], REQUIREMENT_VALUES,
            [True, False], [True, False], [True, False],
        ):
            ex = ExtractionResult(degree=degree, degree_requirement=dreq, years_required=years,
                                  years_requirement=yreq, extraction_ok=ok, can_drop=can_drop, needs_judgment=nj)
            status, _ = compare_eligibility(ex, self.edu, self.exp)
            self.assertIn(status, ce.STATUSES)
            allowed = ok and can_drop and dreq == "required" and degree in ORDER and ORDER[degree] > ORDER["BS"]
            if status == "ineligible":
                seen_ineligible += 1
                self.assertTrue(allowed, f"{degree} {dreq} {years} {yreq} ok={ok} can_drop={can_drop}")
            else:
                self.assertFalse(allowed and not nj, f"missed drop: {degree} {dreq} ok={ok}")
        self.assertGreater(seen_ineligible, 0)

    def test_unknown_fields_are_unverified(self):
        self.assertEqual(compare_eligibility(ExtractionResult(extraction_ok=True, can_drop=True),
                                             self.edu, self.exp)[0], "unverified")

    def test_failed_extraction_is_unverified_even_with_scary_degree(self):
        ex = ExtractionResult(degree="PhD", degree_requirement="required", extraction_ok=False, can_drop=True)
        status, reason = compare_eligibility(ex, self.edu, self.exp)
        self.assertEqual(status, "unverified")
        self.assertIn("fail-open", reason)

    def test_missing_education_is_unverified(self):
        ex = ExtractionResult(degree="PhD", degree_requirement="required", extraction_ok=True, can_drop=True)
        self.assertEqual(compare_eligibility(ex, self.tmp / "nope.md", self.exp)[0], "unverified")

    def test_unparsable_education_degree_is_unverified(self):
        edu = make_education(self.tmp / "amb", degree_line="degree: something ambiguous")
        ex = ExtractionResult(degree="PhD", degree_requirement="required", extraction_ok=True, can_drop=True)
        self.assertEqual(compare_eligibility(ex, edu, self.exp)[0], "unverified")

    def test_preferred_only_is_unverified(self):
        ex = ExtractionResult(degree="PhD", degree_requirement="preferred", extraction_ok=True, can_drop=True)
        status, reason = compare_eligibility(ex, self.edu, self.exp)
        self.assertEqual(status, "unverified")
        self.assertIn("preferred", reason)


# ---------------------------------------------------------------------------
# 3. Years can never drop -- behavioral sweep + AST structure check
# ---------------------------------------------------------------------------


class YearsNeverDropsTests(TmpKnowledge):
    def test_years_alone_never_produces_ineligible(self):
        safe = [("unknown", "unknown"), ("unknown", "required"), ("none", "required"), ("BS", "required"),
                ("BS", "preferred"), ("MS", "preferred"), ("PhD", "preferred"), ("PhD", "unknown")]
        for (degree, dreq), years, yreq in itertools.product(
            safe, [None, 0, 1, 2, 5, 10, 20, 50, 999, -1], REQUIREMENT_VALUES
        ):
            ex = ExtractionResult(degree=degree, degree_requirement=dreq, years_required=years,
                                  years_requirement=yreq, extraction_ok=True, can_drop=True)
            self.assertNotEqual(compare_eligibility(ex, self.edu, self.exp)[0], "ineligible")

    def test_drop_predicate_takes_no_years_data(self):
        params = set(inspect.signature(is_hard_degree_mismatch).parameters)
        self.assertEqual(params, {"degree", "degree_requirement", "extraction_ok", "can_drop", "user_degree"})
        tree = ast.parse(textwrap.dedent(inspect.getsource(is_hard_degree_mismatch)))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(any("year" in s.lower() for s in names | attrs))

    def test_only_return_ineligible_is_guarded_by_the_drop_predicate(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(compare_eligibility)))
        guarded = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                for child in node.body:
                    if (isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple)
                            and isinstance(child.value.elts[0], ast.Constant)
                            and child.value.elts[0].value == "ineligible"):
                        guarded.append(node)
        all_ineligible = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == "ineligible"
        ]
        self.assertEqual(len(all_ineligible), 1, "exactly one 'ineligible' literal in compare_eligibility")
        self.assertEqual(len(guarded), 1)
        test = guarded[0].test
        self.assertIsInstance(test, ast.Call)
        self.assertEqual(test.func.id, "is_hard_degree_mismatch")
        arg_src = " ".join(ast.dump(a) for a in test.args + [k.value for k in test.keywords])
        self.assertNotIn("year", arg_src.lower())


# ---------------------------------------------------------------------------
# 4. Regex pre-filter fixtures + reviewer regressions
# ---------------------------------------------------------------------------


class RegexPreFilterTests(TmpKnowledge):
    def assertRegex_(self, text, degree, req):
        r = regex_extract(text)
        self.assertIsNotNone(r, text)
        self.assertEqual((r.degree, r.degree_requirement), (degree, req), text)
        self.assertTrue(r.extraction_ok)
        return r

    def test_bachelors_required_with_field(self):
        r = self.assertRegex_("Bachelor's degree in Computer Science or related field required.", "BS", "required")
        self.assertIn("Bachelor", r.source_span)

    def test_curly_apostrophe(self):
        self.assertRegex_("Bachelor’s degree in Computer Science or related field.", "BS", "required")

    def test_phd_required(self):
        self.assertRegex_("PhD required in Machine Learning.", "PhD", "required")
        self.assertRegex_("Ph.D. in Physics required.", "PhD", "required")

    def test_requires_a_masters(self):
        self.assertRegex_("This role requires a Master's degree in Statistics.", "MS", "required")

    def test_ms_or_phd_preferred(self):
        self.assertRegex_("MS or PhD preferred, BS accepted.", "MS", "preferred")

    def test_required_and_preferred_clauses(self):
        self.assertRegex_("Bachelor's degree in CS is required. Master's degree preferred.", "BS", "required")
        self.assertRegex_("BS required, MS preferred.", "BS", "required")

    def test_no_degree_required(self):
        self.assertRegex_("No degree required. We value hands-on skills.", "none", "required")

    def test_years(self):
        r = regex_extract("5+ years of experience required.")
        self.assertEqual((r.years_required, r.years_requirement), (5, "required"))
        r = regex_extract("3+ years of experience preferred.")
        self.assertEqual((r.years_required, r.years_requirement), (3, "preferred"))
        r = regex_extract("0-1 years of professional software development experience.")
        self.assertEqual(r.years_required, 0)

    def test_no_degree_language_is_confidently_unknown(self):
        r = self.assertRegex_("We need someone sharp with strong communication skills.", "unknown", "unknown")
        self.assertIsNone(r.source_span)

    def test_ambiguous_phrasings_defer(self):
        for text in (
            "Ideal candidates bring doctoral research rigor to ambiguous problems.",
            "A Bachelor's or Master's degree in CS, or equivalent work experience.",
            "Preferred Qualifications\nMaster's degree in Computer Science",
            "Master's degree in Computer Science.",  # implicit-only above BS
            "Bachelor's and Master's degrees in CS.",
            "Bachelor's degree required; preferred candidates hold a PhD required for senior track.",
        ):
            self.assertIsNone(regex_extract(text), text)

    def test_ms_office_is_not_a_degree(self):
        self.assertRegex_("Proficiency with MS Office required.", "unknown", "unknown")

    # --- Phase 5 reviewer regressions: each previously returned a confident
    # --- false "ineligible" against a BS education.md.
    def _status(self, text):
        return compare_eligibility(extract(text, jev_enabled=False), self.edu, self.exp)

    def test_review_regression_list_takes_lowest(self):
        self.assertRegex_("Bachelor's, Master's, or PhD degree in Computer Science", "BS", "required")
        self.assertEqual(self._status("Bachelor's, Master's, or PhD degree in Computer Science")[0], "eligible")

    def test_review_regression_slash_list(self):
        self.assertRegex_("BS/MS/PhD in Computer Science", "BS", "required")
        self.assertEqual(self._status("BS/MS/PhD in Computer Science")[0], "eligible")

    def test_review_regression_bachelors_or_masters(self):
        self.assertRegex_("Bachelor's or Master's degree in CS", "BS", "required")
        self.assertEqual(self._status("Bachelor's or Master's degree in CS")[0], "eligible")

    def test_review_regression_systems_is_not_ms(self):
        self.assertRegex_("distributed systems is required", "unknown", "unknown")
        self.assertEqual(self._status("distributed systems is required")[0], "unverified")

    def test_review_regression_algorithms_is_not_ms(self):
        self.assertRegex_("algorithms in Python", "unknown", "unknown")
        self.assertEqual(self._status("algorithms in Python")[0], "unverified")

    def test_regex_confident_match_never_calls_jev(self):
        with mock.patch("check_eligibility.call_jev") as mocked:
            r = extract("Bachelor's degree in Computer Science or related field required.", jev_enabled=True)
            mocked.assert_not_called()
        self.assertEqual(r.path, "regex")

    def test_empty_jd_is_unverified_not_unknown(self):
        with mock.patch("check_eligibility.call_jev") as mocked:
            r = extract("   \n", jev_enabled=True)
            mocked.assert_not_called()
        self.assertFalse(r.extraction_ok)
        self.assertEqual(compare_eligibility(r, self.edu, self.exp)[0], "unverified")


# ---------------------------------------------------------------------------
# 5. Jev path -- network always mocked
# ---------------------------------------------------------------------------

AMBIGUOUS_PHD_JD = "Ideal candidates bring doctoral research rigor to ambiguous problems."
AMBIGUOUS_MS_JD = "Candidates holding a Master's are encouraged; see the Master's track details."


class JevPathTests(TmpKnowledge):
    def _call(self, payload, jd=AMBIGUOUS_MS_JD):
        with mock.patch("check_eligibility.urllib.request.urlopen", return_value=fake_urlopen_response(payload)):
            return call_jev(jd, api_key="fake-key")

    def test_request_never_contains_knowledge_content(self):
        captured = {}

        def spy(req, timeout=None):
            captured["body"] = req.data.decode("utf-8")
            return fake_urlopen_response(jev_payload())

        with mock.patch("check_eligibility.urllib.request.urlopen", side_effect=spy):
            ex = extract(AMBIGUOUS_MS_JD, jev_enabled=True, api_key="fake-key")
            compare_eligibility(ex, self.edu, self.exp)
        body = json.loads(captured["body"])
        self.assertEqual(body["state"], AMBIGUOUS_MS_JD)
        for sentinel in (SENTINEL_SCHOOL, SENTINEL_EMPLOYER, "Computer Engineering"):
            self.assertNotIn(sentinel, captured["body"])

    def test_successful_response_is_parsed(self):
        r = self._call(jev_payload())
        self.assertTrue(r.extraction_ok)
        self.assertTrue(r.can_drop)
        self.assertEqual((r.degree, r.degree_requirement, r.years_required, r.years_requirement),
                         ("MS", "required", 3, "preferred"))
        self.assertIsNone(r.source_span)

    def test_not_stated_years_maps_to_none(self):
        r = self._call(jev_payload(degree="unknown", degree_req="unknown", years="not_stated", years_req="unknown"))
        self.assertTrue(r.extraction_ok)
        self.assertIsNone(r.years_required)

    def test_uncorroborated_degree_is_extraction_failure(self):
        # Jev says PhD but the JD has no PhD/doctorate token.
        r = self._call(jev_payload(degree="PhD"), jd=AMBIGUOUS_MS_JD)
        self.assertFalse(r.extraction_ok)
        self.assertEqual(compare_eligibility(r, self.edu, self.exp)[0], "unverified")

    def test_low_probability_cannot_drop(self):
        r = self._call(jev_payload(degree="PhD", prob=0.55), jd=AMBIGUOUS_PHD_JD)
        self.assertTrue(r.extraction_ok)
        self.assertFalse(r.can_drop)
        self.assertEqual(compare_eligibility(r, self.edu, self.exp)[0], "unverified")

    def test_missing_probabilities_cannot_drop(self):
        payload = jev_payload(degree="PhD")
        for a in payload["answers"].values():
            a.pop("probabilities")
        r = self._call(payload, jd=AMBIGUOUS_PHD_JD)
        self.assertFalse(r.can_drop)

    def test_confident_corroborated_phd_can_drop(self):
        r = self._call(jev_payload(degree="PhD"), jd=AMBIGUOUS_PHD_JD)
        self.assertEqual(compare_eligibility(r, self.edu, self.exp)[0], "ineligible")

    def test_malformed_and_error_responses_fail_open(self):
        cases = [
            b'{"answers": {"degree": {"choice": "NOT_AN_ENUM"}}}',
            b'{"answers": {}}',
            b"not json{{{",
        ]
        for raw in cases:
            r = self._call(raw)
            self.assertFalse(r.extraction_ok, raw)
        for exc in (urllib.error.URLError("timed out"), http.client.IncompleteRead(b""),
                    http.client.BadStatusLine("x"), TimeoutError()):
            with mock.patch("check_eligibility.urllib.request.urlopen", side_effect=exc):
                r = call_jev("some JD", api_key="fake-key")
            self.assertFalse(r.extraction_ok, exc)
            self.assertEqual(r.degree, "unknown")

    def test_no_key_fails_closed_to_unverified(self):
        with mock.patch("check_eligibility._load_api_key", return_value=None):
            self.assertFalse(call_jev("some JD").extraction_ok)


class CorroborationSynonymTests(TmpKnowledge):
    SYNONYMS = {
        "MS": ["Master's", "Masters", "MS", "M.S.", "MSc", "M.Eng", "graduate degree",
               "graduate-level", "advanced degree"],
        "PhD": ["PhD", "Ph.D.", "doctorate", "doctoral", "doctoral degree"],
        "BS": ["Bachelor's", "Bachelors", "BS", "B.S.", "BSc", "B.Eng", "undergraduate degree", "4-year degree"],
    }

    def test_each_synonym_corroborates_its_level_only(self):
        for level, words in self.SYNONYMS.items():
            for word in words:
                jd = f"Candidates should hold a {word} in a quantitative field."
                self.assertTrue(ce.degree_is_evidenced(level, jd), f"{word} -> {level}")
                for other in set(self.SYNONYMS) - {level}:
                    self.assertFalse(ce.degree_is_evidenced(other, jd), f"{word} wrongly -> {other}")

    def test_graduate_never_corroborates_phd(self):
        for jd in ("graduate degree required", "graduate-level coursework", "an advanced degree"):
            self.assertFalse(ce.degree_is_evidenced("PhD", jd), jd)

    def test_word_boundaries(self):
        for jd in ("distributed systems", "algorithms in Python", "undergraduate research", "a recent graduate"):
            self.assertFalse(ce.degree_is_evidenced("MS", jd), jd)

    def test_real_live_payload_corroborates_ms_and_stays_non_dropping(self):
        # Verbatim shape of the 2026-09-29 live jev-1.13.0 response (synthetic JD).
        raw = {"model": "jev-1.13.0", "answers": {
            "degree": {"type": "choice", "choice": "MS", "confidence": 0.85,
                       "probabilities": {"MS": 0.88, "PhD": 0.01, "none": 0.0, "unknown": 0.11, "BS": 0.0}},
            "degree_requirement": {"type": "choice", "choice": "preferred", "confidence": 0.89,
                                   "probabilities": {"preferred": 0.93, "required": 0.0, "unknown": 0.07}},
            "years_required": {"type": "choice", "choice": "not_stated", "confidence": 1.0,
                               "probabilities": {"not_stated": 1.0}},
            "years_requirement": {"type": "choice", "choice": "unknown", "confidence": 0.91,
                                  "probabilities": {"preferred": 0.06, "required": 0.0, "unknown": 0.94}}},
            "usage": {"input_tokens": 934, "output_tokens": 242}}
        jd = ("Ideal candidates will have completed graduate-level coursework or a doctorate "
              "in a quantitative field.")
        with mock.patch("check_eligibility.urllib.request.urlopen", return_value=fake_urlopen_response(raw)):
            r = call_jev(jd, api_key="fake-key")
        self.assertTrue(r.extraction_ok)
        self.assertEqual((r.degree, r.degree_requirement, r.path), ("MS", "preferred", "jev"))
        self.assertTrue(r.can_drop)
        self.assertIn(compare_eligibility(r, self.edu, self.exp)[0], ("eligible", "unverified"))


# ---------------------------------------------------------------------------
# 6. Feature gate
# ---------------------------------------------------------------------------


def run_main(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ce.main(args)
    return code, out.getvalue(), err.getvalue()


class FeatureGateTests(TmpKnowledge):
    def setUp(self):
        super().setUp()
        self.jds = {
            "regex_bs": "Bachelor's degree in Computer Science or related field required.",
            "regex_phd": "PhD required in Machine Learning.",
            "ambiguous": AMBIGUOUS_PHD_JD,
            "empty": "",
        }
        self.paths = {}
        for name, text in self.jds.items():
            p = self.tmp / f"{name}.txt"
            p.write_text(text)
            self.paths[name] = str(p)

    def _main(self, extra, env):
        args = list(self.paths.values()) + ["--json", "--education", str(self.edu),
                                            "--experience-dir", str(self.exp)] + extra
        with mock.patch.dict(os.environ, env, clear=False):
            return run_main(args)

    def test_gate_reasons(self):
        with mock.patch("check_eligibility._load_api_key", return_value=None), \
                mock.patch.dict(os.environ, {"RESUME_BUILDER_JEV": ""}):
            self.assertEqual(jev_gate(), (False, "TYPESAFE_API_KEY not set"))
        with mock.patch("check_eligibility._load_api_key", return_value="k"), \
                mock.patch.dict(os.environ, {"RESUME_BUILDER_JEV": "off"}):
            self.assertFalse(jev_gate()[0])
        with mock.patch("check_eligibility._load_api_key", return_value="k"), \
                mock.patch.dict(os.environ, {"RESUME_BUILDER_JEV": ""}):
            self.assertTrue(jev_gate()[0])
            self.assertFalse(jev_gate(no_jev_flag=True)[0])

    def _assert_gate_off(self, extra, env):
        with mock.patch("check_eligibility.urllib.request.urlopen",
                        side_effect=AssertionError("network used with Jev off")) as net:
            code, out, err = self._main(extra, env)
            net.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(err.count("Jev disabled"), 1, "exactly one notice, not one per posting")
        data = json.loads(out)
        self.assertFalse(data["jev_enabled"])
        status = {Path(r["jd_path"]).stem: r["status"] for r in data["results"]}
        self.assertEqual(status, {"regex_bs": "eligible", "regex_phd": "ineligible",
                                  "ambiguous": "needs_judgment", "empty": "unverified"})

    def test_no_key_gate_off(self):
        with mock.patch("check_eligibility._load_api_key", return_value=None):
            self._assert_gate_off([], {"RESUME_BUILDER_JEV": ""})

    def test_env_off_with_key(self):
        with mock.patch("check_eligibility._load_api_key", return_value="k"):
            self._assert_gate_off([], {"RESUME_BUILDER_JEV": "off"})

    def test_no_jev_flag_with_key(self):
        with mock.patch("check_eligibility._load_api_key", return_value="k"):
            self._assert_gate_off(["--no-jev"], {"RESUME_BUILDER_JEV": ""})

    def test_gate_on_calls_jev_only_for_ambiguous(self):
        with mock.patch("check_eligibility._load_api_key", return_value="k"), \
                mock.patch("check_eligibility.urllib.request.urlopen",
                           return_value=fake_urlopen_response(jev_payload(degree="PhD"))) as net:
            code, out, err = self._main([], {"RESUME_BUILDER_JEV": ""})
        self.assertEqual(net.call_count, 1)
        self.assertEqual(err, "")
        status = {Path(r["jd_path"]).stem: (r["status"], r["path"]) for r in json.loads(out)["results"]}
        self.assertEqual(status["ambiguous"], ("ineligible", "jev"))
        self.assertEqual(status["regex_bs"], ("eligible", "regex"))

    def test_gate_on_call_failure_is_unverified(self):
        with mock.patch("check_eligibility._load_api_key", return_value="k"), \
                mock.patch("check_eligibility.urllib.request.urlopen",
                           side_effect=http.client.IncompleteRead(b"")):
            _, out, _ = self._main([], {"RESUME_BUILDER_JEV": ""})
        status = {Path(r["jd_path"]).stem: r["status"] for r in json.loads(out)["results"]}
        self.assertEqual(status["ambiguous"], "unverified")

    def test_per_posting_exception_is_unverified(self):
        with mock.patch("check_eligibility._load_api_key", return_value=None), \
                mock.patch("check_eligibility.compare_eligibility", side_effect=RuntimeError("boom")):
            code, out, _ = self._main([], {"RESUME_BUILDER_JEV": ""})
        self.assertEqual(code, 0)
        self.assertTrue(all(r["status"] == "unverified" for r in json.loads(out)["results"]))

    def test_top_level_exception_is_nonzero_and_unverified(self):
        with mock.patch("check_eligibility.jev_gate", side_effect=RuntimeError("boom")):
            code, out, _ = self._main([], {})
        self.assertNotEqual(code, 0)
        self.assertTrue(all(r["status"] == "unverified" for r in json.loads(out)["results"]))


# ---------------------------------------------------------------------------
# Local knowledge/ parsing helpers
# ---------------------------------------------------------------------------


class LocalParsingTests(unittest.TestCase):
    def test_parse_user_degree(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(parse_user_degree(make_education(Path(td))), "BS")
            self.assertIsNone(parse_user_degree(Path(td) / "missing.md"))

    def test_sum_years_experience(self):
        with tempfile.TemporaryDirectory() as td:
            exp = make_experience_dir(Path(td), [("Aug 2023", "Aug 2024"), ("May 2025", "Aug 2025")])
            self.assertAlmostEqual(sum_years_experience(exp), round(15 / 12.0, 1), places=2)
            self.assertIsNone(sum_years_experience(Path(td) / "nope"))


if __name__ == "__main__":
    unittest.main()
