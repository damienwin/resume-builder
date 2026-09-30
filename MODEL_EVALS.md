# Cross-model resume-builder evaluations

This protocol measures which model is most effective at each stage of the
resume-builder workflow. It keeps quality and efficiency separate so a fast,
cheap run cannot hide a factual or rendering failure.

## Storage and reproducibility

- Raw events are append-only in `knowledge/model_eval_runs.jsonl`.
- PDFs, active timers, case files, and blinded review packs live under
  `eval/model_bakeoff/`.
- Both locations are gitignored because they can contain personal resume data.
- In local Conductor workspaces, `scripts/model_eval.py` writes through
  `CONDUCTOR_ROOT_PATH` only when that path is another checkout containing this
  evaluator. When invoked from an unrelated Conductor repository, it stays in
  the Resume Builder checkout. Use `--repo-root` or
  `RESUME_BUILDER_EVAL_REPO` to deliberately select a different checkout.
- Every run records the exact provider, model, reasoning level, repo commit,
  dirty-worktree state, protocol version, benchmark cohort, case hash, and
  prompt hash. Never
  replace old events when a prompt or model changes; create new runs and compare
  versions.

If a run used a broken fixture or invalid protocol, retain it but exclude it
from aggregates with `python3 scripts/model_eval.py exclude --run-id <id>
--reason '<reason>'`. Exclusions are append-only audit events; nothing is deleted.

Initialize the local dataset once:

```bash
python3 scripts/model_eval.py init
```

Fill the three local case files referenced by `eval/model_bakeoff/cases.json`
with frozen job descriptions: backend/infrastructure, ML/data, and general SWE.
Do not fetch live pages during the basic bake-off.

## Pilot matrix

Run each available model once on each of the three cases for the
`tailor-end-to-end` stage. Use the exact same prompt file, knowledge snapshot,
repo commit, and reasoning level. Record exact model IDs rather than product
labels such as "Codex" or "Claude".

Start timing immediately before sending the task:

```bash
python3 scripts/model_eval.py start \
  --case backend \
  --case-path eval/model_bakeoff/cases/backend.txt \
  --prompt-path eval/model_bakeoff/prompts/tailor-end-to-end.md \
  --stage tailor-end-to-end \
  --provider codex \
  --model '<exact-model-id>' \
  --reasoning medium \
  --cohort pilot-v1 \
  --plan chatgpt-plus
```

Keep the printed run ID. Finish after the PDF and verification results exist:

```bash
python3 scripts/model_eval.py finish \
  --run-id '<run-id>' \
  --status success \
  --artifact build/<slug>.pdf \
  --artifact build/<slug>.tex \
  --input-tokens 0 \
  --output-tokens 0 \
  --reasoning-tokens 0 \
  --cached-input-tokens 0 \
  --cache-write-input-tokens 0 \
  --total-tokens 0 \
  --token-source exact \
  --latency-s 0 \
  --cost-usd 0 \
  --cost-basis list-price-proxy \
  --resolved-model '<canonical-model-id>' \
  --turns 1 \
  --interventions 0 \
  --retries 0
```

Use provider-reported token counts when available. Mark ChatGPT web counts as
`estimated` or `unavailable`; hidden reasoning and subscription limits make them
incomparable to exact Codex or Claude transcript counts. Reports deliberately
exclude estimated tokens from the exact-token average. Subscription-backed CLI
costs are recorded as `list-price-proxy`, not actual marginal spend.

Latency includes the entire user-visible run: reasoning, tool calls, compile,
verification, and repairs. Record an intervention whenever the user must clarify,
approve a correction, provide missing text, or tell the agent to continue.

## HackerRank ATS scoring

Score every successful PDF three times with the existing `ats-score` workflow.
Do not reuse a cached LLM evaluation across the three runs. Record all totals,
not only the best one:

```bash
python3 scripts/model_eval.py ats \
  --run-id '<run-id>' \
  --score 68 --score 72 --score 70
```

The report uses the median and preserves min, max, and spread. HackerRank's
open-source scorer is noisy and is one quality signal, not the winner selector.

## Blinded human readability

After at least two models finish the same case and stage, generate a blinded
pack:

```bash
python3 scripts/model_eval.py blind \
  --case backend --stage tailor-end-to-end --seed 20260907
```

Open the lettered PDFs without opening `mapping.json`. Fill `review.csv` with
integer ratings from 1 to 5 for:

