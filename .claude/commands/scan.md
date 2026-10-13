---
description: Quick job scan — optional day count, otherwise everything since the last scan
argument-hint: [days]
---

Run the **job-scan** skill (`.claude/skills/job-scan/SKILL.md`) using the
defaults in `knowledge/rules.md` for board, categories, and offer comparison
— don't ask the Step 0 checklist for those.

Recency comes from `$ARGUMENTS`:

- A single integer N → `--days N`.
- Blank → `--since-last-scan`.

Follow that skill's steps exactly.
