# Acting on job-scan results

Loaded from `job-scan/SKILL.md` Step 6, and reused by the `start` skill. This
is the triage-then-fan-out flow that turns a scan table into tailored resumes
and filled applications.

## 1. Confirm the list

Ask with `AskUserQuestion` (single-select) before doing anything else:
**"Looks good, let's act" / "Adjust the filters" / "Just browsing, no action."**

- *Adjust* → go back to the skill's Step 0 with their new input. Never
  silently re-run with assumed changes.
- *Just browsing* → stop. The Step 5 report already covers this run.

## 2. Pick postings and an action

`AskUserQuestion` (multi-select) for which surfaced postings to act on,
listed as `Company — Role`. Then one single-select action applying to all
postings picked this round:

- **Tailor + apply** — the full `apply` skill per posting.
- **Tailor only** — the `tailor-resume` skill per posting, no autofill.
- **Skip everything** — do nothing further.

## 3. Resolve shared state once, before dispatching

Both of these are resolved once in the main conversation, never per-fork —
otherwise parallel forks each stop and ask the user the same question.

- **Archive destination.** If `knowledge/rules.md` has no "Output archive"
  section (e.g. a fresh clone still on the `knowledge.example/` template),
  ask once now for the destination folder.
- **job-apply plugin availability** — only if any posting's action is
  "tailor + apply". Locate the store helper as in `app-profile-sync/SKILL.md`
  Step 0:
  ```bash
  ls -d ~/.claude/plugins/cache/neonwatty-plugins/job-apply/*/scripts/job-apply-store.py
  ```
  If it's missing, tell the user to install it and downgrade those postings to
  "tailor only" for this run rather than dispatching forks that will fail.

## 4. Dispatch one fork per posting, all in a single batch

Launch them **together, in parallel** — never one at a time in the main
conversation. Tailoring and filling are I/O- and compile-bound (network
fetch, tectonic, browser automation), so N postings in parallel cost roughly
the wall time of one. Forks share your context but still need explicit
direction, so each prompt must be self-contained.

Every fork must:

**a. Check hard eligibility blockers first**, before spending any tailoring
effort. These are categorical — either the candidate qualifies or the posting
is off the table:

- Graduation-year / start-date cohort mismatches, and degree-level or field
  mismatches (Master's or PhD required) — answered directly from
  `knowledge/profile.md` and `knowledge/education.md`.
- Right-to-work / visa / citizenship / clearance requirements. This is **not**
  stored in `knowledge/` — it's personal, and this repo may be forked or
  shared (see `rules.md`'s fork note). Check the job-apply plugin's local
  store instead. Resolve `$STORE` inside the fork (it does not inherit the
  main conversation's shell), exactly as in `app-profile-sync/SKILL.md`
  Step 0:
  ```bash
  STORE=$(ls -d ~/.claude/plugins/cache/neonwatty-plugins/job-apply/*/scripts/job-apply-store.py | head -1)
  python3 "$STORE" answer-find --question "<the JD's exact eligibility phrasing>"
  ```
  Use a confirmed stored answer; never re-ask. If nothing is stored, ask the
  user directly (every clone starts empty and gets asked independently) and,
  only with explicit consent, remember it via
  `answer-put --input <file> --remember-sensitive` (state `"confirmed"`,
  sensitivity `"high"`).

> **A stated years-of-experience minimum is explicitly NOT a stop
> condition.** "1–3 years required" or "2+ years post-Bachelor's" does not
> block a fork — companies routinely list an experience bar that a strong
> new-grad candidate applies past anyway, and the user has said to always
> attempt these rather than self-select out. Note it as a stretch in the
> fork's summary, then tailor and apply regardless.

If a requirement is genuinely unmet, or the user declines to answer: stop
immediately, do not tailor or open a browser tab, and report the specific
conflicting line so the user can decide whether to override it.

**b. Run the full `tailor-resume` skill** for the posting — fetch the JD,
archive the verified PDF per `rules.md`'s "Output archive" section, and log
via `scripts/log_metric.py resume_tailor`.

For JS-rendered ATS pages WebFetch can't extract, try in order: Workday's
`wday/cxs/<tenant>/<site>/job/...` JSON API, then a `.md`/markdown alternate
link some platforms expose in the page `<head>`, and only then fall back to
asking the user for pasted JD text.

**c. If the action is "tailor + apply"**, run the `apply` skill
(`.claude/skills/apply/SKILL.md`) for that URL exactly as `/apply <url>`
would — it already handles pointing the store at the archived PDF and filling
the form. Two of its details exist specifically because of this parallel
fan-out and must not be skipped: per-job `build/<slug>.*` working files, and
the lock held across the `resumePath` write and the resume upload (that field
is global and Chrome uploads one file at a time, so an unlocked fork can
attach another posting's resume). Open its **own** new tab via `tabs_create_mcp`; never touch a tab
another fork or the main conversation is using. Leave ambiguous or subjective
screening questions (self-reported years of experience, "select up to N"
checklists with no true match) for the user rather than guessing, unless
`knowledge/` makes the answer unambiguous. Log via
`scripts/log_metric.py job_apply_e2e`.

**d. Report back**: archived resume path, what got filled vs. left for the
user, and enough to identify the browser tab for final review.

## 5. Summarize as forks land

Each fork's completion surfaces its own notification. Compile a running
summary as results arrive rather than blocking silently on all of them — if
the user asks for status mid-flight, answer from what has landed. Final
summary covers per-posting outcomes: tailored / applied /
skipped-for-eligibility / left-for-review.
