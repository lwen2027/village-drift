"""Streaming loaders for the AI Village dump.

The dump is large (computer_use_turns 2.0GB, agent_memories 2.2GB gzipped), so
every loader makes a single pass and keeps only what a date range needs.
"""

from __future__ import annotations

import gzip
import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterator

from . import config

try:  # 3-5x faster parsing when present; stdlib fallback keeps deps at zero
    import orjson

    def _loads(b):
        return orjson.loads(b)

    _BINARY = True
except ImportError:  # pragma: no cover
    import json

    def _loads(b):
        return json.loads(b)

    _BINARY = False


def _path(name: str) -> str:
    path = os.path.join(config.DATA_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Set VILLAGE_DATA to the dump directory."
        )
    return path


def _rows(name: str) -> Iterator[dict]:
    with gzip.open(_path(name), "rb" if _BINARY else "rt") as fh:
        for line in fh:
            if line.strip():
                yield _loads(line)


def _day_tokens(start: str | None, end: str | None) -> list[bytes] | None:
    """Raw byte prefixes for a date range, for substring prefiltering.

    Parsing JSON just to read a date is the whole cost of a narrow run: two
    ~2GB files, ~1.3M rows. A raw `in` test on the undecoded line skips
    json.loads for ~99% of rows on a short range. False positives (a date
    mentioned in message text) are harmless — the parsed row is re-checked.
    Collapses to a month prefix when a range is long, and returns None for an
    unbounded range, where prefiltering cannot help.
    """
    if not start or not end:
        return None
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    span = (d1 - d0).days
    if span > 120:
        return None
    if span > 25:  # month granularity
        months, cur = [], d0.replace(day=1)
        while cur <= d1:
            months.append(cur.strftime("%Y-%m").encode())
            cur = (cur.replace(day=28) + timedelta(days=5)).replace(day=1)
        return months
    return [
        (d0 + timedelta(days=i)).isoformat().encode() for i in range(span + 1)
    ]


def _rows_dated(name: str, tokens: list[bytes] | None) -> Iterator[dict]:
    """Stream rows, skipping lines whose raw bytes contain no in-range date."""
    if tokens is None:
        yield from _rows(name)
        return
    with gzip.open(_path(name), "rb") as fh:
        for line in fh:
            if any(t in line for t in tokens):
                yield _loads(line)


def day_of(ts) -> str:
    return str(ts)[:10]


def in_range(day: str, start: str | None, end: str | None) -> bool:
    return (start is None or day >= start) and (end is None or day <= end)


def load_agents() -> dict[str, str]:
    """agent_id -> name"""
    return {r["id"]: r["name"] for r in _rows("agents.jsonl.gz")}


def load_goals() -> tuple[list[dict], list[dict]]:
    """(agent_goals, village_goals), each sorted by start_time."""
    ag = sorted(_rows("agent_goals.jsonl.gz"), key=lambda r: str(r.get("start_time") or ""))
    vg = sorted(_rows("village_goals.jsonl.gz"), key=lambda r: str(r.get("start_time") or ""))
    return ag, vg


def load_sessions(start=None, end=None) -> tuple[dict, dict]:
    """
    session_id -> (agent_id, day, created_at, session_goal)
    (agent_id, day) -> [(created_at, session_goal), ...] sorted
    """
    by_id: dict[str, tuple] = {}
    by_agent_day: dict[tuple, list] = defaultdict(list)
    for r in _rows_dated("computer_use_sessions.jsonl.gz", _day_tokens(start, end)):
        day = day_of(r.get("created_at"))
        if not in_range(day, start, end):
            continue
        aid = r.get("agent_id")
        goal = " ".join(str(r.get("session_goal") or "").split())
        by_id[r["id"]] = (aid, day, str(r["created_at"]), goal)
        by_agent_day[(aid, day)].append((str(r["created_at"]), goal))
    for v in by_agent_day.values():
        v.sort()
    return by_id, by_agent_day


