---
type: demographics
# Every field below is OPTIONAL. Leave any of them blank and the apply flow
# will stop and ask you instead of guessing. Fill in the ones you're happy to
# have answered automatically.
#
# Voluntary EEO / self-identification questions (US employers). Answering
# these is always your choice, and declining is itself a valid answer —
# write "decline" for any question you'd rather not answer, and the flow will
# select the form's "I don't wish to answer" option rather than asking again.
gender:              # e.g. Male | Female | Non-binary | decline
gender_identity:     # only if a form asks separately from `gender`
pronouns:            # e.g. he/him | she/her | they/them | decline
hispanic_latino:     # Yes | No | decline
race:                # e.g. Asian | Black or African American | White | Two or more races | decline
veteran_status:      # e.g. not a protected veteran | protected veteran | decline
disability_status:   # Yes | No | decline

# Practical questions that block submission on many forms. These aren't EEO
# fields, they're just answers worth storing once.
willing_to_relocate:   # Yes | No | Depends — see notes below
requires_sponsorship:  # Yes | No
work_authorization:    # e.g. authorized to work for any employer in the US

# Consent toggles some portals require before they accept the form.
demographic_data_consent:   # Yes | No
data_retention_consent:     # Yes | No — some portals ask to retain your data (e.g. 1460 days)
---

# Demographics & self-identification (optional)

This file exists purely to reduce friction: US job applications ask the same
voluntary EEO questions every time, and a handful of consent checkboxes are
marked required, so an unanswered one blocks submission.

**This file is optional in full.** Delete it if you'd rather be asked each
time. `knowledge/` is gitignored, so nothing here is ever committed — but it
does get synced into the local job-apply store by `/app-profile-sync`, which
is what the autofill reads.

**How blanks behave.** A blank field means "ask me." A field set to
`decline` means "select the form's decline-to-answer option" — that's a real
answer, not a blank, so you won't be asked again. Nothing in this file is
ever inferred from your name, photo, school, or anything else; if it isn't
written here or already confirmed in the store, you get asked.

## Notes

Anything that needs more than one word — a relocation answer that depends on
the city, a work-authorization detail worth spelling out — goes here as prose.
The apply flow reads this section when a form's phrasing doesn't map cleanly
onto the fields above.

## What still won't be auto-filled

Some questions stay with you no matter what this file says:

- **Certification / attestation dropdowns** ("I certify this information is
  true and correct") — a statement you make personally.
- **Free-text "why do you want to work here"** and similar essay prompts.
- **Subjective screening questions** with no unambiguous answer in
  `knowledge/` (self-reported years of experience, "select up to N" skill
  checklists with no true match).
