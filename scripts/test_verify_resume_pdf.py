#!/usr/bin/env python3
"""Tests for verify_resume_pdf.py.

Run: python3 scripts/test_verify_resume_pdf.py

The link-visibility / reading-order / clean-extraction / keyword-list
checks are integration-tested against REAL compiled PDFs (tectonic +
poppler), not just hand-written strings — see `PdfFixtureTests` below.
Compiling is slow-ish (~1-3s/doc) and needs `tectonic`/`pdftotext` on PATH;
those tests are skipped (not failed) if either tool is missing.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import verify_resume_pdf as vrp

HAVE_TECTONIC = shutil.which("tectonic") is not None
HAVE_POPPLER = shutil.which("pdftotext") is not None and shutil.which("pdfinfo") is not None


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def compile_tex(tex_source: str, tmpdir: Path, name: str) -> tuple[Path, Path]:
    """Write `tex_source` to `<tmpdir>/<name>.tex`, compile it, return (tex, pdf) paths."""
    tex_path = tmpdir / f"{name}.tex"
    tex_path.write_text(tex_source, encoding="utf-8")
    proc = subprocess.run(["tectonic", str(tex_path)], cwd=tmpdir,
                          capture_output=True, text=True, timeout=120)
    pdf_path = tmpdir / f"{name}.pdf"
    assert proc.returncode == 0 and pdf_path.exists(), (
        f"tectonic failed for {name}:\n{proc.stdout}\n{proc.stderr}"
    )
    return tex_path, pdf_path


def layout_text_of(pdf_path: Path) -> str:
    return _run(["pdftotext", "-layout", str(pdf_path), "-"])


# A minimal but real preamble matching templates/jakes_resume.tex's macros,
# so declared_content()'s \resumeItem/\resumeSubheading/\resumeProjectHeading
# parsing exercises the same commands real runs produce.
_PREAMBLE = r"""
\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage[usenames,dvipsnames]{color}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\pagestyle{fancy}
\fancyhf{} \fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}
\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]
\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}
\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}
\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}
\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
\begin{document}
"""

_EDUCATION = r"""
\section{Education}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Test University}{Test City, ST}
      {B.S. in Computer Science}{Aug 2022 - May 2026}
      \resumeItemListStart
        \resumeItem{\textbf{Relevant Coursework:} Algorithms, Operating Systems}
      \resumeItemListEnd
  \resumeSubHeadingListEnd
"""

_EXPERIENCE = r"""
\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Acme Corp}{Remote}
      {Software Engineering Intern}{May 2025 - Aug 2025}
      \resumeItemListStart
        \resumeItem{Cut p99 latency by 42\% by rewriting the hot path in Rust.}
        \resumeItem{Shipped a caching layer serving 10000 requests per second.}
      \resumeItemListEnd
  \resumeSubHeadingListEnd
"""

_PROJECTS = r"""
\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading
          {\textbf{Sample Project} $|$ \emph{Python, PyTorch}}{\href{https://github.com/janedoe/sample-project}{github.com/janedoe/sample-project}}
          \resumeItemListStart
            \resumeItem{Built a classifier that improved accuracy by 15\%.}
            \resumeItem{Processed 3 million rows in under 2 seconds.}
          \resumeItemListEnd
    \resumeSubHeadingListEnd
"""

_SKILLS = r"""
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Languages}{: Python, Rust, SQL} \\
     \textbf{Frameworks \& Tools}{: PyTorch, Docker}
    }}
 \end{itemize}
