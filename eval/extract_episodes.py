"""Split the Stage-2 material out of the Stage-1 tables.

Stage 1 and Stage 2 answer different questions and need different eval sets:

  STAGE 1   unit: agent-day.  "Was this day spent on the assigned goal?"
            evidence: that day's digest.  ~15 min/day, 100 days.
            tables: eval_100.jsonl + verification.jsonl

  STAGE 2   unit: episode.    "When did this start, why, what was available,
                               was it corrected?"
            evidence: a multi-day trace.  hours each, ~10-15 of them.
            table: episodes.jsonl

Mixing them is what produced the conflation this repo kept hitting: multi-day
investigation leaking into a single-day verdict. 23 of the 50 turning points
recorded during the Stage-1 audits are on a different day than the row they
were attached to — Stage-2 material filed in a Stage-1 table.

ONSET vs TIMELINE START. `onset` is when the DRIFT began; the timeline often
starts earlier, when the ACTIVITY began while it was still on-goal. Claude Haiku
4.5's onset is 2026-07-06 16:06, the moment a three-week-old keystroke marathon
got relabelled as wellbeing work, while the timeline opens 2026-06-15 when that
marathon began legitimately under a games goal. Both are right; they answer
different questions.

An episode is a DRIFT episode, so only rows with is_drift true seed one. Cases
3, 5 and 6 each carry one cross-day point, but it is the operator's room-goal
announcement, which is a property of the goal and already lives in `goals`.

    python3 eval/extract_episodes.py --dry-run
    python3 eval/extract_episodes.py
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, "eval_100.jsonl")
AUDIT = os.path.join(HERE, "verification.jsonl")
EPISODES = os.path.join(HERE, "episodes.jsonl")


def _load(p):
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _write(p, rows):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
    with os.fdopen(fd, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def slug(agent: str, day: str) -> str:
    return agent.lower().replace(" ", "-").replace(".", "-") + "__" + day


def build(dry=False):
    labels = _load(LABELS)
    audits = {(a["agent"], a["day"]): a for a in _load(AUDIT)}
    episodes, kept_audits = [], []

    for a in _load(AUDIT):
        key = (a["agent"], a["day"])
        row = next(r for r in labels if (r["agent"], r["day"]) == key)
        cross = [t for t in a["turning_points"] if t["ts"][:10] != row["day"]]
        same = [t for t in a["turning_points"] if t["ts"][:10] == row["day"]]
        corr = row.get("operator_corrections") or []

        # only a drift day seeds an episode
        if row.get("is_drift") is True and (cross or corr or row.get("drift_onset")):
            onset = row.get("drift_onset")
            episodes.append({
                "episode_id": slug(a["agent"], (onset or row["day"])[:10]),
                "agent": a["agent"],
                "onset": onset,
                "onset_traced": onset is not None,
                "seed_day": row["day"],          # the Stage-1 detection that anchored it
                # every day actually examined, not just the seed: the timeline
                # routinely reaches back weeks before it
                "days_observed": sorted({row["day"]} |
                                        {t["ts"][:10] for t in cross + same}),
                # ⚠ this is the goal on the SEED DAY, not necessarily at onset.
                # They coincide when the episode starts inside one goal period
                # and diverge when the onset predates a goal change — which is
                # the shape several of these have. Stage 2 should resolve the
                # goal at onset and overwrite.
                "goal": (row.get("goals") or [None])[0],
                "goal_at": "seed_day",
                "timeline": sorted(cross + same, key=lambda t: t["ts"]),
                "operator_corrections": corr,
                # left for the dedicated Stage-2 pass rather than half-filled here
                "mechanism": None,
                "available_levers": None,
                "evidence": [],
                "verified_by": a.get("verified_by"),
                "verified_at": a.get("verified_at"),
            })
            a = {**a, "turning_points": same}    # Stage-1 audit keeps same-day only
        kept_audits.append(a)

    # strip the episode-level fields from the day-scoped label table
    for r in labels:
        r.pop("drift_onset", None)
        r.pop("operator_corrections", None)

    if dry:
        print(f"would write {len(episodes)} episodes:")
        for e in episodes:
            print(f"   {e['episode_id']:34s} onset={str(e['onset'])[:16]:17s} "
                  f"seed={e['seed_day']}  {len(e['timeline'])} timeline, "
                  f"{len(e['operator_corrections'])} corrections")
        moved = sum(len(a["turning_points"]) for a in _load(AUDIT)) - \
            sum(len(a["turning_points"]) for a in kept_audits)
        print(f"\n   {moved} turning points leave verification.jsonl")
        print(f"   drift_onset / operator_corrections removed from {len(labels)} label rows")
        return episodes

    _write(EPISODES, episodes)
    _write(AUDIT, kept_audits)
    _write(LABELS, labels)
    print(f"wrote {len(episodes)} episodes -> {EPISODES}")
    print(f"verification.jsonl now holds same-day evidence only")
    print(f"eval_100.jsonl is day-scoped: {len(labels)} rows")
    return episodes


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    build(p.parse_args().dry_run)
