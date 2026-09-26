"""Snapshot the hand-made label work.

eval/*.jsonl is gitignored — the rows quote agent memory and chat from a gated
dataset — so the labels and audits are the one part of this project with no
version history. The frames are reproducible (build_train_set.py,
sample_eval_set.py --seed …); the judgements in them are not. Roughly 40 claim
verdicts and 20 labels exist in exactly one place.

    python3 eval/snapshot.py            # -> ~/village-drift-labels-backup/<ts>/
    python3 eval/snapshot.py --list
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.expanduser("~/village-drift-labels-backup")
FILES = ("eval_100.jsonl", "train_23.jsonl", "verification.jsonl")


def snapshot() -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = os.path.join(DEST, stamp)
    os.makedirs(out, exist_ok=True)
    for name in FILES:
        src = os.path.join(HERE, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, name))
            rows = sum(1 for l in open(src) if l.strip())
            print(f"  {name:22s} {rows:4d} rows")
    print(f"-> {out}")
    return out


def listing() -> None:
    if not os.path.isdir(DEST):
        print("no snapshots yet")
        return
    for d in sorted(os.listdir(DEST)):
        p = os.path.join(DEST, d)
        if not os.path.isdir(p):
            continue
        counts = []
        for name in FILES:
            f = os.path.join(p, name)
            if os.path.exists(f):
                n = sum(1 for l in open(f) if l.strip())
                lab = sum(1 for l in open(f)
                          if json.loads(l).get("is_drift") is not None) \
                    if name != "verification.jsonl" else n
                counts.append(f"{name.split('.')[0]}={n}({lab} labelled)"
                              if name != "verification.jsonl" else f"audits={n}")
        print(f"  {d}   " + "  ".join(counts))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    listing() if a.list else snapshot()
