# resume-builder — agent instructions

This repo generates a tailored one-page LaTeX resume for a job description
from the personal knowledge base under `knowledge/`.

## The one task this repo exists for

When the user provides a job posting URL, a JD file path, or pasted JD text
and asks for a tailored resume, follow the pipeline in
**`.claude/skills/tailor-resume/SKILL.md`** exactly. That file is plain
markdown and is the single source of truth for the workflow — it is not
Claude-specific apart from tool names:

- Where it says `WebFetch`, use your web search / URL fetch capability.
- Everything else (shell commands, file edits) uses standard tools.

Key invariants from that pipeline (do not skip):

- All facts come from `knowledge/` — **never invent metrics, employers,
  dates, or coursework**. If the JD asks for something the knowledge base
  doesn't support, leave it out.
- If `knowledge/rules.md` exists, its per-user selection and output
  preferences override the generic defaults.
- Output must be **exactly one page** — solve overflow by shrinking font
  and spacing before cutting content.
- Compile with `tectonic build/resume.tex` (writes `build/resume.pdf`).
- ATS-safety pass is mandatory: no math-mode `$\to$`/`$\times$`/`$\sim$` in
  bullet text, avoid `ffi`/`ffl` ligature words (e.g. prefer "burst load"
  over "burst traffic"), and verify clean extraction with
  `pdftotext -layout build/resume.pdf -` after every compile.
- Keyword coverage is checked against the same extracted text: every
  *required* JD skill the knowledge base truthfully supports must appear
  verbatim; unsupported ones are reported as gaps, never added.
- Every selected project renders a visible repo/demo link (from its
  `repo:`/`demo:` frontmatter only — never invented) in place of a date,
  unless the project's `origin:` is `work` or `research`. AI resume
  screeners (e.g. HackerRank's open-source ATS) deduct per unlinked
  project — this is the single biggest fixable score loss. Verify with
  `pdftotext -layout` that each link's displayed URL text appears
  unbroken.

## Repo layout

- `knowledge/` — the user's full history (profile, education, skills,
  experience/, research/, projects/, optional rules.md)
- `templates/jakes_resume.tex` — parameterized template; fill every
  `<<PLACEHOLDER>>` from `knowledge/`
- `build/` — generated output (gitignored)
- `jd.txt` — scratch file for pasted job descriptions (gitignored)
- `tools/hiring-agent/` — third-party clone of HackerRank's open-source ATS
  (interviewstreet/hiring-agent), used only by the optional `ats-score`
  skill (gitignored, not vendored)
- `eval/` — `/ats-score` output cache (gitignored)

## Optional: scoring against HackerRank's public ATS rubric

The `ats-score` skill (`.claude/skills/ats-score/SKILL.md`, `/ats-score`)
runs HackerRank's own open-sourced ATS locally against a built PDF and
reports a median score + spread. It's diagnostic only — the scorer is
non-deterministic — never treat it as a gate on `tailor-resume`.

## Optional: scanning Simplify's job boards, and the guided `/start` flow

The `job-scan` skill (`.claude/skills/job-scan/SKILL.md`, `/job-scan`) fetches
`SimplifyJobs/New-Grad-Positions` or `SimplifyJobs/Summer2027-Internships`
(raw `README.md`, branch `dev`) and prints a compact, filtered list of
postings — board, categories (`swe`/`pm`/`dsa`/`quant` by default, `hw`
opt-in, `startup` always a pass-through, never filtered), and recency window
(`--days N`, default 7, a plain rolling window) can be passed as flags or,
for anything unspecified, asked as an interactive checklist (Claude Code
only — needs `AskUserQuestion`). It never fabricates a posting or a comp
figure; when `--compare-offer` is used against `knowledge/current_offer.md`,
the comparison is deliberately **lenient** — it surfaces a posting whenever
it looks better, equal, or simply unclear, and only stays quiet when a
posting reads as a clear step down on every available signal. This skill
only scans and reports; it never tailors or applies on its own.

The `start` skill (`.claude/skills/start/SKILL.md`, `/start`) is the
recommended entry point for a fresh session: it runs `job-scan`'s full
checklist, then asks which surfaced postings (if any) to act on, and for
each selected one runs either `tailor-resume` or the full `apply` flow —
never on a posting the user didn't explicitly pick.

## Optional: application-form autofill

Web-form filling is delegated to the third-party `job-apply` Claude Code
plugin (neonwatty/job-apply-plugin) via the Claude in Chrome extension; the
plugin always stops at final review and never submits. The `app-profile-sync`
skill (`.claude/skills/app-profile-sync/SKILL.md`, `/app-profile-sync`) merges
frontmatter facts from `knowledge/` into the plugin's local store — only
through the plugin's bundled helper script, never by editing `~/.job-apply/`
files directly. See "Applying with the autofiller" in `README.md`.

Applying end-to-end goes through the `apply` skill
(`.claude/skills/apply/SKILL.md`, `/apply <job-url>`), which **always runs
tailor-resume for that specific posting first**, points the plugin's
`resumePath` at the archived tailored PDF, then fills the form. Never fill
an application with a resume that wasn't tailored for that posting.

## Setup for a new user

`knowledge/` is gitignored and won't exist on a fresh clone. See "Setting it
up for yourself" in `README.md`: copy `knowledge.example/` to `knowledge/`
(`cp -r knowledge.example knowledge`), fill it in with your own history
(use the template files as format examples), delete or rewrite
`knowledge/rules.md`, and install `tectonic` + `poppler` (for `pdftotext`).
`knowledge/current_offer.md` is optional and only needed for
`/job-scan --compare-offer` / `/start`'s offer-comparison step.

After setup, point the user at **`/start`** (Claude Code) as the default way
to use this repo day-to-day — it chains job-scan, triage, and
tailor/apply into one guided flow. Other agents without `AskUserQuestion`
should drive `tailor-resume`/`apply` directly instead.
