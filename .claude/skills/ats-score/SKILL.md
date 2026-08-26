---
name: ats-score
description: Score a resume PDF against HackerRank's open-source ATS rubric (interviewstreet/hiring-agent). Use when the user wants to know how a tailored resume would score, wants to compare resumes, or wants to batch-evaluate the archived resume corpus.
---

# ats-score — Score resume(s) against the open-source HackerRank ATS

Runs [interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent)
locally against one or more resume PDFs and reports a median score + spread.
This is a **diagnostic tool, not a gate** — the underlying scorer is known to
be non-deterministic (the same PDF has scored 66–99 across 100 runs in public
testing), so treat the output as directional signal, never a pass/fail
threshold.

## Step 0 — Setup (idempotent, run once)

Check if `tools/hiring-agent/` exists in the repo root. If not:

```bash
git clone https://github.com/interviewstreet/hiring-agent tools/hiring-agent
cd tools/hiring-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Read `tools/hiring-agent/providers.json` at setup time (don't assume its
contents) to confirm which models/providers are available — the repo evolves.
Prefer the `anthropic` provider with model `claude-haiku-4-5` if present in
providers.json (cheap, no local model download, no Ollama dependency). Set in
`tools/hiring-agent/.env`:

```
DEFAULT_MODEL=claude-haiku-4-5
```

`ANTHROPIC_API_KEY` must be set in the shell environment or in
`tools/hiring-agent/.env` for that model to work (per `config.py`'s
`provider_for()`). **Never grep, cat, or otherwise display the key's value.**
If it's unset, tell the user to add it to `tools/hiring-agent/.env` or export
it, then stop and wait — do not fall back to a different provider without
asking, since Ollama/Gemini require their own separate setup.

If `providers.json` no longer has an `anthropic` entry when you check it,
fall back to Gemini (`GEMINI_API_KEY`) or Ollama, and tell the user which
provider you used and why.

## Step 1 — Score a single PDF

```bash
cd tools/hiring-agent
source .venv/bin/activate
python score.py <path-to-pdf> --role software_engineering_intern
```

Run this **3 times** by default (`--runs N` from the user overrides this).
Each run is independent (non-deterministic LLM scoring) — do not reuse a
cached result across runs; if `DEVELOPMENT_MODE` caching in `config.py`
interferes, note it but don't disable it without asking.

Parse each run's printed report (and/or the appended
`resume_evaluations_software_engineering_intern.csv` row) for:
`open_source`, `self_projects`, `production`, `technical_skills`,
`bonus_points.total`, `deductions.total`, and the final total.

## Step 2 — Report

For a single PDF:
- **Median total** and **min–max spread** across the runs.
- Per-category breakdown from the median-total run, with its evidence text.
- Any deduction reasons mentioned (link-related, generic-name, tutorial-project)
  — these are actionable in `knowledge/` or the template.
- Anything else (GitHub profile findings, bonus gaps) — route to a short
  "outside the resume" note rather than trying to fix it in the PDF.

## Step 3 — Batch mode

When asked to score the archived corpus (or given `--all` / a folder path):

```bash
find "$HOME/Desktop/Tailored Resumes" -name "*.pdf"
```

Each `score.py` run is an independent Anthropic API call — N PDFs × 3 runs
scored one at a time is N×3 sequential round-trips for no reason. Build the
full `(pdf, run-index)` work list up front and drive it through a **bounded
pool of 4 concurrent runs** (`xargs -P 4`, or the user's `--runs`/`--parallel`
override), not more — a wider pool risks provider rate limits producing 429s
that look like scoring failures rather than genuine low scores. A small
wrapper script (in the scratchpad, not the repo) that activates the venv and
runs one `(pdf, run-index)` pair, appending its parsed result to a per-run
file, keeps output from interleaving:

```bash
# one line per (pdf, run-index) pair in a worklist file, then:
xargs -P 4 -I{} <scratchpad>/score_one.sh {} < <scratchpad>/worklist.txt
```

**Retry a run once** on failure before reporting it, and **distinguish an
API/rate-limit failure from a genuine low score** in the aggregation — never
fold a 429 or timeout into the median as if it were a real run.

Once every PDF's runs are in, run Step 2's aggregation per PDF. Write results
to `eval/scores.json` in the repo root (gitignored) as
`{"<pdf-path>": {"median": N, "min": N, "max": N, "categories": {...}, "scored_at": "<ISO date>"}}`,
merging into any existing file rather than overwriting other entries. Report
a **corpus average** (mean of medians) and flag any deduction pattern that
recurs across most PDFs (e.g. "every resume loses 3-5 pts per project for
missing links") — that's a signal to fix the template/skill, not individual
resumes.

## Caveats to always state in the report

- Scores are noisy by design (LLM non-determinism); compare medians and
  trends across resumes/runs, never treat a single run's number as ground
  truth.
- GitHub enrichment hits the live GitHub API for the profile in the resume's
  `basics.url`/profiles — rate limits apply; an optional `GITHUB_TOKEN` in
  `tools/hiring-agent/.env` raises the limit.
- This is HackerRank's own public statement: the tool ranks/triages a large
  applicant pool, it is not what actually screens applications at most
  employers, and it is not a general ATS. Use it as one proxy signal among
  several, not the target to maximize.