"""

GOOD_RESUME_TEX = _PREAMBLE + _EDUCATION + _EXPERIENCE + _PROJECTS + _SKILLS + r"\end{document}"

# Reading order broken: Projects rendered before Experience.
BAD_ORDER_TEX = _PREAMBLE + _EDUCATION + _PROJECTS + _EXPERIENCE + _SKILLS + r"\end{document}"

# Link glued to neighboring text in the real template's right-hand cell:
# nothing separates the display string from "Extra", so the rendered PDF
# shows "...sample-projectExtra". Drives the end-to-end broken-link case.
GLUED_LINK_TEX = GOOD_RESUME_TEX.replace(
    "{github.com/janedoe/sample-project}}",
    "{github.com/janedoe/sample-project}Extra}",
)
assert GLUED_LINK_TEX != GOOD_RESUME_TEX

# Escaped underscore in the displayed URL: the .tex says `\_`, the PDF
# shows `_`. Must pass — strip_tex() on the display side handles it.
UNDERSCORE_LINK_TEX = GOOD_RESUME_TEX.replace(
    "{github.com/janedoe/sample-project}}",
    "{github.com/janedoe/sample\\_project}}",
)
assert UNDERSCORE_LINK_TEX != GOOD_RESUME_TEX

# Heading words ("Projects", "Technical Skills") inside an EARLIER bullet:
# an unanchored find() would locate them before Experience and false-fail.
STRAY_HEADING_WORD_TEX = GOOD_RESUME_TEX.replace(
    "Algorithms, Operating Systems",
    "Algorithms, Senior Projects, Technical Skills Seminar",
)
assert STRAY_HEADING_WORD_TEX != GOOD_RESUME_TEX


@unittest.skipUnless(HAVE_TECTONIC and HAVE_POPPLER,
                     "tectonic and poppler (pdftotext/pdfinfo) required")
class PdfFixtureTests(unittest.TestCase):
    """Integration tests against real compiled PDFs (good and deliberately broken)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="verify_resume_pdf_test_"))
        cls.good_tex, cls.good_pdf = compile_tex(GOOD_RESUME_TEX, cls.tmpdir, "good")
        cls.bad_order_tex, cls.bad_order_pdf = compile_tex(BAD_ORDER_TEX, cls.tmpdir, "bad_order")
        cls.glued_tex, cls.glued_pdf = compile_tex(GLUED_LINK_TEX, cls.tmpdir, "glued_link")
        cls.underscore_tex, cls.underscore_pdf = compile_tex(
            UNDERSCORE_LINK_TEX, cls.tmpdir, "underscore_link")
        cls.stray_tex, cls.stray_pdf = compile_tex(
            STRAY_HEADING_WORD_TEX, cls.tmpdir, "stray_heading_word")

        # Minimal standalone docs with a FORCED `\\` line break inserted in
        # the middle of a link / number. This is not organic overflow: the
        # break is put there on purpose so the compiled PDF's extracted text
        # reliably shows the token split across two lines, which is the
        # symptom these checks look for (whatever caused it).
        broken_link_doc = (
            _PREAMBLE
            + r"Link: \href{https://x.com}{github.com/janedoe/resume-\\builder}"
            + "\n\\end{document}"
        )
        cls.broken_link_tex, cls.broken_link_pdf = compile_tex(
            broken_link_doc, cls.tmpdir, "broken_link")

        broken_num_doc = (
            _PREAMBLE
            + r"Bullet: Cut latency by 4\\2\% today."
            + "\n\\end{document}"
        )
        cls.broken_num_tex, cls.broken_num_pdf = compile_tex(
            broken_num_doc, cls.tmpdir, "broken_num")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # -- full end-to-end verify() --------------------------------------

    def test_good_resume_passes_all_checks(self):
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0)
        failed = [c for c in report["checks"] if not c["ok"]]
        self.assertEqual(failed, [], f"unexpected failures: {failed}")
        for name in ("clean_extraction", "reading_order", "link_visibility"):
            names = [c["check"] for c in report["checks"]]
            self.assertIn(name, names)

    def test_reading_order_fails_when_sections_reordered(self):
        report = vrp.verify(self.bad_order_tex, self.bad_order_pdf, None, 0.0)
        order_check = next(c for c in report["checks"] if c["check"] == "reading_order")
        self.assertFalse(order_check["ok"])
        self.assertIn("out of order", order_check["detail"])
        # Everything else about this doc is fine — isolate the failure.
        other_fails = [c["check"] for c in report["checks"]
                      if not c["ok"] and c["check"] != "reading_order"]
        self.assertEqual(other_fails, [])

    def test_reading_order_ignores_heading_words_inside_earlier_bullets(self):
        report = vrp.verify(self.stray_tex, self.stray_pdf, None, 0.0)
        failed = [c for c in report["checks"] if not c["ok"]]
        self.assertEqual(failed, [], f"unexpected failures: {failed}")

    def test_end_to_end_glued_link_fails_link_visibility_only(self):
        report = vrp.verify(self.glued_tex, self.glued_pdf, None, 0.0)
        link = next(c for c in report["checks"] if c["check"] == "link_visibility")
        self.assertFalse(link["ok"])
        self.assertIn("github.com/janedoe/sample-project", link["detail"])
        self.assertFalse(report["ok"])
        other_fails = [c["check"] for c in report["checks"]
                       if not c["ok"] and c["check"] != "link_visibility"]
        self.assertEqual(other_fails, [])

    def test_end_to_end_escaped_underscore_link_passes(self):
        self.assertIn("sample_project", layout_text_of(self.underscore_pdf))
        report = vrp.verify(self.underscore_tex, self.underscore_pdf, None, 0.0)
        failed = [c for c in report["checks"] if not c["ok"]]
        self.assertEqual(failed, [], f"unexpected failures: {failed}")

    def test_reading_order_passes_in_correct_order(self):
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0)
        order_check = next(c for c in report["checks"] if c["check"] == "reading_order")
        self.assertTrue(order_check["ok"])

    # -- link visibility, against a real PDF with a forced `\\` break --

    def test_link_visibility_passes_for_clean_render(self):
        text = layout_text_of(self.good_pdf)
        issues = vrp.link_visibility_issues(self.good_tex.read_text(), text)
        self.assertEqual(issues, [])

    def test_link_visibility_fails_when_pdf_splits_the_link_across_lines(self):
        # The *declared* link is clean (what a correct .tex would say); the
        # extracted text comes from a real PDF where a forced `\\` break
        # split that exact URL across two lines.
        declared_tex = r"\href{https://x.com}{github.com/janedoe/resume-builder}"
        broken_layout = layout_text_of(self.broken_link_pdf)
        self.assertIn("resume-\nbuilder", broken_layout)  # sanity: real split happened
        issues = vrp.link_visibility_issues(declared_tex, broken_layout)
        self.assertEqual(issues, ["github.com/janedoe/resume-builder"])

    # -- clean extraction, against a real PDF with a forced `\\` break -

    def test_clean_extraction_passes_for_clean_render(self):
        text = layout_text_of(self.good_pdf)
        _, bullets = vrp.declared_content(self.good_tex.read_text())
        issues = vrp.clean_extraction_issues(bullets, text)
        self.assertEqual(issues, [])

    def test_clean_extraction_fails_when_pdf_splits_a_number_across_lines(self):
        broken_layout = layout_text_of(self.broken_num_pdf)
        self.assertIn("4\n2% today.", broken_layout)  # sanity: real split happened
        issues = vrp.clean_extraction_issues(["Cut latency by 42% today."], broken_layout)
        self.assertEqual(issues, ["42%"])

    # -- keyword-list presence/non-emptiness -----------------------------

    def test_keyword_list_missing_fails_clearly(self):
        missing_path = self.tmpdir / "does-not-exist.keywords.txt"
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0, missing_path)
        kw = next(c for c in report["checks"] if c["check"] == "keyword_list")
        self.assertFalse(kw["ok"])
        self.assertIn("missing", kw["detail"])
        self.assertFalse(report["ok"])

    def test_keyword_list_empty_file_fails_clearly(self):
        empty_path = self.tmpdir / "empty.keywords.txt"
        empty_path.write_text("   \n\n", encoding="utf-8")
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0, empty_path)
        kw = next(c for c in report["checks"] if c["check"] == "keyword_list")
        self.assertFalse(kw["ok"])
        self.assertIn("empty", kw["detail"])

    def test_keyword_list_present_and_nonempty_passes(self):
        kw_path = self.tmpdir / "real.keywords.txt"
        kw_path.write_text("Python\nRust\ndistributed systems\n", encoding="utf-8")
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0, kw_path)
        kw = next(c for c in report["checks"] if c["check"] == "keyword_list")
        self.assertTrue(kw["ok"])
        self.assertIn("3 keyword", kw["detail"])

    def test_no_keywords_path_means_no_keyword_check(self):
        report = vrp.verify(self.good_tex, self.good_pdf, None, 0.0, None)
        names = [c["check"] for c in report["checks"]]
        self.assertNotIn("keyword_list", names)


