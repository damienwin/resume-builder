---
name: job-scan
description: Scan Simplify Jobs' New-Grad-Positions or Summer-Internships GitHub board (New Grad runs also merge speedyapply/2027-SWE-College-Jobs) for postings from the last N days (default 7), filtered interactively by category, with an optional comparison against the user's current offer. Use when the user wants to browse or check for new job/internship postings, e.g. "/job-scan", "any new SWE new grad roles?", "check simplify for internships".
---

# job-scan — Scan job boards, filtered to what matters

Fetch the live community-maintained board(s), filter with the repo's parser
scripts, and print one compact table. Never a browser, never an invented
posting or comp figure.

## Step 0 — Resolve inputs

Four inputs: **board** (new-grad/internship), **categories**
(`swe` `pm` `dsa` `quant`, plus opt-in `hw`), **recency** (`--days N`,
default 7, always a plain rolling window from today), and **compare to
current offer** (yes/no).

Resolve each in this order, stopping at the first that covers it:

1. An explicit flag in `$ARGUMENTS` (`new-grad`/`internship`,
   `--swe --pm --dsa --quant --hw --startup`, `--days N`, `--compare-offer`).
2. A "Job scan defaults" section in `knowledge/rules.md` — used silently, no
   prompt. If the user's current message contradicts a stored default, follow
   the message for this run only; don't rewrite `rules.md` over an off-hand
   one-off.
3. Otherwise ask via `AskUserQuestion`. It caps at 4 options and there are 5
   categories, so this takes **two calls**: call 1 covers board, categories
   (multi-select; default all four core ones if none picked), recency, and
   offer-comparison; call 2 is a single yes/no for hardware (default no).
   Skip either call entirely if nothing in it is still unresolved.

Never offer `startup` as a checklist option — it's a pass-through that never
filters, and presenting a no-op as a selectable filter misleads the user. It
stays accepted as a flag for scripting convenience.

**Step 0.5** — if anything had to be asked, offer once *after* the scan
(Step 6) to save those answers as defaults in `rules.md`. Write only on
confirmation. Skip the offer if `rules.md` already covered everything.

## Step 1 — Fetch

| Board | Sources |
|---|---|
| New Grad | `SimplifyJobs/New-Grad-Positions` `dev` `README.md` **plus** `speedyapply/2027-SWE-College-Jobs` `main` `NEW_GRAD_USA.md` |
| Internship | `SimplifyJobs/Summer2027-Internships` `dev` `README.md` only |

speedyapply is merged into every New Grad run by default (no flag gates it);
it isn't wired in for internships (different format, not yet reviewed).

Fetch every board file for this run **in one parallel batch** — the boards
are independent, so fetching them one at a time burns latency for no benefit.
Write one line per file to a urls-file (tab-separated `<url>\t<name>`, e.g.
`simplify.md`, `speedyapply.md`) and pass it to `scripts/fetch_urls.py`:

```bash
python3 scripts/fetch_urls.py --urls-file <scratchpad>/board_urls.txt \
  --out-dir <scratchpad> --concurrency 4 > <scratchpad>/board_fetch.json
```

Never `WebFetch` for these — WebFetch summarizes through a small model and
destroys the table structure the parsers need. Save to the scratchpad, never
into the repo. Read `board_fetch.json`'s `results` to judge each file below
(`ok: true` and the file contains `<table` is success).

- **Simplify fetch fails** (`ok: false`, empty file, or no `<table` in it):
  stop and say the scan couldn't run, and why. Do NOT report "0 postings" —
  that reads as "nothing new" when the truth is "couldn't check."
- **speedyapply fetch fails** (New Grad only): not fatal. Continue
  Simplify-only and note the degraded fetch in Step 6.

## Step 2 — Parse, merge, filter

Always run the scripts — these files are thousands of rows, and eyeballing
them misses same-day postings a parser catches cleanly.

The two parsers read independent files — run them in the same background
batch rather than one after the other (New Grad only runs the second, and
only if the speedyapply fetch succeeded):

