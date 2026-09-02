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

**Set `$SLUG` now** — the same per-job slug the `tailor-resume` skill uses
for `build/$SLUG.*`. Separately, **set `$RUN_ID` now and pass it as
`--scope` on every `run_timer.py` call in this run** — never `$SLUG`, and
never reassign it once set. This skill runs as one fork per posting in
`job-scan/references/acting-on-results.md`'s parallel dispatch, and
concurrent forks were observed sharing one `CLAUDE_CODE_SESSION_ID` — see
`run_timer.py`'s docstring. Without a distinct `--scope` per fork, one
fork's `start` clobbers another's shared timer file, and every fork after
the first `finish` gets `{}` back instead of real timing. `$SLUG` is not
distinct enough on its own for this — two postings can slugify to the same
value (e.g. two "Software Engineer" roles at the same company) and
reproduce that exact collision — so use a random token instead:

```bash
SLUG=<company-role-slug>
RUN_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:10])")
python3 scripts/run_timer.py start apply --scope "$RUN_ID"
```

Run the **tailor-resume** skill (`.claude/skills/tailor-resume/SKILL.md`) on
the given job URL / JD file / pasted text, exactly as `/tailor` would —
including its ATS-safety pass, one-page check, and the archive step from
`knowledge/rules.md` (copy to `~/Desktop/Tailored Resumes/<Tag> Damien
Nguyen.pdf`). That skill names its working files `build/<slug>.*` per job —
do not reintroduce a shared `build/resume.pdf`. Do not skip tailoring even if
some build looks recent; it may have been tailored for a different company.
Only skip if the user explicitly confirms the current build was tailored for
**this** posting in this session.

```bash
python3 scripts/run_timer.py mark apply tailor --scope "$RUN_ID"
```

## Step 2 — Point the autofill store at the archived PDF

Use the **archived** copy (stable path), not a `build/` working file (the
next tailor run for the same job overwrites it). Update `resumePath` through
the plugin helper only (locate it as in `.claude/skills/app-profile-sync/SKILL.md`
Step 0):

```bash
python3 "$STORE" profile-get   # read current profile
# merge: set resumePath to the archived PDF's absolute path (temp file in scratchpad)
python3 "$STORE" profile-replace --input <temp-profile.json>
```

Never edit `~/.job-apply/` files directly.

**`resumePath` is a single global field, and Claude in Chrome uploads one
file at a time.** Parallel apply runs (job-scan Step 6 fans out one fork per
posting) share both, so a fork that sets `resumePath` early and uploads late
will upload whatever the *other* fork wrote in between. Last writer wins, so
the losing fork attaches the wrong company's resume to a real application,
with nothing in the UI flagging it.

Treat set-then-upload as one critical section, and hold a lock across it:

```bash
LOCK=~/.job-apply/.resumepath.lock
for i in $(seq 1 60); do                       # bounded: ~3 min, never forever
  mkdir "$LOCK" 2>/dev/null && break
  # Break a lock leaked by a fork that died mid-upload. No real upload takes
  # 5 minutes, so an older lock has no live owner.
  if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +5)" ]; then
    rmdir "$LOCK" 2>/dev/null
  fi
  sleep 3
done
[ -d "$LOCK" ] || { echo "could not acquire resumePath lock"; exit 1; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
```

The retry is **bounded and self-healing on purpose.** `trap ... EXIT` fires
only for the shell that set it, so a killed fork, an interrupted session, or
a crashed tool call all leave the directory behind. A plain
`until mkdir ...; do sleep 3; done` then never exits against a leaked lock,
wedging every later apply run in every session until someone removes the
directory by hand. Fail loudly instead of hanging.

Take the lock immediately before the `profile-replace` above, and release it
only after Step 3's upload has been verified. Do the slow work — tailoring,
opening the tab, filling every other field — *outside* the lock, so the
serialized window is just write → upload → verify.

## Step 3 — Fill the application

Run the `job-apply` plugin's fill flow (`/job-apply:job-apply <job URL>`,
Claude in Chrome extension) for the posting. Reach the resume upload with the
Step 2 lock still held, and verify the attached filename is this posting's
archived tailored PDF from Step 1 — read it back off the form, not from your
own expectation. If it isn't, stop and fix before continuing. Release the
lock (`rmdir "$LOCK"`) once the filename is confirmed; the rest of the form
needs no lock.

## Step 4 — Stop at final review

The plugin never submits; neither does this skill. Summarize the filled
fields, confirm the uploaded resume filename, and leave Submit to the user.

## Step 5 — Log metrics

First mark the fill step and close out the run timer — its output has
`duration_s` and a `steps` breakdown (`tailor`, `fill`):

```bash
python3 scripts/run_timer.py mark apply fill --scope "$RUN_ID"
python3 scripts/run_timer.py finish apply --scope "$RUN_ID"
```

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
  "status": "<filled_pending_submit|filled_with_gaps|failed>",
  "duration_s": <from run_timer finish>, "steps": <from run_timer finish>
}'
```

Fire-and-forget — don't mention it to the user (it's a background log).
