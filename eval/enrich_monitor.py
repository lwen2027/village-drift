"""Attach the existing monitor's verdict to each labelled agent-day.

This is the incumbent baseline arm. It MUST stay hidden from whoever labels —
seeing it would anchor the label and destroy the comparison. It is joined in
afterwards, purely for scoring.

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
    p.add_argument("--labels", default="eval/train_24.jsonl",
                   help="works on either train_24.jsonl or eval_set.jsonl")
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
        rec["monitor_flagged"] = m is not None
        rec["monitor_severity"] = m["severity"] if m else None
        rec["monitor_heading"] = m["heading"] if m else None
        hit += m is not None
        out.append(rec)
    with open(a.labels, "w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print(f"{len(out)} rows · {hit} carry an off-goal finding · "
          f"{len(out)-hit} do not")


if __name__ == "__main__":
    main()
