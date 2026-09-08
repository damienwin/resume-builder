#!/usr/bin/env python3
"""Record and summarize cross-model resume-builder evaluations.

Raw measurements are append-only in knowledge/model_eval_runs.jsonl. Large
artifacts and blinded review packs live under eval/model_bakeoff/. In a local
Conductor workspace, records are written to CONDUCTOR_ROOT_PATH so parallel
workspaces contribute to one durable dataset.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROTOCOL_VERSION = 1
STAGES = (
    "jd-extraction",
    "eligibility",
    "selection",
    "bullet-writing",
    "tailor-end-to-end",
    "review",
    "job-scan",
    "application-fill",
)
JUDGE_DIMENSIONS = ("scanability", "clarity", "specificity", "naturalness", "confidence")
SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Iterable[float], quantile: float) -> Optional[float]:
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, int(len(ordered) * quantile))
    return ordered[index]


def require_safe_path_component(value: str, label: str) -> None:
    if not isinstance(value, str) or not SAFE_PATH_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} contains unsafe path characters")


def valid_metric_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


class EvalStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.eval_root = self.repo_root / "eval" / "model_bakeoff"
        self.active_dir = self.eval_root / ".active"
        self.runs_dir = self.eval_root / "runs"
        self.blind_dir = self.eval_root / "blind"
        self.events_path = self.repo_root / "knowledge" / "model_eval_runs.jsonl"

    def initialize(self) -> None:
        for directory in (self.active_dir, self.runs_dir, self.blind_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        cases_path = self.eval_root / "cases.json"
        if not cases_path.exists():
            cases_path.write_text(json.dumps({
                "protocol_version": PROTOCOL_VERSION,
                "cases": [
                    {
                        "id": "backend",
                        "jd_path": "cases/backend.txt",
                        "description": "Backend or infrastructure role",
                    },
                    {
                        "id": "ml-data",
                        "jd_path": "cases/ml-data.txt",
                        "description": "Machine-learning or data role",
                    },
                    {
                        "id": "general-swe",
                        "jd_path": "cases/general-swe.txt",
                        "description": "General software-engineering role",
                    },
                ],
            }, indent=2) + "\n")
            (self.eval_root / "cases").mkdir(exist_ok=True)
        prompts_dir = self.eval_root / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        prompt_path = prompts_dir / "tailor-end-to-end.md"
        if not prompt_path.exists():
            prompt_path.write_text(
                "Tailor a one-page resume for the frozen job description at "
                "<CASE_PATH>. Follow the repository's tailor-resume skill exactly. "
                "Do not modify knowledge/. Preserve truthful gaps, compile the PDF, "
                "run deterministic PDF verification, and report the output PDF and "
                "TeX paths plus any unsupported JD requirements.\n",
                encoding="utf-8",
            )

    def append_event(self, event: Dict[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load_events(self) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events = []
        with self.events_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def git_metadata(self) -> Dict[str, Any]:
        def run_git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(self.repo_root), *args],
                check=False,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() if result.returncode == 0 else ""

        return {
            "repo_commit": run_git("rev-parse", "HEAD") or None,
            "repo_dirty": bool(run_git("status", "--porcelain")),
        }

    def start_run(
        self,
        case_id: str,
        stage: str,
        provider: str,
        model: str,
        reasoning: str,
        cohort: str,
        case_path: Optional[Path] = None,
        prompt_path: Optional[Path] = None,
        plan: Optional[str] = None,
    ) -> str:
        self.initialize()
        run_id = "%s-%s" % (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            uuid.uuid4().hex[:10],
        )
        state = {
            "protocol_version": PROTOCOL_VERSION,
            "run_id": run_id,
            "case_id": case_id,
            "stage": stage,
            "provider": provider,
            "model": model,
            "reasoning": reasoning,
            "cohort": cohort,
            "plan": plan,
            "started_at": utc_now(),
            "started_epoch": time.time(),
            "case_path": str(case_path.resolve()) if case_path else None,
            "case_sha256": sha256_file(case_path),
            "prompt_path": str(prompt_path.resolve()) if prompt_path else None,
            "prompt_sha256": sha256_file(prompt_path),
        }
        state.update(self.git_metadata())
        (self.active_dir / (run_id + ".json")).write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        artifacts: List[Path],
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        reasoning_tokens: Optional[int],
        cached_input_tokens: Optional[int],
        cache_write_input_tokens: Optional[int],
        total_tokens: Optional[int],
        token_source: str,
        turns: Optional[int],
        interventions: int,
        retries: int,
        notes: Optional[str],
        latency_s: Optional[float] = None,
        cost_usd: Optional[float] = None,
        cost_basis: str = "unavailable",
        resolved_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        require_safe_path_component(run_id, "run ID")
        state_path = self.active_dir / (run_id + ".json")
        if not state_path.exists():
            raise ValueError("unknown or already-finished run: %s" % run_id)
        state = json.loads(state_path.read_text(encoding="utf-8"))

        copied_artifacts = []
        artifact_names = [artifact.name for artifact in artifacts]
        if len(artifact_names) != len(set(artifact_names)):
            raise ValueError("artifact basenames must be unique within a run")
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            if not artifact.exists():
                raise ValueError("artifact does not exist: %s" % artifact)
            destination = run_dir / artifact.name
            shutil.copy2(str(artifact), str(destination))
            copied_artifacts.append({
                "path": str(destination),
                "sha256": sha256_file(destination),
            })

        total_input_tokens = None
        if input_tokens is not None:
            total_input_tokens = (
                input_tokens + (cached_input_tokens or 0) + (cache_write_input_tokens or 0)
            )
        if total_tokens is None and total_input_tokens is not None and output_tokens is not None:
            total_tokens = total_input_tokens + output_tokens
        event = dict(state)
        event.pop("started_epoch", None)
        if resolved_model and resolved_model != event["model"]:
            event["requested_model"] = event["model"]
            event["model"] = resolved_model
        harness_latency_s = round(time.time() - state["started_epoch"], 3)
        event.update({
            "event": "model_eval_run",
            "ended_at": utc_now(),
            "latency_s": latency_s if latency_s is not None else harness_latency_s,
            "latency_source": "provider" if latency_s is not None else "harness",
            "harness_latency_s": harness_latency_s,
            "status": status,
            "artifacts": copied_artifacts,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_input_tokens": cache_write_input_tokens,
            "total_input_tokens": total_input_tokens,
            "total_tokens": total_tokens,
            "token_source": token_source,
            "turns": turns,
            "interventions": interventions,
            "retries": retries,
            "notes": notes,
            "cost_usd": cost_usd,
            "cost_basis": cost_basis,
        })
        if total_input_tokens:
            event["output_input_ratio"] = round((output_tokens or 0) / total_input_tokens, 4)
            event["cache_hit_rate"] = round(
                (cached_input_tokens or 0) / total_input_tokens, 4
            )
        self.append_event(event)
        state_path.unlink()
        return event

    def add_ats_scores(self, run_id: str, scores: List[float], scorer: str) -> Dict[str, Any]:
        if len(scores) < 3:
            raise ValueError("record at least three ATS runs to size scorer variance")
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "event": "model_eval_ats",
            "timestamp": utc_now(),
            "run_id": run_id,
            "scorer": scorer,
            "scores": scores,
            "n": len(scores),
            "median": statistics.median(scores),
            "mean": round(statistics.mean(scores), 3),
            "min": min(scores),
            "max": max(scores),
            "spread": max(scores) - min(scores),
        }
        self.append_event(event)
        return event

    def add_human_scores(
        self,
        run_id: str,
        scanability: int,
        clarity: int,
        specificity: int,
        naturalness: int,
        confidence: int,
        reviewer: str,
        notes: Optional[str],
    ) -> Dict[str, Any]:
        ratings = [scanability, clarity, specificity, naturalness, confidence]
        if any(not isinstance(rating, int) or isinstance(rating, bool)
               or rating < 1 or rating > 5 for rating in ratings):
            raise ValueError("human ratings must be integers from 1 to 5")
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "event": "model_eval_human",
            "timestamp": utc_now(),
            "run_id": run_id,
            "reviewer": reviewer,
            "scanability": scanability,
            "clarity": clarity,
            "specificity": specificity,
            "naturalness": naturalness,
            "confidence": confidence,
            "mean": round(statistics.mean(ratings), 3),
            "notes": notes,
        }
        self.append_event(event)
        return event

    def add_audit(self, run_id: str, report: Dict[str, Any]) -> Dict[str, Any]:
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("audit report must contain a non-empty checks list")
        for check in checks:
            if (not isinstance(check, dict)
                    or not isinstance(check.get("ok"), bool)
                    or check.get("severity", "hard") not in {"hard", "warning"}):
                raise ValueError("audit checks require boolean ok and hard/warning severity")
        passed = all(check["ok"] or check.get("severity", "hard") == "warning"
                     for check in checks)
        event = {"protocol_version": PROTOCOL_VERSION, "event": "model_eval_audit",
                 "timestamp": utc_now(), "run_id": run_id, "ok": passed,
                 "stage": report.get("stage"), "checks": checks}
        self.append_event(event)
        return event

    def add_judge(self, run_id: str, judge_provider: str, judge_model: str,
                  verdict: Dict[str, Any]) -> Dict[str, Any]:
        ratings = verdict.get("ratings")
        if (not isinstance(ratings, dict)
                or set(ratings) != set(JUDGE_DIMENSIONS)
                or any(
            not isinstance(ratings.get(name), int)
            or isinstance(ratings.get(name), bool)
            or not 1 <= ratings[name] <= 5
            for name in JUDGE_DIMENSIONS
        )):
            raise ValueError("judge ratings must provide exactly every 1-5 readability dimension")
        if not isinstance(verdict.get("factuality_pass"), bool):
            raise ValueError("judge verdict must include boolean factuality_pass")
        event = {"protocol_version": PROTOCOL_VERSION, "event": "model_eval_judge",
                 "timestamp": utc_now(), "run_id": run_id, "judge_provider": judge_provider,
                 "judge_model": judge_model, "ratings": ratings,
                 "mean": round(statistics.mean(ratings[name] for name in JUDGE_DIMENSIONS), 3),
                 "factuality_pass": verdict["factuality_pass"],
                 "findings": verdict.get("findings", [])}
        telemetry = verdict.get("telemetry")
        if telemetry is not None:
            if not isinstance(telemetry, dict):
                raise ValueError("judge telemetry must be an object")
            for name in ("latency_s", "input_tokens", "output_tokens", "total_tokens",
                         "cost_usd"):
                if name in telemetry and not valid_metric_number(telemetry[name]):
                    raise ValueError(f"judge telemetry {name} must be a non-negative number")
            event["telemetry"] = telemetry
        self.append_event(event)
        return event

    def correct_latency(self, run_id: str, latency_s: float, source: str, reason: str) -> Dict[str, Any]:
        if latency_s < 0:
            raise ValueError("latency must be non-negative")
        event = {"protocol_version": PROTOCOL_VERSION, "event": "model_eval_correction",
                 "timestamp": utc_now(), "run_id": run_id,
                 "fields": {"latency_s": latency_s, "latency_source": source},
                 "reason": reason}
        self.append_event(event)
        return event

    def exclude_run(self, run_id: str, reason: str) -> Dict[str, Any]:
        event = {
            "protocol_version": PROTOCOL_VERSION,
            "event": "model_eval_exclusion",
            "timestamp": utc_now(),
            "run_id": run_id,
            "reason": reason,
        }
        self.append_event(event)
        return event

    def create_blind_pack(self, case_id: str, stage: str, seed: int) -> Path:
        require_safe_path_component(case_id, "case ID")
        require_safe_path_component(stage, "stage")
        events = [event for event in self.load_events()
                  if event.get("event") == "model_eval_run"
                  and event.get("case_id") == case_id
                  and event.get("stage") == stage
                  and event.get("status") == "success"
                  and event.get("artifacts")]
        if len(events) < 2:
            raise ValueError("need at least two successful artifact-producing runs")

        randomizer = random.Random(seed)
        randomizer.shuffle(events)
        pack_dir = self.blind_dir / (case_id + "-" + stage)
        pack_dir.mkdir(parents=True, exist_ok=True)
        mapping = {}
        review_rows = []
        for index, event in enumerate(events):
            label = chr(ord("A") + index)
            source = Path(event["artifacts"][0]["path"])
            destination = pack_dir / (label + source.suffix.lower())
            shutil.copy2(str(source), str(destination))
            mapping[label] = event["run_id"]
            review_rows.append({
                "label": label,
                "scanability": "",
                "clarity": "",
                "specificity": "",
                "naturalness": "",
                "confidence": "",
                "notes": "",
            })
        (pack_dir / "mapping.json").write_text(
            json.dumps(mapping, indent=2) + "\n", encoding="utf-8"
        )
        with (pack_dir / "review.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(review_rows[0].keys()))
            writer.writeheader()
            writer.writerows(review_rows)
        return pack_dir

    def import_human_review(self, review_csv: Path, reviewer: str) -> int:
        mapping_path = review_csv.parent / "mapping.json"
        if not mapping_path.exists():
            raise ValueError("mapping.json is missing beside the review CSV")
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        imported = 0
        with review_csv.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                label = row.get("label", "")
                if label not in mapping:
                    raise ValueError("unknown blind label: %s" % label)
                fields = ("scanability", "clarity", "specificity", "naturalness", "confidence")
                if not all(row.get(field, "").strip() for field in fields):
                    continue
                self.add_human_scores(
                    mapping[label],
                    *(int(row[field]) for field in fields),
                    reviewer=reviewer,
                    notes=row.get("notes") or None,
                )
                imported += 1
        return imported

    def aggregate(self, by_case: bool = False) -> List[Dict[str, Any]]:
        events = self.load_events()
        excluded_run_ids = {event["run_id"] for event in events
                            if event.get("event") == "model_eval_exclusion"}
        ats_by_run = {event["run_id"]: event for event in events
                      if event.get("event") == "model_eval_ats"}
        human_by_run: Dict[str, List[Dict[str, Any]]] = {}
        audits_by_run: Dict[str, List[Dict[str, Any]]] = {}
        judges_by_run: Dict[str, List[Dict[str, Any]]] = {}
        corrections_by_run: Dict[str, Dict[str, Any]] = {}
        for event in events:
            if event.get("event") == "model_eval_human":
                human_by_run.setdefault(event["run_id"], []).append(event)
            elif event.get("event") == "model_eval_audit":
                audits_by_run.setdefault(event["run_id"], []).append(event)
            elif event.get("event") == "model_eval_judge":
                judges_by_run.setdefault(event["run_id"], []).append(event)
            elif event.get("event") == "model_eval_correction":
                corrections_by_run.setdefault(event["run_id"], {}).update(event.get("fields", {}))

        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for event in events:
            if event.get("event") != "model_eval_run":
                continue
            if event["run_id"] in excluded_run_ids:
                continue
            key = (
                event.get("cohort", "legacy"),
                event["provider"],
                event["model"],
                event["reasoning"],
                event["stage"],
                event["case_id"] if by_case else "all",
            )
            enriched = dict(event)
            enriched.update(corrections_by_run.get(event["run_id"], {}))
            enriched["ats"] = ats_by_run.get(event["run_id"])
            enriched["human"] = human_by_run.get(event["run_id"], [])
            enriched["audits"] = audits_by_run.get(event["run_id"], [])
            enriched["judges"] = judges_by_run.get(event["run_id"], [])
            groups.setdefault(key, []).append(enriched)

        rows = []
        for key, runs in sorted(groups.items()):
            exact_runs = [run for run in runs if run.get("token_source") == "exact"]
            exact_tokens = [run["total_tokens"] for run in exact_runs
                            if run.get("total_tokens") is not None]
            exact_inputs = [run["total_input_tokens"] for run in exact_runs
                            if run.get("total_input_tokens") is not None]
            exact_outputs = [run["output_tokens"] for run in exact_runs
                             if run.get("output_tokens") is not None]
            exact_reasoning = [run["reasoning_tokens"] for run in exact_runs
                               if run.get("reasoning_tokens") is not None]
            cache_rates = [run["cache_hit_rate"] for run in exact_runs
                           if run.get("cache_hit_rate") is not None]
            ats_medians = [run["ats"]["median"] for run in runs if run.get("ats")]
            human_means = [rating["mean"] for run in runs for rating in run.get("human", [])]
            judge_means = [rating["mean"] for run in runs for rating in run.get("judges", [])]
            judge_latencies = [rating["telemetry"]["latency_s"]
                               for run in runs for rating in run.get("judges", [])
                               if isinstance(rating.get("telemetry"), dict)
                               and rating["telemetry"].get("latency_s") is not None]
            judge_tokens = [rating["telemetry"]["total_tokens"]
                            for run in runs for rating in run.get("judges", [])
                            if isinstance(rating.get("telemetry"), dict)
                            and rating["telemetry"].get("total_tokens") is not None]
            judge_costs = [rating["telemetry"]["cost_usd"]
                           for run in runs for rating in run.get("judges", [])
                           if isinstance(rating.get("telemetry"), dict)
                           and rating["telemetry"].get("cost_usd") is not None]
            audit_failures = sum(any(not audit["ok"] for audit in run.get("audits", [])) for run in runs)
            quality_reviewed = [
                run for run in runs
                if run.get("audits") and (
                    any(audit.get("ok") is not True for audit in run["audits"])
                    or run.get("judges")
                )
            ]
            quality_passes = sum(
                all(audit.get("ok") is True for audit in run["audits"])
                and bool(run.get("judges"))
                and all(
                    judge.get("factuality_pass") is True
                    and judge.get("mean", 0) >= 4.0
                    and min(
                        (judge.get("ratings", {}).get(name, 0)
                         for name in JUDGE_DIMENSIONS),
                        default=0,
                    ) >= 3
                    and judge.get("ratings", {}).get("confidence", 0) >= 4
                    for judge in run["judges"]
                )
                for run in quality_reviewed
            )
            costs = [run["cost_usd"] for run in runs if run.get("cost_usd") is not None]
            successful = sum(run.get("status") == "success" for run in runs)
            rows.append({
                "cohort": key[0],
                "provider": key[1],
                "model": key[2],
                "reasoning": key[3],
                "stage": key[4],
                "case_id": key[5],
                "runs": len(runs),
                "success_rate": round(successful / len(runs), 4),
                "p50_latency_s": percentile(
                    [run["latency_s"] for run in runs if run.get("latency_s") is not None], 0.5
                ),
                "mean_exact_tokens": round(statistics.mean(exact_tokens), 1) if exact_tokens else None,
                "mean_exact_input": round(statistics.mean(exact_inputs), 1) if exact_inputs else None,
                "mean_exact_output": round(statistics.mean(exact_outputs), 1) if exact_outputs else None,
                "mean_exact_reasoning": round(statistics.mean(exact_reasoning), 1) if exact_reasoning else None,
                "mean_cache_hit_rate": round(statistics.mean(cache_rates), 4) if cache_rates else None,
                "token_coverage": len(exact_tokens),
                "median_ats": statistics.median(ats_medians) if ats_medians else None,
                "mean_human": round(statistics.mean(human_means), 3) if human_means else None,
                "mean_judge": round(statistics.mean(judge_means), 3) if judge_means else None,
                "mean_judge_latency_s": round(statistics.mean(judge_latencies), 3)
                if judge_latencies else None,
                "mean_judge_tokens": round(statistics.mean(judge_tokens), 1)
                if judge_tokens else None,
                "mean_judge_cost_usd": round(statistics.mean(judge_costs), 4)
                if judge_costs else None,
                "quality_reviewed": len(quality_reviewed),
                "quality_passes": quality_passes,
                "quality_pass_rate": round(quality_passes / len(quality_reviewed), 4)
                if quality_reviewed else None,
                "audit_failures": audit_failures,
                "mean_cost_usd": round(statistics.mean(costs), 4) if costs else None,
                "mean_interventions": round(statistics.mean(
                    [run.get("interventions", 0) for run in runs]
                ), 3),
            })
        return rows


def default_repo_root() -> Path:
    explicit_root = os.environ.get("RESUME_BUILDER_EVAL_REPO")
    if explicit_root:
        return Path(explicit_root)
    conductor_root = os.environ.get("CONDUCTOR_ROOT_PATH")
    script_root = Path(__file__).resolve().parent.parent
    if conductor_root:
        candidate = Path(conductor_root)
        # Conductor exports its own repository root even when this evaluator
        # is being run in a separate checkout. Only redirect when the target
        # is demonstrably another checkout of this same tool.
        if (candidate / "scripts" / Path(__file__).name).is_file():
            return candidate
    return script_root


def optional_path(value: Optional[str]) -> Optional[Path]:
    return Path(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=default_repo_root())
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="create the local eval workspace")

    start = subparsers.add_parser("start", help="start a timed model run")
    start.add_argument("--case", required=True)
    start.add_argument("--stage", required=True, choices=STAGES)
    start.add_argument("--provider", required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--reasoning", default="default")
    start.add_argument("--cohort", default="pilot-v1")
    start.add_argument("--case-path")
    start.add_argument("--prompt-path")
    start.add_argument("--plan", help="subscription or billing plan used")

    finish = subparsers.add_parser("finish", help="finish and persist a model run")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", choices=("success", "partial", "failed"), required=True)
    finish.add_argument("--artifact", action="append", default=[])
    finish.add_argument("--input-tokens", type=int)
    finish.add_argument("--output-tokens", type=int)
    finish.add_argument("--reasoning-tokens", type=int)
    finish.add_argument("--cached-input-tokens", type=int)
    finish.add_argument("--cache-write-input-tokens", type=int)
    finish.add_argument("--total-tokens", type=int)
    finish.add_argument(
        "--token-source",
        choices=("exact", "estimated", "unavailable"),
        default="unavailable",
    )
    finish.add_argument("--turns", type=int)
    finish.add_argument("--interventions", type=int, default=0)
    finish.add_argument("--retries", type=int, default=0)
    finish.add_argument("--notes")
    finish.add_argument("--latency-s", type=float, help="provider-reported end-to-end latency")
    finish.add_argument("--cost-usd", type=float, help="provider-reported or equivalent cost")
    finish.add_argument(
        "--cost-basis",
        choices=("provider-reported", "list-price-proxy", "subscription", "unavailable"),
        default="unavailable",
    )
    finish.add_argument("--resolved-model", help="canonical model ID reported by the provider")

    ats = subparsers.add_parser("ats", help="attach repeated HackerRank ATS scores")
    ats.add_argument("--run-id", required=True)
    ats.add_argument("--score", action="append", type=float, required=True)
    ats.add_argument("--scorer", default="interviewstreet/hiring-agent")

    human = subparsers.add_parser("human", help="attach a human readability rating")
    human.add_argument("--run-id", required=True)
    human.add_argument("--scanability", type=int, required=True)
    human.add_argument("--clarity", type=int, required=True)
    human.add_argument("--specificity", type=int, required=True)
    human.add_argument("--naturalness", type=int, required=True)
    human.add_argument("--confidence", type=int, required=True)
    human.add_argument("--reviewer", default="owner")
    human.add_argument("--notes")

    audit = subparsers.add_parser("audit", help="attach a deterministic stage audit JSON report")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--report", type=Path, required=True)

    judge = subparsers.add_parser("judge", help="attach a blinded cross-model judge JSON verdict")
    judge.add_argument("--run-id", required=True)
    judge.add_argument("--provider", required=True)
    judge.add_argument("--model", required=True)
    judge.add_argument("--verdict", type=Path, required=True)

    correct = subparsers.add_parser("correct-latency", help="append a latency correction")
    correct.add_argument("--run-id", required=True)
    correct.add_argument("--latency-s", type=float, required=True)
    correct.add_argument("--source", required=True)
    correct.add_argument("--reason", required=True)

    exclude = subparsers.add_parser("exclude", help="exclude an invalid run without deleting it")
    exclude.add_argument("--run-id", required=True)
    exclude.add_argument("--reason", required=True)

    blind = subparsers.add_parser("blind", help="create a blinded human-review pack")
    blind.add_argument("--case", required=True)
    blind.add_argument("--stage", required=True, choices=STAGES)
    blind.add_argument("--seed", type=int, default=1)

    import_human = subparsers.add_parser("import-human", help="import a filled blind review CSV")
    import_human.add_argument("--review-csv", type=Path, required=True)
    import_human.add_argument("--reviewer", default="owner")

    report = subparsers.add_parser("report", help="summarize accumulated model evals")
    report.add_argument("--json", action="store_true")
    report.add_argument("--by-case", action="store_true")
    return parser


def print_report(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("No model eval runs recorded yet.")
        return
    header = (
        f"{'cohort':12} {'case':12} {'provider/model':30} {'stage':18} {'n':>3} {'ok%':>6} "
        f"{'gate%':>6} {'p50_s':>8} {'in/out':>15} {'cache':>7} {'cost$':>8} "
        f"{'ats':>7} {'human':>7} {'judge':>7} {'audit':>5} {'help':>6}"
    )
    print(header)
    for row in rows:
        identity = (row["provider"] + "/" + row["model"])[:34]
        values = {
            "latency": "-" if row["p50_latency_s"] is None else "%.1f" % row["p50_latency_s"],
            "tokens": "-" if row["mean_exact_input"] is None else "%.0f/%.0f" % (
                row["mean_exact_input"], row["mean_exact_output"] or 0
            ),
            "cache": "-" if row["mean_cache_hit_rate"] is None else "%.0f%%" % (
                row["mean_cache_hit_rate"] * 100
            ),
            "cost": "-" if row["mean_cost_usd"] is None else "%.3f" % row["mean_cost_usd"],
            "ats": "-" if row["median_ats"] is None else "%.1f" % row["median_ats"],
            "human": "-" if row["mean_human"] is None else "%.2f" % row["mean_human"],
            "judge": "-" if row["mean_judge"] is None else "%.2f" % row["mean_judge"],
            "gate": "-" if row["quality_pass_rate"] is None else "%.0f%%" % (
                row["quality_pass_rate"] * 100
            ),
        }
        print(
            f"{row['cohort'][:12]:12} {row['case_id'][:12]:12} {identity[:30]:30} "
            f"{row['stage'][:18]:18} {row['runs']:>3} "
            f"{row['success_rate'] * 100:>5.0f}% {values['gate']:>6} {values['latency']:>8} "
            f"{values['tokens']:>15} {values['cache']:>7} {values['cost']:>8} "
            f"{values['ats']:>7} "
            f"{values['human']:>7} "
            f"{values['judge']:>7} {row['audit_failures']:>5} "
            f"{row['mean_interventions']:>6.2f}"
        )


def main() -> None:
    args = build_parser().parse_args()
    store = EvalStore(args.repo_root)
    if args.command == "init":
        store.initialize()
        print(store.eval_root)
    elif args.command == "start":
        run_id = store.start_run(
            args.case,
            args.stage,
            args.provider,
            args.model,
            args.reasoning,
            args.cohort,
            optional_path(args.case_path),
            optional_path(args.prompt_path),
            args.plan,
        )
        print(run_id)
    elif args.command == "finish":
        event = store.finish_run(
            args.run_id,
            args.status,
            [Path(path) for path in args.artifact],
            args.input_tokens,
            args.output_tokens,
            args.reasoning_tokens,
            args.cached_input_tokens,
            args.cache_write_input_tokens,
            args.total_tokens,
            args.token_source,
            args.turns,
            args.interventions,
            args.retries,
            args.notes,
            args.latency_s,
            args.cost_usd,
            args.cost_basis,
            args.resolved_model,
        )
        print(json.dumps(event, indent=2))
    elif args.command == "ats":
        print(json.dumps(store.add_ats_scores(args.run_id, args.score, args.scorer), indent=2))
    elif args.command == "human":
        print(json.dumps(store.add_human_scores(
            args.run_id,
            args.scanability,
            args.clarity,
            args.specificity,
            args.naturalness,
            args.confidence,
            args.reviewer,
            args.notes,
        ), indent=2))
    elif args.command == "exclude":
        print(json.dumps(store.exclude_run(args.run_id, args.reason), indent=2))
    elif args.command == "blind":
        print(store.create_blind_pack(args.case, args.stage, args.seed))
    elif args.command == "import-human":
        print("Imported %d review(s)." % store.import_human_review(
            args.review_csv, args.reviewer
        ))
    elif args.command == "audit":
        print(json.dumps(store.add_audit(args.run_id, json.loads(
            args.report.read_text(encoding="utf-8")
        )), indent=2))
    elif args.command == "judge":
        print(json.dumps(store.add_judge(
            args.run_id, args.provider, args.model,
            json.loads(args.verdict.read_text(encoding="utf-8")),
        ), indent=2))
    elif args.command == "correct-latency":
        print(json.dumps(store.correct_latency(
            args.run_id, args.latency_s, args.source, args.reason
        ), indent=2))
    elif args.command == "report":
        rows = store.aggregate(args.by_case)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print_report(rows)


if __name__ == "__main__":
    main()
