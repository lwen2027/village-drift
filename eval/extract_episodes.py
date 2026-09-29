"""Split the Stage-2 material out of the Stage-1 tables.

Stage 1 and Stage 2 answer different questions and need different eval sets:

  STAGE 1   unit: agent-day.  "Was this day spent on the assigned goal?"
            evidence: that day's digest.  ~15 min/day, 100 days.
            tables: eval_100.jsonl + verification.jsonl

  STAGE 2   unit: episode.    "When did this start, why, what was available,
                               was it corrected?"

            Three timestamps, two latencies, and neither latency is stored:
              goal.start     -> onset   how fast it drifted after assignment
              activity_start -> onset   how long the activity predates the drift
            The pair separates mechanisms. Haiku 4.5 is 7 minutes on the first
            and 21 days on the second: a RELABELLING of work that was
            legitimately on-goal under two previous goals. GPT-4.1 is 0 days on
            the second: the posture was there in its first message, so there
            was never an on-goal phase. It also dissolves a false reading —
            GPT-4.1's goal->onset looks like 13 days only because it joined the
            village 13 days into an existing goal.
            evidence: a multi-day trace.  hours each, ~10-15 of them.
            table: episodes.jsonl

Mixing them is what produced the conflation this repo kept hitting: multi-day
investigation leaking into a single-day verdict.

CORRECTED 2026-09-29. This docstring claimed "23 of the 50 turning points
recorded during the Stage-1 audits are on a different day than the row they
were attached to". Measured against the current verification.jsonl it is 3 of
40, across 13 of 100 audit rows. The claim was true of an earlier state of
the table and was not updated as the audits were completed and cleaned.

The correction inverts what it implies. It is NOT that Stage-2 material was
being filed in Stage-1 tables at scale — it is that the Stage-1 audits stayed
almost entirely inside their own day. That is still a reason to build Stage 2,
but a different one, and it has a consequence: Stage 1 supplies essentially NO
lower bound on activity_start. Do not expect turning_points to seed the
window.

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
TABLES = os.path.join(HERE, "tables")
LABELS = os.path.join(TABLES, "stage1", "eval_100.jsonl")
AUDIT = os.path.join(TABLES, "stage1", "verification.jsonl")
EPISODES = os.path.join(TABLES, "stage2", "episodes.jsonl")


def _load(p):
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _write(p, rows):
    # stage2/ may not exist yet — it is gitignored and created on first write.
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p))
    with os.fdopen(fd, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def slug(agent: str, day: str) -> str:
    return agent.lower().replace(" ", "-").replace(".", "-") + "__" + day


# Fields only a human/Stage-2 pass can fill. The extractor rebuilds an episode
# from the Stage-1 tables on every run, so without this it would silently wipe
# them — which it would have done to activity_start on the very next run.
#
# activity_start IS NOT DERIVED BY ANYTHING. It starts None and stays None
# until a human writes it, and it is the field that bounds how much history a
# Stage-2 pass must read — so the window cannot be sized without first doing
# the work that sizing the window was supposed to make affordable.
#
# The cheap way out, unbuilt: walk BACKWARD over session goals only. They run
# ~7,245 chars/agent-day against ~256,240 for the full activity, so a 21-day
# lookback is ~36K tokens rather than ~1.3M, and the activity the agent
# drifted TO is named in its own session goals on the days it was doing it.
# activity_start is then the earliest day it appears contiguously.
#
# That needs the full dump (~/Documents/ai-village, 4.7G). eval/raw holds only
# the 58 eval days and they are not contiguous — Claude Haiku 4.5, the worked
# example above, appears on 5 scattered days there, so the walk cannot be
# developed or tested against eval/raw alone.
STAGE2_FIELDS = ("activity_start", "activity_start_note", "onset", "onset_traced",
                 "mechanism", "available_levers", "evidence", "goal", "goal_at")


def build(dry=False):
    labels = _load(LABELS)
    existing = {e["episode_id"]: e for e in _load(EPISODES)}
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
                "activity_start": None,
                "activity_start_note": None,
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
            # never clobber work a Stage-2 pass has already done
            prior = existing.get(episodes[-1]["episode_id"], {})
            for f in STAGE2_FIELDS:
                if prior.get(f) not in (None, [], ""):
                    episodes[-1][f] = prior[f]
            a = {**a, "turning_points": same}    # Stage-1 audit keeps same-day only
        kept_audits.append(a)

    # strip the episode-level fields from the day-scoped label table
    for r in labels:
        r.pop("drift_onset", None)
        r.pop("operator_corrections", None)

    # One-shot migration, not an idempotent build: it reads drift_onset and
    # operator_corrections out of eval_100.jsonl and cross-day turning points
    # out of verification.jsonl, all of which it then removes. Running it twice
    # therefore finds nothing and would truncate episodes.jsonl to empty — which
    # it did once. Refuse rather than destroy.
    if len(episodes) < len(existing):
        raise SystemExit(
            f"refusing: would write {len(episodes)} episodes over {len(existing)} existing.\n"
            f"The Stage-1 tables no longer hold the source fields; this script has "
            f"already run. Edit episodes.jsonl directly, or restore the Stage-1 "
            f"fields first.")

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
