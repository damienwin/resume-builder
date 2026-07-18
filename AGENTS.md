# resume-builder — agent instructions

This repo generates a tailored one-page LaTeX resume for a job description
from the personal knowledge base under `knowledge/`.

## The one task this repo exists for

When the user provides a job posting URL, a JD file path, or pasted JD text
and asks for a tailored resume, follow the pipeline in
**`.claude/skills/tailor-resume/SKILL.md`** exactly. That file is plain
markdown and is the single source of truth for the workflow — it is not
Claude-specific apart from tool names:

- Where it says `WebFetch`, use your web search / URL fetch capability.
- Everything else (shell commands, file edits) uses standard tools.

Key invariants from that pipeline (do not skip):

- All facts come from `knowledge/` — **never invent metrics, employers,
  dates, or coursework**. If the JD asks for something the knowledge base
  doesn't support, leave it out.
- If `knowledge/rules.md` exists, its per-user selection and output
  preferences override the generic defaults.
- Output must be **exactly one page** — solve overflow by shrinking font
  and spacing before cutting content.
- Compile with `tectonic build/resume.tex` (writes `build/resume.pdf`).
- ATS-safety pass is mandatory: no math-mode `$\to$`/`$\times$`/`$\sim$` in
  bullet text, avoid `ffi`/`ffl` ligature words (e.g. prefer "burst load"
  over "burst traffic"), and verify clean extraction with
  `pdftotext -layout build/resume.pdf -` after every compile.
- Keyword coverage is checked against the same extracted text: every
  *required* JD skill the knowledge base truthfully supports must appear
  verbatim; unsupported ones are reported as gaps, never added.

## Repo layout

- `knowledge/` — the user's full history (profile, education, skills,
  experience/, research/, projects/, optional rules.md)
- `templates/jakes_resume.tex` — parameterized template; fill every
  `<<PLACEHOLDER>>` from `knowledge/`
- `build/` — generated output (gitignored)
- `jd.txt` — scratch file for pasted job descriptions (gitignored)

## Setup for a new user

See "Setting it up for yourself" in `README.md`: replace the contents of
`knowledge/` with your own files (copy the existing ones as format
examples), delete or rewrite `knowledge/rules.md`, and install
`tectonic` + `poppler` (for `pdftotext`).