class ReadingOrderUnitTests(unittest.TestCase):
    def test_missing_heading_flagged(self):
        issues = vrp.reading_order_issues("Education\nExperience\nProjects\n")
        self.assertTrue(issues)
        self.assertIn("Technical Skills", issues[0])

    def test_out_of_order_flagged(self):
        issues = vrp.reading_order_issues(
            "Education\nProjects\nExperience\nTechnical Skills\n")
        self.assertTrue(issues)
        self.assertIn("out of order", issues[0])

    def test_heading_word_inside_earlier_line_is_ignored(self):
        text = ("Education\nCoursework: Senior Projects, Technical Skills lab\n"
                "Experience\nProjects\nTechnical Skills\n")
        self.assertEqual(vrp.reading_order_issues(text), [])

    def test_real_reorder_not_hidden_by_stray_word(self):
        text = ("Education\nbuilt Experience dashboards\n"
                "Projects\nExperience\nTechnical Skills\n")
        self.assertIn("out of order", vrp.reading_order_issues(text)[0])

    def test_correct_order_passes(self):
        issues = vrp.reading_order_issues(
            "Education\nExperience\nProjects\nTechnical Skills\n")
        self.assertEqual(issues, [])


class LinkVisibilityUnitTests(unittest.TestCase):
    def test_no_links_declared_is_not_an_issue(self):
        self.assertEqual(vrp.link_visibility_issues(r"\begin{document}no links\end{document}",
                                                     "no links"), [])

    def test_glued_to_neighboring_text_flagged(self):
        tex = r"\href{https://x.com}{github.com/x/y}"
        # present as a substring, but jammed against unrelated alnum text
        text = "seegithub.com/x/yhere"
        issues = vrp.link_visibility_issues(tex, text)
        self.assertEqual(issues, ["github.com/x/y"])

    def test_escaped_underscore_display_matches_rendered_underscore(self):
        tex = r"\href{https://github.com/x/my_proj}{github.com/x/my\_proj}"
        self.assertEqual(vrp.link_visibility_issues(tex, "repo: github.com/x/my_proj"), [])

    def test_macro_wrapped_display_matches(self):
        tex = r"\href{mailto:a@b.co}{\underline{a@b.co}}"
        self.assertEqual(vrp.link_visibility_issues(tex, "a@b.co | x"), [])

    def test_bounded_by_punctuation_or_space_passes(self):
        tex = r"\href{https://x.com}{github.com/x/y}"
        text = "see: github.com/x/y (repo)"
        self.assertEqual(vrp.link_visibility_issues(tex, text), [])


