---
name: app-profile-sync
description: Sync facts from knowledge/ into the job-apply plugin's local store (~/.job-apply) so web-form autofill answers stay consistent with the resume source of truth. Use when the user wants to set up or refresh their application-autofill profile, after editing knowledge/ files, or before a batch of applications.
---

# app-profile-sync — Push knowledge/ facts into the job-apply autofill store

The `job-apply` plugin (neonwatty-plugins) fills job-application web forms and
keeps its applicant data in `~/.job-apply/`. This skill makes `knowledge/` the
source of truth: it reads the repo's frontmatter and merges it into the
plugin's store **through the plugin's own helper script** — never by editing
the JSON files directly.

## Hard rules

- **Never** create, patch, append to, or replace files under `~/.job-apply/`
  with Read/Write/Edit or shell redirection. Every read and write goes through
  the plugin's `job-apply-store.py` helper. If the helper exits nonzero, stop,
  preserve the files, and explain the failure without printing stored values.
- Content marked `INTERNAL-ONLY` in `knowledge/` files, and anything
  `knowledge/rules.md` marks confidential, must never be written to the store.
- Never store credentials, tokens, payment data, or browser state.
- Sensitive facts (work authorization, visa status, salary expectations,
  demographics) follow the plugin's two-decision rule in Step 4.

## Step 0 — Locate the helper and initialize

Find the installed plugin's helper (the version directory changes on update):

```bash
ls -d ~/.claude/plugins/cache/neonwatty-plugins/job-apply/*/scripts/job-apply-store.py
```

If missing, tell the user to install the plugin
(`claude plugin marketplace add neonwatty/job-apply-plugin` then
`claude plugin install job-apply@neonwatty-plugins`) and stop. If more than one
version directory exists, use the one `claude plugin list` reports as
installed. Call the resolved path `$STORE` below, then:

```bash
python3 "$STORE" init
python3 "$STORE" paths
```

## Step 1 — Gather facts from knowledge/

Read frontmatter (and only what's needed from bodies) of:

- `knowledge/profile.md` — name, email, phone, location, github, linkedin,
  website. **If `phone` is blank, ask the user for it once** (forms always
  require it) and offer to save it back to `knowledge/profile.md`.
- `knowledge/education.md` — school, degree, location, start, end, gpa, honors.
- `knowledge/experience/*.md` — company, title, start, end, location, tech.
  Skip body content; seed bullets are resume material, not form answers.
- `knowledge/projects/*.md` — name, repo, demo, tech (useful for portfolio
  questions).
- `knowledge/skills.md` — the categorized master list, flattened.
- `knowledge/demographics.md` — **optional and often absent**; skip silently
  if missing, and never create it unprompted. Read only its filled fields:
  voluntary EEO answers, pronouns, relocation, work authorization, and the
  consent toggles. A blank field means "ask at fill time" — do not store a
  placeholder for it. A field set to `decline` is a real answer meaning
  "choose the form's decline-to-answer option," so store it as such. Never
  infer any of these from a name, school, or any other field.
- `knowledge/rules.md` — apply its confidentiality rules to everything above.

## Step 2 — Read the current store profile

```bash
python3 "$STORE" profile-get
```

The inner `profile` object is free-form; **conform to whatever field names it
already uses** (the plugin's own resume extraction may have populated it). Do
not invent a parallel schema next to existing fields. If the profile is empty
(fresh install), use flat, plainly-named fields (name, email, phone, location,
links, education[], experience[], skills[]).

## Step 3 — Merge and replace

Build the merged profile object in a private temp file in the session
scratchpad directory (never in the repo, never under `~/.job-apply/`):
knowledge/ values win for facts they cover; every field knowledge/ doesn't
cover (plugin preferences, prior answers metadata, unknown keys) is preserved
exactly. Then:

```bash
python3 "$STORE" profile-replace --input <temp-profile.json>
rm <temp-profile.json>
```

## Step 4 — Seed reusable answers

For non-sensitive facts that recur across applications (GitHub/LinkedIn/
portfolio URLs, graduation date, degree/school), check for an existing record
first with `answer-find`, then store missing ones via `answer-put --input`
with state `"inferred"` — the plugin will still show them for confirmation
before first use. Never mark anything `confirmed` yourself.

Demographic and self-identification answers from `knowledge/demographics.md`
are sensitive: the user already opted in by writing them down, but that is
consent to *use* them, not consent for the plugin to remember them. Ask the
plugin's two questions for these exactly as for any sensitive fact below,
and store with `--remember-sensitive` only on explicit consent. Store them
verbatim — never normalize a `decline` into a blank, or a blank into a
guess.

For sensitive facts (work authorization, visa, salary): ask the user the
plugin's two separate questions — (1) use in forms? (2) remember for later?
Only with explicit remember-consent store via
`answer-put --input <file> --remember-sensitive`; otherwise store nothing (a
`"value": null` sensitive placeholder is allowed).

## Step 5 — Report

Summarize what changed: fields added/updated in the profile, answers seeded,
anything skipped for confidentiality — **without printing sensitive values**.
Remind the user that the actual filling flow is `/job-apply:job-apply
<job URL>` (with the Claude in Chrome extension connected), which always stops
at final review and never submits.
