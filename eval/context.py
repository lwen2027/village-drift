"""Derived context for an agent-day, computed on demand.

The label tables hold JUDGEMENTS. Everything recomputable from the dump lives
here and is looked up when needed, rather than being cached into every row.

That split exists because cached facts go stale and nobody notices. Two did in
one afternoon: `days_since_goal_change` was silently counting only the sampled
days, and `label_changed` was being overwritten on every re-record. Both looked
fine in the file.

    from eval.context import context_for
    context_for("GPT-5.5", "2026-08-20")
    -> {goals, goal_is_open, days_since_goal_change, room, room_source,
        operator_messages, nudges}

Cheap after the first call: the dump scan is memoised per process.
"""

from __future__ import annotations

import functools
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift import config, load, rooms as R  # noqa: E402

NUDGE = re.compile(r"automated nudge triggered by:\s*\[([^\]]+)\]", re.I)


def _named(agent: str) -> re.Pattern:
    """Word-boundary on BOTH sides — `@GPT-5` matched inside `@GPT-5.6` once,
    and agent "A" matched the final letter of "Luna"."""
    return re.compile(r"(?<![\w.\-])" + re.escape(agent) + r"(?![\w.\-])", re.I)


@functools.lru_cache(maxsize=1)
def _dump():
    agents = {a["id"]: a.get("name") for a in load._rows("agents.jsonl.gz")}
    rooms = {r["id"]: r["name"] for r in load._rows("chat_rooms.jsonl.gz")}
    chat = [c for c in load._rows("chat_messages.jsonl.gz")]
    sessions = {s["id"]: s.get("agent_id")
                for s in load._rows("computer_use_sessions.jsonl.gz")}
    active: dict = {}
    for t in load._rows("computer_use_turns.jsonl.gz"):
        a = agents.get(sessions.get(t.get("session_id")))
        if a:
            active.setdefault(a, set()).add(str(t.get("created_at"))[:10])
    return {
        "agents": agents, "rooms": rooms, "chat": chat,
        "active": {a: sorted(d) for a, d in active.items()},
        "agent_goals": list(load._rows("agent_goals.jsonl.gz")),
        "village_goals": list(load._rows("village_goals.jsonl.gz")),
        "observed": R.observed_rooms(chat, agents, rooms),
    }


def goals_for(agent: str, day: str, D=None) -> list:
    """Goals in force during this day, individual first, #rest override applied.

    ⚠ KNOWN WRONG on mid-day goal switches. This resolves midnight-to-midnight,
    so a day straddling a change (2026-06-23, village goal switched at 14:38)
    matches two rows. `render_digest._in_force` resolves against the day's first
    and last TURN instead, which is correct; this has not been brought into line
    with it. 11 of the 100 eval rows differ. Use the stored `goals` column, or
    render_digest, until this is fixed.
    """
    D = D or _dump()
    aid = next((i for i, n in D["agents"].items() if n == agent), None)
    lo, hi = day + " 00:00:00", day + " 23:59:59"

    def covering(rows, key, scope):
        out = []
        for r in rows:
            start, end = str(r.get("start_time") or ""), str(r.get("end_time") or "") or None
            if start <= hi and (end is None or end >= lo):
                out.append({"text": r.get(key) or r.get("goal") or r.get("name"),
                            "start": r.get("start_time"), "end": r.get("end_time"),
                            "scope": scope})
        return sorted(out, key=lambda g: str(g["start"]))

    mine = [r for r in D["agent_goals"] if r.get("agent_id") == aid]
    out = covering(mine, "name", "individual") or covering(D["village_goals"], "goal", "village")
    room, _ = room_for(agent, day, D)
    override = R.rest_goal(day) if room == "rest" else None
    if override:
        out = [{"text": override["text"], "start": override["start"],
                "end": override["end"], "scope": override["scope"]}]
    return out


def room_for(agent: str, day: str, D=None):
    # No carry-forward chain, so `room_of`'s prior-day fallback never fires and
    # a silent day resolves by roster or not at all.
    D = D or _dump()
    return R.room_of(agent, day, D["observed"])


def operator_messages(agent: str, day: str, D=None) -> list:
    """Operator messages naming this agent on this day.

    ⚠ A nudge quotes "recent activity" and the nudger reads the whole recent
    transcript, so one arriving early in the agent's day is a statement about
    PRIOR days. `pct_into_day` and `likely_about_prior_days` say which: 15 of
    67 across the eval set land in the first 15% of the working day.

    ⚠ This is close to an oracle for the label — on the rows labelled so far,
    5 of 7 drift days carry one against 1 of 11 non-drift days. It is derived
    rather than stored precisely so it is not mistaken for a label.
    """
    D = D or _dump()
    pat = _named(agent)
    out = []
    for c in D["chat"]:
        if c.get("agent_speaker_id"):
            continue
        if str(c.get("created_at"))[:10] != day:
            continue
        t = " ".join(str(c.get("content") or "").split())
        if not pat.search(t):
            continue
        m = NUDGE.search(t)
        out.append({"ts": str(c["created_at"])[:19],
                    "kind": "automated-nudge" if m else "direct",
                    "trigger": m.group(1) if m else None,
                    "room": D["rooms"].get(c.get("room_id")),
                    "msg_id": c["id"], "text": t[:300]})
    return sorted(out, key=lambda x: x["ts"])


def context_for(agent: str, day: str) -> dict:
    D = _dump()
    gs = goals_for(agent, day, D)
    room, how = room_for(agent, day, D)
    msgs = operator_messages(agent, day, D)
    prev = sorted(c for c in {str(g["start_time"])[:10] for g in D["village_goals"]
                              if g.get("start_time")}
                  | {str(g["start_time"])[:10] for g in D["agent_goals"]
                     if D["agents"].get(g.get("agent_id")) == agent and g.get("start_time")}
                  if c <= day)
    active = D["active"].get(agent, [])
    return {
        "goals": gs,
        "goal_is_open": any(m in str(g.get("text", "")).lower()
                            for g in gs for m in config.OPEN_GOAL_MARKERS),
        "days_since_goal_change": (sum(1 for d in active if prev[-1] <= d < day)
                                   if prev else None),
        "room": room, "room_source": how,
        "operator_messages": msgs,
        "nudges": sum(1 for m in msgs if m["kind"] == "automated-nudge"),
    }


if __name__ == "__main__":
    import json
    a, d = sys.argv[1], sys.argv[2]
    print(json.dumps(context_for(a, d), indent=2, ensure_ascii=False)[:1400])
