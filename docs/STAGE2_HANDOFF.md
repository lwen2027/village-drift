# Stage 2 handoff

Last updated: 2026-10-02

## Current state

Stage 2 implements explain, a conditional bounded backward walk, and revision
against expanded evidence. It records evidence coverage, fingerprints cached
inputs, supports episode-level incompleteness, checkpoints evaluation rows,
and locks evaluation outputs against concurrent writers.

There is currently **no valid post-overhaul baseline**. The extraction,
Stage 1 judge, Stage 2 explain, walk, and revision contracts changed after the
last run. The former results and their hand score are preserved under
`artifacts/archive/2026-10-02-pre-prompt-overhaul/`; they are historical diagnostics,
not measurements of the current pipeline.

## Next measurement

1. Regenerate Stage 1 extraction outputs and day descriptors with the current
   prompts.
2. Rerun the frozen Stage 1 golden evaluation and report its ranking and
   threshold metrics.
3. Rebuild the Stage 2 descriptor index and verify coverage readiness.
4. Rerun the frozen end-to-end Stage 2 evaluation, including explain, any
   requested walk, and revision.
5. Report classification, coverage, boundary accuracy, mechanism accuracy,
   evidence validity, explanation hand scores, calls, tokens and cost.

Keep the golden labels, episode identity anchors, sampled rowsets and window
definitions frozen so changes measure the new protocol rather than a new test
set.

## Guardrails

- Incomplete hypotheses remain debugging state, not audit findings.
- Report accepted-only and coverage-adjusted metrics together.
- Do not compare a post-overhaul score directly with an archived score without
  naming the protocol change.
- The API key is loaded from `.env`; never print or commit it.
