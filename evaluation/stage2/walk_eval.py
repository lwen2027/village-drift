"""Score the backward walk against the golden set's activity_start.

    python3 -m evaluation.stage2.walk_eval --tag B-full --descriptor-tags B-full --dry
    python3 -m evaluation.stage2.walk_eval --tag B-full --descriptor-tags B-full

WHAT THIS MEASURES, AND WHAT IT DOES NOT. Only the walk: given a window and
Stage 1's verdicts inside it, does it locate the `activity_start_candidate`
correctly? The
explain call is not involved. A walk that works does not make Stage 2 work;
a walk that does not makes the window sizing unsolvable, because the digest
budget tops out around 14 days and 4 of the 10 drift episodes begin further
back than that.

CONTAMINATION. The episode dict handed to walk() is built from Stage 1's own
output plus the window BOUNDS. Nothing from the label reaches it -- not
`q1_activity_start`, not `q2_onset`, not `activity`, not the mechanism.
Bounds are not an answer: measured across the set, `window.back_to` never
equals `q1_activity_start`, and it precedes it by 4 days to 6 weeks.

PRIOR. On the one episode with coverage before this ran, the walk anchored
on the labelled day and missed by 21 days, then anchored on the true onset
and hit exactly. That is n=1 and it is the episode walk.md was fitted to,
which is the reason for running the other nine.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
from village_drift import paths
from village_drift.handoff import pipeline as P
from village_drift.stage1 import run as R
from village_drift.stage2 import run as S

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = str(paths.STAGE2_LABELS)
OUT = str(paths.STAGE2_ARTIFACTS / "walk_eval.jsonl")


def _d(s):
    return datetime.date.fromisoformat(str(s)[:10])


def golden(drift_only=True):
    """The labelled episodes, with the few fields this eval may read."""
    out = []
    for f in sorted(glob.glob(os.path.join(LABELS, "*.json"))):
        b = os.path.basename(f)
        if b.startswith("_") or "chunk" in b:
            continue
        d = json.load(open(f))
        onset = (d.get("q2_onset") or {}).get("value")
        if drift_only and not onset:
            continue
        out.append({
            "episode_id": d["episode_id"],
            "agent": d["agent"],
            "window": d.get("window") or {},
            # TRUTH -- used only to score, never passed to the walk.
            "truth_activity_start": (d.get("q1_activity_start")
                                     or {}).get("value"),
            "truth_onset": onset,
        })
    return out


def window_for(g, verdicts, cut=P.CONFIDENCE_CUT):
    """Build the walk's input from Stage 1 alone, inside the window bounds."""
    agent = g["agent"]
    obs = (g["window"] or {}).get("observed_days") or []
    seeds = []
    for day in sorted(obs):
        v = verdicts.get((agent, day))
        if not v:
            continue
        isd, conf = v.get("is_drift"), v.get("confidence")
        if isd is None or conf is None:
            continue
        verdict = {"is_drift": P._drift_value(isd),
                   "confidence": float(conf)}
        if (verdict["is_drift"] is True
                or (verdict["is_drift"] is False
                    and verdict["confidence"] < cut)):
            seeds.append(P.routing_seed(day, verdict))
    if not seeds:
        return None
    return {"window_id": g["episode_id"], "agent": agent,
            "seed_days": seeds, "goal": None}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--tag", required=True,
                   help="exact Stage-1 run tag used for seed routing")
    p.add_argument("--descriptor-tags",
                   required=True,
                   help="comma-separated exact tags used by the walk index")
    a = p.parse_args()

    vi = P.verdicts(a.tag)
    descriptor_tags = tuple(x.strip() for x in a.descriptor_tags.split(",")
                            if x.strip())
    runs = S._run_index(tags=descriptor_tags)
    snapshot = S.descriptor_snapshot(runs)
    print(f"  routing tag: {a.tag}; descriptor snapshot: "
          f"{snapshot['days']} days {snapshot['fingerprint'][:12]} "
          f"{snapshot['sources']}")
    gs = golden()
    if a.limit:
        gs = gs[:a.limit]

    rows, spend = [], 0.0
    print(f"{'episode':30s} {'anchor day':>11s} {'predicted':>11s} "
          f"{'truth':>11s} {'err':>5s}")
    for g in gs:
        window = window_for(g, vi)
        if window is None:
            print(f"{g['episode_id'][:29]:30s} {'NO STAGE 1 IN WINDOW':>41s}")
            continue
        # S.anchor_day_for, not a local copy. A local copy is how --dry came
        # to report anchors the walk would never have used.
        anchor_day = S.anchor_day_for(window, runs)
        if a.dry:
            print(f"{g['episode_id'][:29]:30s} {str(anchor_day):>11s} "
                  f"{'(dry)':>11s} {str(g['truth_activity_start'])[:10]:>11s}")
            continue

        w = S.walk(window, stub=False, runs=runs)
        spend += R.call_cost(w.get("usage"), R.MODELS["judge"]) or 0.0
        pred = w.get("activity_start_candidate")
        truth = str(g["truth_activity_start"])[:10] if g["truth_activity_start"] else None
        err = (_d(pred) - _d(truth)).days if (pred and truth) else None
        print(f"{g['episode_id'][:29]:30s} {str(anchor_day):>11s} "
              f"{str(pred):>11s} {str(truth):>11s} "
              f"{('%+d' % err) if err is not None else '-':>5s}")
        rows.append({**{k: g[k] for k in ("episode_id", "agent")},
                     "routing_tag": a.tag,
                     "descriptor_snapshot": snapshot,
                     "anchor_day": anchor_day, "anchor": w.get("anchor"),
                     "predicted": pred, "truth": truth, "error_days": err,
                     "last_nonmatching_day": w.get("last_nonmatching_day"),
                     "n_seeds": len(window["seed_days"]),
                     "n_positive": sum(s["route"] == "positive"
                                       for s in window["seed_days"]),
                     "index_days": w.get("index_days"),
                     "index_earliest": w.get("index_earliest"),
                     "note": w.get("activity_start_note"),
                     "walk_error": w.get("error")})

    if a.dry or not rows:
        return
    errs = [abs(r["error_days"]) for r in rows if r["error_days"] is not None]
    if errs:
        errs_sorted = sorted(errs)
        print(f"\n  n={len(errs)}  exact={sum(1 for e in errs if e == 0)}  "
              f"within 3d={sum(1 for e in errs if e <= 3)}  "
              f"median |err|={errs_sorted[len(errs_sorted)//2]}d  "
              f"max={max(errs)}d")
    print(f"  spend ${spend:.2f}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"  wrote {len(rows)} -> {OUT}")


if __name__ == "__main__":
    main()
