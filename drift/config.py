"""Constants for Stage 1 feature extraction.

Every value here is a decision recorded in ai-village-stage1-feature-spec.md.
Changing one invalidates comparisons across sweep runs, so bump PROMPT_VERSION.
"""

from __future__ import annotations

import os

# --- data location -----------------------------------------------------------
# The gated HuggingFace dump. Override with VILLAGE_DATA=/path/to/dir
DATA_DIR = os.environ.get("VILLAGE_DATA", os.path.expanduser("~/Documents/ai-village"))

# Bump when any constant below changes, so records stay comparable across runs.
FEATURE_VERSION = "stage1-v1"

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
METRIC_SLOPE_DAYS = 7

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
# Several metrics switch self-report -> real instrument on this date; a slope
# straddling it measures the instrument, not the agent.
INSTRUMENT_CHANGE_DATE = "2026-08-14"
# metric_datapoints does not exist before this.
METRICS_START = "2026-07-06"
# Individual agent goals begin here; earlier days fall back to village_goals.
INDIVIDUAL_GOALS_START = "2026-07-06"

# --- watchlist ---------------------------------------------------------------
# Named items survive 36-97% across a day; arbitrary lines survive 1-68%.
# General clause diffing was measured and cut — see spec.
WATCHLIST_PATTERNS = [
    r"\bP\d{1,4}\b",              # P67, P375, P228 ...
    r"\b[A-Z][A-Z0-9_]{4,}\b",    # NO_TRADE, HOLD, LEVEL_5_VICTORY ...
]

STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "to", "with", "you", "your", "own",
}
