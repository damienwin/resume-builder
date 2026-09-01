---
name: tailor-resume
description: Generate a tailored one-page LaTeX resume for a job description from the knowledge/ base in this repo. Use when the user provides a job posting URL, a JD file path, or pasted JD text and wants a resume tailored to it.
---

# tailor-resume — Tailor a one-page resume to a job description

All personal facts come from `knowledge/`; the skill itself is
person-agnostic. **`knowledge/rules.md`, if present, overrides every generic
default below.**

The bar: survive both automated screening (ATS keyword matchers, AI
screeners) and a 6-second recruiter skim at a top-tier tech company. Every
step serves one of those two audiences.

## Step 1 — Ingest the JD

**Set `$SLUG` now, before starting the timer** — the same per-job slug
Step 5 uses for `build/$SLUG.*` (lowercase, hyphenated
`<company>-<role-first-two-words>`, e.g. `whatnot-resume`; refine it in Step
5 once the exact company/role are locked in, but keep it stable for the
rest of this run rather than re-deriving it). Pass it as every
`run_timer.py` call's `--scope` for the rest of this run:

```bash
SLUG=<company-role-slug>
python3 scripts/run_timer.py start tailor-resume --scope "$SLUG"
```

**Why this matters even for a single tailoring run:** when this posting was
dispatched as one of a parallel fork batch (see
`job-scan/references/acting-on-results.md` Step 4), concurrent forks of this
same skill were observed sharing one `CLAUDE_CODE_SESSION_ID` — see
`run_timer.py`'s docstring. Without a distinct `--scope` per fork, one
fork's `start` clobbers another's, and whichever fork's `finish` runs first
deletes the shared file, so every other fork's `resume_tailor` metric ships
with no `duration_s`/`steps` at all (observed live 2026-09-01: 6 parallel
forks, only the first `finish` got real numbers).

URL → `WebFetch` first. If the page is JS-rendered and comes back as nav
chrome or an empty body (common on Workday, Greenhouse behind a JS shell,
and Oracle Cloud/Fusion `*.oraclecloud.com/hcmUI/CandidateExperience/...`
career pages), try, in order, before asking the user for pasted text:
1. **Workday**: the `wday/cxs/<tenant>/<site>/job/...` JSON API.
2. A `.md`/markdown alternate link some ATS platforms expose in the page
   `<head>`.
3. **Oracle Cloud / Fusion HCM CandidateExperience** pages (URL contains
   `hcmUI/CandidateExperience`): fetch the JSON directly —
   `https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?finder=ById;Id="<jobId>",siteNumber="<siteNumber>"`
   (`jobId` and `siteNumber` both appear in the page URL, e.g.
   `.../sites/CX_1/job/26013253/` → `siteNumber=CX_1`, `jobId=26013253`).
   Verified live 2026-09-01 against American Express's careers site — plain
   `WebFetch`/`--strip-tags` returned only a truncated `og:description`, the
   REST endpoint returned the full requisition text.
4. `python3 scripts/fetch_urls.py --urls-file <file> --out-dir <dir>
   --strip-tags` with a browser User-Agent, which recovers many
   JS-rendered pages plain `WebFetch` can't.

File path → read it. Pasted text → save to `jd.txt` first.

If every fallback fails, ask the user to paste the JD or put it in
`jd.txt`. **Never proceed on a guessed JD.**

Extract and keep for Step 6's coverage check:
- **Required** skills/languages/frameworks, as exact strings — ATS matchers
  are literal: "JavaScript" ≠ "JS", "CI/CD" ≠ "continuous integration".
- Preferred / nice-to-have skills.
- Domain (infra, ML, frontend, security, quant, …) and seniority.
- Recurring vocabulary to mirror where truthful.

## Step 2 — Load the knowledge base

Read every file under `knowledge/` **in one parallel batch** (single message,
one Read per file — never sequentially): `profile.md`, `education.md`,
`skills.md`, `rules.md` if present, and all of `experience/*.md`,
`research/*.md`, `projects/*.md`.

Frontmatter is structured metadata; the body is ground truth. **Seed bullets
are pre-polished — prefer adapting them over inventing new wording.**

