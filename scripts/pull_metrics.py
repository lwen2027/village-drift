"""Pull metric_datapoints (DB-only; excluded from the public dump).

    export DATABASE_URI='postgresql://…'      # NEVER hardcode this
    python3 scripts/pull_metrics.py --since 2026-07-06 --out data/metrics.json

Uses the Neon HTTPS SQL endpoint, so no postgres driver is needed.
Daily last-value per (agent, metric) is enough for slope and flatness, and is
~1/100th the raw volume.
"""
from __future__ import annotations

import argparse, json, os, sys, urllib.request

SQL = """
SELECT a.name AS agent, m.metric_key,
       date_trunc('day', m.measured_at)::date::text AS day,
       min(m.value) AS lo, max(m.value) AS hi,
       (array_agg(m.value  ORDER BY m.measured_at DESC))[1] AS last_value,
       (array_agg(m.source ORDER BY m.measured_at DESC))[1] AS last_source,
       count(*) AS n
FROM metric_datapoints m
LEFT JOIN agents a ON a.id = m.agent_id     -- nullable: some metrics are village-level
WHERE m.measured_at >= %(since)s            -- measured_at, NOT created_at
GROUP BY 1,2,3 ORDER BY 1,2,3;
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2026-07-06", help="series starts here")
    p.add_argument("--out", default="data/metrics.json")
    a = p.parse_args()

    uri = os.environ.get("DATABASE_URI")
    if not uri:
        sys.exit("DATABASE_URI not set (do not hardcode it; use the environment)")
    host = uri.split("@", 1)[1].split("/", 1)[0]

    body = json.dumps({"query": SQL.replace("%(since)s", f"'{a.since}'"), "params": []})
    req = urllib.request.Request(
        f"https://{host}/sql", data=body.encode(),
        headers={"Neon-Connection-String": uri, "Content-Type": "application/json"},
    )
    rows = json.loads(urllib.request.urlopen(req, timeout=120).read()).get("rows", [])

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"{len(rows):,} daily rows -> {a.out}")
    # A flat SELF-REPORT metric is no signal, not evidence.
    srcs = {r.get("last_source") for r in rows}
    print("sources:", ", ".join(sorted(s for s in srcs if s)))


if __name__ == "__main__":
    main()