```bash
python3 scripts/parse_simplify_jobs.py <scratchpad>/simplify.md \
  --categories <active flags, e.g. swe,dsa,quant> --days <N> > <scratchpad>/s.json &
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply.md \
  --categories <same> --days <same> > <scratchpad>/p.json &
wait

python3 scripts/merge_and_filter_jobs.py --simplify <scratchpad>/s.json \
  [--speedyapply <scratchpad>/p.json] \
  --archive "<the 'Output archive' subfolder for this board from rules.md>"
```

`merge_and_filter_jobs.py` does the cross-source dedupe and the
already-applied filtering deterministically — don't re-derive either in
prose. It emits `entries` plus `scanned`, `scanned_simplify`,
`scanned_speedyapply`, `closed_excluded`, `cross_source_dropped`, and
`already_applied_dropped`. Read those numbers straight through to Steps 5–7.

Each entry has `category`, `company`, `role`, `location`, `age_raw`,
`salary` (speedyapply FAANG+/Quant rows only, else `null`), `apply_url`,
`source`, and the booleans `faang`/`adv_degree`/`no_sponsor`/`us_citizen`.
Closed postings and anything outside the day window are already gone. Known
source gaps, not bugs: speedyapply has no closed marker (`closed_excluded` is
0 for it) and no adv_degree/no_sponsor/us_citizen signal (always `false`).

An entry may carry **`applied_note`** — the company has a recent archived
resume but the role couldn't be confirmed as the same posting. Keep it in the
table and append that note to its Note column. Surfacing beats silently
hiding a possibly-different opening.

## Step 2.4 — Batch-fetch JDs once

Steps 2.5 and 3 each need certain postings' JD pages. Fetch the **union** of
both needs here, in one pass, so no JD is ever fetched twice in the same run:

- every entry with `adv_degree: true` (needed by Step 2.5), plus
- if `--compare-offer` is active, every entry with no `salary` field that
  isn't already headed for a drop on every other signal (needed by Step 3).

```bash
python3 scripts/fetch_urls.py --urls-file <scratchpad>/jd_urls.txt \
  --out-dir <scratchpad>/jds --concurrency 8 --strip-tags > <scratchpad>/jd_fetch.json
```

One line per `apply_url` in the union, no name column needed (the manifest
maps each URL back to its saved `.txt`). Steps 2.5 and 3 both read from
`jd_fetch.json` and the `.txt` files it wrote — neither step fetches on its
own, and a URL present in both needs (rare, but possible for a hatted
compare-offer posting with no stated salary) is only fetched once.

A failed fetch is not fatal to either downstream check — `ok: false` in the
manifest means "keep the posting, mark it unverified," exactly as each step
already specifies.

## Step 2.5 — Verify advanced-degree (🎓) postings

Every entry with `adv_degree: true` must have its JD checked **before it
reaches the table**, using Step 2.4's cache. The board's role title routinely
hides the requirement — a row listed as "Software Engineer Early Career,
Multiple Teams" turned out to be "Software Engineer, Infrastructure, PhD,
Early Career," with a PhD as a minimum qualification.

**Resolve the user's own degree level first**, from `knowledge/education.md`
(the degree in progress, and its expected completion date). Everything below
is a comparison against *that* — never a blanket rule. A PhD posting is a
hard mismatch for a BS candidate and a perfectly good match for a PhD
candidate, so a repo used by a grad student must keep exactly the rows a
bachelor's candidate drops. If `education.md` is missing or its degree level
is ambiguous, keep every hatted posting and note the flag as unverified
rather than assuming bachelor's.

Then read the cached JD text (`<scratchpad>/jds/<name>.txt` per the Step 2.4
manifest) for the minimum-qualifications section:

- **Required degree is above the user's** (it appears under minimum/required
  qualifications, or the title/cohort names the degree) → drop the posting as
  a hard eligibility mismatch and count it for Step 5. Do not surface it.
- **Required degree is at or below the user's** → keep it. If the posting is
  aimed at a cohort the user is in, that's a positive signal worth a Note.
