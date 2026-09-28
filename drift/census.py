"""Per-agent channel census: catch a provider that has gone silently empty.

`split_messages` has emptied a whole provider three times. It never raises —
the reasoning channel just returns "" and every downstream count of it reads
zero, which is indistinguishable from an agent that stopped thinking. Two
auditors wrote that artefact into the eval record as a finding before anyone
noticed.

This is the cheap standing check. Run it after touching `split_messages`, and
before any cross-model claim: loss is correlated with the provider, therefore
with the model, so a comparison drawn in that state measures JSON conventions
rather than behaviour.

Reading the output: a zero reasoning channel is only a bug for a model that
HAS one. GPT-4o, GPT-4.1, Claude 3.5 Sonnet, Claude Opus 4 and Grok 4 predate
exposed reasoning, and o1/o3/o4-mini never expose it through this API, so
those zeros are correct. A near-empty NARRATION channel is likewise fine for
agents that put everything in reasoning plus tool_calls and leave `content`
as "" — Kimi K2.6 and Fine-Tuned Leader do exactly that. What is never fine
is BOTH channels empty: that is what "Opus 4.5 (Claude Code)" looked like for
11,700 turns, and it meant an unhandled envelope.

Usage:  VILLAGE_DATA=~/Documents/ai-village python3 -m drift.census
"""
from __future__ import annotations

import collections

from . import load

# Models with no exposed reasoning channel through this API: a zero here is
# the truth about the provider, not a bug in the loader.
NO_REASONING = {
    "GPT-4o", "GPT-4.1", "Claude 3.5 Sonnet", "Claude Opus 4", "Grok 4",
    "o1", "o3", "o4-mini",
}
MIN_TURNS = 50   # below this the percentages are noise

# An empty channel is a weak signal — o3 and o4-mini run almost entirely on
# tool calls with `content: null`, so they read as empty and are perfectly
# well handled. The strong signal is a KEY WE HAVE NEVER SEEN, because that
# is what both real bugs actually were: `reasoning_content` and `_sdkFormat`
# sat in plain sight in the JSON while the parser walked past them.
HANDLED = {                      # keys split_messages reads prose out of
    "content", "reasoning", "reasoning_content", "candidates",
    "_sdkFormat", "textMessage", "thinkingMessage",
}
INERT = {                        # keys that carry no agent prose
    "role", "tool_calls", "refusal", "annotations", "function_call", "name",
    "stop_reason", "stop_sequence", "context_management", "finish_reason",
    "type", "id", "model", "usage", "index", "audio", "uuid", "message",
    "parent_tool_use_id", "session_id", "logprobs",
    # Transport/billing metadata, verified to carry no prose.
    "modelVersion", "usageMetadata", "responseId", "sdkHttpResponse",
    "stop_details",
    # `reasoning_details` is the OpenRouter block form of the same reasoning
    # already in `reasoning_content` — checked across all 50,290 turns that
    # carry it (DeepSeek-V4-Pro, GLM-5.2, DeepSeek-V3.2, GLM-5.3 Flash) and
    # ZERO hold text split_messages does not already return. It is duplicate,
    # not additional, so GLM-5.2's 30% reasoning rate is real behaviour.
    # Re-check if a new provider starts emitting it.
    "reasoning_details",
}


def census():
    """Return (per-agent counters, {unknown key: {agent: turns}})."""
    agents = load.load_agents()
    sessions, _ = load.load_sessions(None, None)
    stats: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    unknown: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for row in load._rows("computer_use_turns.jsonl.gz"):
        meta = sessions.get(row.get("session_id"))
        if not meta:
            continue
        agent = agents.get(meta[0]) or "?"
        s = stats[agent]
        am = row.get("agent_messages")
        reasoning, narration = load.split_messages(am)
        s[0] += 1
        if reasoning.strip():
            s[1] += 1
            s[2] += len(reasoning)
        if narration.strip():
            s[3] += 1
            s[4] += len(narration)
        if isinstance(am, dict):
            for key in am.keys() - HANDLED - INERT:
                unknown[key][agent] += 1
    return stats, unknown


def main() -> int:
    stats, unknown = census()
    print(f"{'agent':28s} {'turns':>7} {'%reas':>6} {'r-ch/t':>7} "
          f"{'%narr':>6} {'n-ch/t':>7}  note")
    suspect = []
    for agent in sorted(stats, key=lambda a: -stats[a][0]):
        turns, r_turns, r_chars, n_turns, n_chars = stats[agent]
        if turns < MIN_TURNS:
            continue
        pr, pn = 100 * r_turns / turns, 100 * n_turns / turns
        note = ""
        if pr < 5 and agent not in NO_REASONING:
            note = "⚠ no reasoning, but this model has one"
            suspect.append(agent)
        print(f"{agent:28s} {turns:7d} {pr:5.1f}% {r_chars / turns:7.0f} "
              f"{pn:5.1f}% {n_chars / turns:7.0f}  {note}")

    rc = 0
    if unknown:
        print("\n⚠⚠ UNRECOGNISED agent_messages KEYS — a shape nobody parses:")
        for key, per_agent in sorted(unknown.items(),
                                     key=lambda kv: -sum(kv[1].values())):
            top = ", ".join(f"{a} ({n:,})" for a, n in per_agent.most_common(3))
            print(f"    {key!r}: {sum(per_agent.values()):,} turns — {top}")
        print("    Inspect one, and either parse it or add it to INERT.")
        rc = 1
    if suspect:
        print(f"\n{len(suspect)} agent(s) have an unexplained empty reasoning "
              f"channel: {', '.join(suspect)}")
        rc = 1
    if not rc:
        print("\nEvery channel is either populated or explained.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
