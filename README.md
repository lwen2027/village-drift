# village-drift

Tooling for a one-time audit of **goal drift** in the [AI Village](https://theaidigest.org/village)
— 42 long-running LLM agents, 15 months, ~4,100 agent-days of computer use, chat
and memory.

## What this is for

The village already runs an LLM monitor that flags `off-goal` days. Hand-reading
24 of its findings showed the monitor is good at *"this day is worth looking at"*
and unreliable at *"here is what was wrong with it"* — it has no proportionality
test, no memory access, and a single-day window, so it misidentifies which
behaviour diverged and misses multi-day states entirely.

This project runs a **separate, one-shot audit** with a different goal: not to
re-detect known categories, but to **discover the mechanisms** by which agents end
up working on something other than what they were assigned.

Two design choices follow from that, and everything else follows from them.

**Discovery, not classification.** The detector does not pick from a fixed label
set. It emits free text describing what the agent did and what appears to have
caused it; the taxonomy is derived afterwards by clustering those descriptions.
Handing a model seven labels guarantees it finds seven things.

**Mechanical where possible, judged where necessary.** Facts that can be counted
or diffed are computed deterministically in code and injected into the prompt;
only genuinely semantic questions go to the model — and the model is never asked
to count, diff or divide.

## Pipeline

```
STAGE 1   unit: the agent-day   "was this day spent on the assigned goal?"
          every day -> is_drift + confidence, i.e. a RANKED LIST
HANDOFF   the selection rule (conf<0.74), then window construction
          src/village_drift/handoff/pipeline.py
STAGE 2   unit: the episode     "when did it start, why, what could it have
          a window -> a LIST of drift episodes    done instead, was it fixed?"
```

![The pipeline](docs/assets/pipeline.png)

**The design, in full, is `docs/PIPELINE.md`** — both stages, the
handoff, the measurements behind each choice, and what was tried and
rejected. That document is authoritative; this README is an entry point.

A window is not an episode. It is the evidence handed to Stage 2, deliberately
wider than any single activity, and 14 of 20 measured windows contain more
than one — which is why Stage 2 returns a list rather than a verdict.

Stage 1 deliberately excludes agent reasoning. Not for cost — for bias. Reasoning
availability ranges 28–98% by agent, so a reasoning-fed detector would flag agents
in proportion to how much reasoning their provider records. Keeping *detection* on
channels that exist uniformly (actions, timestamps, memory, chat) means Stage 2
inherits an unbiased candidate set.

## Install and run

Python 3.10+, standard library only. `orjson` is used automatically if present.

```bash
python3 -m pip install -e .
export VILLAGE_DATA=~/Documents/ai-village          # the dataset dump

# all agent-days in a range -> artifacts/current/stage1/<run>/
python3 -m village_drift.stage1.cli --start 2026-08-26 --end 2026-08-28

# inspect one agent's block instead of writing
python3 -m village_drift.stage1.cli --start 2026-08-26 --end 2026-08-28 --preview "Claude Haiku 4.5"
```

A 3-day range takes ~45s; the cost is streaming two ~2GB gzipped files.

**Optional — metric series.** `metric_datapoints` is DB-only (excluded from the
public dump). Without it the metric fields emit `null(absent)` and everything else
still runs.

```bash
export DATABASE_URI='postgresql://…'                # never commit this
python3 scripts/pull_metrics.py --since 2026-07-01 --out data/metrics.json
```

## Layout

```
src/village_drift/
  shared/           ingestion, compression, evidence and rendering
  stage1/           agent-day construction, prompts and model runner
  handoff/          readiness, selection and window construction
  stage2/           explain, backward walk, revision and validation
evaluation/
  goldens/          frozen labels and golden-set tooling
  evidence/         rendered/raw evaluation inputs (gitignored)
  stage1/ stage2/   scorers and evaluation runners
artifacts/
  current/          generated blocks and model outputs (gitignored)
  archive/          explicitly superseded experiments
docs/               active protocols, design, research and history
examples/           committed synthetic record and rendered block
tests/              unit, structural and pipeline tests
```

## Output

One indented JSON file per village-day, holding an array of that day's agent-day
records, plus a manifest recording the feature version and every constant used.

```
artifacts/current/stage1/82-agent-days-2026-08-26..2026-08-28/
    2026-08-26.json
    manifest.json
```

Each record has two parts. **Facts** are computed statistics, each marked for how
far to trust it. **Context** is raw material the judge reads and interprets itself.

```
GOAL              assigned goal; how much of its wording survives in memory
MEMORY            snapshot count; persistence and first-seen date of named rules
ASSIGNED-METRIC   the goal's metric, its source, and whether it is moving
ACTIVITY          turns vs this agent's own norm; bash/gui/pause mix; span
ARTIFACTS         what the day's work was pointed at; new vs revisited
REPETITION        how much of the day was one action, or one plan, repeated
INTERACTION       chat volume; which peers the day was organised around
-----
CONTEXT           prior memory outline · prior 14 active days of session goals
```

`examples/example_block.txt` shows the rendered form. It is **synthetic** —
fabricated values through the real render path — because real records carry
verbatim agent memory and chat from a gated dataset. Regenerate with
`python3 examples/make_sample.py`.

### Reading a record

**`[heuristic]`** marks any value depending on a regex, threshold or segmentation
choice. Plain counts cannot be wrong; these can, and the judge is told to verify
them when they are load-bearing.

**`null` is never zero.** Three kinds, each routing differently:

| | meaning |
|---|---|
| `null(absent)` | no data exists — do not hunt, and do not read as flat |
| `null(extract_failed)` | data exists, the rule missed it — go read the logs |
| `null(edge)` | series boundary — ignore |

The distinction matters most for metrics. A goal whose metric is self-reported has
no instrument at all, so a flat value is *no signal* rather than evidence of
stagnation — the block says so instead of emitting a number.

**Field names are deliberately neutral** (`clauses_added`, not
`prohibition_count`), and there is no score, severity or `signals_fired` row. In a
discovery sweep a loaded name presumes the answer, and a summary verdict turns the
judge into a checklist.

## What Stage 1 emits, and what reaches Stage 2

**Stage 1 is not a verdict.** It scores each agent-day and emits `is_drift`
with a `confidence`, and the binary label is the weaker half of that output.
Rerun the same rows and ~6 of 93 verdicts flip, all at confidence 0.45-0.60,
while AUC holds to two decimals. **Read AUC, not recall.**

**The selection rule.** A day goes to Stage 2 if:

```
verdict is drift   OR   verdict is not-drift AND confidence < 0.74
```

In the archived pre-overhaul run this read 53% of scored days and caught 25 of
25; that coverage claim must be remeasured under the current prompts.
Implemented in `src/village_drift/handoff/pipeline.py`, which also builds the
windows Stage 2 reads.

After the corpus-wide Stage 1 run, validate its exact expected row set before
paying for Stage 2:

```bash
python3 -m village_drift.handoff.pipeline validate --tag B-full --rows full
```

The command exits nonzero until every expected active day has a normally
completed Arm-B call, a valid drift verdict and confidence, a list-form
`day_activity`, a current deterministic block and raw evidence. It also prints
per-window missing descriptor, block and evidence
dates, plus warnings when the expected row set itself contains too little
contiguous history for a backward walk.

Stage 2 cache entries are tied to an input-coverage fingerprint, so completing
that backfill automatically reruns affected windows. Run
`python3 -m village_drift.stage2.run --all --descriptor-tags B-full --rerun`
to force a fresh judgement even when the fingerprint matches. Stage 2 and evaluation
commands require exact routing and descriptor tags; archived experiments are
never selected implicitly.

Archived pre-overhaul run `B-peerfix`, 93 scorable rows of 100:

```
  AUC 0.95   precision 0.86   recall 0.76   F1 0.81   accuracy 0.90
```

These are historical diagnostics, not current performance numbers. **→
`docs/PIPELINE.md` has the rest**: why a threshold beats a fixed
percentile (the era split), why 0.74 rather than the tightest cut that works,
the out-of-sample check against the golden set, and the caveats on every
number above.

## Design decisions

Constants are not preferences — each was measured, and several replaced an
approach that failed. Reasoning is recorded inline in `src/village_drift/shared/config.py` and in
full in `ai-village-stage1-feature-spec.md`. In brief:

- **Precompute agent-major, emit day-major.** Rolling windows need each agent's
  whole series; the Stage-2 sweep must run day-major so the shared village
  transcript stays in prompt cache.
- **Turns belong to the day they happened**, not the day their session opened —
  sessions cross midnight.
- **Windows count active days**, never calendar days. Agents skip weekends and go
  dormant.
- **Baselines compare like for like.** "Unusual for this agent" is measured
  against that agent's own prior 14 active days, in the same units.
- **Memory comparison is semantic, not mechanical.** Clause-level diffing and
  goal-line extraction were both measured and cut: agents rewrite memory wholesale
  and share no goal-line format. What survives is persistence of *named* rules,
  plus showing the judge the prior snapshot.

## Testing

```bash
python3 tests/run.py               # no dependencies; 16 tests
python3 -m pytest tests/ -q        # same tests, if pytest is installed
```

Do **not** run a test module directly (`python3 tests/test_structure.py`) — that
only defines the functions and exits 0 in silence, which reads as success.

Unit tests cover the parts that have silently broken: provider-shape message
splitting, word-boundary name matching, bash capping, null semantics. Structural
tests use synthetic fixtures for date-range emission, lookback, cross-midnight day
assignment, cross-agent isolation, goal fallback, baseline units, and that the
byte-prefilter cannot change results.

Output is also validated against an independent parser that reads the dump
directly with no `village_drift` imports and recomputes nine fields per
agent-day. On
2026-08-26..28 all nine match on every record.

## Data handling

The source dataset is gated ("use for research and analysis… do not attempt to
re-identify"). `artifacts/current/`, `evaluation/goldens/`,
`evaluation/evidence/`, and `data/` are gitignored: records carry verbatim
agent memory, session goals and chat. Only the synthetic fixture is committed.

Database credentials are read from `DATABASE_URI` and must never be committed.

## Status

**The current protocol is implemented but not yet remeasured.** On 2026-10-02,
prompt and schema changes affected Stage 1 extraction and judging plus Stage 2
explain, walk and revision. All earlier model outputs, score tables and charts
are therefore historical. They are preserved in
`artifacts/archive/2026-10-02-pre-prompt-overhaul/`, not treated as a current
baseline. Golden labels and deterministic inputs remain active and unchanged.

**Stage 1 — implemented, post-overhaul baseline pending.** The historical arm
bake-off still supports the hybrid-B architecture, but its exact AUC,
precision, recall, F1 and accuracy do not measure the present prompts.

**Stage 2 — implemented, post-overhaul baseline pending.** It reads a contiguous
token-bounded window and returns a LIST of drift episodes, because 14 of 20
measured windows contain more than one activity. Three passes, the last two
conditional: explain; a backward walk when an episode predates the readable
window; then revision against the expanded evidence. Prompts are
`src/village_drift/stage2/prompts/explain.md`,
`src/village_drift/stage2/prompts/walk.md`, and
`src/village_drift/stage2/prompts/revise.md`.

**The handoff exists** — `src/village_drift/handoff/pipeline.py` applies the
selection rule and builds windows. Until 2026-10-01 nothing joined the two
stages.

The next valid measurement must regenerate Stage 1 extraction outputs and
descriptors, rebuild the Stage 2 descriptor index, and rerun both frozen golden
evaluations. See `docs/STAGE2_HANDOFF.md` for the measurement checklist.

**Not yet done, in rough priority order:**

- Stabilize the uncertain-negative expansion trigger. A structured
  `history_request` now reuses the one bounded walk and revision, anchors the
  walk on the named activity, and samples interior evidence days without
  recursion. In repeated GPT-5.5-agent probes, however, the Claude Opus 5.5
  judge requested expansion in only one of three runs. An unconditional sparse
  fallback would also expand three true negatives in this golden set.
- **The backward walk is useful but not solved.** On ten drift episodes it was
  exact on 5, within three days on 6, with median absolute error 2 days and
  maximum error 21 days. It cost $0.14. The mechanical predecessor scored AUC
  0.158, which is *inverted*, not weak.
- `evaluation/evidence/raw/` holds only the 101 sampled days, so Stage 2's unsampled
  reasoning pull needs raw fetched for flagged days and the day before each.
- `eval/episodes.py` and `eval/walk_probe.py` are **deleted** (2026-10-01).
  Both implemented the mechanical similarity walk — Jaccard over whole-day
  word bags — which scored AUC 0.158, i.e. inverted. `src/village_drift/handoff/pipeline.py`
  builds windows now and the walk is a model call. Git has both files; the
  finding they produced is in `docs/PIPELINE.md`.
