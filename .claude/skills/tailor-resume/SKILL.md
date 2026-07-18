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
- **1–2 projects** most aligned with the JD.
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
- Keep each bullet to roughly ≤ 165 characters at 11pt (a bit more at 10pt);
  a bullet that wraps past two lines gets cut or split.
- No first person ("I", "my"), no filler adjectives ("various",
  "cutting-edge", "state-of-the-art") — let the metric carry the weight.
- Prefer adapting an existing seed bullet over writing from scratch.

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

## Step 6 — Compile and verify (one pass)

Compile, then run the full verification in a single batch:

```bash
tectonic build/resume.tex
pdfinfo build/resume.pdf | grep Pages        # must be exactly 1
pdftotext -layout build/resume.pdf -          # extraction check
pdftotext build/resume.pdf -                  # reading-order check
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
- Preferences in `knowledge/rules.md` override the generic defaults here.
