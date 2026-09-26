"""Render the method-neutral digest a human labels from.

This is the ONLY thing the labeller sees. It must not contain any detector
output — no Stage-1 block, no score, no monitor heading, no `signals_fired`.
Showing any of those would anchor the label on the thing being tested.

It also must not summarise in a way that embeds a judgement. Everything here is
either verbatim, a count, or a fixed-length truncation. No dedup, no clustering,
no "unusual for this agent" — those are all methods under test.

    python3 eval/render_digest.py --labels eval/eval_100.jsonl --out eval/digests

⚠ Ground truth is established with MORE information than any arm receives —
including agent reasoning, which Stage 1 deliberately excludes for bias reasons.
That asymmetry is the point: the label is the best available account of what
happened, and each arm is then measured on how close it gets from a thinner
channel. It does mean no arm should be expected to reach 100%.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift import config, load  # noqa: E402

CMD_CHARS = 160      # enough to see intent and redirect target
OUT_CHARS = 120      # results, not intent
MEM_CHARS = 4000     # the last snapshot of the day
REASON_CHARS = 420   # per turn
REASON_TURNS = 30    # systematic sample; see note below
BASH_TURNS   = 100   # ditto — p90 was 225 KB of shell log per day
CHAT_PEERS   = 40    # peer messages; human/operator msgs are never sampled
CHAT_CHARS = 400

# The village chat is one shared room: rendering all of it put 303 KB of other
# agents' conversation into every digest (57% of the file) and buried the agent
# under the firehose. Kept: this agent's own messages, messages that name it, and
# every human/operator message. That is a fixed mechanical rule, not a judgement
# about relevance, and the suppressed count is printed so it is never silent.
#
# Reasoning is sampled the same way — every k-th turn, never "the interesting
# ones". Dedup, clustering and salience ranking are all methods under test, so
# none of them can be used to build the surface the ground truth is read from.


def _systematic(items: list, n: int) -> list:
    """Every k-th item. Bounded, reproducible, and embeds no judgement."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def _names_agent(text: str, agent: str) -> bool:
    """Does this message name the agent? Word-boundary, not substring.

    `@GPT-5` matching inside `@GPT-5.6` silently mis-attributed a nudge count
    earlier in this project. The trailing guard is what prevents the repeat.
    """
    import re
    return bool(re.search(re.escape(agent) + r"(?![\w.\-])", text or "", re.I))


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + f" …[+{len(s) - n}c]"


