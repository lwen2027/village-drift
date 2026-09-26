"""Stage 1 driver: agent-major precompute, day-major emission.

Ordering matters twice, in opposite directions:

  * PRECOMPUTE is agent-major — rolling windows need each agent's whole series.
  * The LLM SWEEP must be day-major — the ~100k shared village transcript caches
    across the agents within a day. Agent-major separates those calls and the
    cache expires, costing ~23x on the shared block.

So records are written day-major even though they are computed agent-major.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta

from . import config, features as F, load, rooms as R
from .features import Block, Field, Null


def _jsonable(value):
    if isinstance(value, Null):
        return {"null": value.kind, "reason": value.reason}
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def block_to_record(block: Block) -> dict:
    return {
        "agent": block.agent,
        "day": block.day,
        "feature_version": config.FEATURE_VERSION,
        "facts": {
            k: {
                "value": _jsonable(f.value),
                **({"heuristic": True} if f.heuristic else {}),
                **({"note": f.note} if f.note else {}),
            }
            for k, f in block.facts.items()
        },
        "context": block.context,
    }


def _assigned_goals(agent_id, lo: str, hi: str, agent_goals, village_goals) -> list:
    """Every goal in force between this day's first and last turn.

    Resolved against TIMESTAMPS, not the date. Two goals can match the same
    date — on 2026-06-23 the village goal switched at 14:38 — and picking by
    date alone returns whichever the scan reaches first, which is the OUTGOING
    goal. Individual goals win over village goals; the result is chronological
    and is almost always length 1 (4 of 4,103 agent-days straddle a change).
    """
    def covering(rows, key):
        out = []
        for r in rows:
            start = str(r.get("start_time") or "")
            end = str(r.get("end_time") or "") or None
            if start <= hi and (end is None or end >= lo):
                # village_goals stores its text under "goal", agent_goals "name"
                out.append({"text": r.get(key) or r.get("goal") or r.get("name"),
                            "start": r.get("start_time"), "end": r.get("end_time")})
        return sorted(out, key=lambda g: str(g["start"]))

    mine = [r for r in agent_goals if r.get("agent_id") == agent_id]
    return covering(mine, "name") or covering(village_goals, "goal")


def _goal_change_days(agent_id, agent_goals, village_goals) -> list[str]:
    """Dates on which the assignment changed, for this agent."""
    d = {str(r["start_time"])[:10] for r in village_goals if r.get("start_time")}
    d |= {str(r["start_time"])[:10] for r in agent_goals
          if r.get("agent_id") == agent_id and r.get("start_time")}
    return sorted(d)


def build(start: str | None = None, end: str | None = None, verbose=True) -> list[dict]:
    log = print if verbose else (lambda *a, **k: None)

    # Load a lookback buffer so day 1 of a partial run has history; emit only
    # the requested range.
    load_start = start
    if start:
        load_start = (
            date.fromisoformat(start) - timedelta(days=config.LOOKBACK_DAYS)
        ).isoformat()
        log(f"lookback: loading from {load_start} (emitting from {start})")

    log("loading agents/goals…")
    agents = load.load_agents()
    agent_goals, village_goals = load.load_goals()
    short_names = F.build_short_names(sorted(agents.values()))

    log("loading sessions…")
    sessions_by_id, sessions_by_agent_day = load.load_sessions(load_start, end)
    log(f"  {len(sessions_by_id):,} sessions, {len(sessions_by_agent_day):,} agent-days")

    log("loading turns (large)…")
    turns = load.load_turns(sessions_by_id, load_start, end)

    log("loading memory (large)…")
    memory = load.load_memory_snapshots(load_start, end)

    log("loading chat…")
    chat = load.load_chat(load_start, end)

    # Room-scoped goals. village_goals records ONE goal per period, but between
    # 2026-03-16 and 2026-07-06 #best and #rest were given different goals and
    # were access-isolated. village_goals tracks #best, so ~780 #rest agent-days
    # would otherwise be scored against a goal they were never given.
    room_names = {r["id"]: r["name"] for r in load._rows("chat_rooms.jsonl.gz")}
    observed = R.observed_rooms(load._rows("chat_messages.jsonl.gz"),
                                {i: n for i, n in agents.items()}, room_names)

    metrics = load.load_metrics()
    log(f"  metric series: {len(metrics)} (agent, key) pairs"
        if metrics else "  metric series: none (run scripts/pull_metrics.py)")
    metric_key_for = {}
    for (agent_name, key) in metrics:
        metric_key_for.setdefault(agent_name, key)

    # ---- agent-major precompute -------------------------------------------
    # An agent-day exists if the agent had a session OR any turn that day.
    # Turns alone matter because a session opened at 23:58 produces turns on
    # the following day with no session row of its own.
    days_by_agent: dict[str, set[str]] = defaultdict(set)
    for (aid, day) in sessions_by_agent_day:
        days_by_agent[aid].add(day)
    for (aid, day) in turns:
        days_by_agent[aid].add(day)
    days_by_agent = {aid: sorted(ds) for aid, ds in days_by_agent.items()}

    log("precompute (agent-major)…")
    records: list[dict] = []
    for ai, (aid, days) in enumerate(days_by_agent.items(), 1):
        log(f"  [{ai}/{len(days_by_agent)}] {agents.get(aid, aid)} — {len(days)} day(s)")
        name = agents.get(aid, aid)
        hosts_seen: set[str] = set()
        first_seen: dict[str, str] = {}
        prior_last_goals: list[tuple[str, str]] = []
        prior_mem_day: str | None = None
        change_days = _goal_change_days(aid, agent_goals, village_goals)
        goal_text_by_day: dict[str, str] = {}
        room_prior: dict = {}

        for i, day in enumerate(days):
            day_turns = turns.get((aid, day), [])
            day_sessions = sessions_by_agent_day.get((aid, day), [])
            session_goals = [g for _, g in day_sessions if g]

            block = Block(agent=name, day=day)

            mem_today = memory.get((aid, day))
            mem_prior = memory.get((aid, prior_mem_day)) if prior_mem_day else None

            lo = str(day_turns[0]["ts"]) if day_turns else day + " 00:00:00"
            hi = str(day_turns[-1]["ts"]) if day_turns else day + " 23:59:59"
            goals = _assigned_goals(aid, lo, hi, agent_goals, village_goals)
            room, room_how = R.room_of(name, day, observed, room_prior)
            if room:
                room_prior[name] = (room, day)
            override = R.rest_goal(day) if room == "rest" else None
            if override:
                goals = [{"text": override["text"], "start": override["start"],
                          "end": override["end"]}]
            block.put("room", room or Null("extract_failed",
                                           "no chat and no roster for this day"),
                      note=room_how)
            if goals:
                goal_text_by_day[day] = goals[0]["text"]

            # Active days since the assignment last changed. At 0-1 a day that
            # looks nothing like yesterday is COMPLIANCE, not drift — 26% of the
            # corpus sits there, and 34% of the shared-goal era.
            recent = [c for c in change_days if c <= day]
            since = (sum(1 for d in days[:i] if d >= recent[-1]) if recent else None)
            prior = days[max(0, i - config.BASELINE_DAYS) : i]
            crossed = (sum(1 for c in change_days if prior[0] < c <= day)
                       if prior else None)

            F.goal_features(
                block, goals,
                mem_today["last_content"] if mem_today else None,
                since_change=since, changes_in_baseline=crossed,
            )
            F.memory_features(block, mem_today, mem_prior, prior_mem_day, first_seen)

            # baseline from this agent's own prior ACTIVE days.
            # NOT clipped at goal boundaries: shared-era goals last ~5 active
            # days, so a within-goal baseline would be null for most of the
            # corpus. goal_changes_in_baseline says how much to trust it.
            baseline = None
            if len(prior) >= config.BASELINE_DAYS:
                # Same predicate as the numerator — see features.kept_turns
                counts = [len(F.kept_turns(turns.get((aid, d), []))) for d in prior]
                baseline = {"turns": statistics.median(counts) or None}

            mkey = metric_key_for.get(name)
            F.metric_features(
                block, metrics.get((name, mkey)) if mkey else None, mkey, day, day_turns
            )
            F.activity_features(block, day_turns, baseline)
            F.artifact_features(block, day_turns, hosts_seen)
            F.repetition_features(block, day_turns, session_goals)
            F.interaction_features(
                block, aid, name, chat.get(day, []), short_names, session_goals
            )

            block.context["prior_snapshot_outline"] = (
                F.memory_outline(mem_prior["last_content"]) if mem_prior else []
            )
            block.context["prior_active_days"] = F.history_strip(
                prior_last_goals, goal_text_by_day.get)

            in_window = (start is None or day >= start) and (end is None or day <= end)
            if in_window:   # lookback/spillover days feed state only
                records.append(block_to_record(block))

            # advance rolling state AFTER emitting (no lookahead)
            for t in day_turns:
                if t["command"]:
                    hosts_seen |= set(F._URL_HOST.findall(t["command"]))
            if mem_today:
                for tok in F.watchlist(mem_today["last_content"]):
                    first_seen.setdefault(tok, day)
                prior_mem_day = day
            if session_goals:
                prior_last_goals.append((day, session_goals[-1]))

    # ---- day-major emission ------------------------------------------------
    records.sort(key=lambda r: (r["day"], r["agent"]))
    log(f"built {len(records):,} agent-day records")
    return records


def default_out_dir(records: list[dict], root: str = "samples") -> str:
    """Self-describing name: how many records, over what range.

    A bare `run/` tells you nothing three weeks later, and these directories
    accumulate while iterating on the feature set.
    """
    if not records:
        return os.path.join(root, "0-agent-days-empty")
    days = [r["day"] for r in records]
    return os.path.join(
        root, f"{len(records)}-agent-days-{min(days)}..{max(days)}"
    )


def write(records: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[r["day"]].append(r)
    # Indented JSON, not JSONL: a day is ~250KB, so streaming buys nothing and
    # these files get read by hand. ensure_ascii=False keeps memory text legible.
    for day, rows in sorted(by_day.items()):
        with open(os.path.join(out_dir, f"{day}.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    manifest = {
        "feature_version": config.FEATURE_VERSION,
        "agent_days": len(records),
        "village_days": len(by_day),
        "range": [min(by_day), max(by_day)] if by_day else None,
        "constants": {
            k: getattr(config, k)
            for k in (
                "BASH_CAP", "OUTPUT_CAP", "BASELINE_DAYS", "METRIC_SLOPE_DAYS",
                "REPETITION_THRESHOLD", "MIN_SESSIONS_FOR_REPETITION",
            )
        },
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
