# The drift pipeline

Authoritative description of how the two stages work and why. Supersedes
`STAGE2_DESIGN.md`, which is deleted — git has it if you want the history.

**This document holds what no single source file can see**: the architecture
as a whole, the measurements that forced each choice, and what was tried and
rejected. Mechanism-level rationale — why one line is the way it is — lives
in a docstring next to that line, and is deliberately *not* repeated here.
Where a fact appears in both places it will drift apart; that has already
happened in this repo more than once.

Everything below with a number attached is measured. Where something is an
argument rather than a measurement it says so.

![The pipeline](../pipeline.png)

*Figure: `eval/pipeline_chart.py` → `eval/pipeline.svg` → `eval/pipeline.png`.
Every number on it is pulled from a constants block at the top of that
script, each with a comment saying where it came from, so the figure cannot
quietly drift from the text.*

---

## What this is

42 LLM agents, 15 months, ~4,100 agent-days of computer use, chat and memory.
The question is **goal drift**: an agent stops working toward its assigned
goal and works on something else instead.

Two stages, because the two useful questions have different units:

```
  Stage 1   unit: the agent-day    "was this day spent on the assigned goal?"
  handoff   the selection rule, then window construction
  Stage 2   unit: the episode      "when did this start, why, what could it
                                    have done instead, was it corrected?"
```

## Three units, and conflating them has caused most of the bugs

| unit | what it is | who uses it |
|---|---|---|
| **agent-day** | one agent, one calendar day | Stage 1's input and output |
| **episode** | one continuous activity, which may span weeks | Stage 2's *output*, and the golden labels |
| **window** | a span of days handed to a reader as evidence | Stage 2's *input* |

**A window is not an episode.** It is wider on purpose — you cannot see where
an activity begins without seeing before it began. Measured on the golden
set, **14 of 20 windows contain more than one distinct activity**, and one
labeller wrote *"EPISODE B (should be its own row…)"* in the margin.

That is why Stage 2 returns a **list** of episodes rather than a verdict about
the window. A single `is_drift` per window cannot represent the third
mechanism shape at all — an activity that is on-goal and then off-goal with
nothing about the work changing, because the assignment moved underneath it.
One golden window carries three human labels, two not-drift and one drift,
covering the same chess activity throughout; a window-scoped verdict is wrong
against at least one of them whichever way it goes.

---

## Stage 1 — the agent-day

**Input** is the mechanical block (`drift/features.py` + `render.py`, pure
code) plus two fields a cheap model extracts by reading the raw day:
`reached_audience` and `peer_requests`. This is "arm B", the hybrid. The
A/B/C/D bake-off is **finished and closed**; hybrid B won and is the design.

> **SINGLE-ARM. Arm B only, everywhere** (LW, 2026-10-01). Arm A — the block
> alone, no cheap stage — must not generate anything downstream consumes.
> `audit/stage2.py` enforces it via `ARM_PREFIX` and the descriptor index
> skips arm A records outright.
>
> Mixing has caused two bugs. `_anchor` scored arm B's threads against arm
> A's `decisive_evidence` on 9 of 17 episodes, changing the anchor on 4 of
> 16. And the descriptor index silently mixed arms for 8 of 31 agents.
>
> The trap is that arm A is *always the available one when coverage is thin*
> — arm B's cheap stage needs `eval/raw/`, arm A needs only a block `prep`
> builds free. So the shortcut presents itself exactly on the sparse windows
> where a polluted index does the most damage.

**Output is a ranked list, not a verdict.** It emits `is_drift` with a
`confidence`, and the binary label is the weaker half. Run the same 93 rows
twice under an identical prompt and 6 verdicts flip — all at confidence
0.45–0.60 — while AUC holds to two decimals. The ordering reproduces; the
labels do not. Any recall figure from a single run carries roughly ±8 points.

> That repeat measurement was taken on `B-final_a` vs `B-final_b`, and
> `final_b` was destroyed by an unquoted shell glob on 2026-10-01. The finding
> stands as recorded but cannot be re-derived from the tree.

**Current run: `B-peerfix`** (100 rows, 93 scorable — 7 are open-goal, where
drift is undefined by construction).

```
  AUC 0.95   precision 0.86   recall 0.76   F1 0.81   accuracy 0.90
```

Read AUC, not recall. It supersedes `B-final_a` and must never be averaged
with it: the two read different text, because `monitor_view` had been
filtering every peer message out of the cheap stage's input. Fixing that took
`peer_requests` from a **7% fill rate to 72%** (25 captured requests → 477).

Prompt: `audit/rubric.md`. Runner: `audit/run.py`. Scorer: `eval/arena.py`.

---

## The handoff — `audit/pipeline.py`

This did not exist until 2026-10-01. Stage 1 wrote verdicts and Stage 2 read a
different file; the selection rule lived only as prose in the README and the
window builder lived only in a conversation.

### The selection rule

