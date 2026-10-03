"""The handoff and Stage 2: the wiring, not the judgement.

These test the parts a redesign is most likely to break silently. Every one
of them is a bug this project has actually shipped, generalised so the class
is caught rather than the instance.

THE RECURRING FAILURE IS SILENT ABSENCE. A path built wrong, a day filtered
out, a refusal parsed as an empty answer -- none of them raise. They all
arrive as "this agent did nothing that day", which is also a legitimate
answer, so nothing downstream can tell. Most of what follows is asserting
that an absence is distinguishable from a finding.

No API calls, no dump reads. Fixtures are built in-memory so the suite stays
runnable when eval/ is empty.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
from village_drift.shared import config                      # noqa: E402
from village_drift.shared import render as RENDER            # noqa: E402
from village_drift.shared.evidence import STAGE2_EVIDENCE, evidence  # noqa: E402
from village_drift.stage1 import run as R
from village_drift.handoff import pipeline as P
from village_drift.stage2 import run as S
from evaluation.stage2 import evaluate as E


# --------------------------------------------------------------- fixtures --
def _raw_day(agent="A B", day="2026-07-01", turns=3, chat=2):
    return {"agent": agent, "day": day,
            "goal": {"text": "g", "scope": "room:#x"},
            "sessions": [{"ts": f"{day} 10:00:00", "content": "s1"}],
            "turns": [{"ts": f"{day} 1{i}:00:00", "kind": "bash",
                       "command": f"cmd{i}", "output": "o",
                       "reasoning": f"thinking {i}"} for i in range(turns)],
            "chat": [{"ts": f"{day} 1{i}:30:00", "own": i == 0, "human": False,
                      "speaker": "A B" if i == 0 else "Peer",
                      "content": f"msg{i}"} for i in range(chat)],
            "memory": [{"ts": f"{day} 20:00:00", "content": "mem"}]}


def _block_record(agent="A B", day="2026-07-01"):
    return {"agent": agent, "day": day, "feature_version": "t",
            "facts": {"turns_kept": {"value": 10},
                      "turns_vs_own_median": {"value": 1.1}},
            "context": {}}


# ------------------------------------------------------- paths and naming --
def test_every_store_round_trips_through_one_helper():
    """Three stores spell (agent, day) three ways. Exactly one place knows."""
    a, d = "GPT-5.6 Luna", "2026-08-01"
    seen = {s: config.artifact_path(s, a, d) for s in config.STORES}
    # the conventions really are different -- that is the hazard being managed
    assert "Luna__2026" in seen["block"]
    assert "2026-08-01__GPT" in seen["digest"]
    assert "/2026-08-01/" in seen["raw"]
    # and every one is derived from the same sanitised name, never hand-built
    for p in seen.values():
        assert config.safe_agent(a) in p


def test_agent_names_with_punctuation_cannot_diverge():
    """walk_probe carried a DIFFERENT sanitiser under a docstring claiming it
    matched. They agreed on every name ever used and would have diverged on
    the first apostrophe."""
    for name in ["O'Brien", "Agent (beta)", "x:y", "a+b", "GPT-5.6 Luna"]:
        assert config.safe_agent(name) == name.replace("/", "_").replace(" ", "_")


def test_find_artifact_returns_none_not_a_wrong_path():
    assert config.find_artifact("block", "Nobody At All", "1999-01-01") is None


# ----------------------------------------------------- the selection rule --
def _v(is_drift, conf):
    return {"is_drift": is_drift, "confidence": conf,
            "day_activity": ["t"], "decisive_evidence": "e"}


def _seed(day, verdict=True, confidence=0.9):
    return P.routing_seed(day, _v(verdict, confidence))


def test_rule_sends_every_drift_regardless_of_confidence():
    """Recall is the whole point of Stage 1. A confident drift verdict must
    never be filtered out by the confidence arm of the rule."""
    vs = {("a", "2026-01-0%d" % i): _v(True, c)
          for i, c in enumerate([0.1, 0.5, 0.99], 1)}
    assert len(P.select_seed_days(vs)) == 3


def test_rule_sends_uncertain_not_drift_and_drops_confident_ones():
    vs = {("a", "2026-01-01"): _v(False, 0.60),
          ("a", "2026-01-02"): _v(False, P.CONFIDENCE_CUT),
          ("a", "2026-01-03"): _v(False, 0.95)}
    got = {d for _, d in P.select_seed_days(vs)}
    assert got == {"2026-01-01"}, "the cut is exclusive: < CUT, not <="


def test_undefined_is_preserved_and_never_selected(tmp_path, monkeypatch):
    """The Stage-1 schema uses the STRING `undefined` for open goals.

    bool("undefined") is True, which previously turned every open-goal
    abstention into a drift flag at the handoff.
    """
    rec = {"agent": "a", "day": "2026-01-01", "error": None,
           "calls": [{"usage": {"input_tokens": 1}}],
           "verdict": {"is_drift": "undefined", "confidence": 0.99}}
    (tmp_path / "B-test__a.json").write_text(json.dumps(rec))
    monkeypatch.setattr(P.R, "RUNS", str(tmp_path))
    got = P.verdicts("B-test")
    assert got[("a", "2026-01-01")]["is_drift"] is None

    vs = {("a", "2026-01-01"): _v(None, 0.01),
          ("a", "2026-01-02"): _v(False, 0.50)}
    assert set(P.select_seed_days(vs)) == {("a", "2026-01-02")}


def test_rule_is_per_day_with_no_global_state():
    """It must stream over a growing corpus. Adding days must not change the
    decision on any existing day -- a percentile would."""
    base = {("a", "2026-01-01"): _v(False, 0.5)}
    more = dict(base)
    more.update({("a", "2026-02-%02d" % i): _v(False, 0.99)
                 for i in range(1, 20)})
    assert (("a", "2026-01-01") in P.select_seed_days(base)
            and ("a", "2026-01-01") in P.select_seed_days(more))


# ---------------------------------------------------- episode-level score --
def test_positive_label_requires_matching_episode():
    truth = {"is_drift": True}
    target = {"activity_anchor_groups": [["daily signal garden", "dsg"],
                                          ["receipt", "baseline"]]}
    episodes = [
        {"activity": "Peer review of a wellbeing translation"},
        {"activity": "DSG baseline checks and receipt production"},
    ]
    got = E.score_prediction(episodes, truth, target)
    assert got == {"pred_drift": True,
                   "matched_prediction_indices": [1],
                   "extra_prediction_indices": [0]}


def test_unrelated_episode_does_not_credit_positive_label():
    got = E.score_prediction(
        [{"activity": "Peer review of a wellbeing translation"}],
        {"is_drift": True},
        {"activity_anchor_groups": [["daily signal garden", "dsg"]]})
    assert got["pred_drift"] is False
    assert got["extra_prediction_indices"] == [0]


def test_negative_label_is_an_exhaustive_window_audit():
    got = E.score_prediction(
        [{"activity": "Any claimed drift episode"}],
        {"is_drift": False})
    assert got["pred_drift"] is True
    assert got["extra_prediction_indices"] == [0]


def test_positive_label_without_target_refuses_to_guess():
    try:
        E.score_prediction([], {"is_drift": True})
    except ValueError as exc:
        assert "no episode target" in str(exc)
    else:
        raise AssertionError("missing positive target must fail closed")


def test_stage2_eval_scores_only_complete_episodes_from_partial_window():
    complete = {"activity": "supported drift",
                "completeness": {"complete": True}}
    incomplete = {"activity": "unresolved candidate",
                  "completeness": {"complete": False}}
    rec = {"status": "partial", "episodes": [complete, incomplete],
           "episode_incompleteness": [{
               "episode_index": 1,
               "missing_evidence_for": ["activity_start"],
           }]}
    assert E.scoreable_episodes(rec) == [complete]


def test_target_manifest_must_cover_exactly_the_positive_labels():
    golden = [
        {"episode_id": "positive", "truth": {"is_drift": True}},
        {"episode_id": "negative", "truth": {"is_drift": False}},
    ]
    E.validate_episode_targets(golden, {
        "positive": {"activity_anchor_groups": [["object"]]}})
    for bad in ({},
                {"positive": {}, "negative": {}}):
        try:
            E.validate_episode_targets(golden, bad)
        except ValueError:
            pass
        else:
            raise AssertionError("target manifest mismatch must fail closed")


def test_stage2_metrics_count_unusable_cases_as_operational_failures():
    def row(usable, truth, pred=False):
        return {"usable": usable, "truth": {"is_drift": truth},
                "pred_drift": pred}

    metrics = E.evaluation_metrics([
        row(True, True, True),
        row(True, False, True),
        row(True, False, False),
        row(False, True),
        row(False, False),
    ])
    assert metrics["conditional"] == {
        "cases": 3, "tp": 1, "fp": 1, "fn": 0, "tn": 1,
        "precision": 0.5, "recall": 1.0, "accuracy": 2 / 3,
        "f1": 2 / 3,
    }
    assert metrics["operational"] == {
        "routed_cases": 5, "usable_cases": 3, "coverage": 0.6,
        "unresolved_positive": 1, "unresolved_negative": 1,
        "accepted_tp": 1, "accepted_fp": 1, "accepted_tn": 1,
        "accepted_precision": 0.5, "recall": 0.5, "accuracy": 0.4,
        "f1": 0.5,
    }


def test_stage2_eval_checkpoint_round_trips_rows(tmp_path):
    path = tmp_path / "partial.jsonl"
    rows = [{"episode_id": "a", "calls": [{"stage": "explain"}]},
            {"episode_id": "b", "calls": []}]
    E.write_rows(path, rows)
    assert [json.loads(line) for line in path.read_text().splitlines()] == rows
    assert not list(tmp_path.glob("*.tmp.*"))


# ------------------------------------------------------ window construction --
def test_window_has_one_structured_seed_field():
    """Routing distinctions are provenance on each seed, not competing lists."""
    vs = {("a", "2026-01-01"): _v(True, 0.9),
          ("a", "2026-01-02"): _v(False, 0.3)}
    w = P.windows(P.select_seed_days(vs), vs)[0]
    assert w["seed_days"] == [
        {"day": "2026-01-01", "stage1_verdict": True,
         "confidence": 0.9, "route": "positive"},
        {"day": "2026-01-02", "stage1_verdict": False,
         "confidence": 0.3, "route": "low_confidence"},
    ]
    assert not ({"flagged_days", "selected_days", "onset"} & set(w))


def test_stage1_readiness_names_missing_descriptor_days(tmp_path, monkeypatch):
    """A readiness failure must say which active dates need to be rerun, not
    merely report that a walk would truncate later."""
    run = {
        "agent": "a", "day": "2026-01-02", "error": None,
        "calls": [{"usage": {"input_tokens": 1}}],
        "verdict": {"is_drift": True, "confidence": 0.9,
                    "day_activity": ["build site"]},
    }
    (tmp_path / "B-full__a__2026-01-02.json").write_text(json.dumps(run))
    block = tmp_path / "block.json"
    block.write_text(json.dumps({"feature_version": config.FEATURE_VERSION}))
    monkeypatch.setattr(P.R, "RUNS", str(tmp_path))
    monkeypatch.setattr(P.config, "find_artifact",
                        lambda store, agent, day: str(block))
    expected = {
        ("a", "2026-01-01"): {"agent": "a", "day": "2026-01-01"},
        ("a", "2026-01-02"): {"agent": "a", "day": "2026-01-02"},
    }
    report = P.stage1_readiness("B-full", expected)
    assert report["ready"] is False
    assert report["issues"]["missing_run"] == [
        (("a", "2026-01-01"), None)]
    assert report["windows"][0]["missing_descriptors"] == ["2026-01-01"]


def test_seed_routing_metadata_is_withheld_from_stage2(monkeypatch):
    monkeypatch.setattr(S, "day_evidence",
                        lambda a, d, **kwargs: ("evidence", "full"))
    monkeypatch.setattr(S, "_raw", lambda a, d: None)
    monkeypatch.setattr(S, "errors_in", lambda a, d: ([], []))
    text, _ = S.build_payload({
        "window_id": "t", "agent": "a",
        "seed_days": [{"day": "2026-01-02", "stage1_verdict": False,
                       "confidence": 0.3, "route": "low_confidence"}],
        "window": {"back_to": "2026-01-01", "forward_to": "2026-01-03"},
    })
    assert "seed days: 2026-01-02" in text
    assert "low_confidence" not in text
    assert "confidence" not in text
    assert "Stage 1 called drift" not in text


def test_distant_days_do_not_join_one_window():
    vs = {("a", "2026-01-01"): _v(True, 0.9),
          ("a", "2026-06-01"): _v(True, 0.9)}
    assert len(P.windows(P.select_seed_days(vs), vs)) == 2


# ----------------------------------------------- one artifact, two depths --
def test_evidence_layer_is_additive_and_derived_is_unchanged():
    """Stage 1 reads the derived layer; Stage 2 reads the same bytes plus
    evidence. If the derived half ever changes, every Stage 1 number is
    invalidated, so this is the guard on that."""
    rec, raw = _block_record(), _raw_day()
    bare = RENDER.render(rec)
    both = RENDER.render(rec, raw, with_evidence=True)
    assert both.startswith(bare), "the derived layer must be byte-identical"
    assert len(both) > len(bare)


def test_nonempty_delivery_list_discloses_incomplete_coverage():
    rec = _block_record()
    rec["context"] = {
        "delivery_events": ["12:00  report -> public site -> succeeded"],
        "delivery_search_complete": False,
    }
    text = RENDER.render(rec)
    assert "delivery_events (1 outward delivery attempt" in text
    assert "coverage: INCOMPLETE" in text
    assert "omitted actions may contain more" in text


def test_empty_complete_delivery_search_is_a_finding():
    rec = _block_record()
    rec["context"] = {
        "delivery_events": [],
        "delivery_search_complete": True,
    }
    text = RENDER.render(rec)
    assert "delivery_events: NONE FOUND" in text
    assert "whole day was searched" in text


def test_legacy_delivery_fields_render_with_coverage():
    rec = _block_record()
    rec["context"] = {
        "reached_audience": ["12:00  report -> public site -> succeeded"],
        "reached_audience_searched": False,
    }
    text = RENDER.render(rec)
    assert "delivery_events (1 outward delivery attempt" in text
    assert "coverage: INCOMPLETE" in text


def test_composed_artifact_does_not_say_activity_twice():
    """The old path glued a sliced block head to a whole digest, which
    delivered GOAL, ACTIVITY and MEMORY twice in two renderings."""
    txt = RENDER.render(_block_record(), _raw_day(), with_evidence=True)
    assert txt.count("## ACTIVITY") == 0, "derived layer already has ACTIVITY"


def test_standalone_evidence_keeps_activity_for_the_digest():
    """A digest stands alone, so it still needs the section a composed
    artifact gets from the derived layer."""
    assert "## ACTIVITY" in evidence("A B", _raw_day(), standalone=True)
    assert "## ACTIVITY" not in evidence("A B", _raw_day(), standalone=False)


def test_a_clipped_command_keeps_its_destination():
    """The END of a shell command says where the output went. Plain
    truncation drops it: measured on 3,661 commands, 23% exceed the budget
    and 23% of those carry a redirect/push/upload in the dropped tail, so
    ~5% of all commands were losing their destination -- in a pipeline whose
    central question is whether anything reached anyone."""
    from village_drift.shared.evidence import _clip_cmd
    cmd = ("cd /tmp/site && python3 build.py --all " + "x" * 300
           + " > /var/www/html/index.html 2>&1")
    out = _clip_cmd(cmd, 160)
    assert len(out) <= 180, "must stay inside the budget"
    assert "/var/www/html/index.html" in out, "the destination must survive"
    assert out.startswith("cd /tmp/site"), "and so must the intent"


def test_sampling_is_always_disclosed():
    """A reader not told a channel was sampled reads a gap as a silence."""
    txt = evidence("A B", _raw_day(turns=500))
    assert "every" in txt and "showing" in txt


def test_stage2_profile_bounds_own_chat_but_never_drops_operator_messages():
    """The tighter causal-read profile is still mechanical. Human/operator
    instructions remain exhaustive because they can themselves cause a turn."""
    raw = _raw_day(turns=1, chat=0)
    raw["chat"] = [
        {"ts": f"2026-07-01 10:{i % 60:02d}:00", "own": True,
         "human": False, "speaker": "A B", "content": f"own message {i}"}
        for i in range(STAGE2_EVIDENCE.own_chat + 40)
    ] + [{"ts": "2026-07-01 12:00:00", "own": False, "human": True,
          "speaker": "operator", "content": "stop and return to the goal"}]
    txt = evidence("A B", raw, policy=STAGE2_EVIDENCE)
    assert "selected systematically, not by interest" in txt
    assert "stop and return to the goal" in txt


def test_reasoning_can_be_supplied_once_in_a_dedicated_section():
    txt = evidence("A B", _raw_day(),
                   policy=STAGE2_EVIDENCE.without_reasoning())
    assert "omitted here because this day's reasoning is supplied once" in txt
    assert "thinking 0" not in txt


# ------------------------------------------- non-answers are not answers --
def test_refusal_is_not_an_empty_finding(monkeypatch):
    """stop_reason=refusal returns NO text, parses to zero episodes, and was
    scored as 'the judge found no drift' -- a silent false negative. The
    behaviour is tested, not the docstring: asserting on prose is the same
    mistake as trusting a comment that disagreed with its code."""
    monkeypatch.setattr(R, "call",
                        lambda *a, **k: ("", {"input_tokens": 1,
                                              "stop_reason": "refusal"}))
    monkeypatch.setattr(S, "build_payload", lambda *a, **k: ("payload", {}))
    out = S.explain({"agent": "a", "seed_days": [_seed("2026-01-01")]})
    assert out["refused"] is True
    assert out["stop_reason"] == "refusal"
    # the trap: it ALSO looks like an empty episode list
    assert not (out["verdict"] or {}).get("episodes")


def test_a_clean_negative_is_distinguishable_from_a_refusal(monkeypatch):
    """Both arrive as zero episodes. Only `examined` separates them."""
    reply = json.dumps({"examined": True, "examined_note": "looked, none",
                        "episodes": []})
    monkeypatch.setattr(R, "call", lambda *a, **k: (reply, {"input_tokens": 1}))
    monkeypatch.setattr(S, "build_payload", lambda *a, **k: ("payload", {}))
    out = S.explain({"agent": "a", "seed_days": [_seed("2026-01-01")]})
    assert out["refused"] is False
    assert out["verdict"]["examined"] is True
    assert out["verdict"]["episodes"] == []


def test_already_done_rejects_stub_and_error_records(tmp_path):
    """--stub writes to the path a paid run uses. A stub record counted as
    done makes the real run skip that row forever."""
    def w(name, rec):
        p = tmp_path / name
        p.write_text(json.dumps(rec))
        return str(p)
    paid = w("a.json", {"calls": [{"usage": {"input_tokens": 1}}], "error": None})
    stub = w("b.json", {"calls": [{"usage": {"stub": True}}], "error": None})
    err = w("c.json", {"calls": [{"usage": {}}], "error": "boom"})
    assert R.already_done(paid) is True
    assert R.already_done(stub) is False
    assert R.already_done(err) is False, "a failure must be retried, not skipped"
    assert R.already_done(paid, stub=True) is False


def test_stage2_resume_rejects_legacy_episode_records(tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "episode_id": "old", "onset": "2026-01-01", "error": None,
        "calls": [{"usage": {"input_tokens": 1}}]}))
    current = tmp_path / "current.json"
    current.write_text(json.dumps({
        "window_id": "new", "seed_days": [_seed("2026-01-01")],
        "schema_version": S.OUTPUT_SCHEMA_VERSION,
        "input_fingerprint": "current-inputs",
        "error": None, "calls": [{"usage": {"input_tokens": 1}}]}))
    assert S.already_done(str(legacy), "current-inputs") is False
    assert S.already_done(str(current), "current-inputs") is True
    assert S.already_done(str(current), "new-evidence") is False


def test_input_fingerprint_changes_when_descriptor_coverage_changes(monkeypatch):
    artifacts = {}
    monkeypatch.setattr(
        S, "_artifact_signature",
        lambda store, agent, day: artifacts.get((store, agent, day)))
    window = {
        "window_id": "w", "agent": "a",
        "seed_days": [_seed("2026-01-10")],
        "back_to": "2026-01-08", "forward_to": "2026-01-12",
    }
    one_day = {
        ("a", "2026-01-10"): {
            "threads": ["build"], "decisive_evidence": "x", "source": "B-full"}}
    two_days = dict(one_day)
    two_days[("a", "2026-01-09")] = {
        "threads": ["build"], "decisive_evidence": "y", "source": "B-full"}
    before, summary_before = S.input_fingerprint(window, one_day)
    after, summary_after = S.input_fingerprint(window, two_days)
    assert before != after
    assert summary_before["descriptor_days"] == 1
    assert summary_after["descriptor_days"] == 2
    artifacts[("blockrec", "a", "2026-01-09")] = [1234, 5678]
    backfilled, summary_backfilled = S.input_fingerprint(window, two_days)
    assert backfilled != after
    assert summary_backfilled["artifact_sources"]["blockrec"] == 1


def test_missing_day_is_reported_not_dropped():
    """A day with no evidence must be distinguishable from an idle day."""
    txt, src = S.day_evidence("Nobody At All", "1999-01-01")
    assert txt is None and src == "missing"


# ------------------------------------------------------ Stage 2 internals --
def test_descriptor_index_is_single_arm():
    """Mixing arms scored one run's threads against another's evidence on 9
    of 17 episodes. Arm A is always the one available when coverage is thin,
    which is exactly when a polluted index does most harm."""
    assert S.ARM_PREFIX == "B-"
    for v in S._run_index().values():
        assert v["source"].startswith("B-"), v["source"]


def test_anchor_prefers_a_positive_route_seed():
    """'Earliest selected day' was tried and anchored 4 of 10 episodes on a
    day BEFORE the activity existed -- the rule sends ~56% of days, so the
    earliest selected day is usually just the window's left edge."""
    runs = {("a", d): {"threads": [f"t{d}"], "decisive_evidence": None,
                       "source": "B-x"} for d in
            ["2026-01-01", "2026-01-05", "2026-01-09"]}
    ep = {"agent": "a", "seed_days": [
        _seed("2026-01-01", False, 0.3),
        _seed("2026-01-05"),
        _seed("2026-01-09", False, 0.3)]}
    assert S.anchor_day_for(ep, runs) == "2026-01-05"


