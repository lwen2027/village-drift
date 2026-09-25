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

from . import config, features as F, load
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


def _assigned_goal(agent_id, day, agent_goals, village_goals) -> str | None:
    """Individual goal if one covers the day, else the village goal."""
    for row in reversed(agent_goals):
        if row.get("agent_id") != agent_id:
            continue
        start = str(row.get("start_time") or "")[:10]
        end = str(row.get("end_time") or "")[:10] or None
        if start <= day and (end is None or day < end):
            return row.get("name")
    for row in reversed(village_goals):
        start = str(row.get("start_time") or "")[:10]
        end = str(row.get("end_time") or "")[:10] or None
        if start <= day and (end is None or day < end):
            return row.get("name") or row.get("goal")
    return None


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

    # ---- agent-major precompute -------------------------------------------
    days_by_agent: dict[str, list[str]] = defaultdict(list)
    for (aid, day) in sessions_by_agent_day:
        days_by_agent[aid].append(day)
    for v in days_by_agent.values():
        v.sort()

    log("precompute (agent-major)…")
    records: list[dict] = []
    for ai, (aid, days) in enumerate(days_by_agent.items(), 1):
        log(f"  [{ai}/{len(days_by_agent)}] {agents.get(aid, aid)} — {len(days)} day(s)")
        name = agents.get(aid, aid)
        hosts_seen: set[str] = set()
        first_seen: dict[str, str] = {}
        prior_last_goals: list[tuple[str, str]] = []
        prior_mem_day: str | None = None

        for i, day in enumerate(days):
            day_turns = turns.get((aid, day), [])
            day_sessions = sessions_by_agent_day[(aid, day)]
            session_goals = [g for _, g in day_sessions if g]

            block = Block(agent=name, day=day)

            mem_today = memory.get((aid, day))
            mem_prior = memory.get((aid, prior_mem_day)) if prior_mem_day else None

            F.goal_features(
                block,
                _assigned_goal(aid, day, agent_goals, village_goals),
                mem_today["last_content"] if mem_today else None,
            )
            F.memory_features(block, mem_today, mem_prior, prior_mem_day, first_seen)

            # baseline from this agent's own prior ACTIVE days
            prior = days[max(0, i - config.BASELINE_DAYS) : i]
            baseline = None
            if len(prior) >= config.BASELINE_DAYS:
                counts = [len(turns.get((aid, d), [])) for d in prior]
                baseline = {"turns": statistics.median(counts) or None}

            F.activity_features(block, day_turns, baseline)
            F.artifact_features(block, day_turns, hosts_seen)
            F.repetition_features(block, day_turns, session_goals)
            F.interaction_features(
                block, aid, name, chat.get(day, []), short_names, session_goals
            )

            block.context["prior_snapshot_outline"] = (
                F.memory_outline(mem_prior["last_content"]) if mem_prior else []
            )
            block.context["prior_active_days"] = F.history_strip(prior_last_goals)

            if start is None or day >= start:   # lookback days feed state only
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


def write(records: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_day[r["day"]].append(r)
    for day, rows in sorted(by_day.items()):
        with open(os.path.join(out_dir, f"{day}.jsonl"), "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
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
