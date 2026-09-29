"""Snapshot the work that exists in exactly one place.

eval/*.jsonl is gitignored — the rows quote agent memory and chat from a gated
dataset — so the labels and audits are the one part of this project with no
version history. The frames are reproducible (build_train_set.py,
sample_eval_set.py --seed …); the judgements in them are not.

arena_runs/ is here for a different reason: those 160 files are API calls that
were PAID FOR, and re-running them costs real money and does not reproduce —
the judge is sampled. They were backed up once by hand, which is not a
process; a hand copy is taken when someone remembers, and the arm B rerun and
the arm D rerun both landed after the last one.

arena_blocks/ comes too, even though build.py regenerates it. It regenerates
it *from today's code*, and features.py changes — TODAY_GOAL_CHARS went
200 -> 400 the same day these runs finished. Without the blocks as they were
sent, a stored verdict cannot be traced back to the text that produced it.

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
FILES = ("eval_100.jsonl", "train_23.jsonl", "verification.jsonl",
         "arena_40.jsonl")
DIRS = ("arena_runs", "arena_blocks")


def snapshot() -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = os.path.join(DEST, stamp)
    os.makedirs(out, exist_ok=True)
    for name in FILES:
        src = os.path.join(HERE, "tables", "stage1", name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, name))
            rows = sum(1 for l in open(src) if l.strip())
            print(f"  {name:22s} {rows:4d} rows")
    for name in DIRS:
        src = os.path.join(HERE, "tables", "stage1", name)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(out, name)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        n = len(os.listdir(dst))
        mb = sum(os.path.getsize(os.path.join(dst, f))
                 for f in os.listdir(dst)) / 1e6
        print(f"  {name + '/':22s} {n:4d} files  {mb:5.1f} MB")
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
        for name in DIRS:
            d2 = os.path.join(p, name)
            if os.path.isdir(d2):
                counts.append(f"{name}={len(os.listdir(d2))}")
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