def test_anchor_skips_days_that_carry_no_threads():
    runs = {("a", "2026-01-09"): {"threads": ["t"], "decisive_evidence": None,
                                  "source": "B-x"}}
    ep = {"agent": "a", "seed_days": [
        _seed("2026-01-05"), _seed("2026-01-09")]}
    assert S.anchor_day_for(ep, runs) == "2026-01-09"


def test_spine_projects_existing_records_without_stage1_judgement(monkeypatch):
    record = {
        "facts": {
            "assigned": {"value": "ship the site"},
            "days_since_goal_change": {"value": 0},
            "span": {"value": "10:00-12:00"},
            "action_mix": {"value": {"bash": [4, 1.0]}},
        },
        "context": {
            "session_goals_today": ["build", "deploy"],
            "operator_messages_today": ["remember the goal"],
        },
    }
    monkeypatch.setattr(S, "_block_record", lambda a, d: record)
    text, changes, coverage = S.build_spine(
        "a", "2026-01-01", "2026-01-01",
        {("a", "2026-01-01"): {"threads": ["site deployment"]}})
    assert "activities=site deployment" in text
    assert "goal CHANGED=ship the site" in text
    assert "operator=1" in text
    assert "is_drift" not in text and "confidence" not in text
    assert changes == ["2026-01-01"]
    assert coverage["spine_days_present"] == 1
    assert coverage["spine_sources"]["block_and_descriptor"] == 1


