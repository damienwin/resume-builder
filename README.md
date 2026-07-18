# resume-builder

Generate a tailored one-page LaTeX resume for any job description by combining
a rich personal knowledge base with a Claude Code skill.

## Setting it up for yourself

Everything person-specific lives in `knowledge/`, which is gitignored — your
real data never gets committed. The repo ships `knowledge.example/` with
placeholder templates showing the expected format. To make this repo yours:

1. **Install prerequisites:**
   ```bash
   brew install tectonic poppler   # tectonic compiles the LaTeX; poppler's pdftotext runs the ATS check
   ```
2. **Copy the templates**: `cp -r knowledge.example knowledge`
3. **Fill in `knowledge/profile.md`, `education.md`, and `skills.md`** with
   your info, and add files under `knowledge/experience/`,
   `knowledge/research/`, and `knowledge/projects/` with your own history
   (use the template files as format examples: YAML frontmatter + rich
   prose + seed bullets). The richer the prose, the better the tailoring —
   but avoid pasting internal-only system names, proprietary architecture,
   or non-public metrics from employers if you ever intend to make your
   fork public.
4. **Delete or rewrite `knowledge/rules.md`** — it holds per-person
   selection preferences (which roles to always include, where to archive
   finished PDFs). The skill works fine without it.
5. Open the folder in Claude Code (or Codex — see below) and run
   `/tailor <jd-url-or-file>`.

The skill itself (`.claude/skills/tailor-resume/SKILL.md`) is fully
person-agnostic — it fills the template header/education from your
`knowledge/` files, so no template editing is needed.

**Using Codex (or another agent) instead of Claude Code:** the repo ships an
`AGENTS.md` that points any agent at the same pipeline. In Codex, just ask
"tailor my resume to this JD: <url or paste>" from inside the repo — no
slash command needed.

## Usage

From inside this directory in Claude Code:

```
/tailor https://jobs.example.com/swe-intern-2026
```

or, if the page is JS-rendered / behind a login wall:

```
# paste the JD text into jd.txt, then:
/tailor jd.txt
```

(The command is `/tailor`, not `/resume` — `/resume` is Claude Code's
built-in session-resume command and would shadow it.)

You can also just ask in plain language ("tailor my resume to this JD: …") —
the `tailor-resume` skill triggers on that too.

Output: `build/resume.pdf` (one page, Jake's Resume LaTeX format).

## How it works

1. The `tailor-resume` skill fetches the JD (URL, file, or pasted text).
2. Reads everything under `knowledge/` — your full history in prose form.
3. Scores each experience / research / project by JD relevance.
4. Picks 3–4 experiences + 1–2 projects + filtered skills/coursework to fit one page.
5. Tailors bullets to mirror JD vocabulary (truthfully — never invents).
6. Fills `templates/jakes_resume.tex`, writes `build/resume.tex`, compiles to PDF.
7. Runs an ATS-safety pass (clean text extraction, no garbled ligatures or
   math-mode symbols), checks required JD keywords actually appear in the
   extracted text, and verifies the result is exactly one page.

## Adding a new experience / project

Drop a new markdown file into the right folder:

```
knowledge/
  experience/<company-year>.md      # internships, jobs
  research/<project-slug>.md         # lab / research positions
  projects/<project-slug>.md         # personal / class projects
```

Use any existing file as a template. The richer the prose, the better the
tailoring — include metrics, scale, stakeholders, design tradeoffs, even
material that won't fit on the resume. The AI's job is to compress; the
knowledge base's job is to remember everything.

## Editing the master profile

- `knowledge/profile.md` — name, email, github, linkedin, phone
- `knowledge/education.md` — school, dates, GPA, coursework, honors
- `knowledge/skills.md` — full skill master list (filtered per JD at gen time)
- `knowledge/rules.md` — optional per-person selection & output rules

## Layout

```
resume-builder/
├── AGENTS.md                       # entry point for Codex & other agents
├── .claude/
│   ├── commands/tailor.md          # thin /tailor wrapper (Claude Code)
│   └── skills/tailor-resume/       # the pipeline (person-agnostic)
│       └── SKILL.md
├── knowledge.example/               # placeholder templates (committed)
│   ├── profile.md
│   ├── education.md
│   ├── skills.md
│   ├── rules.md
│   ├── experience/
│   ├── research/
│   └── projects/
├── knowledge/                      # YOUR full history (gitignored, not committed)
│   ├── profile.md
│   ├── education.md
│   ├── skills.md
│   ├── rules.md                    # optional per-person preferences
│   ├── experience/                 # one file per role
│   ├── research/                   # one file per research position
│   └── projects/                   # one file per project
├── templates/jakes_resume.tex     # parameterized LaTeX template
├── build/                          # generated, gitignored
└── README.md
```
