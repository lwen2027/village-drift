"""Probe: does ANY backward-walk variant date Claude Haiku 4.5's activity_start?

Read-only. Makes NO model calls, writes no tables, touches no live file.
Everything comes from eval/tables/stage1/arena_blocks, filtered to 57 contiguous
active days, 2026-05-01 .. 2026-07-15, for the one agent whose answer is known.

    onset            2026-07-06   the day the marathon got relabelled
    activity_start   2026-06-15   TRUTH, 21 days before onset
    corpus floor     2026-05-01   32 active days BELOW the answer

Both ends are failures. Returning 2026-07-05 is "too late"; returning
2026-05-01 is "runaway". Every variant below is scored against both.

INPUT. session_goals_today, the same whole-day word bag the documented
failure was measured on. `day_activity` -- the 3-8 word descriptor that is
the intended fix -- does not exist for these days and cannot be produced
without a model call. So this isolates the ALGORITHM from the INPUT.
Anything that fails here fails on whole-day bags; that is not evidence
about what it would do on four-word descriptors.

HEADLINE RESULT (section 6). On this input the similarity signal is not weak,
it is INVERTED. Jaccard against the onset day ranks the 32 days BEFORE the
activity started ABOVE the 15 days of the activity itself: AUC 0.169, where
0.5 is a coin flip. A stopping rule looks for similarity to FALL as it walks
back; here it RISES. No threshold, no K and no bound can fix a sign error.

    python3 eval/walk_probe.py          # everything
    python3 eval/walk_probe.py profile  # per-day similarity curve only
"""

from __future__ import annotations

import datetime
import math
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from drift import config  # noqa: E402
from drift.config import LOOKBACK_DAYS  # noqa: E402
from drift.features import content_words  # noqa: E402

# arena_blocks, not a private copy. The duplicate directory this used to
# read was built before prep could produce these, and holding two copies of
# the same 57 days invites them to diverge. arena_blocks is a superset, so
# the range filter below is load-bearing: it also holds four SCATTERED
# haiku days (2025-11-19, 2026-04-06, 2026-07-22, 2026-08-06) and splicing
# those onto the contiguous run is the exact error this probe diagnosed.
BLOCKS = os.path.join(HERE, "tables", "stage1", "arena_blocks")
AGENT, LO, HI = "Claude Haiku 4.5", "2026-05-01", "2026-07-15"


def _safe(a):
    """arena_blocks filename convention. This used to be a DIFFERENT rule
    under a docstring claiming it matched run.py -- see drift/config.py."""
    return config.safe_agent(a)


ONSET = "2026-07-06"
TRUTH = "2026-06-15"
TRUTH_DAYS = 21
FLOOR = "2026-05-01"
MAX_GAP_DAYS = 3        # episodes.MAX_GAP_DAYS; max real gap here is 3


# --------------------------------------------------------------- loading ----
def load_days() -> dict:
    """day -> content-word set of that day's session goals."""
    out = {}
    for fn in sorted(os.listdir(BLOCKS)):
        if not fn.endswith(".json"):
            continue
        b = json.load(open(os.path.join(BLOCKS, fn)))
        if b.get("agent") != AGENT or not LO <= b.get("day", "") <= HI:
            continue
        sg = (b.get("context") or {}).get("session_goals_today") or []
        if isinstance(sg, str):
            sg = [sg]
        out[b["day"]] = content_words(" ".join(str(s) for s in sg))
    return out


def _d(s):
    return datetime.date.fromisoformat(s[:10])


def apart(a, b):
    return abs((_d(b) - _d(a)).days)


# --------------------------------------------------------------- metrics ----
def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def containment(a, ref):
    """Share of the CANDIDATE day that is reference vocabulary.

    Asymmetric on purpose. Jaccard against the 272-word onset day caps a
    124-word day at 124/272 however perfectly contained it is, which is the
    dilution half of the documented failure.
    """
    return len(a & ref) / len(a) if a and ref else 0.0


def make_idf(days: dict):
    """IDF over THIS agent's own days, plus raw document frequency.

    Targets the other half of the failure: persistent private vocabulary.
    A word the agent writes every day is no evidence that two days are the
    same activity, and plain Jaccard counts it the same as a word used once.
    """
    n = len(days)
    df = {}
    for s in days.values():
        for w in s:
            df[w] = df.get(w, 0) + 1
    return {w: math.log(n / c) for w, c in df.items()}, df


