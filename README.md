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
          audit/pipeline.py
STAGE 2   unit: the episode     "when did it start, why, what could it have
          a window -> a LIST of drift episodes    done instead, was it fixed?"
```

![The pipeline](eval/pipeline.png)

**📄 The design, in full, is `eval/docs/PIPELINE.md`** — both stages, the
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

Python 3.9+, standard library only. `orjson` is used automatically if present.

```bash
export VILLAGE_DATA=~/Documents/ai-village          # the dataset dump

# all agent-days in a range -> samples/<N>-agent-days-<start>..<end>/
python3 -m drift.cli --start 2026-08-26 --end 2026-08-28

# inspect one agent's block instead of writing
python3 -m drift.cli --start 2026-08-26 --end 2026-08-28 --preview "Claude Haiku 4.5"
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
drift/config.py     tunable constants; each carries the measurement behind it
drift/load.py       streaming loaders + provider-shape message splitting
drift/features.py   the feature computations
drift/build.py      agent-major precompute -> day-major emission
drift/render.py     record -> the text block the judge reads
scripts/            pull_metrics.py (DB-only metric series)
samples/            synthetic example; real runs land here and are gitignored
tests/              unit + structural tests
```

## Output

One indented JSON file per village-day, holding an array of that day's agent-day
records, plus a manifest recording the feature version and every constant used.

```
samples/82-agent-days-2026-08-26..2026-08-28/
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

`samples/example_block.txt` shows the rendered form. It is **synthetic** —
fabricated values through the real render path — because real records carry
verbatim agent memory and chat from a gated dataset. Regenerate with
`python3 samples/make_sample.py`.

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

Reads 53% of scored days, catches 25 of 25. Implemented in
`audit/pipeline.py`, which also builds the windows Stage 2 reads.

After the corpus-wide Stage 1 run, validate its exact expected row set before
paying for Stage 2:

```bash
python3 audit/pipeline.py validate --tag B-full --rows full
```

The command exits nonzero until every expected active day has a successful
Arm-B result, a list-form `day_activity`, a current deterministic block and raw
evidence. It also prints per-window missing descriptor, block and evidence
dates, plus warnings when the expected row set itself contains too little
contiguous history for a backward walk.

Stage 2 cache entries are tied to an input-coverage fingerprint, so completing
that backfill automatically reruns affected windows. Pass `--rerun` to
`audit/stage2.py` to force a fresh judgement even when the fingerprint matches.

Current run `B-peerfix`, 93 scorable rows of 100:

```
  AUC 0.95   precision 0.86   recall 0.76   F1 0.81   accuracy 0.90
```

**→ `eval/docs/PIPELINE.md` has the rest**: why a threshold beats a fixed
percentile (the era split), why 0.74 rather than the tightest cut that works,
the out-of-sample check against the golden set, and the caveats on every
number above.

## Design decisions

Constants are not preferences — each was measured, and several replaced an
approach that failed. Reasoning is recorded inline in `drift/config.py` and in
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
directly with no `drift/` imports and recomputes nine fields per agent-day. On
2026-08-26..28 all nine match on every record.

## Data handling

The source dataset is gated ("use for research and analysis… do not attempt to
re-identify"). `samples/*/` and `data/` are gitignored: records carry verbatim
agent memory, session goals and chat. Only the synthetic fixture is committed.

Database credentials are read from `DATABASE_URI` and must never be committed.

## Status

**Stage 1 — implemented and measured.** Current run is `B-peerfix`, 93
scorable rows of 100, hybrid arm B:

```
  AUC 0.95     precision 0.86   recall 0.76   F1 0.81   accuracy 0.90
```

Read AUC, not recall: see the variance note above. The arm bake-off (A/B/C/D)
is finished and closed — hybrid B won and is the design.

**Stage 2 — implemented, current baseline measured.** It reads a contiguous
token-bounded window and returns a LIST of drift episodes, because 14 of 20
measured windows contain more than one activity. Three passes, the last two
conditional: explain; a backward walk when an episode predates the readable
window; then revision against the expanded evidence. Prompts are
`audit/stage2.md`, `audit/walk.md`, and `audit/stage2_revision.md`.

**The handoff exists** — `audit/pipeline.py` applies the selection rule and
builds windows. Until 2026-10-01 nothing joined the two stages.

**Current measurement, 2026-10-02.** With Stage-1 routing pinned to
`B-peerfix` and the frozen 490-day descriptor snapshot (`c59beb90f609…`), 17
of 20 golden cases route to Stage 2. Seven produced usable final answers and
ten were explicitly incomplete. Conditional on an answer, TP 5 / FP 0 /
FN 0 / TN 2 gives precision, recall, accuracy and F1 of 1.000. Operationally,
coverage is only 0.412; counting unresolved cases as failures gives
accepted-call precision 1.000, recall 0.500, accuracy 0.412 and F1 0.667. Five
unresolved cases are positive and five negative. Nine requested a walk, seven
made a revision call, two revisions were skipped, and the run cost $17.60.

The accepted-answer metrics improved over the 2026-10-01 baseline, but the
pipeline did not improve operationally: stricter boundary checks increased
incomplete windows from two to ten. The prior TP 5 / FP 3 / FN 3 / TN 4 on
15 usable cases is historical rather than directly comparable.

The evaluator now matches positive predictions to human-authored activity
identity anchors rather than crediting any episode in the same window.
Negative labels are exhaustive window audits. Re-scoring the saved run under
that episode-level contract left the TP/FP/FN/TN counts unchanged.

A three-case probe after restoring the explicit metric-substitution rule fixed
two prior false negatives (Claude Sonnet 4.5 and DeepSeek V4 Pro). GPT-5.5
remained negative with only about one of 68 days visible, isolating the next
problem as negative explanations being unable to request a backward walk.
This probe cost $2.49; the full baseline above has not yet been rerun.

Stage 2 also requires each episode to distinguish a supported onset from a
date inferred across an evidence gap. Unsupported or omitted onset support is
recorded by episode index and makes the existing window status incomplete; no
additional `partial` status has been introduced.
The motivating GPT-5.2-agent probe now records its onset as unsupported across
the unsupplied 2026-07-24..2026-08-12 gap and correctly finishes incomplete
instead of as a final false positive. The validation call cost $1.06.

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
- `eval/raw/` holds only the 101 sampled days, so Stage 2's unsampled
  reasoning pull needs raw fetched for flagged days and the day before each.
- `eval/episodes.py` and `eval/walk_probe.py` are **deleted** (2026-10-01).
  Both implemented the mechanical similarity walk — Jaccard over whole-day
  word bags — which scored AUC 0.158, i.e. inverted. `audit/pipeline.py`
  builds windows now and the walk is a model call. Git has both files; the
  finding they produced is in `eval/docs/PIPELINE.md`.
