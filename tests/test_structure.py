"""Structural tests: date ranges, day assignment, lookback, no leakage.

These exist because a validation pass against independently-parsed ground
truth found two real defects that unit tests on pure functions could never
catch — both about *which rows end up on which day*.

Fixtures are synthetic and written to a temp dir, so these run in
milliseconds and never touch the 2GB dump.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile

from drift import build, config, load


# --- fixture ----------------------------------------------------------------

AGENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _write(dirpath, name, rows):
    with gzip.open(os.path.join(dirpath, name), "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def make_fixture(dirpath):
    """Two agents. One session CROSSES MIDNIGHT (the bug this caught)."""
    _write(dirpath, "agents.jsonl.gz", [
        {"id": AGENT, "name": "Agent A"}, {"id": OTHER, "name": "Agent B"}])
    _write(dirpath, "agent_goals.jsonl.gz", [
        {"agent_id": AGENT, "name": "Maximize widgets",
         "start_time": "2026-08-01 00:00:00", "end_time": None}])
    # village_goals stores its text under "goal", NOT "name" like agent_goals.
    # The fixture used "name", so this path was never actually exercised.
    _write(dirpath, "village_goals.jsonl.gz", [
        {"goal": "Village goal", "start_time": "2026-01-01 00:00:00",
         "end_time": None}])
    _write(dirpath, "chat_rooms.jsonl.gz", [])

    sessions, turns = [], []
    for day in ("2026-08-10", "2026-08-11", "2026-08-12"):
        sid = f"s-{day}"
        sessions.append({"id": sid, "agent_id": AGENT,
                         "created_at": f"{day} 10:00:00",
                         "session_goal": f"work on {day}"})
        for i in range(5):
            turns.append({"session_id": sid,
                          "created_at": f"{day} 10:0{i}:00",
                          "agent_action": {"command": f"echo {day} {i}"},
                          "agent_messages": {"content": "doing a thing"},
                          "output": "", "error": ""})
    # session opens 2026-08-11 23:58 and runs past midnight into 08-12
    sessions.append({"id": "s-cross", "agent_id": AGENT,
                     "created_at": "2026-08-11 23:58:00",
                     "session_goal": "late session"})
    turns.append({"session_id": "s-cross", "created_at": "2026-08-11 23:59:00",
                  "agent_action": {"command": "echo before midnight"},
                  "agent_messages": {"content": ""}, "output": "", "error": ""})
    for i in range(3):  # these happen on 08-12
        turns.append({"session_id": "s-cross",
                      "created_at": f"2026-08-12 00:0{i}:00",
                      "agent_action": {"command": f"echo after midnight {i}"},
                      "agent_messages": {"content": ""}, "output": "", "error": ""})
    # a second agent, to prove per-agent isolation
    sessions.append({"id": "s-other", "agent_id": OTHER,
                     "created_at": "2026-08-11 12:00:00",
                     "session_goal": "other agent work"})
    turns.append({"session_id": "s-other", "created_at": "2026-08-11 12:00:00",
                  "agent_action": {"command": "echo other"},
                  "agent_messages": {"content": ""}, "output": "", "error": ""})

    _write(dirpath, "computer_use_sessions.jsonl.gz", sessions)
    _write(dirpath, "computer_use_turns.jsonl.gz", turns)
    _write(dirpath, "agent_memories.jsonl.gz", [
        {"agent_id": AGENT, "created_at": f"{d} 16:00:00",
         "content": "## GOAL\nMaximize widgets\n- P01 keep shipping"}
        for d in ("2026-08-10", "2026-08-11", "2026-08-12")])
    _write(dirpath, "chat_messages.jsonl.gz", [
        {"created_at": "2026-08-11 09:00:00", "speaker_type": "agent",
         "agent_speaker_id": AGENT, "content": "hello"}])


def _run(start, end):
    return build.build(start, end, verbose=False)


def setup_module(module):
    module._tmp = tempfile.mkdtemp()
    make_fixture(module._tmp)
    module._orig = config.DATA_DIR
    config.DATA_DIR = module._tmp


def teardown_module(module):
    config.DATA_DIR = module._orig
    shutil.rmtree(module._tmp, ignore_errors=True)


# --- tests ------------------------------------------------------------------

def test_only_requested_days_emitted():
    recs = _run("2026-08-11", "2026-08-11")
    assert {r["day"] for r in recs} == {"2026-08-11"}, \
        "lookback days must feed state but never be emitted"


def test_all_active_agents_present():
    recs = _run("2026-08-11", "2026-08-11")
    assert {r["agent"] for r in recs} == {"Agent A", "Agent B"}


def test_lookback_supplies_history_but_not_records():
    """Day 1 of a partial run must still see prior days."""
    recs = [r for r in _run("2026-08-12", "2026-08-12") if r["agent"] == "Agent A"]
    assert len(recs) == 1
    assert recs[0]["context"]["prior_active_days"], \
        "prior days exist in the dump; lookback should surface them"
    assert recs[0]["context"]["prior_snapshot_outline"], \
        "prior memory snapshot should be available via lookback"


def test_turns_counted_on_the_day_they_HAPPENED():
    """Cross-midnight regression.

    A session opened 2026-08-11 23:58 has 1 turn on the 11th and 3 on the 12th.
    Keying turns by SESSION day puts all 4 on the 11th, which silently moves
    work onto the wrong date. Found by validating real output: GPT-5 had 13
    bash turns land on the wrong day this way.
    """
    recs = {r["day"]: r for r in _run("2026-08-11", "2026-08-12")
            if r["agent"] == "Agent A"}
    d11 = recs["2026-08-11"]["facts"]["turns_raw"]["value"]
    d12 = recs["2026-08-12"]["facts"]["turns_raw"]["value"]
    assert d11 == 6, f"5 normal + 1 pre-midnight, got {d11}"
    assert d12 == 8, f"5 normal + 3 post-midnight, got {d12}"


def test_span_reflects_when_the_agent_was_present():
    recs = {r["day"]: r for r in _run("2026-08-12", "2026-08-12")
            if r["agent"] == "Agent A"}
    span = recs["2026-08-12"]["facts"]["span"]["value"]
    assert span.startswith("00:00"), \
        f"post-midnight turns belong to this day; got {span}"


def test_no_cross_agent_leakage():
    recs = _run("2026-08-11", "2026-08-11")
    b = [r for r in recs if r["agent"] == "Agent B"][0]
    assert b["facts"]["turns_raw"]["value"] == 1
    assert b["facts"]["chat_sent"]["value"] == 0, "chat belongs to Agent A"


def test_goal_resolution_prefers_individual_then_village():
    recs = _run("2026-08-11", "2026-08-11")
    a = [r for r in recs if r["agent"] == "Agent A"][0]
    b = [r for r in recs if r["agent"] == "Agent B"][0]
    assert a["facts"]["assigned"]["value"] == "Maximize widgets"
    assert b["facts"]["assigned"]["value"] == "Village goal", \
        "no individual goal -> fall back to the village goal"


def test_date_token_prefilter_matches_unfiltered():
    """The byte-prefilter is an optimisation; it must not change results."""
    fast = _run("2026-08-11", "2026-08-11")
    orig = load._day_tokens
    try:
        load._day_tokens = lambda s, e: None  # disable prefiltering
        slow = _run("2026-08-11", "2026-08-11")
    finally:
        load._day_tokens = orig
    assert [(r["agent"], r["day"], r["facts"]["turns_raw"]["value"]) for r in fast] \
        == [(r["agent"], r["day"], r["facts"]["turns_raw"]["value"]) for r in slow]


def test_empty_command_is_not_a_bash_turn():
    """command="" with no action is not work. `is not None` counted it as bash,
    over-counting GPT-5 by 5 turns/day on real data."""
    from drift.features import Block, activity_features
    turns = [{"ts": "2026-08-11 10:00:00", "command": "echo hi", "action": None},
             {"ts": "2026-08-11 10:01:00", "command": "", "action": None},
             {"ts": "2026-08-11 10:02:00", "command": None, "action": "left_click"}]
    b = Block("A", "2026-08-11")
    activity_features(b, turns, None)
    assert b.facts["turns_raw"].value == 3
    assert b.facts["turns_kept"].value == 2, "the empty command is not a turn of work"
    assert b.facts["action_mix"].value["bash"][0] == 1


def test_span_uses_all_turns_not_just_kept():
    """Presence must not depend on whether the first event was a screenshot."""
    from drift.features import Block, activity_features
    turns = [{"ts": "2026-08-11 00:00:00", "command": None, "action": "screenshot"},
             {"ts": "2026-08-11 12:00:00", "command": "echo hi", "action": None},
             {"ts": "2026-08-11 23:59:00", "command": None, "action": "mouse_move"}]
    b = Block("A", "2026-08-11")
    activity_features(b, turns, None)
    assert b.facts["span"].value == "00:00–23:59", b.facts["span"].value


def test_baseline_compares_like_for_like():
    """Numerator and denominator must both be KEPT turns.

    Mixing them (kept / median-of-raw) understated every agent's activity:
    Haiku reported 0.93 where like-for-like was 1.42, and two agents flipped
    from "below normal" to "above normal".
    """
    from drift.features import Block, activity_features, kept_turns
    noisy = ([{"ts": f"2026-08-11 10:{i:02d}:00", "command": "echo x", "action": None}
              for i in range(10)]
             + [{"ts": f"2026-08-11 11:{i:02d}:00", "command": None, "action": "screenshot"}
                for i in range(90)])
    assert len(kept_turns(noisy)) == 10, "screenshots are not behavioural turns"
    b = Block("A", "2026-08-11")
    activity_features(b, noisy, {"turns": 10})   # baseline already in KEPT units
    assert b.facts["turns_vs_own_median"].value == 1.0, \
        "10 kept today vs a 10-kept baseline is a normal day"
    assert b.facts["turns_raw"].value == 100


def test_goal_resolved_by_timestamp_not_date():
    """Two goals can match the same DATE.

    On 2026-06-23 the village goal switched from "Help Gemini 2.5 Pro!" to
    "Beat the hardest game you can!" at 14:38. Matching on the date alone
    returns both, and taking whichever the scan reaches first returns the
    OUTGOING goal — which is how the eval renderer came to label that day
    "Help Gemini 2.5 Pro!" when the taxonomy has it as the games goal.
    """
    from drift.build import _assigned_goals
    vg = [{"goal": "Help Gemini 2.5 Pro!", "start_time": "2026-06-22 14:20:00",
           "end_time": "2026-06-23 14:38:00"},
          {"goal": "Beat the hardest game you can!",
           "start_time": "2026-06-23 14:38:00", "end_time": "2026-06-29 09:22:00"}]
    got = _assigned_goals(AGENT, "2026-06-23 16:00:00", "2026-06-23 23:59:00", [], vg)
    assert [g["text"] for g in got] == ["Beat the hardest game you can!"]
    # the previous working day is the OTHER one, not "whichever came first"
    got = _assigned_goals(AGENT, "2026-06-22 16:00:00", "2026-06-22 23:59:00", [], vg)
    assert [g["text"] for g in got] == ["Help Gemini 2.5 Pro!"]


def test_straddling_day_returns_both_in_order():
    """4 of 4,103 agent-days have turns on both sides of a change (all of them
    2025-06-19). Naming only the incoming goal makes the morning's compliant
    work read as off-goal."""
    from drift.build import _assigned_goals
    vg = [{"goal": "Write a story", "start_time": "2025-05-15 18:00:00",
           "end_time": "2025-06-19 12:00:00"},
          {"goal": "Holiday: do whatever you like!",
           "start_time": "2025-06-19 12:00:00", "end_time": "2025-06-26 12:00:00"}]
    got = _assigned_goals(AGENT, "2025-06-19 01:06:00", "2025-06-19 19:13:00", [], vg)
    assert [g["text"] for g in got] == ["Write a story", "Holiday: do whatever you like!"]


def test_individual_goal_beats_village_goal():
    from drift.build import _assigned_goals
    ag = [{"agent_id": AGENT, "name": "Maximize widgets",
           "start_time": "2026-08-01 00:00:00", "end_time": None}]
    vg = [{"goal": "Village goal", "start_time": "2026-01-01 00:00:00",
           "end_time": None}]
    got = _assigned_goals(AGENT, "2026-08-10 16:00:00", "2026-08-10 23:00:00", ag, vg)
    assert [g["text"] for g in got] == ["Maximize widgets"]


def test_open_goal_is_flagged_not_recorded_as_no_drift():
    """You cannot drift from "do whatever you'd like". 262 agent-days (6%)."""
    from drift.features import Block, goal_features
    b = Block("A", "2026-02-16")
    goal_features(b, [{"text": "Pick your own goal (agents bid 3.7 farewell)"}], None)
    assert b.facts["goal_is_open"].value is True
    b2 = Block("A", "2026-08-10")
    goal_features(b2, [{"text": "Maximize widgets shipped"}], None)
    assert b2.facts["goal_is_open"].value is False


def test_goal_change_counters_are_recorded():
    from drift.features import Block, goal_features
    b = Block("A", "2026-06-23")
    goal_features(b, [{"text": "Beat the hardest game you can!"}], None,
                  since_change=0, changes_in_baseline=4)
    assert b.facts["days_since_goal_change"].value == 0
    assert b.facts["goal_changes_in_baseline"].value == 4
    # pure table lookups — no regex or threshold, so not heuristic
    assert not b.facts["days_since_goal_change"].heuristic
    assert not b.facts["goal_is_open"].heuristic


def test_history_strip_marks_where_the_assignment_changed():
    """76% of 14-day windows cross a goal change. Unmarked, the judge reads
    "yesterday a park clean-up, today chess" as a swerve, not an instruction."""
    from drift.features import history_strip
    prior = [("2026-06-16", "clean the park"), ("2026-06-17", "more park"),
             ("2026-06-23", "play chess")]
    goals = {"2026-06-16": "Adopt a park", "2026-06-17": "Adopt a park",
             "2026-06-23": "Beat the hardest game you can!"}
    out = history_strip(prior, goals.get)
    joined = "\n".join(out)
    assert "assigned goal is: Adopt a park" in joined
    assert "assigned goal changed to: Beat the hardest game you can!" in joined
    assert joined.index("changed to") < joined.index("2026-06-23  play chess")
    # unchanged behaviour when no resolver is supplied
    assert history_strip(prior) == [f"{d}  {g}" for d, g in prior]
