#!/usr/bin/env python3
"""Tests for fetch_urls.py — the concurrent fetcher every job-scan network
step (and any future one) relies on.

Run: python3 scripts/test_fetch_urls.py
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scan_cache
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


class CacheTests(unittest.TestCase):
    HTML = b"<html><body><p>Minimum qualifications: BS</p></body></html>"

    def setUp(self):
        self._tds = [tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()]
        for t in self._tds:
            self.addCleanup(t.cleanup)
        self.out = Path(self._tds[0].name)
        self.cache = Path(self._tds[1].name)
        self.calls = []

    def _fetcher(self, status=200, body=None, content_type="text/html", raises=False):
        def fetcher(url, timeout, user_agent):
            self.calls.append(url)
            if raises:
                raise ConnectionError("down")
            return status, content_type, self.HTML if body is None else body
        return fetcher

    def _run(self, entries, fetcher, strip=True, cache=True, max_age_s=24 * 3600):
        return fetch_all(entries, self.out, 2, 5, "ua", strip, fetcher,
                         cache_dir=self.cache if cache else None, max_age_s=max_age_s)

    def test_miss_writes_through_then_hit_skips_fetch(self):
        entries = [("https://a.com/j", "a.html")]
        m1 = self._run(entries, self._fetcher())
        self.assertEqual((m1["cache_hits"], m1["cache_misses"]), (0, 1))
        self.assertFalse(m1["results"][0]["cached"])
        self.assertEqual(self.calls, ["https://a.com/j"])
        # stored: html + stripped text
        entry = scan_cache.jd_get("https://a.com/j", root=self.cache)
        self.assertIn("Minimum qualifications: BS", Path(entry["text_path"]).read_text())

        (self.out / "a.html").unlink()
        (self.out / "a.html.txt").unlink()
        m2 = self._run(entries, self._fetcher(raises=True))
        self.assertEqual(self.calls, ["https://a.com/j"])  # no second fetch
        self.assertEqual((m2["cache_hits"], m2["cache_misses"]), (1, 0))
        r = m2["results"][0]
        self.assertTrue(r["cached"] and r["ok"])
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["bytes"], len(self.HTML))
        self.assertEqual((self.out / "a.html").read_bytes(), self.HTML)
        self.assertEqual(Path(r["text_path"]), self.out / "a.html.txt")
        self.assertIn("Minimum qualifications: BS", (self.out / "a.html.txt").read_text())
        self.assertEqual(m2["fetched"], 1)
        self.assertEqual(m2["failed"], 0)

    def test_hit_matches_live_filenames_derived_name(self):
        url = "https://a.com/j"
        self._run([(url, None)], self._fetcher(content_type="application/json"))
        live = sorted(p.name for p in self.out.iterdir())
        for p in self.out.iterdir():
            p.unlink()
        m = self._run([(url, None)], self._fetcher(raises=True))
        self.assertTrue(m["results"][0]["cached"])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), live)
        self.assertTrue(any(n.endswith(".json") for n in live))

    def test_hit_without_strip_tags_writes_no_txt(self):
        self._run([("https://a.com/j", "a.html")], self._fetcher(), strip=False)
        for p in self.out.iterdir():
            p.unlink()
        m = self._run([("https://a.com/j", "a.html")], self._fetcher(raises=True), strip=False)
        self.assertNotIn("text_path", m["results"][0])
        self.assertEqual([p.name for p in self.out.iterdir()], ["a.html"])

    def test_text_stored_even_without_strip_tags(self):
        self._run([("https://a.com/j", "a.html")], self._fetcher(), strip=False)
        self.assertIsNotNone(scan_cache.jd_get("https://a.com/j", root=self.cache))
        m = self._run([("https://a.com/j", "a.html")], self._fetcher(raises=True), strip=True)
        self.assertTrue(m["results"][0]["cached"])
        self.assertTrue((self.out / "a.html.txt").is_file())

    def test_stale_entry_refetches(self):
        scan_cache.jd_put("https://a.com/j", b"old", "old", 200, root=self.cache, now=0)
        m = self._run([("https://a.com/j", "a.html")], self._fetcher())
        self.assertEqual(self.calls, ["https://a.com/j"])
        self.assertEqual(m["cache_misses"], 1)
        self.assertEqual((self.out / "a.html").read_bytes(), self.HTML)

    def test_max_age_respected(self):
        self._run([("https://a.com/j", "a.html")], self._fetcher())
        m = self._run([("https://a.com/j", "a.html")], self._fetcher(), max_age_s=-1)
        self.assertEqual(m["cache_hits"], 0)
        self.assertEqual(len(self.calls), 2)

    def test_failures_never_cached(self):
        entries = [("https://a.com/403", "a.html"), ("https://a.com/err", "b.html")]
        m = fetch_all([entries[0]], self.out, 2, 5, "ua", True, self._fetcher(status=403),
                      cache_dir=self.cache)
        self.assertFalse(m["results"][0]["ok"])
        m = fetch_all([entries[1]], self.out, 2, 5, "ua", True, self._fetcher(raises=True),
                      cache_dir=self.cache)
        self.assertFalse(m["results"][0]["ok"])
        self.assertEqual(m["cache_misses"], 1)
        for url, _ in entries:
            self.assertIsNone(scan_cache.jd_get(url, root=self.cache))

    def test_empty_body_not_cached(self):
        self._run([("https://a.com/j", "a.html")], self._fetcher(body=b"<html></html>"))
        self.assertIsNone(scan_cache.jd_get("https://a.com/j", root=self.cache))

    def test_cache_write_failure_does_not_fail_fetch(self):
        from unittest import mock
        with mock.patch("fetch_urls.scan_cache.jd_put", side_effect=OSError("disk full")):
            m = self._run([("https://a.com/j", "a.html")], self._fetcher())
        self.assertTrue(m["results"][0]["ok"])

    def test_mixed_batch_counts(self):
        scan_cache.jd_put("https://a.com/hit", self.HTML, "x", 200, root=self.cache)
        entries = [("https://a.com/hit", "h.html"), ("https://a.com/miss", "m.html")]
        m = self._run(entries, self._fetcher())
        self.assertEqual((m["cache_hits"], m["cache_misses"]), (1, 1))
        self.assertEqual(self.calls, ["https://a.com/miss"])
        self.assertEqual([r["cached"] for r in m["results"]], [True, False])

    def test_no_cache_flag_unchanged(self):
        m = self._run([("https://a.com/j", "a.html")], self._fetcher(), cache=False)
        self.assertNotIn("cache_hits", m)
        self.assertNotIn("cache_misses", m)
        self.assertNotIn("cached", m["results"][0])
        self.assertFalse(any(self.cache.iterdir()))
        # and a pre-populated cache is ignored
        scan_cache.jd_put("https://a.com/j", b"cached", "cached", 200, root=self.cache)
        self._run([("https://a.com/j", "a.html")], self._fetcher(), cache=False)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.out / "a.html").read_bytes(), self.HTML)

    def test_canonical_url_variant_hits(self):
        self._run([("https://a.com/j?utm_source=x", "a.html")], self._fetcher())
        m = self._run([("https://a.com/j/", "b.html")], self._fetcher(raises=True))
        self.assertTrue(m["results"][0]["cached"])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.base = Path(self._td.name)
        self.out = self.base / "out"
        self.calls = []

    def _f(self, url, timeout, ua):
        self.calls.append(url)
        return 200, "text/html", b"<p>hi</p>"

    def test_non_http_schemes_rejected(self):
        for u in ("file:///etc/passwd", "ftp://x.com/a", "gopher://x", "/etc/passwd"):
            m = fetch_all([(u, None)], self.out, 1, 5, "ua", False, self._f)
            r = m["results"][0]
            self.assertFalse(r["ok"], u)
            self.assertEqual(r["error"], "unsupported scheme")
        self.assertEqual(self.calls, [])

    def test_non_http_rejected_with_cache(self):
        cache = self.base / "cache"
        m = fetch_all([("file:///etc/passwd", "a.html")], self.out, 1, 5, "ua", True, self._f,
                      cache_dir=cache)
        self.assertEqual(m["results"][0]["error"], "unsupported scheme")
        self.assertEqual(self.calls, [])

    def test_invalid_output_names_rejected(self):
        for n in ("../escape.txt", "../../escape.txt", "a/b.txt", "/abs.txt", "..", "a\\b.txt"):
            m = fetch_all([("https://x.com/a", n)], self.out, 1, 5, "ua", True, self._f)
            r = m["results"][0]
            self.assertFalse(r["ok"], n)
            self.assertEqual(r["error"], "invalid output name")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.base / "escape.txt").exists())
        self.assertFalse((self.base.parent / "escape.txt").exists())

    def test_tab_injected_line_rejected(self):
        p = self.base / "urls.txt"
        p.write_text("https://x/a\t../../escape.txt\nhttps://x/b\tok.html\n")
        m = fetch_all(parse_urls_file(str(p)), self.out, 1, 5, "ua", False, self._f)
        self.assertEqual(m["results"][0]["error"], "invalid output name")
        self.assertTrue(m["results"][1]["ok"])
        self.assertEqual(self.calls, ["https://x/b"])
        self.assertFalse((self.base.parent / "escape.txt").exists())
        self.assertFalse((self.out.parent / "escape.txt").exists())


class EndToEndEquivalenceTests(unittest.TestCase):
    HTML = (b"<html><head><style>p{color:red}</style><script>var a='$999,999';</script></head>"
            b"<body><h1>Engineer</h1><p>R&amp;D team</p>"
            b"<p>Salary: $120,000 - $150,000 per year</p></body></html>")

    def test_cached_equals_live(self):
        import extract_comp
        with tempfile.TemporaryDirectory() as o1, tempfile.TemporaryDirectory() as o2, \
                tempfile.TemporaryDirectory() as c:
            def live(url, timeout, ua):
                return 200, "text/html", self.HTML

            def boom(url, timeout, ua):
                raise AssertionError("must not fetch")
            e = [("https://a.com/j", "jd.html")]
            m1 = fetch_all(e, Path(o1), 1, 5, "ua", True, live, cache_dir=Path(c))
            m2 = fetch_all(e, Path(o2), 1, 5, "ua", True, boom, cache_dir=Path(c))
            self.assertTrue(m2["results"][0]["cached"])
            t1, t2 = m1["results"][0]["text_path"], m2["results"][0]["text_path"]
            self.assertEqual(Path(t1).read_bytes(), Path(t2).read_bytes())
            self.assertEqual(Path(m1["results"][0]["path"]).read_bytes(),
                             Path(m2["results"][0]["path"]).read_bytes())
            a = extract_comp.extract_file(t1)
            with mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": c}):
                b = extract_comp.extract_file(t2, cache=True)
            a.pop("jd_path"), b.pop("jd_path")
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
