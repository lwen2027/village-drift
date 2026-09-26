"""Build the sampling frame: every agent-day, with the attributes we stratify on.

Shared by the sampler and by anything that needs to know what days exist.

Two populations, and the difference matters:

  monitored    the monitor produced a completed run for this agent-day. 1,134 of
               them within the dump's coverage. This is where precision is
               measurable, because the incumbent has an opinion to compare against.
  unmonitored  the agent worked but the monitor never ran. The ONLY place monitor
               false negatives are visible at all.

⚠ The frame stops at the dump's last day. The monitor DB runs a month further
(597 more agent-days through 2026-09-25), but Stage 1's mechanical arm reads the
gzipped dump, so a September day is one that arm structurally cannot score. Adding
it would hand the model-based arms a third of the eval set uncontested. Extending
the frame means re-pulling the dump, which is a decision about the whole sweep and
not just about eval.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift import config, load  # noqa: E402

# Individual agent goals begin here; before it every agent shares one village goal.
# Drift means something different either side of this line, so it is a stratum.
ERA_SPLIT = config.INDIVIDUAL_GOALS_START

MONITORED_SQL = """
SELECT a.name AS agent, r.monitor_date AS day
FROM monitor_runs r JOIN agents a ON a.id = r.agent_id
WHERE r.status = 'completed'
GROUP BY 1, 2;
"""


def monitored_days(uri: str) -> set[tuple[str, str]]:
    """(agent, day) pairs the monitor completed a run for.

    Deduped in SQL: 2,198 runs collapse to 1,731 agent-days because ~470 were
    re-run. Counting runs instead of agent-days inflates the frame by 27%.
    """
    host = uri.split("@", 1)[1].split("/", 1)[0]
    req = urllib.request.Request(
        f"https://{host}/sql",
        data=json.dumps({"query": MONITORED_SQL, "params": []}).encode(),
        headers={"Neon-Connection-String": uri, "Content-Type": "application/json"})
    rows = json.loads(urllib.request.urlopen(req, timeout=180).read())["rows"]
    return {(r["agent"], str(r["day"])[:10]) for r in rows}


def activity() -> dict[tuple[str, str], int]:
    """Turns per agent-day, straight from the dump. One ~10s streaming pass.

    Counts RAW turns, not kept turns: this is a stratification attribute, and
    stratifying on anything a method computes would tilt the comparison.
    """
    agents = {a["id"]: a.get("name") for a in load._rows("agents.jsonl.gz")}
    sess = {s["id"]: s.get("agent_id")
            for s in load._rows("computer_use_sessions.jsonl.gz")}
    counts: collections.Counter = collections.Counter()
    for t in load._rows("computer_use_turns.jsonl.gz"):
        name = agents.get(sess.get(t.get("session_id")))
        if name:
            counts[(name, str(t.get("created_at"))[:10])] += 1
    return dict(counts)


def deciles(counts: dict) -> dict[tuple[str, str], int]:
    """Activity decile 0-9 over the whole frame, by rank not by value.

    Rank-based because the distribution is heavily skewed — a value-based cut
    would put almost every agent-day in one bucket.
    """
    order = sorted(counts, key=lambda k: (counts[k], k))
    n = len(order)
    return {k: min(9, i * 10 // n) for i, k in enumerate(order)}


def build(uri: str | None, cutoff: str | None = None) -> list[dict]:
    """Every agent-day in the dump, tagged with its stratification attributes."""
    counts = activity()
    if cutoff:
        counts = {k: v for k, v in counts.items() if k[1] <= cutoff}
    dec = deciles(counts)
    mon = monitored_days(uri) if uri else set()
    return [
        {
            "agent": agent,
            "day": day,
            "turns_raw": n,
            "era": "individual-goal" if day >= ERA_SPLIT else "shared-goal",
            "activity_decile": dec[(agent, day)],
            "monitored": (agent, day) in mon,
        }
        for (agent, day), n in sorted(counts.items())
    ]


def main() -> None:
    uri = os.environ.get("DATABASE_URI")
    if not uri:
        sys.exit("DATABASE_URI not set (never hardcode it)")
    rows = build(uri, cutoff=os.environ.get("FRAME_CUTOFF"))
    m = [r for r in rows if r["monitored"]]
    print(f"{len(rows)} agent-days in the dump "
          f"({min(r['day'] for r in rows)} .. {max(r['day'] for r in rows)})")
    print(f"  monitored   {len(m):5d}")
    print(f"  unmonitored {len(rows) - len(m):5d}")
    for era in ("shared-goal", "individual-goal"):
        e = [r for r in rows if r["era"] == era]
        print(f"  {era:16s} {len(e):5d}  "
              f"({sum(r['monitored'] for r in e)} monitored)")


if __name__ == "__main__":
    main()
