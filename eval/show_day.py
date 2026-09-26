"""Render one agent-day COMPLETE — no sampling, no truncation.

The digest is a bounded reading surface. This is the whole day, formatted for
reading rather than for parsing. Use it when labelling: the digest's sampling
is what keeps 100 days tractable, but a label should be made against everything
that happened.

    python3 eval/show_day.py 2026-08-25 "GPT-5.6 Terra"
    python3 eval/show_day.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")


def one(s) -> str:
    return " ".join(str(s or "").split())


def show(day: str, agent: str, agent_only: bool = False) -> str:
    """agent_only bounds the two channels that are the VILLAGE's, not this
    agent's: the shared chat room (~935 msgs/day) and the memory history. Every
    turn this agent took is still rendered complete. Without it a single day is
    2.4 MB median, almost all of it other agents talking."""
    path = os.path.join(RAW, day, agent.replace("/", "_").replace(" ", "_") + ".json")
    d = json.load(open(path))
    L: list[str] = []
    A = L.append

    A(f"{'='*78}\nAGENT-DAY (COMPLETE) — {d['agent']} — {d['day']}\n{'='*78}")

    gs = d.get("goals") or ([d["goal"]] if d.get("goal") else [])
    A("\n## ASSIGNED GOAL" + ("  — ⚠ CHANGED DURING THIS DAY" if len(gs) > 1 else ""))
    for g in gs:
        A(f"  [{g.get('scope','?')}] {str(g.get('start'))[:16]} .. "
          f"{str(g.get('end') or 'ongoing')[:16]}\n     {g['text']}")
    if not gs:
        A("  (none recorded)")
    if d.get("goal_is_open"):
        A("  ⚠ GOAL DOES NOT CONSTRAIN BEHAVIOUR — drift here is UNDEFINED, not absent")
    if d.get("days_since_goal_change") is not None:
        n = d["days_since_goal_change"]
        A(f"  active days since the assignment changed: {n}"
          + ("   ⚠ at 0-1, differing from yesterday is EXPECTED" if n <= 1 else ""))

    A(f"\n## SESSION GOALS — {len(d['sessions'])}")
    for s in d["sessions"]:
        tag = " [CARRIED OVER from the previous day]" if s.get("carried_over") else ""
        A(f"\n  [{str(s['opened'])[:16]}]{tag}\n  {one(s.get('session_goal'))}")

    turns = d["turns"]
    kinds: dict = {}
    for t in turns:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    A(f"\n## ACTIVITY — {len(turns)} turns"
      + (f", span {str(turns[0]['ts'])[11:16]}–{str(turns[-1]['ts'])[11:16]}" if turns else ""))
    A("  " + " · ".join(f"{k} {v}" for k, v in
                        sorted(kinds.items(), key=lambda x: -x[1])))

    A(f"\n## TURNS — all {len(turns)}, complete")
    for i, t in enumerate(turns):
        A(f"\n--- turn {i+1}/{len(turns)}  {str(t['ts'])[11:19]}  [{t['kind']}]")
        if t.get("reasoning"):
            A(f"  REASONING: {one(t['reasoning'])}")
        if t.get("text"):
            A(f"  SAYS: {one(t['text'])}")
        if t.get("command"):
            A(f"  $ {t['command']}")
        if t.get("output"):
            A(f"  OUT: {one(t['output'])}")
        if t.get("error"):
            A(f"  ERR: {one(t['error'])}")

    chat = d["chat"]
    if agent_only:
        idx = [i for i, c in enumerate(chat) if c["own"] or c["human"]
               or agent.split()[0].lower() in one(c["content"]).lower()]
        keep = sorted({j for i in idx for j in range(max(0, i-3), min(len(chat), i+2))})
        chat = [chat[j] for j in keep]
    A(f"\n## CHAT — {len(chat)} of {len(d['chat'])} shared-room messages"
      f"{' (this agent, plus 3 before / 1 after each for antecedent)' if agent_only else ''}")
    for c in chat:
        who = "SELF" if c["own"] else ("HUMAN" if c["human"] else c["speaker"])
        A(f"  [{str(c['ts'])[11:19]}] {who}: {one(c['content'])}")

    mem = d["memory"][-1:] if agent_only else d["memory"]
    A(f"\n## MEMORY — {len(mem)} of {len(d['memory'])} snapshots"
      f"{' (last of the day)' if agent_only and d['memory'] else ''}")
    for m in mem:
        A(f"\n----- snapshot {str(m['ts'])[:19]} ({len(m['content'])} chars)\n{m['content']}")
    return "\n".join(L)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("day", nargs="?")
    p.add_argument("agent", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--agent-only", action="store_true",
                   help="bound the shared room and memory history")
    a = p.parse_args()
    if a.list or not (a.day and a.agent):
        for day in sorted(os.listdir(RAW)):
            for f in sorted(os.listdir(os.path.join(RAW, day))):
                size = os.path.getsize(os.path.join(RAW, day, f))
                print(f"  {day}  {f[:-5].replace('_', ' '):22s} {size/1024:7.0f} KB")
        return
    print(show(a.day, a.agent, a.agent_only))


if __name__ == "__main__":
    main()