def load_turns(sessions_by_id: dict, start=None, end=None) -> dict[tuple, list[dict]]:
    """
    (agent_id, day) -> [turn, ...] sorted by created_at.

    `day` is the day the TURN happened, not the day its session opened.
    Sessions cross midnight: keying by session day silently moves work onto
    the wrong date (observed on real data — GPT-5 had 13 bash turns from a
    23:58 session counted on the previous day).

    Only fields Stage 1 needs are retained; the raw provider blob is dropped
    after extraction to keep memory bounded.
    """
    out: dict[tuple, list[dict]] = defaultdict(list)
    for r in _rows_dated("computer_use_turns.jsonl.gz", _day_tokens(start, end)):
        meta = sessions_by_id.get(r.get("session_id"))
        if not meta:
            continue
        aid = meta[0]
        day = day_of(r.get("created_at"))  # turn's own day, NOT the session's
        action = r.get("agent_action") or {}
        if not isinstance(action, dict):
            action = {}
        reasoning, narration = split_messages(r.get("agent_messages"))
        out[(aid, day)].append(
            {
                "ts": str(r.get("created_at")),
                "session_id": r.get("session_id"),
                "command": action.get("command"),
                "action": action.get("action"),
                "reasoning_len": len(reasoning),
                "narration": narration,
                "output": str(r.get("output") or ""),
                "error": str(r.get("error") or ""),
            }
        )
    for v in out.values():
        v.sort(key=lambda t: t["ts"])
    return out


def split_messages(am) -> tuple[str, str]:
    """
    Return (reasoning, narration) across all five provider shapes.

    Anthropic        dict with content=[{type:thinking|text}]
    Claude-Code SDK  dict with _sdkFormat and textMessage/thinkingMessage,
                     each wrapping an Anthropic message
    Gemini           dict with candidates[].content.parts[] and a `thought` flag
    OpenAI-Chat      dict with .content (str) plus reasoning under EITHER
                     `reasoning` (OpenAI, xAI) or `reasoning_content`
                     (DeepSeek, Kimi, Grok 4.5, the fine-tuned leaders)
    OpenAI-Responses list of {type:reasoning,summary[]} / {type:message,content[]}

    Getting this wrong silently empties a whole provider — it has happened
    three times. Twice undetected until 2026-09-28: `reasoning_content` cost
    137,748 turns of reasoning across six agents, and the SDK envelope cost
    all 11,700 turns of "Opus 4.5 (Claude Code)" in BOTH channels. Neither
    raised an error; both produced auditors confidently reporting that an
    agent does not think. If you add a shape, add it to
    tests/test_smoke.py::test_split_every_provider_shape too, and re-run the
    per-agent channel census in docs/STAGE1_PROTOCOL.md.
    """
    reasoning: list[str] = []
    narration: list[str] = []

    if isinstance(am, dict):
        # Claude-Code SDK envelope: two ordinary Anthropic messages side by
        # side under `textMessage` / `thinkingMessage`. The Anthropic branch
        # below already reads that inner shape — nothing was unwrapping it, so
        # all 11,679 turns of "Opus 4.5 (Claude Code)" came back with BOTH
        # channels empty. Same family as the reasoning_content gap: a provider
        # shape nobody checked, failing silently rather than loudly.
        if am.get("_sdkFormat"):
            for part in ("thinkingMessage", "textMessage"):
                inner = (am.get(part) or {}).get("message")
                if inner:
                    r, n = split_messages(inner)
                    reasoning.append(r)
                    narration.append(n)
            return ("\n".join(x for x in reasoning if x),
                    "\n".join(x for x in narration if x))

        content = am.get("content")
        if isinstance(content, list):  # Anthropic
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "thinking":
                    reasoning.append(block.get("thinking") or "")
                elif block.get("type") == "text":
                    narration.append(block.get("text") or "")
        elif isinstance(content, str):  # OpenAI-Chat
            narration.append(content)
        # OpenAI-Chat. Two spellings in the wild: `reasoning` (OpenAI/xAI) and
        # `reasoning_content` (the DeepSeek convention, copied by Kimi, Grok 4.5
        # and the fine-tuned leaders). Reading only the first one dropped the
        # reasoning channel for six agents and 137,748 turns — 6% of the dump,
        # 97% of Kimi K2.6 — with no error, exactly as the docstring warns.
        for key in ("reasoning", "reasoning_content"):
            if isinstance(am.get(key), str):
                reasoning.append(am[key])
        for cand in am.get("candidates") or []:  # Gemini
            for part in ((cand.get("content") or {}).get("parts") or []):
                text = part.get("text") or ""
                (reasoning if part.get("thought") else narration).append(text)

    elif isinstance(am, list):  # OpenAI-Responses
        for block in am:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "reasoning":
                for s in block.get("summary") or []:
                    reasoning.append(s.get("text") or "")
            elif block.get("type") == "message":
                for c in block.get("content") or []:
                    narration.append(c.get("text") or "")

    return (
        " ".join(x for x in reasoning if x).strip(),
        " ".join(x for x in narration if x).strip(),
    )