```bash
python3 scripts/run_timer.py mark tailor-resume read_knowledge --scope "$SLUG"
```

## Step 3 — Score and select

Rank every experience, research entry, and project against the JD using
`tech`/`domain` frontmatter plus body content. Select what fits one page:

- **3 experiences** (from `experience/`), most relevant and most recent
  first. **Do not add a 4th** — the production-experience signal saturates
  at 3, so a fourth costs most of a project's worth of page space for no
  gain.
- **A `research/` entry belongs in Projects, not Experience.** Rendered as a
  fourth experience it lands in a category that's already saturated;
  rendered as a project it still counts for something.
- **3 projects** most aligned with the JD — fill all three slots. Drop to 2
  only when the page truly cannot hold a third; never ship 1. Prefer ones
  with a `repo:` —
  AI screeners deduct per unlinked project. `origin: work` and
  `origin: research` projects are exempt (no public repo expected); a plain
  `origin: self` project with no `repo:` loses to a linked alternative that
  fits comparably well. Never use generic names ("Calculator", "Todo App",
  "Weather App") — screeners penalize them. Use the knowledge file's full
  descriptive name.
- **~10–14 languages and ~10–14 frameworks/tools** the JD signals, spelled
  the way the JD spells them. Never dump the master list.
- **4–6 coursework items** from `education.md` aligned to the JD.

```bash
python3 scripts/run_timer.py mark tailor-resume select --scope "$SLUG"
```

## Step 4 — Tailor bullets

3–4 bullets per selected role.

- **XYZ formula:** "Accomplished X, as measured by Y, by doing Z" — impact
  first, metric attached, method last. Not every bullet needs all three, but
  every bullet needs X and every role needs at least one strong Y.
- Open with a strong action verb (Built, Vectorized, Engineered, Designed,
  Shipped, Tuned, Deployed). Vary them; never reuse an opening verb within a
  role.
- Include a metric whenever the knowledge file supports one. **Never invent
  a metric.**
- Mirror JD vocabulary, in the JD's exact spelling, where truthful.
- Prefer vocabulary AI screeners read as complexity signals — real-time,
  authentication, databases, microservices, named algorithms/data
  structures, ML/AI specifics, concrete adoption numbers — but **never
  invent a signal the knowledge file doesn't back**.
- Keep bullets to roughly ≤165 characters at 11pt (slightly more at 10pt). A
  bullet wrapping past two lines gets cut or split.
- No first person, no filler adjectives ("various", "cutting-edge",
  "state-of-the-art"). Let the metric carry the weight.

**Prefer 3 tight bullets over 4 dense ones.** Four two-line bullets per role
read worse on a 6-second skim, and ATS category scores track
entities/keywords present, not prose length — cutting filler costs no
coverage. Dropping the weakest of four: cut a pure process/activity metric
(review counts, commit counts) first, since those read as administrative
rather than outcome-driven. Trim hedge words that carry neither keyword nor
metric ("roughly", "about", restated trailing clauses) before shortening a
metric itself.

**For self-projects, lean into systems-design and scale vocabulary the
knowledge file actually supports.** An under-framed project reads to AI
screeners as a "simple utility tool" and draws a deduction even when the
underlying work is substantive. Name the orchestration, the multi-stage
pipeline, the actual algorithm, the scale, the automated verification layer.
Prefer "architected a multi-agent orchestration system chaining N stages to
process X at scale" over "built a pipeline that scans X"; prefer
"implementing forward/backward propagation, AdamW optimization" over "used
PyTorch/NumPy". *A/B-tested on a real JD: same two projects, same facts,
wording only — this removed a recurring self-projects deduction and lifted
that category from 22/30 to 24/30 across repeated scoring runs.* Still never
invent an unsupported signal.

**Never hallucinate.** If the JD wants something `knowledge/` doesn't
support, leave it out and report it as a gap in Step 8.

```bash
python3 scripts/run_timer.py mark tailor-resume write_bullets --scope "$SLUG"
```

## Step 5 — Render

