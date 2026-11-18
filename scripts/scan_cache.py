#!/usr/bin/env python3
"""Shared on-disk cache for job-scan: fetched JDs and parsed-JD results.

Stdlib only. The cache root is `$RESUME_BUILDER_CACHE` or
`~/.cache/resume-builder/` — deliberately outside `knowledge/` so every
Conductor workspace shares one cache. Only public JD text is ever stored.

Layout under the root:
    jds/<key>.html        raw body            (key = url_key(url))
    jds/<key>.txt         tag-stripped text
    jds/<key>.meta.json   {"url", "fetched_at", "status", "content_hash"}
    parsed/<kind>/<key>.json
                          {"jd_hash", "parser_version", "result"} per parser
                          (kind: "comp", "eligibility", ...)
    levels/<company-slug>.json   {"fetched_at", "result"}  (30-day TTL)

Only successful (2xx, non-empty) fetches are ever stored. Every write is
atomic (temp file + os.replace), so a reader never sees a partial entry and
two concurrent runs (scheduled prewarm + interactive /scan) cannot corrupt
each other; last writer wins with identical content.

Parsed results are valid only for the exact JD content hash AND the parser
version they were computed with — bump a parser's version constant and old
entries miss automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_MAX_AGE_S = 24 * 3600
LEVELS_TTL_S = 30 * 24 * 3600
MARKER = ".resume-builder-cache"
PRUNE_SUBDIRS = ("jds", "parsed", "levels")
_TRACKING_PARAMS = {"ref", "source", "src", "gh_src", "fbclid", "gclid", "mc_cid", "mc_eid"}


def cache_root() -> Path:
    env = os.environ.get("RESUME_BUILDER_CACHE")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "resume-builder"


def canonical_url(url: str) -> str:
    """Strip fragment, utm_*/ref/etc. params, default ports, and trailing slash."""
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    scheme, netloc = parts.scheme.lower(), parts.netloc.lower()
    if (scheme, netloc.rpartition(":")[2]) in (("http", "80"), ("https", "443")):
        netloc = netloc.rpartition(":")[0]
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_key(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """Whitespace-insensitive hash of JD text (cheap normalization; the
    handoff manifest keeps its own stricter one)."""
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


def ensure_root(root: Path | None = None) -> Path:
    """Create the cache root and its ownership marker (idempotent). prune
    refuses to run in a directory without the marker."""
    root = root or cache_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        try:
            marker.write_text("resume-builder scan cache\n", encoding="utf-8")
        except OSError:
            pass
    return root


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---- JD cache --------------------------------------------------------------

def jd_paths(url: str, root: Path | None = None) -> dict[str, Path]:
    d = (root or cache_root()) / "jds"
    key = url_key(url)
    return {"html": d / f"{key}.html", "txt": d / f"{key}.txt", "meta": d / f"{key}.meta.json"}


def jd_get(url: str, max_age_s: float = DEFAULT_MAX_AGE_S, root: Path | None = None,
           now: float | None = None) -> dict | None:
    """Return {"html_path","text_path","meta"} for a fresh entry, else None."""
    p = jd_paths(url, root)
    meta = _read_json(p["meta"])
    if not isinstance(meta, dict) or not p["txt"].is_file() or not p["html"].is_file():
        return None
    try:
        txt = p["txt"].read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if not meta.get("content_hash") or content_hash(txt) != meta["content_hash"]:
        return None  # txt/meta pair torn by concurrent writers
    if (now if now is not None else time.time()) - meta.get("fetched_at", 0) > max_age_s:
        return None
    return {"html_path": str(p["html"]), "text_path": str(p["txt"]), "meta": meta}


def jd_put(url: str, body: bytes, text: str, status: int, root: Path | None = None,
           now: float | None = None, content_type: str | None = None) -> dict | None:
    """Store a successful fetch. Returns the entry, or None if not cacheable
    (non-2xx or empty text). Data files land before the meta file, so a
    reader that sees meta always sees complete data."""
    if not (200 <= status < 300) or not text.strip():
        return None
    ensure_root(root)
    p = jd_paths(url, root)
    meta = {"url": canonical_url(url), "fetched_at": now if now is not None else time.time(),
            "status": status, "content_hash": content_hash(text)}
    if content_type:
        meta["content_type"] = content_type
    _atomic_write(p["html"], body)
    _atomic_write(p["txt"], text.encode("utf-8"))
    _atomic_write(p["meta"], json.dumps(meta).encode("utf-8"))
    return {"html_path": str(p["html"]), "text_path": str(p["txt"]), "meta": meta}


# ---- parsed-JD cache --------------------------------------------------------

def _parsed_path(kind: str, url_or_key: str, root: Path | None) -> Path:
    key = url_or_key if re.fullmatch(r"[0-9a-f]{64}", url_or_key) else url_key(url_or_key)
    return (root or cache_root()) / "parsed" / kind / f"{key}.json"


def parsed_get(kind: str, url_or_key: str, jd_hash: str, parser_version: str | int,
               root: Path | None = None):
    """Cached parser result, or None unless jd_hash AND parser_version match."""
    rec = _read_json(_parsed_path(kind, url_or_key, root))
    if (isinstance(rec, dict) and rec.get("jd_hash") == jd_hash
            and str(rec.get("parser_version")) == str(parser_version)):
        return rec.get("result")
    return None


def parsed_put(kind: str, url_or_key: str, jd_hash: str, parser_version: str | int,
               result, root: Path | None = None) -> None:
    ensure_root(root)
    rec = {"jd_hash": jd_hash, "parser_version": str(parser_version), "result": result}
    _atomic_write(_parsed_path(kind, url_or_key, root), json.dumps(rec).encode("utf-8"))


# ---- levels.fyi cache -------------------------------------------------------

def _levels_path(company: str, root: Path | None) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "unknown"
    return (root or cache_root()) / "levels" / f"{slug}.json"


def levels_get(company: str, ttl_s: float = LEVELS_TTL_S, root: Path | None = None,
               now: float | None = None):
    rec = _read_json(_levels_path(company, root))
    if isinstance(rec, dict) and (now if now is not None else time.time()) - rec.get("fetched_at", 0) <= ttl_s:
        return rec.get("result")
    return None


def levels_put(company: str, result, root: Path | None = None, now: float | None = None) -> None:
    ensure_root(root)
    rec = {"fetched_at": now if now is not None else time.time(), "result": result}
    _atomic_write(_levels_path(company, root), json.dumps(rec).encode("utf-8"))


# ---- maintenance ------------------------------------------------------------

def prune(max_age_s: float = 14 * 24 * 3600, root: Path | None = None,
          now: float | None = None) -> int:
    """Delete stale cache files; returns count removed.

    Safety: only touches files under jds/, parsed/ and levels/ of a root that
    carries the MARKER file (so a mis-set RESUME_BUILDER_CACHE such as ~ is
    never swept). jds/ and parsed/ expire after max_age_s; levels/ after
    max(max_age_s, LEVELS_TTL_S) so prune never shortens its TTL. Empty
    subdirectories left behind are removed.
    """
    root = root or cache_root()
    if not (root / MARKER).is_file():
        return 0
    t = now if now is not None else time.time()
    removed = 0
    for sub in PRUNE_SUBDIRS:
        base = root / sub
        if not base.is_dir() or base.is_symlink():
            continue
        cutoff = t - (max(max_age_s, LEVELS_TTL_S) if sub == "levels" else max_age_s)
        for f in base.rglob("*"):
            try:
                if f.is_file() and not f.is_symlink() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        for d in sorted((x for x in base.rglob("*") if x.is_dir() and not x.is_symlink()),
                        key=lambda x: len(x.parts), reverse=True):
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                pass
    return removed


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    pr = sub.add_parser("prune")
    pr.add_argument("--max-age-days", type=float, default=14)
    args = ap.parse_args(argv)
    root = cache_root()
    if args.cmd == "stats":
        counts = {sub_: sum(1 for _ in (root / sub_).rglob("*") if _.is_file())
                  for sub_ in ("jds", "parsed", "levels") if (root / sub_).exists()}
        print(json.dumps({"root": str(root), "files": counts}))
    else:
        print(json.dumps({"removed": prune(args.max_age_days * 86400)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