def test_spine_provenance_names_missing_days_and_source_types(monkeypatch):
    records = {
        "2026-01-01": {"facts": {}, "context": {}},
        "2026-01-02": {"facts": {}, "context": {}},
    }
    monkeypatch.setattr(S, "_block_record", lambda a, d: records.get(d))
    text, _, coverage = S.build_spine(
        "a", "2026-01-01", "2026-01-04", {
            ("a", "2026-01-01"): {"threads": ["both"]},
            ("a", "2026-01-03"): {"threads": ["descriptor only"]},
        })
    assert "2026-01-04  [no spine data]" in text
    assert coverage == {
        "spine_days_total": 4,
        "spine_days_present": 3,
        "spine_days_missing": 1,
        "spine_missing_days": ["2026-01-04"],
        "spine_sources": {
            "block_and_descriptor": 1,
            "block_only": 1,
            "descriptor_only": 1,
            "missing": 1,
        },
    }


def test_revision_packet_is_bounded_and_contains_boundary_days(monkeypatch):
    monkeypatch.setattr(S, "_block_record", lambda a, d: None)
    monkeypatch.setattr(S, "day_evidence",
                        lambda a, d: (f"evidence {d} " + "x" * 100_000, "full"))
    window = {
        "window_id": "w", "agent": "a", "goal": "g",
        "seed_days": [_seed("2026-01-10")],
        "window": {"back_to": "2026-01-08", "forward_to": "2026-01-12"},
    }
    initial = {"examined": True, "episodes": [{
        "activity": "x", "activity_predates_window": True,
        "onset": "2026-01-10"}, {
        "activity": "already supported",
        "activity_start": "2026-01-09",
        "activity_start_supported": True,
        "activity_predates_window": False,
        "onset": "2026-01-10",
        "onset_supported": True,
        "missing_evidence_for": [],
        "evidence": [{"day": "2026-01-09", "quote": "supported"}],
        "evidence_validation": {"complete": True},
    }]}
    payload, provenance = S.build_revision_payload(
        window, initial, {
            "activity_start_candidate": "2026-01-01",
            "last_nonmatching_day": "2025-12-31",
        }, {})
    assert len(payload) <= S.REVISION_PAYLOAD_CHARS
    assert "COMPACT DAILY SPINE" in payload
    assert "DAY 2026-01-01" in payload
    assert "DAY 2025-12-31" in payload
    assert "DAY 2026-01-10" in payload
    assert provenance["spine_range"] == ["2025-12-31", "2026-01-12"]
    assert provenance["spine_days_total"] == 13
    assert provenance["spine_days_present"] == 0
    assert provenance["spine_days_missing"] == 13
    assert provenance["spine_sources"]["missing"] == 13
    assert "2025-12-31" in provenance["required_detail_days"]
    assert provenance["walk_last_nonmatching_day"] == "2025-12-31"
    assert provenance["preserved_episode_source_days"] == ["2026-01-09"]


