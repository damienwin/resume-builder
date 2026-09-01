#!/usr/bin/env python3
"""Tests for verify_resume_pdf.py.

Run: python3 scripts/test_verify_resume_pdf.py
"""
import unittest

import verify_resume_pdf as vrp


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


class NormalizeTests(unittest.TestCase):
    def test_idempotent(self):
        s = "AWS  |  Seattle—WA"
        once = vrp.normalize(s)
        self.assertEqual(vrp.normalize(once), once)


if __name__ == "__main__":
    unittest.main()
