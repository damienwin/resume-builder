---
name: tailor-resume
description: Generate a tailored one-page LaTeX resume for a job description from the knowledge/ base in this repo. Use when the user provides a job posting URL, a JD file path, or pasted JD text and wants a resume tailored to it.
---

# tailor-resume — Tailor a one-page resume to a job description

Generate a competitive-tech one-page LaTeX resume tailored to the job
description the user supplied (URL, file path, or pasted text). All personal
facts come from `knowledge/` in this repo — the skill itself is
person-agnostic. If `knowledge/rules.md` exists, its selection and output
preferences OVERRIDE the generic defaults below.

The bar is a resume that survives both automated screening (ATS keyword
matchers, AI screeners) and a 6-second recruiter skim at a top-tier tech
company. Every step below serves one of those two audiences.

## Step 1 — Ingest the JD

- URL → use `WebFetch` to retrieve the posting and extract the full JD text
  (responsibilities, requirements, preferred qualifications, tech stack).
- File path → read the file.
- Pasted text → save it to `jd.txt` in the repo root, then proceed.
- If the fetch fails (JS-rendered page, login wall, empty response) → ask the
  user to paste the JD text or put it in `jd.txt` and rerun. Do NOT proceed
  with a guessed JD.

Extract from the JD and keep for the coverage check in Step 6:
- **Required** skills / languages / frameworks (exact strings as written —
  ATS matchers are literal: "JavaScript" ≠ "JS", "CI/CD" ≠ "continuous
  integration")
- Preferred / nice-to-have skills
- Domain (infra, ML, frontend, security, quant, …)
- Seniority (intern, new grad, mid, …)
- Recurring vocabulary the resume should mirror where truthful

## Step 2 — Load the knowledge base

Read every file under `knowledge/` **in one parallel batch** (single message,
one Read call per file — never sequentially):
- `profile.md`, `education.md`, `skills.md`
- `rules.md` if present (per-user selection & output preferences)
- All `knowledge/experience/*.md`, `knowledge/research/*.md`,
  `knowledge/projects/*.md`

Treat YAML frontmatter as structured metadata and the markdown body as
ground-truth context. The **seed bullets** in each file are pre-polished —
prefer adapting them over inventing new wording.

## Step 3 — Score and select

Rank each experience, research entry, and project by relevance to the JD
using the file's `tech` and `domain` frontmatter plus body content.

Select the subset that fits one page (defaults — `rules.md` overrides):
- **3–4 experiences** (combined from `experience/` + `research/`), most
  relevant and most recent first.
- **1–2 projects** most aligned with the JD. Prefer projects with a `repo:`
  in their frontmatter — AI resume screeners deduct per unlinked project.
  Projects marked `origin: work` or `origin: research` are exempt from this
  (no public repo expected for employer or academic work); a plain
  `origin: self` project with no `repo:` should be deprioritized in favor
  of a linked alternative when one fits the JD comparably well. Never use
  generic project names ("Calculator", "Todo App", "Weather App") — screeners
  penalize them; use the full descriptive name from the knowledge file.
- **Filtered skills** — pick ~10–14 languages and ~10–14 frameworks/tools
  the JD signals are relevant, spelled the way the JD spells them. Do NOT
  dump the entire master list.
- **Coursework line** — pick 4–6 courses from `education.md` aligned with
  the JD.

## Step 4 — Tailor bullets

For each selected role, write 3–4 bullets:
- Follow the XYZ formula recruiters at top companies are trained on:
  **"Accomplished X, as measured by Y, by doing Z"** — impact first, metric
  attached, method last. Not every bullet needs all three parts, but every
  bullet needs X, and every role needs at least one strong Y.
- Start with a strong action verb (Built, Vectorized, Engineered, Designed,
  Shipped, Tuned, Deployed — vary across the resume; never reuse the same
  opening verb twice in one role).
- Include a metric whenever the knowledge file supports one. **Never invent
  metrics that aren't in the knowledge base.**
- Mirror JD vocabulary where truthful, using the JD's exact spelling.
- When truthful and the knowledge base supports it, prefer vocabulary AI
  resume screeners read as complexity signals — real-time, authentication,
  database(s), microservices, named algorithms/data structures, ML/AI
  specifics, concrete user/adoption numbers. Never invent a signal that
  isn't backed by the knowledge file.
- Keep each bullet to roughly ≤ 165 characters at 11pt (a bit more at 10pt);
  a bullet that wraps past two lines gets cut or split.
- No first person ("I", "my"), no filler adjectives ("various",
  "cutting-edge", "state-of-the-art") — let the metric carry the weight.
- Prefer adapting an existing seed bullet over writing from scratch.
- **Prefer 3 tight, single-line-ish bullets over 4 dense ones.** A wall of
  four two-line bullets per role reads worse on a 6-second skim than three
  crisp ones, and ATS category scores are driven by entities/keywords
  present, not prose length — cutting filler doesn't cost keyword coverage.
  If a role has 4 candidate bullets, drop the weakest (usually a pure
  process/activity metric like a review or commit count, which reads as
  administrative rather than outcome-driven) before trimming the other
  three. Cut hedge words that don't carry a keyword or metric ("roughly,"
  "about," trailing restated clauses) rather than shortening the metric
  itself.