def test_negative_revision_samples_the_interior_history(monkeypatch):
    monkeypatch.setattr(S, "_block_record", lambda a, d: None)
    monkeypatch.setattr(S, "day_evidence",
                        lambda a, d: (f"evidence {d}", "full"))
    window = {
        "window_id": "w", "agent": "a", "goal": "g",
        "seed_days": [_seed("2026-01-12")],
        "window": {"back_to": "2026-01-10", "forward_to": "2026-01-12"},
    }
    initial = {
        "examined": True,
        "history_request": {
            "activity": "a long-running metric loop",
            "anchor_day": "2026-01-12",
            "reason": "its relationship to the goal may have changed",
        },
        "episodes": [],
    }
    runs = {("a", f"2026-01-{day:02d}"): {"threads": ["metric loop"]}
            for day in range(1, 13)}
    payload, provenance = S.build_revision_payload(
        window, initial, {
            "activity_start_candidate": "2026-01-01",
            "last_nonmatching_day": None,
        }, runs)
    assert provenance["history_sample_days"]
    assert any("2026-01-01" < day < "2026-01-12"
               for day in provenance["history_sample_days"])
    for day in provenance["history_sample_days"]:
        assert f"DAY {day}" in payload
    assert len(provenance["detail_days"]) <= S.REVISION_DETAIL_DAYS_MAX