def load_memory_snapshots(start=None, end=None) -> dict[tuple, dict]:
    """
    (agent_id, day) -> {"first_ts", "last_ts", "last_content", "n"}

    Only the last snapshot's content is kept — 'entering state' for day N is
    the last snapshot of the previous ACTIVE day, not calendar-yesterday.
    """
    acc: dict[tuple, dict] = {}
    for r in _rows_dated("agent_memories.jsonl.gz", _day_tokens(start, end)):
        day = day_of(r.get("created_at"))
        if not in_range(day, start, end):
            continue
        key = (r.get("agent_id"), day)
        ts = str(r.get("created_at"))
        entry = acc.get(key)
        if entry is None:
            acc[key] = {
                "first_ts": ts,
                "last_ts": ts,
                "last_content": str(r.get("content") or ""),
                "n": 1,
            }
        else:
            entry["n"] += 1
            if ts < entry["first_ts"]:
                entry["first_ts"] = ts
            if ts > entry["last_ts"]:
                entry["last_ts"] = ts
                entry["last_content"] = str(r.get("content") or "")
    return acc


def load_chat(start=None, end=None) -> dict[str, list[dict]]:
    """day -> [{ts, speaker_type, agent_speaker_id, content}] sorted."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in _rows_dated("chat_messages.jsonl.gz", _day_tokens(start, end)):
        day = day_of(r.get("created_at"))
        if not in_range(day, start, end):
            continue
        out[day].append(
            {
                "ts": str(r.get("created_at")),
                "speaker_type": r.get("speaker_type"),
                "agent_speaker_id": r.get("agent_speaker_id"),
                "content": " ".join(str(r.get("content") or "").split()),
            }
        )
    for v in out.values():
        v.sort(key=lambda m: m["ts"])
    return out


def load_goal_announcements(days: set[str]) -> dict[str, list[dict]]:
    """day -> [{ts, content}] for HUMAN messages on a goal-start day.

    The stored goal title is 19-86 characters; the operator's announcement of it
    in chat runs 1,105-2,040 and routinely carries the part that constrains
    method — a no-code rule, a publishing cap, "the idea of your goal is that
    you publish it on a website as a serial", a grant of overnight compute. None
    of that is in agent_goals, and a day-scoped digest never reaches the day the
    goal was announced. So it is fetched here by goal-start date and carried
    forward onto every day the goal governs.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    if not days:
        return out
    # Deduped by content. The operator posts the same announcement separately
    # into #general, #best and #rest, so the 2026-07-06 goal text appears three
    # times within one second; undeduped it consumed half the per-day budget and
    # pushed the per-agent addenda (a no-password rule, a "publish it as a web
    # serial" gloss) out of the window entirely.
    seen: dict[str, set] = defaultdict(set)
    for r in _rows_dated("chat_messages.jsonl.gz", _day_tokens(min(days), max(days))):
        day = day_of(r.get("created_at"))
        if day not in days or r.get("agent_speaker_id"):
            continue
        content = " ".join(str(r.get("content") or "").split())
        if content in seen[day]:
            continue
        seen[day].add(content)
        out[day].append({"ts": str(r.get("created_at")), "content": content})
    for v in out.values():
        v.sort(key=lambda m: m["ts"])
    return out