```
  a day goes to Stage 2 if:   verdict is drift
                         OR   verdict is not-drift AND confidence < 0.74
```

Reads **53%** of scored days and catches **25 of 25** drift days.

0.74 is chosen for margin, not for being the tightest cut that works. The
lowest-confidence drift day the judge got wrong sits at 0.62, and confidence
is quantised to ~19 values with five rows landing exactly on 0.62 — so a cut
just above that separates nothing and would miss any future drift day scored
0.65, 0.68, 0.70 or 0.72, all populated. The headroom costs ~8 points of
extra reading.

**Why a threshold and not "read the top N%".** A percentile is 1–2 points
cheaper on this sample, but splitting by era shows the judge is markedly less
confident on the later one (median not-drift confidence 0.85 → 0.74):

```
                      RULE conf<0.74        RANKING top 46%
                    read      found       read      found
  era before 07-09   37%       6/6         47%       6/6
  era 07-09 onward   60%      19/19        46%      18/19
```

The rule absorbed that by reading more; the percentile held its budget and
dropped a drift day. A percentile is a bet that the corpus resembles the
sample. The rule also has no global state — each day is decided on its own
verdict, so it streams over a growing corpus without re-deciding the past.

**Out-of-sample check.** On the 20-episode golden set the rule sends 17 and
drops **zero** drift episodes. Stage 1 called four of those ten drift
episodes not-drift at the day level; the confidence threshold caught all four.

### Window construction

A window is grown **contiguously** outward from the flagged days under a token
budget: backward first, then forward with the remainder.

Backward first because that is where the answer is — the question is when the
activity began, and every day spent forward is a day not spent reaching it.
Contiguous because the day an activity *starts* is in the middle of a span,
not at an edge: the previous approach trimmed a wide window by dropping from
the middle, and the true `activity_start` survived in only 6 of 17 episodes.

---

## Stage 2 — the episode

**Input** is a window. **Output** is a list of drift episodes, each with its
own activity, start, onset, mechanism, levers, correction and dissent — plus
an `examined` flag separating *"I read this and there is no drift"* from
*"I could not tell"*. An empty list with `examined: true` is a finding.

Prompt: `audit/stage2.md`. Implementation: `audit/stage2.py`.

### Two passes

```
  pass 1   explain a contiguous token-bounded window ending at the flagged days
  trigger  the judge sets `activity_predates_window` on an episode
  pass 2   the walk — a cheap descriptor index reaching ~45 days back for ~4¢
```

The walk used to run **first**, on the theory that a window could not be sized
until `activity_start` was known. Measured, that is backwards for most
episodes: a budget-filled backward window already contains the activity start
in 6 of 17, and a walk for those spends a call to learn what the window would
have shown. The walk earns its cost only where the activity genuinely predates
what one read can hold — which the judge now reports directly instead of being
guessed at in advance.

When pass 2 runs, the walk's answer is recorded **alongside** the judge's,
never over it. They are different measurements — the judge saw the days, the
walk saw 3–8 word descriptors — and a disagreement is a finding.

### What it reads

```
  ONE artifact per day, at two depths:
    derived layer     27 computed facts + 7 verbatim context sections
    + evidence layer  bash, chat, last memory snapshot, reasoning
  + unsampled reasoning   flagged days and the day before each, 25% of budget
  + tool errors           ~45 tok/day, with the days actually searched named
```

Stage 1 reads the derived layer alone; Stage 2 reads the same record with
evidence appended — `render(rec, raw, with_evidence=True)`.

Until 2026-10-01 these were two documents. Stage 2 sliced the computed head
off a rendered block with a string split and concatenated it to a whole
digest, which delivered GOAL, ACTIVITY and MEMORY **twice** in one payload,
in two renderings. Slicing a document to recombine it with another is what
you do when the two cannot compose; they compose now, and `block_stats()`
is deleted. A standalone digest is still produced for days with no block
record — `drift/evidence.py` serves both.

**It read Stage 1 *blocks* until 2026-10-01 and could not have worked.** The
block has no command text, no inbound chat and **no reasoning section at
all** — only derived statistics plus operator messages. All four Stage 2
questions require quoting primary evidence. Checked against the one case with
a known answer: the onset quote appears **0 times** in that day's block.

The block is not discarded. Its computed head carries the only cross-day
comparatives that exist — `turns_vs_own_median`, `hosts_new_today` vs
`hosts_seen_earlier`, repetition clustering, `prior_active_days` — which a
per-day digest structurally cannot hold. ~5% overhead.

Digests come from the dump via `goldenset/render_digest.py`, **not** from
Stage 1, and that independence is load-bearing: `window_days` used to gate on
cached-block existence, and with 156 blocks across 31 agents (median 3) a
23-day window silently became three scattered sampled days.

> **Stage 1 decides which episodes to look at. The dump decides how far back
> Stage 2 can see.**

---