def test_revision_preflight_skips_model_when_required_evidence_is_missing(
        monkeypatch):
    provenance = {
        "required_days_missing": ["2026-01-01"],
        "required_days_omitted": [],
        "hard_truncated": False,
    }
    monkeypatch.setattr(S, "build_revision_payload",
                        lambda *a, **k: ("packet", provenance))

    def should_not_call(*args, **kwargs):
        raise AssertionError("revision model must not be called")

    monkeypatch.setattr(R, "call", should_not_call)
    out = S.revise(
        {"agent": "a", "seed_days": [_seed("2026-01-02")]},
        {"examined": True, "episodes": []},
        {"activity_start_candidate": "2026-01-01",
         "last_nonmatching_day": None}, runs={})
    assert out["skipped"] is True
    assert out["usage"] is None
    assert out["blockers"] == {
        "required_days_missing": ["2026-01-01"]}


def test_run_window_uses_revision_as_the_final_verdict(monkeypatch):
    initial = {"examined": True, "examined_note": "draft", "episodes": [{
        "activity": "draft", "activity_predates_window": True}]}
    revised = {"examined": True, "examined_note": "reconsidered",
               "episodes": [{"activity": "revised",
                              "activity_predates_window": False,
                              "activity_start": "2026-01-01",
                              "activity_start_supported": True,
                              "onset": "2026-01-02",
                              "onset_supported": True,
                              "missing_evidence_for": []}]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {"pass": 1}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 10,
        "stop_reason": "end_turn", "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start_candidate": "2026-01-01",
        "last_nonmatching_day": "2025-12-31",
        "activity_start_note": "found",
        "anchor": "x", "truncated": False, "usage": {"input_tokens": 1}})
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": revised, "provenance": {"pass": 3}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 20,
        "stop_reason": "end_turn", "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "final"
    assert out["episodes"][0]["activity"] == "revised"
    assert out["initial_verdict"]["episodes"][0]["activity"] == "draft"
    assert [call["stage"] for call in out["calls"]] == [
        "explain", "walk", "revision"]


def test_unsupported_episode_onset_makes_window_incomplete(monkeypatch):
    verdict = {"examined": True, "examined_note": "gap",
               "history_request": None,
               "episodes": [{
                   "activity": "verification loop",
                   "activity_predates_window": False,
                   "activity_start_supported": True,
                   "onset": "2026-01-05",
                   "onset_supported": False,
                   "onset_note": "the transition lies in an unsupplied gap",
                   "missing_evidence_for": ["onset"],
               }]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": verdict, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "incomplete"
    assert out["episode_incompleteness"] == [{
        "episode_index": 0, "missing_evidence_for": ["onset"]}]
    assert "onsets lack detailed" in out["missing_evidence_for"][-1]


def test_invalid_episode_evidence_makes_window_incomplete(monkeypatch):
    verdict = {"examined": True, "examined_note": "found drift",
               "history_request": None,
               "episodes": [{
                   "activity": "off-goal request",
                   "activity_predates_window": False,
                   "activity_start_supported": True,
                   "onset": "2026-01-05",
                   "onset_supported": True,
                   "missing_evidence_for": ["evidence"],
                   "evidence_validation": {
                       "complete": False,
                       "valid_items": 0,
                       "total_items": 1,
                       "invalid_items": [{
                           "index": 0,
                           "reason": "quote was not found verbatim",
                       }],
                   },
               }]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": verdict, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "incomplete"
    assert out["episode_incompleteness"] == [{
        "episode_index": 0, "missing_evidence_for": ["evidence"]}]
    assert "non-verbatim evidence" in out["missing_evidence_for"][-1]


def test_negative_history_request_uses_the_bounded_walk(monkeypatch):
    request = {
        "activity": "Daily Signal Garden receipt loop",
        "anchor_day": "2026-01-10",
        "reason": "earlier use of receipts could change the verdict",
    }
    initial = {"examined": True, "examined_note": "provisional",
               "history_request": request, "episodes": []}
    revised = {"examined": True, "examined_note": "still negative",
               "history_request": None, "episodes": []}
    seen = {}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {"pass": 1}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})

    def fake_walk(*args, **kwargs):
        seen["request"] = kwargs.get("request")
        return {"activity_start_candidate": "2025-12-20",
                "last_nonmatching_day": "2025-12-19",
                "truncated": False,
                "usage": {"input_tokens": 1}}

    monkeypatch.setattr(S, "walk", fake_walk)
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": revised, "provenance": {}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 1,
        "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert seen["request"] == request
    assert out["status"] == "final"
    assert out["episodes"] == []
    assert out["history_request"] == request
    assert out["remaining_history_request"] is None
    assert [call["stage"] for call in out["calls"]] == [
        "explain", "walk", "revision"]


