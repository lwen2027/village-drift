"""CLI: python -m village_drift.stage1.cli --start DATE --end DATE."""
from __future__ import annotations

import argparse

from village_drift.shared import render
from village_drift.stage1 import build


def main() -> None:
    p = argparse.ArgumentParser(description="Stage 1 feature extraction")
    p.add_argument("--start", help="YYYY-MM-DD inclusive")
    p.add_argument("--end", help="YYYY-MM-DD inclusive")
    p.add_argument("--out", help="output dir; default is a self-describing "
                                 "artifacts/current/stage1/<N>-agent-days-"
                                 "<start>..<end>/")
    p.add_argument("--preview", metavar="AGENT",
                   help="render one agent's block to stdout instead of writing")
    a = p.parse_args()

    records = build.build(a.start, a.end)
    if a.preview:
        hits = [r for r in records if r["agent"] == a.preview]
        if not hits:
            raise SystemExit(f"no records for {a.preview!r} in range")
        print(render.render(hits[-1]))
        return
    out = a.out or build.default_out_dir(records)
    build.write(records, out)
    print(f"wrote {len(records):,} records to {out}/")


if __name__ == "__main__":
    main()
