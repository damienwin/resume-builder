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

## Repo layout

- `knowledge/` — the user's history (profile, education, skills,
  `experience/`, `research/`, `projects/`, optional `rules.md`). Gitignored;
  absent on a fresh clone.
- `templates/jakes_resume.tex` — parameterized template; every
  `<<PLACEHOLDER>>` gets filled from `knowledge/`.
- `scripts/` — board parsers, the merge/already-applied filter (with tests),
  and `log_metric.py`.
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