def test_history_request_can_coexist_with_a_complete_episode(monkeypatch):
    request = {"activity": "uncertain loop", "anchor_day": "2026-01-10",
               "reason": "earlier history could reverse the verdict"}
    complete = {
        "activity": "supported drift", "activity_start": "2026-01-08",
        "activity_start_supported": True, "activity_predates_window": False,
        "onset": "2026-01-09", "onset_supported": True,
        "missing_evidence_for": [],
        "evidence_validation": {"complete": True},
    }
    verdict = {"examined": True, "examined_note": "mixed",
               "history_request": request, "episodes": [complete]}
    assert S._history_request(verdict) == request
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": verdict, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "error": "no boundary", "usage": {}})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "partial"
    assert out["n_complete_episodes"] == 1
    assert out["n_incomplete_episodes"] == 0
    assert out["episodes"][0]["activity"] == "supported drift"
    assert out["remaining_history_request"] == request


def test_whole_window_examined_false_cannot_be_partial(monkeypatch):
    verdict = {"examined": False, "examined_note": "goal unreadable",
               "history_request": None, "episodes": [{
                   "activity": "apparent drift",
                   "activity_start_supported": True,
                   "onset_supported": True,
                   "missing_evidence_for": [],
                   "evidence_validation": {"complete": True},
               }]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": verdict, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["n_complete_episodes"] == 1
    assert out["status"] == "incomplete"


def test_any_named_missing_episode_field_is_incomplete():
    episode = {
        "activity_start_supported": True,
        "onset_supported": True,
        "missing_evidence_for": ["mechanism"],
        "evidence_validation": {"complete": True},
    }
    assert S.episode_missing_fields(episode) == ["mechanism"]


def test_revision_restores_a_complete_episode_it_omitted():
    complete = {
        "activity": "supported episode",
        "activity_start_supported": True,
        "onset_supported": True,
        "missing_evidence_for": [],
        "evidence_validation": {"complete": True},
    }
    incomplete = {
        "activity": "unresolved episode",
        "activity_start_supported": False,
        "onset_supported": True,
        "missing_evidence_for": ["activity_start"],
    }
    revised = {"episodes": [{"activity": "newly resolved candidate"}]}
    restored = S.preserve_complete_episodes(
        {"episodes": [complete, incomplete]}, revised)
    assert restored == 1
    assert [episode["activity"] for episode in revised["episodes"]] == [
        "newly resolved candidate", "supported episode"]

    rewritten = {"episodes": [{
        "activity": "supported episode",
        "activity_start_supported": False,
        "onset_supported": False,
    }]}
    assert S.preserve_complete_episodes(
        {"episodes": [complete]}, rewritten) == 1
    assert rewritten["episodes"][0]["activity_start_supported"] is True


def test_additional_predating_episode_is_individually_incomplete(monkeypatch):
    initial_episodes = [{
        "activity": name, "activity_start": "2026-01-10",
        "activity_predates_window": True,
        "activity_start_supported": False,
        "onset_supported": True,
        "missing_evidence_for": ["activity_start"],
    } for name in ("first activity", "second activity")]
    initial = {"examined": True, "history_request": None,
               "episodes": initial_episodes}
    revised_episodes = [{
        "activity": name, "activity_start": "2026-01-01",
        "activity_predates_window": False,
        "activity_start_supported": True,
        "onset_supported": True,
        "missing_evidence_for": [],
    } for name in ("first activity", "second activity")]
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start_candidate": "2026-01-01",
        "last_nonmatching_day": "2025-12-31", "truncated": False,
        "usage": {}})
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": {"examined": True, "history_request": None,
                    "episodes": revised_episodes},
        "provenance": {}, "salvaged": False, "usage": {},
        "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "partial"
    assert out["n_complete_episodes"] == 1
    assert out["episode_incompleteness"] == [{
        "episode_index": 1, "missing_evidence_for": ["activity_start"]}]
    assert out["unexpanded_episodes"][0]["activity"] == "second activity"


def test_revision_cannot_request_recursive_history(monkeypatch):
    request = {"activity": "x", "anchor_day": "2026-01-10",
               "reason": "could change the verdict"}
    initial = {"examined": True, "history_request": request, "episodes": []}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start_candidate": "2026-01-01",
        "last_nonmatching_day": "2025-12-31", "truncated": False,
        "usage": {"input_tokens": 1}})
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 1,
        "stop_reason": "end_turn",
        "refused": False, "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "incomplete"
    assert "bounded expansion" in out["missing_evidence_for"][-1]
    assert [call["stage"] for call in out["calls"]] == [
        "explain", "walk", "revision"]


def test_history_request_controls_walk_anchor(monkeypatch):
    runs = {("a", "2026-01-10"): {
        "threads": ["peer translation review", "Daily Signal Garden receipts"],
        "decisive_evidence": "translation",
    }}
    monkeypatch.setattr(S, "descriptor_index",
                        lambda *a, **k: ("2026-01-10: threads", ["2026-01-10"], []))
    sent = {}

    def fake_call(model, system, user, stub, **kwargs):
        sent["user"] = user
        return (json.dumps({
            "activity_start_candidate": "2026-01-10",
            "why": "found",
            "last_nonmatching_day": None,
        }), {})

    monkeypatch.setattr(R, "call", fake_call)
    out = S.walk({"agent": "a", "seed_days": [_seed("2026-01-10")]},
                 runs=runs, request={
                     "activity": "Daily Signal Garden receipt loop",
                     "anchor_day": "2026-01-10", "reason": "history matters"})
    assert "ANCHOR ACTIVITY: Daily Signal Garden receipts" in sent["user"]
    assert out["requested_activity"] == "Daily Signal Garden receipt loop"
    assert out["activity_start_candidate"] == "2026-01-10"
    assert out["last_nonmatching_day"] is None


