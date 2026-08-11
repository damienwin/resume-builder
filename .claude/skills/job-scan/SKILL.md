---
name: job-scan
description: Scan Simplify Jobs' New-Grad-Positions or Summer-Internships GitHub board for postings from the last N days (default 7), filtered interactively by category, with an optional comparison against the user's current offer. Use when the user wants to browse or check for new job/internship postings, e.g. "/job-scan", "any new SWE new grad roles?", "check simplify for internships".
---

# job-scan — Scan Simplify's job boards, filtered to what matters

Simplify Jobs maintains two community-updated GitHub repos of live postings:
`SimplifyJobs/New-Grad-Positions` and `SimplifyJobs/Summer2027-Internships`
(branch `dev`, file `README.md` in both). This skill fetches the live table,
filters it down, and prints a compact list — never a browser, never invented
postings.

## Step 0 — Resolve inputs

`$ARGUMENTS` may already specify some choices as flags:
`new-grad`/`internship`, `--dsa --pm --swe --quant --hw --startup`,
`--days N`, `--compare-offer`. Anything given via flags is used as-is and
skipped below.

**Check `knowledge/rules.md` first** for a "Job scan defaults" section. Any
input covered there (and not overridden by an explicit flag this run) is
used silently — do not ask about it. Only fall back to the interactive
checklist below for inputs that are covered by **neither** a flag **nor** a
`rules.md` default (e.g. `rules.md` doesn't exist yet, or only sets some of
the four inputs). If the user says something in the current message that
contradicts a stored default (e.g. "just this once, check internships"),
follow the message for this run only — don't rewrite `rules.md` from an
off-hand one-off request.

`AskUserQuestion` caps each question at 4 options, and there are 5 possible
categories, so this needs **two calls** when categories aren't fully pinned
by flags or `rules.md`:

**Call 1** — everything except the hardware opt-in, all questions together
(only for the inputs still unresolved after flags + `rules.md`):

1. **Board** — "New Grad" vs "Internship".
2. **Categories** (multi-select) — options: `swe` (Software Engineering),
   `pm` (Product Management), `dsa` (Data Science, AI & Machine Learning),
   `quant` (Quantitative Finance). Default if the user picks none: all
   four — this is the core set most users want. "Other" is never offered —
   it's a catch-all with no coherent category signal. `--startup` remains
   accepted as a flag for scripting convenience (always a pass-through,
   never filters), but do NOT offer it as a checklist option — presenting a
   no-op as a selectable filter misleads the user into thinking it does
   something.
3. **Recency** — "how many days back?" Default **7** if the user doesn't
   say otherwise. Always a plain rolling window from today — do NOT derive
   it from any file timestamp (a user may apply out of order, so the most
   recently *modified* tailored-resume file is not a reliable "last
   applied" signal).
4. **Compare to current offer?** — yes/no. If yes and
   `knowledge/current_offer.md` doesn't exist, this is handled gracefully in
   Step 4, not blocked here.

**Call 2** (skip if `--hw`, an explicit category flag, or `rules.md`
already covers hardware inclusion) — a single yes/no: **"Also include
Hardware Engineering roles?"** (default no — it's opt-in for a narrower
audience, not part of the core set). If yes, add `hw` to the active
category set from Call 1.

## Step 0.5 — Offer to save new defaults

