"""Generate a SYNTHETIC example record + rendered block.

Real output is not committed: the source dataset is gated ("use for research
and analysis… do not attempt to re-identify"), and a record's `context`
section carries verbatim agent memory, session goals and chat. This fixture
is entirely fabricated but goes through the real Block/render path, so it is
guaranteed to match the true shape.

    python3 samples/make_sample.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drift.build import block_to_record  # noqa: E402
from drift.features import Block, Null  # noqa: E402
from drift.render import render  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def build_sample() -> Block:
    b = Block(agent="Example Agent 1.0", day="2026-08-28")

    # GOAL — ratio, never a boolean
    b.put("assigned", "Maximize widgets shipped from your own storefront")
    b.put("assigned_goal_words_present", 0.71)
    b.put("assigned_goal_words_missing", ["storefront", "shipped"])

    # MEMORY — named items only; clause diffing was measured and cut
    b.put("snapshots_today", 34)
    b.put(
        "watchlist_persistence",
        {"carried": 7, "new_today": ["P112", "NO_PROMO"],
         "absent_today": ["P044"], "compared_against": "2026-08-27"},
        heuristic=True,
        note="named items only; general clause diffing is unreliable (see spec)",
    )
    b.put("watchlist_provenance", {"NO_PROMO": "2026-08-19", "P112": "2026-08-28"},
          heuristic=True, note="date each named item first appeared in this agent's memory")

    # ASSIGNED-METRIC — a real instrument, so a slope is meaningful
    b.put("metric_key", "example-agent-widgets-shipped")
    b.put("metric_source", "site-scrape")
    b.put("metric_datapoints_all_time", 54)
    b.put("metric_last_value", 31.0)
    b.put("metric_slope_7d", 0.0, note="OLS over 7 active days")
    b.put("agent_actions_touching_this_source", 0, heuristic=True,
          note="bash commands mentioning ['gitlab.io']")

    # ACTIVITY
    b.put("turns_kept", 512, note="behavioural turns; cursor/screenshot noise dropped")
    b.put("turns_raw", 601)
    b.put("turns_vs_own_median", 1.24)
    b.put("action_mix", {"bash": [318, 0.62], "gui": [171, 0.33], "pause_wait": [23, 0.04]})
    b.put("span", "16:02–23:54")
    b.put("gaps_over_30min", 1)

    # ARTIFACTS — new->0 while turns stay high is how one substitution began
    b.put("distinct_hosts_touched", 6, heuristic=True,
          note="URL regex over bash; misses GUI-only and non-URL targets")
    b.put("hosts_new_today", [], heuristic=True)
    b.put("hosts_seen_earlier", 6, heuristic=True)
    b.put("most_touched", [("example-site-aaa111.gitlab.io", 96), ("gitlab.com", 71)],
          heuristic=True)

    # REPETITION — deliberately non-judgemental
    b.put("largest_bash_group",
          {"n": 44, "of": 318, "share": 0.14, "shape": "glab api projects/NNNN …"},
          heuristic=True, note="normalised, token-set Jaccard >= 0.7")
    b.put("command_topic_concentration",
          {"token": "widget", "in_commands": 208, "of": 318, "share": 0.65,
           "next": [("inject", 140), ("sampler", 88)]},
          heuristic=True,
          note="most frequent token across bash commands; topic focus, not repetition")
    b.put("session_goal_repetition", {"n": 19, "of": 27, "share": 0.7}, heuristic=True,
          note="largest cluster, not adjacent similarity; catches verbatim templating "
               "only, not 'ship feature N'")

    # INTERACTION
    b.put("chat_sent", 4)
    b.put("agents_named_in_session_goals", [], heuristic=True, note="word-boundary matched")

    # A worked example of each null kind
    b.put("example_null_absent", Null("absent", "source is self-report; the agent is the instrument"))
    b.put("example_null_extract_failed", Null("extract_failed", "no pattern matched the goal header"))
    b.put("example_null_edge", Null("edge", "fewer than 14 prior active days"))

    b.context["prior_snapshot_outline"] = [
        "## CURRENT GOAL  |  Maximize widgets shipped (synthetic)",
        "## SHIP LOG  |  - S204 shipped 2026-08-27, verified live",
        "## STANDING RULES  |  - NO_PROMO: no outbound promotion without approval",
    ]
    b.context["prior_active_days"] = [
        "2026-08-26  Ship S201–S203; verify live; roll sampler; check counter",
        "2026-08-27  Ship S204; counter flat at 31 for third day; continue widget queue",
    ]
    return b


def main() -> None:
    record = block_to_record(build_sample())
    with open(os.path.join(HERE, "example_record.json"), "w") as fh:
        json.dump(record, fh, indent=2)
    text = render(record)
    with open(os.path.join(HERE, "example_block.txt"), "w") as fh:
        fh.write("SYNTHETIC EXAMPLE — fabricated data, real code path.\n"
                 "Regenerate with: python3 samples/make_sample.py\n\n" + text + "\n")
    print("wrote samples/example_record.json and samples/example_block.txt")


if __name__ == "__main__":
    main()
