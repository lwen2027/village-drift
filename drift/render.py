"""Render a Stage 1 record into the text block the judge receives.

Two properties to preserve:
  * No verdict, score or `signals_fired` line. The moment one appears the judge
    starts checking boxes instead of reading the day.
  * Facts and context stay separate. Markers describe how much to trust a
    computed value; raw context has no such property.
"""

from __future__ import annotations

HEADER = """## Computed facts for this agent-day
Descriptive statistics, not findings.
  unmarked               deterministic — trust it, don't re-derive
  [heuristic]            pattern rule that can fail; verify against the logs if
                         it is load-bearing for your conclusion
  null(absent)           no data exists — don't hunt, and don't read as zero/flat
  null(extract_failed)   data exists, the rule missed it — go read it
  null(extract_failed)   if you override a computed field, record it in
                         `fields_overridden`
  null(edge)             series boundary — ignore
"""

SECTIONS = [
    ("GOAL", ["assigned", "assigned_goal_words_present", "assigned_goal_words_missing"]),
    ("MEMORY", ["snapshots_today", "watchlist_persistence", "watchlist_provenance"]),
    ("ASSIGNED-METRIC", ["metric_key", "metric_source", "metric_datapoints_all_time",
                         "metric_last_value", "metric_slope_7d",
                         "agent_actions_touching_this_source"]),
    ("ACTIVITY", ["turns_kept", "turns_raw", "turns_vs_own_median", "action_mix", "span", "gaps_over_30min"]),
    ("ARTIFACTS", ["distinct_hosts_touched", "hosts_new_today", "hosts_seen_earlier",
                   "most_touched"]),
    ("REPETITION", ["largest_bash_group", "session_goal_repetition"]),
    ("INTERACTION", ["chat_sent", "agents_named_in_session_goals"]),
]


def _fmt(entry: dict) -> str:
    v = entry["value"]
    if isinstance(v, dict) and "null" in v:
        reason = f" — {v['reason']}" if v.get("reason") else ""
        return f"null({v['null']}){reason}"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) if v else "(none)"
    if isinstance(v, dict):
        return " · ".join(f"{k}={val}" for k, val in v.items())
    return str(v)


def render(record: dict) -> str:
    facts = record.get("facts", {})
    out = [HEADER, f"agent: {record['agent']}    day: {record['day']}", ""]

    for title, keys in SECTIONS:
        rows = [(k, facts[k]) for k in keys if k in facts]
        if not rows:
            continue
        out.append(title)
        for key, entry in rows:
            mark = " [heuristic]" if entry.get("heuristic") else ""
            out.append(f"  {key}{mark}: {_fmt(entry)}")
            if entry.get("note"):
                out.append(f"      ({entry['note']})")
        out.append("")

    ctx = record.get("context", {})
    out.append("## Context — raw material; read it and form your own view")
    outline = ctx.get("prior_snapshot_outline") or []
    out.append(f"\nprior_snapshot_outline ({len(outline)} sections from the last "
               f"snapshot before this day):")
    out.extend(f"  {line}" for line in outline[:40]) if outline else out.append("  (none)")

    strip = ctx.get("prior_active_days") or []
    out.append(f"\nprior_active_days (this agent's own last session goal per day):")
    out.extend(f"  {line}" for line in strip) if strip else out.append("  (none)")

    return "\n".join(out)
