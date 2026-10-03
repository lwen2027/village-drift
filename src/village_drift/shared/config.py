"""Constants for Stage 1 feature extraction.

Every value here is a decision recorded in ai-village-stage1-feature-spec.md.
Changing one invalidates comparisons across sweep runs, so bump PROMPT_VERSION.
"""

from __future__ import annotations

import os

# --- data location -----------------------------------------------------------
# The gated HuggingFace dump. Override with VILLAGE_DATA=/path/to/dir
DATA_DIR = os.environ.get("VILLAGE_DATA", os.path.expanduser("~/Documents/ai-village"))

# Bump when anything changes what a block CONTAINS, and src/village_drift/stage1/run.py will
# refuse a cached block that does not match — the bump alone is useless,
# which is how v1 survived a day of changes.
#
# v2, 2026-09-29:
#   + goal_period_messages   operator messages sent during the goal, after
#                            the announcement day and before today. 37 of 40
#                            rows had traffic in that window; nothing saw it.
#   + no per-goal cap on session goals, whole-day budget instead
#   - assigned_goal_words_present / _missing   delivery_events replaces proxy
#   - agent_actions_touching_this_source       ditto
#   - command_topic_concentration              computed, never rendered
#   - the [heuristic] marker (heuristic=True stays in the record)
FEATURE_VERSION = "stage1-v2"

# --- truncation --------------------------------------------------------------
# Bash is ~88% of the per-agent block; median command 346 chars but p99 16,279
# (heredocs writing whole Python files). Head+tail keeps intent and redirect,
# drops the program body. Curve flattens below 500 — see spec.
# ⚠ Drift-detector only. Never apply to the safety monitor, whose top category
#   is surreptitious behaviour "especially via bash".
BASH_CAP = 500
BASH_HEAD = 350
BASH_TAIL = 150

# Matches the existing monitor. Outputs are results, not intent.
OUTPUT_CAP = 1000

# --- windows -----------------------------------------------------------------
# Baselines use ACTIVE days, never calendar days (Sol: 38 active days / 7 weeks).
BASELINE_DAYS = 14
HISTORY_STRIP_DAYS = 14
HISTORY_GOAL_CHARS = 200
# TODAY's session goals are a different job from the history strip and had no
# business sharing its constant. They decide the verdict -- tracing every
# decisive quote back to its section, session_goals_today supplies it in 78%
# of arm A's verdicts and every other Context section supplies it in 2% or
# less -- whereas the history strip is cross-day and rule 4 forbids it
# carrying a verdict at all.
#
# These are not one-line goals. They are the agent's working-memory dump:
# mean 1,270 chars, p90 2,691, max 25,453 over 2,160 goals in 101 agent-days.
# At 200 the block kept 13% of that text and cut 61% of goals mid-sentence.
#
# NO per-goal cap. An earlier version truncated each goal (200, then 400) on
# the reasoning that intent is front-loaded -- ten long goals sampled across
# ten agents all state their target inside the first ~130 chars. That part is
# true and it is the wrong conclusion: past the intent line these goals carry
# "DONE this session (don't redo): (1)... (2)...", which is the agent's own
# record of what it spent the day ON. For "did it work toward the assigned
# target", that record is the evidence, not padding.
#
# Instead, a whole-DAY budget, and goals are dropped entire rather than cut
# mid-sentence. Per agent-day the uncapped text runs median 7,008 chars, p75
# 15,116, p95 68,980, max 387,397 -- so a cap tuned for the tail starves the
# median. 60,000 leaves 93 of 101 agent-days completely untouched and trims 8,
# four of them the same agent (Claude Sonnet 5, 310-387K chars/day) whose
# goals would bury every other section anyway.
#
# Dropped from the MIDDLE, keeping the day's opening intent and where it ended
# up, with an explicit marker -- a judge told text is missing can weigh the
# absence; one silently cut cannot, and "absent from my input" is exactly the
# inference the rubric warns against.
TODAY_GOAL_DAY_BUDGET = 60_000
# Operator messages are few and decisive, so they are carried verbatim rather
# than summarised. 600 holds the longest real correction seen in the sample.
OPERATOR_MSG_CHARS = 600
# Operator messages sent DURING a goal, after its announcement day and before
# the audited day. Nothing saw this window: goal_announcement covers the start
# day and operator_messages_today covers today, and 37 of 40 arena rows had
# operator traffic in between -- 18,758 messages the block could not reach.
# One of them decided an eval row: a 2026-05-19 amendment capping output at
# one video a day and permitting zero, three days before the audited day.
#
# Filtered to what is addressed to THIS agent, minus auto-nudges and session
# bookends. After that: median 2 messages and 752 chars, p90 14 and 6,063,
# and 26 of 88 rows have none at all. Only the tail needs the cap; 6,000 is
# the p90, so nine rows in ten are carried whole.
GOAL_PERIOD_MSG_CHARS = 6_000
METRIC_SLOPE_DAYS = 7
# Calendar days loaded BEFORE the requested start, so day 1 of a partial run
# still has a baseline and a history strip. Without it the first ~14 days of
# any windowed run silently degrade: empty context, null baselines. Generous
# because the windows count ACTIVE days and agents skip weekends/go dormant.
LOOKBACK_DAYS = 45

