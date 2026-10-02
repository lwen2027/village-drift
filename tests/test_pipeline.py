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
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "audit"))

from drift import config                      # noqa: E402
from drift import render as RENDER            # noqa: E402
from drift.evidence import STAGE2_EVIDENCE, evidence  # noqa: E402
import run as R                               # noqa: E402
import pipeline as P                          # noqa: E402
import stage2 as S                            # noqa: E402


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
    from drift.evidence import _clip_cmd
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
        "onset": "2026-01-10"}]}
    payload, provenance = S.build_revision_payload(
        window, initial, {"activity_start": "2026-01-01"}, {})
    assert len(payload) <= S.REVISION_PAYLOAD_CHARS
    assert "COMPACT DAILY SPINE" in payload
    assert "DAY 2026-01-01" in payload
    assert "DAY 2026-01-10" in payload
    assert provenance["spine_range"] == ["2026-01-01", "2026-01-12"]
    assert provenance["spine_days_total"] == 12
    assert provenance["spine_days_present"] == 0
    assert provenance["spine_days_missing"] == 12
    assert provenance["spine_sources"]["missing"] == 12


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
        {"activity_start": "2026-01-01"}, runs={})
    assert out["skipped"] is True
    assert out["usage"] is None
    assert out["blockers"] == {
        "required_days_missing": ["2026-01-01"]}


def test_run_window_uses_revision_as_the_final_verdict(monkeypatch):
    initial = {"examined": True, "examined_note": "draft", "episodes": [{
        "activity": "draft", "activity_predates_window": True}]}
    revised = {"examined": True, "examined_note": "reconsidered",
               "episodes": [{"activity": "revised",
                              "activity_predates_window": False}]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {"pass": 1}, "salvaged": False,
        "usage": {"input_tokens": 1}, "payload_chars": 10,
        "stop_reason": "end_turn", "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start": "2026-01-01", "activity_start_note": "found",
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


def test_unusable_revision_leaves_the_window_incomplete(monkeypatch):
    initial = {"examined": True, "episodes": [{
        "activity": "draft", "activity_predates_window": True}]}
    monkeypatch.setattr(S, "explain", lambda *a, **k: {
        "verdict": initial, "provenance": {}, "salvaged": False,
        "usage": {}, "payload_chars": 1, "stop_reason": "end_turn",
        "refused": False, "raw": None})
    monkeypatch.setattr(S, "walk", lambda *a, **k: {
        "activity_start": "2026-01-01", "truncated": False, "usage": {}})
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
        "activity_start": "2026-01-01", "truncated": False,
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


def test_cost_counts_cache_writes_and_retries():
    """stage2's cost function omitted the 1.25x cache write; the refusal
    retry returned only the last attempt's usage, hiding ~$4."""
    one = R.call_cost({"cache_creation_input_tokens": 1_000_000},
                      "claude-opus-5-5")
    assert abs(one - 5.0) < 1e-9
    assert R.call_cost({"input_tokens": 1}, "no-such-model") is None
