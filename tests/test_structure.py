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
    _write(dirpath, "village_goals.jsonl.gz", [
        {"name": "Village goal", "start_time": "2026-01-01 00:00:00",
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
