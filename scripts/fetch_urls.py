#!/usr/bin/env python3
"""Fetch a batch of URLs concurrently and report a JSON manifest.

Used by the job-scan skill (board fetch, advanced-degree JD verification,
salary JD lookup) and any other skill that needs several independent HTTP
fetches done in one pass instead of one `curl` at a time. Stdlib only — no
third-party deps, same convention as the other scripts/ files.

Usage:
    fetch_urls.py --urls-file <path> --out-dir <dir> [--concurrency 8]
                   [--timeout 20] [--strip-tags] [--user-agent <ua>]
                   [--cache] [--max-age-hours 24]

Each line of --urls-file is either:
    <url>
    <url>\t<output-name>

Without a name column, the output filename is derived from a hash of the
URL. With one, the caller controls it (e.g. "simplify.md", "speedyapply.md").

--strip-tags additionally writes a "<name>.txt" beside the raw body with
script/style blocks removed, tags stripped, and whitespace collapsed — the
plain-text form the degree and salary checks actually read.

--cache consults the shared scan_cache JD cache (scan_cache.cache_root()): a
fresh entry (younger than --max-age-hours) is copied into --out-dir under the
exact filenames a live fetch would have used and is not fetched; its result
gets "cached": true. Live 2xx fetches are written through (stripped text is
always stored). Failures are never cached. The manifest then also carries
"cache_hits" and "cache_misses". Without --cache nothing changes.

A failed fetch (timeout, non-2xx, connection error) is never an exception —
it's a manifest entry with "ok": false and an "error" string. One bad URL
never sinks the batch. Output: JSON to stdout —
    {"fetched": N, "failed": N, "elapsed_s": W, "concurrency": C,
     "serial_estimate_s": S, "results": [
        {"url": ..., "path": ..., "status": 200, "ok": true, "bytes": N, "elapsed_s": E},
        {"url": ..., "status": 403, "ok": false, "error": "HTTP 403", "elapsed_s": E}
    ]}

elapsed_s (top-level) is the actual wall-clock time for the whole batch;
serial_estimate_s is the sum of each result's own elapsed_s — i.e. how long
the batch would have taken one URL at a time. serial_estimate_s / elapsed_s
is the measured speedup from --concurrency, computed from the run rather
than assumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import scan_cache

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_tags(html_text: str) -> str:
    """Reduce raw HTML to plain text: drop script/style, strip tags, collapse
    whitespace. Not a real HTML parser — good enough for keyword/qualification
    scanning, not for rendering."""
    text = SCRIPT_STYLE_RE.sub(" ", html_text)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text)
    text = BLANK_LINES_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def output_name(url: str, explicit: str | None, content_type: str | None) -> str:
    if explicit:
        return explicit
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    ext = "html"
    if content_type:
        if "json" in content_type:
            ext = "json"
        elif "markdown" in content_type or "text/plain" in content_type:
            ext = "md"
    return f"{digest}.{ext}"


def parse_urls_file(path: str) -> list[tuple[str, str | None]]:
    """Return [(url, explicit_name_or_None), ...], skipping blank lines."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            url = parts[0].strip()
            name = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
            entries.append((url, name))
    return entries


def _valid_name(name: str) -> bool:
    return bool(name) and Path(name).name == name and os.sep not in name \
        and "/" not in name and "\\" not in name and ".." not in name


def _precheck(url: str, explicit_name: str | None) -> str | None:
    """Error string if the entry must not be fetched/written, else None."""
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return "unsupported scheme"
    if explicit_name is not None and not _valid_name(explicit_name):
        return "invalid output name"
    return None


