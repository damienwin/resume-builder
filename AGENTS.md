# resume-builder — agent instructions

Generates a tailored one-page LaTeX resume for a job description from the
personal knowledge base under `knowledge/`.

Each skill below is the single source of truth for its own workflow and
carries its own rules — read the skill when you run it rather than working
from this file's summary. The skills are plain markdown and not
Claude-specific apart from tool names: where one says `WebFetch`, use your
URL-fetch capability; everything else is standard shell and file tools.

| Skill | Command | Does |
|---|---|---|
| `tailor-resume` | `/tailor <url\|file>` | The core task: JD → verified one-page PDF |
| `job-scan` | `/job-scan` | Scan Simplify + speedyapply boards, filtered; can fan out to tailor/apply |
| `apply` | `/apply <url>` | Tailors for that posting first, then autofills the form |
| `start` | `/start` | Guided entry point: scan → triage → tailor/apply |
| `ats-score` | `/ats-score [pdf]` | Diagnostic score against HackerRank's open-source ATS |
| `app-profile-sync` | `/app-profile-sync` | Push `knowledge/` facts into the job-apply plugin's store |

Skills live in `.claude/skills/<name>/SKILL.md`.

## Repo-wide invariants

These hold everywhere and are the ones worth stating outside a skill:

- **All facts come from `knowledge/`.** Never invent metrics, employers,
  dates, coursework, comp figures, or posting details. If a JD asks for
  something the knowledge base doesn't support, leave it out and report it
  as a gap.
- **`knowledge/rules.md` overrides generic defaults** wherever it applies
  (selection preferences, output archive, job-scan defaults).
- **Never apply with an untailored resume.** `apply` always runs
  `tailor-resume` for that specific posting first.
- **Acting always requires explicit user selection.** Scanning reports;
  tailoring and applying happen only on postings the user picked.
- The `job-apply` plugin stops at final review and never submits.

## Conductor cross-model workflow

Use a separate local Conductor workspace for each role. The `knowledge/`
directory contains private resume facts and is copied only for local
workspaces via `.worktreeinclude`; never use a cloud workspace for a task
that reads it.

1. **Claude Code — triage and design.** Ask Claude to inspect the relevant
   skills and scripts, identify constraints and regression risks, and return
   a concise implementation brief. Do not ask it to edit the implementation
   branch in this phase.
2. **Codex — implementation and verification.** Start a fresh workspace from
   `main` using the Claude brief. Codex owns the patch, runs the targeted
   Python tests, and summarizes the exact behavior changed. Keep one
   independent concern per workspace/branch.
3. **Claude Code — independent review.** Create a workspace from Codex's
   implementation branch. Ask Claude to review `origin/main...HEAD` for
   factual-resume invariants, concurrency hazards, privacy leaks, and missing
   tests. Leave review comments rather than changing code unless explicitly
   asked to make a fix.
4. **Codex — resolve and hand off.** Give Codex the review findings in the
   implementation workspace. It fixes confirmed issues, reruns the focused
   test suite, and prepares the branch for your final review.

For a job search session, keep interactive scanning and any browser-based
application filling in one Claude Code workspace. Use Codex in separate
workspaces for deterministic work: resume generation from a supplied JD,
parser/test changes, PDF verification, and metrics analysis. Never run two
application-filling agents at once: `resumePath` is global and the apply
skill intentionally serializes uploads.

Cross-model benchmark runs follow `MODEL_EVALS.md`. Start and finish every
measured run with `scripts/model_eval.py`, preserve the exact provider/model
identifier and reasoning level, and write through the shared local eval store.
Never overwrite prior measurements when models, prompts, or skills change.

## Repo layout

- `knowledge/` — the user's history (profile, education, skills,
  `experience/`, `research/`, `projects/`, optional `rules.md`). Gitignored;
  absent on a fresh clone.
- `templates/jakes_resume.tex` — parameterized template; every
  `<<PLACEHOLDER>>` gets filled from `knowledge/`.
- `scripts/` — board parsers, the merge/already-applied filter (with tests),
  and the metrics pipeline: `log_metric.py` (append-only event log),
  `cc_transcripts.py` (reads Claude Code's own session transcripts for
  per-turn tokens/model/cost, cached incrementally), `build_run_metrics.py`
  (joins the two into `knowledge/runs.jsonl` — latency, tokens, cost, cache
  hit rate per run), `run_timer.py` (coarse step timing bridged across a
  skill's separate tool calls via a scratch file), `metrics_summary.py`
  (`--perf`/`--ab` CLI reports), and `build_metrics_dashboard.py` (renders
  `knowledge/dashboard.html`, now with latency/cost/token-mix panels).
- `scripts/` also holds the fail-closed phase wrappers that keep a run's
  deterministic work in one tool call instead of several: `gate_stage.py`
  (stage audit -> route plan -> timer mark), `compile_verify.py` (tectonic ->
  PDF verification), and `finalize_resume.py` (final gate -> archive ->
  routing record -> metric -> handoff). `resume_handoff.py` writes/verifies
  the hash-bound tailor→apply manifest that stops the fan-out from tailoring a
  posting twice; `sync_skills.py` generates the `.agents/skills` mirror from
  the canonical `.claude/skills` (run `--check` in CI).
- `build/`, `eval/`, `jd.txt` — generated/scratch, gitignored.
- `tools/hiring-agent/` — third-party clone of HackerRank's open-source ATS,
  used only by `ats-score`. Gitignored, not vendored.

## Setup for a new user

`knowledge/` won't exist on a fresh clone. Per "Setting it up for yourself"
in `README.md`: `cp -r knowledge.example knowledge`, fill it in, delete or
rewrite `knowledge/rules.md`, and install `tectonic` plus `poppler` (for
`pdftotext`). `knowledge/current_offer.md` is optional — only
`/job-scan --compare-offer` and `/start` use it.

After setup, point the user at **`/start`**. Agents without
`AskUserQuestion` should drive `tailor-resume` / `apply` directly instead.
