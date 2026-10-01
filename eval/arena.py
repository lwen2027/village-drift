"""The bake-off, and everything that MEASURES the labelling run.

The run itself lives in audit/run.py and does not import this file. Nothing
here is needed to label an agent-day; everything here exists to find out
whether the labelling is any good — drawing a sample, scoring against golden
labels, and the free incumbent baseline.

    audit/   the run      — prompts, client, payload, verdicts
    eval/    the measure  — sampling, scoring, charts

THE BAKE-OFF IS OVER. It ran five ways of answering "to label 4,027
agent-days, what should a smart judge read?" and its result is below, kept
because every later decision refers back to it. Arms C and D have been
removed from the code; their runs are still in arena_runs/ and the snapshot.

         P     R    F1   acc   $/corpus
  A    .75   .60   .67   .84       $175   mechanical block -> judge
  B    .86   .60   .71   .84       $191   cheap model reads the raw day
  C    .64   .70   .67   .81     $2,022   cheap screen -> judge on flagged
  D    .62   .50   .56   .78       $138   arm A minus the computed facts
  0    .67   .80   .73   .84     ~$3,980  the production monitor

It did NOT separate the arms on accuracy -- A, B and the incumbent tie, and
every gap is 1-3 rows at 10 positives. It separated them on cost, by 26x.

  C is eliminated on its own terms: it flags 23 of 37 days (precision .43),
    so it avoids 38% of judge calls while paying the full monitor-view read
    on EVERY day. Screening only pays when confirming is cheap.
  D answered its question -- do the 31 computed facts earn their keep? --
    narrowly yes. It disagrees with A on 2 of 37 rows, both A's way, which
    is inside noise, so the honest reading is "removing them did not help".
    It also stands as the warning that citation frequency is not importance.

WHAT IS LEFT IS A HYBRID, and arm B is now that hybrid rather than the pure
cheap-model arm it started as. The division is drawn from measurement, not
taste -- the cheap model gets 28% of computed fields right, 0% of anything
needing aggregation, and 98% at copying verbatim text:

  code   every count, ratio, cluster and lookup; and every channel that
         lives outside the audited day (goal_announcement,
         goal_period_messages, outreach_constraints, prior snapshots),
         because a one-day reader structurally cannot see them
  model  reading 1,048 turns of raw day, which code cannot do, and the two
         fields that need it -- reached_audience and peer_requests -- plus
         verbatim copying

THE OPEN PROBLEM IS RECALL, not arm choice. Both A and B sit at .60, so a
corpus labelled today would miss 40% of drift days. Diagnosing all six of
arm A's errors gave one mechanism for each direction, and both are now
addressed but unmeasured:

  4 missed drifts   an agent producing substantial real work, none of which
                    could move its target. Read as "tried and was blocked",
                    which rule 2 exempts, when they are "never tried".
                    -> reached_audience. NOTE: its first version asked for
                    actions that could "plausibly advance the goal", which
                    separates almost nothing (AUC 0.655) because plausibility
                    is true of any competent work. Rewritten around RECEIPT.
  2 false alarms    work that looks off-goal in the agent's own session
                    goals and was asked for by someone else.
                    -> peer_requests

QUEUED AND UNMEASURED, all against the held-back rows:
  * reached_audience + peer_requests (extract.md)
  * goal_period_messages (build.py) -- the operator channel nothing saw
  * day_activity (rubric.md) -- for Stage 2 episode dating
  * uncapped session goals, TODAY_GOAL_DAY_BUDGET (config.py)
  * a confidence threshold on the judge's own certainty, unimplemented

That is five changes against one holdout. Several are bug fixes rather than
tuning -- the goal_is_open injection and goal_period_messages in particular
-- and shipping those without ceremony would leave fewer things to
attribute a moved number to.

    python3 eval/arena.py draw       # -> tables/stage1/arena_40.jsonl
    python3 eval/arena.py baseline   # arm0 on exactly those rows
    python3 eval/arena.py run --arm B   # -> audit.run over the sample
    python3 eval/arena.py score
"""
from __future__ import annotations

import argparse
import collections
import http.client
import glob
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # import drift/ from the repo root

from drift import config  # noqa: E402  (kept: _describe reads config)
from audit import run as A  # noqa: E402

