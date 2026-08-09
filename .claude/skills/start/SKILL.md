---
name: start
description: The guided entry point for this repo — scan Simplify's job boards, then optionally tailor and/or apply to whatever looks worth pursuing. Use when the user opens this repo fresh and isn't sure where to begin, or explicitly asks to "start", "get going", "find and apply to jobs".
---

# start — Scan, then act

This is the recommended first command in a fresh clone of this repo. It
chains three things that otherwise have to be invoked separately: scanning
Simplify's boards, tailoring a resume, and filling an application. It never
does any of that silently — every step is confirmed with the user before it
runs, and applications always stop at final review.

## Step 1 — Check setup

If `knowledge/` doesn't exist yet, stop here and walk the user through
"Setting it up for yourself" in `README.md` (copy `knowledge.example/` to
`knowledge/`, fill in their real info) before continuing — scanning without
a filled `knowledge/` means later tailoring/applying can't do anything
useful with the results.

## Step 2 — Scan

Run the **job-scan** skill (`.claude/skills/job-scan/SKILL.md`) exactly as
`/job-scan` would, with no preset flags — let its Step 0 interactive
checklist run in full (board, categories, recency, compare-offer). Do not
skip or shortcut its steps; this skill only adds Step 3 below on top of it.

## Step 3 — Offer to act on the results

If job-scan surfaced zero postings, say so and stop — nothing to act on.

Otherwise, ask the user (`AskUserQuestion`, multi-select) which of the
surfaced postings, if any, they want to act on now (list them by
company — role). For the ones selected, ask once (single-select, applies to
all selected postings) which action to take:

- **Tailor + apply** — run the `apply` skill
  (`.claude/skills/apply/SKILL.md`) for that posting's URL, exactly as
  `/apply <url>` would.
- **Tailor only** — run the `tailor-resume` skill
  (`.claude/skills/tailor-resume/SKILL.md`) for that posting's URL, exactly
  as `/tailor <url>` would, without touching the job-apply plugin.
- **Skip** — do nothing further for that posting.

Process selected postings one at a time, in the order the user picked them.
Before starting each one, name the company/role so the user can see
progress. If a posting's apply link isn't a page `tailor-resume`/`apply` can
fetch cleanly (JS-rendered, login wall), follow those skills' existing
fallback (ask the user to paste the JD or save it to `jd.txt`) rather than
guessing content.

## Step 4 — Report

Summarize what happened per posting acted on (tailored PDF path, and for
"tailor + apply", confirmation it stopped at final review per the `apply`
skill's Step 4 — never submitted). Remind the user any postings they didn't
act on are still listed above if they change their mind.

## Hard rules

- Never skip job-scan's own interactive checklist — this skill adds a
  results-triage step on top of it, it doesn't replace any of its steps.
- Never tailor or apply to a posting the user didn't explicitly select.
- Inherits every hard rule from `job-scan`, `tailor-resume`, and `apply` —
  in particular: never invent facts, and applications always stop at final
  review, never auto-submit.
