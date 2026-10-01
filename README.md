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
STAGE 1  (built)         all agent-days · mechanical features + capped logs
                         -> a ranked list, and a rule for what reaches Stage 2
STAGE 2  (written, never run)
                         episodes only · the days Stage 1 flagged, plus the
                         span around them  -> when it began, and why
```

Stage 1's spec is settled (see "What Stage 1 emits" below). Stage 2 exists as
`audit/stage2.py` + `audit/stage2.md` and stubs cleanly, but no real run has
happened and nothing about it is validated. Its golden set is being built
under `eval/docs/EPISODE_PROTOCOL.md`.

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
Run the same 93 labelled rows twice under an identical prompt and **6 of the
verdicts flip** — every one of them at confidence 0.45–0.60 — while AUC is
identical to two decimals. The ordering reproduces; the labels do not. Any
recall figure quoted from a single run carries roughly ±8 points.

**The selection rule.** A day goes to Stage 2 if:

```
verdict is drift   OR   verdict is not-drift AND confidence < 0.75
```

Measured on both runs of the final rubric (93 rows, 25 drift): this reads
~54% of agent-days and catches **25 of 25**. The lowest cutoff that achieved
full recall in *both* runs was 0.74; 0.75 is the same cut with margin, since
confidence clusters on round values and nothing sits between them.

**Why a threshold rather than "read the top N%".** A ranked list cut at a
fixed percentile is 1–2 points cheaper on this sample and is strictly better
ordered — the rule promotes every drift verdict above its merit, so a
drift-at-0.55 is read before a not-drift-at-0.45 even though both carry
P(drift) 0.55. The rule wins anyway, on robustness:

```
                      RULE conf<0.74        RANKING top 46%
                    read      found       read      found
  era before 07-09   30%       6/6         46%       6/6
  era 07-09 onward   65%      19/19        46%      18/19
```

Split the sample by era and the judge is markedly less confident on the later
one (median not-drift confidence 0.85 -> 0.72). **The rule absorbed that and
kept full recall by reading more; the percentile held its budget and dropped
a drift day.** A fixed percentile is a bet that the corpus resembles the
sample. A threshold makes no such bet — it applies a constant evidentiary bar
and lets the corpus decide the volume.

Its failure mode is also the cheaper one. Over-reading costs Stage 2 money;
missing an episode costs the episode, silently. And the rule is *streaming* —
it decides each day as Stage 1 emits it, with no global sort and no
recomputation as the corpus grows.

**Monitor the realised read share.** It is the diagnostic the percentile
would have hidden: if it jumps, something changed about the agents or the
judge, and that is worth knowing.

**Caveats.** 0.74 was fitted to these 93 rows and two runs; the held-back 60
is spent, so every cut discussed here is in-sample. The era split is 30/63
with 6 drift days in the smaller half — the direction is clear, the magnitude
is not. And averaging two runs per day lifts AUC 0.94 -> 0.95, but that means
scoring every day twice and doubling Stage 1 cost; the figures in
`eval/ranking_final.png` assume it.

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

Stage 1 is implemented and validated. Not yet built: Stage 2 input assembly
(`features.cap_bash` exists but the day's turn text is not rendered yet), and the
judge prompt itself.