- **For self-projects, lean into systems-design/scale vocabulary the
  knowledge file actually supports, rather than a flatter feature
  description.** AI resume screeners can read an under-framed project as a
  "simple utility tool" and deduct for it even when the underlying work is
  substantive (orchestration, multi-agent/multi-stage pipelines,
  named-algorithm implementations, at-scale data processing, automated
  verification/testing layers). A/B-tested on a real JD: reframing bullets
  this way — same two projects, same facts, only wording — eliminated a
  recurring self-projects deduction and lifted the self-projects category
  score consistently (24/30 vs. 22/30 baseline across repeated scoring
  runs) without touching project selection. Concretely: prefer "architected
  a multi-agent orchestration system chaining N stages to process X at
  scale" over "built a pipeline that scans X"; prefer naming the actual
  algorithm/technique ("implementing forward/backward propagation, AdamW
  optimization") over a generic "used PyTorch/NumPy." Still never invent a
  signal the knowledge file doesn't back.

**Never hallucinate.** If the JD asks for something the knowledge base
doesn't support, leave it out rather than invent experience.

## Step 5 — Render

Read `templates/jakes_resume.tex`. Produce a fully-filled `.tex` file at
`build/resume.tex` by substituting every `<<PLACEHOLDER>>` block:
- Header (name, email, github, linkedin) from `profile.md`; the
  `\hypersetup` PDF metadata block also takes the name.
- Education (school, degree, GPA/honors, dates, location) from
  `education.md`; only the coursework line changes per JD.
- Repeat the `\resumeSubheading` / `\resumeProjectHeading` blocks per
  selected role/project.
- Each project's `\resumeProjectHeading` right cell renders its links, not a
  date: `\href{https://<repo>}{<repo>}` from the project's `repo:`
  frontmatter (bare `github.com/...` display text), plus
  `$|$ \href{<demo>}{<demo domain>}` when `demo:` is set. Links come only
  from frontmatter — never construct or guess a URL. For an
  `origin: work`/`origin: research` project with no `repo:`, fall back to
  the plain date in that cell. The `$|$` separator is allowed in headings
  (existing practice in the header line); it is still banned in bullets.
- Keep the standard section titles exactly as in the template
  (Education / Experience / Projects / Technical Skills) — ATS parsers
  segment resumes by those headings; never rename them.

**ATS-safe LaTeX rules (hard requirements, learned from real extraction
failures):**
- **No math-mode symbols in bullet text.** Never `$\to$`, `$\times$`, or
  `$\sim$` — they extract as Unicode arrows/multiplication/tilde glued to
  adjacent digits (e.g. `63.4s→<100ms`), which ATS keyword-matchers
  mis-tokenize. Write metrics in plain ASCII: "63.4s to under 100ms (about
  634x faster)", "roughly 12,388 tokens." `$p_{50}$`/`$R^2$`-style
  subscripts/superscripts extract cleanly as `p50`/`R2` and are fine.