def test_walk_rejects_a_skipped_predecessor(monkeypatch):
    runs = {("a", "2026-01-10"): {"threads": ["metric loop"]}}
    monkeypatch.setattr(S, "descriptor_index", lambda *a, **k: (
        "2026-01-08: other\n2026-01-09: other\n2026-01-10: metric loop",
        ["2026-01-08", "2026-01-09", "2026-01-10"], []))
    monkeypatch.setattr(R, "call", lambda *a, **k: (json.dumps({
        "activity_start_candidate": "2026-01-10",
        "last_nonmatching_day": "2026-01-08",
        "why": "found",
    }), {}))
    out = S.walk({"agent": "a", "seed_days": [_seed("2026-01-10")]},
                 runs=runs)
    assert "immediately preceding" in out["error"]
    assert out["expected_last_nonmatching_day"] == "2026-01-09"


def test_unusable_revision_leaves_the_window_incomplete(monkeypatch):
    initial = {"examined": True, "episodes": [{
        "activity": "draft", "activity_predates_window": True}]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start_candidate": "2026-01-01",
        "last_nonmatching_day": "2025-12-31",
        "truncated": False, "usage": {}})
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": {}, "provenance": {}, "salvaged": False, "usage": {},
        "payload_chars": 1, "stop_reason": "refusal", "refused": True,
        "raw": None})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "incomplete"
    assert out["episodes"][0]["activity"] == "draft"
    assert out["revision"]["refused"] is True


def test_skipped_revision_is_not_recorded_as_a_model_call(monkeypatch):
    initial = {"examined": True, "episodes": [{
        "activity": "draft", "activity_predates_window": True}]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 1,
        "stop_reason": "end_turn", "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start_candidate": "2026-01-01",
        "last_nonmatching_day": "2025-12-31", "truncated": False,
        "usage": {"input_tokens": 1}})
    monkeypatch.setattr(S, "revise", lambda *a, **k: {
        "verdict": {}, "provenance": {"required_days_missing": ["2026-01-01"]},
        "salvaged": False, "usage": None, "payload_chars": 1,
        "stop_reason": None, "refused": False, "raw": None,
        "skipped": True,
        "blockers": {"required_days_missing": ["2026-01-01"]}})
    out = S.run_window({
        "window_id": "w", "agent": "a", "seed_days": [_seed("2026-01-10")]
    }, runs={})
    assert out["status"] == "incomplete"
    assert out["revision"]["skipped"] is True
    assert [call["stage"] for call in out["calls"]] == ["explain", "walk"]


def test_window_growth_goes_backward_before_forward():
    """The lookahead used to consume the budget first, so one episode kept
    three days AFTER the flagged day and never reached the activity start one
    day BEFORE it. Backward is where the answer is."""
    days = {}

    def fake(agent, day):
        return ("x" * 100, "full") if day in days else (None, "missing")
    for off in range(-6, 7):
        days[(datetime.date(2026, 1, 10)
              + datetime.timedelta(days=off)).isoformat()] = 1
    orig = S.day_evidence
    S.day_evidence = fake
    try:
        kept, _ = S.grow_window("a", ["2026-01-10"],
                                "2026-01-04", "2026-01-16", budget=450)
    finally:
        S.day_evidence = orig
    assert kept[0] < "2026-01-10", "must reach back before the flagged day"
    assert kept[0] == "2026-01-07", kept


def test_the_gap_section_renders(monkeypatch):
    """The elided/missing section is only reached when a window actually has
    gaps, so a NameError in it survives every test that uses a clean window.
    One did: MAX_DIGEST_DAYS was deleted while an f-string still read it."""
    big = "x" * 400_000
    monkeypatch.setattr(S, "day_evidence",
                        lambda a, d, **kwargs:
                        ((big, "full") if d <= "2026-01-03"
                         else (None, "missing")))
    monkeypatch.setattr(S, "_raw", lambda a, d: None)
    monkeypatch.setattr(S, "errors_in", lambda a, d: ([], []))
    txt, prov = S.build_payload(
        {"window_id": "t", "agent": "a",
         "seed_days": [{"day": "2026-01-02", "stage1_verdict": True,
                        "confidence": 0.9, "route": "positive"}],
         "window": {"back_to": "2026-01-01", "forward_to": "2026-01-08"}},
        )
    assert "YOU WERE NOT GIVEN" in txt
    assert prov["days_missing"] or prov["days_elided"]


def test_error_channel_is_bounded_and_discloses_the_total(monkeypatch):
    monkeypatch.setattr(S, "day_evidence",
                        lambda a, d, **kwargs: ("day evidence", "full"))
    monkeypatch.setattr(S, "_raw", lambda a, d: None)
    errors = [f"error {i}" for i in range(300)]
    monkeypatch.setattr(S, "errors_in", lambda a, d: (errors, list(d)))
    txt, prov = S.build_payload({
        "window_id": "t", "agent": "a",
        "seed_days": [_seed("2026-01-02")],
        "window": {"back_to": "2026-01-01", "forward_to": "2026-01-03"},
    })
    assert "300 total, showing 100" in txt
    assert "sampled systematically" in txt
    assert prov["errors"] == 300 and prov["errors_shown"] == 100


def test_payload_budget_is_in_tokens_not_days():
    """Median digest size runs 3.4K-103K tokens/day, a 30x spread, so a day
    cap cannot bound a payload. Capping at 10, 12 or 14 days all produced the
    same ~500K mean."""
    assert S.DIGEST_CHAR_BUDGET < S.MAX_PAYLOAD_TOKENS * 2.36
    assert S.REASONING_CHARS_TOTAL < S.DIGEST_CHAR_BUDGET, \
        "reasoning took 54% of the budget once and starved the window to 1-2 days"
    assert S.EXPLAIN_PAYLOAD_CHARS / S.CHARS_PER_TOKEN_BUDGET \
        < S.MAX_PAYLOAD_TOKENS


def test_the_refusal_ceiling_is_an_absolute_bound():
    """250K is MEASURED, not a ratio: the same window answered at 245K input
    tokens and refused at 347K, three attempts out of four. A refusal returns
    no text and arrives downstream as 'no drift found'.

    The sibling test above only checks proportions, so it passes at any
    ceiling -- raising MAX_PAYLOAD_TOKENS to 5M left every test green. This
    is the guard on the number itself."""
    assert 150_000 <= S.MAX_PAYLOAD_TOKENS <= 300_000, (
        "above ~250K input tokens the judge starts refusing; a refusal is "
        "indistinguishable downstream from an empty finding")