def widx(a, b, idf):
    if not a or not b:
        return 0.0
    den = sum(idf.get(w, 0.0) for w in a | b)
    return sum(idf.get(w, 0.0) for w in a & b) / den if den else 0.0


def wcont(a, ref, idf):
    if not a or not ref:
        return 0.0
    den = sum(idf.get(w, 0.0) for w in a)
    return sum(idf.get(w, 0.0) for w in a & ref) / den if den else 0.0


def rare(s, df, n, frac):
    """Keep only words appearing in <= frac of the agent's days."""
    return {w for w in s if df.get(w, 0) <= frac * n}


# ------------------------------------------------------------------ walk ----
def walk(days, order, onset, sim_fn, threshold, k, *, chained,
         ref=None, bound=None, skip_empty=False):
    """Backward walk. Returns (start_day, trail, stop_reason).

    chained=True  : compare each day to the last ACCEPTED day (episodes.py)
    chained=False : compare each day to a fixed `ref` (anchored)

    Halts after `k` CONSECUTIVE days below threshold. `bound` caps lookback
    in calendar days. `skip_empty` treats a day with no session goals as
    NO EVIDENCE rather than as a miss -- see section 0.
    """
    prior = [d for d in order if d < onset]
    start, misses, trail = onset, 0, []
    later, last_seen = days[onset], onset
    stop = "ran out of days (corpus floor)"
    for day in reversed(prior):
        if bound is not None and apart(day, onset) > bound:
            stop = f"hit {bound}-day bound"
            break
        gap = apart(day, last_seen)
        if gap > MAX_GAP_DAYS:
            stop = f"unobserved gap of {gap} days"
            break
        last_seen = day
        if skip_empty and not days[day]:
            trail.append((day, None))
            continue
        s = sim_fn(days[day], later if chained else ref)
        trail.append((day, round(s, 4)))
        if s >= threshold:
            start, misses = day, 0
            if chained:
                later = days[day]
        else:
            misses += 1
            if misses >= k:
                stop = f"{k} consecutive days < {threshold}"
                break
    return start, trail, stop


def score(start, onset=ONSET, truth_days=TRUTH_DAYS):
    n = apart(start, onset)
    err = n - truth_days
    if abs(err) == 0:
        tag = "EXACT"
    elif abs(err) <= 3:
        tag = f"NEAR ({err:+d}d)"
    elif start == FLOOR:
        tag = "RUNAWAY (floor)"
    elif err < 0:
        tag = f"too late ({err:+d}d)"
    else:
        tag = f"too early ({err:+d}d)"
    return n, tag


def auc(pos, neg):
    """P(a random in-episode day outscores a random pre-episode day)."""
    n, w = 0, 0.0
    for p in pos:
        for q in neg:
            n += 1
            w += 1.0 if p > q else (0.5 if p == q else 0.0)
    return w / n if n else 0.5


# ----------------------------------------------------------- references ----
def references(days, order, idf, df):
    """Fixed anchors. All are built only from days at/after onset, except
    the one marked [oracle], which uses the known answer and is a ceiling,
    not a rule."""
    post = [d for d in order if d >= ONSET]
    n = len(days)
    R = {"onset-day": set(days[ONSET])}
    for k in (2, 3, 5):
        win = post[:k]
        u = set()
        for d in win:
            u |= days[d]
        R[f"union(onset..+{k - 1})"] = u
        i = set(days[win[0]])
        for d in win[1:]:
            i &= days[d]
        R[f"inter(onset..+{k - 1})"] = i
    cnt = {}
    for d in post[:3]:
        for w in days[d]:
            cnt[w] = cnt.get(w, 0) + 1
    R["maj2of3(onset..+2)"] = {w for w, c in cnt.items() if c >= 2}
    R["onset rare<=30%"] = rare(days[ONSET], df, n, 0.30)
    R["onset rare<=15%"] = rare(days[ONSET], df, n, 0.15)
    R["onset top-40 IDF"] = set(
        sorted(days[ONSET], key=lambda w: (-idf[w], w))[:40])
    pu = set()
    for d in [x for x in order if x > ONSET][:4]:
        pu |= days[d]
    R["onset minus post-onset"] = days[ONSET] - pu
    R["[oracle] activity terms"] = content_words(
        "keystroke marathon games victory points score play2048 beat")
    return R


# ----------------------------------------------------------------- views ----
def hdr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def row(name, thr, k, start, extra=""):
    n, tag = score(start)
    print(f"  {name:<32} thr={thr:<5} K={k}  -> {start}  {n:>3}d  "
          f"{tag:<17} {extra}")


