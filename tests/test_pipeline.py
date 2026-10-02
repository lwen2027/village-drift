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
from drift.evidence import evidence           # noqa: E402
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


def test_rule_sends_every_drift_regardless_of_confidence():
    """Recall is the whole point of Stage 1. A confident drift verdict must
    never be filtered out by the confidence arm of the rule."""
    vs = {("a", "2026-01-0%d" % i): _v(True, c)
          for i, c in enumerate([0.1, 0.5, 0.99], 1)}
    assert len(P.selected_days(vs)) == 3


def test_rule_sends_uncertain_not_drift_and_drops_confident_ones():
    vs = {("a", "2026-01-01"): _v(False, 0.60),
          ("a", "2026-01-02"): _v(False, P.CONFIDENCE_CUT),
          ("a", "2026-01-03"): _v(False, 0.95)}
    got = {d for _, d in P.selected_days(vs)}
    assert got == {"2026-01-01"}, "the cut is exclusive: < CUT, not <="


def test_rule_is_per_day_with_no_global_state():
    """It must stream over a growing corpus. Adding days must not change the
    decision on any existing day -- a percentile would."""
    base = {("a", "2026-01-01"): _v(False, 0.5)}
    more = dict(base)
    more.update({("a", "2026-02-%02d" % i): _v(False, 0.99)
                 for i in range(1, 20)})
    assert (("a", "2026-01-01") in P.selected_days(base)
            and ("a", "2026-01-01") in P.selected_days(more))


# ------------------------------------------------------ window construction --
def test_flagged_and_selected_are_not_the_same_field():
    """A day can be SELECTED for low confidence while the verdict was
    not-drift. Telling Stage 2 it was 'flagged as drift' is a false statement
    about its own input, and would reintroduce the drift-presupposing bias
    the prompt was rewritten to remove."""
    vs = {("a", "2026-01-01"): _v(True, 0.9),
          ("a", "2026-01-02"): _v(False, 0.3)}
    w = P.windows(P.selected_days(vs), vs)[0]
    assert w["selected_days"] == ["2026-01-01", "2026-01-02"]
    assert w["flagged_days"] == ["2026-01-01"]


def test_distant_days_do_not_join_one_window():
    vs = {("a", "2026-01-01"): _v(True, 0.9),
          ("a", "2026-06-01"): _v(True, 0.9)}
    assert len(P.windows(P.selected_days(vs), vs)) == 2


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
    out = S.explain({"agent": "a", "onset": "2026-01-01",
                     "flagged_days": ["2026-01-01"]}, None)
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
    out = S.explain({"agent": "a", "onset": "2026-01-01",
                     "flagged_days": ["2026-01-01"]}, None)
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


def test_anchor_prefers_a_flagged_day_over_the_window_edge():
    """'Earliest selected day' was tried and anchored 4 of 10 episodes on a
    day BEFORE the activity existed -- the rule sends ~56% of days, so the
    earliest selected day is usually just the window's left edge."""
    runs = {("a", d): {"threads": [f"t{d}"], "decisive_evidence": None,
                       "source": "B-x"} for d in
            ["2026-01-01", "2026-01-05", "2026-01-09"]}
    ep = {"agent": "a", "onset": "2026-01-09",
          "flagged_days": ["2026-01-05"],
          "selected_days": ["2026-01-01", "2026-01-05", "2026-01-09"]}
    assert S.anchor_day_for(ep, runs) == "2026-01-05"


def test_anchor_skips_days_that_carry_no_threads():
    runs = {("a", "2026-01-09"): {"threads": ["t"], "decisive_evidence": None,
                                  "source": "B-x"}}
    ep = {"agent": "a", "onset": "2026-01-09",
          "flagged_days": ["2026-01-05", "2026-01-09"], "selected_days": []}
    assert S.anchor_day_for(ep, runs) == "2026-01-09"


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
        kept, _ = S.grow_window("a", ["2026-01-10"], "2026-01-10",
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
                        lambda a, d: ((big, "full") if d <= "2026-01-03"
                                      else (None, "missing")))
    monkeypatch.setattr(S, "_raw", lambda a, d: None)
    monkeypatch.setattr(S, "errors_in", lambda a, d: ([], []))
    txt, prov = S.build_payload(
        {"episode_id": "t", "agent": "a", "onset": "2026-01-02",
         "flagged_days": ["2026-01-02"], "selected_days": ["2026-01-02"],
         "window": {"back_to": "2026-01-01", "forward_to": "2026-01-08"}},
        None)
    assert "YOU WERE NOT GIVEN" in txt
    assert prov["days_missing"] or prov["days_elided"]


def test_payload_budget_is_in_tokens_not_days():
    """Median digest size runs 3.4K-103K tokens/day, a 30x spread, so a day
    cap cannot bound a payload. Capping at 10, 12 or 14 days all produced the
    same ~500K mean."""
    assert S.DIGEST_CHAR_BUDGET < S.MAX_PAYLOAD_TOKENS * 2.36
    assert S.REASONING_CHARS_TOTAL < S.DIGEST_CHAR_BUDGET, \
        "reasoning took 54% of the budget once and starved the window to 1-2 days"


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
