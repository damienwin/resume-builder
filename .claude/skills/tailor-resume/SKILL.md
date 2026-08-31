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

Start the run timer (fire-and-forget, folded into Step 9's log):

```bash
python3 scripts/run_timer.py start tailor-resume
```

URL → `WebFetch`. File path → read it. Pasted text → save to `jd.txt` first.

If the fetch fails (JS-rendered page, login wall, empty response), ask the
user to paste the JD or put it in `jd.txt`. **Never proceed on a guessed JD.**

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
python3 scripts/run_timer.py mark tailor-resume read_knowledge
```

## Step 3 — Score and select

Rank every experience, research entry, and project against the JD using
`tech`/`domain` frontmatter plus body content. Select what fits one page:

- **3–4 experiences** (`experience/` + `research/` combined), most relevant
  and most recent first.
- **1–2 projects** most aligned with the JD. Prefer ones with a `repo:` —
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
python3 scripts/run_timer.py mark tailor-resume select
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
python3 scripts/run_timer.py mark tailor-resume write_bullets
```

## Step 5 — Render

Read `templates/jakes_resume.tex` and fill every `<<PLACEHOLDER>>` into
`build/resume.tex`:

- Header (name, email, github, linkedin) from `profile.md` — the
  `\hypersetup` PDF metadata block also takes the name. If `website:` is
  filled, append it to the header as a fourth `$|$`-separated link
  (`\href{https://<<WEBSITE>>}{<<WEBSITE>>}`, no `https://` in the display
  text).
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
tectonic build/resume.tex 2>&1 | tee /tmp/tectonic.log | tail -20
pdfinfo build/resume.pdf | grep Pages    # must be exactly 1
pdftotext -layout build/resume.pdf -     # extraction check
pdftotext build/resume.pdf -             # reading-order check
grep -i "overfull" /tmp/tectonic.log     # heading-row overflow check
```

(Tectonic pulls packages on demand. Missing tools: `brew install tectonic`,
`brew install poppler`. On macOS `mdls -name kMDItemNumberOfPages
build/resume.pdf` also gives page count.)

Check all six against the extracted text; fix the `.tex`, recompile, and
re-verify on any failure.

1. **Exactly one page.**
   - *Over* → shrink before cutting: drop to
     `\documentclass[letterpaper,10pt]{article}`, tighten
     `\resumeItemListStart` to
     `\begin{itemize}[itemsep=1pt, topsep=2pt, parsep=0pt]`. Still over →
     drop the weakest bullet from the longest role → shorten two-line
     bullets → drop a project before dropping an experience.
   - *Under*, with significant trailing whitespace → add back a bullet,
     experience, or project. The page must look full.
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
6. **No overfull-hbox warnings** on any `\resumeSubheading` or
   `\resumeProjectHeading` line. This is the Step 5 overflow bug, not
   cosmetic noise.

Compilation errors → read the output, fix the `.tex`, recompile.

```bash
python3 scripts/run_timer.py mark tailor-resume compile
```

## Step 7 — Save

`build/resume.pdf` and `build/resume.tex` stay as working files. If
`rules.md` defines an archive location and naming convention, copy the
verified PDF there. If the destination is ambiguous, ask once.

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
python3 scripts/run_timer.py finish tailor-resume
```

```bash
python3 scripts/log_metric.py resume_tailor '{
  "company": "<company>", "role": "<role title>",
  "jd_source": "<url, file path, or \"pasted\">",
  "output_path": "<archived PDF path, else build/resume.pdf>",
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