# ------------------------------------------------------------------ main ----
def main(mode="all"):
    days = load_days()
    order = sorted(days)
    idf, df = make_idf(days)
    n = len(days)
    sizes = {d: len(days[d]) for d in order}
    empty = [d for d in order if not days[d]]

    print(f"agent      {AGENT}")
    print(f"days       {n} ({order[0]} .. {order[-1]}); every gap <= "
          f"{MAX_GAP_DAYS}, so the gap guard never fires on this data")
    print(f"onset      {ONSET}   truth {TRUTH}   = {TRUTH_DAYS} days")
    print(f"pre-truth  {sum(1 for d in order if d < TRUTH)} active days below "
          f"the answer -- the runaway target")
    print(f"day size   median {sorted(sizes.values())[n // 2]} words, "
          f"min {min(sizes.values())}, max {max(sizes.values())}, "
          f"onset {sizes[ONSET]}")

    if mode == "profile":
        hdr("per-day similarity vs the onset day, whole corpus")
        for d in order:
            flag = "IN " if TRUTH <= d <= ONSET else "out"
            print(f"  {d} {flag} {len(days[d]):>5}w  "
                  f"j={jaccard(days[d], days[ONSET]):.3f}  "
                  f"cont={containment(days[d], days[ONSET]):.3f}  "
                  f"widf={widx(days[d], days[ONSET], idf):.3f}"
                  f"{'   <- TRUTH' if d == TRUTH else ''}")
        return

    # --------------------------------------------- 0. a data bug first
    hdr("0. DATA BUG -- two days have NO session goals at all")
    for d in empty:
        b = json.load(open(os.path.join(BLOCKS, f"{_safe(AGENT)}__{d}.json")))
        print(f"  {d}  session_goals_today = []  "
              f"turns_kept={b['facts']['turns_kept']['value']}  "
              f"({_d(d).strftime('%A')})")
    print("\n  2026-07-04 is the FIRST day the backward walk reaches. Its")
    print("  similarity is 0.000 against anything, so the current walk spends")
    print("  one of its K misses on a day that contains no evidence either")
    print("  way. At K=1 that alone ends the walk at step 1. Scoring an")
    print("  absent day as a miss is a bug independent of the metric; the")
    print("  skip-empty variant below separates the two.")

    # --------------------------------------------- A. documented inversion
    hdr("A. the documented inversion, re-measured on contiguous data")
    print("  jaccard of each day against the onset day 2026-07-06")
    for d in ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04",
              "2026-07-07", TRUTH, "2026-06-14", "2026-05-13", FLOOR]:
        note = {TRUTH: "  <- TRUTH (activity_start)",
                "2026-06-14": "  <- day BEFORE truth, should be LOW",
                "2026-07-07": "  <- the documented 0.219 inversion",
                "2026-05-13": "  <- 33 days before truth, unrelated work",
                }.get(d, "")
        print(f"    {d}  {len(days[d]):>5} words  "
              f"j={jaccard(days[d], days[ONSET]):.3f}"
              f"  cont={containment(days[d], days[ONSET]):.3f}"
              f"  widf={widx(days[d], days[ONSET], idf):.3f}{note}")
    print("\n  Confirmed, and worse than documented: 2026-05-13 -- Observatory")
    print("  page-building, nothing to do with the marathon -- scores 0.117,")
    print("  ABOVE every single day of the real episode. Section 6 quantifies.")

    # --------------------------------------------- 1. chained baseline
    hdr("1. BASELINE -- chained walk (episodes.walk_back), reproduce failure")
    for thr in (0.06, 0.08, 0.10, 0.12, 0.15, 0.20):
        for k in (1, 2, 3):
            st, _, stop = walk(days, order, ONSET, jaccard, thr, k,
                               chained=True)
            row("chained jaccard", thr, k, st, f"[{stop}]")
        print()
    print("  The documented cliff is confirmed in shape. Exact values differ")
    print("  because the docstring's 1/162/258-day figures were measured")
    print("  against the sparse full-dump index, not these 57 blocks.")

    hdr("1b. the chained walk, step by step, threshold 0.08 K=2")
    st, tr, stop = walk(days, order, ONSET, jaccard, 0.08, 2, chained=True)
    for d, s in tr:
        print(f"    {d}  {s:.3f}  {'HIT ' if s >= 0.08 else 'miss'}"
              f"{'   <- TRUTH' if d == TRUTH else ''}")
    print(f"    stop: {stop}  ->  {st}")
    print("\n  This cell returns the exact right answer. Section 7 shows it is")
    print("  a coin toss: it is one cell in a grid whose neighbours on BOTH")
    print("  sides run away to the floor.")

    hdr("1c. skip-empty variant (the section-0 bug fixed)")
    for thr in (0.06, 0.08, 0.10, 0.12):
        for k in (1, 2, 3):
            st, _, stop = walk(days, order, ONSET, jaccard, thr, k,
                               chained=True, skip_empty=True)
            row("chained, skip empty", thr, k, st, f"[{stop}]")
        print()

    # --------------------------------------------- 2. anchored
    R = references(days, order, idf, df)
    hdr("2. ANCHORED -- every day vs a FIXED reference (no compounding)")
    for rn, ref in R.items():
        print(f"\n  --- reference: {rn}  ({len(ref)} words) ---")
        for thr in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20):
            st, _, stop = walk(days, order, ONSET, jaccard, thr, 3,
                               chained=False, ref=ref, skip_empty=True)
            row("anchored jaccard", thr, 3, st, f"[{stop}]")

    # --------------------------------------------- 3. K sweep
    hdr("3. K-CONSECUTIVE-MISSES tolerance (empty days skipped)")
    for label, chained, ref in [("chained", True, None),
                                ("anchored onset-day", False, R["onset-day"])]:
        print(f"\n  --- {label} ---")
        for k in (1, 2, 3, 4, 6):
            for thr in (0.06, 0.08, 0.10, 0.12):
                st, _, _ = walk(days, order, ONSET, jaccard, thr, k,
                                chained=chained, ref=ref, skip_empty=True)
                row(label, thr, k, st)
            print()

    # --------------------------------------------- 4. hard bound
    hdr(f"4. HARD BOUND -- drift.config.LOOKBACK_DAYS = {LOOKBACK_DAYS}")
    floor_day = _d(ONSET) - datetime.timedelta(days=LOOKBACK_DAYS)
    print(f"  {LOOKBACK_DAYS} days before {ONSET} is {floor_day}, which is")
    print(f"  {LOOKBACK_DAYS - TRUTH_DAYS} days EARLIER than the truth. The")
    print("  bound cannot produce the right answer; it can only cap a wrong")
    print("  one. It converts 'runaway to 05-01' (66d, +45 error) into")
    print(f"  '{floor_day}' ({LOOKBACK_DAYS}d, +{LOOKBACK_DAYS - TRUTH_DAYS} "
          "error) -- a smaller error, still wrong, and now")
    print("  indistinguishable from a genuine 45-day episode.\n")
    for thr in (0.06, 0.08, 0.10):
        for lbl, ch in (("chained", True), ("anchored", False)):
            st, _, stop = walk(days, order, ONSET, jaccard, thr, 3,
                               chained=ch, ref=R["onset-day"],
                               bound=LOOKBACK_DAYS, skip_empty=True)
            row(f"{lbl} + bound{LOOKBACK_DAYS}", thr, 3, st, f"[{stop}]")

    # --------------------------------------------- 5. other metrics
    hdr("5. OTHER METRICS -- aimed at the two diagnosed causes")
    print("  containment  -> dilution (a big anchor caps a small day)")
    print("  idf-weighted -> persistent private vocabulary")
    print("  presence     -> drop magnitudes, just ask 'did it come up?'")
    variants = [
        ("containment vs onset", lambda a, r: containment(a, r),
         R["onset-day"], (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)),
        ("idf-jaccard vs onset", lambda a, r: widx(a, r, idf),
         R["onset-day"], (0.02, 0.04, 0.06, 0.08, 0.10, 0.15)),
        ("idf-contain vs onset", lambda a, r: wcont(a, r, idf),
         R["onset-day"], (0.05, 0.10, 0.15, 0.20, 0.30)),
        ("count vs onset top-40 IDF", lambda a, r: float(len(a & r)),
         R["onset top-40 IDF"], (1, 2, 3, 5)),
        ("count vs [oracle] terms", lambda a, r: float(len(a & r)),
         R["[oracle] activity terms"], (1, 2, 3, 4)),
    ]
    for name, fn, ref, thrs in variants:
        print(f"\n  --- {name}  (ref {len(ref)} words) ---")
        for thr in thrs:
            st, _, stop = walk(days, order, ONSET, fn, thr, 3,
                               chained=False, ref=ref, skip_empty=True)
            row(name, thr, 3, st, f"[{stop}]")

    # --------------------------------------------- 6. the decisive test
    hdr("6. IS THE BOUNDARY IN THE DATA AT ALL?  (the decisive test)")
    print("  Forget the walk. Score the 15 real episode days (06-15..07-03,")
    print("  empty days dropped) against the 32 days before truth. AUC 0.5 is")
    print("  a coin flip; BELOW 0.5 means the metric ranks pre-episode days")
    print("  ABOVE episode days, i.e. the signal has the wrong sign.\n")
    tests = [
        ("jaccard vs onset-day", lambda a: jaccard(a, R["onset-day"])),
        ("jaccard vs union(onset..+2)",
         lambda a: jaccard(a, R["union(onset..+2)"])),
        ("jaccard vs inter(onset..+2)",
         lambda a: jaccard(a, R["inter(onset..+2)"])),
        ("jaccard vs maj2of3(onset..+2)",
         lambda a: jaccard(a, R["maj2of3(onset..+2)"])),
        ("containment vs onset-day",
         lambda a: containment(a, R["onset-day"])),
        ("idf-jaccard vs onset-day", lambda a: widx(a, R["onset-day"], idf)),
        ("idf-contain vs onset-day", lambda a: wcont(a, R["onset-day"], idf)),
        ("count vs onset rare<=15%",
         lambda a: float(len(a & R["onset rare<=15%"]))),
        ("count vs onset top-40 IDF",
         lambda a: float(len(a & R["onset top-40 IDF"]))),
        ("count vs onset minus post-onset",
         lambda a: float(len(a & R["onset minus post-onset"]))),
        ("[ORACLE] count vs activity terms",
         lambda a: float(len(a & R["[oracle] activity terms"]))),
    ]
    ins_days = [d for d in order if TRUTH <= d < ONSET and days[d]]
    out_days = [d for d in order if d < TRUTH and days[d]]
    print(f"  {'metric / reference':<36} {'AUC':>6} {'IN med':>8} "
          f"{'OUT med':>8} {'IN min':>8} {'OUT max':>8}  verdict")
    results = []
    for label, fn in tests:
        ins = [fn(days[d]) for d in ins_days]
        out = [fn(days[d]) for d in out_days]
        a = auc(ins, out)
        v = ("SEPARABLE" if min(ins) > max(out)
             else "inverted" if a < 0.45
             else "no signal" if a < 0.6
             else "STRONG (not clean)" if a >= 0.9 else "overlapping")
        print(f"  {label:<36} {a:>6.3f} {statistics.median(ins):>8.3f} "
              f"{statistics.median(out):>8.3f} {min(ins):>8.3f} "
              f"{max(out):>8.3f}  {v}")
        results.append((a, label, v))

    # --------------------------------------------- 6b. chained boundary
    hdr("6b. CHAINED: is 06-14 -> 06-15 even the weakest link?")
    print("  A chained walk can only stop where the step-to-step similarity")
    print("  dips. If the true boundary is not the weakest step, no chained")
    print("  threshold stops there without stopping somewhere else first.\n")
    steps = []
    for i in range(1, len(order)):
        a, b = order[i - 1], order[i]
        if b > ONSET or not days[a] or not days[b]:
            continue
        steps.append((jaccard(days[a], days[b]), a, b))
    ranked = sorted(steps)
    for r, (s, a, b) in enumerate(ranked[:6], 1):
        print(f"    #{r} weakest  {a} -> {b}   j={s:.3f}"
              f"{'   <- TRUE BOUNDARY' if b == TRUTH else ''}")
    tr_rank = [i for i, (s, a, b) in enumerate(ranked, 1) if b == TRUTH][0]
    tr_s = [s for s, a, b in steps if b == TRUTH][0]
    print(f"\n    true boundary 2026-06-14 -> {TRUTH}: j={tr_s:.3f}, "
          f"ranked #{tr_rank} of {len(steps)} (#1 would be ideal).")
    print(f"    {tr_rank - 1} step(s) inside the episode are weaker, so any")
    print("    threshold that cuts here also cuts there first.")

    # --------------------------------------------- 7. fragility
    hdr("7. FRAGILITY -- how many grid cells land near truth?")
    print("  Dense sweep. A fix should occupy a CONTIGUOUS REGION of the")
    print("  grid. An isolated cell is a coin toss.\n")
    for lbl, ch, ref in [("chained", True, None),
                         ("anchored onset-day", False, R["onset-day"])]:
        hits, total, cells = 0, 0, []
        t = 0.02
        while t <= 0.2001:
            for k in (1, 2, 3, 4):
                st, _, _ = walk(days, order, ONSET, jaccard, round(t, 3), k,
                                chained=ch, ref=ref, skip_empty=True)
                total += 1
                if abs(apart(st, ONSET) - TRUTH_DAYS) <= 3:
                    hits += 1
                    cells.append((round(t, 3), k, st))
            t += 0.005
        print(f"  {lbl:<22} {hits:>3}/{total} cells within +-3 days of truth")
        if cells:
            print("      " + "; ".join(f"thr={c[0]} K={c[1]} -> {c[2]}"
                                       for c in cells))
    print("\n  The one working band, at 0.0025 resolution (chained, K=2):")
    lo = hi = None
    t = 0.060
    while t <= 0.1101:
        st, _, _ = walk(days, order, ONSET, jaccard, round(t, 4), 2,
                        chained=True, skip_empty=True)
        if st == TRUTH:
            lo = round(t, 4) if lo is None else lo
            hi = round(t, 4)
        t += 0.0025
    print(f"    EXACT for thr in [{lo}, {hi}]; RUNAWAY to {FLOOR} below it,")
    print("    0 days above it. Width ~0.0125 of a 0.18-wide sweep.")
    print("\n  Why that band exists, to 6 decimal places:")
    for a, b, tag in [("2026-06-17", "2026-06-18", "INSIDE episode"),
                      ("2026-06-26", "2026-06-29", "INSIDE episode"),
                      ("2026-06-14", "2026-06-15", "the TRUE boundary")]:
        print(f"    {a} -> {b}  j={jaccard(days[a], days[b]):.6f}   {tag}")
    print("\n    The true boundary and an inside-episode step differ by")
    print("    0.000380 -- 4 parts in 10,000. The band's lower edge sits in")
    print("    that interval. K=2 forgives the inside step only because it")
    print("    happens to be isolated. This is a threshold fitted to noise,")
    print("    on n=1 episode, and it is not evidence that chaining works.")
    print("\n  For comparison, the same sweep on the oracle presence rule:")
    hits, total, ks = 0, 0, []
    for k in (1, 2, 3, 4, 6, 8, 10, 12):
        st, _, _ = walk(days, order, ONSET,
                        lambda a, r: float(len(a & r)), 1.0, k,
                        chained=False, ref=R["[oracle] activity terms"],
                        skip_empty=True)
        total += 1
        if abs(apart(st, ONSET) - TRUTH_DAYS) <= 3:
            hits += 1
            ks.append(k)
    print(f"  [oracle] presence>=1  {hits:>3}/{total} cells, exact at K="
          f"{ks} -- stable across every K tried.")

    # --------------------------------------------- 8. onset sensitivity
    hdr("8. SENSITIVITY -- onset has been written as BOTH 07-06 and 07-07")
    print("  07-06 is the true onset (the turn that reverses the decision);")
    print("  07-07 is the day the SAMPLE happened to label, and the episode")
    print("  record carries that one. Only 07-06 is 21 days from truth, so")
    print("  07-06 is used above. Re-run of the baseline with 07-07:\n")
    for thr in (0.06, 0.08, 0.10):
        for k in (1, 2, 3):
            st, _, stop = walk(days, order, "2026-07-07", jaccard, thr, k,
                               chained=True)
            nn, tag = score(st, "2026-07-07", 22)
            print(f"  chained, onset 07-07         thr={thr:<5} K={k}  -> "
                  f"{st}  {nn:>3}d  {tag}")
        print()

    # --------------------------------------------- summary
    hdr("SUMMARY")
    sep = [r for r in results if r[2] == "SEPARABLE" and "ORACLE" not in r[1]]
    if sep:
        print("  SEPARABLE without the oracle: " + ", ".join(r[1] for r in sep))
    else:
        print("  No non-oracle metric or reference tested separates the true")
        print("  episode from the 32 days before it. Best non-oracle AUC:")
        for a, label, v in sorted(results, reverse=True)[:4]:
            if "ORACLE" in label:
                continue
            print(f"      {a:.3f}  {label}  ({v})")
        orc = [r for r in results if "ORACLE" in r[1]][0]
        print(f"  Oracle, same walk machinery: AUC {orc[0]:.3f} ({orc[2]}).")
        print("  The boundary IS in the day's text. Whole-day word bags")
        print("  destroy it; the walk mechanics are not what is broken.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
