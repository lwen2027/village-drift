# village-drift — Stage 1

Mechanical feature extraction over the AI Village dataset, for goal-drift analysis.

Produces one record per **agent-day**: deterministic statistics plus raw context,
which a Stage-2 LLM judge reads alongside the day's transcript. Stage 1 answers
*"did this day diverge, and by how much"*. It deliberately does not attempt *"why"* —
see **Design notes** below.

## Quick start

```bash
export VILLAGE_DATA=~/Documents/ai-village     # the dataset dump
python3 -m drift.cli --start 2026-08-26 --end 2026-08-28 --out out/v1
python3 -m drift.cli --start 2026-08-26 --end 2026-08-28 --preview "Claude Haiku 4.5"
```

Stdlib only. `orjson` is used automatically if installed (3–5× faster parsing).
A 3-day range takes ~45s; the dominant cost is streaming two ~2GB gzipped files.

```
out/v1/2026-08-26.jsonl     one record per agent active that day
out/v1/manifest.json        feature_version + the constants used
```

## Example output

`samples/example_block.txt` and `samples/example_record.json` are **synthetic** —
fabricated values pushed through the real `Block`/`render` path, so the shape is
exact. Regenerate with `python3 samples/make_sample.py`.

Real output is **not** committed. `out/` and `data/` are gitignored: the source
dataset is gated ("use for research and analysis… do not attempt to re-identify"),
and a record's `context` section carries verbatim agent memory, session goals and
chat.

## Layout

```
drift/config.py     every tunable constant, each with the measurement behind it
drift/load.py       streaming loaders; provider-shape message splitting
drift/features.py   the computations
drift/build.py      agent-major precompute -> day-major emission
drift/render.py     record -> the text block the judge sees
tests/              unit tests for the parts that have silently broken before
```

## Design notes

These are not preferences. Each was measured; several replaced an approach that
failed. Full detail in `ai-village-stage1-feature-spec.md`.

**Precompute agent-major, emit day-major.** Rolling windows need each agent's whole
series, but the LLM sweep must run day-major — the ~100k shared village transcript
caches across the agents *within* a day, and agent-major ordering expires that cache
and pays ~23× on the shared block.

**Null is not zero.** Three kinds, routing the judge differently: `absent` (no data
exists — don't hunt, and don't read as flat), `extract_failed` (data exists, the rule
missed it — go read the logs), `edge` (series boundary — ignore).

**Heuristic fields are marked.** Anything depending on a regex, threshold or
segmentation choice carries `heuristic: true`. Counts of rows cannot be wrong;
these can, and the judge is told to verify them when load-bearing.

**Neutral naming.** `clauses_added`, not `prohibition_count`. A loaded field name
smuggles a hypothesis into an exploratory sweep, and labels here are retrofitted
after clustering, not assumed up front. There is deliberately **no** verdict, score
or `signals_fired` row — the moment one exists the judge starts checking boxes
instead of reading the day.

**Windows use ACTIVE days**, never calendar days. Agents skip weekends and go
dormant; one agent has 38 active days across a 7-week goal.

### Two approaches that were measured and cut

**Clause-level memory diffing.** Verbatim clause survival across a day ranges 2–68%
by agent — these agents *rewrite* memory wholesale rather than editing it.
Normalisation doesn't help (98%→99% for the worst agent) and fuzzy matching is
infeasible (killed at 15 minutes on 5 agents). Replaced by `watchlist_persistence`
over *named* items, which survive 36–97%.

**Goal-line extraction from memory.** Five patterns scored 0–98% (median ~25%) across
42 agents; the four highest-volume agents all sit at 18–29%. There is no recoverable
format. Replaced by `assigned_goal_words_present` — bag-of-words containment, needing
no extraction — with goal *restatement* moved to the judge.

### Performance traps hit while building this

Recorded because each cost a debugging cycle and each is easy to reintroduce.

1. **`SequenceMatcher` is O(n²) in string length.** Bash commands here reach 34KB;
   comparing a few dozen hangs outright. Similarity is token-set Jaccard.
2. **Prefix blocking splits real groups.** Agents prepend a varying comment to
   otherwise-identical commands, so blocking is on a sorted vocabulary signature.
3. **Blocking alone doesn't bound the worst case.** An agent whose commands all open
   the same way lands everything in one bucket; buckets above a limit are taken at
   face value.
4. **Date-prefilter before `json.loads`.** Parsing every row just to read a date is
   the entire cost of a narrow run.

## Metric fields (Signal 2)

`metric_datapoints` is **DB-only** — excluded from the public dump — so pull it first:

```bash
export DATABASE_URI='postgresql://…'          # never commit this
python3 scripts/pull_metrics.py --since 2026-07-01 --out data/metrics.json
```

1,410 daily rows / 27 keys as of 2026-09. Absent file ⇒ the metric fields emit
`null(absent)`; everything else still runs.

Three null conditions, all mandatory:

* **`source ∈ {self-report, manual}` ⇒ `null(absent)`.** A flat self-reported metric
  is *no signal*, not evidence — the agent is the instrument. Several goals were
  never instrumented at all (one wellbeing metric has 8 manual datapoints in its
  entire life).
* **source changes inside the 7-day window ⇒ `null(absent)`.** Several metrics switch
  self-report → real instrument on 2026-08-14; a straddling slope measures the
  instrument, not the agent.
* **fewer than 7 active days, or before 2026-07-06 ⇒ `null(edge)`.** The series does
  not exist earlier.

`agent_actions_touching_this_source` is the one genuinely hand-mapped field, keyed by
**source** (13) rather than goal (27) — fewer, and stable as goals change.

## Not implemented yet

- Bash capping is implemented (`features.cap_bash`) but the day's turn text is not
  yet rendered; Stage 2 input assembly is the next piece.
