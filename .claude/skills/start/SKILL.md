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

## Step 2 — Scan (and act)

Run the **job-scan** skill (`.claude/skills/job-scan/SKILL.md`) exactly as
`/job-scan` would, with no preset flags — let its Step 0 interactive
checklist run in full (board, categories, recency, compare-offer), and let
it run all the way through, including its own Step 6.5 "Offer to act on
results" (confirm the list, pick postings + tailor/apply, fan out to
parallel subagent forks). Do not skip or shortcut any of its steps —
job-scan's triage-and-act flow is now its own default behavior, so `/start`
doesn't need to add a separate triage layer on top of it.

## Step 3 — Report

job-scan's own Step 6.5/7 summary already covers everything acted on. Just
remind the user this was `/start`'s guided entry point and they can rerun
`/job-scan` directly next time for the same flow without the setup check in
Step 1.

## Hard rules

- Never skip job-scan's own interactive checklist or its Step 6.5 act flow.
- Inherits every hard rule from `job-scan`, `tailor-resume`, and `apply` —
  in particular: never invent facts, and applications always stop at final
  review, never auto-submit.