- `scanability`: structure and six-second skim quality
- `clarity`: direct, understandable bullets
- `specificity`: concrete technical substance and supported impact
- `naturalness`: avoids awkward keyword stuffing or AI-like prose
- `confidence`: willingness to submit without rewriting

Then import it:

```bash
python3 scripts/model_eval.py import-human \
  --review-csv eval/model_bakeoff/blind/backend-tailor-end-to-end/review.csv
```

## Comparing models

## Stage-level, adversarial evaluation (stage-v2)

Use a new `stage-v2` cohort; never mix it into `pilot-v1`. Each stage artifact
is JSON and must cite exact JD spans (`requirements[].jd_quote`) and exact
knowledge evidence (`claims[]` or `bullets[]` with `source_path` and
`source_quote`). Audit it before any LLM review:

```bash
python3 scripts/eval_audits.py artifact.json --case-path eval/model_bakeoff/cases/backend.txt \
  --knowledge-root knowledge --out audit.json
python3 scripts/model_eval.py audit --run-id '<run-id>' --report audit.json
```

Every author receives a blinded reciprocal review: Claude judges Codex output,
and Codex judges Claude output. Judge verdict JSON must contain 1–5 ratings for
`scanability`, `clarity`, `specificity`, `naturalness`, and `confidence`, a
boolean `factuality_pass`, and evidence-backed `findings`. Record it with
`model_eval.py judge`. Deterministic audits remain the factuality gate; a judge
cannot waive a failed audit.

Run the combined production gate after attaching the judge verdict:

```bash
python3 scripts/resume_quality_gate.py artifact.json \
  --case-path eval/model_bakeoff/cases/backend.txt \
  --knowledge-root knowledge --judge verdict.json --out quality.json
```

The full gate passes only when deterministic checks pass, semantic factuality
passes, the five judge ratings average at least 4.0, every dimension is at least
3, and confidence is at least 4. The report's `gate%` includes deterministic
hard failures immediately; audit passes remain pending until a judge verdict is
attached. An author process returning successfully is not itself a quality
pass. Judge latency, token count, and cost are retained in the JSON aggregate as
`mean_judge_latency_s`, `mean_judge_tokens`, and `mean_judge_cost_usd`.

Routing policy: among outputs that pass factuality, rendering, and comparable
ATS gates, prefer higher blinded human readability. LLM-judge readability is a
triage signal only; latency, tokens, and subscription cost proxies break ties
but never outweigh a meaningful human-readability advantage.

```bash
python3 scripts/model_eval.py report
python3 scripts/model_eval.py report --by-case
python3 scripts/model_eval.py report --json
```

The report keeps latency, exact input/output/cache balance, ATS median, human
readability, success rate, and interventions as separate columns. Cohorts keep
future prompt and implementation generations from being silently mixed into the
pilot baseline. Do not collapse the measurements into one score until tradeoffs
are discussed. A model is ineligible for a workflow stage if it
invents a fact, fails eligibility logic, produces an invalid PDF, exceeds one
page, or mutates `knowledge/`.

## Current routing decision

The `pilot-v1` human review preferred Claude's blinded resume on all three
cases, while ATS differences were not meaningful enough to override that
preference. For the current subscription-only workflow, use Claude Sonnet to
author final resume prose and Codex Luna as its independent adversarial judge.
Do not route ordinary resume work to GPT-6 Astra. Stage-v2 also showed that a
deterministic citation pass can still hide semantic overstatement, so failed
selection or bullet gates enter a bounded repair loop (maximum two repairs)
instead of advancing to rendering. No additional ATS calls are needed while
testing these stage-level controls.

The ML/data repair exercise confirmed the fail-closed path. Repair 1 passed
the deterministic audit but failed Luna's semantic review (3.6/5, factuality
false). Repair 2 then failed exact quote grounding, so the pipeline skipped a
paid judge call and blocked the artifact after the configured two repairs.
These are useful failures: neither artifact may flow into resume prose, and
both author and judge telemetry remain in the append-only ledger.

## Production adaptive review routing

The stage-v2 protocol deliberately uses reviews after individual stages to
measure models. Production `auto` mode is different: deterministic evidence
audits run at every stage, but the normal LLM semantic/readability review runs
once after the completed PDF verifies. This is the default balance between
independent adversarial scrutiny and subscription latency/token use.

Use `scripts/review_routing.py plan` after each deterministic audit. Before a
final resume it returns `review_required: false` unless it finds a documented
risk signal (skills-list evidence, multi-paragraph evidence, repeated JD
mapping, or a deterministic warning). At final review it returns the selected
reviewer. A deterministic hard failure returns `review_skipped: true`; never
launch a paid judge until it is repaired. `full-stage-review` retains the
benchmark behavior, while `final-review`, `deterministic-only`, and
`human-review` make the tradeoff explicit.

