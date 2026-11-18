---
name: start
description: The guided entry point for this repo — connect the user's resume, local repos, GitHub and past Claude/Codex sessions, fill knowledge/ and then ask for whatever is still missing (probing for metrics), set up the tunable settings (scan defaults, blocked companies, offer comparison, archive folder, optional ATS-scoring and Jev keys) and the application autofiller, then scan the job boards and tailor + apply to what the user picks. Use whenever the user opens this repo fresh or says anything like "start", "begin", "set me up", "get going", "do your thing", "find and apply to jobs", or hands the repo to an agent without a specific task; also for "/start settings" to revisit the settings.
---

# start — Set up, scan, act

This is the first thing to run in a fresh clone, and what an agent should do
whenever the user just says "start" or "begin" without a specific task. On a
fresh clone it always walks the same fixed sequence, so the user knows what
is coming:

1. **Connect sources** (Step 1.1): resume file, repos on this machine,
   GitHub, Claude Code / Codex session history.
2. **Draft `knowledge/`** from whatever they connected (Steps 1.2–1.3).
3. **Ask for everything still missing** (Step 1.4): required profile and
   education fields first, then the numbers behind thin bullets.
4. **Settings** (Step 1.6): scan defaults, blocked companies, offer
   comparison, archive folder, optional API keys.
5. **Autofill setup** (Step 1.7): on by default.
6. **Scan and act** (Step 2): tailor + apply to the postings they pick.

Say this sequence to the user in two or three lines before Step 1.1, so they
know setup ends in a scan. Nothing is acted on silently: every write to
`knowledge/` is shown first, and applications always stop at final review.

`/start settings` (or "change my settings") jumps straight to Step 1.6 and
then 1.7, and stops after them.

## Step 1 — Check setup

Everything downstream reads `knowledge/`. A scan still runs without it, but
tailoring and applying can't do anything useful, so resolve this first.

```bash
test -d knowledge || echo MISSING
ls knowledge/experience/*.md knowledge/projects/*.md 2>/dev/null | wc -l
grep -rlE 'Company Name|Your Title|1-2 paragraphs|Project Name|project-slug' \
  knowledge/ 2>/dev/null
```

Read those three results together:

- **`knowledge/` missing** → `cp -r knowledge.example knowledge`, then treat
  it as unfilled below.
- **Zero experience/project files, or any file still carrying template
  placeholder text** → unfilled. Go to Step 1.1.
- **Otherwise** → filled. Skip to Step 1.5, but still run Step 1.6 if
  `knowledge/rules.md` has no "Job scan defaults" section, and Step 1.7 if
  the autofiller isn't installed and `rules.md` doesn't say
  `Default action: tailor only`.

Never scan-then-tailor off a placeholder file. A resume built from
`knowledge.example/` is fabricated content with the user's name on it, which
is the one failure this repo must never produce.

## Step 1.1 — Connect sources

Only when Step 1 found `knowledge/` unfilled. Always ask this, in one
`AskUserQuestion` call (multi-select, all four options; each one says what
it reads):

- **"My resume file"** — a PDF/.docx/.tex/.md they point you at. Step 1.2.
- **"Repos on this machine"** — git history and READMEs. Step 1.3.
- **"My GitHub account"** — repos and merged PRs via `gh`. Step 1.3b.
- **"Claude Code / Codex history"** — their own past sessions, as leads for
  questions. Step 1.3c.

Read the answer this way:

- One or more picked → run those steps in the order above, then Step 1.4.
- Nothing picked, or "Other" saying they'd rather type it → interview them
  directly against the `knowledge.example/` schema, then Step 1.4.
- "Other" saying skip / just scan → continue to Step 1.5 and say plainly
  that this run is scan-only: no tailoring or applying, because there is
  nothing truthful to tailor from.

Every source is a **drafting aid, not an authority.** It produces a draft
the user confirms. Anything you could not read out of a source becomes a
Step 1.4 question or a `TODO` line, never a guess.

## Step 1.2 — Ingest an existing resume

Extract text first; do not read a PDF by eye.

```bash
pdftotext -layout "<their resume.pdf>" -    # .pdf
# .docx: unzip -p "<file>" word/document.xml | sed -e 's/<[^>]*>/ /g'
# .tex / .md / .txt: cat
```

Map what you find onto the `knowledge.example/` schema: one file per role in
`knowledge/experience/<company>-<year>.md`, one per project in
`knowledge/projects/<slug>.md`, plus `profile.md`, `education.md`, and
`skills.md`. Copy each resume bullet into that file's **Seed bullets**
section verbatim — those are already battle-tested phrasing and are the most
valuable thing in the file.

