#!/usr/bin/env python3
"""Merge the Simplify and speedyapply parser outputs, collapse cross-source
duplicates, and drop postings whose company already has a recent tailored
resume in the archive folder.

This is the deterministic half of the job-scan skill's Step 2 (cross-source
dedupe and already-applied filtering).
It lives in a script rather than in skill prose because it is pure set logic
with fixed thresholds — running it is faster, reproducible across runs, and
testable, none of which is true of re-deriving the same decision tree in
prose on every scan.

Usage:
    merge_and_filter_jobs.py --simplify <simplify.json> \
        [--speedyapply <speedyapply.json>] \
        [--archive "<archive subfolder>"] [--applied-days 30] [--auto-drop-days 3]

Output: JSON to stdout —
    {"entries": [...], "scanned": N, "scanned_simplify": N,
     "scanned_speedyapply": N, "closed_excluded": N,
     "cross_source_dropped": N, "already_applied_dropped": N}

Entries keep every field the parsers emit. An entry whose company matches an
archived resume older than --auto-drop-days but whose role reads as a
different posting is KEPT, with an "applied_note" field explaining why it is
still surfaced — surfacing beats silently hiding a good option.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlunsplit, urlencode

# Query params that identify a tracking/referral variant of the same canonical
# posting URL rather than a distinct posting.
#
# `gh_jid` is deliberately NOT here. On company-hosted Greenhouse pages
# (ixl.com/company/jobs?gh_jid=..., careers.withwaymo.com/jobs?gh_jid=...) the
# path is a shared careers page and gh_jid is the only thing identifying the
# posting — stripping it collapses every one of that company's openings into a
# single row.
TRACKING_PARAM_RE = re.compile(r"^(utm_|ref$|source$|gh_src$|embed$)", re.I)

# Words that carry no distinguishing signal when comparing two role titles.
YEAR_RE = re.compile(r"^(19|20)\d{2}$")

ROLE_STOPWORDS = {
    "a", "an", "and", "the", "of", "for", "to", "in", "at", "or", "with",
    "new", "grad", "graduate", "college", "university", "entry", "level",
    "early", "career", "campus", "student", "job", "role", "position",
    "i", "ii", "iii", "1", "2", "3", "us", "usa", "remote", "hybrid",
    "full", "time", "fulltime", "intern", "internship", "summer",
    "software", "engineer", "engineering", "developer", "development",
    "swe", "sde",
}


def canonical_url(url):
    """Strip tracking params so two links to the same posting compare equal."""
    if not url:
        return None
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAM_RE.match(k)]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
         urlencode(sorted(kept)), "")
    )


def norm_company(name):
    """Normalize a company name for exact comparison (case, punctuation, spacing)."""
    return " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))


def role_tokens(role):
    """Distinctive lowercase word tokens of a role/tag string.

    Graduation years are dropped alongside the stopwords: "2026"/"2027" appear
    in most new-grad titles, so keeping them would make two unrelated roles at
    one company look like a match on the year alone.
    """
    words = re.findall(r"[a-z0-9]+", (role or "").lower())
    return {
        w for w in words
        if w not in ROLE_STOPWORDS and len(w) > 1 and not YEAR_RE.match(w)
    }


def roles_match(a, b):
    """True when two role strings read as the same posting.

    Either the normalized titles are identical, or their distinctive
    (non-boilerplate) keywords overlap heavily.

    The overlap test is deliberately conservative, because a false positive
    here silently hides a real posting. It uses a *symmetric* denominator
    (union, not the smaller set) and needs at least two shared distinctive
    tokens — otherwise any title reducing to one token would match everything
    sharing it ("Data Analyst - Dashboard Developer" vs "Data Engineer 1" on
    "data"; "Wi-Fi Software Engineer - Starlink" vs another Starlink role).
    """
    na = " ".join(sorted(re.findall(r"[a-z0-9]+", (a or "").lower())))
    nb = " ".join(sorted(re.findall(r"[a-z0-9]+", (b or "").lower())))
    if na and na == nb:
        return True
    ta, tb = role_tokens(a), role_tokens(b)
    if not ta or not tb:
        # Nothing distinctive left on one side (e.g. an archive tag that is
        # just the company name) — not enough evidence to call it a match.
        return False
    overlap = len(ta & tb)
    return overlap >= 2 and overlap / len(ta | tb) >= 0.5


def dedupe_cross_source(entries):
    """Collapse the same posting appearing on both boards, keeping Simplify's
    entry (it carries the richer faang/adv_degree/no_sponsor/us_citizen flags).
    """
    # Simplify first so it always wins a duplicate pair regardless of input order.
    ordered = sorted(entries, key=lambda e: 0 if e.get("source") == "simplify" else 1)
    kept = []
    seen_urls = {}
    dropped = 0
    for entry in ordered:
        url = canonical_url(entry.get("apply_url"))
        company = (entry.get("company") or "").strip().lower()
        source = entry.get("source")

        # Both dedupe rules only ever collapse entries from *different* boards.
        # Two same-source postings are two real openings on one company's
        # board — even when they share an apply URL, which happens whenever a
        # company points every posting at one careers page. Dropping either
        # would silently hide a real job.
        if url and source in seen_urls.get(url, ()):
            pass  # same board, same URL: distinct openings, keep both
        elif url and seen_urls.get(url):
            dropped += 1
            continue

        duplicate = any(
            k.get("source") != source
            and (k.get("company") or "").strip().lower() == company
            and roles_match(k.get("role"), entry.get("role"))
            for k in kept
        )
        if duplicate:
            dropped += 1
            continue

        if url:
            seen_urls.setdefault(url, set()).add(source)
        kept.append(entry)
    return kept, dropped


def archive_index(archive_dir, metrics_path=None):
    """[(company, role_text, when)] for every resume already tailored.

    Two sources, because neither is complete on its own:

    - `metrics.jsonl` resume_tailor events carry the real company AND role
      string, which is the only reliable way to tell "already applied to this
      exact posting" from "this company is running several postings."
    - Archive PDF filenames cover runs predating the metrics log, but the
      naming convention is `<Company> Resume <Name>.pdf`, so the stem carries
      no role signal. Those entries get an empty role, which never satisfies
      the role check on its own.

    Both sources are scoped to the board being scanned. The archive folder is
    already per-board; metrics events are attributed by whether their
    `output_path` lands in that same folder. Without this, tailoring a
    new-grad resume for a company would suppress that company's internship
    postings for the whole window, and vice versa. An event that cannot be
    attributed to this board is skipped rather than assumed to match —
    surfacing an extra posting is much cheaper than hiding a real one.
    """
    out = []
    # e.g. ".../Tailored Resumes/New Grad 27" -> "New Grad 27"
    board_tag = Path(archive_dir).expanduser().name if archive_dir else None

    if metrics_path:
        mpath = Path(metrics_path).expanduser()
        if mpath.is_file():
            for line in mpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") != "resume_tailor":
                    continue
                if board_tag and board_tag not in (rec.get("output_path") or ""):
                    continue
                try:
                    when = datetime.datetime.fromisoformat(rec["timestamp"])
                except (KeyError, ValueError):
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=datetime.timezone.utc)
                out.append((rec.get("company") or "", rec.get("role") or "", when))

    # Companies the metrics log already covers, with real tailoring
    # timestamps. PDF mtimes are skipped for these: a bulk copy, restore, or
    # cloud-sync of the archive folder restamps every file to "now", which
    # would otherwise put the whole folder inside the auto-drop window and
    # silently empty out a scan.
    from_metrics = {norm_company(c) for c, _, _ in out}

    if archive_dir:
        path = Path(archive_dir).expanduser()
        if path.is_dir():
            for pdf in path.glob("*.pdf"):
                try:
                    mtime = datetime.datetime.fromtimestamp(
                        pdf.stat().st_mtime, tz=datetime.timezone.utc
                    )
                except OSError:
                    continue
                # Naming convention is `<Company> Resume <Name>.pdf`, so the
                # company is everything before " Resume". Without that marker
                # (the short `<Event> <Name>.pdf` form) there is no reliable
                # way to tell company from applicant name, so the whole stem
                # is used and simply won't match anything exactly — which
                # errs toward surfacing the posting.
                stem = pdf.stem
                company = stem.split(" Resume", 1)[0] if re.search(r"\sResume\b", stem, re.I) else stem
                company = company.strip()
                if norm_company(company) in from_metrics:
                    continue
                out.append((company, "", mtime))

    return out


def filter_already_applied(entries, archive, applied_days, auto_drop_days):
    """Drop entries whose company has a recent archived resume for the same role.

    A company match alone is not enough to drop — companies routinely run
    several distinct new-grad postings at once. Within auto_drop_days the
    archive file is almost always this same scan re-surfacing, so it drops
    without a role check; between there and applied_days the role text must
    also match. Anything older than applied_days is not a reliable signal
    at all and is ignored.
    """
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    kept = []
    dropped = 0
    for entry in entries:
        company = norm_company(entry.get("company"))
        drop = False
        note = None
        if company:
            for archived_company, archived_role, when in archive:
                # Exact match only. A substring test makes "Citadel" match an
                # archived "Citadel Securities", "Intel" match
                # "IntelliGenesis", and "KLA" match "Klaviyo" — different
                # companies whose postings would then be silently hidden.
                if company != norm_company(archived_company):
                    continue
                age_days = (now - when).total_seconds() / 86400
                if age_days > applied_days:
                    continue
                if age_days <= auto_drop_days:
                    drop = True
                    break
                if archived_role and roles_match(entry.get("role"), archived_role):
                    drop = True
                    break
                # Company matched but there is no role evidence to confirm it
                # is the same posting — surface it with a note rather than
                # silently hiding what may be a different opening.
                note = (
                    f"resume already archived for {entry.get('company')} on "
                    f"{when.date().isoformat()}, role not confirmed as the same"
                )
        if drop:
            dropped += 1
            continue
        if note:
            entry = {**entry, "applied_note": note}
        kept.append(entry)
    return kept, dropped


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simplify", required=True, help="parse_simplify_jobs.py JSON output")
    ap.add_argument("--speedyapply", help="parse_speedyapply_jobs.py JSON output (New Grad only)")
    ap.add_argument("--archive", help="archive subfolder of tailored resumes")
    ap.add_argument(
        "--metrics",
        default=str(Path(__file__).resolve().parent.parent / "knowledge" / "metrics.jsonl"),
        help="metrics.jsonl holding resume_tailor events (company + role)",
    )
    ap.add_argument("--applied-days", type=float, default=30)
    ap.add_argument("--auto-drop-days", type=float, default=3)
    args = ap.parse_args()

    simplify = load(args.simplify)
    entries = list(simplify["entries"])
    scanned_simplify = simplify["scanned"]
    closed_excluded = simplify["closed_excluded"]

    scanned_speedyapply = None
    cross_source_dropped = 0
    if args.speedyapply:
        speedy = load(args.speedyapply)
        entries += speedy["entries"]
        scanned_speedyapply = speedy["scanned"]
        closed_excluded += speedy.get("closed_excluded", 0)
        entries, cross_source_dropped = dedupe_cross_source(entries)

    entries, already_applied_dropped = filter_already_applied(
        entries,
        archive_index(args.archive, args.metrics),
        args.applied_days,
        args.auto_drop_days,
    )

    result = {
        "entries": entries,
        "scanned": scanned_simplify + (scanned_speedyapply or 0),
        "scanned_simplify": scanned_simplify,
        "closed_excluded": closed_excluded,
        "cross_source_dropped": cross_source_dropped,
        "already_applied_dropped": already_applied_dropped,
    }
    if scanned_speedyapply is not None:
        result["scanned_speedyapply"] = scanned_speedyapply

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
