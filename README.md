# resume-builder

Find postings, generate a tailored one-page LaTeX resume for any job
description, and (optionally) autofill the application — all from a rich
personal knowledge base plus a set of Claude Code skills.

## Setting it up for yourself

Everything person-specific lives in `knowledge/`, which is gitignored — your
real data never gets committed. The repo ships `knowledge.example/` with
placeholder templates showing the expected format. To make this repo yours:

1. **Install prerequisites:**
   ```bash
   brew install tectonic poppler   # tectonic compiles the LaTeX; poppler's pdftotext runs the ATS check
   ```
2. **Copy the templates**: `cp -r knowledge.example knowledge`
3. **Fill in `knowledge/profile.md`, `education.md`, and `skills.md`** with
   your info, and add files under `knowledge/experience/`,
   `knowledge/research/`, and `knowledge/projects/` with your own history
   (use the template files as format examples: YAML frontmatter + rich
   prose + seed bullets). The richer the prose, the better the tailoring —
   but avoid pasting internal-only system names, proprietary architecture,
   or non-public metrics from employers if you ever intend to make your
   fork public.
4. **Delete or rewrite `knowledge/rules.md`** — it holds per-person
   selection preferences (which roles to always include, where to archive
   finished PDFs). The skill works fine without it.
5. Open the folder in Claude Code (or Codex — see below) and run **`/start`**
   — the guided entry point. It scans Simplify's job boards, walks you
   through an interactive checklist (board, categories, recency, whether to
   compare against a current offer), then offers to tailor and/or apply to
   whatever looks worth pursuing from the results.

The skill itself (`.claude/skills/tailor-resume/SKILL.md`) is fully
person-agnostic — it fills the template header/education from your
`knowledge/` files, so no template editing is needed.

**Using Codex (or another agent) instead of Claude Code:** the repo ships an
`AGENTS.md` that points any agent at the same pipeline. In Codex, just ask
"tailor my resume to this JD: <url or paste>" from inside the repo — no
slash command needed. (`/start`'s guided job-scan flow is Claude Code
specific, since it relies on the `AskUserQuestion` tool; other agents can
still drive `tailor-resume`/`apply` directly.)

## Quickstart: `/start`

```
/start
```

This is the recommended way to use the repo day-to-day, once `knowledge/`
is filled in. It's a thin wrapper around `/job-scan` (see below) that also
checks `knowledge/` is set up first — the scan → triage → act flow now
lives in `job-scan` itself, so `/job-scan` and `/start` behave the same way
once setup is done:

1. **Scan** — asks an interactive checklist for board (new grad /
   internship), categories, recency window, and whether to compare
   postings against a current offer.
2. **Triage** — shows the compact results, confirms the list looks right,
   then asks which postings (if any) to act on and whether to tailor only
   or tailor + apply.
3. **Act, in parallel** — every selected posting is handed to its own
   subagent, all launched together rather than one at a time, so tailoring
   N postings costs about the same wall-clock time as tailoring one. Each
   subagent first checks the JD for hard eligibility blockers (work
   authorization/visa/clearance requirements, graduation-year or
   start-date cohort mismatches) against your `knowledge/` profile and
   skips with a reported reason rather than wasting effort on a posting
   you can't actually take; otherwise it tailors, archives the PDF, and
   (for tailor + apply) fills the application in its own browser tab,
   always stopping at final review — never auto-submitting.

Just want to browse without triggering the act step? Say so when asked, or
answer "just browsing" at the first triage prompt.

## Usage

From inside this directory in Claude Code:

```
/tailor https://jobs.example.com/swe-intern-2026
```

or, if the page is JS-rendered / behind a login wall:

```
# paste the JD text into jd.txt, then:
/tailor jd.txt
```

(The command is `/tailor`, not `/resume` — `/resume` is Claude Code's
built-in session-resume command and would shadow it.)

You can also just ask in plain language ("tailor my resume to this JD: …") —
the `tailor-resume` skill triggers on that too.

