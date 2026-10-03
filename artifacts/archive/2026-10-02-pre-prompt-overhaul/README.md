# Pre-prompt-overhaul experiment archive

Archived on 2026-10-02 after coordinated prompt and schema changes across
Stage 1 extraction/judging and Stage 2 explain/walk/revision.

Nothing in this directory is a current pipeline measurement. In particular,
the recorded precision, recall, accuracy, explanation scores, descriptor
coverage and mechanism distribution must not be reported as results for the
current protocol. They remain useful only as historical diagnostics.

## Contents

- `stage1/model_runs/`: prompt-dependent Stage 1 responses (local and
  gitignored because they contain gated data)
- `stage1/arena_results.*` and `stage1/ranking_final.*`: charts derived from
  those responses
- `stage2/artifacts/`: old descriptors, explanations, walk results, baseline
  responses, summaries and hand scores (local and gitignored)
- `stage2/STAGE2_HANDOFF.md`: the handoff note describing the superseded
  2026-10-02 Stage 2 baseline

## Deliberately not archived

Golden labels, episode identity anchors, sampled rowsets, deterministic Stage
1 blocks, raw source data, rendered digests and mechanically constructed
windows remain in their normal locations. They are reusable inputs rather
than model findings and should seed the next evaluation.

The active pipeline currently has no post-overhaul baseline. A result becomes
current only after all required Stage 1 descriptors are regenerated and the
Stage 1 and end-to-end Stage 2 evaluations are rerun with the present prompts.
