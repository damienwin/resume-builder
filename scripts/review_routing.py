#!/usr/bin/env python3
"""Choose and record subscription-aware adversarial resume reviews.

This is deliberately a policy and telemetry layer, not a model launcher.  It
never sends a prompt or reads credential files.  A caller obtains a plan,
runs the chosen reviewer in a fresh context when requested, then appends the
observed result with ``record``.  Keeping those operations separate makes a
failed deterministic audit incapable of accidentally spending model usage.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


MODES = ("auto", "deterministic-only", "final-review", "full-stage-review", "human-review")
EARLY_STAGES = {"jd-extraction", "selection", "bullet-writing"}
FINAL_STAGES = {"review", "tailor-end-to-end", "final-resume"}


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.casefold() in {"1", "true", "yes", "available"}:
        return True
    if isinstance(value, str) and value.casefold() in {"0", "false", "no", "unavailable"}:
        return False
    return None


# Candidate executables per provider. The Conductor/ChatGPT-app paths matter
# because `codex` is frequently installed inside an app bundle rather than on
# PATH, which is exactly why detection reported chatgpt.available=false and
# silently downgraded the cross-provider review to same-provider.
PROVIDER_COMMAND_ENV = {
    "claude": "RESUME_BUILDER_CLAUDE_COMMAND",
    "chatgpt": "RESUME_BUILDER_CHATGPT_COMMAND",
}
PROVIDER_EXTRA_ENV = {
    "claude": (),
    "chatgpt": ("CODEX_CLI_PATH",),
}
PROVIDER_CANDIDATES = {
    "claude": ("claude",),
    "chatgpt": (
        "codex",
        "~/Library/Application Support/com.conductor.app/bin/codex",
        "~/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
    ),
}
PROVIDER_AVAILABLE_ENV = {
    "claude": "RESUME_BUILDER_CLAUDE_AVAILABLE",
    "chatgpt": "RESUME_BUILDER_CHATGPT_AVAILABLE",
}


def detect_capabilities(
    config: Optional[dict[str, Any]] = None,
    environ: Optional[dict[str, str]] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> dict[str, Any]:
    """Return locally available subscription-backed CLIs without a paid call.

    ``knowledge/review_routing.json`` or environment variables may override
    executable discovery, which makes a fresh clone and CI deterministic.
    Presence means the CLI is locally usable; it intentionally does not claim
    to inspect or expose authentication credentials.

    Command resolution order per provider: an explicit
    ``RESUME_BUILDER_<PROVIDER>_COMMAND`` env var, a provider-specific alias
    (e.g. ``CODEX_CLI_PATH``), then the known install locations. ``which`` is
    injectable so tests never touch the real filesystem.
    """
    config = config or {}
    environ = environ if environ is not None else os.environ
    result: dict[str, Any] = {}
    for name in ("claude", "chatgpt"):
        configured = _bool(config.get(name))
        overridden = _bool(environ.get(PROVIDER_AVAILABLE_ENV[name]))
        available = overridden if overridden is not None else configured
        source = "environment" if overridden is not None else "configuration"

        candidates: list[str] = []
        for key in (PROVIDER_COMMAND_ENV[name], *PROVIDER_EXTRA_ENV[name]):
            value = environ.get(key)
            if value:
                candidates.append(value)
        candidates.extend(PROVIDER_CANDIDATES[name])

        resolved = None
        for candidate in candidates:
            found = which(os.path.expanduser(candidate))
            if found:
                resolved = found
                break

        if available is None:
            available = bool(resolved)
            source = "executable"
        result[name] = {"available": bool(available), "source": source,
                        "command": resolved or PROVIDER_CANDIDATES[name][0]}
    return result


def load_capabilities(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("capabilities config must be a JSON object")
    return parsed


def risk_signals(artifact: Optional[dict[str, Any]], audit: Optional[dict[str, Any]]) -> list[str]:
    """Return conservative, explainable reasons to review before final render."""
    signals: list[str] = []
    checks = audit.get("checks", []) if isinstance(audit, dict) else []
    if isinstance(audit, dict) and audit.get("ok") is False:
        signals.append("deterministic_hard_failure")
    for check in checks if isinstance(checks, list) else []:
        if not isinstance(check, dict) or check.get("ok") is not False:
            continue
        name = check.get("check")
        if check.get("severity") == "warning":
            signals.append(f"audit_warning:{name}")
    claims: list[Any] = []
    if isinstance(artifact, dict):
        for field in ("claims", "bullets"):
            value = artifact.get(field, [])
            if isinstance(value, list):
                claims.extend(value)
    if isinstance(claims, list):
        requirement_quotes: list[str] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            source_name = str(claim.get("source_path", "")).replace("\\", "/").rsplit("/", 1)[-1]
            if source_name == "skills.md":
                signals.append("skills_list_evidence")
            quote = claim.get("source_quote")
            if isinstance(quote, str) and quote.count("\n\n") > 0:
                signals.append("multi_paragraph_evidence")
            requirement = claim.get("requirement_quote")
            if isinstance(requirement, str) and requirement.strip():
                requirement_quotes.append(requirement.strip().casefold())
        if len(requirement_quotes) != len(set(requirement_quotes)):
            signals.append("duplicate_requirement_mapping")
    return sorted(set(signals))


def deterministic_failure_reason(
    artifact: Optional[dict[str, Any]], audit: Optional[dict[str, Any]]
) -> Optional[str]:
    """Validate caller-supplied gate data before authorizing model usage.

    Routing is intentionally fail-closed: a missing or malformed audit cannot
    be treated as a deterministic pass, and a caller cannot override a failed
    hard check by changing only the report's top-level ``ok`` value.
    """
    if not isinstance(audit, dict):
        return "missing_or_malformed_deterministic_audit"
    checks = audit.get("checks")
    if not isinstance(audit.get("ok"), bool) or not isinstance(checks, list) or not checks:
        return "missing_or_malformed_deterministic_audit"
    computed_ok = True
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("ok"), bool):
            return "missing_or_malformed_deterministic_audit"
        severity = check.get("severity", "hard")
        if severity not in {"hard", "warning"}:
            return "missing_or_malformed_deterministic_audit"
        if severity == "hard" and check["ok"] is False:
            computed_ok = False
    if audit["ok"] is not computed_ok:
        return "missing_or_malformed_deterministic_audit"
    if not computed_ok:
        return "deterministic_hard_failure"

    if artifact is not None:
        if not isinstance(artifact, dict):
            return "malformed_artifact"
        for field in ("claims", "bullets"):
            value = artifact.get(field)
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(item, dict) for item in value)
            ):
                return "malformed_artifact"
    return None


def choose_route(capabilities: dict[str, Any]) -> dict[str, Any]:
    def available(name: str) -> bool:
        value = capabilities.get(name, {}) if isinstance(capabilities, dict) else {}
        return isinstance(value, dict) and bool(value.get("available"))

    def command(name: str) -> Optional[str]:
        value = capabilities.get(name, {}) if isinstance(capabilities, dict) else {}
        return value.get("command") if isinstance(value, dict) else None

    claude = available("claude")
    chatgpt = available("chatgpt")
    if claude and chatgpt:
        return {"author": {"provider": "claude", "model": "sonnet", "command": command("claude")},
                "reviewer": {"provider": "chatgpt", "model": "gpt-5.6-luna",
                             "isolated": True, "command": command("chatgpt")}}
    if claude:
        return {"author": {"provider": "claude", "model": "sonnet", "command": command("claude")},
                "reviewer": {"provider": "claude", "model": "sonnet",
                             "isolated": True, "command": command("claude")}}
    if chatgpt:
        return {"author": {"provider": "chatgpt", "model": "gpt-5.6-luna", "command": command("chatgpt")},
                "reviewer": {"provider": "chatgpt", "model": "gpt-5.6-luna",
                             "isolated": True, "command": command("chatgpt")}}
    return {"author": None, "reviewer": None}


def review_plan(
    mode: str,
    stage: str,
    capabilities: dict[str, Any],
    audit: Optional[dict[str, Any]] = None,
    artifact: Optional[dict[str, Any]] = None,
    final: bool = False,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    route = choose_route(capabilities)
    signals = risk_signals(artifact, audit)
    failure_reason = deterministic_failure_reason(artifact, audit)
    if failure_reason and failure_reason not in signals:
        signals.append(failure_reason)
        signals.sort()
    deterministic_ok = failure_reason is None
    is_final = final or stage in FINAL_STAGES
    paid_review = False
    skip_reason: Optional[str] = None
    human_required = False
    if not deterministic_ok:
        skip_reason = failure_reason
    elif mode == "deterministic-only":
        skip_reason, human_required = "mode_deterministic_only", True
    elif mode == "human-review":
        skip_reason, human_required = "mode_human_review", True
    elif mode == "full-stage-review":
        if route["reviewer"] is None:
            skip_reason, human_required = "no_subscription_available", True
        else:
            paid_review = True
    elif mode == "final-review":
        if not is_final:
            skip_reason = "not_final_stage"
        elif route["reviewer"] is None:
            skip_reason, human_required = "no_subscription_available", True
        else:
            paid_review = True
    elif is_final or signals:
        if route["reviewer"] is None:
            skip_reason, human_required = "no_subscription_available", True
        else:
            paid_review = True
    else:
        skip_reason = "no_risk_signal_before_final"
    return {
        "mode": mode,
        "stage": stage,
        "final": is_final,
        "capabilities": capabilities,
        "author": route["author"],
        "reviewer": route["reviewer"] if paid_review else None,
        "review_required": paid_review,
        "review_skipped": not paid_review,
        "skip_reason": skip_reason,
        "human_review_required": human_required,
        "deterministic_ok": deterministic_ok,
        "risk_signals": signals,
        "fresh_isolated_context": bool(paid_review and route["reviewer"] and route["reviewer"]["isolated"]),
    }


def append_metric(path: Path, event: dict[str, Any]) -> None:
    """Append routing, skip, reviewer telemetry, repairs, and final gate data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event.setdefault("event", "resume_review_routing")
    event.setdefault("recorded_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("detect", "plan"):
        command = sub.add_parser(name)
        command.add_argument("--capabilities", type=Path,
                             help="optional gitignored JSON: {\"claude\": true, \"chatgpt\": false}")
    plan_parser = sub.choices["plan"]
    plan_parser.add_argument("--mode", choices=MODES, default="auto")
    plan_parser.add_argument("--stage", required=True)
    plan_parser.add_argument("--audit", type=Path)
    plan_parser.add_argument("--artifact", type=Path)
    plan_parser.add_argument("--final", action="store_true")
    record = sub.add_parser("record")
    record.add_argument("--metrics-path", required=True, type=Path)
    record.add_argument("--plan", required=True, type=Path)
    record.add_argument("--review-telemetry", type=Path,
                        help="JSON with latency_s, input_tokens, output_tokens, total_tokens, cost_usd")
    record.add_argument("--repair-count", type=int, default=0)
    record.add_argument("--final-gate", type=Path, help="resume_quality_gate JSON report")
    args = parser.parse_args()
    try:
        if args.command == "detect":
            print(json.dumps(detect_capabilities(load_capabilities(args.capabilities)), indent=2))
            return 0
        if args.command == "plan":
            plan = review_plan(args.mode, args.stage,
                               detect_capabilities(load_capabilities(args.capabilities)),
                               _read_json(args.audit), _read_json(args.artifact), args.final)
            print(json.dumps(plan, indent=2))
            return 0
        plan = _read_json(args.plan)
        assert plan is not None
        event = dict(plan)
        event.update({"repair_count": args.repair_count,
                      "review_telemetry": _read_json(args.review_telemetry),
                      "final_gate": _read_json(args.final_gate)})
        append_metric(args.metrics_path, event)
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
