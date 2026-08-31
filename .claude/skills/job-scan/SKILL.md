---
name: job-scan
description: Scan Simplify Jobs' New-Grad-Positions or Summer-Internships GitHub board (New Grad runs also merge speedyapply/2027-SWE-College-Jobs and speedyapply/2027-AI-College-Jobs) for postings from the last N days (default 7), filtered interactively by category, with an optional comparison against the user's current offer. Use when the user wants to browse or check for new job/internship postings, e.g. "/job-scan", "any new SWE new grad roles?", "check simplify for internships".
---

# job-scan — Scan job boards, filtered to what matters

Fetch the live community-maintained board(s), filter with the repo's parser
scripts, and print one compact table. Never a browser, never an invented
posting or comp figure.

## Step 0 — Resolve inputs

Four inputs: **board** (new-grad/internship), **categories**
(`swe` `pm` `dsa` `quant`, plus opt-in `hw`), **recency**, and **compare to
current offer** (yes/no).

Recency is one of two modes, mutually exclusive:

- **`--days N`** — a plain rolling window from today (default 7).
- **`--since-last-scan`** — everything newer than the last successful scan
  of this same board, read from `knowledge/job_scan_state.json` (see
  Step 2.4). This is the tighter, exact option once at least one prior scan
  exists — no risk of re-showing something from 20 hours ago just because it
  rounds up to "1 day." First-ever run for a board (no state file, or the
  board's not in it yet) silently falls back to the `--days` default.

Recency is always presented as one choice between these two modes, not a
second independent input — asking "how many days, AND since last scan y/n"
would be two questions for one setting.

Resolve each in this order, stopping at the first that covers it:

1. An explicit flag in `$ARGUMENTS` (`new-grad`/`internship`,
   `--swe --pm --dsa --quant --hw --startup`, `--days N` or
   `--since-last-scan`, `--compare-offer`).
2. A "Job scan defaults" section in `knowledge/rules.md` — used silently, no
   prompt. If the user's current message contradicts a stored default, follow
   the message for this run only; don't rewrite `rules.md` over an off-hand
   one-off.
3. Otherwise ask via `AskUserQuestion`. It caps at 4 options and there are 5
   categories, so this takes **two calls**: call 1 covers board, categories
   (multi-select; default all four core ones if none picked), recency, and
   offer-comparison; call 2 is a single yes/no for hardware (default no).
   Skip either call entirely if nothing in it is still unresolved. For
   recency, offer it as one single-select question with options like "Since
   last scan (recommended once one exists)", "0 days (today only)", "1 day",
   "3 days" — not a free-text day count.

Never offer `startup` as a checklist option — it's a pass-through that never
filters, and presenting a no-op as a selectable filter misleads the user. It
stays accepted as a flag for scripting convenience.

**Step 0.5** — if anything had to be asked, offer once *after* the scan
(Step 6) to save those answers as defaults in `rules.md`. Write only on
confirmation. Skip the offer if `rules.md` already covered everything.

## Step 1 — Fetch

Start the run timer before the first fetch (fire-and-forget, same as the
metrics log — see Step 7):

```bash
python3 scripts/run_timer.py start job-scan
```

| Board | Sources |
|---|---|
| New Grad | `SimplifyJobs/New-Grad-Positions` `dev` `README.md` **plus** `speedyapply/2027-SWE-College-Jobs` `main` `NEW_GRAD_USA.md` **plus** `speedyapply/2027-AI-College-Jobs` `main` `NEW_GRAD_USA.md` |
| Internship | `SimplifyJobs/Summer2027-Internships` `dev` `README.md` only |

Both speedyapply boards are merged into every New Grad run by default (no
flag gates either); neither is wired in for internships (different format,
not yet reviewed). The two speedyapply boards are otherwise-independent
sources — same file layout, disjoint postings (the AI board runs its own
separate FAANG/Quant/Other tables, e.g. carrying GTS, Man Group, and Flow
Traders that the SWE board's `quant` table misses) — so they're fetched,
parsed, and tracked in `job_scan_state.json` as two distinct sources, not
merged into one `speedyapply` bucket. The AI board's parser call uses
`--source-name speedyapply_ai --default-category dsa`: its unmatched rows
skew data-science rather than general SWE, and a distinct `source` name is
required for cross-source dedupe to treat it as its own board (see
`merge_and_filter_jobs.py`'s docstring). Its analyst/consultant/GTM-family
rows (e.g. "Data Analyst", "Management Consultant - AI Strategy Evaluation")
classify to a category no run ever requests, so they're filtered out
automatically rather than needing a company denylist — a real quant-analyst
title like "Quantitative Risk Management - Summer Analyst" is unaffected
since the quant keyword match happens first.

Fetch every board file for this run **in one parallel batch** — the boards
are independent, so fetching them one at a time burns latency for no benefit.
Write one line per file to a urls-file (tab-separated `<url>\t<name>`, e.g.
`simplify.md`, `speedyapply.md`, `speedyapply_ai.md`) and pass it to
`scripts/fetch_urls.py`:

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
- **A speedyapply fetch fails** (either board, New Grad only): not fatal.
  Continue with whichever sources succeeded and note the degraded fetch in
  Step 6, naming which board (SWE or AI) failed.

```bash
python3 scripts/run_timer.py mark job-scan fetch_boards
```

## Step 2 — Parse, merge, filter

Always run the scripts — these files are thousands of rows, and eyeballing
them misses same-day postings a parser catches cleanly.

Run all parsers in parallel too, same reasoning as Step 1 (New Grad only
runs the speedyapply parsers, and only for whichever of the two fetches
succeeded).

**`--days N` mode** (the default):

```bash
python3 scripts/parse_simplify_jobs.py <scratchpad>/simplify.md \
  --categories <active flags, e.g. swe,dsa,quant> --days <N> > <scratchpad>/s.json &
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply.md \
  --categories <same> --days <same> \
  --source-name speedyapply --default-category swe > <scratchpad>/p.json &
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply_ai.md \
  --categories <same> --days <same> \
  --source-name speedyapply_ai --default-category dsa > <scratchpad>/pai.json &
wait
```

**`--since-last-scan` mode**: read `knowledge/job_scan_state.json`'s
`<board>.simplify`, `<board>.speedyapply`, and `<board>.speedyapply_ai`
objects (missing file or missing board key → treat as `{}`) and pass each as
`--since-json`, still with a `--days` fallback bound (14) for any
category/section with no marker yet (new board, or a category that surfaced
nothing last run):

```bash
SIMPLIFY_SINCE=$(python3 -c "import json;print(json.dumps(json.load(open('knowledge/job_scan_state.json')).get('<board>',{}).get('simplify',{})))" 2>/dev/null || echo '{}')
SPEEDY_SINCE=$(python3 -c "import json;print(json.dumps(json.load(open('knowledge/job_scan_state.json')).get('<board>',{}).get('speedyapply',{})))" 2>/dev/null || echo '{}')
SPEEDY_AI_SINCE=$(python3 -c "import json;print(json.dumps(json.load(open('knowledge/job_scan_state.json')).get('<board>',{}).get('speedyapply_ai',{})))" 2>/dev/null || echo '{}')

python3 scripts/parse_simplify_jobs.py <scratchpad>/simplify.md \
  --categories <active flags> --days 14 --since-json "$SIMPLIFY_SINCE" > <scratchpad>/s.json &
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply.md \
  --categories <same> --days 14 --since-json "$SPEEDY_SINCE" \
  --source-name speedyapply --default-category swe > <scratchpad>/p.json &
python3 scripts/parse_speedyapply_jobs.py <scratchpad>/speedyapply_ai.md \
  --categories <same> --days 14 --since-json "$SPEEDY_AI_SINCE" \
  --source-name speedyapply_ai --default-category dsa > <scratchpad>/pai.json &
wait
```

`--since-json` for simplify is keyed by **category** (its sections are
literally per-category headings). For each speedyapply-format board it's
keyed by **section** (`faang`/`quant`/`other`) — that source's three tables
each mix categories and are independently newest-first, so the stop boundary
has to be the table, not the category; see the parser's own
docstring/comments before changing this. The SWE and AI boards' markers are
tracked as two separate state keys (`speedyapply` / `speedyapply_ai`) since
they're two distinct, independent tables — never merge them into one.

Then, either mode:

```bash
python3 scripts/merge_and_filter_jobs.py --simplify <scratchpad>/s.json \
  [--speedyapply <scratchpad>/p.json] [--speedyapply-ai <scratchpad>/pai.json] \
  --archive "<the 'Output archive' subfolder for this board from rules.md>"
```

`merge_and_filter_jobs.py` does the cross-source dedupe and the
already-applied filtering deterministically — don't re-derive either in
prose. It emits `entries` plus `scanned`, `scanned_simplify`,
`scanned_speedyapply`, `scanned_speedyapply_ai`, `closed_excluded`,
`cross_source_dropped`, and `already_applied_dropped`. Read those numbers
straight through to Steps 5–7.

Each entry has `category`, `company`, `role`, `location`, `age_raw`,
`salary` (speedyapply-format FAANG+/Quant rows only, else `null`),
`apply_url`, `source`, and the booleans
`faang`/`adv_degree`/`no_sponsor`/`us_citizen`. Closed postings and anything
outside the day window are already gone. Known source gaps, not bugs: both
speedyapply-format boards have no closed marker (`closed_excluded` is 0 for
them) and no no_sponsor/us_citizen signal (always `false`).

**`adv_degree` on a speedyapply-format entry is a much weaker signal than
Simplify's.** Simplify's flag comes from the board's hand-curated 🎓 marker;
speedyapply boards carry no such marker, so `parse_speedyapply_jobs.py` can
only regex the role *title* for an explicit "PhD"/"Doctorate"/"Postdoc"
mention (e.g. TikTok's "... - 2027 Start - PhD" suffix). A JD that requires
a PhD without saying so in the title — verified live: Iambic Therapeutics,
Applied Intuition, Lila Sciences, Flow Traders, and Axon all required a PhD
with zero title signal — still comes through as `adv_degree: false` and
Step 2.6 never checks its JD, since that step only runs on `adv_degree:
true` rows. **Do not treat an unhatted speedyapply-format row as
degree-verified.** The real backstop for this source is
`acting-on-results.md`'s per-posting eligibility check at dispatch time,
which reads the actual JD before tailoring — that check is mandatory and is
what actually protects the user here, not this flag.

An entry may carry **`applied_note`** — the company has a recent archived
resume but the role couldn't be confirmed as the same posting. Keep it in the
table and append that note to its Note column. Surfacing beats silently
hiding a possibly-different opening.

```bash
python3 scripts/run_timer.py mark job-scan parse_merge
```

## Step 2.4 — Persist scan state

After `s.json` (and `p.json`/`pai.json`, if run) exist, update the marker
file so a future `--since-last-scan` run knows where this one left off —
regardless of which recency mode *this* run used:

```bash
python3 scripts/update_scan_state.py --board <new-grad|internship> \
  --simplify <scratchpad>/s.json [--speedyapply <scratchpad>/p.json] \
  [--speedyapply-ai <scratchpad>/pai.json] \
  --state-file knowledge/job_scan_state.json
```

Do this unconditionally on every successful parse, right after Step 2 and
before the JD fetch — it's cheap, and skipping it on a `--days`-mode run
would leave the next `--since-last-scan` run pointed at stale markers.

## Step 2.5 — Batch-fetch JDs once

Steps 2.6, 3, and 4 each need certain postings' JD pages. Fetch the **union**
of all three needs here, in one pass, so no JD is ever fetched twice in the
same run:

- every entry with `adv_degree: true` (needed by Step 2.6), plus
- if `--compare-offer` is active, every entry that isn't already headed for a
  drop on every other signal — **including one with a `salary` field already
  populated.** A known base figure only means Step 3 skips re-deriving *that*
  number from the JD; Step 4 still needs the JD text to check for stated
  bonus/equity/RSU language to append to it. Skipping the fetch here is what
  silently drops real stated equity (a JD stating "your package will include
  sign-on payments and RSUs" is worthless to Step 4 if the JD was never
  fetched because the base salary was already known).

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

```bash
python3 scripts/run_timer.py mark job-scan fetch_jds
```

## Step 2.6 — Verify advanced-degree (🎓) postings

Every entry with `adv_degree: true` must have its JD checked **before it
reaches the table**, using Step 2.5's cache. The board's role title routinely
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

Then read the cached JD text (`<scratchpad>/jds/<name>.txt` per the Step 2.5
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
  `$202k/yr`), and the parser passes it straight through. Never re-derive or
  estimate a base figure for a posting whose salary is already in the JSON —
  but its JD is still fetched for stated bonus/equity/RSU language to append
  (Step 2.5).
- **For every remaining posting** (no `salary` field, and not already headed
  for a drop on every other signal), scan its cached JD text from Step 2.5
  for a stated salary/range — Simplify's table never carries comp, and
  speedyapply's "Other" rows don't either. This step is mandatory, not
  something to skip for speed: a stated JD figure is the single biggest
  driver of tier placement, and defaulting straight to "Worth a skim" without
  checking misclassifies postings that actually beat the bar. These postings
  must already be in Step 2.5's fetch union — if one was missed there
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

```bash
python3 scripts/run_timer.py mark job-scan compare_offer
```

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
  doesn't state. Otherwise `—`.
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
  (below) and any Step 2.6 degree note.

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
- The recency mode used: the day window and its resolved cutoff date for
  `--days`, or "since last scan" for `--since-last-scan` (and if any
  category/section had no marker and fell back to the 14-day bound, say so).
- Closed postings excluded, cross-source duplicates dropped, and
  already-applied entries dropped — each only if nonzero. Don't pad the
  report with zero-count lines.
- If either speedyapply fetch failed, say so and name which board (SWE or
  AI) — the scan ran, just without that source.
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

First close out the run timer (started in Step 1) — its JSON has
`duration_s` and a `steps` breakdown (`fetch_boards`, `parse_merge`,
`fetch_jds`, `compare_offer` — whichever marks actually ran this time):

```bash
python3 scripts/run_timer.py finish job-scan
```

Fold its output straight into the `steps`/`duration_s` fields below — don't
recompute timing by hand. `fetch_speedup` is `serial_estimate_s / elapsed_s`
from Step 1's and Step 2.5's `fetch_urls.py` manifests (both now report
`elapsed_s`, `concurrency`, and `serial_estimate_s`); omit it if a fetch
manifest is unavailable rather than estimating it.

```bash
python3 scripts/log_metric.py job_scan '{
  "board": "<new-grad|internship>", "categories": ["swe", ...],
  "recency": "<since-last-scan|days>", "days": <N, or the 14 fallback bound>,
  "scanned": <N>, "scanned_simplify": <N>, "scanned_speedyapply": <N or omit>,
  "scanned_speedyapply_ai": <N or omit>,
  "closed_excluded": <N>, "cross_source_dropped": <N or omit if 0>,
  "already_applied_dropped": <N>, "surfaced": <final count>,
  "compare_offer_used": <true|false>,
  "better_count": <N or omit>, "comparable_count": <N or omit>,
  "worth_a_skim_count": <N or omit>, "dropped_worse_count": <N or omit>,
  "duration_s": <from run_timer finish>, "steps": <from run_timer finish>,
  "fetch_speedup": <N or omit>
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
