---
type: rules
owner: Your Name
---

# Personal selection & output rules (optional)

These override the generic defaults in the `tailor-resume` skill. This file
is entirely optional — delete it if you're fine with the skill's defaults.

## Experience selection

- Which role(s) should always be included, and why (e.g. most recent /
  strongest signal)?
- How should the remaining slots be filled based on JD focus?
- Which role gets dropped first when space is tight?
- Prefer shrinking font vs. dropping content? Any hard floor on number of
  experiences?

## Project selection

- Which project(s) map to which kind of role (ML research vs. systems vs.
  general SWE, etc.)?
- Any bullet variants that should be picked based on JD emphasis (e.g.
  on-prem vs. cloud, research vs. production)?

## Job scan defaults (optional)

Fill these in to skip `/job-scan`'s interactive checklist:

- Which board (New Grad vs. Internship), which categories, how many days back?
- Compare postings against a current offer? If so, is there a flat comparison
  bar to use instead of the real figure (keeps output generic)?
- Anything the scan should always flag or always drop?

Two behaviors are built into the skill and need no configuration here, but
are worth knowing:

- **Advanced-degree (🎓) postings** are checked against the degree in
  `education.md` before they reach the table, and dropped only when the
  posting requires a degree above yours. A Master's or PhD candidate keeps
  the rows a bachelor's candidate drops.
- **Application caps** (some employers cap concurrent applications) are noted
  per posting when known, and never guessed.

## Output archive

Where should verified PDFs be copied, and how should they be named /
organized (e.g. by season, by role type)?

If the JD is ambiguous about categorization, ask once before saving.
