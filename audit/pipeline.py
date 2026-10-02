"""Stage 1 -> Stage 2. The selection rule, and the windows it produces.

This is the connective tissue that did not exist. Stage 1 wrote verdicts to
arena_runs/ and Stage 2 read episodes_mechanical.jsonl, and nothing joined
them: the selection rule lived only as prose in README.md, and the window
builder lived only in a conversation. Both are here now.

    python3 audit/pipeline.py days      # the rule's output: which agent-days
    python3 audit/pipeline.py windows   # those days grouped into windows
    python3 audit/pipeline.py --write   # -> tables/stage2/windows.jsonl
    python3 audit/pipeline.py validate --tag B-full --rows full

`validate` is the paid-Stage-2 gate. The named row set is the manifest of
active agent-days Stage 1 was expected to process; without that manifest an
unprocessed day is indistinguishable from an inactive one. It reports exact
missing dates and exits nonzero until runs, descriptors, blocks and raw
evidence are complete.

NOT NAMED select.py. It was, for about a minute: audit/ goes on sys.path
ahead of the stdlib, so audit/select.py shadowed the `select` module and
broke `import socket` three levels down inside urllib.

TWO STEPS, AND THEY ANSWER DIFFERENT QUESTIONS.

`select_seed_days` applies the rule: send every drift verdict, plus every
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
from drift import config  # noqa: E402

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


def _drift_value(value):
    """Normalise the judge's tri-state verdict without truthiness coercion."""
    if value is True or value is False:
        return value
    if isinstance(value, str) and value.lower() == "undefined":
        return None
    raise ValueError(f"invalid Stage-1 is_drift value: {value!r}")