class CleanExtractionUnitTests(unittest.TestCase):
    def test_missing_numeric_token_flagged(self):
        issues = vrp.clean_extraction_issues(["Cut latency by 42%."], "Cut latency by today.")
        self.assertEqual(issues, ["42%"])

    def test_glued_numeric_token_flagged(self):
        # "42" is present only as a substring of an unrelated "142%", so the
        # genuine "42%" token must still be reported missing.
        issues = vrp.clean_extraction_issues(["grew revenue by 42%"], "grew revenue by 142% ish")
        self.assertEqual(issues, ["42%"])

    def test_present_token_passes(self):
        issues = vrp.clean_extraction_issues(["grew revenue by 42%"], "we grew revenue by 42% yoy")
        self.assertEqual(issues, [])


class StripTexTests(unittest.TestCase):
    def test_href_keeps_display_text_only(self):
        self.assertEqual(vrp.strip_tex(r"\href{https://x.com}{github.com/x}"),
                          "github.com/x")

    def test_bold_and_emph_unwrapped(self):
        self.assertEqual(vrp.strip_tex(r"\textbf{Zoox} \emph{Foster City}"),
                          "Zoox Foster City")

    def test_pipe_separator_kept_as_literal_pipe(self):
        # Regression: an earlier version replaced $|$ with a space, which
        # made "AWS $|$ Seattle" indistinguishable from two separate words
        # and could mask a heading that actually renders "AWS | Seattle".
        self.assertEqual(vrp.strip_tex(r"AWS $|$ Seattle"), "AWS | Seattle")

    def test_simple_superscript_math_flattens_like_pdftotext(self):
        # The skill's own ATS rules document that $R^2$ extracts as "R2" —
        # ordinary text, glued, no caret. strip_tex must match that.
        self.assertEqual(vrp.strip_tex(r"lifted $R^2$ from 0.2 to 0.7"),
                          "lifted R2 from 0.2 to 0.7")

    def test_subscript_math_flattens(self):
        self.assertEqual(vrp.strip_tex(r"tuned $p_{50}$ latency"),
                          "tuned p50 latency")

    def test_escaped_special_chars(self):
        self.assertEqual(vrp.strip_tex(r"C\&C, 20\% faster, a\_b, \#1"),
                          "C&C, 20% faster, a_b, #1")

    def test_en_dash_and_curly_quotes_normalize(self):
        self.assertEqual(vrp.strip_tex("63.4s – 100ms ‘ok’"),
                          "63.4s - 100ms 'ok'")