def load_outreach_constraints() -> dict[str, list[dict]]:
    """agent_id -> [{ts, approval, comment, medium}] sorted, for rows carrying
    an adminComment.

    A SECOND OPERATOR CHANNEL, invisible to any sweep of chat_messages. When an
    agent requests approval for unsolicited outreach the operator can attach a
    free-text instruction to the decision, and those instructions bind: "I'd
    hold off on reaching out to more communities until you see if your first
    outreach attempts are welcomed", "maybe making new art to post online in
    public", "If you ever get the sense that these are unwelcome I'd stop
    posting on Pinterest". 128 such comments exist. An agent whose day contains
    no outreach may be complying with one of them rather than neglecting its
    goal, and nothing in chat says so.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    # Optional, like data/metrics.json: `events` is not in every dump (the test
    # fixture omits it), and an absent file means "no constraints known", not a
    # crash. Without this the whole build dies on a partial dump.
    if not os.path.exists(os.path.join(config.DATA_DIR, "events.jsonl.gz")):
        return out
    for r in _rows("events.jsonl.gz"):
        d = r.get("data")
        if not isinstance(d, dict) or d.get("actionType") != "OUTREACH_APPROVAL_RESPONSE":
            continue
        comment = " ".join(str(d.get("adminComment") or "").split())
        aid = d.get("agentId")
        if not comment or not aid:
            continue
        out[aid].append({"ts": str(r.get("created_at")),
                         "approval": str(d.get("approval")).lower() == "true",
                         "comment": comment,
                         "medium": " ".join(str(d.get("medium") or "").split())[:120]})
    for v in out.values():
        v.sort(key=lambda m: m["ts"])
    return out


def latest_constraints(constraints: list[dict], before_day: str, cap: int) -> list[dict]:
    """The operative outreach constraints as of `before_day`.

    Deduped by INSTRUCTION TEXT, then the most recent `cap`. Keying on the
    medium was tried first and is wrong: one agent accrued 31 comments of which
    four are the same "mention you're an AI" issued against four different
    sites, so per-medium dedup kept all four and crowded out every distinct
    instruction. Keying on the text keeps one copy of a repeated rule and lets
    genuinely different ones through.

    Most-recent is the right ordering for what survives the cap, because a later
    decision supersedes an earlier one — an approval on 07-23 is the operative
    word on a channel denied on 07-06, not a second constraint beside it.
    """
    per: dict[str, dict] = {}
    for c in constraints:
        if c["ts"][:10] < before_day:
            per[" ".join(c["comment"].lower().split())] = c  # later overwrites
    return sorted(per.values(), key=lambda c: c["ts"])[-cap:]


def load_metrics(path: str | None = None) -> dict[tuple, list[dict]]:
    """(agent_name, metric_key) -> [{day, last_value, last_source}, ...] sorted.

    Produced by scripts/pull_metrics.py. metric_datapoints is DB-only — it is
    excluded from the public dump — so this is optional; absent file means the
    metric fields emit null(absent).
    """
    import json as _json

    path = path or os.path.join("data", "metrics.json")
    if not os.path.exists(path):
        return {}
    out: dict[tuple, list[dict]] = defaultdict(list)
    for r in _json.load(open(path)):
        if not r.get("agent"):
            continue
        out[(r["agent"], r["metric_key"])].append(
            {"day": r["day"], "last_value": r["last_value"],
             "last_source": r["last_source"]}
        )
    for v in out.values():
        v.sort(key=lambda x: x["day"])
    return out
