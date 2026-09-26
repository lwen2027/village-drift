"""Record a manual verification pass over one labelled agent-day.

Two tables, deliberately separate:

  eval_100.jsonl      the LABEL   — is_drift and the one-paragraph case for it
  verification.jsonl  the AUDIT   — which claims were checked against source,
                                    what each one turned out to be, and what
                                    changed as a result

They are separate because a label is a conclusion and an audit is a history.
Across this project roughly a third of deeply-read cases had a load-bearing
error in them, so knowing WHICH parts of a label were checked against raw logs
— and which are still first-pass inference — is worth as much as the label.

    from eval.verify import record
    record(agent, day, claims=[...], turning_points=[...])

Written only during the manual step-through; nothing generates it automatically.

Writing a verification with `new_reasoning` or `new_is_drift` updates the label
table too, so the two never drift apart.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, "eval_100.jsonl")
AUDIT = os.path.join(HERE, "verification.jsonl")

# What a checked claim can turn out to be.
VERDICTS = {
    "confirmed",   # the source says what the label said
    "corrected",   # partly right; the label's version was materially off
    "refuted",     # the source contradicts it outright
    "unresolved",  # checked, and the record does not settle it
}


def _load(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def _write(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def record(agent: str, day: str, *, claims, turning_points=(),
           sources=(), verified_by="claude-opus-5", verified_at="2026-09-26",
           new_reasoning=None, new_is_drift="unchanged", new_text=None,
           notes=None) -> dict:
    """Append (or replace) the audit row for one agent-day, and sync the label."""
    for c in claims:
        if c["verdict"] not in VERDICTS:
            raise ValueError(f"bad verdict {c['verdict']!r}; expected one of {VERDICTS}")

    labels = _load(LABELS)
    row = next((r for r in labels if r["agent"] == agent and r["day"] == day), None)
    if row is None:
        raise KeyError(f"{agent} {day} is not in {LABELS}")

    # No label_before snapshot and no separate corrections list: what the first
    # pass got wrong is already carried by the `refuted`/`corrected` claims and
    # their evidence, and duplicating it invites the two copies to disagree.
    audit = {
        "agent": agent, "day": day,
        "verified_by": verified_by, "verified_at": verified_at,
        "claims": list(claims),
        "turning_points": list(turning_points),
        "sources": list(sources),
        "notes": notes,
    }

    changed = []
    if new_is_drift != "unchanged" and new_is_drift != row["is_drift"]:
        row["is_drift"] = new_is_drift
        changed.append("is_drift")
    if new_reasoning:
        row["reasoning"] = " ".join(new_reasoning.split())
        changed.append("reasoning")
    if new_text:
        row["text"] = " ".join(new_text.split())
        changed.append("text")
    row["verified"] = True
    audit["label_changed"] = changed

    rows = [r for r in _load(AUDIT) if not (r["agent"] == agent and r["day"] == day)]
    rows.append(audit)
    rows.sort(key=lambda r: (r["day"], r["agent"]))
    _write(AUDIT, rows)
    _write(LABELS, labels)

    n = {v: sum(1 for c in claims if c["verdict"] == v) for v in VERDICTS}
    print(f"{agent} {day}: {len(claims)} claims checked "
          + " · ".join(f"{k} {v}" for k, v in n.items() if v)
          + (f"   LABEL UPDATED: {', '.join(changed)}" if changed else "   label unchanged"))
    return audit


def status() -> None:
    labels, audit = _load(LABELS), _load(AUDIT)
    done = {(r["agent"], r["day"]) for r in audit}
    lab = [r for r in labels if r["is_drift"] is not None]
    print(f"{len(labels)} sampled · {len(lab)} labelled · {len(audit)} verified")
    for r in lab:
        k = (r["agent"], r["day"])
        print(f"  {'[v]' if k in done else '[ ]'} {r['day']} {r['agent'][:22]:23s} "
              f"{'DRIFT' if r['is_drift'] else 'not drift'}")


if __name__ == "__main__":
    status()
