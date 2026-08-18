---
name: job-scan
description: Scan Simplify Jobs' New-Grad-Positions or Summer-Internships GitHub board (New Grad runs also merge speedyapply/2027-SWE-College-Jobs) for postings from the last N days (default 7), filtered interactively by category, with an optional comparison against the user's current offer. Use when the user wants to browse or check for new job/internship postings, e.g. "/job-scan", "any new SWE new grad roles?", "check simplify for internships".
---

# job-scan — Scan job boards, filtered to what matters

Simplify Jobs maintains two community-updated GitHub repos of live postings:
`SimplifyJobs/New-Grad-Positions` and `SimplifyJobs/Summer2027-Internships`
(branch `dev`, file `README.md` in both). On the New Grad board, results are
merged with a second community-updated repo,
`speedyapply/2027-SWE-College-Jobs` (file `NEW_GRAD_USA.md`), which tracks
many postings Simplify's board doesn't. This skill fetches the live
table(s), filters them down, and prints a single compact list — never a
browser, never invented postings.

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

## Step 1 — Resolve the source URL(s)

- New Grad → `https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md`
  **plus** a second source,
  `https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md`
  — merged into every New Grad run by default (no flag/prompt gates this).
- Internship → `https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md`
  only — speedyapply's board isn't wired in for internships (different
  file/format, not yet reviewed).

## Step 2 — Fetch

```bash
curl -sf <simplify_url> -o <scratchpad>/simplify_readme.md
curl -sf <speedyapply_url> -o <scratchpad>/speedyapply_readme.md   # New Grad only
```

Use `curl -sf` (the `-f` makes curl exit nonzero on an HTTP error instead of
saving an error page) via Bash, not `WebFetch` — WebFetch summarizes through
a small model and loses exact table structure, which the parsers in Step 3
depend on. Save to scratchpad temp files, never into the repo.

