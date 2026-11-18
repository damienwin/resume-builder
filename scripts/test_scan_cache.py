#!/usr/bin/env python3
"""Tests for scan_cache.py — the shared on-disk cache for job-scan.

Run: python3 scripts/test_scan_cache.py
"""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import scan_cache as sc

URL = "https://boards.example.com/jobs/123"


class TmpRoot(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)


class UrlKeyTests(unittest.TestCase):
    def test_strips_tracking_and_fragment(self):
        base = sc.url_key("https://x.com/job/1")
        for variant in (
            "https://x.com/job/1?utm_source=a&utm_medium=b",
            "https://x.com/job/1?ref=simplify",
            "https://x.com/job/1?gh_src=abc&fbclid=z",
            "https://x.com/job/1#apply",
            "https://x.com/job/1/",
            "HTTPS://X.COM/job/1",
            "  https://x.com/job/1  ",
            "https://x.com:443/job/1",
        ):
            self.assertEqual(sc.url_key(variant), base, variant)

    def test_param_order_irrelevant(self):
        self.assertEqual(sc.url_key("https://x.com/j?a=1&b=2"), sc.url_key("https://x.com/j?b=2&a=1"))

    def test_meaningful_params_kept(self):
        self.assertNotEqual(sc.url_key("https://x.com/j?id=1"), sc.url_key("https://x.com/j?id=2"))
        self.assertEqual(sc.canonical_url("https://x.com/j?utm_x=1&gh_jid=5"), "https://x.com/j?gh_jid=5")

    def test_different_paths_differ(self):
        self.assertNotEqual(sc.url_key("https://x.com/a"), sc.url_key("https://x.com/b"))

    def test_key_is_sha256_hex(self):
        self.assertRegex(sc.url_key(URL), r"^[0-9a-f]{64}$")

    def test_root_path_stable(self):
        self.assertEqual(sc.canonical_url("https://x.com"), sc.canonical_url("https://x.com/"))