Capability detection is local and credential-safe: it observes the available
`claude`/`codex` executables or optional gitignored
`knowledge/review_routing.json` overrides. It never reads tokens or makes a
model request. Claude + ChatGPT routes Claude Sonnet author → Codex Luna
reviewer. With only one subscription, author and reviewer use a fresh,
isolated context from that provider; with neither, human review is mandatory.
This is a reviewer independence fallback, not a claim that same-provider
review is equal to cross-provider evaluation.

Append every plan with `review_routing.py record` to
`knowledge/review_routing_runs.jsonl`, including capability route, risk or
skip reason, observed reviewer latency/tokens/cost, repairs, and final gate.
The record is subscription-aware telemetry; it does not turn a skipped or
failed review into a successful quality outcome.

After the nine-run pilot, repeat the top two models per stage twice on two cases.
Adopt the fastest model whose success rate is at least 90%, has no factual hard
failures, and remains within five quality points of the best model. Use a
different model for final review than the one that authored the resume.

## Phase 4 measurement protocol

Phase 4 of the efficiency-improvement effort compares a proposed change
against the current behavior (an "arm") on latency, tokens, and cost. The
Phase 3 baseline's own numbers turned out untrustworthy on inspection — its
r2 runs reused r1's already-validated draft content instead of generating
fresh content, so they measured warm-cache/no-repair behavior, not
independent samples, and within-case spread already reached ~28% at n=1-2.
That's not enough signal to apply a ≥10% keep/reject rule to. Every Phase 4
before/after comparison follows this protocol instead:

- **At least 3 cold runs per case per arm.** "Cold" means no reuse of a
  prior run's validated output — each run regenerates its own JD extraction,
  selection, and bullet content from scratch. A run that short-circuits
  because it found already-validated content from an earlier attempt does
  not count as an independent sample.
- **Interleave arms**, not block them (arm A run 1, arm B run 1, arm A run 2,
  ...). Running all of one arm's samples first and then all of the other's
  confounds the comparison with whatever else changed over that time window
  (model updates, system load, knowledge/ edits).
- **Same commit apart from the change under test.** Don't compare a
  before-run on an old commit against an after-run that also picked up
  unrelated fixes landed in between.
- **No content reuse across runs being compared.** Use `scripts/model_eval.py
  start`/`finish` (or the equivalent for a non-bakeoff run) so each run's
  artifacts and telemetry are independently recorded, and don't hand a later
  run the previous run's draft as a starting point.
- **Exclude, don't delete, an invalid run.** If a run turns out to be
  unusable (crashed, wrong fixture, manual interruption), record it with
  `scripts/model_eval.py exclude --run-id ... --reason ...` (bakeoff runs)
  or a `run_exclusion` event via `scripts/log_metric.py run_exclusion
  '{"run_id": "...", "reason": "..."}'` (production runs.jsonl runs) instead
  of deleting the record — the raw event stays in the append-only log for
  audit, and `model_eval.py aggregate()` / `build_run_metrics.py` /
  `metrics_summary.py --perf`/`--ab` / `build_metrics_dashboard.py` all skip
  it the same way.
- **Status-aware aggregation.** By default `model_eval.py report` computes
  `p50_latency_s`, `mean_cost_usd`, and the exact-token aggregates
  (`mean_exact_*`, `mean_cache_hit_rate`, `token_coverage`) from
  `status: success` runs only — a failed or partial run's latency and
  tokens are often early-abort artifacts, not a measurement of the thing
  being compared. `runs` and `success_rate` still count every run, and ATS,
  human, judge, and audit aggregates are unfiltered. Pass
  `--include-failed` when failure behavior itself is what's being measured.
- **Run ids for exclusion.** A `runs.jsonl` run id is
  `<event>-<timestamp>`, plus `-<run_scope>` for records whose
  `finalize_resume.py` call passed `--timer-scope` (the per-run `$RUN_ID`).
  Timestamps are one-second precision, so older same-second records without
  a `run_scope` are disambiguated as `#2`, `#3`, ... in `metrics.jsonl`
  order — copy the id from `runs.jsonl` rather than constructing it.
- **Report n, not just the point estimate.** State how many cold runs fed
  each number in this document and in any Phase 4 write-up; a ≥10%
  keep-rule decision on n=1 is not a decision, it's a coin flip.