- **Advanced degree only preferred**, or a lower degree is also accepted →
  keep the posting and say so in its Note column (e.g. "PhD preferred, BS
  accepted").
- **JD unreachable** (JS-rendered page, 403, empty body) → keep the posting,
  and mark the Note "advanced-degree flag unverified." Never let a failed
  fetch silently disqualify a role.

Degree level is the only thing this step tests. A graduation-year or
start-date cohort mismatch is checked later, per posting, in
`references/acting-on-results.md`.

Never surface a hatted posting unchecked and leave the disqualification for
the user to catch. Google Careers and many ATS pages are JS-rendered and
return only nav chrome through `WebFetch`; `fetch_urls.py`'s browser
User-Agent plus its `--strip-tags` pass usually recovers the qualifications
text where plain `WebFetch` fails.

## Step 3 — Optional current-offer comparison

Only if the user opted in.

- `knowledge/current_offer.md` missing → still render the table (no Tier
  column), then say the file doesn't exist and offer to create it from
  `knowledge.example/current_offer.md`. Only on confirmation. No comparison
  this run.
- Check `rules.md` for a **"Comparison bar"** override under "Job scan
  defaults" first. If set (e.g. a flat dollar figure), use it as the bar
  instead of `current_offer.md`'s real number, and keep output generic — do
  not name the current employer anywhere. Otherwise compare against
  `current_offer.md`'s company/comp/level.
- **Use the entry's own `salary` first.** speedyapply's FAANG+ and Quant
  tables carry a Salary column (roughly a fifth of its rows have one, e.g.
  `$202k/yr`), and the parser passes it straight through. Never fetch a JD or
  estimate for a posting whose figure is already in the JSON.
- **For every remaining posting** (no `salary` field, and not already headed
  for a drop on every other signal), scan its cached JD text from Step 2.4
  for a stated salary/range — Simplify's table never carries comp, and
  speedyapply's "Other" rows don't either. This step is mandatory, not
  something to skip for speed: a stated JD figure is the single biggest
  driver of tier placement, and defaulting straight to "Worth a skim" without
  checking misclassifies postings that actually beat the bar. These postings
  must already be in Step 2.4's fetch union — if one was missed there
  (compare-offer turned on after that step ran, for instance), fetch it now
  the same way rather than falling straight to levels.fyi. A stated figure in
  the JD or table is authoritative and takes precedence over any estimate —
  never relabel it as one.
- **Only after** the JD check comes up empty for a posting, fall back to a
  levels.fyi lookup (`https://www.levels.fyi/companies/<company>/salaries/software-engineer`
  or search) for that company + role, and label the figure `~$X (est.)`.
  levels.fyi is the last resort, never the first move.

Classify each posting:

| Tier | When |
|---|---|
| **Better** | Stated or estimated comp beats the bar, a clearly higher level, or a company the user's notes call preferred/aspirational |
| **Comparable** | No explicit beat but a real positive signal — `🔥 FAANG+`, an estimate near the bar, an obviously on-par tier |
| **Worth a skim** | No clear signal either way; unfamiliar or unverifiable, nothing reading as a step down |
| *(dropped)* | Only when clearly worse on **every** available signal — comp confirmed below the bar *and* level/tier also a step down |

**Be lenient.** Absent or ambiguous signal means "Worth a skim," never a
drop — one extra table row costs far less than hiding a good option. Never
invent a comp or level; label any levels.fyi figure as an estimate.

## Step 4 — Render

Always one lean markdown table with every surfaced posting — never
category-grouped sections, never prose lists.

| Company | Role | TC | Location | Posted | Tier | Note |
|---|---|---|---|---|---|---|

Drop the **Tier** column when `--compare-offer` isn't active (no bar to rank
against). Everything else is identical.