class DeclaredContentTests(unittest.TestCase):
    def test_preamble_macro_templates_are_not_content(self):
        # Regression: the template's \newcommand{\resumeItem}[1]{...#1...}
        # definitions live in the preamble and must never be treated as a
        # bullet the PDF is expected to contain.
        tex = r"""
\newcommand{\resumeItem}[1]{\item\small{#1 \vspace{-2pt}}}
\newcommand{\resumeSubheading}[4]{
  \textbf{#1} & #2 \\
  \small#3 & #4 \\
}
\begin{document}
\resumeSubheading{AWS}{Seattle}{SDE Intern}{2025}
\resumeItem{Cut latency from 56s to 1ms.}
\end{document}
"""
        titles, bullets = vrp.declared_content(tex)
        self.assertEqual(titles, ["AWS"])
        self.assertEqual(bullets, ["Cut latency from 56s to 1ms."])
        self.assertNotIn("#1", " ".join(titles + bullets))

    def test_project_heading_title_extracted(self):
        tex = r"""
\begin{document}
\resumeProjectHeading
  {\textbf{Kalshi Sentiment Predictor} $|$ Python, PyTorch}
  {\href{https://github.com/x/y}{github.com/x/y}}
\end{document}
"""
        titles, _ = vrp.declared_content(tex)
        self.assertEqual(titles, ["Kalshi Sentiment Predictor | Python, PyTorch"])

    def test_comments_stripped_but_escaped_percent_kept(self):
        tex = r"""
\begin{document}
\resumeItem{Improved throughput 20\%.}  % this is a comment, not content
\resumeItem{A commented-out fake bullet}
\end{document}
"""
        # Only the first \resumeItem should be seen — the second's braces
        # are matched by the parser regardless of the trailing comment on
        # line 1, so assert on content rather than count.
        _, bullets = vrp.declared_content(tex)
        self.assertIn("Improved throughput 20%.", bullets)


class BulletPresentTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(vrp.bullet_present("cut latency in half", "cut latency in half today"))

    def test_wrapped_across_lines_with_extra_whitespace(self):
        # bullet_present is always called against already-normalized text
        # (verify() runs normalize() on the extracted PDF text first) —
        # mirror that here rather than feeding it raw pdftotext output.
        rendered = vrp.normalize("cut   latency\nin half today")
        self.assertTrue(vrp.bullet_present("cut latency in half", rendered))

    def test_hyphen_wrap_difference_tolerated(self):
        self.assertTrue(vrp.bullet_present("reengineering the pipeline",
                                            "re-engineering the pipeline"))

    def test_genuinely_missing_bullet_fails(self):
        self.assertFalse(vrp.bullet_present(
            "shipped a completely new feature nobody has seen before",
            "an unrelated resume about a different job entirely",
        ))

    def test_truncated_bullet_still_fails(self):
        # Only the first few words rendered (a real partial-overflow case)
        # — must not pass on a short, coincidental head match.
        long_bullet = "designed a distributed caching layer that reduced p99 latency by sixty four percent across all regions"
        rendered = "designed a distributed caching layer that reduced"
        self.assertFalse(vrp.bullet_present(long_bullet, rendered))


class DateRangeTests(unittest.TestCase):
    def test_en_dash_ranges_flagged(self):
        raw = "Aug 2023 – May 2027\nSDE Intern May 2026—Present\n2019 – 2021"
        self.assertEqual(vrp.non_ascii_date_ranges(raw),
                         ["Aug 2023 – May 2027", "May 2026—Present", "2019 – 2021"])

    def test_ascii_hyphen_ranges_pass(self):
        self.assertEqual(vrp.non_ascii_date_ranges("Aug 2023 - May 2027\nJan 2024 - Present"), [])

    def test_non_date_dashes_ignored(self):
        self.assertEqual(vrp.non_ascii_date_ranges("cut 63.4s – 100ms, p50 – p99"), [])


class NormalizeTests(unittest.TestCase):
    def test_idempotent(self):
        s = "AWS  |  Seattle—WA"
        once = vrp.normalize(s)
        self.assertEqual(vrp.normalize(once), once)


if __name__ == "__main__":
    unittest.main()