**`$SLUG` was already set in Step 1** for the run timer; this is where it
earns its keep as the working-file name too. Every working file in this run
is named `build/<slug>.*` — never the shared
`build/resume.tex`/`build/resume.pdf`. If the provisional value from Step 1
doesn't match the archive tag from `rules.md`'s "Output archive" now that
company/role are locked in, reassign it here (lowercased and hyphenated:
`Acme Resume` -> `acme-resume`, `Globex Resume` -> `globex-resume`) — just
keep using the same variable so the working file and the archived PDF stay
traceable to each other:

```bash
SLUG=<company-role slug>      # e.g. whatnot-resume
```

This is not a cosmetic naming preference. Two tailor runs in parallel
(job-scan's Step 6 fans out one fork per posting, by design) both write the
same fixed path otherwise, and the second one silently overwrites the
first's `.tex` mid-compile. A fixed filename makes concurrent
tailoring unsafe; a per-job one makes it free.

Compile in `build/`, not in the archive folder. The archive doubles as
job-scan's already-applied ledger (`merge_and_filter_jobs.py` matches
company names against the filenames there), so a failed or 2-page compile
landing in it would make the next scan silently drop that company — and
tectonic would leave `.log`/`.aux` siblings beside the PDF. The archive
receives exactly one thing: a PDF that already passed Step 6.

Read `templates/jakes_resume.tex` and fill every `<<PLACEHOLDER>>` into
`build/$SLUG.tex`:

- Header (name, email, github, linkedin, **website**) from `profile.md` —
  the `\hypersetup` PDF metadata block also takes the name. `<<WEBSITE>>` is
  a required placeholder, not an optional flourish: fill it whenever
  `profile.md` has a `website:`, with no `https://` in the display text.
  Screeners award a bonus point for a portfolio site and there is no reason
  to leave it on the table. Only if `website:` is genuinely empty, delete
  that header line and the `$|$` that precedes it.
- Education from `education.md`; only the coursework line changes per JD.
- Repeat `\resumeSubheading` / `\resumeProjectHeading` per selected item.
- **Each project's `\resumeProjectHeading` right cell renders links, not a
  date:** `\href{https://<repo>}{<repo>}` from frontmatter (bare
  `github.com/...` display text), plus `$|$ \href{<demo>}{<demo domain>}`
  when `demo:` is set. Links come only from frontmatter — never construct or
  guess a URL. For an `origin: work`/`origin: research` project with no
  `repo:`, fall back to the plain date. `$|$` is allowed in headings (as in
  the header line); it stays banned in bullets.
- **Keep the section titles exactly as templated** (Education / Experience /
  Projects / Technical Skills) — ATS parsers segment on those headings.
  Never rename them.

### ATS-safe LaTeX rules (hard requirements, from real extraction failures)

- **No math-mode symbols in bullet text.** Never `$\to$`, `$\times$`,
  `$\sim$` — they extract as Unicode glyphs glued to adjacent digits
  (`63.4s→<100ms`), which keyword matchers mis-tokenize. Write plain ASCII:
  "63.4s to under 100ms (about 634x faster)". Subscripts/superscripts like
  `$p_{50}$`/`$R^2$` extract cleanly as `p50`/`R2` and are fine.
- **Avoid `ffi`/`ffl` words when a plain synonym exists** ("burst load", not
  "burst traffic"). Tectonic's Latin Modern OTF fonts drop the ToUnicode
  mapping for those ligature triples, so "traffic" extracts garbled.
- **A project heading row must not overflow its column.** A long `repo:`
  paired with a long title/tech-stack list overflows the fixed-width
  `tabular*` row — LaTeX reports "Overfull \hbox" and the cells visually
  collide with no space (`NumPygithub.com/...`), corrupting extraction. The
  link text must stay verbatim, so **shorten the left side**: drop a
  parenthetical from the title first ("Deep Neural Network (from scratch)" →
  "Deep Neural Network"), then the tech-stack list. Never leave an
  overfull-hbox warning unresolved — treat it as a failed check.
  **Trim minimally — one item at a time, recompiling until the warning just
  clears.** The tech-stack list is scored evidence, not decoration: AI
  screeners cite it when judging project depth, so every item dropped past
  what the layout actually requires is evidence given away for free. Aim for
  the longest left side that compiles clean, not the shortest one that
  obviously will.

