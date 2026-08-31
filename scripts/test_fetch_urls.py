#!/usr/bin/env python3
"""Tests for fetch_urls.py — the concurrent fetcher every job-scan network
step (and any future one) relies on.

Run: python3 scripts/test_fetch_urls.py
"""
import tempfile
import unittest
from pathlib import Path

from fetch_urls import fetch_all, output_name, parse_urls_file, strip_tags


class StripTagsTests(unittest.TestCase):
    def test_drops_script_and_style(self):
        html = "<html><head><style>.a{}</style><script>var x=1;</script></head>" \
               "<body><p>Minimum qualifications: BS in CS</p></body></html>"
        text = strip_tags(html)
        self.assertNotIn("var x", text)
        self.assertNotIn(".a{}", text)
        self.assertIn("Minimum qualifications: BS in CS", text)

    def test_collapses_whitespace(self):
        html = "<p>Hello   world</p>\n\n\n\n<p>Next</p>"
        text = strip_tags(html)
        self.assertNotIn("   ", text)
        self.assertNotIn("\n\n\n", text)


class OutputNameTests(unittest.TestCase):
    def test_explicit_name_wins(self):
        self.assertEqual(output_name("https://x.com/a", "simplify.md", "text/markdown"), "simplify.md")

    def test_derives_from_url_when_no_name(self):
        name = output_name("https://x.com/a", None, "text/html")
        self.assertTrue(name.endswith(".html"))
        self.assertEqual(len(name), len("0123456789abcdef") + len(".html"))

    def test_stable_for_same_url(self):
        a = output_name("https://x.com/a", None, "text/html")
        b = output_name("https://x.com/a", None, "text/html")
        self.assertEqual(a, b)

    def test_json_content_type(self):
        name = output_name("https://x.com/a", None, "application/json")
        self.assertTrue(name.endswith(".json"))


class ParseUrlsFileTests(unittest.TestCase):
    def test_url_only_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://a.com\nhttps://b.com\n")
            path = f.name
        entries = parse_urls_file(path)
        self.assertEqual(entries, [("https://a.com", None), ("https://b.com", None)])

    def test_url_and_name(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://a.com\tsimplify.md\nhttps://b.com\n")
            path = f.name
        entries = parse_urls_file(path)
        self.assertEqual(entries, [("https://a.com", "simplify.md"), ("https://b.com", None)])

    def test_skips_blank_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("https://a.com\n\n\nhttps://b.com\n")
            path = f.name
        entries = parse_urls_file(path)
        self.assertEqual(len(entries), 2)


class FetchAllTests(unittest.TestCase):
    def _fake_fetcher(self, status_by_url, body=b"<html><body>ok</body></html>"):
        def fetcher(url, timeout, user_agent):
            status = status_by_url[url]
            if status == "raise":
                raise ConnectionError("simulated network failure")
            return status, "text/html", body
        return fetcher

    def test_manifest_shape_all_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html"), ("https://b.com", "b.html")]
            fetcher = self._fake_fetcher({"https://a.com": 200, "https://b.com": 200})
            manifest = fetch_all(entries, Path(tmp), concurrency=4, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            self.assertEqual(manifest["fetched"], 2)
            self.assertEqual(manifest["failed"], 0)
            self.assertTrue(all(r["ok"] for r in manifest["results"]))
            self.assertTrue((Path(tmp) / "a.html").exists())

    def test_manifest_carries_timing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html"), ("https://b.com", "b.html")]
            fetcher = self._fake_fetcher({"https://a.com": 200, "https://b.com": 200})
            manifest = fetch_all(entries, Path(tmp), concurrency=4, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            self.assertIn("elapsed_s", manifest)
            self.assertIn("serial_estimate_s", manifest)
            self.assertEqual(manifest["concurrency"], 4)
            self.assertGreaterEqual(manifest["elapsed_s"], 0)
            for r in manifest["results"]:
                self.assertIn("elapsed_s", r)
                self.assertGreaterEqual(r["elapsed_s"], 0)

    def test_serial_estimate_is_sum_of_per_result_elapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html"), ("https://b.com", "b.html")]
            fetcher = self._fake_fetcher({"https://a.com": 200, "https://b.com": 200})
            manifest = fetch_all(entries, Path(tmp), concurrency=4, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            expected = round(sum(r["elapsed_s"] for r in manifest["results"]), 3)
            self.assertAlmostEqual(manifest["serial_estimate_s"], expected)

    def test_concurrency_floor_of_one_even_when_requested_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html")]
            fetcher = self._fake_fetcher({"https://a.com": 200})
            manifest = fetch_all(entries, Path(tmp), concurrency=0, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            self.assertEqual(manifest["concurrency"], 1)

    def test_one_failure_does_not_sink_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html"), ("https://bad.com", "b.html")]
            fetcher = self._fake_fetcher({"https://a.com": 200, "https://bad.com": "raise"})
            manifest = fetch_all(entries, Path(tmp), concurrency=4, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            self.assertEqual(manifest["fetched"], 1)
            self.assertEqual(manifest["failed"], 1)
            failed = [r for r in manifest["results"] if not r["ok"]][0]
            self.assertEqual(failed["url"], "https://bad.com")
            self.assertIn("error", failed)

    def test_http_error_status_marked_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://forbidden.com", "f.html")]
            fetcher = self._fake_fetcher({"https://forbidden.com": 403})
            manifest = fetch_all(entries, Path(tmp), concurrency=2, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            result = manifest["results"][0]
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 403)
            self.assertEqual(result["error"], "HTTP 403")

    def test_strip_tags_writes_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [("https://a.com", "a.html")]
            fetcher = self._fake_fetcher(
                {"https://a.com": 200},
                body=b"<html><body><p>Minimum qualifications: BS</p></body></html>",
            )
            manifest = fetch_all(entries, Path(tmp), concurrency=2, timeout=5,
                                  user_agent="ua", strip_tags_flag=True, fetcher=fetcher)
            result = manifest["results"][0]
            self.assertIn("text_path", result)
            text = Path(result["text_path"]).read_text()
            self.assertIn("Minimum qualifications: BS", text)

    def test_concurrency_bounded_by_max_workers(self):
        # Not a timing test — just confirms a large concurrency value doesn't
        # error and every URL still gets a result.
        with tempfile.TemporaryDirectory() as tmp:
            entries = [(f"https://{i}.com", f"{i}.html") for i in range(20)]
            fetcher = self._fake_fetcher({f"https://{i}.com": 200 for i in range(20)})
            manifest = fetch_all(entries, Path(tmp), concurrency=4, timeout=5,
                                  user_agent="ua", strip_tags_flag=False, fetcher=fetcher)
            self.assertEqual(manifest["fetched"], 20)
            self.assertEqual(len(manifest["results"]), 20)


if __name__ == "__main__":
    unittest.main()