**If the Simplify fetch fails** (nonzero exit, empty file, or a file that
doesn't contain `<table` — e.g. GitHub rate-limited the request or is down):
stop and tell the user the scan couldn't run and why. Do NOT proceed and
report "0 postings found" — that reads as "no new postings" when the real
story is "couldn't check." Simplify is the primary source.

**If the speedyapply fetch fails** (New Grad board only — nonzero exit,
empty file, or a file missing any `TABLE_` marker): this is *not* fatal —
it's a supplementary source. Continue the scan with Simplify results only,
and note the degraded fetch in Step 6's report rather than failing silently
or stopping the whole scan.

## Step 3 — Parse and filter

Run the repo's parser scripts rather than hand-parsing the raw text — these
files are thousands of lines across several large tables, and eyeballing
that many rows for company/role/flags/age is slow and error-prone (a manual
first pass missed same-day postings a script caught cleanly):

```bash
python3 scripts/parse_simplify_jobs.py <scratchpad>/simplify_readme.md \
  --categories <comma-separated active category flags, e.g. swe,dsa,quant or swe,pm,dsa,quant,hw> \
  --days <N from Step 0, default 7>

# New Grad board only, and only if the speedyapply fetch succeeded:
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply_readme.md \
  --categories <same category flags as above> \
  --days <same N as above>
```

Both scripts output the same JSON shape:
`{"entries": [...], "scanned": N, "closed_excluded": N}`. Each entry has
`category`, `company`, `role`, `location`, `age_raw`, `apply_url`, `source`
(`"simplify"` or `"speedyapply"`), and booleans
`faang`/`adv_degree`/`no_sponsor`/`us_citizen` — closed postings (Simplify
only: it replaces the whole apply-links cell with a bare 🔒 rather than
marking company/role text) and anything older than the day window are
already excluded, and Simplify's `↳` continuation rows are already resolved
to their parent company. speedyapply has no closed-posting marker
(`closed_excluded` is always 0 for it) and no adv_degree/no_sponsor/
us_citizen signal (always `false`) — that's a known gap in that source, not
a bug. Read the JSON directly; don't re-derive any of this from the raw
files.

When the speedyapply parser ran, concatenate its `entries` list with
Simplify's into one merged list (carry both `scanned` counts forward
separately for Step 6/7 — don't just sum them into one opaque number).

## Step 3.25 — Dedupe across sources (New Grad board only)

Only runs when the speedyapply parser also ran (Step 3). The same live
posting can legitimately appear on both boards, so before any
already-applied filtering, collapse cross-source duplicates out of the
merged entries list:

- Two entries are the same posting if either holds:
  - **Same `apply_url`** (compare with tracking query params stripped —
    some ATS links append `?gh_jid=`/`?utm_...` variants of the same
    canonical URL), or
  - **Same company** (case-insensitive) **and** a clearly matching `role`
    string — reuse the same "near-identical title / shared distinctive
    keywords" comparison described in Step 3.5 below rather than a new
    heuristic.
- When a duplicate pair is found, **keep the Simplify-sourced entry** (it
  carries the richer `faang`/`adv_degree`/`no_sponsor`/`us_citizen` flags)
  and drop the speedyapply one.
- Track a count of duplicates dropped this way, to report in Step 6. If
  none dropped, omit the mention entirely (same zero-count convention as
  Step 3.5).

## Step 3.5 — Filter out already-applied companies

The archive folder in `knowledge/rules.md`'s "Output archive" section
(`/Users/damienwin/Desktop/Tailored Resumes/<SUBFOLDER>/`) holds every
tailored resume ever produced, named `<TAG> Damien Nguyen.pdf`. A recently
created file there for a company means a resume was already tailored
against that company's JD — treat that as already applied and drop it from
this scan's results, so the same posting doesn't get re-surfaced scan after
scan. Applies identically regardless of `source` — runs over the merged,
cross-source-deduped list from Step 3.25 (or the plain Simplify list on
boards where speedyapply doesn't run).

- Resolve the matching subfolder for the board scanned this run (`New Grad
  27/` for New Grad, `Summer 26/` for Internship).
- List files with mtimes: `ls -la "<subfolder>"`.
- For each parsed entry (Step 3), check whether the company name appears
  (case-insensitive, substring match) in a filename with an mtime within
  the last **30 days**. A company match alone is *not* enough to drop —
  companies routinely post multiple distinct new-grad roles, and an
  archived resume only means one specific JD was already tailored.
  - **Tight window, no role check needed:** if the matching archive file's
    mtime is within the last **3 days**, treat it as the same posting and
    drop automatically — a same-company file that fresh is almost always
    this exact scan re-surfacing.
  - **Company matches but archive file is older than 3 days (up to the
    30-day cutoff):** also compare the parsed entry's `role` string against
    the archived filename/tag. Drop only if the role text is a clear match
    (near-identical title, or the filename's `<TAG>` — often a shortened
    role/company label — shares its distinctive keywords with the posting
    role, e.g. "Identity and Network Access" vs. a same-named tag). If the
    role reads as meaningfully different (different team, function, or
    title — e.g. "Officepy" vs. "Identity and Network Access"), do **not**
    auto-drop it: keep it in the surfaced set and append a short note to
    its Step 5 row, e.g. "(resume already archived for <Company> on
    <date>, different role)" — surface it rather than silently hide it,
    same principle as never silently hiding a good option.
- Files older than 30 days are not a reliable "still relevant" signal (the
  company may be running a new posting since) — do not match against them
  at all, not even for the role-text check.
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
- Before falling back to a levels.fyi estimate or "comp unknown": if the
  parsed entry itself has no comp (neither Simplify nor speedyapply table
  cells reliably carry salary data), fetch the posting's `apply_url` and
  scan the JD text for a stated salary/range. A directly stated JD figure
  is authoritative — prefer it over a levels.fyi estimate either way, and
  never relabel it as an "estimate." Skip this fetch for postings that are
  clearly going to be dropped on every other signal anyway (no point
  fetching a JD just to confirm a drop); it's meant to catch cases where a
  stated JD comp would have changed the tier.
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

Always a single lean markdown table, all surfaced postings in one table
(not grouped into per-category sections) — easiest to scan in chat. Columns:

**With `--compare-offer` active:**

| Company | Role | TC | Location | Posted | Tier | Note |
|---|---|---|---|---|---|---|

**Without `--compare-offer`:** same table minus the Tier column (there's no
bar to rank against).

- **Company / Role** — verbatim from the parsed entry.
- **TC** — a total-comp figure only when actually findable: stated in the
  posting itself, or (only when `--compare-offer` is active, per Step 4) a
  levels.fyi estimate — label it `~$X (est.)`. Otherwise `—`. Never invent
  a number, and never spend a levels.fyi lookup on a posting when
  `--compare-offer` isn't active — that lookup is Step 4's, not Step 5's.
- **Location** — verbatim.
- **Posted** — the `age_raw` field (e.g. `1d`, `3d`).
- **Tier** (compare-offer runs only) — `Better` / `Comparable` / `Worth a
  skim`, from Step 4's classification. Sort the table by tier (Better →
  Comparable → Worth a skim), newest first within each tier.
