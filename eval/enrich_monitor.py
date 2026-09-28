"""Record the existing monitor's verdict for each labelled agent-day.

This is the incumbent baseline arm. It MUST stay hidden from whoever labels —
seeing it would anchor the label and destroy the comparison. It is joined in
afterwards, purely for scoring.

⚠ IT WAS NOT HIDDEN. Until 2026-09-28 these three fields lived on every row of
eval_100.jsonl, which is the file auditors read to find their case. Two
subagents quoted their own row's monitor_heading back in their reports, so the
leak is demonstrated, not hypothetical, and it applies to ALL audits before that
date — not only the batches where I stated the verdict in the brief.

They now live in eval/monitor.jsonl, joined on (agent, day). Do not merge them
back into the label table.

    export DATABASE_URI='postgresql://…'
    python3 eval/enrich_monitor.py
"""
from __future__ import annotations

import argparse, json, os, sys, urllib.request

SQL = """
SELECT a.name AS agent, f.monitor_date AS day, f.severity, f.heading, f.category
FROM monitor_findings f JOIN agents a ON a.id = f.agent_id
WHERE f.category = 'off-goal';
"""
RANK = {"high": 3, "medium": 2, "low": 1}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default="eval/tables/stage1/train_23.jsonl",
                   help="rows to key on; the verdict is written to --out, never back")
    p.add_argument("--out", default=os.path.expanduser("~/village-drift-monitor/monitor.jsonl"),
                   help="OUTSIDE the repo. Auditors are pointed at /Users/lwen/village-drift "
                        "and will read anything inside it — one did, from eval/monitor.jsonl, "
                        "after the fields were moved off the label rows. Keep it out of reach.")
    a = p.parse_args()
    uri = os.environ.get("DATABASE_URI")
    if not uri:
        sys.exit("DATABASE_URI not set (never hardcode it)")
    host = uri.split("@", 1)[1].split("/", 1)[0]
    req = urllib.request.Request(
        f"https://{host}/sql",
        data=json.dumps({"query": SQL, "params": []}).encode(),
        headers={"Neon-Connection-String": uri, "Content-Type": "application/json"})
    rows = json.loads(urllib.request.urlopen(req, timeout=120).read())["rows"]

    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["agent"], str(r["day"])[:10])
        if k not in best or RANK.get(r["severity"], 0) > RANK.get(best[k]["severity"], 0):
            best[k] = r

    out, hit = [], 0
    for line in open(a.labels):
        rec = json.loads(line)
        m = best.get((rec["agent"], rec["day"]))
        hit += m is not None
        out.append({"agent": rec["agent"], "day": rec["day"],
                    "monitor_flagged": m is not None,
                    "monitor_severity": m["severity"] if m else None,
                    "monitor_heading": m["heading"] if m else None})
    with open(a.out, "w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"{len(out)} rows · {hit} carry an off-goal finding · "
          f"{len(out)-hit} do not")


if __name__ == "__main__":
    main()
