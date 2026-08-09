---
description: Tailor the resume to a job posting, then autofill that job's application with the tailored PDF
argument-hint: [job-url | jd-file]
---

Run the **apply** skill (`.claude/skills/apply/SKILL.md`) with `$ARGUMENTS`
as the job posting (URL or JD file path). It always tailors first
(tailor-resume skill), then fills the application via the job-apply plugin
with that freshly tailored PDF, stopping at final review. Follow that
skill's steps exactly.