def digest(agent: str, day: str, data: dict) -> str:
    L: list[str] = []
    A = L.append
    A(f"AGENT-DAY DIGEST — {agent} — {day}")
    A("Raw material. No detector has run on this; form your own view.")
    A("")

    g = data["goal"]
    A("## ASSIGNED GOAL")
    A(f"  {g['text'] if g else '(none recorded — village goal only)'}")
    if g and g.get("start"):
        A(f"  in force {str(g['start'])[:10]} .. {str(g.get('end') or 'ongoing')[:10]}")
    A("")

    A(f"## SESSION GOALS — {len(data['sessions'])} sessions, verbatim, in order")
    A("   (the agent's own statement of what it set out to do each session)")
    for s in data["sessions"]:
        when = (f"(opened {str(s['opened'])[:10]} {str(s['opened'])[11:16]}, ran into today)"
                if s.get("carried_over") else str(s["opened"])[11:16])
        A(f"  {when}  {_clip(s.get('session_goal'), 300)}")
    A("")

    turns = data["turns"]
    mix = collections.Counter(t["kind"] for t in turns)
    A(f"## ACTIVITY — {len(turns)} turns")
    if turns:
        A(f"  span {str(turns[0]['ts'])[11:16]}–{str(turns[-1]['ts'])[11:16]}")
    A("  " + " · ".join(f"{k} {v}" for k, v in mix.most_common()))
    A("")

    bash = [t for t in turns if t["kind"] == "bash"]
    bsamp = _systematic(bash, BASH_TURNS)
    A(f"## BASH — {len(bash)} commands, showing {len(bsamp)} "
      f"(every {max(1, len(bash)//max(1,len(bsamp)))}th), {CMD_CHARS} chars each")
    if len(bsamp) < len(bash):
        A("   (sampled mechanically, NOT by interest — all of it is in eval/raw/)")
    for t in bsamp:
        A(f"  {str(t['ts'])[11:16]}  {_clip(t['command'], CMD_CHARS)}")
        if t.get("output") or t.get("error"):
            A(f"         -> {_clip((t.get('output') or '') + (t.get('error') or ''), OUT_CHARS)}")
    A("")

    keep = [c for c in data["chat"] if c["own"] or c["human"]]
    peers = [c for c in data["chat"]
             if not (c["own"] or c["human"]) and _names_agent(c["content"], agent)]
    # Operator and own messages are never sampled: an operator instruction is the
    # single most common external cause of a day changing direction.
    shown = sorted(keep + _systematic(peers, CHAT_PEERS), key=lambda c: str(c["ts"]))
    hidden = len(data["chat"]) - len(shown)
    A(f"## CHAT — {sum(1 for c in shown if c['own'])} sent by this agent, "
      f"{sum(1 for c in shown if not c['own'])} to it or from a human")
    A(f"   ({hidden} other messages in the shared room not shown — "
      f"full transcript in eval/raw/)")
    for c in shown:
        arrow = "→" if c["own"] else "←"
        A(f"  {str(c['ts'])[11:16]}  {arrow} {c['speaker']}: {_clip(c['content'], CHAT_CHARS)}")
    A("")

    A(f"## MEMORY — {len(data['memory'])} snapshots; last one of the day below")
    if data["memory"]:
        A(_clip(data["memory"][-1]["content"], MEM_CHARS))
    A("")

    r = [t for t in turns if t.get("reasoning")]
    samp = _systematic(r, REASON_TURNS)
    A(f"## REASONING — recorded for {len(r)} of {len(turns)} turns; "
      f"showing {len(samp)}, every {max(1, len(r)//max(1,len(samp)))}th")
    A("   (availability varies 28–98% by provider; absence is not silence.")
    A("    Sampled mechanically, NOT by interest. All of it is in eval/raw/.)")
    for t in samp:
        A(f"  {str(t['ts'])[11:16]}  {_clip(t['reasoning'], REASON_CHARS)}")
    return "\n".join(L) + "\n"


