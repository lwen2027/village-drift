"""CLI: python -m drift.cli --start 2026-08-26 --end 2026-08-28 --out out/v1"""
from __future__ import annotations

import argparse

from . import build, render


def main() -> None:
    p = argparse.ArgumentParser(description="Stage 1 feature extraction")
    p.add_argument("--start", help="YYYY-MM-DD inclusive")
    p.add_argument("--end", help="YYYY-MM-DD inclusive")
    p.add_argument("--out", default="samples/run",
                   help="real output; samples/<run>/ is gitignored")
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
    build.write(records, a.out)
    print(f"wrote {len(records):,} records to {a.out}/")


if __name__ == "__main__":
    main()