class CacheRootTests(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": "/tmp/xyz-cache"}):
            self.assertEqual(sc.cache_root(), Path("/tmp/xyz-cache"))

    def test_default_under_home(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESUME_BUILDER_CACHE", None)
            self.assertEqual(sc.cache_root(), Path.home() / ".cache" / "resume-builder")


class ContentHashTests(unittest.TestCase):
    def test_whitespace_insensitive(self):
        self.assertEqual(sc.content_hash("a  b\n\nc"), sc.content_hash(" a b c "))
        self.assertNotEqual(sc.content_hash("a b"), sc.content_hash("a c"))


class JdCacheTests(TmpRoot):
    def test_roundtrip(self):
        entry = sc.jd_put(URL, b"<p>hi</p>", "hi", 200, root=self.root, now=1000)
        self.assertIsNotNone(entry)
        got = sc.jd_get(URL, root=self.root, now=1001)
        self.assertEqual(Path(got["html_path"]).read_bytes(), b"<p>hi</p>")
        self.assertEqual(Path(got["text_path"]).read_text(), "hi")
        self.assertEqual(got["meta"]["status"], 200)
        self.assertEqual(got["meta"]["content_hash"], sc.content_hash("hi"))
        self.assertEqual(got["meta"]["url"], sc.canonical_url(URL))

    def test_canonical_variants_share_entry(self):
        sc.jd_put(URL + "?utm_source=x#frag", b"b", "t", 200, root=self.root)
        self.assertIsNotNone(sc.jd_get(URL + "/", root=self.root))

    def test_content_type_stored(self):
        sc.jd_put(URL, b"{}", "t", 200, root=self.root, content_type="application/json")
        self.assertEqual(sc.jd_get(URL, root=self.root)["meta"]["content_type"], "application/json")

    def test_miss_when_absent(self):
        self.assertIsNone(sc.jd_get(URL, root=self.root))

    def test_ttl_expiry(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root, now=1000)
        self.assertIsNotNone(sc.jd_get(URL, max_age_s=100, root=self.root, now=1100))  # boundary: fresh
        self.assertIsNone(sc.jd_get(URL, max_age_s=100, root=self.root, now=1101))
        self.assertIsNotNone(sc.jd_get(URL, max_age_s=1000, root=self.root, now=1101))

    def test_default_ttl_is_24h(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root, now=0)
        self.assertIsNotNone(sc.jd_get(URL, root=self.root, now=24 * 3600))
        self.assertIsNone(sc.jd_get(URL, root=self.root, now=24 * 3600 + 1))

    def test_non_2xx_not_cached(self):
        for status in (199, 301, 403, 404, 500):
            self.assertIsNone(sc.jd_put(URL, b"body", "text", status, root=self.root), status)
        self.assertIsNone(sc.jd_get(URL, root=self.root))
        self.assertFalse((self.root / "jds").exists())

    def test_empty_text_not_cached(self):
        self.assertIsNone(sc.jd_put(URL, b"<html></html>", "", 200, root=self.root))
        self.assertIsNone(sc.jd_put(URL, b"x", "  \n\t ", 200, root=self.root))
        self.assertIsNone(sc.jd_get(URL, root=self.root))

    def test_failed_put_does_not_clobber_good_entry(self):
        sc.jd_put(URL, b"good", "good", 200, root=self.root)
        sc.jd_put(URL, b"err", "err", 500, root=self.root)
        got = sc.jd_get(URL, root=self.root)
        self.assertEqual(Path(got["text_path"]).read_text(), "good")

    def test_corrupt_or_partial_entry_is_miss(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root)
        p = sc.jd_paths(URL, self.root)
        p["meta"].write_text("{not json")
        self.assertIsNone(sc.jd_get(URL, root=self.root))
        p["meta"].write_text("[]")
        self.assertIsNone(sc.jd_get(URL, root=self.root))
        sc.jd_put(URL, b"b", "t", 200, root=self.root)
        p["txt"].unlink()
        self.assertIsNone(sc.jd_get(URL, root=self.root))

    def test_overwrite_refreshes_timestamp(self):
        sc.jd_put(URL, b"old", "old", 200, root=self.root, now=0)
        sc.jd_put(URL, b"new", "new", 200, root=self.root, now=100000)
        got = sc.jd_get(URL, root=self.root, now=100001)
        self.assertEqual(Path(got["text_path"]).read_text(), "new")


class ParsedCacheTests(TmpRoot):
    def test_roundtrip(self):
        sc.parsed_put("comp", URL, "h1", 1, {"min": 100}, root=self.root)
        self.assertEqual(sc.parsed_get("comp", URL, "h1", 1, root=self.root), {"min": 100})

    def test_miss_on_jd_hash_change(self):
        sc.parsed_put("comp", URL, "h1", 1, {"min": 100}, root=self.root)
        self.assertIsNone(sc.parsed_get("comp", URL, "h2", 1, root=self.root))

    def test_miss_on_parser_version_change(self):
        sc.parsed_put("comp", URL, "h1", 1, {"min": 100}, root=self.root)
        self.assertIsNone(sc.parsed_get("comp", URL, "h1", 2, root=self.root))

    def test_version_int_str_equivalent(self):
        sc.parsed_put("comp", URL, "h1", 3, "r", root=self.root)
        self.assertEqual(sc.parsed_get("comp", URL, "h1", "3", root=self.root), "r")

    def test_kinds_are_separate(self):
        sc.parsed_put("comp", URL, "h", 1, "c", root=self.root)
        sc.parsed_put("eligibility", URL, "h", 1, "e", root=self.root)
        self.assertEqual(sc.parsed_get("comp", URL, "h", 1, root=self.root), "c")
        self.assertEqual(sc.parsed_get("eligibility", URL, "h", 1, root=self.root), "e")

    def test_url_or_key_equivalent(self):
        sc.parsed_put("comp", URL, "h", 1, "r", root=self.root)
        self.assertEqual(sc.parsed_get("comp", sc.url_key(URL), "h", 1, root=self.root), "r")
        self.assertEqual(sc.parsed_get("comp", URL + "?utm_source=z", "h", 1, root=self.root), "r")

    def test_falsy_result_roundtrips_but_absent_is_none(self):
        sc.parsed_put("elig", URL, "h", 1, False, root=self.root)
        self.assertIs(sc.parsed_get("elig", URL, "h", 1, root=self.root), False)
        self.assertIsNone(sc.parsed_get("elig", URL + "/other", "h", 1, root=self.root))

    def test_corrupt_record_is_miss(self):
        sc.parsed_put("comp", URL, "h", 1, "r", root=self.root)
        path = self.root / "parsed" / "comp" / f"{sc.url_key(URL)}.json"
        path.write_text("garbage")
        self.assertIsNone(sc.parsed_get("comp", URL, "h", 1, root=self.root))
        path.write_text(json.dumps({"jd_hash": "h", "parser_version": "1"}))  # no result key
        self.assertIsNone(sc.parsed_get("comp", URL, "h", 1, root=self.root))


class LevelsCacheTests(TmpRoot):
    def test_roundtrip_and_slug(self):
        sc.levels_put("Acme, Inc.", {"p50": 1}, root=self.root, now=0)
        self.assertEqual(sc.levels_get("acme inc", root=self.root, now=1), {"p50": 1})
        self.assertTrue((self.root / "levels" / "acme-inc.json").is_file())

    def test_ttl_30_days(self):
        sc.levels_put("Acme", "r", root=self.root, now=0)
        day = 24 * 3600
        self.assertEqual(sc.levels_get("Acme", root=self.root, now=30 * day), "r")
        self.assertIsNone(sc.levels_get("Acme", root=self.root, now=30 * day + 1))

    def test_custom_ttl(self):
        sc.levels_put("Acme", "r", root=self.root, now=0)
        self.assertIsNone(sc.levels_get("Acme", ttl_s=10, root=self.root, now=11))

    def test_miss_and_empty_company(self):
        self.assertIsNone(sc.levels_get("nobody", root=self.root))
        sc.levels_put("!!!", "r", root=self.root)
        self.assertEqual(sc.levels_get("???", root=self.root), "r")  # both slug to "unknown"


class AtomicWriteTests(TmpRoot):
    def test_no_temp_files_left_and_content_correct(self):
        target = self.root / "d" / "f.bin"
        sc._atomic_write(target, b"abc")
        self.assertEqual(target.read_bytes(), b"abc")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["f.bin"])

    def test_failure_leaves_old_content_and_cleans_temp(self):
        target = self.root / "f.bin"
        sc._atomic_write(target, b"old")
        with mock.patch("scan_cache.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                sc._atomic_write(target, b"new")
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual([p.name for p in self.root.iterdir()], ["f.bin"])

    def test_reader_never_sees_partial_data(self):
        target = self.root / "f.bin"
        payloads = [bytes([i]) * 200000 for i in range(4)]
        sc._atomic_write(target, payloads[0])
        stop = threading.Event()
        bad = []

        def writer(i):
            while not stop.is_set():
                sc._atomic_write(target, payloads[i])

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        try:
            for _ in range(300):
                data = target.read_bytes()
                if data not in payloads:
                    bad.append(len(data))
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertEqual(bad, [])


class ConcurrentWriterTests(TmpRoot):
    def test_concurrent_jd_put_same_url(self):
        errors = []

        def work():
            try:
                for _ in range(20):
                    sc.jd_put(URL, b"<p>same</p>", "same", 200, root=self.root)
                    got = sc.jd_get(URL, root=self.root)
                    if got is not None:
                        assert Path(got["text_path"]).read_text() == "same"
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        got = sc.jd_get(URL, root=self.root)
        self.assertEqual(Path(got["html_path"]).read_bytes(), b"<p>same</p>")
        leftovers = [p.name for p in (self.root / "jds").iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_concurrent_distinct_urls(self):
        urls = [f"https://x.com/job/{i}" for i in range(30)]
        threads = [threading.Thread(target=sc.jd_put, args=(u, u.encode(), u, 200),
                                    kwargs={"root": self.root}) for u in urls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for u in urls:
            self.assertEqual(Path(sc.jd_get(u, root=self.root)["text_path"]).read_text(), u)


class PruneTests(TmpRoot):
    def test_prunes_only_old_files(self):
        sc.jd_put("https://x.com/old", b"b", "t", 200, root=self.root)
        sc.jd_put("https://x.com/new", b"b", "t", 200, root=self.root)
        old = sc.jd_paths("https://x.com/old", self.root)
        for p in old.values():
            os.utime(p, (1000, 1000))
        removed = sc.prune(max_age_s=3600, root=self.root)
        self.assertEqual(removed, 3)
        self.assertIsNone(sc.jd_get("https://x.com/old", root=self.root, max_age_s=1e12))
        self.assertIsNotNone(sc.jd_get("https://x.com/new", root=self.root))

    def test_prune_keeps_prewarm_log_and_handles_missing_root(self):
        log = self.root / "prewarm.log"
        log.write_text("x")
        os.utime(log, (1000, 1000))
        self.assertEqual(sc.prune(max_age_s=1, root=self.root), 0)
        self.assertTrue(log.exists())
        self.assertEqual(sc.prune(root=self.root / "nope"), 0)

    def test_prune_uses_now(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root)
        self.assertEqual(sc.prune(max_age_s=100, root=self.root, now=time_far()), 3)

    def test_prune_refuses_without_marker(self):
        d = self.root / "jds"
        d.mkdir()
        f = d / "x.txt"
        f.write_text("x")
        os.utime(f, (1000, 1000))
        self.assertEqual(sc.prune(max_age_s=1, root=self.root), 0)
        self.assertTrue(f.exists())

    def test_prune_only_touches_known_subdirs(self):
        sc.ensure_root(self.root)
        for rel in ("notes.txt", "prewarm.lock", "prewarm.log", "other/a.txt", "docs.md"):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("keep")
            os.utime(p, (1000, 1000))
        self.assertEqual(sc.prune(max_age_s=1, root=self.root), 0)
        for rel in ("notes.txt", "prewarm.lock", "prewarm.log", "other/a.txt", "docs.md",
                    sc.MARKER):
            self.assertTrue((self.root / rel).exists(), rel)

    def test_put_creates_marker(self):
        sc.parsed_put("comp", URL, "h", 1, {"a": 1}, root=self.root)
        self.assertTrue((self.root / sc.MARKER).is_file())

    def test_levels_never_pruned_before_ttl(self):
        sc.levels_put("acme", "r", root=self.root)
        p = sc._levels_path("acme", self.root)
        import time
        os.utime(p, (time.time() - 20 * 86400,) * 2)
        self.assertEqual(sc.prune(max_age_s=3600, root=self.root), 0)
        self.assertTrue(p.exists())
        os.utime(p, (time.time() - 31 * 86400,) * 2)
        self.assertEqual(sc.prune(max_age_s=3600, root=self.root), 1)

    def test_empty_subdirs_removed(self):
        sc.parsed_put("comp", URL, "h", 1, {}, root=self.root)
        f = sc._parsed_path("comp", URL, self.root)
        os.utime(f, (1000, 1000))
        self.assertEqual(sc.prune(max_age_s=3600, root=self.root), 1)
        self.assertFalse(f.parent.exists())
        self.assertTrue((self.root / sc.MARKER).exists())


class TornPairTests(TmpRoot):
    def test_mismatched_txt_is_miss(self):
        sc.jd_put(URL, b"<p>A</p>", "text A", 200, root=self.root)
        p = sc.jd_paths(URL, self.root)
        p["txt"].write_text("text B from another writer")
        self.assertIsNone(sc.jd_get(URL, root=self.root))
        p["txt"].write_text("text A")
        self.assertIsNotNone(sc.jd_get(URL, root=self.root))

    def test_meta_without_hash_is_miss(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root)
        p = sc.jd_paths(URL, self.root)
        meta = json.loads(p["meta"].read_text())
        del meta["content_hash"]
        p["meta"].write_text(json.dumps(meta))
        self.assertIsNone(sc.jd_get(URL, root=self.root))


def time_far():
    import time
    return time.time() + 10_000


class CliTests(TmpRoot):
    def test_stats_and_prune(self):
        sc.jd_put(URL, b"b", "t", 200, root=self.root)
        with mock.patch.dict(os.environ, {"RESUME_BUILDER_CACHE": str(self.root)}):
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(sc.main(["stats"]), 0)
            self.assertEqual(json.loads(buf.getvalue())["files"]["jds"], 3)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sc.main(["prune", "--max-age-days", "14"])
            self.assertEqual(json.loads(buf.getvalue())["removed"], 0)


if __name__ == "__main__":
    unittest.main()