# --- standing operator instruction -------------------------------------------
# Both are carried forward from BEFORE the audited day, so both are capped: the
# goal announcement is usually 1 message but a goal-start day can carry several
# addenda, and one agent accumulated 213 outreach-approval decisions of which
# 22 were denials. Only the most recent constraints bind in practice.
GOAL_ANNOUNCEMENTS = 6
OUTREACH_CONSTRAINTS = 5

# --- repetition --------------------------------------------------------------
# Largest near-identical cluster, NOT adjacent- or all-pairs similarity:
# adjacent ranks Haiku (cumulative restating) above Opus 4.7 (the real template
# day). Mirrors the bash-group method so both rows mean the same thing.
REPETITION_THRESHOLD = 0.70
MIN_SESSIONS_FOR_REPETITION = 5  # below this -> null(edge)

# --- action types ------------------------------------------------------------
# The production monitor drops these. We re-include pause/wait: pause_fraction
# is uncomputable without them.
PAUSE_ACTIONS = {"pause", "wait"}
DROP_ACTIONS = {
    "send_message_back_to_chat",  # already in the chat transcript
    "search_history",
    "move_to_room",
    "screenshot",
    "mouse_move",
    "cursor_position",
    "get_pixel_coords_of_element",
}

# --- metric caveats ----------------------------------------------------------
# A flat self-reported metric is NO SIGNAL, not evidence. Treating it as
# evidence reproduces the exact failure mechanical-over-judge exists to avoid.
UNINSTRUMENTED_SOURCES = {"self-report", "manual"}
# INSTRUMENT_CHANGE_DATE = "2026-08-14" stood here, for the fact that several
# metrics switch self-report -> real instrument around then and a slope
# straddling that measures the instrument rather than the agent. Nothing read
# it, and nothing should: features.py enforces the rule EMPIRICALLY instead,
# nulling the slope whenever last_source differs anywhere inside the window.
# That is strictly better than a date -- it is per-metric, and it catches a
# switch on any date rather than the one someone wrote down. It fires on
# DeepSeek-V3.2 2026-08-17 in the current sample.
# metric_datapoints does not exist before this.
METRICS_START = "2026-07-06"
# Individual agent goals begin here; earlier days fall back to village_goals.
INDIVIDUAL_GOALS_START = "2026-07-06"

# METRIC_SOURCE_HOSTS lived here: a hand-mapped host list per metric source,
# and the only consumer was agent_actions_touching_this_source. That field
# counted commands MENTIONING an endpoint, conflating reading a metric with
# acting on it, and is gone -- so the map went with it. This was described as
# "the one genuinely hand-mapped thing in Stage 1"; there is now none.

# --- goals -------------------------------------------------------------------
# Goals whose text does not constrain behaviour, so "drift" is UNDEFINED rather
# than absent: 262 agent-days (6%) across 9 village goals. Recording these as
# `is_drift: false` would deflate every rate and would punish a detector for
# flagging a day where flagging is not wrong.
OPEN_GOAL_MARKERS = (
    "holiday", "pick your own", "choose your own", "whatever you",
    "pursue whatever", "do as you please",
)

# --- watchlist ---------------------------------------------------------------
# Named items survive 36-97% across a day; arbitrary lines survive 1-68%.
# General clause diffing was measured and cut — see spec.
# Structured identifiers only. A bare ALL-CAPS pattern matches ordinary
# markdown emphasis (ABSOLUTE, MONDAY, CRITICAL…) and drowns the signal — the
# first run returned 26 "carried" tokens that were all just shouty prose.
WATCHLIST_PATTERNS = [
    r"\bP\d{1,4}\b",                    # P67, P375, P228
    r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b",  # NO_TRADE, LEVEL_5_VICTORY
    r"\b[A-Z]{2,6}\d{1,5}\b",            # CH4570, KO83, S386
]

