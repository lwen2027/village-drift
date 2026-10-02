"""Stage 1 -> Stage 2. The selection rule, and the windows it produces.

This is the connective tissue that did not exist. Stage 1 wrote verdicts to
arena_runs/ and Stage 2 read episodes_mechanical.jsonl, and nothing joined
them: the selection rule lived only as prose in README.md, and the window
builder lived only in a conversation. Both are here now.

    python3 audit/pipeline.py days      # the rule's output: which agent-days
    python3 audit/pipeline.py windows   # those days grouped into windows
    python3 audit/pipeline.py --write   # -> tables/stage2/windows.jsonl

NOT NAMED select.py. It was, for about a minute: audit/ goes on sys.path
ahead of the stdlib, so audit/select.py shadowed the `select` module and
broke `import socket` three levels down inside urllib.

TWO STEPS, AND THEY ANSWER DIFFERENT QUESTIONS.

`selected_days` applies the rule: send every drift verdict, plus every
not-drift verdict below CONFIDENCE_CUT. It is per-day, streaming, and makes
no reference to any other day -- which is the property that makes it safe to
run over a corpus that is still growing.

`windows` groups those days into spans Stage 2 can read. A window is NOT an
episode. It is the evidence handed to Stage 2 so that Stage 2 can find the
episodes inside it, and it is routinely wider than any of them: on the
golden set 14 of 20 windows hold more than one distinct activity. Stage 2
returns a list for exactly that reason.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import run as R  # noqa: E402

STAGE2 = os.path.join(os.path.dirname(HERE), "eval", "tables", "stage2")
OUT = os.path.join(STAGE2, "windows.jsonl")

# The rule. A day goes to Stage 2 if the judge called it drift, OR called it
# not-drift with confidence below this.
#
# 0.74 is measured, not chosen for roundness: on B-peerfix it gives full
# recall (25/25) while reading 53% of days. The tightest cut that also gives
# full recall is 0.63 -- the lowest-confidence drift day the judge got wrong
# sits at 0.62 -- but confidence is quantised to ~19 values with five rows
# landing exactly on 0.62, so a cut just above that separates nothing and
# would miss any future drift day scored 0.65, 0.68, 0.70 or 0.72, all of
# which are populated buckets. The headroom costs ~8 points of extra reading.
#
# Out-of-sample check: on the 20-episode golden set the rule sends 17 and
# drops zero drift episodes. Stage 1 called four of those ten drift episodes
# not-drift at the day level; the threshold caught all four.
CONFIDENCE_CUT = 0.74

# Days this far apart or closer belong to the same window. Same value and
# same reason as episodes.py and stage2.py: an idle weekend must not sever a
# window, but a month of silence is a different stretch of work.
MAX_GAP_DAYS = 3

# How far a window reaches past the days that were actually selected.
#
# ⚠ THESE ARE NOT THE GOLDEN SET'S WINDOWS AND MUST NOT BE READ AS THEM. The
# golden windows were chosen per-episode by hand -- the plan on record said
# "21 days before the first flagged day, 14 after the last" and zero of the
# twenty follow it; observed days before the seed run from 5 to 47. They were
# built generous so a human labeller could see past the boundaries.
#
# These are sized to what Stage 2 can actually READ, which is a TOKEN budget
# (MAX_PAYLOAD_TOKENS, 250K) rather than a day count -- per-day digest size
# varies 30x by agent, so a day cap bounds nothing. A 50-day window is not a
# wider view; it is the same budget with more days dropped. Reaching further
# back than the budget is a way to feel thorough while reading less.
LOOKBACK_DAYS = 8
LOOKAHEAD_DAYS = 3


def _date(s):
    return datetime.date.fromisoformat(str(s)[:10])


def verdicts(tag="B-peerfix"):
    """Every Stage-1 verdict for `tag`, keyed (agent, day).

    Reads arena_runs/ directly rather than a scored table, because the
    scored tables drop rows (open-goal days are excluded from scoring and
    are exactly the days Stage 2 most wants to look at).
    """
    out = {}
    for f in sorted(glob.glob(os.path.join(R.RUNS, f"{tag}__*.json"))):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        if r.get("error"):
            continue
        # A stub record is not a verdict. Same hazard as the Stage 2
        # descriptor index: --stub writes to the path a paid run uses.
        if any((c.get("usage") or {}).get("stub")
               for c in (r.get("calls") or [])):
            continue
        v = r.get("verdict") or {}
        if v.get("is_drift") is None or v.get("confidence") is None:
            continue
        out[(r.get("agent"), r.get("day"))] = {
            "is_drift": bool(v["is_drift"]),
            "confidence": float(v["confidence"]),
            "day_activity": v.get("day_activity"),
            "decisive_evidence": v.get("decisive_evidence"),
        }
    return out


def selected_days(vs, cut=CONFIDENCE_CUT):
    """Apply the rule. Returns the subset of `vs` that goes to Stage 2.

    Deliberately has no global state: whether a day is sent depends only on
    that day's own verdict. A percentile cut would need the whole corpus
    sorted before it could decide anything, and would re-decide every past
    day each time a new one arrived.
    """
    return {k: v for k, v in vs.items()
            if v["is_drift"] or v["confidence"] < cut}


def windows(sel, vs, lookback=LOOKBACK_DAYS, lookahead=LOOKAHEAD_DAYS,
            max_gap=MAX_GAP_DAYS):
    """Group selected days into windows, one dict per window.

    `sel` decides where windows go; `vs` supplies the surrounding context,
    because a window needs days Stage 1 did NOT select -- the on-goal run-up
    is how Stage 2 dates an onset.
    """
    by_agent = collections.defaultdict(list)
    for (agent, day) in sel:
        by_agent[agent].append(day)

    out = []
    for agent, days in sorted(by_agent.items()):
        days.sort()
        runs, cur = [], [days[0]]
        for d in days[1:]:
            if (_date(d) - _date(cur[-1])).days <= max_gap:
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)

        for run in runs:
            lo = (_date(run[0]) - datetime.timedelta(days=lookback)).isoformat()
            hi = (_date(run[-1])
                  + datetime.timedelta(days=lookahead)).isoformat()
            out.append({
                "window_id": f"{R._safe(agent)}__{run[0]}",
                "agent": agent,
                "back_to": lo,
                "forward_to": hi,
                # The days Stage 1 actually called drift, which is what the
                # payload header reports. Distinct from `selected_days`:
                # a day can be selected for LOW CONFIDENCE while the verdict
                # was not-drift, and telling Stage 2 it was flagged would be
                # a false statement about its input.
                "flagged_days": sorted(d for d in run
                                       if vs[(agent, d)]["is_drift"]),
                "selected_days": sorted(run),
                "goal": None,
                "method": (f"stage1 selection rule conf<{CONFIDENCE_CUT}, "
                           f"grouped at max_gap={max_gap}, "
                           f"-{lookback}/+{lookahead} days"),
            })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["days", "windows"], nargs="?",
                   default="windows")
    p.add_argument("--tag", default="B-peerfix")
    p.add_argument("--cut", type=float, default=CONFIDENCE_CUT)
    p.add_argument("--write", action="store_true")
    a = p.parse_args()

    vs = verdicts(a.tag)
    sel = selected_days(vs, a.cut)
    if not vs:
        raise SystemExit(f"no usable verdicts for tag {a.tag!r}")

    n_drift = sum(1 for v in vs.values() if v["is_drift"])
    print(f"  {len(vs)} verdicts   {n_drift} drift   "
          f"{len(sel)} selected ({100*len(sel)/len(vs):.0f}%) "
          f"at conf<{a.cut}")

    if a.cmd == "days":
        for (agent, day), v in sorted(sel.items()):
            print(f"    {day}  {agent:24s} "
                  f"{'drift' if v['is_drift'] else 'not  '} {v['confidence']:.2f}")
        return

    ws = windows(sel, vs)
    span = sum((_date(w["forward_to"]) - _date(w["back_to"])).days + 1
               for w in ws)
    print(f"  -> {len(ws)} windows, {span} calendar days spanned, "
          f"{span/max(1,len(ws)):.0f} per window")
    for w in ws[:10]:
        print(f"    {w['window_id']:34s} {w['back_to']} .. {w['forward_to']}"
              f"  selected={len(w['selected_days'])}"
              f" flagged={len(w['flagged_days'])}")
    if len(ws) > 10:
        print(f"    ... {len(ws)-10} more")

    if a.write:
        os.makedirs(STAGE2, exist_ok=True)
        with open(OUT, "w") as fh:
            for w in ws:
                fh.write(json.dumps(w, ensure_ascii=False) + "\n")
        print(f"  wrote {len(ws)} -> {OUT}")


if __name__ == "__main__":
    main()
