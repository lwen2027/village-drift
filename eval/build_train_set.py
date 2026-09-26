"""Extract the 24 hand-read cases into labels.jsonl as the `train` split.

These 24 are the cases the codebook, feature set and label space were derived
from. They are TRAINING DATA: scoring any Stage-1 method against them measures
memorisation. The `split` field exists to make excluding them mechanical rather
than a thing someone has to remember.

    python3 eval/build_train_set.py        # -> eval/train_24.jsonl
"""
from __future__ import annotations

import argparse, json, os, re, sys

MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October "
    "November December".split(), 1)}

HEADER = re.compile(
    r"^### Case ([\w.]+) — (.+?) — (\d{1,2}) (\w+) (\d{4})(.*)$", re.M)


def field(body: str, name: str) -> str | None:
    m = re.search(rf"\*\*{name}:?\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)", body, re.S)
    if not m:
        return None
    return " ".join(m.group(1).split()) or None


def parse(md: str) -> list[dict]:
    app = md[md.find("# Appendix — the 24 cases"):]
    hits = list(HEADER.finditer(app))
    out = []
    for i, h in enumerate(hits):
        case_id, agent, dd, mon, yyyy, tail = h.groups()
        body = app[h.end(): hits[i + 1].start() if i + 1 < len(hits) else len(app)]
        day = f"{yyyy}-{MONTHS[mon]:02d}-{int(dd):02d}"
        verified = "[V]" in tail
        clusters = sorted(set(re.findall(r"\bCluster ([A-K])\b", body))
                          | set(re.findall(r"\b(K1-inv|K1|K2|A4|E3)\b", body)))
        ver = re.search(r"\*Verified:(.+?)\*", body, re.S)
        out.append({
            "agent": agent.strip(),
            "day": day,
            "split": "train",
            "source": "hand-read-case",
            "sampling_weight": None,
            "is_drift": None,          # adjudicate: several are unresolved
            "rationale": field(body, "Cause") or "",
            "labeled_by": "LW+prior-analysis",
            "labeled_at": None,
            "second_label": None,
            "monitor_flagged": None,   # filled by enrich_monitor.py
            "monitor_severity": None,
            "monitor_heading": None,
            "case_id": case_id,
            "assigned_goal": field(body, "Assigned goal"),
            "actually_working_toward": field(body, "Actually working toward"),
            "when_it_changed": field(body, "When it changed"),
            "clusters": clusters,      # train-only archival; not a label on eval rows
            "verified": verified,
            "verification_notes": " ".join(ver.group(1).split())[:600] if ver else None,
        })
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--taxonomy", default=os.path.expanduser(
        "~/ai-village-drift-taxonomy.md"))
    p.add_argument("--out", default="eval/train_24.jsonl")
    a = p.parse_args()
    rows = parse(open(a.taxonomy).read())
    if len(rows) != 24:
        print(f"WARNING: parsed {len(rows)} cases, expected 24", file=sys.stderr)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} train rows -> {a.out}")
    miss = [f"{r['case_id']} {r['agent']} {r['day']}" for r in rows if not r["assigned_goal"]]
    if miss:
        print("  no assigned_goal parsed for:", ", ".join(miss))


if __name__ == "__main__":
    main()