## The constraints that shape all of it

**Refusals, not the input guard, are the real ceiling.** Measured on one
agent, one window, varying only the amount:

```
   4 days     37,558 input tokens   → answered
   8 days    244,907               → answered
  13 days    347,057               → stop_reason="refusal", 3 of 4 attempts
```

A refusal returns an empty thinking block and **no text**, so it arrives
downstream as "zero episodes" — a non-answer wearing a negative answer's
clothes. It is probabilistic, not a cliff: another window refused once and
answered twice at ~330K. Budget is **250K tokens**. Refusals are retried
(capped, counted) and excluded from scoring, never counted as findings.

**Days are not a unit of size.** Median digest size runs from 3,378 tokens/day
(DeepSeek-V4-Pro) to 102,985 (DeepSeek-V3.2) — a **30× spread**. "12 days" is
40K tokens for one agent and 1.2M for another. Capping at 10, 12 or 14 days
all produced the same ~500K mean across the golden windows. Budget in tokens;
let the day count fall out.

**Which means some episodes cannot be read back to their start at any budget.**
22 days of a verbose agent is ~1.3M tokens. That is not a tuning problem, and
it is the entire reason pass 2 exists.

---

## Evaluation

Two labelled sets, scoring different targets.

**Stage 1**: `eval/tables/stage1/eval_100.jsonl`, 100 agent-days, human
verdicts. Protocol: `AUDIT_PROTOCOL.md`. The 60-row holdout is spent, so every
threshold quoted here is in-sample.

**Stage 2**: `eval/tables/stage2/labels/`, 20 hand-labelled episodes across 18
windows, 10 drift / 10 not-drift, every label agreeing with its seed day's
Stage 1 verdict. Protocol: `EPISODE_PROTOCOL.md`. Runner: `eval/stage2_eval.py`.

It is ~18 distinct episodes, not 20 — two Kimi labels say in their own notes
that they are "day 2" and "day 3" of one chess activity whose onset is a third
label.

**The contamination rule**, and it is the point of the exercise: the episode
protocol was written without reading `audit/stage2.md`. Same four questions in
the same words would mean Stage 2 scoring well for having been asked its own
question back.

**What the eval can and cannot measure.** Windows come from the labels, which
isolates the judge from the unvalidated walk — but those bounds were chosen by
a human who already knew the answer, so they contain `activity_start` 20/20
where a production window would 10/20. Detection precision is the number to
trust; dating accuracy is measurable only on the subset where the budget
actually reaches the start.

---

## Tried and rejected

| | why it went |
|---|---|
| Single-pass, strong model, no stages | Reversed. Stage 1 became a permissive *ranking* rather than a filter, which answers the original objection that a cheap filter hides unknown patterns |
| Stage 2 reads Stage 1 blocks | The block contains none of the primary evidence the questions require |
| Stage 2 returns one verdict per window | Cannot represent an activity that is on-goal then off-goal; 14/20 windows hold several episodes |
| Stage 2 forbidden from re-judging drift | Stage 1 over-sends by design at precision 0.86, so a filter that cannot reject pins final precision at Stage 1's |
| Mechanical backward walk (Jaccard over word bags) | AUC 0.158 — *inverted*, not weak. No threshold fixes a sign error |
| Walk runs before the explain call | Spends a call on the majority of episodes whose window already contains the answer |
| Day-count caps on the payload | A day is not a unit of size; 30× spread between agents |
| Returning on-goal activities too | `activity_start` already records the legitimate phase on the drift episode |
| Fixed percentile instead of a confidence cut | Drops a drift day when the judge's confidence distribution shifts between eras |

---

## Status

Stage 1 is measured. The handoff is implemented and checked out-of-sample
against the golden set.

**Stage 2's first baseline is in, and it is weak.**

```
  precision 0.67   recall 0.60   accuracy 10/17   zero refusals
```

Calling everything drift scores precision 0.59, so detection is barely above
base rate. But the failure is specific. On the worst false positive it
independently identified both activities the human labeller had identified —
including one the human explicitly considered and rejected — wrote the
human's own reasoning into `dissent`, and ruled against it. Segmentation and
evidence-gathering work; calibration does not.

**All three false positives were episodes where the judge reported it could
not see the activity's start.** That is also the single biggest caveat on
the number: `activity_start` was actually read in only 6 of 17 episodes, and
three of the five pass-2 walks had a ONE-DAY index to walk over, because
Stage 1 coverage over those windows did not exist. This baseline is
substantially measuring missing inputs, not judge quality.

**The backward walk has one exact hit and is otherwise unvalidated.** Given
the right anchor day it dated an activity start to the day; given the day
the sample happened to label, it missed by 21 days — because the drifted
activity was not among that day's threads at all. n=1. See `audit/walk.md`.

Outstanding: Stage 1 coverage over the golden windows (in progress), then a
re-run of the baseline on inputs that are actually present.
