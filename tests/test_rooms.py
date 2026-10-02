"""Room-scoped goals.

village_goals records one goal per period, but from 2026-03-16 to 2026-07-06
the village ran #best and #rest as separate, access-isolated arms with
different goals. village_goals tracks #best, so every #rest agent-day in a
divergent week was being scored against a goal it was never given.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift import rooms as R  # noqa: E402


def test_split_window_bounds():
    assert not R.in_split("2026-03-15")
    assert R.in_split("2026-03-16")
    assert R.in_split("2026-07-05")
    assert not R.in_split("2026-07-06"), "individual-goal era; one room again"


def test_rest_gets_a_different_goal_from_village_goals():
    """The case that exposed this: 2026-06-18."""
    g = R.rest_goal("2026-06-18")
    assert g["text"] == "Beat as many games as you can!"
    assert g["is_open"] is False
    # village_goals says "Reduce global suffering as much as you can!" that day,
    # which is what #best was given.


def test_open_rest_goals_are_flagged():
    assert R.rest_goal("2026-07-01")["is_open"] is True     # "Pick your own goal!"
    assert R.rest_goal("2026-04-10")["is_open"] is True     # "do whatever you'd like"
    assert R.rest_goal("2026-06-10")["is_open"] is False    # "Surprise each other!"


def test_weeks_where_both_rooms_matched_fall_through():
    """Most weeks the operator posted identical text to both rooms; those must
    NOT be overridden or we would invent a divergence that never happened."""
    for day in ("2026-03-25", "2026-05-05", "2026-05-20", "2026-06-23"):
        assert R.rest_goal(day) is None, day


def test_observed_chat_beats_a_stale_roster():
    """The roster goes stale mid-window. Gemini 3.5 Flash replaced Gemini 3.1
    Pro in #best before any roster named it, and posted in #best on 2026-05-20
    while the standing roster still implied #rest."""
    observed = {("Gemini 3.5 Flash", "2026-05-20"): {"best": 9}}
    room, how = R.room_of("Gemini 3.5 Flash", "2026-05-20", observed)
    assert (room, how) == ("best", "observed in chat")
    assert R.rostered_best("Gemini 3.5 Flash", "2026-05-20") is False, \
        "the roster is wrong here; that is why observed wins"


def test_roster_covers_days_with_no_chat():
    room, how = R.room_of("Kimi K2.6", "2026-06-10", {})
    assert (room, how) == ("best", "operator roster")
    room, how = R.room_of("DeepSeek-V3.2", "2026-06-10", {})
    assert (room, how) == ("rest", "operator roster"), "everyone else is #rest"


def test_carry_forward_is_last_resort():
    prior = {"Some Agent": ("rest", "2026-04-09")}
    room, how = R.room_of("Some Agent", "2026-04-10", {}, prior)
    assert (room, how) == ("rest", "carried from previous day")
    # April 2026 predates any named roster, and #rest had its own goal then,
    # so an agent with no chat and no prior is genuinely unresolvable — and the
    # room changes the answer, so refuse to guess.
    room, how = R.room_of("Unknown Agent", "2026-04-10", {}, {})
    assert room is None and "changes the goal" in how, "never guess a room"


def test_outside_the_split_observed_chat_still_wins():
    """Outside the split window the default is #general — but only as a default.

    This used to return ("general", "single-room era") unconditionally, which
    was wrong: #focus was created 2026-08-05, a month after the split closed,
    and agents used it heavily. On 2026-08-11 one agent posted 89 of its 122
    messages there and on 2026-08-13 another posted 153 of 154, while this
    function reported "general" for both. No goal attaches to #focus, so the
    room does not change the assignment — the field was simply asserting
    something false.
    """
    # No chat that day: fall back to the default, and say so.
    room, how = R.room_of("anyone", "2025-10-21", {})
    assert room == "general" and how.startswith("single-room era")

    # Chat exists: it wins, even outside the split window.
    observed = {("anyone", "2026-08-13"): {"focus": 153, "general": 1}}
    assert R.room_of("anyone", "2026-08-13", observed) == ("focus", "observed in chat")


def test_goal_override_applies_only_to_rest():
    """Retargeted from goal_for(), which returned a shape neither caller
    could use and survived only because this test referenced it."""
    assert R.goal_override("2026-06-18", "best") is None
    assert R.goal_override("2026-06-18", None) is None
    assert R.goal_override("2026-06-18", "rest")["text"] == \
        "Beat as many games as you can!"


def test_unresolvable_room_only_fails_loudly_when_it_matters():
    """The split window is not continuous: on 2026-06-22 the operator pulled
    everyone back to #general for a week. A mid-window day with no divergent
    #rest goal is single-room, so an unresolved room is immaterial there."""
    room, how = R.room_of("Gemini 3.1 Pro", "2026-06-24", {}, {})
    assert room == "general" and "no divergent" in how
    # In a rostered week the roster resolves even an unseen agent, because the
    # operator's rule is "#best: <named four>. #rest: everyone else."
    room, how = R.room_of("Nobody", "2026-06-18", {}, {})
    assert (room, how) == ("rest", "operator roster")
    # But before any roster existed, with a divergent #rest goal in force,
    # there is nothing to fall back on — refuse to guess.
    room, how = R.room_of("Nobody", "2026-04-10", {}, {})
    assert room is None and "changes the goal" in how
