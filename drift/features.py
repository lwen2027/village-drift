"""Stage 1 feature computation.

Two conventions the rest of the pipeline depends on:

  * Every value is either a number/string, or a Null(kind, reason). NEVER emit 0
    for "no data" — absence reading as flatness is a false drift signal.
  * Fields whose value depends on a regex, threshold or segmentation choice are
    marked heuristic=True. The judge is told to verify those against the logs.

Field names are deliberately neutral (`clauses`, not `prohibitions`). A loaded
name smuggles a hypothesis into an exploratory sweep.
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import config

# --- null taxonomy -----------------------------------------------------------
# The three kinds route the judge differently:
#   absent          -> no data exists; don't hunt, don't read as zero
#   extract_failed  -> data exists, the rule missed it; go read the logs
#   edge            -> series boundary; ignore


@dataclass(frozen=True)
class Null:
    kind: str  # "absent" | "extract_failed" | "edge"
    reason: str = ""

    def __repr__(self) -> str:
        return f"null({self.kind})" + (f" — {self.reason}" if self.reason else "")


@dataclass
class Field:
    value: Any
    heuristic: bool = False
    note: str = ""


@dataclass
class Block:
    """One agent-day. `facts` are marked; `context` is raw text for the judge."""

    agent: str
    day: str
    facts: dict[str, Field] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def put(self, key, value, heuristic=False, note=""):
        self.facts[key] = Field(value, heuristic, note)


# --- helpers -----------------------------------------------------------------

_HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
_NUM = re.compile(r"\d[\d,./:%_-]*")
_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^a-z0-9 ]")
_URL_HOST = re.compile(r"https?://([\w.-]+)")


def normalise(text: str) -> str:
    """Collapse volatile detail so 'same shape' commands/goals compare equal."""
    text = _HEX.sub("H", text)
    text = _NUM.sub("N", text)
    return _WS.sub(" ", _NONWORD.sub(" ", text.lower())).strip()


def content_words(text: str) -> set[str]:
    return {
        w for w in _WS.sub(" ", _NONWORD.sub(" ", text.lower())).split()
        if len(w) > 2 and w not in config.STOPWORDS
    }


def cap_bash(command: str) -> str:
    """Head+tail: keeps the intent line and the redirect, drops the body."""
    if len(command) <= config.BASH_CAP:
        return command
    dropped = len(command) - config.BASH_HEAD - config.BASH_TAIL
    return (
        command[: config.BASH_HEAD]
        + f"\n… [{dropped} chars elided] …\n"
        + command[-config.BASH_TAIL :]
    )


def largest_cluster(items: list[str], threshold: float) -> tuple[int, str | None]:
    """Greedy: biggest set of near-identical strings. Mirrors the bash method."""
    if not items:
        return 0, None
    best_n, best_seed = 0, None
    normed = [normalise(x) for x in items]
    for i, seed in enumerate(normed):
        n = sum(
            1 for other in normed
            if difflib.SequenceMatcher(None, seed, other).ratio() >= threshold
        )
        if n > best_n:
            best_n, best_seed = n, items[i]
    return best_n, best_seed


def _hhmmss(ts: str) -> int:
    h, m, s = int(ts[11:13]), int(ts[14:16]), int(ts[17:19])
    return h * 3600 + m * 60 + s


# --- GOAL --------------------------------------------------------------------
# `goal_text_in_memory` was CUT: pattern extraction scored 0-98% (median ~25%)
# across 42 agents. Locating a goal line is hard for a regex and trivial for a
# reader, so restatement is the judge's job. What survives needs no extraction.


def goal_features(block: Block, assigned: str | None, memory: str | None) -> None:
    if not assigned:
        block.put("assigned", Null("absent", "no goal row covers this day"))
        return
    block.put("assigned", assigned)

    if not memory:
        block.put(
            "assigned_goal_words_present",
            Null("absent", "no memory snapshot for this day"),
        )
        return

    want = content_words(assigned)
    if not want:
        block.put("assigned_goal_words_present", Null("edge", "goal has no content words"))
        return
    have = content_words(memory)
    # Ratio, never a boolean: Terra retains 92% and is the flagship case.
    block.put("assigned_goal_words_present", round(len(want & have) / len(want), 2))
    block.put("assigned_goal_words_missing", sorted(want - have)[:12])


# --- MEMORY ------------------------------------------------------------------
# Clause-level diffing was measured and CUT (verbatim survival 2-68% across a
# day; agents rewrite memory wholesale). Named items survive 36-97%.


def watchlist(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in config.WATCHLIST_PATTERNS:
        found |= set(re.findall(pattern, text))
    return found


def memory_features(
    block: Block, today: dict | None, prior: dict | None, prior_day: str | None,
    first_seen: dict[str, str] | None = None,
) -> None:
    if not today:
        block.put("snapshots_today", Null("absent", "no memory rows this day"))
        return
    block.put("snapshots_today", today["n"])

    now = watchlist(today["last_content"])
    if not prior:
        block.put("watchlist_persistence", Null("edge", "no prior snapshot"), heuristic=True)
    else:
        before = watchlist(prior["last_content"])
        block.put(
            "watchlist_persistence",
            {
                "carried": len(now & before),
                "new_today": sorted(now - before)[:15],
                "absent_today": sorted(before - now)[:15],
                "compared_against": prior_day,
            },
            heuristic=True,
            note="named items only; general clause diffing is unreliable (see spec)",
        )

    # Provenance closes the cross-day gap: B6's default inversion was 5 weeks
    # before its flagged day, B4's operator broadcast 6 weeks. A 14-day strip
    # cannot reach either.
    if first_seen:
        block.put(
            "watchlist_provenance",
            {tok: first_seen[tok] for tok in sorted(now) if tok in first_seen},
            heuristic=True,
            note="date each named item first appeared in this agent's memory",
        )


# --- ACTIVITY ----------------------------------------------------------------


def activity_features(block: Block, turns: list[dict], baseline: dict | None) -> None:
    kept = [
        t for t in turns
        if t["command"] is not None
        or (t["action"] and t["action"] not in config.DROP_ACTIONS)
    ]
    n = len(kept)
    block.put("turns", n)
    if not n:
        return

    mix = Counter()
    for t in kept:
        if t["command"] is not None:
            mix["bash"] += 1
        elif t["action"] in config.PAUSE_ACTIONS:
            mix["pause_wait"] += 1
        else:
            mix["gui"] += 1
    block.put("action_mix", {k: [v, round(v / n, 2)] for k, v in mix.most_common()})

    times = sorted(t["ts"] for t in kept)
    block.put("span", f"{times[0][11:16]}–{times[-1][11:16]}")
    gaps = [
        _hhmmss(times[i + 1]) - _hhmmss(times[i]) for i in range(len(times) - 1)
    ]
    block.put("gaps_over_30min", sum(1 for g in gaps if g > 1800))

    if baseline and baseline.get("turns"):
        block.put("turns_vs_own_median", round(n / baseline["turns"], 2))
    else:
        block.put(
            "turns_vs_own_median",
            Null("edge", f"fewer than {config.BASELINE_DAYS} prior active days"),
        )


# --- ARTIFACTS ---------------------------------------------------------------
# What the day's work was pointed at. new->0 while turns stay high is how B7's
# substitution began: finished the content, started polishing.


def artifact_features(block: Block, turns: list[dict], seen_before: set[str]) -> None:
    hosts = Counter()
    for t in turns:
        if t["command"]:
            for host in set(_URL_HOST.findall(t["command"])):
                hosts[host] += 1
    if not hosts:
        block.put(
            "distinct_hosts_touched",
            Null("absent", "no URLs in bash; agent may be GUI-only"),
            heuristic=True,
        )
        return
    today = set(hosts)
    block.put("distinct_hosts_touched", len(today), heuristic=True,
              note="URL regex over bash; misses GUI-only and non-URL targets")
    block.put("hosts_new_today", sorted(today - seen_before)[:15], heuristic=True)
    block.put("hosts_seen_earlier", len(today & seen_before), heuristic=True)
    block.put("most_touched", hosts.most_common(5), heuristic=True)


# --- REPETITION --------------------------------------------------------------


def repetition_features(block: Block, turns: list[dict], session_goals: list[str]) -> None:
    commands = [t["command"] for t in turns if t["command"]]
    if commands:
        n, seed = largest_cluster(commands, config.REPETITION_THRESHOLD)
        block.put(
            "largest_bash_group",
            {"n": n, "of": len(commands), "share": round(n / len(commands), 2),
             "shape": (seed or "")[:120]},
            heuristic=True,
            note=f"normalised, SequenceMatcher >= {config.REPETITION_THRESHOLD}",
        )
    else:
        block.put("largest_bash_group", Null("absent", "no bash this day"), heuristic=True)

    if len(session_goals) < config.MIN_SESSIONS_FOR_REPETITION:
        block.put(
            "session_goal_repetition",
            Null("edge", f"fewer than {config.MIN_SESSIONS_FOR_REPETITION} sessions"),
            heuristic=True,
        )
        return
    n, _ = largest_cluster(session_goals, config.REPETITION_THRESHOLD)
    block.put(
        "session_goal_repetition",
        {"n": n, "of": len(session_goals), "share": round(n / len(session_goals), 2)},
        heuristic=True,
        note="largest cluster, not adjacent similarity; catches verbatim "
             "templating only, not 'ship feature N'",
    )


# --- INTERACTION -------------------------------------------------------------


def interaction_features(
    block: Block, agent_id: str, agent_name: str, chat: list[dict],
    short_names: dict[str, list[str]], session_goals: list[str],
) -> None:
    sent = sum(1 for m in chat if m["agent_speaker_id"] == agent_id)
    block.put("chat_sent", sent)

    # Word boundaries, or 'GPT-5' matches inside 'GPT-5.6 Luna' — this exact
    # bug inflated an earlier nudge count.
    haystack = " ".join(session_goals)
    named = Counter()
    for other, variants in short_names.items():
        if other == agent_name:
            continue
        for v in variants:
            if re.search(rf"(?<![\w.-]){re.escape(v)}(?![\w.-])", haystack):
                named[other] += 1
                break
    block.put("agents_named_in_session_goals", named.most_common(6), heuristic=True,
              note="word-boundary matched")


def build_short_names(names: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in names:
        stripped = re.sub(r"^(Claude|Gemini|Kimi)\s+", "", n)
        out[n] = sorted({n, stripped}, key=len, reverse=True)
    return out


# --- CONTEXT (no markers; the judge reads these directly) --------------------


def memory_outline(content: str, max_sections: int = 60) -> list[str]:
    """Headers + first line under each. The remedy for 'can't see deletions'."""
    out: list[str] = []
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\s*#{1,4}\s+\S", line):
            head = line.strip()
            nxt = ""
            for follow in lines[i + 1 : i + 4]:
                if follow.strip():
                    nxt = follow.strip()[:120]
                    break
            out.append(f"{head}  |  {nxt}")
            if len(out) >= max_sections:
                break
    return out


def history_strip(prior_days: list[tuple[str, str]]) -> list[str]:
    """Agent's own LAST session goal per active day — what happened, not planned."""
    return [
        f"{day}  {goal[: config.HISTORY_GOAL_CHARS]}"
        for day, goal in prior_days[-config.HISTORY_STRIP_DAYS :]
    ]