- **Note** — always a short, factual clause on **what the company actually
  does** (industry/product — general public knowledge about the company,
  e.g. "aerospace/spaceflight," "payments network," "healthcare provider
  for autism care") — not an invented detail about the specific role or
  team, and not filler ("great opportunity," "innovative company").
  - **With `--compare-offer` active:** after the company description,
    append *why the move could be worth it beyond the comp number* —
    brand/industry-leader standing, career-trajectory value, name
    recognition — grounded in a real signal (a `🔥 FAANG+` flag, known
    industry-leader status, a funded/notable startup) rather than
    generic enthusiasm. For a `Worth a skim` posting with no such signal,
    the company description alone is enough — don't manufacture a legacy
    angle where none exists.
  - **Without `--compare-offer`:** the company description is the whole
    Note — no rationale needed since there's nothing to compare against.
- Postings Step 4 drops as a clear step-down don't get a row — report the
  count in Step 6 instead, same as before.

Sort ties (same tier, same post date) by company name alphabetically for a
stable order across re-runs.

## Step 6 — Report

Tell the user:
- Total postings scanned vs. surfaced after category/date/closed filtering.
  On the New Grad board, break the scanned count down by source (Simplify
  vs. speedyapply) rather than one merged number.
- The day-count window used and its resolved cutoff date.
- How many closed postings were excluded, if any.
- If the speedyapply fetch failed (Step 2), say so here — the scan still
  ran, just Simplify-only for this run.
- How many cross-source duplicates were dropped (Step 3.25), if any.
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
   Before dispatching any forks, resolve two things once (not per-fork, so
   parallel forks don't each independently prompt the user for the same
   answer):
   - **Archive destination.** If `knowledge/rules.md` has no "Output
     archive" section (e.g. a fresh clone still on the `knowledge.example/`
     template), ask the user once now for the destination folder — don't
     let each fork ask independently.
   - **job-apply plugin availability** (only if any posting's action is
     "tailor + apply"). Locate the store helper as in
     `app-profile-sync/SKILL.md` Step 0
     (`ls -d ~/.claude/plugins/cache/neonwatty-plugins/job-apply/*/scripts/job-apply-store.py`).
     If missing, tell the user to install it and downgrade those postings'
     action to "tailor only" for this run rather than dispatching forks
     that will fail.
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
     eligibility blockers that conflict with `knowledge/profile.md` /
     `knowledge/education.md` — graduation-year or start-date cohorts that
     don't match, degree-level/field mismatches (e.g. Master's/PhD
     required), or right-to-work/visa/clearance requirements. These are
     categorical: either the candidate meets them or the posting is off the
     table entirely, no judgment call needed. **A stated years-of-experience
     minimum (e.g. "1-3 years required," "2+ years post-Bachelor's") is
     explicitly NOT a stop condition** — companies routinely list an
     experience bar that a strong new-grad candidate applies past anyway,
     and the candidate has said to always attempt these rather than
     self-select out. Note it in the report/fork summary as a stretch, but
     tailor and apply regardless. Cohort and degree-level checks are
     answered directly from `knowledge/`.
     Right-to-work/visa/citizenship/clearance eligibility is **not** stored
     in `knowledge/` (it's personal and would otherwise land in a
     git-tracked file — a real risk since this repo may be forked/shared,
     per `knowledge/rules.md`'s fork note) — instead check the job-apply
     plugin's local store first: `python3 "$STORE" answer-find --question
     "<the JD's exact eligibility phrasing, e.g. 'Are you a U.S.
     citizen?'>"` (see `app-profile-sync/SKILL.md` Step 0 for locating
     `$STORE`). If a confirmed answer exists there, use it — never re-ask.
     If none exists, ask the user directly (this is per-user, per-machine
     state — every clone of this repo, including other people's forks,
     starts with nothing stored and gets asked independently) and, only
     with their explicit consent, remember it via `answer-put --input
     <file> --remember-sensitive` (state `"confirmed"`, sensitivity
     `"high"`) so future runs don't ask again. If the JD requirement is
     genuinely unmet (or the user declines to answer), stop immediately, do
     not tailor or open a browser tab, and report the specific conflicting
     line back so the user can decide whether to override it.
   - If the action is "tailor + apply": run the full `apply` skill
     (`.claude/skills/apply/SKILL.md`) for that posting's URL, exactly as
     `/apply <url>` would — it already covers pointing the store at the
     archived PDF and filling the application. Open its own new browser
     tab via `tabs_create_mcp` (never touch a tab another fork or the main
     conversation is using). Leave ambiguous/subjective screening
     questions (self-reported years of experience, "select up to N"
     checklists with no true match) for the user rather than guessing,
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
  "scanned": <Step 3 scanned, summed across sources>,
  "scanned_simplify": <Step 3 Simplify scanned>,
  "scanned_speedyapply": <Step 3 speedyapply scanned, or omit if not New Grad / fetch failed>,
  "closed_excluded": <Step 3 closed_excluded>,
  "cross_source_dropped": <Step 3.25 count, or omit if 0 / not applicable>,
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
  Simplify README, the speedyapply file, or `knowledge/current_offer.md`.
- Comparison judgment is deliberately lenient: when in doubt, classify as
  "Worth a skim" rather than dropping a posting — the cost of one extra
  table row is much lower than hiding a good offer. Still never invent a
  comp number or level that isn't in either source; use `—` instead.
- Step 5's single lean table is the default output shape for every run,
  compare-offer or not — never fall back to category-grouped bullet lists
  or a wall of prose.
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
  Citizenship/visa/clearance answers are per-user, machine-local state in
  the job-apply plugin's store (never written to `knowledge/` or any other
  git-tracked file) — check there first, ask and offer to remember (with
  consent) if nothing's stored yet.
