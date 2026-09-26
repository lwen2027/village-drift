"""Draw the held-out eval set.

    export DATABASE_URI='postgresql://…'
    export VILLAGE_DATA=~/Documents/ai-village
    python3 eval/sample_eval_set.py --seed 20260926

Two rules matter more than the sample size:

  * Exclude every (agent, day) already in the training set. Case 7 is NOT in it
    — it was analysed but never adjudicated — so it stays eligible.
  * Never stratify on anything a method computes. Drawing days where mechanical
    signals fire would guarantee the mechanical arm wins. Agent, era and
    activity decile are properties of the day, not of any detector.

Sampling is SYSTEMATIC over a sorted frame rather than per-cell quotas. With
~36 agents x 2 eras x 10 deciles there are ~700 possible cells for 100 draws, so
quotas are impossible; taking every k-th row from a frame sorted on those keys
spreads the draw proportionally across all three at once. The random start is
seeded, so the draw is reproducible and auditable.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frame as frame_mod  # noqa: E402

SORT_KEY = ("era", "activity_decile", "agent", "day")


def systematic(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Every k-th row from a frame sorted on the stratification keys."""
    if n >= len(rows):
        return list(rows)
    ordered = sorted(rows, key=lambda r: tuple(r[k] for k in SORT_KEY))
    step = len(ordered) / n
    start = rng.random() * step
    return [ordered[min(len(ordered) - 1, int(start + i * step))] for i in range(n)]


def blank_row(src: dict, source: str) -> dict:
    """An eval row: the shared schema, with the train-only block nulled.

    Deliberately empty of anything a labeller could anchor on. The monitor's
    verdict is joined in AFTERWARDS by enrich_monitor.py — showing it here would
    destroy the comparison it exists to support.
    """
    return {
        "agent": src["agent"],
        "day": src["day"],

        "split": "eval",
        "source": source,

        "is_drift": None,
        "rationale": "",
        "labeled_by": None,
        "labeled_at": None,
        "second_label": None,

        "digest_sha": None,
        "label_confidence": None,
        "label_minutes": None,

        "monitor_flagged": None,
        "monitor_severity": None,
        "monitor_heading": None,

        # stratification attributes, kept for analysis of WHERE arms fail
        "era": src["era"],
        "activity_decile": src["activity_decile"],
        "turns_raw": src["turns_raw"],

        # train-only
        "case_id": None, "assigned_goal": None, "actually_working_toward": None,
        "when_it_changed": None, "clusters": None, "verified": None,
        "verification_notes": None, "adjudication": None,
    }


def load_frame(cache: str, uri: str | None, cutoff: str | None) -> list[dict]:
    if cache and os.path.exists(cache):
        print(f"frame from cache: {cache}")
        return json.load(open(cache))
    rows = frame_mod.build(uri, cutoff)
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(rows, open(cache, "w"))
        print(f"frame cached -> {cache}")
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n-monitored", type=int, default=80,
                   help="days the monitor ran on; where precision is measurable")
    p.add_argument("--n-unmonitored", type=int, default=20,
                   help="days it never ran on; the only place FNs are visible")
    p.add_argument("--seed", type=int, required=True, help="record this in the PR")
    p.add_argument("--train", default="eval/train_23.jsonl")
    p.add_argument("--out", default="eval/eval_100.jsonl")
    p.add_argument("--cache", default="data/frame.json")
    p.add_argument("--cutoff", default=None,
                   help="last day to consider; defaults to the dump's coverage")
    p.add_argument("--min-turns", type=int, default=1,
                   help="skip days with no recorded activity at all")
    a = p.parse_args()

    rows = load_frame(a.cache, os.environ.get("DATABASE_URI"), a.cutoff)

    excluded = {(json.loads(l)["agent"], json.loads(l)["day"])
                for l in open(a.train)}
    pool = [r for r in rows
            if (r["agent"], r["day"]) not in excluded
            and r["turns_raw"] >= a.min_turns]
    dropped = len(rows) - len(pool)

    rng = random.Random(a.seed)
    mon = [r for r in pool if r["monitored"]]
    unm = [r for r in pool if not r["monitored"]]

    picked = ([blank_row(r, "random-monitored")
               for r in systematic(mon, a.n_monitored, rng)]
              + [blank_row(r, "random-unmonitored")
                 for r in systematic(unm, a.n_unmonitored, rng)])
    picked.sort(key=lambda r: (r["day"], r["agent"]))

    assert len({(r["agent"], r["day"]) for r in picked}) == len(picked), \
        "systematic draw produced a duplicate"
    assert not ({(r["agent"], r["day"]) for r in picked} & excluded), \
        "a training day leaked into the eval set"

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        for r in picked:
            fh.write(json.dumps(r) + "\n")

    print(f"\nwrote {len(picked)} rows -> {a.out}   (seed {a.seed})")
    print(f"  pool {len(pool)} of {len(rows)} agent-days "
          f"({len(excluded)} training days + {dropped - len(excluded)} empty removed)")
    for key in ("source", "era"):
        tally: dict = {}
        for r in picked:
            tally[r[key]] = tally.get(r[key], 0) + 1
        print(f"  {key:6s} " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    dec = sorted(r["activity_decile"] for r in picked)
    print(f"  decile spread {dec[0]}..{dec[-1]}, "
          f"{len(set(dec))}/10 represented")
    print(f"  agents        {len({r['agent'] for r in picked})} distinct")


if __name__ == "__main__":
    main()
