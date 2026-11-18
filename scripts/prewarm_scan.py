#!/usr/bin/env python3
"""No-model cache prewarm for /scan: mirrors job-scan's data path so the
interactive run finds JDs, comp, eligibility and levels.fyi results cached.

Usage:
    python3 scripts/prewarm_scan.py [--board new-grad|internship]
        [--categories swe,dsa,quant] [--days 3] [--dry-run]

Defaults match knowledge/rules.md "Job scan defaults": New Grad, swe/dsa/
quant, 3 days, all three sources, compare-offer on. Read-only with respect to
the scan: it NEVER writes job_scan_state.json / last_scan_at, never tailors or
applies, never touches resumePath. It writes only inside the cache root
(cache entries, a scratch dir, prewarm.lock, prewarm.log) plus one metric
line through log_metric.py. A non-blocking flock makes overlapping runs exit
0 quietly. One bad URL never aborts the run; exit is non-zero only when the
board data itself couldn't be fetched/parsed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import scan_cache

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
LOG_KEEP_LINES = 500
STEP_TIMEOUT_S = 900
CHUNK = 40

BOARDS = {
    "new-grad": [
        ("simplify", "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"),
        ("speedyapply", "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md"),
        ("speedyapply_ai", "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/NEW_GRAD_USA.md"),
    ],
    "internship": [
        ("simplify", "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md"),
    ],
}
SAFE_URL = re.compile(r"https?://\S+")  # fullmatch: no spaces/tabs/newlines
ARCHIVE_SUBFOLDER = {"new-grad": "New Grad 27", "internship": "Summer 26"}


def load_api_key() -> str | None:
    """TYPESAFE_API_KEY from the environment, else repo-root .env. Never logged."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    try:
        lines = (REPO_ROOT / ".env").read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "TYPESAFE_API_KEY":
            return value.strip().strip('"').strip("'") or None
    return None


def archive_dir(board: str, rules_path: Path | None = None) -> str:
    """Archive folder for the already-applied filter, using the base path
    that rules.md's "Output archive" section names (fallback: ~/Desktop)."""
    rules = rules_path or REPO_ROOT / "knowledge" / "rules.md"
    base = None
    try:
        m = re.search(r"`(/[^`]*?Tailored Resumes)/", rules.read_text(encoding="utf-8"))
        base = m.group(1) if m else None
    except OSError:
        pass
    return str(Path(base or Path.home() / "Desktop" / "Tailored Resumes") / ARCHIVE_SUBFOLDER[board])


class Terminated(BaseException):
    """Raised by the SIGTERM/SIGINT handler so `finally` cleanup runs."""


def _raise_terminated(signum, frame):
    raise Terminated(f"signal {signum}")


def clean_stale_scratch(root: Path) -> int:
    """Remove prewarm-* scratch dirs left by earlier killed runs. Call only
    while holding the prewarm lock (so none belongs to a live run)."""
    n = 0
    try:
        for p in root.glob("prewarm-*"):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
                n += 1
    except OSError:
        pass
    return n