def fetch_one(url: str, explicit_name: str | None, out_dir: Path, timeout: float,
              user_agent: str, strip_tags_flag: bool, fetcher=None,
              cache_dir: Path | None = None, max_age_s: float = scan_cache.DEFAULT_MAX_AGE_S) -> dict:
    """Fetch a single URL. `fetcher` is injectable for tests — it must accept
    (url, timeout, user_agent) and return (status, content_type, body_bytes),
    or raise."""
    result = {"url": url}
    started = time.monotonic()
    bad = _precheck(url, explicit_name)
    if bad:
        result.update({"ok": False, "error": bad, "elapsed_s": round(time.monotonic() - started, 3)})
        return result
    if cache_dir is not None:
        hit = _serve_from_cache(url, explicit_name, out_dir, strip_tags_flag, cache_dir, max_age_s)
        if hit is not None:
            result.update(hit)
            result["elapsed_s"] = round(time.monotonic() - started, 3)
            return result
        result["cached"] = False
    try:
        if fetcher is not None:
            status, content_type, body = fetcher(url, timeout, user_agent)
        else:
            status, content_type, body = _http_get(url, timeout, user_agent)

        name = output_name(url, explicit_name, content_type)
        out_path = out_dir / name
        out_path.write_bytes(body)

        result.update({
            "path": str(out_path),
            "status": status,
            "ok": 200 <= status < 300,
            "bytes": len(body),
        })

        if strip_tags_flag:
            text = strip_tags(body.decode("utf-8", errors="replace"))
            txt_path = out_path.with_suffix(out_path.suffix + ".txt")
            txt_path.write_text(text, encoding="utf-8")
            result["text_path"] = str(txt_path)

        if not result["ok"]:
            result["error"] = f"HTTP {status}"
        elif cache_dir is not None:
            try:
                text = strip_tags(body.decode("utf-8", errors="replace"))
                scan_cache.jd_put(url, body, text, status, root=cache_dir, content_type=content_type)
            except OSError:
                pass  # a cache write failure must never fail the fetch
    except Exception as e:  # noqa: BLE001 - any fetch failure is data, not a crash
        result.update({"ok": False, "error": str(e)})
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result


def _serve_from_cache(url, explicit_name, out_dir, strip_tags_flag, cache_dir, max_age_s):
    """Copy a fresh cached entry into out_dir; None on miss or any cache error."""
    entry = scan_cache.jd_get(url, max_age_s=max_age_s, root=cache_dir)
    if entry is None:
        return None
    meta = entry["meta"]
    try:
        out_path = out_dir / output_name(url, explicit_name, meta.get("content_type"))
        shutil.copyfile(entry["html_path"], out_path)
        hit = {
            "path": str(out_path),
            "status": meta.get("status", 200),
            "ok": True,
            "bytes": out_path.stat().st_size,
            "cached": True,
        }
        if strip_tags_flag:
            txt_path = out_path.with_suffix(out_path.suffix + ".txt")
            shutil.copyfile(entry["text_path"], txt_path)
            hit["text_path"] = str(txt_path)
    except OSError:
        return None
    return hit


def _http_get(url: str, timeout: float, user_agent: str):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (resp.headers.get_content_type() or ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get_content_type() if e.headers else "") or "", e.read()


def fetch_all(entries: list[tuple[str, str | None]], out_dir: Path, concurrency: int,
              timeout: float, user_agent: str, strip_tags_flag: bool, fetcher=None,
              cache_dir: Path | None = None, max_age_s: float = scan_cache.DEFAULT_MAX_AGE_S) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(fetch_one, url, name, out_dir, timeout, user_agent, strip_tags_flag, fetcher,
                        cache_dir, max_age_s)
            for url, name in entries
        ]
        for fut in futures:
            results.append(fut.result())
    elapsed_s = round(time.monotonic() - batch_started, 3)

    fetched = sum(1 for r in results if r.get("ok"))
    serial_estimate_s = round(sum(r.get("elapsed_s", 0) for r in results), 3)
    manifest = {
        "fetched": fetched,
        "failed": len(results) - fetched,
        "elapsed_s": elapsed_s,
        "concurrency": max(1, concurrency),
        "serial_estimate_s": serial_estimate_s,
        "results": results,
    }
    if cache_dir is not None:
        hits = sum(1 for r in results if r.get("cached"))
        manifest["cache_hits"] = hits
        manifest["cache_misses"] = len(results) - hits
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls-file", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--strip-tags", action="store_true")
    ap.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    ap.add_argument("--cache", action="store_true",
                    help="serve fresh JDs from / write live fetches to the scan_cache JD cache")
    ap.add_argument("--max-age-hours", type=float, default=24)
    args = ap.parse_args()

    entries = parse_urls_file(args.urls_file)
    manifest = fetch_all(
        entries, Path(args.out_dir), args.concurrency, args.timeout,
        args.user_agent, args.strip_tags,
        cache_dir=scan_cache.cache_root() if args.cache else None,
        max_age_s=args.max_age_hours * 3600,
    )
    json.dump(manifest, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
