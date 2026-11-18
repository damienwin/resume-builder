#!/usr/bin/env python3
"""Install/uninstall the launchd schedule that runs prewarm_scan.py daily.

Usage:
    python3 scripts/install_prewarm_schedule.py install [--print]
    python3 scripts/install_prewarm_schedule.py uninstall [--print]
    python3 scripts/install_prewarm_schedule.py status

Renders scripts/launchd/com.resume-builder.prewarm.plist.template with this
repo's absolute paths and the current python3, writes it to
~/Library/LaunchAgents/, and loads it with `launchctl bootstrap gui/$UID`.
`--print` is a dry run: show the plist and the commands, change nothing.
Runs at 07:00 and 12:00 daily; no KeepAlive. Nothing is activated until you
run `install` yourself.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import scan_cache

LABEL = "com.resume-builder.prewarm"
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "launchd" / f"{LABEL}.plist.template"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def launchd_env(environ=None) -> dict[str, str]:
    """Environment launchd must carry so the scheduled run uses the same cache
    root / Jev setting as the install-time shell. PATH is always present."""
    environ = os.environ if environ is None else environ
    out = {"PATH": DEFAULT_PATH}
    if environ.get("RESUME_BUILDER_CACHE"):
        # Effective (expanded) path, same one the log paths below use.
        out["RESUME_BUILDER_CACHE"] = str(Path(environ["RESUME_BUILDER_CACHE"]).expanduser())
    if environ.get("RESUME_BUILDER_JEV"):
        out["RESUME_BUILDER_JEV"] = environ["RESUME_BUILDER_JEV"]
    return out


def render(template_text: str, python: str, repo: Path, log_dir: Path,
           env: dict[str, str] | None = None) -> str:
    env = launchd_env({}) if env is None else env
    env_xml = "".join(f"        <key>{escape(k)}</key>\n        <string>{escape(v)}</string>\n"
                      for k, v in env.items())
    values = {
        "LABEL": LABEL,
        "PYTHON": python,
        "SCRIPT": str(repo / "scripts" / "prewarm_scan.py"),
        "REPO": str(repo),
        "LOG_DIR": str(log_dir),
    }
    out = template_text.replace("<<ENV_VARS>>", "\0")  # protect from value substitution
    for k, v in values.items():
        out = out.replace(f"<<{k}>>", escape(v))
    return out.replace("\0", env_xml)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--print", dest="dry", action="store_true",
                    help="dry run: print what would be written/run, change nothing")
    args = ap.parse_args(argv)
    target = plist_path()

    if args.action == "install":
        text = render(TEMPLATE.read_text(encoding="utf-8"), sys.executable, REPO_ROOT,
                      scan_cache.cache_root(), launchd_env())
        if args.dry:
            print(f"# would write {target}\n{text}")
            print(f"# would run: launchctl bootout {_domain()}/{LABEL} (if loaded)")
            print(f"# would run: launchctl bootstrap {_domain()} {target}")
            return 0
        scan_cache.cache_root().mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _launchctl("bootout", f"{_domain()}/{LABEL}")  # reload cleanly if already loaded
        r = _launchctl("bootstrap", _domain(), str(target))
        if r.returncode != 0:
            print(f"launchctl bootstrap failed: {r.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"installed {target} (07:00 and 12:00 daily)")
        return 0

    if args.action == "uninstall":
        if args.dry:
            print(f"# would run: launchctl bootout {_domain()}/{LABEL}")
            print(f"# would remove {target}")
            return 0
        _launchctl("bootout", f"{_domain()}/{LABEL}")
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        print(f"uninstalled {LABEL}")
        return 0

    r = _launchctl("print", f"{_domain()}/{LABEL}")
    print(f"plist: {target} ({'present' if target.exists() else 'absent'})")
    print("loaded: " + ("yes" if r.returncode == 0 else "no"))
    log = scan_cache.cache_root() / "prewarm.log"
    if log.is_file():
        lines = log.read_text(encoding="utf-8").splitlines()
        if lines:
            print(f"last run: {lines[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