- **Company / Role / Location** — verbatim from the entry.
- **TC** — only when actually findable: the entry's `salary` field, a figure
  stated in the JD, or a levels.fyi estimate written `~$X (est.)`
  (compare-offer runs only; that lookup belongs to Step 3, never to this
  step). When the JD also names bonus, equity, or stock as part of comp
  (e.g. "+ bonus + equity," "+ RSUs"), append it to the base figure instead
  of dropping it — e.g. `$134k-$168k + bonus + equity` — since a base-only
  number understates the real offer. Never invent an equity value the JD
  doesn't state.
  step). Otherwise `—`.
- **Posted** — the `age_raw` field (`1d`, `3d`).
- **Tier** — Step 3's classification. Sort Better → Comparable → Worth a
  skim, newest first within each tier, then company name alphabetically so
  re-runs are stable.
- **Note** — a short factual clause on **what the company actually does**
  (industry/product: "payments network," "aerospace/spaceflight"), from
  general public knowledge. Never an invented detail about the specific team,
  never filler like "great opportunity." With `--compare-offer`, append why
  the move could be worth it beyond comp — brand standing, trajectory, name
  recognition — but only grounded in a real signal (`🔥 FAANG+`, known
  industry leader, notable funding). For a Worth-a-skim posting with no such
  signal the company description alone is enough; don't manufacture an angle.
  Append any `applied_note` here too, plus any known **application cap**
  (below) and any Step 2.5 degree note.

**Application caps.** Some employers limit how many roles one candidate may
have open at once (TikTok, for example, allows 2). A cap makes each
submission scarce, so note it as `cap: N applications` when it is known, and
say so before a submission is spent on a weak-fit role. Take the number only
from an explicit statement in the JD, the application portal, or a candidate
account page — or from well-established public knowledge of that employer's
policy. **Never guess a number.** If a cap plainly exists but the count
isn't verifiable, write "application cap, count unverified." Write nothing
when there's no signal either way: an absent note means unknown, not
uncapped.

## Step 5 — Report

- Scanned vs. surfaced. On New Grad, break scanned down by source rather than
  giving one merged number.
- The day window and its resolved cutoff date.
- Closed postings excluded, cross-source duplicates dropped, and
  already-applied entries dropped — each only if nonzero. Don't pad the
  report with zero-count lines.
- If the speedyapply fetch failed, say so — the scan ran, just Simplify-only.
- If comparison was requested but `current_offer.md` is missing, remind here.
- If any posting was dropped as a clear step down, give a one-line count
  ("3 postings excluded as a clear step down on comp/level/tier") — never
  silent, never restated row by row.

## Step 6 — Offer to act

Default for every run. Skip only if the user asked for scan-only output in
plain language, or Step 4 surfaced zero postings.

Read **`references/acting-on-results.md`** for this flow — it covers
confirmation, posting selection, the pre-dispatch checks, and the parallel
fork-out. Don't improvise it from memory.

## Step 7 — Log metrics

```bash
python3 scripts/log_metric.py job_scan '{
  "board": "<new-grad|internship>", "categories": ["swe", ...], "days": <N>,
  "scanned": <N>, "scanned_simplify": <N>, "scanned_speedyapply": <N or omit>,
  "closed_excluded": <N>, "cross_source_dropped": <N or omit if 0>,
  "already_applied_dropped": <N>, "surfaced": <final count>,
  "compare_offer_used": <true|false>,
  "better_count": <N or omit>, "comparable_count": <N or omit>,
  "worth_a_skim_count": <N or omit>, "dropped_worse_count": <N or omit>
}'
```

Fire-and-forget — take the counts straight from Step 2's JSON, don't block
the report on it, and don't mention it to the user.

## Hard rules

- Never invent salary, comp, level, or company facts absent from the sources
  or `current_offer.md`. Use `—`.
- Lenient comparison: in doubt, "Worth a skim" — never a silent drop.
- One lean table is the output shape for every run, compare-offer or not.
- Read-only, except optionally creating `current_offer.md` from the example
  template, and only with explicit confirmation.
- `--startup` is always a pass-through — never build a company denylist or
  size heuristic for it.
- Report first; acting always requires the user to pick postings and an
  action. Never tailor or apply to something they didn't select.
