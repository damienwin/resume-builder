---
name: apply
description: Tailor the resume to a job posting, then fill that job's web application with the freshly tailored PDF via the job-apply plugin. Use when the user wants to apply to a job end-to-end (tailor + autofill), or says "apply to <url>". Never fill an application with a resume that wasn't tailored for that posting.
---

# apply — Tailor-then-fill, always in that order

An application must never be filled with a stale or generic resume. This
skill is the required entry point for applying: it runs the tailoring
pipeline for the specific posting first, then drives the `job-apply` plugin
with that exact PDF.

## Step 1 — Tailor for this posting

Run the **tailor-resume** skill (`.claude/skills/tailor-resume/SKILL.md`) on
the given job URL / JD file / pasted text, exactly as `/tailor` would —
including its ATS-safety pass, one-page check, and the archive step from
`knowledge/rules.md` (copy to `~/Desktop/Tailored Resumes/<Tag> Damien
Nguyen.pdf`). Do not skip tailoring even if `build/resume.pdf` looks recent —
it may have been tailored for a different company. Only skip if the user
explicitly confirms the current build was tailored for **this** posting in
this session.

## Step 2 — Point the autofill store at the archived PDF

Use the **archived** copy (stable path), not `build/resume.pdf` (overwritten
by the next tailor). Update `resumePath` through the plugin helper only
(locate it as in `.claude/skills/app-profile-sync/SKILL.md` Step 0):

```bash
python3 "$STORE" profile-get   # read current profile
# merge: set resumePath to the archived PDF's absolute path (temp file in scratchpad)
python3 "$STORE" profile-replace --input <temp-profile.json>
```

Never edit `~/.job-apply/` files directly.

## Step 3 — Fill the application

Run the `job-apply` plugin's fill flow (`/job-apply:job-apply <job URL>`,
Claude in Chrome extension) for the posting. When it reaches the resume
upload, verify the selected filename is the archived tailored PDF from
Step 1 — if it isn't, stop and fix before continuing.

## Step 4 — Stop at final review

The plugin never submits; neither does this skill. Summarize the filled
fields, confirm the uploaded resume filename, and leave Submit to the user.

## Step 5 — Log metrics

Log this run for future reference (see `scripts/log_metric.py`), after
Step 4's summary — never before, since `status` depends on how filling
actually went:

```bash
python3 scripts/log_metric.py job_apply_e2e '{
  "company": "<company name>",
  "role": "<role title>",
  "job_url": "<posting URL>",
  "resume_path": "<archived PDF path used>",
  "ats_platform": "<linkedin|greenhouse|ashby|lever|rippling|workday|other>",
  "status": "<filled_pending_submit|filled_with_gaps|failed>"
}'
```

Fire-and-forget — don't mention it to the user (it's a background log).