# Re-exported so the chart and the tests keep one import site.
PRICES = A.PRICES
prompt = A.prompt
judge_quoted_real_text = A.judge_quoted_real_text
_safe = A._safe
BLOCKS, RAW, RUNS = A.BLOCKS, A.RAW, A.RUNS
STAGE1 = os.path.join(HERE, "tables", "stage1")
LABELS = os.path.join(STAGE1, "eval_100.jsonl")
SAMPLE = os.path.join(STAGE1, "arena_40.jsonl")
MONITOR = os.path.expanduser("~/village-drift-monitor/monitor.jsonl")

SEED = 20260928
N = 40
BROADCAST = "2026-08-12"     # the village-wide anti-drift message


def rows_path(name):
    """`--rows arena_40` is the bake-off sample; anything else is a rowset."""
    return SAMPLE if name in (None, "arena_40") else os.path.join(
        STAGE1, f"rowset_{name}.jsonl")


def _load(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def draw(n: int = N, seed: int = SEED) -> list[dict]:
    """Stratify on the label, then spread within each stratum across era and
    agent so one agent cannot dominate a cell. Seeded, so it is reproducible
    and auditable — the same argument sample_eval_set.py makes."""
    rows = _load(LABELS)
    by_label: dict = collections.defaultdict(list)
    for r in rows:
        by_label[r["is_drift"]].append(r)

    rng = random.Random(seed)
    picked: list[dict] = []
    # Largest-remainder allocation, so the proportions survive rounding.
    exact = {k: len(v) * n / len(rows) for k, v in by_label.items()}
    alloc = {k: int(v) for k, v in exact.items()}
    for k in sorted(by_label, key=lambda k: exact[k] - alloc[k], reverse=True):
        if sum(alloc.values()) >= n:
            break
        alloc[k] += 1

    for label, group in by_label.items():
        # Interleave by (era, agent) so the draw cannot land on one agent's run
        # of consecutive days — the failure mode sample_eval_set.py warns about.
        buckets: dict = collections.defaultdict(list)
        for r in group:
            buckets[(r["day"] >= BROADCAST, r["agent"])].append(r)
        for b in buckets.values():
            rng.shuffle(b)
        keys = sorted(buckets)
        rng.shuffle(keys)
        flat = []
        while any(buckets[k] for k in keys):
            for k in keys:
                if buckets[k]:
                    flat.append(buckets[k].pop())
        picked.extend(flat[:alloc[label]])

    picked.sort(key=lambda r: (r["day"], r["agent"]))
    return picked


def rowset(name: str, write: bool = False) -> None:
    """A named set of rows from eval_100, minus everything already spent.

    arena_40.jsonl is a table; the rows held back from it were only ever
    "whatever is not in the 40" — a definition that is stable exactly as
    long as arena_40 is, and that nothing on disk records. This writes the
    set down so a measurement can name what it ran on.

    EXCLUDES every row in every existing rowset, not just the 40. A method
    scored twice on rows it was changed in response to is measuring its own
    tuning, and the way that happens is by quietly reusing a set.

    The 100 labels support ONE honest measurement of a changed method. This
    command is deliberately general rather than a hardcoded `holdout`,
    because the second measurement needs freshly LABELLED rows — see
    goldenset/ — not a fresh slice of the same hundred.
    """
    out = os.path.join(STAGE1, f"rowset_{name}.jsonl")
    if os.path.exists(out) and write:
        print(f"refusing to overwrite {out}")
        return
    spent = set()
    for f in [SAMPLE] + sorted(glob.glob(os.path.join(STAGE1, "rowset_*.jsonl"))):
        if f == out or not os.path.exists(f):
            continue
        for r in _load(f):
            spent.add((r["agent"], r["day"]))
    picked = [r for r in _load(LABELS) if (r["agent"], r["day"]) not in spent]
    print(f"  {len(spent)} rows already spent, {len(picked)} left")
    _describe(picked)
    if not write:
        print(f"  (dry run — pass --write to persist as rowset_{name})")
        return
    with open(out, "w") as fh:
        for r in picked:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  -> {out}")


def _describe(picked: list[dict]) -> None:
    c = collections.Counter(r["is_drift"] for r in picked)
    d, nd = c[True], c[False]
    print(f"{len(picked)} rows: {d} drift · {nd} not drift · {c[None]} undefined")
    print(f"  prevalence {d / max(d + nd, 1):.1%} over {d + nd} defined "
          f"(full set: 26.9% over 93)")
    post = sum(r["day"] >= BROADCAST for r in picked)
    print(f"  era: {len(picked) - post} pre-{BROADCAST} · {post} on/after "
          f"(full set: 71/29)")
    agents = collections.Counter(r["agent"] for r in picked)
    print(f"  {len(agents)} distinct agents, max {agents.most_common(1)[0][1]} "
          f"rows from one agent")


def baseline(picked: list[dict]) -> None:
    """arm0 on exactly these rows. Costs nothing — it already ran in prod."""
    if not os.path.exists(MONITOR):
        print(f"no monitor verdicts at {MONITOR}")
        return
    mon = {(r["agent"], r["day"]): r for r in _load(MONITOR)}
    tp = fp = fn = tn = 0
    for r in picked:
        if r["is_drift"] is None or (r["agent"], r["day"]) not in mon:
            continue
        flag = bool(mon[(r["agent"], r["day"])].get("monitor_flagged"))
        tp += r["is_drift"] and flag
        fn += r["is_drift"] and not flag
        fp += (not r["is_drift"]) and flag
        tn += (not r["is_drift"]) and not flag
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0
    print(f"\narm0 incumbent on these {tp + fp + fn + tn} defined rows:")
    print(f"  tp={tp} fp={fp} fn={fn} tn={tn}")
    print(f"  precision {p:.2f}  recall {rc:.2f}  F1 {f1:.2f}  "
          f"acc {(tp + tn) / max(tp + fp + fn + tn, 1):.2f}")
    print("  (full 93: P .63 R .68 F1 .65 acc .81)")


# fields() lived here. It scored the cheap model's computed output against
# the mechanical block, and its verdict is why that output no longer exists:
#
#     copying verbatim text                         98%
#     lookups (assigned 92%, goal_is_open 100%*)
#     small salient counts (gaps_over_30min)        88%
#     small counts (chat_sent)                      78%
#     running totals (turns_raw, turns_kept)       8-10%
#     aggregation (most_touched, action_mix)         0%
#     overall                                       28%
#
# * goal_is_open read 100% for months and is 8%. bool subclasses int in
#   Python, so booleans fell through the numeric branch, where the tolerance
#   max(1, 5%) permits a difference of exactly 1 -- a boolean's entire range.
#   Every bool comparison passed whatever the values were. That one field was
#   the cheap arm's entire error set.
#
# Removed because the cheap stage now answers three keys and none of them has
# a mechanical counterpart to score against, so the function could only ever
# print "no arm-B runs yet" on a directory full of arm-B runs.
#
# If a future change asks the cheap model to compute something again, restore
# this from git BEFORE trusting the result, and fix the bool branch first.


def prep(force=False, rows=None):
    """Build every arm-A block in ONE pass.

    drift.build.build() rescans the whole 2.26M-row turns file on each call,
    and the sample spans 33 distinct days, so building per-day meant 33 full
    scans. One range build is one scan.

    It also computes blocks for every other agent-day in the range, which is
    not waste: that is exactly the corpus-wide token total measurement 4 needs.
    Extrapolating arm A's cost by multiplying the 40-row mean by 99.5 would
    inherit this sample's size distribution, and day sizes are skewed. Counting
    the real thing removes that error entirely.
    """
    # Rows are passed in, because prep derives the BUILD RANGE from them.
    # Hardcoded to the 40, it built 2025-05-09..2026-08-27 and the holdout
    # opens 2025-04-28 — eleven days earlier, so its first row had no block
    # and run() stopped with "no cached block ... run prep first" after prep
    # had just reported success.
    rows = _load(SAMPLE) if rows is None else rows
    want = {(r["agent"], r["day"]) for r in rows}
    os.makedirs(BLOCKS, exist_ok=True)
    have = {k for k in want
            if os.path.exists(os.path.join(BLOCKS, f"{_safe(k[0])}__{k[1]}.txt"))}
    if len(have) == len(want) and not force:
        print(f"all {len(want)} blocks cached")
        return
    days = sorted({r["day"] for r in rows})
    print(f"one build over {days[0]}..{days[-1]} "
          f"({len(want) - len(have)} blocks missing) -- this is the slow part")

    from drift import build as B, render as R
    recs = B.build(days[0], days[-1], verbose=True)

    wrote, sizes, corpus = 0, [], []
    for rec in recs:
        txt = R.render(rec)
        corpus.append(len(txt))
        k = (rec.get("agent"), rec.get("day"))
        if k in want:
            stem = os.path.join(BLOCKS, f"{_safe(k[0])}__{k[1]}")
            with open(stem + ".txt", "w") as fh:
                fh.write(txt)
            # The structured record too: the hybrid arm renders from it, and
            # rebuilding it there would reintroduce the per-row full scan this
            # whole step exists to remove.
            with open(stem + ".json", "w") as fh:
                json.dump(rec, fh, ensure_ascii=False, default=str)
            wrote += 1
            sizes.append(len(txt))
    print(f"wrote {wrote}/{len(want)} sample blocks -> {BLOCKS}")
    if sizes:
        sizes.sort()
        print(f"  sample block chars: median {sizes[len(sizes) // 2]:,} "
              f"max {sizes[-1]:,}")
    if corpus:
        # Reported in characters. Tokens come from the API's own usage counts
        # at run time; a chars/4 estimate here would undercut the point of
        # measuring reported usage in the first place.
        stats = {"agent_days": len(corpus), "total_chars": sum(corpus),
                 "median_chars": sorted(corpus)[len(corpus) // 2],
                 "range": [days[0], days[-1]]}
        # Keyed by the range it measured, not a single file every prep
        # clobbers. The unkeyed version read 4,027 for months (the bake-off
        # 40's window), was quoted corpus-wide in every cost estimate, and
        # then silently became 1,020 when a 57-day Haiku prep ran over it.
        # Two different numbers, same filename, no way to tell which you had.
        name = f"arena_corpus_size__{days[0]}__{days[-1]}.json"
        with open(os.path.join(STAGE1, name), "w") as fh:
            json.dump(stats, fh, indent=1)
        print(f"  corpus: {stats['agent_days']:,} agent-days in range, "
              f"{stats['total_chars']:,} block chars total "
              f"-> arena_corpus_size.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd",
                    choices=["draw", "rowset", "baseline", "prep", "run", "score"])
    ap.add_argument("--name", default="holdout",
                    help="rowset: what to call the set being written")
    ap.add_argument("--rows", default="arena_40",
                    help="which row set run/score operate on. Defaults to the "
                         "bake-off sample, so existing commands are unchanged. "
                         "Anything else also TAGS the run files, so a new "
                         "measurement cannot overwrite the old one.")
    ap.add_argument("--arm", choices=["A", "B"])
    ap.add_argument("--stub", action="store_true",
                    help="exercise the whole path, including scoring, with no "
                         "API calls -- validate the measurement before paying")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--write", action="store_true",
                    help="draw: persist the sample (refuses to overwrite). "
                         "prep: REBUILD blocks that already exist -- needed "
                         "after any change to features.py or render.py, "
                         "because prep otherwise skips cached rows and the "
                         "next run scores the old blocks")
    a = ap.parse_args()

    if a.cmd == "prep":
        path = rows_path(a.rows)
        if not os.path.exists(path):
            return print(f"no such row set: {path}") or 1
        # --write rebuilds blocks that already exist
        prep(force=a.write, rows=_load(path))
        return 0

    if a.cmd == "rowset":
        rowset(a.name, write=a.write)
        return 0
    if a.cmd == "run":
        if not a.arm:
            return print("--arm A|B") or 1
        path = rows_path(a.rows)
        if not os.path.exists(path):
            return print(f"no such row set: {path}") or 1
        tag = None if a.rows == "arena_40" else a.rows
        A.run(_load(path), arm=a.arm, stub=a.stub, limit=a.limit, tag=tag)
        return 0
    if a.cmd == "score":
        score(tag=None if a.rows == "arena_40" else a.rows)
        return 0
    if a.cmd == "baseline":
        if not os.path.exists(SAMPLE):
            return print("draw the sample first") or 1
        picked = _load(SAMPLE)
        _describe(picked)
        baseline(picked)
        return 0

    picked = draw()
    _describe(picked)
    baseline(picked)
    if a.write:
        if os.path.exists(SAMPLE):
            print(f"\n{SAMPLE} exists -- refusing to overwrite. Delete it "
                  f"deliberately if you really mean to re-draw.")
            return 1
        os.makedirs(STAGE1, exist_ok=True)
        with open(SAMPLE, "w") as fh:
            for r in picked:
                fh.write(json.dumps({"agent": r["agent"], "day": r["day"],
                                     "is_drift": r["is_drift"],
                                     "goal_is_open": r.get("goal_is_open")},
                                    ensure_ascii=False) + "\n")
        print(f"\nwrote {len(picked)} -> {SAMPLE}")
    else:
        print("\n(dry run -- pass --write to persist)")
    return 0

# ----------------------------------------------------------------- score ----
def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def score(arms=("A", "B"), tag=None):
    # Labels come from eval_100, not from the row set. Loading them from
    # SAMPLE was the same coupling run() had -- it worked exactly as long as
    # the only row set was the 40, and a holdout row then resolved to None
    # and crashed on g["is_drift"]. The tag already restricts WHICH runs are
    # scored; gold only has to be able to look any of them up.
    gold = {(r["agent"], r["day"]): r for r in _load(LABELS)}
    print(f"{'arm':4s} {'n':>3} {'P':>5} {'R':>5} {'F1':>5} {'acc':>5} "
          f"{'abst':>5} {'quote ok':>9} {'salv':>5} {'in tok':>10} {'out tok':>9} "
          f"{'cached':>7} {'$/day':>7} {'$/corpus':>9}")
    for arm in arms:
        stem = f"{arm}-{tag}__" if tag else f"{arm}__"
        files = [f for f in os.listdir(RUNS) if f.startswith(stem)] \
            if os.path.isdir(RUNS) else []
        if not files:
            continue
        tp = fp = fn = tn = 0
        abst_ok = abst_n = salv = errs = 0
        undef_bad = 0
        prov = collections.Counter()
        tin = tout = tcache = 0
        spend = 0.0
        for f in files:
            rec = json.load(open(os.path.join(RUNS, f)))
            for c in rec.get("calls", []):
                u = c.get("usage") or {}
                tin += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                    + (u.get("cache_creation_input_tokens") or 0)
                tcache += u.get("cache_read_input_tokens") or 0
                # Cache reads bill at 0.1x base and writes at 1.25x; both were
                # priced at zero here once, so every cached arm looked cheaper
                # than it is. Worth ~2% on arms B and D. The rule now lives in
                # A.call_cost -- see there for why a 0 in cache_creation is
                # not evidence that writes are free.
                spend += A.call_cost(u, c.get("model")) or 0.0
                tout += u.get("output_tokens") or 0
            salv += bool(rec.get("salvaged"))
            if rec.get("error"):
                errs += 1
                continue
            g = gold.get((rec["agent"], rec["day"]))
            v = rec.get("verdict") or {}
            pred = v.get("is_drift")
            # No `or v.get("decisive_quote")` fallback. All 160 runs on disk
            # use decisive_evidence and nothing writes the old name; the
            # fallback only served to hide a schema that had moved.
            prov[judge_quoted_real_text(v.get("decisive_evidence"),
                                        rec.get("payload"))] += 1
            if g["is_drift"] is None:                 # open goal: should abstain
                abst_n += 1
                abst_ok += str(pred).lower() == "undefined"
                continue
            if str(pred).lower() == "undefined":
                # The goal is CLOSED here, so the rubric's one licensed use of
                # `undefined` does not apply -- this is a non-answer. It used
                # to fall through to `yes = False` and be counted as a correct
                # negative, which handed an arm a point for declining to
                # answer. Only arm B did it, once, and that single free credit
                # was the whole of its 0.86-vs-0.84 lead over arm A.
                undef_bad += 1
                continue
            yes = pred is True or str(pred).lower() == "true"
            tp += g["is_drift"] and yes
            fn += g["is_drift"] and not yes
            fp += (not g["is_drift"]) and yes
            tn += (not g["is_drift"]) and not yes
        p, r, f1 = _prf(tp, fp, fn)
        n = tp + fp + fn + tn + undef_bad
        ok = prov["located"]
        tot = sum(prov[k] for k in ("located", "NOT_FOUND"))
        print(f"{arm:4s} {n:3d} {p:5.2f} {r:5.2f} {f1:5.2f} "
              f"{(tp + tn) / max(n, 1):5.2f} {abst_ok}/{abst_n:<3d} "
              f"{ok}/{tot:<7d} {salv:5d} {tin:10,d} {tout:9,d} "
              f"{100 * tcache / max(tin, 1):6.0f}% "
              f"{spend / max(len(files), 1):7.3f} "
              f"{spend / max(len(files), 1) * 4027 * 0.839:9,.0f}"
              + (f"   ERRORS {errs}" if errs else ""))
        if prov["too_short"] or prov["no_quote"]:
            print(f"     (quote: {dict(prov)})")
if __name__ == "__main__":
    raise SystemExit(main())
