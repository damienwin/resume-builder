#!/usr/bin/env python3
"""Fetch a batch of URLs concurrently and report a JSON manifest.

Used by the job-scan skill (board fetch, advanced-degree JD verification,
salary JD lookup) and any other skill that needs several independent HTTP
fetches done in one pass instead of one `curl` at a time. Stdlib only — no
third-party deps, same convention as the other scripts/ files.

Usage:
    fetch_urls.py --urls-file <path> --out-dir <dir> [--concurrency 8]
                   [--timeout 20] [--strip-tags] [--user-agent <ua>]

Each line of --urls-file is either:
    <url>
    <url>\t<output-name>

Without a name column, the output filename is derived from a hash of the
URL. With one, the caller controls it (e.g. "simplify.md", "speedyapply.md").

--strip-tags additionally writes a "<name>.txt" beside the raw body with
script/style blocks removed, tags stripped, and whitespace collapsed — the
plain-text form the degree and salary checks actually read.

A failed fetch (timeout, non-2xx, connection error) is never an exception —
it's a manifest entry with "ok": false and an "error" string. One bad URL
never sinks the batch. Output: JSON to stdout —
    {"fetched": N, "failed": N, "results": [
        {"url": ..., "path": ..., "status": 200, "ok": true, "bytes": N},
        {"url": ..., "status": 403, "ok": false, "error": "HTTP 403"}
    ]}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def fetch_one(url: str, explicit_name: str | None, out_dir: Path, timeout: float,
              user_agent: str, strip_tags_flag: bool, fetcher=None) -> dict:
    """Fetch a single URL. `fetcher` is injectable for tests — it must accept
    (url, timeout, user_agent) and return (status, content_type, body_bytes),
    or raise."""
    result = {"url": url}
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
    except Exception as e:  # noqa: BLE001 - any fetch failure is data, not a crash
        result.update({"ok": False, "error": str(e)})
    return result


def _http_get(url: str, timeout: float, user_agent: str):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (resp.headers.get_content_type() or ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get_content_type() if e.headers else "") or "", e.read()


def fetch_all(entries: list[tuple[str, str | None]], out_dir: Path, concurrency: int,
              timeout: float, user_agent: str, strip_tags_flag: bool, fetcher=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [
            pool.submit(fetch_one, url, name, out_dir, timeout, user_agent, strip_tags_flag, fetcher)
            for url, name in entries
        ]
        for fut in futures:
            results.append(fut.result())

    fetched = sum(1 for r in results if r.get("ok"))
    return {"fetched": fetched, "failed": len(results) - fetched, "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls-file", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--strip-tags", action="store_true")
    ap.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = ap.parse_args()

    entries = parse_urls_file(args.urls_file)
    manifest = fetch_all(
        entries, Path(args.out_dir), args.concurrency, args.timeout,
        args.user_agent, args.strip_tags,
    )
    json.dump(manifest, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