If any input in Step 0 had to be asked via the checklist (i.e. `rules.md`
didn't already cover it), after the scan completes (Step 6) ask once
whether to save the answer(s) as future defaults in `knowledge/rules.md`'s
"Job scan defaults" section. Only write if they confirm. Skip this offer
entirely if `rules.md` already had a "Job scan defaults" section covering
everything asked this run.

## Step 1 — Resolve the source URL

- New Grad → `https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md`
- Internship → `https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md`

## Step 2 — Fetch

```bash
curl -sf <url> -o <scratchpad>/simplify_readme.md
```

Use `curl -sf` (the `-f` makes curl exit nonzero on an HTTP error instead of
saving an error page) via Bash, not `WebFetch` — WebFetch summarizes through
a small model and loses exact table structure, which the parser in Step 3
depends on. Save to a scratchpad temp file, never into the repo.

**If the fetch fails** (nonzero exit, empty file, or a file that doesn't
contain `<table` — e.g. GitHub rate-limited the request or is down): stop
and tell the user the scan couldn't run and why. Do NOT proceed and report
"0 postings found" — that reads as "no new postings" when the real story is
"couldn't check."

## Step 3 — Parse and filter

Run the repo's parser script rather than hand-parsing the HTML — the README
is thousands of lines across several large tables, and eyeballing that many
rows for company/role/flags/age is slow and error-prone (a manual first
pass missed same-day postings a script caught cleanly):

```bash
python3 scripts/parse_simplify_jobs.py <scratchpad>/simplify_readme.md \
  --categories <comma-separated active category flags, e.g. swe,dsa,quant or swe,pm,dsa,quant,hw> \
  --days <N from Step 0, default 7>
```

Output is JSON: `{"entries": [...], "scanned": N, "closed_excluded": N}`.
Each entry already has `category`, `company`, `role`, `location`, `age_raw`,
`apply_url`, and booleans `faang`/`adv_degree`/`no_sponsor`/`us_citizen` —
closed postings (Simplify replaces the whole apply-links cell with a bare
🔒 rather than marking company/role text) and anything older than the day
window are already excluded, and `↳` continuation rows are already resolved
to their parent company. Read the JSON directly; don't re-derive any of
this from the raw README.

## Step 3.5 — Filter out already-applied companies

The archive folder in `knowledge/rules.md`'s "Output archive" section
(`/Users/damienwin/Desktop/Tailored Resumes/<SUBFOLDER>/`) holds every
tailored resume ever produced, named `<TAG> Damien Nguyen.pdf`. A recently
created file there for a company means a resume was already tailored
against that company's JD — treat that as already applied and drop it from
this scan's results, so the same posting doesn't get re-surfaced scan after
scan.

- Resolve the matching subfolder for the board scanned this run (`New Grad
  27/` for New Grad, `Summer 26/` for Internship).
- List files with mtimes: `ls -la "<subfolder>"`.
- For each parsed entry (Step 3), check whether the company name appears
  (case-insensitive, substring match) in a filename with an mtime within
  the last **30 days**. A match means: drop this entry from the surfaced
  set entirely — before Step 4/5 rendering, not just visually hidden.
- Files older than 30 days are not a reliable "still relevant" signal (the
  company may be running a new posting since) — do not match against them.
- Track a count of how many entries were dropped this way, to report in
  Step 6. If none dropped, omit the mention entirely (don't pad the report
  with a zero-count line).

## Step 4 — Optional current-offer comparison

Only if the user opted in (Step 0, Call 1, Q4):
- `knowledge/current_offer.md` missing → still render the filtered list
  (Step 5, default rendering — there's nothing to rank against), then tell
  the user the file doesn't exist and offer to create it from
  `knowledge.example/current_offer.md`'s template — only if they confirm.
  No comparison this run.
- Present → check `knowledge/rules.md` for a "Comparison bar" override
  under "Job scan defaults" first. If set (e.g. a flat dollar figure), use
  **that** as the bar to beat instead of `current_offer.md`'s actual
  figure, and keep output generic — do not name the current employer in
  headers or lines when a bar override is active. If no override is set,
  fall back to comparing directly against `current_offer.md`'s stated
  company/comp/level.
- For each surfaced posting, classify into one of three tiers using what's
  in the posting, `current_offer.md`/the bar, and (per the rules.md note
  above) an optional levels.fyi lookup when comp isn't stated:
  - **Better** — a stated or levels.fyi-estimated comp beats the bar, a
    clearly higher level/seniority, or a company the user's own notes mark
    as preferred/aspirational.
  - **Comparable** — no explicit beat, but a real positive signal exists
    (🔥 FAANG+ flag, a levels.fyi estimate roughly at/near the bar, or a
    company/role tier obviously on par) — note "comp unknown (est. via
    levels.fyi: $X)" or "comp unknown" as applicable.
  - **Worth a skim** — no clear signal either way: unfamiliar or
    unverifiable company, no comp data (stated or estimable), nothing that
    reads as a step down. This is the catch-all that keeps the "never
    silently hide a good option" guarantee without needing a real signal to
    justify it.
  - **(Not classified / dropped)** — only when the posting is clearly worse
    on *every* available signal (comp confirmed below the bar via posting
    or levels.fyi, and level/company tier also read as a step down). This
    is the only case where a posting doesn't appear in Step 5's output at
    all.
  **Be lenient, not a strict gate:** when signal is genuinely absent or
  ambiguous, classify as "Worth a skim" rather than dropping it — the cost
  of one extra line is much lower than hiding a good option. Never invent a
  comp number or level that isn't stated in the posting, in
  `current_offer.md`, or returned by an actual levels.fyi lookup — always
  label a levels.fyi figure as an estimate, never as the posting's own
  stated number.

## Step 5 — Render

**Default (no `--compare-offer` this run):** group by category, one line
per posting, newest first within a group. Build the blurb only from real
parsed fields — never an invented company description:

```
### Software Engineering
- Arondite — Deployed SWE New Grad — London, UK — posted 1d ago
- ByteDance — SWE New Grad — Seattle, WA — 🔥 FAANG+ · posted 2d ago
- Homey — Junior SWE (AI-Native) — London, UK — 🛂 no sponsorship · posted 1d ago
```

**With `--compare-offer` (and `current_offer.md` present):** replace the
category grouping with a single compact list ranked **Better → Comparable →
Worth a skim** (ties broken newest-first). This is deliberately condensed —
comp data is rarely stated on these boards, so most postings land in
"Worth a skim," and a full category-by-category dump would bury the few
postings that actually carry a real signal.

- Render **Better** and **Comparable** postings in full, one line each,
  with a short rationale:
  ```
  ## vs. $100k bar

  **Better**
  (none this scan)

  **Comparable**
  - ByteDance — Backend Inference Runtime Engineer New Grad — San Jose, CA — 🔥 FAANG+, comp unknown
  - Salesforce — Software Engineer College Grad — 6 locations — 🔥 FAANG+, comp unknown
  - Samsara — Software Engineer 1 New Grad — London, UK — 🔥 FAANG+, comp unknown
  - Roblox — Software Engineer, Early Career — San Mateo, CA — 🔥 FAANG+, comp unknown
  ```
- Render **Worth a skim** as one condensed line per category (not one line
  per posting) — just enough to prove nothing was hidden, without the wall
  of text:
  ```
  **Worth a skim** (no clear signal, not dropped) — SWE: Homey, InterImage,
  Torch Technologies, IXL Learning ×3, Atoms, KBR, General Dynamics IT ×3,
  Torc Robotics · DSA: SentiLink ×2, Cortica, Tyson Foods, LiveScore,
  Varsity Brands · Quant: SentiLink, WallStreetQuants ×3
  ```
- If a posting was dropped as clearly worse (rare, given how little comp
  data exists), say so as a one-line count in Step 6, not in this list.

## Step 6 — Report

Tell the user:
- Total postings scanned vs. surfaced after category/date/closed filtering.
- The day-count window used and its resolved cutoff date.
- How many closed postings were excluded, if any.
- How many entries were dropped as already-applied (Step 3.5), if any.
- If offer-comparison was requested but `knowledge/current_offer.md` is
  missing, remind them here (and whether they asked you to create it).
- If `--compare-offer` was active and any posting was dropped as clearly
  worse on every signal (Step 4's last bullet), give a one-line count here
  (e.g. "3 postings excluded as a clear step down on comp/level/tier") —
  never silent, but never restated line-by-line either.

## Step 6.5 — Offer to act on results

This is now the default flow for every `/job-scan` run, not just `/start`'s
— skip it only if the user's `$ARGUMENTS` explicitly asked for scan-only
output (e.g. a `--no-act` style request in plain language) or if Step 5
surfaced zero postings.

1. **Confirm the list is good.** Ask the user (`AskUserQuestion`,
   single-select: "Looks good, let's act" / "Adjust the filters" / "Just
   browsing, no action") before doing anything else. If they want
   adjustments, go back to Step 0 with their new input rather than guessing
   — do not silently re-run with assumed changes. If "just browsing," stop
   here; the Step 6 report already covers this run.
2. **Pick postings and an action.** Once the list is confirmed, ask
   (`AskUserQuestion`, multi-select) which surfaced postings to act on,
   listed by company — role. For the ones selected, ask once more
   (single-select, applies to all selected postings this round) which
   action to take:
   - **Tailor + apply** — full `apply` skill flow per posting.
   - **Tailor only** — `tailor-resume` skill flow per posting, no
     autofill.
   - **Skip everything** — do nothing further.
3. **Dispatch one subagent fork per selected posting, launched together in
   a single batch so they run in parallel** — never process postings one at
   a time in the main conversation. This is the efficiency win: tailoring +
   filling is I/O- and compile-bound (network fetch, tectonic, browser
   automation), so N postings in parallel forks costs about the same wall
   time as one. Each fork's prompt must be self-contained (forks share your
   context, but still need explicit direction) and must:
   - Run the full `tailor-resume` skill for that posting (fetch the JD —
     for JS-rendered ATS pages that WebFetch can't extract, try Workday's
     `wday/cxs/<tenant>/<site>/job/...` JSON API or a `.md`/markdown
     alternate link some ATS platforms expose in the page `<head>`, before
     falling back to asking the user for pasted JD text), archive the
     verified PDF per `knowledge/rules.md`'s "Output archive" section, and
     log via `scripts/log_metric.py resume_tailor`.
   - **Before spending any tailoring effort**, check the JD for hard
     eligibility blockers stated as requirements (not preferences) that
     conflict with `knowledge/profile.md` / `knowledge/education.md` —
     right-to-work/visa/clearance requirements the profile doesn't meet,
     graduation-year or start-date cohorts that don't match, degree-level
     mismatches. If found, stop immediately, do not tailor or open a
     browser tab, and report the specific conflicting line back so the user
     can decide whether to override it.
   - If the action is "tailor + apply": update the job-apply plugin's
     `resumePath` via the store helper (never edit `~/.job-apply/` files
     directly), then open its **own new browser tab** via
     `tabs_create_mcp` (never touch a tab another fork or the main
     conversation is using) and run the `job-apply` skill's fill flow,
     stopping at final review — never submit. Leave ambiguous/subjective
     screening questions (self-reported years of experience, "select up to
     N" checklists with no true match) for the user rather than guessing,
     unless the knowledge base makes the answer unambiguous.
   - Log via `scripts/log_metric.py job_apply_e2e` when applicable.
   - Report back: archived resume path, what got filled vs. left for the
     user, and (if a browser tab was opened) enough to identify it for the
     user's final review.
4. **Summarize once all forks complete.** Report per-posting outcomes
   (tailored/applied/skipped-for-eligibility/left-for-review) — don't wait
   silently; each fork's completion surfaces as its own notification, so
   compile a running summary as they land rather than blocking on all of
   them before saying anything if the user asks for status mid-flight.

## Step 7 — Log metrics

Log this run for future reference (see `scripts/log_metric.py`):

```bash
python3 scripts/log_metric.py job_scan '{
  "board": "<new-grad|internship>",
  "categories": ["swe", "dsa", ...],
  "days": <N>,
  "scanned": <Step 3 scanned>,
  "closed_excluded": <Step 3 closed_excluded>,
  "already_applied_dropped": <Step 3.5 count>,
  "surfaced": <final count after 3.5, before compare-offer classification>,
  "compare_offer_used": <true|false>,
  "better_count": <N or omit if compare_offer_used is false>,
  "comparable_count": <N or omit>,
  "worth_a_skim_count": <N or omit>,
  "dropped_worse_count": <N or omit>
}'
```

Fire-and-forget — don't block or re-render the report on this call, and
don't mention it in the Step 6 report to the user (it's a background log,
not part of the scan results).

## Hard rules

- Never invent salary, comp, level, or company facts not present in the
  Simplify README or `knowledge/current_offer.md`.
- Comparison judgment is deliberately lenient: when in doubt, classify as
  "Worth a skim" rather than dropping a posting — the cost of one extra
  name in a condensed line is much lower than hiding a good offer. Still
  never invent a comp number or level that isn't in either source; say
  "comp unknown" instead.
- With `--compare-offer` active, the ranked/condensed rendering (Step 5) is
  the default output shape for everyone using this skill, not a one-off —
  don't fall back to the full category dump just because a run has few
  "Better"/"Comparable" hits. The condensed "Worth a skim" line exists
  precisely for that case.
- Read-only, except optionally creating `knowledge/current_offer.md` from
  the example template — and only with explicit user confirmation.
- `--startup` / the "startup" category is always a pass-through — never
  build a company-name denylist or other heuristic for it.
- Scanning always reports first; acting (Step 6.5) always requires explicit
  user selection of which postings and which action — never tailor or
  apply to a posting the user didn't pick.
- When acting on multiple postings, always fan out to parallel subagent
  forks (one per posting, launched in a single batch) rather than
  processing them sequentially in the main conversation.
- Every fork checks for hard eligibility blockers (work authorization,
  degree/graduation-year cohort mismatches, clearance requirements) before
  tailoring or opening a browser tab, and skips with a reported reason
  rather than guessing or proceeding past a stated disqualifier.