def verdicts(tag="B-peerfix"):
    """Every Stage-1 verdict for `tag`, keyed (agent, day).

    Reads arena_runs/ directly rather than a scored table. Open-goal verdicts
    remain present as `is_drift=None` for provenance, but selection excludes
    them: drift is undefined under an unconstrained goal.
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
        drift = _drift_value(v["is_drift"])
        out[(r.get("agent"), r.get("day"))] = {
            "is_drift": drift,
            "confidence": float(v["confidence"]),
            "day_activity": v.get("day_activity"),
            "decisive_evidence": v.get("decisive_evidence"),
        }
    return out


def select_seed_days(vs, cut=CONFIDENCE_CUT):
    """Apply the rule. Returns the subset of `vs` that goes to Stage 2.

    Deliberately has no global state: whether a day is sent depends only on
    that day's own verdict. A percentile cut would need the whole corpus
    sorted before it could decide anything, and would re-decide every past
    day each time a new one arrived.
    """
    return {k: v for k, v in vs.items()
            if (v["is_drift"] is True
                or (v["is_drift"] is False and v["confidence"] < cut))}


def routing_seed(day, verdict):
    """The auditable handoff record for one selected day."""
    return {
        "day": day,
        "stage1_verdict": verdict["is_drift"],
        "confidence": verdict["confidence"],
        "route": ("positive" if verdict["is_drift"] is True
                  else "low_confidence"),
    }


def windows(sel, vs, lookback=LOOKBACK_DAYS, lookahead=LOOKAHEAD_DAYS,
            max_gap=MAX_GAP_DAYS, cut=CONFIDENCE_CUT):
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
            seed_days = []
            for day in sorted(run):
                verdict = vs[(agent, day)]
                seed_days.append(routing_seed(day, verdict))
            out.append({
                "window_id": f"{R._safe(agent)}__{run[0]}",
                "agent": agent,
                "back_to": lo,
                "forward_to": hi,
                # Routing provenance stays on the handoff record for audits.
                # Stage 2 exposes only the dates to the judge: the route is
                # why a day was sent, not evidence that drift occurred.
                "seed_days": seed_days,
                "goal": None,
                "method": (f"stage1 selection rule conf<{cut}, "
                           f"grouped at max_gap={max_gap}, "
                           f"-{lookback}/+{lookahead} days"),
            })
    return out


def _rows_path(value):
    """Resolve the same named row sets accepted by eval/arena.py."""
    if os.path.exists(value):
        return value
    if value == "arena_40":
        return os.path.join(R.STAGE1, "arena_40.jsonl")
    candidate = os.path.join(R.STAGE1, f"rowset_{value}.jsonl")
    if os.path.exists(candidate):
        return candidate
    raise ValueError(f"no expected row set {value!r}: tried {candidate}")


def _expected_rows(path):
    rows = [json.loads(line) for line in open(path) if line.strip()]
    out = {}
    for row in rows:
        key = (row.get("agent"), row.get("day"))
        if not all(key):
            raise ValueError(f"expected row lacks agent/day: {row!r}")
        if key in out:
            raise ValueError(f"duplicate expected row: {key[0]} {key[1]}")
        _date(key[1])
        out[key] = row
    return out


def _stage1_run_states(tag):
    """Every cached record for one exact run tag, including failures."""
    out = {}
    pattern = os.path.join(R.RUNS, f"{tag}__*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            rec = json.load(open(path))
        except Exception as exc:  # noqa: BLE001
            out[(None, path)] = {"state": "unreadable", "path": path,
                                 "detail": str(exc), "record": {}}
            continue
        key = (rec.get("agent"), rec.get("day"))
        state, detail = "usable", None
        if not all(key):
            state, detail = "invalid_record", "missing agent or day"
        elif rec.get("error"):
            state, detail = "error", str(rec["error"])
        elif any((call.get("usage") or {}).get("stub")
                 for call in (rec.get("calls") or [])):
            state, detail = "stub", "stub usage marker present"
        else:
            activity = (rec.get("verdict") or {}).get("day_activity")
            if not isinstance(activity, list) or not activity:
                state = "invalid_descriptor"
                detail = f"day_activity is {type(activity).__name__}, not a nonempty list"
        out[key] = {"state": state, "path": path,
                    "detail": detail, "record": rec}
    return out


def stage1_readiness(tag, expected):
    """Return a structured readiness audit for the Stage-1 -> Stage-2 gate."""
    import stage2 as S

    states = _stage1_run_states(tag)
    usable = {key: value for key, value in states.items()
              if value["state"] == "usable"}
    expected_keys = set(expected)

    issues = collections.defaultdict(list)
    for key in sorted(expected_keys):
        state = states.get(key)
        if state is None:
            issues["missing_run"].append((key, None))
        elif state["state"] != "usable":
            issues[state["state"]].append((key, state.get("detail")))

        agent, day = key
        block = config.find_artifact("blockrec", agent, day)
        if not block:
            issues["missing_block"].append((key, None))
        else:
            try:
                version = (json.load(open(block)) or {}).get("feature_version")
            except Exception as exc:  # noqa: BLE001
                issues["unreadable_block"].append((key, str(exc)))
            else:
                if version != config.FEATURE_VERSION:
                    issues["stale_block"].append(
                        (key, f"has {version!r}, need {config.FEATURE_VERSION!r}"))
        if not config.find_artifact("raw", agent, day):
            issues["missing_raw"].append((key, None))

    descriptors = {
        key: (value["record"].get("verdict") or {}).get("day_activity")
        for key, value in usable.items() if key in expected_keys
    }
    vs = verdicts(tag)
    ws = windows(select_seed_days(vs), vs) if vs else []
    window_reports = []
    by_agent = collections.defaultdict(list)
    for agent, day in expected_keys:
        by_agent[agent].append(day)
    for days in by_agent.values():
        days.sort()

    for window in ws:
        agent = window["agent"]
        seeds = [seed["day"] for seed in window["seed_days"]]
        anchor = next((day for day in seeds if (agent, day) in descriptors),
                      seeds[0])
        preceding = [(day, []) for day in by_agent.get(agent, [])
                     if day <= anchor]
        expected_walk = [day for day, _ in S._stop_at_gap(preceding)][-S.WALK_LOOKBACK:]
        missing_descriptors = [day for day in expected_walk
                               if (agent, day) not in descriptors]

        span_expected = [day for day in by_agent.get(agent, [])
                         if window["back_to"] <= day <= window["forward_to"]]
        prior_evidence = [day for day in span_expected if day <= seeds[0]]
        missing_blocks = [day for day in span_expected
                          if not config.find_artifact("blockrec", agent, day)]
        missing_evidence = [day for day in span_expected
                            if not (config.find_artifact("raw", agent, day)
                                    or config.find_artifact("digest", agent, day))]
        actual_rows = [(day, descriptors[(agent, day)])
                       for day in expected_walk
                       if (agent, day) in descriptors]
        contiguous = S._stop_at_gap(actual_rows)
        window_reports.append({
            "window_id": window["window_id"],
            "agent": agent,
            "anchor": anchor,
            "expected_walk_days": len(expected_walk),
            "contiguous_descriptor_days": len(contiguous),
            "walk_history_limited": len(expected_walk) < 2,
            "explain_history_limited": len(prior_evidence) < 2,
            "missing_descriptors": missing_descriptors,
            "missing_blocks": missing_blocks,
            "missing_evidence": missing_evidence,
        })

    ready = not any(issues.values()) and all(
        not (report["missing_descriptors"] or report["missing_blocks"]
             or report["missing_evidence"] or report["walk_history_limited"]
             or report["explain_history_limited"])
        for report in window_reports
    )
    return {
        "ready": ready,
        "tag": tag,
        "expected_days": len(expected_keys),
        "usable_runs": len(usable.keys() & expected_keys),
        "issues": dict(issues),
        "windows": window_reports,
    }


def _print_items(label, items, limit):
    if not items:
        return
    print(f"  {label}: {len(items)}")
    shown = items if limit == 0 else items[:limit]
    for (agent, day), detail in shown:
        print(f"    {day}  {agent}" + (f"  -- {detail}" if detail else ""))
    if len(shown) < len(items):
        print(f"    ... {len(items) - len(shown)} more; pass --details 0 for all")


def print_readiness(report, details=50):
    verdict = "READY" if report["ready"] else "NOT READY"
    print(f"Stage 1 -> Stage 2: {verdict}")
    print(f"  tag={report['tag']}  expected={report['expected_days']}  "
          f"usable={report['usable_runs']}  windows={len(report['windows'])}")
    order = ["missing_run", "error", "stub", "invalid_descriptor",
             "invalid_record", "unreadable", "missing_block",
             "unreadable_block", "stale_block", "missing_raw"]
    for name in order:
        _print_items(name.replace("_", " "),
                     report["issues"].get(name, []), details)

    bad = [window for window in report["windows"]
           if window["missing_descriptors"] or window["missing_blocks"]
           or window["missing_evidence"] or window["walk_history_limited"]
           or window["explain_history_limited"]]
    if bad:
        print(f"  windows with coverage gaps: {len(bad)}")
    shown_bad = bad if details == 0 else bad[:details]
    for window in shown_bad:
        print(f"    {window['window_id']}  anchor={window['anchor']}  "
              f"descriptors={window['contiguous_descriptor_days']}/"
              f"{window['expected_walk_days']}")
        if window["walk_history_limited"]:
            print("      expected row set supplies fewer than 2 contiguous "
                  "active days before the anchor")
        if window["explain_history_limited"]:
            print("      expected row set supplies fewer than 2 active "
                  "evidence days from back_to through the first seed")
        for key in ("missing_descriptors", "missing_blocks", "missing_evidence"):
            days = window[key]
            if days:
                rendered = days if details == 0 else days[:details]
                suffix = (f" ... +{len(days) - len(rendered)}" if len(rendered) < len(days)
                          else "")
                print(f"      {key.replace('_', ' ')}: {', '.join(rendered)}{suffix}")
    if len(shown_bad) < len(bad):
        print(f"    ... {len(bad) - len(shown_bad)} more windows; "
              "pass --details 0 for all")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["days", "windows", "validate"], nargs="?",
                   default="windows")
    p.add_argument("--tag", default="B-peerfix")
    p.add_argument("--rows", help="expected row-set name or JSONL path; required by validate")
    p.add_argument("--details", type=int, default=50,
                   help="maximum dates per validation category; 0 prints all")
    p.add_argument("--cut", type=float, default=CONFIDENCE_CUT)
    p.add_argument("--write", action="store_true")
    a = p.parse_args()

    if a.cmd == "validate":
        if not a.rows:
            raise SystemExit("validate requires --rows <row-set name or JSONL path>")
        expected_path = _rows_path(a.rows)
        report = stage1_readiness(a.tag, _expected_rows(expected_path))
        print(f"  expected row set: {expected_path}")
        print_readiness(report, details=a.details)
        return 0 if report["ready"] else 1

    vs = verdicts(a.tag)
    sel = select_seed_days(vs, a.cut)
    if not vs:
        raise SystemExit(f"no usable verdicts for tag {a.tag!r}")

    n_defined = sum(1 for v in vs.values() if v["is_drift"] is not None)
    n_drift = sum(1 for v in vs.values() if v["is_drift"] is True)
    print(f"  {len(vs)} verdicts   {n_drift} drift   "
          f"{len(sel)} selected ({100*len(sel)/max(1,n_defined):.0f}% of "
          f"{n_defined} defined) "
          f"at conf<{a.cut}")

    if a.cmd == "days":
        for (agent, day), v in sorted(sel.items()):
            print(f"    {day}  {agent:24s} "
                  f"{'drift' if v['is_drift'] else 'not  '} {v['confidence']:.2f}")
        return

    ws = windows(sel, vs, cut=a.cut)
    span = sum((_date(w["forward_to"]) - _date(w["back_to"])).days + 1
               for w in ws)
    print(f"  -> {len(ws)} windows, {span} calendar days spanned, "
          f"{span/max(1,len(ws)):.0f} per window")
    for w in ws[:10]:
        print(f"    {w['window_id']:34s} {w['back_to']} .. {w['forward_to']}"
              f"  seeds={len(w['seed_days'])}"
              f" positive={sum(s['route'] == 'positive' for s in w['seed_days'])}")
    if len(ws) > 10:
        print(f"    ... {len(ws)-10} more")

    if a.write:
        os.makedirs(STAGE2, exist_ok=True)
        with open(OUT, "w") as fh:
            for w in ws:
                fh.write(json.dumps(w, ensure_ascii=False) + "\n")
        print(f"  wrote {len(ws)} -> {OUT}")


if __name__ == "__main__":
    sys.exit(main() or 0)
