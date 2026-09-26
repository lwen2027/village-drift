"""Room-scoped goals.

`village_goals` records ONE goal per period. Between 2026-03-16 and 2026-07-06
the village ran as a controlled two-arm experiment: #best and #rest were given
goals independently, and the rooms were access-isolated. From the 2026-04-02
announcement to #rest:

    The agents in the #best room (which you do not have access to) are
    separately working on a charity project - please do not interact with them
    or their fundraising efforts in any way, so we can get a clear
    understanding of how they do alone.

`village_goals` tracks **#best**. So for the #rest cohort the recorded goal is
simply the wrong one — 667 agent-days of it. The taxonomy hit this once by hand
(case 1 is annotated "Room-override data problem") and worked around it; this
module is the general fix.

Nothing in the dump encodes room goals, so DIVERGENT below is hand-curated from
the operator's own announcements in chat_messages. Only weeks where the two
rooms were given DIFFERENT goals are listed: most weeks the operator posted
identical text to both, and those fall through to village_goals correctly.
"""

from __future__ import annotations

import collections

# The split window. Outside it there is one room and village_goals is correct.
SPLIT_START = "2026-03-16"
SPLIT_END = "2026-07-06"

# Weeks where #rest was given a different goal from #best.
#   (start, end, goal, is_open)
# is_open marks goals that do not constrain behaviour, so drift is UNDEFINED.
# Sources are the operator messages at these timestamps, in #rest.
REST_GOALS = [
    ("2026-04-02", "2026-04-27",
     "Feel free to do whatever you'd like - e.g., playing videogames, doing "
     "your own projects, etc.", True),
    ("2026-05-26", "2026-06-08",
     "Pick your own goal!", True),
    ("2026-06-08", "2026-06-15",
     "Surprise each other!", False),
    ("2026-06-15", "2026-06-22",
     "Beat as many games as you can!", False),
    ("2026-06-29", "2026-07-06",
     "Pick your own goal! Feel free to pursue whatever goal you feel like. The "
     "only thing I'd like to ask is that you don't go to sleep, or wait, or do "
     "silent monitoring.", True),
]

# #best membership, as named by the operator each week. Everyone else is #rest.
# The operator re-reads the roster at every handover, which is why this is a
# list of windows rather than a single set.
BEST_ROSTER = [
    ("2026-05-18", "2026-05-25",
     {"Gemini 3.1 Pro", "GPT-5.5", "Claude Opus 4.7", "Kimi K2.6"}),
    ("2026-05-25", "2026-06-01",
     {"Gemini 3.5 Flash", "GPT-5.5", "Claude Opus 4.7", "Kimi K2.6"}),
    ("2026-06-01", "2026-06-08",
     {"Gemini 3.5 Flash", "GPT-5.5", "Claude Opus 4.8", "Kimi K2.6",
      "Fine-Tuned Leader", "[Temporary] Fine-tuned Leader"}),
    ("2026-06-08", "2026-06-22",
     {"Gemini 3.5 Flash", "GPT-5.5", "Claude Opus 4.8", "Kimi K2.6"}),
    ("2026-06-29", "2026-07-06",
     {"Gemini 3.5 Flash", "GPT-5.5", "Claude Opus 4.8", "Kimi K2.6"}),
]


def in_split(day: str) -> bool:
    return SPLIT_START <= day < SPLIT_END


def rest_goal(day: str):
    """The #rest goal in force on `day`, or None if #rest matched #best."""
    for start, end, text, is_open in REST_GOALS:
        if start <= day < end:
            return {"text": text, "scope": "room:#rest", "start": start,
                    "end": end, "is_open": is_open}
    return None


def rostered_best(agent: str, day: str):
    """True/False if the operator named a roster for this week, else None.

    None means the roster is unknown for that week, NOT that the agent was in
    #rest — the caller should fall back to observed chat rather than guess.
    """
    for start, end, roster in BEST_ROSTER:
        if start <= day < end:
            return agent in roster
    return None


def observed_rooms(chat_rows, agents: dict, rooms: dict) -> dict:
    """(agent, day) -> Counter of rooms the agent actually posted in."""
    seen: dict = collections.defaultdict(collections.Counter)
    for c in chat_rows:
        name = agents.get(c.get("agent_speaker_id"))
        if name:
            seen[(name, str(c.get("created_at"))[:10])][
                rooms.get(c.get("room_id"), "?")] += 1
    return seen


def room_of(agent: str, day: str, observed: dict, prior: dict | None = None):
    """Which room this agent was in. Returns (room, how_we_know).

    OBSERVED CHAT FIRST. The roster is what the operator announced at the
    weekly handover; it goes stale whenever membership rotates mid-window, and
    it did — Gemini 3.1 Pro was swapped for Gemini 3.5 Flash, Opus 4.7 for Opus
    4.8, and new agents (Fable 5, Sonnet 5) joined #best before appearing in any
    roster. Checked against where agents actually posted, the roster is right
    456 times and wrong 18, and every miss is of that kind. So the roster is the
    fallback for days the agent stayed silent, not the primary.
    """
    if not in_split(day):
        return "general", "single-room era"
    c = observed.get((agent, day))
    if c:
        # max(), not Counter.most_common(), so a plain dict works too
        return max(c, key=lambda k: (c[k], k)), "observed in chat"
    r = rostered_best(agent, day)
    if r is not None:
        return ("best" if r else "rest"), "operator roster"
    if prior and prior.get(agent):
        return prior[agent][0], "carried from previous day"
    # Fail loudly ONLY where the room changes the answer. The split window is
    # not continuous — on 2026-06-22 the operator pulled everyone back into
    # #general for a week ("I'd like you to all move to the #general
    # chatroom"), so mid-window days can legitimately be single-room. If no
    # divergent #rest goal exists for this day, the room is immaterial.
    if rest_goal(day) is None:
        return "general", "no divergent room goal that day"
    return None, "unresolved, and the room changes the goal — read the logs"


def goal_for(agent: str, day: str, room: str | None, village_goals_text):
    """Resolve the goal, overriding village_goals when the agent was in #rest.

    `village_goals_text` is whatever the existing day-based lookup returned —
    correct for #best and for the single-room eras, wrong for #rest during a
    divergent week.
    """
    if room == "rest":
        g = rest_goal(day)
        if g:
            return g
    return village_goals_text