A resume is compressed, so expect gaps. Ask about the ones that matter:
missing employment dates or locations, a project with no repo link (required
for `origin: self`), and a listed technology with no role or project behind
it. Do not fill any of these from inference.

## Step 1.3 — Ingest from projects on this machine

Ask for a code directory to look in (their Claude Code project folders under
`~/.claude/projects/` encode real paths in their names, so offer those as
candidates). Then, per repo, read only what's actually there:

```bash
cat README.md 2>/dev/null | head -60         # what it is, in their words
git -C <repo> log --format='%ad' --date=format:'%b %Y' | tail -1   # start
git -C <repo> log --format='%ad' --date=format:'%b %Y' | head -1   # end
git -C <repo> log --author="$(git -C <repo> config user.email)" --oneline | wc -l
git -C <repo> remote get-url origin 2>/dev/null                    # repo: link
```

Judge scale from the repo itself (languages present, test suite, whether it
deploys anywhere), and put it in **Context**. Propose the shortlist of repos
worth writing up before writing anything, and let the user cut it. A tutorial
follow-along or an abandoned scratch repo is worse than nothing on a resume.

Commit count is context for you, never a resume bullet. "Wrote 300 commits"
says nothing a reader values.

## Step 1.3b — GitHub

Via `gh`. Check `gh auth status` first. If `gh` is missing or not signed in,
the user asked for this source, so don't skip it silently: tell them to run
`gh auth login` themselves (or install `gh`) and say "continue", or to drop
this source.

```bash
gh repo list --limit 50 --json name,description,primaryLanguage,isFork,pushedAt,url,stargazerCount
gh pr list --repo <owner/repo> --author @me --state merged --limit 20 --json title,mergedAt,url
```

Use it to find repos that were never cloned here, spot forks, and see merged
PRs to other people's projects (open-source contributions are worth a
bullet). Treat forks and repos with no commits of the user's own as noise.
Everything still goes through the shortlist and the Step 1.4 questions.

## Step 1.3c — Claude Code / Codex session history

Git history says when and how much; it doesn't say what was hard or what the
work achieved. The user's own past sessions can fill that in. Read-only.
Candidates are the project folders under `~/.claude/projects/` (their names
encode real repo paths) and `~/.codex/sessions`; match them to the repos and
projects already drafted, and ask about any session-heavy project that has
no file yet.

```bash
ls ~/.claude/projects/<encoded-repo-path>/*.jsonl 2>/dev/null | wc -l   # Claude Code
ls ~/.codex/sessions 2>/dev/null | head                                  # Codex
```

