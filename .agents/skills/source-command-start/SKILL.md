---
name: "source-command-start"
description: "Guided entry point — connect your sources, fill knowledge/, set your settings and autofill, then scan and tailor + apply"
---

# source-command-start

Use this skill when the user asks to run the migrated source command `start`.

## Command Template

Run the **start** skill (`.agents/skills/start/SKILL.md`). On a fresh clone
it connects sources, fills `knowledge/` and asks for anything missing, sets
up settings and the autofiller, then runs the `job-scan` skill and tailors +
applies to whatever the user picks. If the user asked for settings, run only
its Steps 1.6 and 1.7. Follow that skill's steps exactly — never skip its
confirmation prompts.
