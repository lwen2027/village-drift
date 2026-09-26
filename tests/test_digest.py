"""Tests for the eval digest — the surface ground truth is read from.

Two properties, both of which have already broken once:

  * a session belongs to every day it produced turns on, not the day it opened
  * the digest never leaks detector output, or the label anchors on the thing
    being tested
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "eval"))

import render_digest as R  # noqa: E402


def _day(sessions, turns=(), chat=(), memory=()):
    return {"goal": {"text": "Maximize widgets", "start": "2026-08-01", "end": None},
            "sessions": list(sessions), "turns": list(turns),
            "chat": list(chat), "memory": list(memory)}


def test_carried_over_session_is_labelled_as_such():
    """The 31-turn day that looked empty.

    GPT-5.6 Terra opened a session at 2026-08-24 23:54 and ran all of the next
    day's turns inside it. Keying sessions by their own date dropped the goal
    ("Preserve Terra; assess concrete valid triggers only") off the 25th and
    left a 31-turn day reading as unexplained inactivity.
    """
    d = _day([{"opened": "2026-08-24 23:54:44", "carried_over": True,
               "session_goal": "Preserve Terra; assess concrete valid triggers only"}])
    txt = R.digest("GPT-5.6 Terra", "2026-08-25", d)
    assert "Preserve Terra" in txt, "a carried-over session goal must still appear"
    assert "ran into today" in txt, "and must be marked as opened on a prior day"
    assert "2026-08-24" in txt


def test_same_day_session_shows_a_plain_time():
    d = _day([{"opened": "2026-08-25 09:12:00", "carried_over": False,
               "session_goal": "ship the thing"}])
    txt = R.digest("A", "2026-08-25", d)
    assert "  09:12  ship the thing" in txt
    assert "ran into today" not in txt


def test_operator_messages_are_never_sampled_away():
    """An operator instruction is the most common external cause of a day
    changing direction, so it must survive any truncation."""
    chat = [{"ts": f"2026-08-25 10:{i:02d}:00", "speaker": f"Peer{i}",
             "own": False, "human": False, "content": "unrelated chatter about A"}
            for i in range(R.CHAT_PEERS * 3)]
    chat.append({"ts": "2026-08-25 11:00:00", "speaker": "Adam", "own": False,
                 "human": True, "content": "STOP publishing and switch to QA"})
    txt = R.digest("A", "2026-08-25", _day([], chat=chat))
    assert "STOP publishing and switch to QA" in txt


def test_peer_name_match_is_word_boundary():
    """`@GPT-5` matching inside `@GPT-5.6` already produced one wrong number."""
    assert R._names_agent("hey @GPT-5 can you look", "GPT-5")
    assert not R._names_agent("hey @GPT-5.6 can you look", "GPT-5")
    assert R._names_agent("hey @GPT-5.6 can you look", "GPT-5.6")


def test_suppressed_chat_is_disclosed_not_silent():
    chat = [{"ts": "2026-08-25 10:00:00", "speaker": "Peer", "own": False,
             "human": False, "content": "nothing to do with anyone"}] * 5
    txt = R.digest("A", "2026-08-25", _day([], chat=chat))
    assert "5 other messages in the shared room not shown" in txt


def test_sampling_is_disclosed_and_systematic():
    turns = [{"ts": f"2026-08-25 10:{i % 60:02d}:00", "kind": "bash",
              "command": f"echo {i}", "output": "", "error": "",
              "reasoning": None} for i in range(R.BASH_TURNS * 3)]
    txt = R.digest("A", "2026-08-25", _day([], turns=turns))
    assert f"showing {R.BASH_TURNS}" in txt
    assert "sampled mechanically, NOT by interest" in txt
    assert "echo 0" in txt and "echo 3" in txt, "every 3rd, starting at the first"


def test_digest_contains_no_detector_output():
    """The labeller must not see anything a method produced."""
    turns = [{"ts": "2026-08-25 10:00:00", "kind": "bash", "command": "echo hi",
              "output": "hi", "error": "", "reasoning": "thinking about it"}]
    txt = R.digest("A", "2026-08-25", _day(
        [{"opened": "2026-08-25 09:00:00", "carried_over": False,
          "session_goal": "g"}], turns=turns)).lower()
    for banned in ("signals_fired", "severity", "monitor", "off-goal",
                   "predicted", "confidence", "[heuristic]", "turns_vs_own_median",
                   "score"):
        assert banned not in txt, f"digest leaked detector output: {banned}"


def test_absent_reasoning_is_reported_not_implied_silent():
    turns = [{"ts": "2026-08-25 10:00:00", "kind": "bash", "command": "x",
              "output": "", "error": "", "reasoning": None}]
    txt = R.digest("A", "2026-08-25", _day([], turns=turns))
    assert "recorded for 0 of 1 turns" in txt
    assert "absence is not silence" in txt
