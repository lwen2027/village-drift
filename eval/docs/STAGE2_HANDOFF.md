# Stage 2 handoff

Last updated: 2026-10-02

## Current state

Stage 2 runs explain, a conditional backward walk, and revision against the
walk's reach. It now records evidence coverage, fingerprints cached inputs,
supports episode-level incompleteness, checkpoints evaluation rows, and locks
evaluation outputs against concurrent writers.

The latest baseline is:

- predictions: `eval/tables/stage2/stage2_eval_2026-10-02.jsonl`
- summary: `eval/tables/stage2/stage2_eval_2026-10-02.summary.json`
- hand score: `eval/tables/stage2/stage2_eval_2026-10-02_hand_score.md`
- routed cases: 17
- accepted cases: 7 (coverage 41.2%)
- accepted confusion matrix: TP 5, FP 0, FN 0, TN 2
- coverage-adjusted drift recall: 50%
- coverage-adjusted accuracy: 41.2%
- accepted-only precision: 100%, but this is selective precision and must not
  be presented without coverage
- recorded logical baseline cost: $17.5998

The hand score found:

- accepted true-positive explanation quality: 31/40 (77.5%)
- emitted gold-positive explanation quality: 37/56 (66.1%)
- coverage-adjusted explanation quality: 37/80 (46.3%)
- all five accepted true-positive causal accounts were materially correct
- exact activity-start day was correct in 2/5 accepted explanations
- exact onset day was correct in 4/5 accepted explanations
- mechanism shape was correct in only 1/5 accepted explanations
- four of seven gold-negative windows produced plausible but false incomplete
  narratives; the completeness gate correctly quarantined them

Ten cases were incomplete: two refusals, two hard-truncated revision packets,
three activity boundaries outside expanded evidence, and three unsupported
onsets. Some cases have overlapping secondary reasons.

## Next implementation

Build a bounded incomplete-outcome resolver before making further prompt
changes. Dispatch on persisted, typed incompleteness reasons:

1. `refusal`: retry the same evidence packet with the configured fallback
   model, with a strict retry cap.
2. `hard_truncated`: split the requested history into bounded chronological
   chunks, summarize each with the existing deterministic compression path,
   then revise from the combined reach metadata.
3. unsupported `activity_start` or `onset`: continue the backward descriptor
   walk from its saved cursor until the boundary is supported, the activity is
   shown to predate available history, or the walk reaches a configured cap.
4. exhausted evidence: retain a structured unresolved result and send it to a
   human-review queue. Do not promote its hypothesis to a confirmed finding.

The resolver should be a small state machine over reason codes, not an
unbounded recursive model loop. Persist attempt count, evidence ranges already
examined, next cursor, stop reason, and cost. Reuse the current evidence
fingerprint so newly populated descriptors or blocks invalidate unresolved
results.

After implementing it, rerun the same frozen 17-case evaluation and compare:

- resolved incomplete cases by reason
- accepted coverage and coverage-adjusted recall
- false narratives that cross the completeness gate
- incremental calls, tokens, and dollars per recovered true positive
- the same hand-score dimensions, especially activity start, onset, and shape

Only after that measurement should Stage 2 prompts be changed. The next prompt
targets are temporal-boundary reconstruction, explicit mechanism-shape
classification, and stronger negative adjudication.

## Guardrails

- Incomplete hypotheses remain useful debugging state, not audit findings.
- Report accepted-only and coverage-adjusted metrics together.
- Keep evaluation routing, labels, and descriptor snapshot frozen for the next
  comparison.
- The API key is loaded from `.env`; never print or commit it.
- The last verified test run had 112 passing tests.
