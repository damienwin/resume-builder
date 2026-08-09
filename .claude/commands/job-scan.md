---
description: Scan Simplify Jobs' New-Grad or Internship board for recent postings, filtered interactively by category and recency
argument-hint: [new-grad|internship] [--dsa] [--pm] [--swe] [--quant] [--hw] [--startup] [--days N] [--compare-offer]
---

Run the **job-scan** skill (`.claude/skills/job-scan/SKILL.md`) with
`$ARGUMENTS`. Any choice not given as a flag (board, categories, recency
window, whether to compare against the current offer) is asked as an
interactive checklist per that skill's Step 0 — don't guess at unstated
choices. Follow that skill's steps exactly.