- **Avoid `ffi`/`ffl` words when a plain synonym exists** (e.g. "burst
  load" instead of "burst traffic"). Tectonic's Latin Modern OTF fonts drop
  the ligature's ToUnicode mapping for those triples, so "traffic" extracts
  garbled.
- **A project heading row (title + tech stack on the left, repo/demo link
  on the right) must not overflow its column width.** A long `repo:` string
  paired with a long title/tech-stack list overflows the fixed-width
  `tabular*` row — LaTeX reports it as an "Overfull \hbox" warning, and
  visually the two cells collide with no space between them (e.g.
  `NumPygithub.com/...`), which also corrupts the extracted text. The link
  text must stay verbatim (Step 6 check 5), so when this happens shorten
  the **left side** instead: drop a parenthetical from the project title
  first (e.g. "Deep Neural Network (from scratch)" → "Deep Neural
  Network"), then shorten the tech-stack list if still overflowing. Never
  leave an overfull-hbox warning unresolved — treat it the same as a failed
  verification check in Step 6.

## Step 6 — Compile and verify (one pass)

Compile, then run the full verification in a single batch:

```bash
tectonic build/resume.tex 2>&1 | tee /tmp/tectonic.log | tail -20
pdfinfo build/resume.pdf | grep Pages        # must be exactly 1
pdftotext -layout build/resume.pdf -          # extraction check
pdftotext build/resume.pdf -                  # reading-order check
grep -i "overfull" /tmp/tectonic.log          # heading-row overflow check
```

(Tectonic pulls packages on demand. If missing: `brew install tectonic`;
`pdftotext` comes from `brew install poppler`. On macOS,
`mdls -name kMDItemNumberOfPages build/resume.pdf` also gives page count.)

Check all of the following against the extracted text — fix the `.tex` and
recompile only if something fails, then re-verify:

1. **Exactly one page.**
   - If **over**: shrink before cutting — drop to
     `\documentclass[letterpaper,10pt]{article}` and tighten
     `\resumeItemListStart` to
     `\begin{itemize}[itemsep=1pt, topsep=2pt, parsep=0pt]`. Still over:
     drop the weakest bullet from the longest role → shorten two-line
     bullets → drop a project before dropping an experience.
   - If **under** with significant trailing whitespace: add back a bullet,
     experience, or project you previously dropped — the final page must
     look full.
2. **Clean extraction** — grep the `-layout` output for every bullet's
   metrics; nothing glued or garbled (see Step 5 rules). Reword rather than
   fight the font.
3. **Reading order** — the no-`-layout` output reads top-to-bottom in
   resume order (header → education → experience → projects → skills).
4. **Keyword coverage** — every *required* JD skill that the knowledge base
   truthfully supports appears verbatim in the extracted text. List any
   required skill that is supported but missing, work it into skills or a
   bullet, and recompile. (Skills the knowledge base does NOT support are
   reported as gaps in Step 8 — never added.)
5. **Link visibility** — for every selected project with a `repo:`/`demo:`,
   grep the `-layout` output for the exact displayed URL string(s); each
   must appear verbatim and unbroken (not hyphen-split across a line wrap,
   and not glued to adjacent tech-stack text with no space). If a URL
   wraps, is missing, or collides with neighboring text, shorten the
   project's title/tech-stack list (not the URL) per the heading-row rule
   in Step 5, and recompile.
6. **No overfull-hbox warnings** on any `\resumeSubheading` or
   `\resumeProjectHeading` line — these are the heading-row-overflow bug
   from Step 5, not cosmetic noise. If `grep -i overfull` on the tectonic
   log matches a heading line, fix per Step 5's rule and recompile.

Compilation errors → read the error output, fix the `.tex`, recompile.

## Step 7 — Save output

`build/resume.pdf` and `build/resume.tex` stay in place as working files.
If `knowledge/rules.md` defines an archive location and naming convention,
copy the verified PDF there; if the destination is ambiguous, ask the user
once before saving.

## Step 8 — Report

Tell the user:
- Path to the final PDF
- Which experiences and projects were included, one-line reason each
- Which were excluded and why
- JD keyword coverage: which required/preferred keywords made it in, and
  anything in the JD that could NOT be backed up from the knowledge base —
  gaps worth adding to `knowledge/` before the next run
- **Outside-the-resume checklist** (AI-screener signals no PDF edit can
  fix): each linked repo should be public with a real README, description,
  and topic tags — screeners fetch the GitHub profile directly; note any
  selected project whose `repo:`/`demo:` is empty; note that real
  contributions to others' popular open-source projects move an
  open-source-style score far more than personal repos (which are capped
  low); mention `knowledge/profile.md`'s `website:` if it's still empty
  (a filled portfolio URL earns a small bonus). Point to `/ats-score` for
  an actual (noisy, diagnostic-only) score against HackerRank's public ATS
  rubric.

## Step 9 — Log metrics

Log this run for future reference (see `scripts/log_metric.py`):

```bash
python3 scripts/log_metric.py resume_tailor '{
  "company": "<company name>",
  "role": "<role title>",
  "jd_source": "<url, file path, or \"pasted\">",
  "output_path": "<archived PDF path if archived, else build/resume.pdf>",
  "experiences_included": ["<name>", ...],
  "projects_included": ["<name>", ...],
  "required_keywords_total": <N>,
  "required_keywords_covered": <N>
}'
```

Fire-and-forget — don't block or re-render the Step 8 report on this call,
and don't mention it to the user (it's a background log).

## Hard rules

- One page, always — via font/spacing shrink first, cutting content second;
  and visually full — no large trailing whitespace.
- Never invent metrics, employers, dates, or coursework.
- Bullet style: XYZ formula — action verb → what → measurable outcome.
- Skills filtered, not dumped. Coursework filtered, not dumped. JD spelling
  mirrored exactly where truthful.
- ATS safety is a hard requirement: standard section headings, no math-mode
  arrows/times/tilde in bullets, ligature-safe wording, verified extraction
  after every compile.
- Every selected project shows a visible repo/demo link unless its
  `origin:` is `work` or `research` — link deductions are the single
  biggest fixable loss against AI resume screeners.
- Preferences in `knowledge/rules.md` override the generic defaults here.