Output: `build/resume.pdf` (one page, Jake's Resume LaTeX format).

## How it works

1. The `tailor-resume` skill fetches the JD (URL, file, or pasted text).
2. Reads everything under `knowledge/` — your full history in prose form.
3. Scores each experience / research / project by JD relevance.
4. Picks 3–4 experiences + 1–2 projects + filtered skills/coursework to fit one page.
5. Tailors bullets to mirror JD vocabulary (truthfully — never invents).
6. Fills `templates/jakes_resume.tex`, writes `build/resume.tex`, compiles to PDF.
7. Runs an ATS-safety pass (clean text extraction, no garbled ligatures or
   math-mode symbols), checks required JD keywords actually appear in the
   extracted text, and verifies the result is exactly one page.

## Adding a new experience / project

Drop a new markdown file into the right folder:

```
knowledge/
  experience/<company-year>.md      # internships, jobs
  research/<project-slug>.md         # lab / research positions
  projects/<project-slug>.md         # personal / class projects
```

Use any existing file as a template. The richer the prose, the better the
tailoring — include metrics, scale, stakeholders, design tradeoffs, even
material that won't fit on the resume. The AI's job is to compress; the
knowledge base's job is to remember everything.

## Editing the master profile

- `knowledge/profile.md` — name, email, github, linkedin, phone, optional
  `website:` (a filled portfolio URL earns a small bonus with AI screeners)
- `knowledge/education.md` — school, dates, GPA, coursework, honors
- `knowledge/skills.md` — full skill master list (filtered per JD at gen time)
- `knowledge/rules.md` — optional per-person selection & output rules
- `knowledge/demographics.md` — optional voluntary EEO / self-identification
  answers, pronouns, relocation and consent toggles. Purely to stop the
  autofiller asking the same questions on every application. Every field is
  optional: blank means "ask me," and `decline` is a real answer meaning
  "pick the form's decline-to-answer option." Nothing here is ever inferred
  from your name, school, or anything else.

Project files (`knowledge/projects/*.md`) also take:
- `repo:` — bare `github.com/you/project` form; rendered as a visible link
  on the resume in place of a date. AI resume screeners (e.g. HackerRank's
  open-source ATS, see below) deduct per unlinked project, so fill this in
  for any project without an employer/academic exemption.
- `demo:` — optional live-demo URL; a small score boost if set.
- `origin:` — `self` (default, needs a `repo:`), `work`, or `research`
  (the latter two are exempt from the link requirement — no public repo
  expected).

## Scoring against HackerRank's open-source ATS (optional)

HackerRank open-sourced the ATS it uses to triage intern applications
([interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent)).
Run `/ats-score [pdf-path | --all]` to score a built resume (or the whole
archived corpus under `~/Desktop/Tailored Resumes/`) against its public
rubric. First run clones the tool into `tools/hiring-agent/` and sets it up
to use Claude Haiku (needs `ANTHROPIC_API_KEY` in your shell environment or
`tools/hiring-agent/.env`). The scorer is known to be non-deterministic —
treat the output as directional signal (and a checklist of concrete,
fixable deductions like missing project links), never a target score.

## Scanning Simplify's job boards (optional)

Run `/job-scan [new-grad|internship] [--dsa] [--pm] [--swe] [--quant] [--hw] [--startup] [--days N] [--compare-offer]`
to pull recent postings and print a single lean markdown table. Anything you
don't pass as a flag is asked as an interactive checklist instead — bare
`/job-scan` works fine.

- Sources: Simplify Jobs' community-maintained GitHub boards
  (`SimplifyJobs/New-Grad-Positions`, `SimplifyJobs/Summer2027-Internships`).
  New Grad runs also merge two speedyapply live sources —
  `speedyapply/2027-SWE-College-Jobs` and `speedyapply/2027-AI-College-Jobs`
  (AI/ML + quant-focused, e.g. GTS, Man Group, Flow Traders) — each carrying
  many postings the other two don't. All three are cross-source deduped
  (same company/role collapsed to one row) before filtering. Neither
  speedyapply board is wired in for internship runs. If a speedyapply fetch
  fails, the scan still runs on whatever sources succeeded and says so.
- Categories are Simplify's own: `swe` (Software Engineering), `pm`
  (Product Management), `dsa` (Data Science, AI & Machine Learning), `quant`
  (Quantitative Finance) are the default set; `hw` (Hardware Engineering) is
  opt-in for a narrower audience and not included unless asked for.
  `--startup` is offered as a flag but is always a pass-through — Simplify's
  data has no startup/company-size signal to filter on.
- Parsing is done by `scripts/parse_simplify_jobs.py` and
  `scripts/parse_speedyapply_jobs.py`, not by the model eyeballing raw
  HTML/Markdown — the boards run to 1,000+ rows across several tables, so a
  real parser is both faster and more reliable.
- Cross-source deduping and already-applied filtering run in
  `scripts/merge_and_filter_jobs.py` (unit-tested in
  `scripts/test_merge_and_filter_jobs.py`) rather than being re-derived by
  the model each scan — same thresholds every run, and reproducible.
- Every network fetch (board files, JD pages for degree/salary checks) goes
  through `scripts/fetch_urls.py` (unit-tested in
  `scripts/test_fetch_urls.py`), a small concurrent fetcher rather than
  hand-rolled `curl` prose per call site — one JD is never fetched twice in
  the same scan, one bad URL never sinks the batch, and failure handling is
  identical everywhere it's used.
  "Already applied" is judged primarily from `knowledge/metrics.jsonl`,
  which records the company *and role* of every tailored resume; archived
  PDF filenames are a fallback for runs predating that log. A company match
  alone never drops a posting outside a 3-day window — companies run several
  new-grad postings at once, so an unconfirmed match is surfaced with a note
  instead of being hidden.
- Recency is a plain rolling window, `--days N`, defaulting to **7**. It's
  always relative to today, not derived from any application-history file —
  applications don't always happen in posting order, so a "since my last
  application" cutoff would be unreliable.
- Output is always a single table — `Company | Role | TC | Location | Posted
  | Tier | Note` under `--compare-offer` (Tier column dropped otherwise) —
  never grouped prose lists.
- `--compare-offer` judges surfaced postings against
  `knowledge/current_offer.md` (optional, freeform prose — copy
  `knowledge.example/current_offer.md` to fill in), classifying each row
  into Better / Comparable / Worth a skim. The judgment is deliberately
  lenient — it flags a posting whenever it looks better, equal, or just
  unclear, and only stays quiet when a posting reads as a clear step down on
  every signal, so a promising option is never silently filtered out. It
  never fabricates a comp number that isn't in either source.
- Postings carrying the advanced-degree flag (🎓) get their JD checked before
  they reach the table, because board titles hide the requirement — a row
  listed as "Software Engineer Early Career" turned out to require a PhD as a
  minimum qualification. A posting is dropped only when it requires a degree
  above the one in `knowledge/education.md`, so a Master's or PhD candidate
  keeps the rows a bachelor's candidate drops. An unreachable JD keeps the
  posting and marks it unverified rather than dropping it.
- Known **application caps** (some employers limit how many roles you may
  have open at once) are noted per posting, so a scarce slot isn't spent on a
  weak-fit role. The count is only ever taken from the JD, the portal, or
  well-established public policy — never guessed.

After reporting results, `/job-scan` also offers to act on them — the same
triage-then-parallel-subagents flow described in the Quickstart above (list
confirmation, pick postings + tailor/apply, fan out to one subagent per
posting). Answer "just browsing" at that prompt if you only want the scan
output this run. Each fork's eligibility check only blocks on
citizenship/visa/clearance, degree level, or graduation-year/cohort
mismatches against `knowledge/profile.md` / `knowledge/education.md` — a
stated years-of-experience minimum never blocks a fork on its own; a strong
new-grad candidate applies past it regardless.

## Applying with the autofiller (optional)

Web-form filling is handled by the third-party
[job-apply plugin](https://github.com/neonwatty/job-apply-plugin) (MIT), which
drives the [Claude in Chrome](https://chromewebstore.google.com/detail/claude-in-chrome)
extension — no Playwright or API key needed. It supports LinkedIn Easy Apply,
Greenhouse, Ashby, Lever, Rippling, and Workday, and **never submits**: it
always stops at final review and leaves the Submit click to you.

Setup (once):

```bash
claude plugin marketplace add neonwatty/job-apply-plugin
claude plugin install job-apply@neonwatty-plugins
```

plus the Claude in Chrome extension, logged into your job sites.

Run `/app-profile-sync` to push contact/education/experience facts from
`knowledge/` into the plugin's local store (`~/.job-apply/`, plaintext JSON —
treat it like your resume).

Then apply with **`/apply <job-url>`** — the recommended entry point. It
always tailors first (same pipeline as `/tailor`), archives the PDF to
`~/Desktop/Tailored Resumes/`, points the plugin at that exact file, and
fills the application, stopping at final review for you to submit. Never
fill an application with a resume that wasn't tailored for that posting —
calling `/job-apply:job-apply` directly skips that guarantee.

## Layout

```
resume-builder/
├── AGENTS.md                       # entry point for Codex & other agents
├── .claude/
│   ├── commands/start.md           # thin /start wrapper (Claude Code) — recommended entry point
│   ├── commands/job-scan.md        # thin /job-scan wrapper (Claude Code)
│   ├── commands/tailor.md          # thin /tailor wrapper (Claude Code)
│   ├── commands/ats-score.md       # thin /ats-score wrapper (Claude Code)
│   ├── commands/app-profile-sync.md # thin /app-profile-sync wrapper (Claude Code)
│   ├── commands/apply.md           # thin /apply wrapper (Claude Code)
│   ├── skills/start/               # setup check + thin wrapper around job-scan (/start)
│   │   └── SKILL.md
│   ├── skills/job-scan/            # scan -> triage -> parallel tailor/apply (/job-scan)
│   │   └── SKILL.md
│   ├── skills/tailor-resume/       # the pipeline (person-agnostic)
│   │   └── SKILL.md
│   ├── skills/ats-score/           # optional HackerRank-ATS scoring harness
│   │   └── SKILL.md
│   ├── skills/app-profile-sync/    # syncs knowledge/ into the job-apply plugin store
│   │   └── SKILL.md
│   └── skills/apply/               # tailor-then-autofill orchestrator (/apply)
│       └── SKILL.md
├── knowledge.example/               # placeholder templates (committed)
│   ├── profile.md
│   ├── education.md
│   ├── skills.md
│   ├── rules.md
│   ├── current_offer.md            # optional, used by /job-scan --compare-offer
│   ├── demographics.md             # optional voluntary EEO / autofill answers
│   ├── experience/
│   ├── research/
│   └── projects/
├── knowledge/                      # YOUR full history (gitignored, not committed)
│   ├── profile.md
│   ├── education.md
│   ├── skills.md
│   ├── rules.md                    # optional per-person preferences
│   ├── current_offer.md            # optional, used by /job-scan --compare-offer
│   ├── demographics.md             # optional voluntary EEO / autofill answers
│   ├── experience/                 # one file per role
│   ├── research/                   # one file per research position
│   └── projects/                   # one file per project
├── templates/jakes_resume.tex     # parameterized LaTeX template
├── scripts/
│   ├── parse_simplify_jobs.py      # HTML-table parser used by /job-scan
│   ├── parse_speedyapply_jobs.py   # second-source parser (New Grad runs)
│   ├── merge_and_filter_jobs.py    # cross-source dedupe + already-applied filter
│   ├── test_merge_and_filter_jobs.py
│   ├── fetch_urls.py               # concurrent URL fetcher used by /job-scan and /ats-score
│   ├── test_fetch_urls.py
│   └── log_metric.py               # appends run records to knowledge/metrics.jsonl
├── build/                           # generated, gitignored
├── tools/hiring-agent/              # cloned by /ats-score on first run, gitignored
├── eval/                            # /ats-score output cache, gitignored
└── README.md
```