def run_cmd(cmd: list[str], env: dict) -> tuple[int, str]:
    """Run a command, return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=STEP_TIMEOUT_S, cwd=REPO_ROOT)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _json(out: str):
    try:
        return json.loads(out)
    except ValueError:
        return None


def _script(name: str) -> str:
    return str(SCRIPTS / name)


def _chunks(items: list, n: int = CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fetch_manifest(runner, env, urls_path, out_dir, extra: list[str], concurrency: int):
    rc, out = runner([sys.executable, _script("fetch_urls.py"), "--urls-file", str(urls_path),
                      "--out-dir", str(out_dir), "--concurrency", str(concurrency), *extra], env)
    return _json(out) if rc == 0 else None


def prewarm(board: str, categories: str, days: float, work: Path, runner=None,
            env: dict | None = None, archive: str | None = None) -> dict:
    """Run the whole data path in `work`. Returns a summary dict; raises
    RuntimeError only when the board data itself is unusable."""
    runner = runner or run_cmd
    env = env if env is not None else dict(os.environ)
    summary = {"postings": 0, "cache_hits": 0, "cache_misses": 0, "jd_failed": 0, "urls_dropped": 0}
    sources = BOARDS[board]

    urls = work / "board_urls.txt"
    urls.write_text("".join(f"{u}\t{n}.md\n" for n, u in sources), encoding="utf-8")
    # Board markdown is fetched live (no --cache): it changes all day.
    manifest = fetch_manifest(runner, env, urls, work, [], 4)
    ok = {Path(r["path"]).name for r in (manifest or {}).get("results", []) if r.get("ok") and r.get("path")}
    if "simplify.md" not in ok:
        raise RuntimeError("Simplify board fetch failed")

    parsed = {}
    for name, _ in sources:
        if f"{name}.md" not in ok:
            continue
        if name == "simplify":
            cmd = [sys.executable, _script("parse_simplify_jobs.py"), str(work / "simplify.md"),
                   "--categories", categories, "--days", str(days)]
        else:
            cmd = [sys.executable, _script("parse_speedyapply_jobs.py"), str(work / f"{name}.md"),
                   "--categories", categories, "--days", str(days), "--source-name", name,
                   "--default-category", "dsa" if name == "speedyapply_ai" else "swe"]
        rc, out = runner(cmd, env)
        data = _json(out) if rc == 0 else None
        if data is None:
            if name == "simplify":
                raise RuntimeError("Simplify parse failed")
            continue
        (work / f"{name}.json").write_text(out, encoding="utf-8")
        parsed[name] = work / f"{name}.json"

    merge = [sys.executable, _script("merge_and_filter_jobs.py"), "--simplify", str(parsed["simplify"])]
    if "speedyapply" in parsed:
        merge += ["--speedyapply", str(parsed["speedyapply"])]
    if "speedyapply_ai" in parsed:
        merge += ["--speedyapply-ai", str(parsed["speedyapply_ai"])]
    rc, out = runner(merge + ["--archive", archive or archive_dir(board)], env)
    merged = _json(out) if rc == 0 else None
    if merged is None:
        # Archive folder may be unreadable (e.g. macOS Desktop permission under
        # launchd). Prewarm only needs a superset, so retry without the
        # already-applied filter.
        rc, out = runner(merge, env)
        merged = _json(out) if rc == 0 else None
        if merged is not None:
            summary["archive"] = "skipped"
    if merged is None:
        raise RuntimeError("merge/filter failed")
    entries = merged.get("entries", [])
    summary["postings"] = len(entries)

    # compare-offer is on, so every surviving posting's JD is needed (Step 2.5).
    raw_urls = list(dict.fromkeys(e["apply_url"] for e in entries if e.get("apply_url")))
    apply_urls = [u for u in raw_urls if isinstance(u, str) and SAFE_URL.fullmatch(u)]
    summary["urls_dropped"] = len(raw_urls) - len(apply_urls)
    if not apply_urls:
        return summary
    jd_urls = work / "jd_urls.txt"
    jd_urls.write_text("".join(f"{u}\n" for u in apply_urls), encoding="utf-8")
    jm = fetch_manifest(runner, env, jd_urls, work / "jds", ["--cache", "--strip-tags"], 8)
    if jm is None:
        raise RuntimeError("JD fetch step failed")
    summary["cache_hits"] = int(jm.get("cache_hits", 0))
    summary["cache_misses"] = int(jm.get("cache_misses", 0))
    text_for = {r["url"]: r["text_path"] for r in jm.get("results", [])
                if r.get("ok") and r.get("text_path")}
    summary["jd_failed"] = len(apply_urls) - len(text_for)

    # Step 3: comp for every fetched JD.
    comp_status: dict[str, str] = {}
    for chunk in _chunks(sorted(set(text_for.values()))):
        rc, out = runner([sys.executable, _script("extract_comp.py"), *chunk, "--cache", "--json"], env)
        for r in ((_json(out) or {}).get("results", []) if rc == 0 else []):
            comp_status[r.get("jd_path")] = r.get("status")

    # Step 2.6: eligibility only for adv_degree rows.
    hatted = sorted({text_for[e["apply_url"]] for e in entries
                     if e.get("adv_degree") and e.get("apply_url") in text_for})
    for chunk in _chunks(hatted):
        runner([sys.executable, _script("check_eligibility.py"), *chunk, "--cache", "--json"], env)

    # Step 3 last: levels.fyi only where comp wasn't stated and no salary field.
    companies = sorted({" ".join(str(e["company"]).split()) for e in entries
                        if e.get("company") and not e.get("salary") and e.get("apply_url") in text_for
                        and comp_status.get(text_for[e["apply_url"]], "none") == "none"})
    if companies:
        cf = work / "companies.txt"
        cf.write_text("\n".join(companies) + "\n", encoding="utf-8")
        runner([sys.executable, _script("levels_fyi_est.py"), "--companies-file", str(cf),
                "--cache", "--json"], env)
    return summary


def append_log(log_path: Path, line: str, keep: int = LOG_KEEP_LINES) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = (lines + [line])[-keep:]
    tmp = log_path.with_name(log_path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, log_path)


def log_metric(summary: dict, board: str, elapsed: float, env: dict) -> None:
    fields = {"board": board, "postings": summary["postings"], "cache_hits": summary["cache_hits"],
              "cache_misses": summary["cache_misses"], "elapsed_s": round(elapsed, 1)}
    try:
        subprocess.run([sys.executable, _script("log_metric.py"), "prewarm_scan", json.dumps(fields)],
                       capture_output=True, env=env, timeout=30, cwd=REPO_ROOT)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", choices=sorted(BOARDS), default="new-grad")
    ap.add_argument("--categories", default="swe,dsa,quant")
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--dry-run", action="store_true", help="print the plan; fetch and write nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(json.dumps({"board": args.board, "categories": args.categories, "days": args.days,
                          "sources": dict(BOARDS[args.board]), "archive": archive_dir(args.board),
                          "cache_root": str(scan_cache.cache_root()),
                          "steps": ["fetch boards (live)", "parse", "merge/filter",
                                    "fetch JDs --cache --strip-tags", "extract_comp --cache",
                                    "check_eligibility --cache (adv_degree rows)",
                                    "levels_fyi_est --cache (no stated comp)", "prune"]}, indent=2))
        return 0

    started = time.monotonic()
    root = scan_cache.cache_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        lock = open(root / "prewarm.lock", "a")
    except OSError as e:
        return _fail_early(root, args.board, f"cache root unusable: {e}")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return 0  # another prewarm is running

    old_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            old_handlers[sig] = signal.signal(sig, _raise_terminated)
        except (ValueError, OSError):  # not the main thread
            pass

    env = dict(os.environ)
    summary = {"postings": 0, "cache_hits": 0, "cache_misses": 0}
    error = None
    work = None
    try:
        key = load_api_key()
        if key:
            env["TYPESAFE_API_KEY"] = key
        clean_stale_scratch(root)
        work = Path(tempfile.mkdtemp(prefix="prewarm-", dir=root))
        summary = prewarm(args.board, args.categories, args.days, work, env=env)
    except Terminated as e:
        error = f"terminated ({e})"
    except Exception as e:  # noqa: BLE001 - logged, reflected in exit code
        error = " ".join(str(e).split()) or type(e).__name__
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        try:
            os.utime(root / "prewarm.lock")  # keep the held lock out of prune's reach
            scan_cache.prune(root=root)
        except (OSError, Terminated):
            pass
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
    elapsed = time.monotonic() - started
    line = (f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} board={args.board} "
            f"postings={summary['postings']} cache_hits={summary['cache_hits']} "
            f"cache_misses={summary['cache_misses']} elapsed_s={elapsed:.1f}"
            + (f" urls_dropped={summary['urls_dropped']}" if summary.get("urls_dropped") else "")
            + (f" archive={summary['archive']}" if summary.get("archive") else "")
            + (f" error={error}" if error else ""))
    try:
        append_log(root / "prewarm.log", line)
    except OSError as e:
        print(f"prewarm: could not write log: {e}", file=sys.stderr)
    if not error:
        try:
            log_metric(summary, args.board, elapsed, env)
        except Exception as e:  # noqa: BLE001 - metrics are best-effort
            print(f"prewarm: metric failed: {e}", file=sys.stderr)
    print(line)
    lock.close()
    return 1 if error else 0


def _fail_early(root: Path, board: str, msg: str) -> int:
    line = (f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} board={board} "
            f"error={' '.join(msg.split())}")
    try:
        append_log(root / "prewarm.log", line)
    except OSError:
        pass
    print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
