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

    python3 -m evaluation.snapshot            # -> ~/village-drift-labels-backup/<ts>/
    python3 -m evaluation.snapshot --list
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil

from village_drift import paths

DEST = os.path.expanduser("~/village-drift-labels-backup")
FILES = (
    ("stage1/eval_100.jsonl", paths.STAGE1_GOLDENS / "eval_100.jsonl"),
    ("stage1/train_23.jsonl", paths.STAGE1_GOLDENS / "train_23.jsonl"),
    ("stage1/verification.jsonl", paths.STAGE1_GOLDENS / "verification.jsonl"),
    ("stage1/arena_40.jsonl", paths.STAGE1_GOLDENS / "arena_40.jsonl"),
         # The mechanical (agent, goal) grouping. SUPERSEDED by
         # windows.jsonl, and its builder (eval/episodes.py) was deleted on
         # 2026-10-01 -- which is precisely why it is still copied here. It
         # is no longer reproducible from the tree, and this script's whole
         # rule is "back up what exists in exactly one place".
         #
         # windows.jsonl is deliberately NOT here: src/village_drift/handoff/pipeline.py rebuilds
         # it from arena_runs/, which IS backed up, so it is reproducible in
         # a way this file no longer is.
    ("stage2/episodes_mechanical.jsonl",
     paths.STAGE2_GOLDENS / "episodes_mechanical.jsonl"),
)

# stage2/labels is the reason this list grew a stage prefix. It holds the 20
# hand-labelled episodes -- ~5M subagent tokens of work, produced once, living
# in exactly one place. It is gitignored for the same reason everything else
# here is (it quotes agent memory and chat from a gated dataset verbatim, and
# more of it than eval_100 does, because labellers are required to cite), so
# this script is its ONLY version history.
#
# digests_windows is deliberately NOT here: 917 files rendered from the dump in
# one free pass, so they are reproducible in a way the labels are not.
# stage2/explained is here for arena_runs' reason, not labels': those files
# are PAID judge calls and the judge is sampled, so re-running costs money and
# does not reproduce. It is empty of real runs today -- only a --stub record --
# but it must be covered BEFORE the first paid Stage 2 batch, not after.
DIRS = (
    ("stage1/runs", paths.STAGE1_RUNS),
    ("stage1/blocks", paths.STAGE1_BLOCKS),
    ("stage2/labels", paths.STAGE2_LABELS),
    ("stage2/explained", paths.STAGE2_ARTIFACTS / "explained"),
)


def snapshot() -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    out = os.path.join(DEST, stamp)
    os.makedirs(out, exist_ok=True)
    for name, source in FILES:
        src = str(source)
        if os.path.exists(src):
            dst = os.path.join(out, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            rows = sum(1 for l in open(src) if l.strip())
            print(f"  {name:32s} {rows:4d} rows")
    for name, source in DIRS:
        src = str(source)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(out, name)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        fs = [f for f in os.listdir(dst)
              if os.path.isfile(os.path.join(dst, f))]
        mb = sum(os.path.getsize(os.path.join(dst, f)) for f in fs) / 1e6
        print(f"  {name + '/':32s} {len(fs):4d} files  {mb:5.1f} MB")
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
        # Snapshots taken before 2026-10-01 are FLAT -- everything at the
        # root, no stage prefix -- because DIRS/FILES only covered stage1
        # then. Try the prefixed path first and fall back, so --list keeps
        # reading the older ones instead of silently reporting them empty.
        def _find(name):
            for cand in (os.path.join(p, name),
                         os.path.join(p, os.path.basename(name))):
                if os.path.exists(cand):
                    return cand
            return None

        for name, _ in DIRS:
            d2 = _find(name)
            if d2 and os.path.isdir(d2):
                counts.append(f"{os.path.basename(name)}="
                              f"{len(os.listdir(d2))}")
        for name, _ in FILES:
            f = _find(name)
            if not f:
                continue
            base = os.path.basename(name)
            n = sum(1 for l in open(f) if l.strip())
            if base == "verification.jsonl":
                counts.append(f"audits={n}")
                continue
            try:
                lab = sum(1 for l in open(f)
                          if json.loads(l).get("is_drift") is not None)
            except (ValueError, AttributeError):
                lab = 0
            # episodes_mechanical.jsonl carries no is_drift at all, so "(0
            # labelled)" would read as "none of these are labelled yet"
            # rather than "this field does not apply here".
            counts.append(f"{base.split('.')[0]}={n}"
                          + (f"({lab} labelled)" if lab else ""))
        print(f"  {d}   " + "  ".join(counts))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    a = p.parse_args()
    listing() if a.list else snapshot()
