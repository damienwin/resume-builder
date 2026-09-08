---
name: "source-command-start"
description: "Guided entry point — scan Simplify's job boards, then optionally tailor and/or apply to what looks worth pursuing"
---

# source-command-start

Use this skill when the user asks to run the migrated source command `start`.

## Command Template

Run the **start** skill (`.Codex/skills/start/SKILL.md`). It runs the
`job-scan` skill's interactive checklist first, then offers to tailor and/or
apply to whatever the user selects from the results. Follow that skill's
steps exactly — never skip its confirmation prompts.