Read each session's first user prompt and its closing assistant summary, not
the whole transcript. They show what was attempted, what shipped, and what
the user said it was for. Use them to write sharper questions in Step 1.4
("your sessions show a caching rewrite in March — what did it speed up, and
by how much?"). A session is a lead, never a source: nothing from it goes into
`knowledge/` until the user confirms it, and a number an assistant stated in a
session is not a measurement. Transcripts can hold secrets and unrelated
private work, so never copy raw transcript text into a file.

## Step 1.4 — Ask for what's still missing

Always runs after Step 1.1's sources (or the interview), and whenever the
user asks to add to `knowledge/` later. Two passes, in this order.

**Pass 1 — required fields.** Check each and ask for every one still blank,
batched into as few `AskUserQuestion` calls as possible (free-text answers
via "Other" are fine):

- `profile.md`: name, email, phone, city/state, LinkedIn, GitHub (website
  optional).
- `education.md`: school, degree and major, graduation month and year
  (job-scan and the eligibility checks depend on these), GPA if they want
  it shown, relevant coursework.
- `skills.md`: languages, frameworks, tools — confirm the drafted list and
  ask what's missing.
- At least one file in `experience/`, `research/` or `projects/`, each with
  dates, and a `repo:` link for any self-built project.

Show each file's draft before writing it. A field they decline stays blank
(or `TODO`); never fill it from inference.

**Pass 2 — strength of the detail.** Unquantified bullets are the single biggest
quality gap in a generated resume, and the moment to fix it is now, while
the user is already thinking about the work, not mid-tailor when they're
focused on a specific posting.

Read what you ingested and judge it. Do not write a counter for this.
Pattern-matching digits gets it wrong in both directions: it takes `2023` or
"5 packages" as evidence, and misses "cut the nightly job from overnight to
before standup," which is a real result stated in plain words.

The question per file is whether a reader learns **how much** the work
mattered, or only what it touched. Thin looks like "improved performance,"
"worked on the data pipeline," "built an internal tool" — true, unfalsifiable,
and identical to what every other candidate writes. Sufficient means the file
carries at least a couple of bullets where the scale, the delta, or the
before-and-after is legible, whether or not it's written as a number.

Judge the file as a whole. One vague bullet in a file that is otherwise
specific is fine and not worth a question.

For files that read thin, ask the user about that file's specific work. Ask
concretely, never "any metrics?" — a vague prompt gets a vague answer:

- How much faster, and measured against what baseline?
- How many users, requests, rows, or dollars did it touch?
- How many people were on the team, and what did you own alone?
- What was the before-and-after on the number anyone actually tracked?

Batch these with `AskUserQuestion` across files rather than interrogating
file by file, and cap it at the two or three weakest files per run. This is a
prompt, not a gate: **if the user doesn't know a number or doesn't want to
share it, accept that immediately and move on.** Record the gap as a `TODO`
line in the file so a later run can ask again.

Never invent, estimate, or round up a metric the user did not state, and
never convert a vague memory into a specific figure. An unquantified true
bullet beats a quantified invented one, and the latter is the kind of thing
that falls apart in an interview.

## Step 1.4a — Offer to start tracking metrics (optional)

Only when Step 1.4 left `TODO` gaps for a project the user still maintains
and the number could be measured going forward (latency, error rate, usage,
build or test time, cost). Ask once per run with `AskUserQuestion`:
"Want me to add lightweight metric tracking to <repo>?" with options "Yes,
show me the plan first", "Not now". Never ask about repos that are archived,
belong to someone else, or have nothing measurable.

If they say yes, propose the smallest thing that works, as a plan, before
touching anything: one append-only event log (a JSONL file plus a tiny logging
helper, the same shape as this repo's `scripts/log_metric.py`) and the one or
two call sites that emit the events, on a new branch in *their* repo so it's a
reviewable diff they can drop. No new dependencies, no network calls, no
services, and nothing committed or pushed without their say-so.

Say plainly that tracking only measures from now on. It cannot produce a
number for past work, so the `TODO` stays until real data exists. Record
where the log lives in that file's `TODO` line so a later run can read it.

## Step 1.5 — One-time star nudge

Only on the very first `/start` in a given clone, and only once ever.

```bash
test -f .star-prompted && echo prompted || echo first-run
```

If it prints `prompted`, say nothing at all and move on. If it prints
`first-run`, create the marker **before** mentioning anything, so a crash or
an interrupted run can't cause a second ask:

```bash
touch .star-prompted
```

Then include exactly one short line in your next message, alongside whatever
else you were going to say:

> If this repo saves you time, a star helps others find it:
> https://github.com/damienwin/resume-builder

Rules for this nudge, all of them firm:

- **Once per clone, ever.** The marker file is gitignored, so it never
  travels with the repo and never gets committed.
- **Never blocking.** It is one line inside a message that already had a
  purpose — not an `AskUserQuestion`, not a confirmation prompt, and never a
  reason to pause the scan.
- **Never repeated**, never rephrased later, and never raised again if the
  user ignores it, declines, or says nothing. No second attempt in a later
  session, no "just a reminder."
- **Never a condition.** Nothing in this repo works differently based on
  whether the user stars it. Don't imply otherwise, don't ask whether they
  did it, and don't check.

## Step 1.6 — Settings

Runs on a fresh setup, on `/start settings`, or when Step 1 found no "Job
scan defaults" in `rules.md`. Two `AskUserQuestion` calls, then write the
answers.

**Call 1 (four questions):**

1. **Board** — New Grad / Internship.
2. **Categories** (multi-select) — Software engineering, Data science & ML,
   Quant, Product management. Hardware is opt-in via "Other".
3. **Blocked companies** — "None" / "Yes, I'll list them" (current or past
   employers, places they'd never work, companies they already have an
   offer from).
4. **Compare postings against an offer?** — "No" / "Yes, I'll describe it".

**Call 2 (three questions):**

1. **Where to save tailored PDFs** — "~/Desktop/Tailored Resumes
   (Recommended)" / "Only build/ in this repo" / Other.
2. **Score resumes with HackerRank's open-source ATS?** — "Yes, I'll add an
   Anthropic API key" / "Not now". Costs a few cents per scored PDF via
   Claude Haiku.
3. **Sharper degree checks with Jev?** — "Yes, I'll add a Typesafe key" /
   "Not now". Only public JD text is sent.

**Write the answers:**

- `knowledge/rules.md`, replacing the template's questions under these
  headings (keep any other sections the user wrote):

  ```
  ## Job scan defaults
  - Board: new-grad
  - Categories: swe, dsa, quant
  - Recency: since last scan
  - Compare offer: no
  - Default action: tailor + apply

  ## Output archive
  - Folder: ~/Desktop/Tailored Resumes/<board>/
  ```

- Blocked companies, one per line, to `knowledge/excluded_companies.txt`
  (create it; append without duplicates). Every scan and the prewarm drop
  them.
- An offer: interview them against `knowledge.example/current_offer.md` and
  write `knowledge/current_offer.md`. Ask whether they want a flat
  "Comparison bar" figure in `rules.md` instead, which keeps the employer's
  name out of scan output.
- API keys: **never ask for a key in chat and never print one.** Tell the
  user to copy `.env.example` to `.env` (if it doesn't exist) and add the
  line themselves — `ANTHROPIC_API_KEY=...` for ATS scoring,
  `TYPESAFE_API_KEY=...` for Jev — then say "done". Confirm presence without
  showing the value:

  ```bash
  grep -qE '^ANTHROPIC_API_KEY=.+' .env && echo set || echo missing
  ```

  A key they skip is fine; both features degrade cleanly without one.

Then tell them in one line that `/start settings` changes any of this later.

## Step 1.7 — Autofill setup (on by default)

Applying is part of the default flow: picking a posting means tailor +
apply, stopping at final review. Check the job-apply plugin:

```bash
ls -d ~/.claude/plugins/cache/neonwatty-plugins/job-apply/*/scripts/job-apply-store.py 2>/dev/null
```

- **Installed** → run the **app-profile-sync** skill now so forms are
  answered from `knowledge/`. Skip it if it already ran since `knowledge/`
  last changed.
- **Missing** → say it's a third-party MIT plugin that fills Greenhouse,
  Lever, Ashby, Workday, Rippling and LinkedIn Easy Apply forms through the
  Claude in Chrome extension and never submits. Ask once: "Install it now
  (Recommended)" / "Tailor only for now". On install, run exactly:

  ```bash
  claude plugin marketplace add neonwatty/job-apply-plugin
  claude plugin install job-apply@neonwatty-plugins
  ```

  then tell them to install the Claude in Chrome extension and sign in to
  their job sites themselves, and run app-profile-sync. On "Tailor only",
  write `Default action: tailor only` in `rules.md` so later runs don't ask
  again.
- Not in Claude Code (no plugin system) → say autofill needs Claude Code,
  write `Default action: tailor only`, and move on.

## Step 2 — Scan (and act)

Run the **job-scan** skill (`.claude/skills/job-scan/SKILL.md`) exactly as
`/job-scan` would, with no preset flags. Its Step 0 reads the defaults Step
1.6 just wrote and asks only for what's still unresolved. Let it run all the
way through, including its Step 6 "Offer to act", which
follows `job-scan/references/acting-on-results.md` (confirm the list, pick
postings + tailor/apply, fan out to parallel subagent forks). Do not skip or
shortcut any of its steps — job-scan's triage-and-act flow is its own
default behavior, so `/start` doesn't add a separate triage layer on top.

**One exception:** if the user chose "skip for now, just scan" in Step 1.1,
`knowledge/` is still unfilled. Let job-scan run and print its table, but
stop before its Step 6 act flow. Say why in one line, and offer to fill
`knowledge/` then, since the postings they just saw are usually what makes
the setup feel worth doing.

## Step 3 — Report

job-scan's own report and act-flow summary already cover everything acted
on. Just remind the user this was `/start`'s guided entry point and they can
rerun `/job-scan` directly next time for the same flow without the setup
check in Step 1.

If Step 1.4 left any `TODO` metric gaps, name the files once here so they
know what's still thin. Don't re-ask in the same run.

## Hard rules

- Never skip job-scan's act flow, or the questions its Step 0 still needs.
- On a fresh setup, never skip Steps 1.1, 1.4, 1.6 or 1.7, and never skip
  asking for a required field that is still blank.
- Never ask for, echo or store an API key value; the user writes `.env`.
- Never install the job-apply plugin without a yes in this run.
- Never tailor or apply from placeholder `knowledge.example/` content.
- Never invent a metric, date, scale figure, or repo link during ingestion.
  Ask, or write a `TODO`.
- Session transcripts and GitHub data are leads, not facts: read-only, never
  copied raw into `knowledge/`, and never a source for a number.
- Never modify, commit to, or push another repo (including metric tracking)
  without an explicit yes for that repo.
- Ingestion always drafts for confirmation and never writes over a
  `knowledge/` file the user already filled in without showing what changes.
- Inherits every hard rule from `job-scan`, `tailor-resume`, and `apply` —
  in particular: never invent facts, and applications always stop at final
  review, never auto-submit.