## Step 6 — Compile and verify (one pass)

```bash
tectonic build/$SLUG.tex 2>&1 | tee build/$SLUG.tectonic.log | tail -20
python3 scripts/verify_resume_pdf.py build/$SLUG.tex build/$SLUG.pdf \
  --log build/$SLUG.tectonic.log
```

(Tectonic pulls packages on demand. Missing tools: `brew install tectonic`,
`brew install poppler`.)

**`verify_resume_pdf.py` exists because page count alone is not proof of
completeness.** Observed live 2026-09-01 (Idler tailoring run): an
over-full document pushed an entire project past the bottom of the page
*without* triggering a page break — `pdfinfo` reported exactly 1 page,
every check below would have passed by eyeballing the extraction, and a
whole project was simply absent from the rendered PDF. It was caught only
by hand-diffing the `.tex` against `pdftotext` output. The script automates
that diff: it parses every `\resumeSubheading`/`\resumeProjectHeading`
title and every `\resumeItem` bullet out of the `.tex` and confirms each
one actually appears in the extracted PDF text — not just "page count is
1." Treat a `completeness` failure exactly like a compile error: something
that should be on the page isn't, full stop.

It also automates checks 1, 6, and 7 below (page count, fill measurement,
overfull-hbox, placeholders, header) plus the new completeness check above.
Exit code nonzero means at least one check failed — read its per-check
output (or `--json` for a machine-readable report), fix the `.tex`,
recompile, and rerun it. It does **not** cover checks 2, 3, 4, or 5 —
clean extraction, reading order, keyword coverage, and link visibility all
need a truthful read of the actual JD and the extracted text, which is
exactly the judgment a script can't make. `mdls -name kMDItemNumberOfPages
build/$SLUG.pdf` still works as a spot-check on macOS if you want a second
page-count source.

Check all eight below against the extracted text and the script's report;
fix the `.tex`, recompile, and re-verify on any failure.

1. **Exactly one page, and visually full.** (Automated above — `page_count`
   and `fill`.) Measure the fill by hand only if you need to debug a
   failure — don't eyeball it:

   ```bash
   pdftotext -bbox build/$SLUG.pdf - | grep -o 'yMax="[0-9.]*"' \
     | sed 's/[^0-9.]//g' | sort -n | tail -1
   ```

   That prints the last text baseline, out of 792pt. A full page lands at
   **740-755**. Treat anything **below 720 as a failed check**, not a
   cosmetic nit — an under-filled page is the most common defect in this
   repo's output, and it always means content that would have scored was
   left out.
   - *Over* (2 pages) → shrink before cutting: tighten `\resumeItemListStart`
     to `\begin{itemize}[itemsep=1pt, topsep=2pt, parsep=0pt]`, then drop to
     `\documentclass[letterpaper,10pt]{article}`. Still over → drop the
     weakest bullet from the longest role → shorten two-line bullets → drop
     a project. Never drop below 3 experiences or 2 projects.
   - *Under 720* → add content back and recompile, in this order: a third
     project → a bullet on the thinnest project → a bullet on the thinnest
     role. Roughly 12pt per bullet line, 37pt per project block (heading +
     2 bullets). Re-measure after each addition; stop once past 740.
   - Adding a **4th experience is never the way to fill space** — see Step 3.
2. **Clean extraction** — grep the `-layout` output for every metric;
   nothing glued or garbled. Reword rather than fight the font.
3. **Reading order** — the no-`-layout` output reads header → education →
   experience → projects → skills.
4. **Keyword coverage** — every *required* JD skill the knowledge base
   truthfully supports appears verbatim. Work any missing supported one into
   skills or a bullet and recompile. Unsupported skills are Step 8 gaps,
   never additions.
5. **Link visibility** — for every project with `repo:`/`demo:`, grep the
   `-layout` output for the exact displayed URL. Each must appear verbatim
   and unbroken — not hyphen-split across a wrap, not glued to neighboring
   text. If it wraps, is missing, or collides, shorten the title/tech-stack
   (never the URL) per Step 5 and recompile.
6. **No overfull-hbox warnings** (automated above — `overfull`) on any
   `\resumeSubheading` or `\resumeProjectHeading` line. This is the Step 5
   overflow bug, not cosmetic noise.
7. **Header complete, no unfilled placeholders.** (Automated above —
   `placeholders` and `header`.)

   ```bash
   grep -c '<<' build/$SLUG.tex                         # must be 0
   pdftotext build/$SLUG.pdf - | head -4 | grep -c .    # header lines present
   ```

   Any surviving `<<PLACEHOLDER>>` is a failed check. Confirm the header
   carries email, github, linkedin, and the website from `profile.md` —
   the website is the one that gets dropped most often.
8. **Completeness — every heading and bullet in the `.tex` actually renders.**
   (Automated above — `completeness`.) This is the check that exists
   because of the 2026-09-01 Idler incident: a passing page count and a
   passing fill measurement are both compatible with an entire project
   having silently fallen off the page. Never treat a `completeness`
   failure as a false positive without first confirming, by eye, that the
   named heading/bullet text is genuinely present in `pdftotext`'s output —
   it isn't one.

Compilation errors → read the output, fix the `.tex`, recompile.

```bash
python3 scripts/run_timer.py mark tailor-resume compile --scope "$SLUG"
```

## Step 7 — Save

`build/$SLUG.pdf` and `build/$SLUG.tex` stay as working files. If
`rules.md` defines an archive location and naming convention, copy the
verified PDF there — **only after every Step 6 check passed.** If the
destination is ambiguous, ask once.

Copy, never compile in place, and never archive an unverified PDF: the
archive folder is what job-scan reads to decide a company was already
applied to, so a bad file there costs a real posting on a later scan.

## Step 8 — Report

- Path to the final PDF.
- Experiences and projects included, one-line reason each; and what was
  excluded, why.
- **Keyword coverage** — which required/preferred keywords landed, and
  anything in the JD that could **not** be backed by `knowledge/`. Those
  gaps are worth adding to `knowledge/` before the next run.
- **Outside-the-resume checklist** (AI-screener signals no PDF edit fixes):
  each linked repo should be public with a real README, description, and
  topic tags — screeners fetch the GitHub profile directly. Flag any
  selected project with an empty `repo:`/`demo:`. Note that real
  contributions to others' popular open-source projects move an
  open-source score far more than personal repos, which are capped low.
  Point to `/ats-score` for an actual — noisy, diagnostic-only — score.

## Step 9 — Log metrics

Close out the run timer first — its output has `duration_s` and a `steps`
breakdown (`read_knowledge`, `select`, `write_bullets`, `compile`):

```bash
python3 scripts/run_timer.py finish tailor-resume --scope "$SLUG"
```

```bash
python3 scripts/log_metric.py resume_tailor '{
  "company": "<company>", "role": "<role title>",
  "jd_source": "<url, file path, or \"pasted\">",
  "output_path": "<archived PDF path, else build/$SLUG.pdf>",
  "experiences_included": ["<name>", ...],
  "projects_included": ["<name>", ...],
  "required_keywords_total": <N>, "required_keywords_covered": <N>,
  "duration_s": <from run_timer finish>, "steps": <from run_timer finish>
}'
```

Fire-and-forget — don't block or re-render Step 8 on it, and don't mention
it to the user. `job-scan` reads these records to tell an already-applied
posting from a genuinely new one, so the `company` and `role` fields must be
accurate.

## Hard rules

- One page, always — shrink font/spacing first, cut content second — and
  visually full, no large trailing whitespace.
- Never invent metrics, employers, dates, or coursework.
- XYZ bullets: action verb → what → measurable outcome.
- Skills and coursework filtered, never dumped. JD spelling mirrored exactly
  where truthful.
- ATS safety is hard-required: standard section headings, no math-mode
  arrows/times/tilde in bullets, ligature-safe wording, verified extraction
  after every compile.
- Every selected project shows a visible repo/demo link unless its `origin:`
  is `work` or `research` — link deductions are the single biggest fixable
  loss against AI screeners.
- `knowledge/rules.md` overrides these defaults.