# Shell/HTTP noise that dominates any bash vocabulary count without saying
# anything about what the day was about.
SHELL_NOISE = {
    "null", "true", "false", "http", "https", "then", "else", "done", "echo",
    "print", "json", "data", "text", "name", "list", "head", "tail", "line",
    "sed", "awk", "cat", "grep", "python", "bash", "home", "tmp", "usr", "var",
    "check", "status", "count", "total", "output", "result", "info", "item",
}

STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "to", "with", "you", "your", "own",
}


def safe_agent(agent):
    """Agent name -> filename token. THE canonical definition; import it.

    It had been written out six times: src/village_drift/stage1/run.py, src/village_drift/stage2/run.py,
    eval/episodes.py, eval/walk_probe.py, evaluation/goldens/show_day.py and
    evaluation/goldens/render_digest.py. It lives in config because config is the one
    module every layer already imports. Putting it in a stage-specific package
    would make shared and evaluation code depend upward.

    THE COPIES WERE NOT ALL THE SAME. eval/walk_probe.py's read

        "".join(c if c.isalnum() or c in "-." else "_" for c in a)

    under a docstring claiming it "matches src/village_drift/stage1/run.py:_safe". It does not:
    it also rewrites parentheses, apostrophes, colons and plus signs. The 42
    village agents happen to use only letters, digits, spaces, dots and
    hyphens, so the two agreed on every name that has ever been passed -- and
    would have diverged the day one did not.

    That failure is silent by construction. Every block, digest and raw file
    is keyed on this, the three stores use DIFFERENT templates

        block    arena_blocks/<agent>__<day>.txt
        digest   digests_windows/<day>__<agent>.txt      <- reversed
        raw      raw/<day>/<agent>.json

    and a mismatch raises nothing: the path simply does not exist, and every
    consumer reports the day as having no data.
    """
    return agent.replace("/", "_").replace(" ", "_")


# ---------------------------------------------------------------- artifacts --
# WHERE EVERY PER-AGENT-DAY ARTIFACT LIVES, AND HOW IT IS NAMED.
#
# Each store is keyed by the same thing -- one agent, one day -- and until
# 2026-10-01 each spelled that key differently, with nine hand-built paths
# across five files:
#
#     block    arena_blocks/<agent>__<day>.txt
#     digest   digests_windows/<day>__<agent>.txt     <- reversed
#     raw      raw/<day>/<agent>.json                 <- nested
#
# THE CONVENTIONS ARE NOT THE BUG. The bug is that callers knew them. Build a
# path with the wrong one and nothing raises: the file simply is not there,
# the lookup returns None, and that is byte-for-byte the same answer as the
# truthful "this agent did not work that day". Every silent-absence bug this
# project has hit has that shape.
#
# So the layouts stay as they are on disk -- renaming ~1,500 files buys
# tidiness and risks the data -- but exactly one function knows them, and no
# caller concatenates a path again.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# store -> (roots searched in order, stem template, extension)
STORES = {
    "block":    (["artifacts/current/stage1/blocks"], "{agent}__{day}", ".txt"),
    "blockrec": (["artifacts/current/stage1/blocks"], "{agent}__{day}", ".json"),
    "digest":   (["evaluation/evidence/digests_windows",
                  "evaluation/evidence/digests"], "{day}__{agent}",
                 ".txt"),
    "raw":      (["evaluation/evidence/raw"], "{day}/{agent}", ".json"),
}


def artifact_path(store, agent, day, root=None):
    """Where this artifact WOULD live. Does not check existence.

    `root` picks among a store's roots when writing; the default is the
    first, which is the one a writer should use.
    """
    roots, stem, ext = STORES[store]
    base = root or roots[0]
    rel = stem.format(agent=safe_agent(agent), day=day) + ext
    return os.path.join(_ROOT, base, rel)


def find_artifact(store, agent, day):
    """The existing path for this artifact, or None. Searches every root.

    Returning None is a real answer here -- plenty of agent-days genuinely
    have no digest -- which is exactly why the path must not also be able
    to be wrong. That is this function's job.
    """
    roots, _, _ = STORES[store]
    for r in roots:
        p = artifact_path(store, agent, day, root=r)
        if os.path.exists(p):
            return p
    return None
