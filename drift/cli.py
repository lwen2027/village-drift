"""CLI: python -m drift.cli --start 2026-08-26 --end 2026-08-28 --out out/v1"""
from __future__ import annotations

import argparse

from . import build, render


def main() -> None:
    p = argparse.ArgumentParser(description="Stage 1 feature extraction")
    p.add_argument("--start", help="YYYY-MM-DD inclusive")
    p.add_argument("--end", help="YYYY-MM-DD inclusive")
    p.add_argument("--out", help="output dir; default is a self-describing "
                                 "samples/<N>-agent-days-<start>..<end>/ "
                                 "(samples/*/ is gitignored)")
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
