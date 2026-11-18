---
description: Guided entry point — connect your sources, fill knowledge/, set your settings and autofill, then scan and tailor + apply
argument-hint: [settings]
---

Run the **start** skill (`.claude/skills/start/SKILL.md`). On a fresh clone
it connects sources, fills `knowledge/` and asks for anything missing, sets
up settings and the autofiller, then runs the `job-scan` skill and tailors +
applies to whatever the user picks. If `$ARGUMENTS` is `settings`, run only
its Steps 1.6 and 1.7. Follow that skill's steps exactly — never skip its
confirmation prompts.
