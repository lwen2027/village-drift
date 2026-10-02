"""Run Stage 2's explain call on the golden windows and score it.

    python3 eval/stage2_eval.py --dry            # payload sizes, no spend
    python3 eval/stage2_eval.py --limit 2        # plumbing check, ~$2
    python3 eval/stage2_eval.py                  # the baseline

ISOLATES THE EXPLAIN CALL. The window comes from the golden label, so the
backward walk is not involved and needs no Stage-1 coverage. That is
deliberate: the walk is unvalidated, and with a bad window a bad judgement
and a bad window score identically.

It also makes the baseline CHEAP. The explain call needs digests (on disk),
the block-stats strip (built by prep, free) and a window (in the label).
Stage 1's day_activity -- the expensive part -- is the walk's input only.

WHAT REACHES THE JUDGE, AND WHAT DOES NOT. From the label: `agent` and the
window bounds. Nothing else. Not `activity`, not `q1_activity_start`, not
`q2_onset`, not `mechanism`, not `separate_episodes_found`. Window bounds
are not an answer: across the set `back_to` never equals the activity start
and precedes it by 4 days to 6 weeks.

`flagged_days` comes from STAGE 1's own verdicts on the seed days, not from
the human labels. Production tells Stage 2 which days Stage 1 flagged, so
using Stage 1 is faithful; using the verified days would hand it a cleaner
input than it will ever get, and would quietly leak the answer on negatives.

SCORING. The label is one episode; Stage 2 returns a list. A window holds
more than one episode in 14 of 20 cases, so a prediction is counted against
the LABELLED episode only -- an extra episode is neither credited nor
penalised here, because `separate_episodes_found` shows most of them are
real. Precision over "did it claim drift in a window whose label says none"
is the number this is for.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "audit"))
sys.path.insert(0, os.path.dirname(HERE))

import run as R        # noqa: E402
import stage2 as S     # noqa: E402
import pipeline as P   # noqa: E402

LABELS = os.path.join(HERE, "tables", "stage2", "labels")
OUT = os.path.join(HERE, "tables", "stage2", "stage2_eval.jsonl")


def _d(s):
    return datetime.date.fromisoformat(str(s)[:10])


def cases():
    """Golden episodes as (input, truth). The input half is what may be sent."""
    out = []
    for f in sorted(glob.glob(os.path.join(LABELS, "*.json"))):
        b = os.path.basename(f)
        if b.startswith("_") or "chunk" in b:
            continue
        d = json.load(open(f))
        w = d.get("window") or {}
        onset = (d.get("q2_onset") or {}).get("value")
        out.append({
            "episode_id": d["episode_id"],
            "agent": d["agent"],
            "window": {"back_to": w.get("back_to"),
                       "forward_to": w.get("forward_to")},
            "seed_days": sorted(d.get("seed_days_covered")
                                or [d.get("seed_day")]),
            "truth": {
                "is_drift": bool(onset),
                "onset": onset,
                "activity_start": (d.get("q1_activity_start") or {}).get("value"),
                "mechanism_shape": None,
                "activity": (d.get("activity") or {}).get("description"),
            },
        })
    return out


def stage1_flagged(agent, days, vs):
    """Which of `days` Stage 1 called drift, and which the rule selected."""
    flagged, sel = [], []
    for day in days:
        v = vs.get((agent, day))
        if not v:
            continue
        if v["is_drift"]:
            flagged.append(day)
        if v["is_drift"] or v["confidence"] < P.CONFIDENCE_CUT:
            sel.append(day)
    return sorted(flagged), sorted(sel)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--only", help="substring filter on episode_id")
    p.add_argument("--tag", default="B-peerfix")
    a = p.parse_args()

    vs = P.verdicts(a.tag)
    runs = S._run_index()
    cs = cases()
    if a.only:
        pats = [x.strip() for x in a.only.split(",") if x.strip()]
        cs = [c for c in cs if any(p in c["episode_id"] for p in pats)]
    # Only what the selection rule would actually send. 3 of 20 are dropped
    # by Stage 1 and would never reach Stage 2 in production.
    kept = []
    for c in cs:
        fl, sel = stage1_flagged(c["agent"], c["seed_days"], vs)
        if not sel:
            continue
        c["flagged_days"], c["selected_days"] = fl, sel
        kept.append(c)
    dropped = len(cs) - len(kept)
    if a.limit:
        kept = kept[:a.limit]

    print(f"  {len(kept)} episodes ({dropped} dropped by the selection rule)")
    rows, spend = [], 0.0
    sysmsg = R.prompt("stage2", check=False)

    for c in kept:
        ep = {"episode_id": c["episode_id"], "agent": c["agent"],
              "onset": (c["flagged_days"] or c["selected_days"])[0],
              "flagged_days": c["flagged_days"],
              "selected_days": c["selected_days"],
              "window": c["window"], "goal": None}
        if a.dry:
            txt, prov = S.build_payload(ep, None)
            est = int((len(sysmsg) + len(txt)) / 1.9)
            print(f"  {c['episode_id'][:30]:32s} {prov['days_in_span']:>3}d span "
                  f"{prov['days_read']:>3} read  est {est:>8,} tok"
                  f"{'  *** OVER GUARD ***' if est > R.MAX_INPUT_TOKENS else ''}")
            continue

        # run_episode, not explain() directly -- otherwise the eval skips
        # pass 2 and measures a pipeline nobody runs.
        rec = S.run_episode(ep, stub=False, runs=runs)
        spend += S.cost(rec)
        eps = rec.get("episodes") or []
        e = {"refused": rec.get("stop_reason") == "refusal",
             "stop_reason": rec.get("stop_reason"),
             "raw": rec.get("explain_raw"),
             "salvaged": rec.get("explain_salvaged"),
             "usage": {}, "provenance": rec.get("provenance")}
        v = {"examined": rec.get("examined"),
             "examined_note": rec.get("examined_note"),
             "episodes": eps}
        # NO ANSWER IS NOT A NEGATIVE ANSWER. A refusal, a length stop or an
        # unparseable reply all arrive as "zero episodes", and scoring them
        # as "found no drift" silently credits the judge with a verdict it
        # never gave -- and on a drift episode, silently counts a
        # non-answer as a miss. Measured: one of the first two calls came
        # back stop_reason=refusal and scored as a false negative.
        usable = (not e.get("refused") and e["raw"] is None
                  and v.get("examined") is not None)
        pred_drift = len(eps) > 0
        if not usable:
            tag = ("REFUSED" if e.get("refused")
                   else f"NO-ANSWER({e.get('stop_reason') or 'unparsed'})")
            print(f"  {tag:8s} {c['episode_id'][:30]:32s} — excluded from scoring")
        else:
            tag = "ok " if pred_drift == c["truth"]["is_drift"] else "MISS"
            print(f"  {tag:8s} {c['episode_id'][:30]:32s} "
                  f"truth={'drift' if c['truth']['is_drift'] else 'not  '} "
                  f"pred={len(eps)} episode(s)")
        rows.append({"episode_id": c["episode_id"], "agent": c["agent"],
                     "truth": c["truth"], "flagged_days": c["flagged_days"],
                     "usable": usable, "refused": e.get("refused"),
                     "stop_reason": e.get("stop_reason"),
                     "examined": v.get("examined"),
                     "examined_note": v.get("examined_note"),
                     "n_pred": len(eps), "pred_drift": pred_drift,
                     "needed_walk": rec.get("needed_walk"),
                     "walk": rec.get("walk"),
                     "episodes": eps, "provenance": e["provenance"],
                     "salvaged": e["salvaged"], "raw": e["raw"]})

    if a.dry or not rows:
        return
    scored = [r for r in rows if r["usable"]]
    unusable = [r for r in rows if not r["usable"]]
    tp = sum(1 for r in scored if r["pred_drift"] and r["truth"]["is_drift"])
    fp = sum(1 for r in scored if r["pred_drift"] and not r["truth"]["is_drift"])
    fn = sum(1 for r in scored if not r["pred_drift"] and r["truth"]["is_drift"])
    tn = sum(1 for r in scored if not r["pred_drift"] and not r["truth"]["is_drift"])
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    if unusable:
        print(f"\n  {len(unusable)} of {len(rows)} gave NO ANSWER and are "
              f"excluded: " + ", ".join(
                  f"{r['episode_id']}({r['stop_reason'] or 'unparsed'})"
                  for r in unusable))
    print(f"\n  scored {len(scored)}   TP {tp}  FP {fp}  FN {fn}  TN {tn}")
    print(f"  precision {prec if prec is None else round(prec, 2)}   "
          f"recall {rec if rec is None else round(rec, 2)}")
    print(f"  spend ${spend:.2f}")
    with open(OUT, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"  wrote {len(rows)} -> {OUT}")


if __name__ == "__main__":
    main()
