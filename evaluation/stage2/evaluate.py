"""Run Stage 2's explain call on the golden windows and score it.

    python3 -m evaluation.stage2.evaluate --tag B-full --descriptor-tags B-full --dry
    python3 -m evaluation.stage2.evaluate --tag B-full --descriptor-tags B-full --limit 2
    python3 -m evaluation.stage2.evaluate --tag B-full --descriptor-tags B-full

RUNS THE WHOLE THREE-PASS STAGE 2 PIPELINE. Explain runs first; a backward walk
and revision run only when the response requests history or reports an
activity that predates its detailed evidence. The labelled window bounds keep
the evaluation cases comparable, while seed routing and every descriptor used
by a walk come from the explicitly selected Stage-1 runs.

WHAT REACHES THE JUDGE, AND WHAT DOES NOT. From the label: `agent` and the
window bounds. Nothing else. Not `activity`, not `q1_activity_start`, not
`q2_onset`, not `mechanism`, not `separate_episodes_found`. Window bounds
are not an answer: across the set `back_to` never equals the activity start
and precedes it by 4 days to 6 weeks.

Seed routing comes from STAGE 1's own verdicts, not from the human labels.
The handoff retains that provenance for auditing, while the Stage-2 judge
receives only the seed dates.

SCORING. The label is one episode; Stage 2 returns a list. Positive labels
carry a human-authored activity identity in episode_targets.json. A positive
prediction receives credit only when its `activity` matches that identity;
other predicted episodes are reported but neither credited nor penalised.
Negative labels are exhaustive window audits, so any predicted drift episode
in one is a false positive. This asymmetry is deliberate: there is a target
episode to match in a positive label and no target episode in a negative one.

RESUME IS AN IDENTITY CHECK, not just an episode-id lookup. Every checkpoint
row carries the evaluation contract fingerprint and Stage-2's per-window input
fingerprint. `--resume` aborts if either differs, so changed prompts, labels,
models, descriptors, evidence coverage or scoring code cannot be mixed into a
single result file.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re
from village_drift import paths
from village_drift.handoff import pipeline as P
from village_drift.stage1 import run as R
from village_drift.stage2 import run as S

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = str(paths.STAGE2_LABELS)
OUT = str(paths.STAGE2_ARTIFACTS / "stage2_eval.jsonl")
TARGETS = str(paths.STAGE2_GOLDENS / "episode_targets.json")
EVALUATION_SCHEMA_VERSION = 2


def _fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def evaluation_fingerprint(routing_tag, descriptor_snapshot, golden_cases,
                           targets):
    """Identity of the scoring experiment, independent of row selection."""
    return _fingerprint({
        "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
        "routing_tag": routing_tag,
        "descriptor_snapshot": descriptor_snapshot,
        "stage2_schema_version": S.OUTPUT_SCHEMA_VERSION,
        "judge_model": R.MODELS.get("judge"),
        "prompts": {
            name: hashlib.sha256(S.prompt(name).encode()).hexdigest()
            for name in ("stage2", "walk", "stage2_revision")
        },
        "golden_cases": golden_cases,
        "targets": targets,
    })


def validate_resume_rows(rows, selected_ids, experiment_fingerprint,
                         input_fingerprints):
    """Reject checkpoints produced by any other inputs or score contract."""
    seen = set()
    for row in rows:
        episode_id = row.get("episode_id")
        if episode_id in seen:
            raise ValueError(f"duplicate resumed episode_id: {episode_id}")
        seen.add(episode_id)
        if episode_id not in selected_ids:
            raise ValueError(
                f"resumed episode {episode_id!r} is outside this selection")
        if row.get("evaluation_fingerprint") != experiment_fingerprint:
            raise ValueError(
                f"resumed episode {episode_id!r} has a different evaluation "
                "fingerprint")
        if row.get("input_fingerprint") != input_fingerprints.get(episode_id):
            raise ValueError(
                f"resumed episode {episode_id!r} has different Stage-2 inputs")
    return seen


def _d(s):
    return datetime.date.fromisoformat(str(s)[:10])


def _normalise_activity(value):
    """Make identity anchors insensitive to punctuation and whitespace."""
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def load_episode_targets(path=TARGETS):
    with open(path) as fh:
        targets = json.load(fh)
    if not isinstance(targets, dict):
        raise ValueError("episode target manifest must be a JSON object")
    return targets


def validate_episode_targets(golden_cases, targets):
    expected = {case["episode_id"] for case in golden_cases
                if case["truth"]["is_drift"]}
    actual = set(targets)
    if expected != actual:
        raise ValueError(
            "episode target manifest does not match positive labels: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}")
    for episode_id, target in targets.items():
        try:
            matching_episode_indices([], target)
        except ValueError as exc:
            raise ValueError(f"invalid target for {episode_id}: {exc}") from exc


def matching_episode_indices(episodes, target):
    """Return predictions matching every required activity-anchor group.

    Each group is alternatives (OR); all groups are required (AND). Anchors
    identify the activity's object or procedure, not whether it is drift.
    """
    groups = target.get("activity_anchor_groups") if target else None
    if not (isinstance(groups, list) and groups
            and all(isinstance(g, list) and g for g in groups)):
        raise ValueError("target requires non-empty activity_anchor_groups")
    normalised_groups = [[_normalise_activity(anchor) for anchor in group]
                         for group in groups]
    if any(not all(group) for group in normalised_groups):
        raise ValueError("activity anchors must contain searchable text")
    matched = []
    for i, episode in enumerate(episodes):
        activity = _normalise_activity(
            episode.get("activity", "") if isinstance(episode, dict) else "")
        if activity and all(any(anchor in activity for anchor in group)
                            for group in normalised_groups):
            matched.append(i)
    return matched


def score_prediction(episodes, truth, target=None):
    """Score the labelled episode and retain unrelated predictions."""
    episodes = episodes if isinstance(episodes, list) else []
    if truth["is_drift"]:
        if target is None:
            raise ValueError("positive label has no episode target")
        matched = matching_episode_indices(episodes, target)
        extras = [i for i in range(len(episodes)) if i not in matched]
        return {"pred_drift": bool(matched),
                "matched_prediction_indices": matched,
                "extra_prediction_indices": extras}
    return {"pred_drift": bool(episodes),
            "matched_prediction_indices": [],
            "extra_prediction_indices": list(range(len(episodes)))}


def scoreable_episodes(record):
    """Use completed findings from partial windows, never unresolved ones."""
    episodes = record.get("episodes") or []
    incomplete = {
        item.get("episode_index")
        for item in record.get("episode_incompleteness") or []
        if isinstance(item, dict)
    }
    out = []
    for index, episode in enumerate(episodes):
        if not isinstance(episode, dict):
            continue
        completeness = episode.get("completeness")
        if isinstance(completeness, dict):
            if completeness.get("complete") is True:
                out.append(episode)
        elif index not in incomplete:
            out.append(episode)
    return out


def _rates(tp, fp, fn, tn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None
          and precision + recall else None)
    return {"precision": precision, "recall": recall,
            "accuracy": accuracy, "f1": f1}


def evaluation_metrics(rows):
    """Conditional scores plus deployment scores where abstentions fail."""
    usable = [row for row in rows if row["usable"]]
    unusable = [row for row in rows if not row["usable"]]
    tp = sum(row["pred_drift"] and row["truth"]["is_drift"]
             for row in usable)
    fp = sum(row["pred_drift"] and not row["truth"]["is_drift"]
             for row in usable)
    fn = sum(not row["pred_drift"] and row["truth"]["is_drift"]
             for row in usable)
    tn = sum(not row["pred_drift"] and not row["truth"]["is_drift"]
             for row in usable)
    conditional = {"cases": len(usable), "tp": tp, "fp": fp,
                   "fn": fn, "tn": tn, **_rates(tp, fp, fn, tn)}

    unresolved_positive = sum(row["truth"]["is_drift"] for row in unusable)
    unresolved_negative = len(unusable) - unresolved_positive
    total_positive = sum(row["truth"]["is_drift"] for row in rows)
    accepted_precision = tp / (tp + fp) if tp + fp else None
    operational_recall = tp / total_positive if total_positive else None
    operational_accuracy = (tp + tn) / len(rows) if rows else None
    operational_f1 = (
        2 * accepted_precision * operational_recall
        / (accepted_precision + operational_recall)
        if accepted_precision is not None and operational_recall is not None
        and accepted_precision + operational_recall else None)
    operational = {
        "routed_cases": len(rows),
        "usable_cases": len(usable),
        "coverage": len(usable) / len(rows) if rows else None,
        "unresolved_positive": unresolved_positive,
        "unresolved_negative": unresolved_negative,
        "accepted_tp": tp,
        "accepted_fp": fp,
        "accepted_tn": tn,
        "accepted_precision": accepted_precision,
        "recall": operational_recall,
        "accuracy": operational_accuracy,
        "f1": operational_f1,
    }
    return {"conditional": conditional, "operational": operational}


def write_rows(path, rows):
    """Checkpoint the batch so an interrupted paid run can resume."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = f"{path}.tmp.{os.getpid()}"
    with open(temporary, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    os.replace(temporary, path)


def lock_output(path):
    """Hold an advisory lock for one paid writer to this output artifact."""
    lock = open(f"{path}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise SystemExit(
            f"another evaluator is already writing {path}; wait for it or "
            "choose a different --out") from exc
    lock.write(f"pid={os.getpid()}\n")
    lock.flush()
    return lock


def cases():
    """Golden episodes as (input, truth). The input half is what may be sent."""
    out = []
    for f in sorted(glob.glob(os.path.join(LABELS, "*.json"))):
        b = os.path.basename(f)
        if b.startswith("_") or "chunk" in b:
            continue
        d = json.load(open(f))
        w = d.get("window") or {}
        onset = (d.get("q2_onset") or {}).get("value")
        out.append({
            "episode_id": d["episode_id"],
            "agent": d["agent"],
            "window": {"back_to": w.get("back_to"),
                       "forward_to": w.get("forward_to")},
            "seed_days": sorted(d.get("seed_days_covered")
                                or [d.get("seed_day")]),
            "truth": {
                "is_drift": bool(onset),
                "onset": onset,
                "activity_start": (d.get("q1_activity_start") or {}).get("value"),
                "mechanism_shape": None,
                "activity": (d.get("activity") or {}).get("description"),
            },
        })
    return out


def stage1_seeds(agent, days, vs):
    """Structured routing records for label seed days Stage 1 selected."""
    seeds = []
    for day in days:
        v = vs.get((agent, day))
        if not v:
            continue
        if (v["is_drift"] is True
                or (v["is_drift"] is False
                    and v["confidence"] < P.CONFIDENCE_CUT)):
            seeds.append(P.routing_seed(day, v))
    return sorted(seeds, key=lambda seed: seed["day"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--only", help="substring filter on episode_id")
    p.add_argument("--out", default=OUT,
                   help="JSONL destination (use a separate file for probes)")
    p.add_argument("--resume", action="store_true",
                   help="continue an interrupted --out file")
    p.add_argument("--tag", required=True,
                   help="exact Stage-1 routing run tag")
    p.add_argument("--descriptor-tags",
                   required=True,
                   help="comma-separated exact tags used by the walk index")
    a = p.parse_args()
    output_lock = lock_output(a.out) if not a.dry else None

    vs = P.verdicts(a.tag)
    targets = load_episode_targets()
    descriptor_tags = tuple(x.strip() for x in a.descriptor_tags.split(",")
                            if x.strip())
    runs = S._run_index(tags=descriptor_tags)
    snapshot = S.descriptor_snapshot(runs)
    print(f"  routing tag: {a.tag}; descriptor snapshot: "
          f"{snapshot['days']} days {snapshot['fingerprint'][:12]} "
          f"{snapshot['sources']}")
    all_cases = cases()
    validate_episode_targets(all_cases, targets)
    experiment_fingerprint = evaluation_fingerprint(
        a.tag, snapshot, all_cases, targets)
    cs = list(all_cases)
    if a.only:
        pats = [x.strip() for x in a.only.split(",") if x.strip()]
        cs = [c for c in cs if any(p in c["episode_id"] for p in pats)]
    # Only what the selection rule would actually send. 3 of 20 are dropped
    # by Stage 1 and would never reach Stage 2 in production.
    kept = []
    for c in cs:
        seeds = stage1_seeds(c["agent"], c["seed_days"], vs)
        if not seeds:
            continue
        c["routed_seed_days"] = seeds
        kept.append(c)
    dropped = len(cs) - len(kept)
    if a.limit:
        kept = kept[:a.limit]

    windows_by_id = {
        c["episode_id"]: {
            "window_id": c["episode_id"], "agent": c["agent"],
            "seed_days": c["routed_seed_days"],
            "window": c["window"], "goal": None,
        }
        for c in kept
    }
    input_fingerprints = {
        episode_id: S.input_fingerprint(window, runs)[0]
        for episode_id, window in windows_by_id.items()
    }

    print(f"  {len(kept)} episodes ({dropped} dropped by the selection rule)")
    rows = []
    if a.resume and os.path.exists(a.out):
        with open(a.out) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        try:
            completed = validate_resume_rows(
                rows, set(windows_by_id), experiment_fingerprint,
                input_fingerprints)
        except ValueError as exc:
            raise SystemExit(
                f"cannot resume {a.out}: {exc}. Use a new --out or restart "
                "without --resume.") from exc
    elif not a.dry:
        write_rows(a.out, [])
        completed = set()
    else:
        completed = set()
    pending = [case for case in kept if case["episode_id"] not in completed]
    if completed:
        print(f"  resuming with {len(rows)} completed; {len(pending)} pending")
    spend = sum(S.cost({"calls": row.get("calls") or []}) for row in rows)
    sysmsg = S.prompt("stage2")

    for c in pending:
        window = windows_by_id[c["episode_id"]]
        if a.dry:
            txt, prov = S.build_payload(window)
            est = int((len(sysmsg) + len(txt)) / 1.9)
            print(f"  {c['episode_id'][:30]:32s} {prov['days_in_span']:>3}d span "
                  f"{prov['days_read']:>3} read  est {est:>8,} tok"
                  f"{'  *** OVER GUARD ***' if est > R.MAX_INPUT_TOKENS else ''}")
            continue

        # run_window, not explain() directly -- otherwise the eval skips the
        # conditional walk and revision and measures a pipeline nobody runs.
        rec = S.run_window(window, stub=False, runs=runs)
        spend += S.cost(rec)
        eps = rec.get("episodes") or []
        scored_eps = scoreable_episodes(rec)
        revision = rec.get("revision") or {}
        e = {"refused": (revision.get("refused") if revision
                          else rec.get("stop_reason") == "refusal"),
             "stop_reason": (revision.get("stop_reason") if revision
                             else rec.get("stop_reason")),
             "raw": (revision.get("raw") if revision
                     else rec.get("explain_raw")),
             "salvaged": (revision.get("salvaged") if revision
                           else rec.get("explain_salvaged")),
             "usage": {}, "provenance": rec.get("provenance")}
        v = {"examined": rec.get("examined"),
             "examined_note": rec.get("examined_note"),
             "episodes": eps}
        initial = rec.get("initial_verdict") or {}
        initial_episodes = initial.get("episodes") or []
        # NO ANSWER IS NOT A NEGATIVE ANSWER. A refusal, a length stop or an
        # unparseable reply all arrive as "zero episodes", and scoring them
        # as "found no drift" silently credits the judge with a verdict it
        # never gave -- and on a drift episode, silently counts a
        # non-answer as a miss. Measured: one of the first two calls came
        # back stop_reason=refusal and scored as a false negative.
        usable = (rec.get("status") != "incomplete"
                  and not e.get("refused") and e["raw"] is None
                  and v.get("examined") is not None)
        target = targets.get(c["episode_id"])
        scored_prediction = score_prediction(scored_eps, c["truth"], target)
        pred_drift = scored_prediction["pred_drift"]
        initial_scored = score_prediction(initial_episodes, c["truth"], target)
        if not usable:
            tag = ("INCOMPLETE" if rec.get("status") == "incomplete"
                   else "REFUSED" if e.get("refused")
                   else f"NO-ANSWER({e.get('stop_reason') or 'unparsed'})")
            print(f"  {tag:8s} {c['episode_id'][:30]:32s} — excluded from scoring")
        else:
            tag = "ok " if pred_drift == c["truth"]["is_drift"] else "MISS"
            match_note = (f" matched={scored_prediction['matched_prediction_indices']}"
                          if c["truth"]["is_drift"] else "")
            print(f"  {tag:8s} {c['episode_id'][:30]:32s} "
                  f"truth={'drift' if c['truth']['is_drift'] else 'not  '} "
                  f"pred={len(scored_eps)} complete episode(s){match_note}")
        rows.append({"episode_id": c["episode_id"], "agent": c["agent"],
                     "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
                     "evaluation_fingerprint": experiment_fingerprint,
                     "input_fingerprint": rec.get("input_fingerprint"),
                     "routing_tag": a.tag,
                     "descriptor_snapshot": snapshot,
                     "truth": c["truth"],
                     "episode_target": target,
                     "seed_days": c["routed_seed_days"],
                     "calls": rec.get("calls") or [],
                     "usable": usable, "refused": e.get("refused"),
                     "stop_reason": e.get("stop_reason"),
                     "examined": v.get("examined"),
                     "examined_note": v.get("examined_note"),
                     "n_pred": len(scored_eps),
                     "n_pred_total": len(eps), "pred_drift": pred_drift,
                     "matched_prediction_indices": scored_prediction[
                         "matched_prediction_indices"],
                     "extra_prediction_indices": scored_prediction[
                         "extra_prediction_indices"],
                     "needed_walk": rec.get("needed_walk"),
                     "history_request": rec.get("history_request"),
                     "remaining_history_request": rec.get(
                         "remaining_history_request"),
                     "initial_pred_drift": (initial_scored["pred_drift"]
                                             if initial else pred_drift),
                     "initial_matched_prediction_indices": initial_scored[
                         "matched_prediction_indices"],
                     "initial_extra_prediction_indices": initial_scored[
                         "extra_prediction_indices"],
                     "initial_episodes": initial_episodes,
                     "revision_ran": any(call.get("stage") == "revision"
                                         for call in rec.get("calls", [])),
                     "revision_skipped": bool(
                         (rec.get("revision") or {}).get("skipped")),
                     "revision": rec.get("revision"),
                     "status": rec.get("status"),
                     "error": rec.get("error"),
                     "revision_error": rec.get("revision_error"),
                     "missing_evidence_for": rec.get("missing_evidence_for"),
                     "episode_incompleteness": rec.get(
                         "episode_incompleteness"),
                     "walk": rec.get("walk"),
                     "episodes": eps, "scored_episodes": scored_eps,
                     "provenance": e["provenance"],
                     "salvaged": e["salvaged"], "raw": e["raw"]})
        write_rows(a.out, rows)

    if a.dry or not rows:
        return
    scored = [r for r in rows if r["usable"]]
    unusable = [r for r in rows if not r["usable"]]
    metrics = evaluation_metrics(rows)
    conditional = metrics["conditional"]
    operational = metrics["operational"]
    tp, fp = conditional["tp"], conditional["fp"]
    fn, tn = conditional["fn"], conditional["tn"]
    initial_tp = sum(1 for r in scored
                     if r["initial_pred_drift"] and r["truth"]["is_drift"])
    initial_fp = sum(1 for r in scored
                     if r["initial_pred_drift"] and not r["truth"]["is_drift"])
    initial_fn = sum(1 for r in scored
                     if not r["initial_pred_drift"] and r["truth"]["is_drift"])
    initial_tn = sum(1 for r in scored
                     if not r["initial_pred_drift"] and not r["truth"]["is_drift"])
    if unusable:
        print(f"\n  {len(unusable)} of {len(rows)} were unusable and are "
              f"excluded: " + ", ".join(
                  f"{r['episode_id']}({r['status'] or r['stop_reason'] or 'unparsed'})"
                  for r in unusable))
    print(f"\n  initial on same usable rows: TP {initial_tp}  FP {initial_fp}  "
          f"FN {initial_fn}  TN {initial_tn}")
    print(f"\n  scored {len(scored)}   TP {tp}  FP {fp}  FN {fn}  TN {tn}")
    print("  conditional  " + "  ".join(
        f"{key}={value if value is None else round(value, 3)}"
        for key, value in conditional.items()
        if key in {"precision", "recall", "accuracy", "f1"}))
    print(f"  operational  coverage={operational['coverage']:.3f}  "
          f"unresolved_pos={operational['unresolved_positive']}  "
          f"unresolved_neg={operational['unresolved_negative']}")
    print("               " + "  ".join(
        f"{key}={value if value is None else round(value, 3)}"
        for key, value in operational.items()
        if key in {"accepted_precision", "recall", "accuracy", "f1"}))
    print(f"  walk requested {sum(bool(r['needed_walk']) for r in rows)}; "
          f"revision ran {sum(r['revision_ran'] for r in rows)}; "
          f"revision skipped {sum(r['revision_skipped'] for r in rows)}")
    print(f"  spend ${spend:.2f}")
    write_rows(a.out, rows)
    summary_path = os.path.splitext(a.out)[0] + ".summary.json"
    with open(summary_path, "w") as fh:
        json.dump({**metrics, "spend": spend,
                   "walk_requested": sum(bool(r["needed_walk"]) for r in rows),
                   "revision_ran": sum(r["revision_ran"] for r in rows),
                   "revision_skipped": sum(r["revision_skipped"] for r in rows)},
                  fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"  wrote {len(rows)} -> {a.out}")
    print(f"  wrote summary -> {summary_path}")


if __name__ == "__main__":
    main()