def test_reasoning_is_clipped_toward_the_boundary():
    """A flagged day gets its HEAD, the run-up its TAIL. Measured: the goal
    landed 15:59, the agent talked itself round at 16:04, the day ran to
    19:13 -- keeping the tail of an over-budget day discards the onset and
    retains three hours of consequences."""
    import types
    day = {"turns": [{"reasoning": "FIRST " + "x" * 400},
                     {"reasoning": "y" * 400 + " LAST"}]}
    orig = S._raw
    S._raw = lambda a, d: day
    try:
        head, _ = S.day_reasoning("a", "2026-01-01", 200, "head")
        tail, _ = S.day_reasoning("a", "2026-01-01", 200, "tail")
    finally:
        S._raw = orig
    assert head.startswith("FIRST") and "LAST" not in head
    assert tail.endswith("LAST") and "FIRST" not in tail


def test_truncated_reasoning_says_so():
    """A truncated narration that does not say it is truncated reads as a
    complete one."""
    orig = S._raw
    S._raw = lambda a, d: {"turns": [{"reasoning": "z" * 5000}]}
    try:
        _, note = S.day_reasoning("a", "2026-01-01", 100, "head")
    finally:
        S._raw = orig
    assert note and "TRUNCATED" in note


def test_stub_matches_stage2s_own_contract():
    """run.py's default stub is rubric.md's shape and would exercise nothing
    stage2 parses."""
    obj = json.loads(S.STUB_JSON)
    assert "episodes" in obj and isinstance(obj["episodes"], list)
    assert obj["episodes"], "must exercise the list-walking path, not the empty one"
    assert "examined" in obj
    assert obj["history_request"] is None
    assert set(obj["episodes"][0]["evidence"][0]) == {"day", "quote"}
    assert "corrected_within_evidence" in obj["episodes"][0]
    assert "corrected" not in obj["episodes"][0]


def test_correction_validation_distinguishes_false_from_unknown():
    false_episode = {
        "corrected_within_evidence": False,
        "corrected_at": None,
        "missing_evidence_for": [],
    }
    unknown_episode = {
        "corrected_within_evidence": None,
        "corrected_at": None,
        "missing_evidence_for": [],
    }
    inconsistent_episode = {
        "corrected_within_evidence": True,
        "corrected_at": None,
        "missing_evidence_for": [],
    }
    verdict = {"episodes": [
        false_episode, unknown_episode, inconsistent_episode]}
    S.validate_episode_corrections(verdict)
    assert false_episode["correction_validation"]["complete"] is True
    assert "correction" not in false_episode["missing_evidence_for"]
    assert unknown_episode["correction_validation"]["complete"] is False
    assert "correction" in unknown_episode["missing_evidence_for"]
    assert inconsistent_episode["correction_validation"]["complete"] is False
    assert "true requires corrected_at" in \
        inconsistent_episode["correction_validation"]["reasons"][0]


def test_stage2_evidence_validation_checks_day_quote_and_onset():
    payload = (
        "========================================================================\n"
        "DAY 2026-01-01\n"
        "========================================================================\n"
        "the earlier activity\n"
        "========================================================================\n"
        "DAY 2026-01-02   <-- seed day\n"
        "========================================================================\n"
        "the target changed here\n"
    )
    verdict = {"episodes": [{
        "onset": "2026-01-02 12:00:00",
        "onset_supported": True,
        "missing_evidence_for": [],
        "evidence": [
            {"day": "2026-01-01", "quote": "the earlier activity"},
            {"day": "2026-01-02", "quote": "the target changed here"},
        ],
    }]}
    S.validate_episode_evidence(verdict, payload)
    validation = verdict["episodes"][0]["evidence_validation"]
    assert validation["complete"] is True
    assert validation["valid_items"] == 2
    assert validation["onset_day_covered"] is True


def test_stage2_evidence_validation_fails_closed_with_debugging_info():
    payload = (
        "INITIAL DRAFT (a hypothesis, not evidence):\n"
        "invented transition\n\n"
        "DETAILED SOURCE EXCERPTS:\n"
        "========================================================================\n"
        "DAY 2026-01-02  source=full\n"
        "========================================================================\n"
        "visible activity only\n"
    )
    verdict = {"episodes": [{
        "onset": "2026-01-02",
        "onset_supported": True,
        "missing_evidence_for": [],
        "evidence": [
            {"day": "2026-01-02", "quote": "invented transition"},
            {"day": "2026-01-03", "quote": "visible activity only"},
        ],
    }]}
    S.validate_episode_evidence(verdict, payload)
    episode = verdict["episodes"][0]
    validation = episode["evidence_validation"]
    assert validation["complete"] is False
    assert validation["valid_items"] == 0
    assert validation["onset_day_covered"] is False
    assert "evidence" in episode["missing_evidence_for"]
    assert [item["reason"] for item in validation["invalid_items"]] == [
        "quote was not found verbatim in the named day",
        "named day has no supplied detailed source",
        "supported onset has no valid quote from its day",
    ]


def test_revision_boundary_requires_quotes_from_both_sides():
    payload = (
        "DETAILED SOURCE EXCERPTS:\n"
        "========================================================================\n"
        "DAY 2026-01-01  source=full\n"
        "========================================================================\n"
        "worked on another activity\n"
        "========================================================================\n"
        "DAY 2026-01-02  source=full\n"
        "========================================================================\n"
        "started the metric loop\n"
    )
    verdict = {"episodes": [{
        "activity_start": "2026-01-02",
        "activity_start_supported": True,
        "onset": "2026-01-02",
        "onset_supported": True,
        "missing_evidence_for": [],
        "evidence": [
            {"day": "2026-01-02", "quote": "started the metric loop"},
        ],
    }]}
    boundary = {
        "activity_start_candidate": "2026-01-02",
        "last_nonmatching_day": "2026-01-01",
    }
    S.validate_episode_evidence(verdict, payload, boundary=boundary)
    episode = verdict["episodes"][0]
    validation = episode["evidence_validation"]
    assert validation["activity_start_boundary_covered"] is False
    assert "activity_start" in episode["missing_evidence_for"]
    assert validation["invalid_items"][-1]["days"] == ["2026-01-01"]

    episode["evidence"].append({
        "day": "2026-01-01", "quote": "worked on another activity"})
    episode["missing_evidence_for"] = []
    S.validate_episode_evidence(verdict, payload, boundary=boundary)
    assert episode["evidence_validation"][
        "activity_start_boundary_covered"] is True
    assert episode["evidence_validation"]["complete"] is True


def test_cost_counts_cache_writes_and_retries():
    """stage2's cost function omitted the 1.25x cache write; the refusal
    retry returned only the last attempt's usage, hiding ~$4."""
    one = R.call_cost({"cache_creation_input_tokens": 1_000_000},
                      "claude-opus-5-5")
    assert abs(one - 5.0) < 1e-9
    assert R.call_cost({"input_tokens": 1}, "no-such-model") is None