def collect(days: set[tuple[str, str]]) -> dict:
    """One streaming pass over the dump for every requested agent-day."""
    agents = {a["id"]: a.get("name") for a in load._rows("agents.jsonl.gz")}
    by_name = {v: k for k, v in agents.items()}
    wanted_agents = {by_name[a] for a, _ in days if a in by_name}

    goals = collections.defaultdict(list)
    for g in load._rows("agent_goals.jsonl.gz"):
        if g.get("agent_id") in wanted_agents:
            goals[g["agent_id"]].append(g)

    sess = {}
    for s in load._rows("computer_use_sessions.jsonl.gz"):
        if s.get("agent_id") in wanted_agents:
            sess[s["id"]] = s

    out = {k: {"goal": None, "sessions": [], "turns": [], "chat": [], "memory": []}
           for k in days}
    sess_days: dict = collections.defaultdict(set)   # session -> days it ran on

    for t in load._rows("computer_use_turns.jsonl.gz"):
        s = sess.get(t.get("session_id"))
        if not s:
            continue
        k = (agents[s["agent_id"]], str(t.get("created_at"))[:10])
        sess_days[s["id"]].add(k[1])
        if k not in out:
            continue
        a = t.get("agent_action") or {}
        cmd, act = a.get("command"), a.get("action")
        reasoning, text = load.split_messages(t.get("agent_messages"))
        out[k]["turns"].append({
            "ts": t.get("created_at"),
            "kind": "bash" if cmd else (act or "none"),
            "command": cmd, "output": t.get("output"), "error": t.get("error"),
            "reasoning": reasoning, "text": text,
        })

    # A session belongs to every day it produced turns on, NOT just the day it
    # opened. Sessions cross midnight: GPT-5.6 Terra opened one at 2026-08-24
    # 23:54 and ran all 31 of the next day's turns inside it, so keying by the
    # session's own date dropped its goal — "Preserve Terra; assess concrete
    # valid triggers only" — off the 25th entirely, leaving a 31-turn day
    # looking like unexplained inactivity. drift/load.py already fixes this for
    # turns; the same rule has to hold here.
    for sid, s in sess.items():
        agent = agents[s["agent_id"]]
        opened = str(s.get("created_at"))[:10]
        for day in sess_days.get(sid, set()) | {opened}:
            k = (agent, day)
            if k in out:
                out[k]["sessions"].append({
                    "opened": s.get("created_at"),
                    "carried_over": day != opened,
                    "session_goal": s.get("session_goal")})

    for m in load._rows("agent_memories.jsonl.gz"):
        if m.get("agent_id") in wanted_agents:
            k = (agents[m["agent_id"]], str(m.get("created_at"))[:10])
            if k in out:
                out[k]["memory"].append({"ts": m.get("created_at"),
                                         "content": m.get("content")})

    names = {a for a, _ in days}
    for c in load._rows("chat_messages.jsonl.gz"):
        day = str(c.get("created_at"))[:10]
        speaker_id = c.get("agent_speaker_id")
        speaker = agents.get(speaker_id) or c.get("speaker_type") or "human"
        for name in names:
            if (name, day) in out:
                out[(name, day)]["chat"].append({
                    "ts": c.get("created_at"), "speaker": speaker,
                    "own": speaker == name,
                    "human": speaker_id is None and c.get("speaker_type") != "agent",
                    "content": c.get("content")})

    for (name, day), d in out.items():
        d["turns"].sort(key=lambda x: str(x["ts"]))
        d["sessions"].sort(key=lambda x: str(x["opened"]))
        d["memory"].sort(key=lambda x: str(x["ts"]))
        d["chat"].sort(key=lambda x: str(x["ts"]))
        aid = by_name.get(name)
        for g in goals.get(aid, []):
            if str(g.get("start_time"))[:10] <= day and (
                    not g.get("end_time") or str(g["end_time"])[:10] >= day):
                d["goal"] = {"text": g.get("name"), "start": g.get("start_time"),
                             "end": g.get("end_time")}
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default="eval/eval_100.jsonl")
    p.add_argument("--out", default="eval/digests")
    p.add_argument("--raw", default="eval/raw",
                   help="full untruncated dump, for failure analysis only")
    a = p.parse_args()

    rows = [json.loads(l) for l in open(a.labels)]
    days = {(r["agent"], r["day"]) for r in rows}
    print(f"collecting {len(days)} agent-days …")
    data = collect(days)

    os.makedirs(a.out, exist_ok=True)
    shas, sizes = {}, []
    for (agent, day), d in sorted(data.items()):
        txt = digest(agent, day, d)
        safe = agent.replace("/", "_").replace(" ", "_")
        open(os.path.join(a.out, f"{day}__{safe}.txt"), "w").write(txt)
        shas[(agent, day)] = hashlib.sha256(txt.encode()).hexdigest()[:16]
        sizes.append(len(txt))
        if a.raw:
            rd = os.path.join(a.raw, day)
            os.makedirs(rd, exist_ok=True)
            json.dump({"agent": agent, "day": day, **d},
                      open(os.path.join(rd, f"{safe}.json"), "w"))

    for r in rows:
        r["digest_sha"] = shas.get((r["agent"], r["day"]))
    with open(a.labels, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    sizes.sort()
    print(f"wrote {len(sizes)} digests -> {a.out}/  (raw -> {a.raw}/)")
    print(f"  size  median {sizes[len(sizes)//2]/1024:.0f} KB  "
          f"p90 {sizes[int(.9*len(sizes))]/1024:.0f} KB  max {sizes[-1]/1024:.0f} KB  "
          f"total {sum(sizes)/1e6:.1f} MB")
    print(f"  digest_sha written back to {a.labels}")


if __name__ == "__main__":
    main()
