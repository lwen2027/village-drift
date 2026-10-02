"""The evidence layer: what the agent actually did, said and thought.

Moved here from goldenset/render_digest.py on 2026-10-01. It lived there
because the digest was the only consumer; it is here because Stage 2 reads
it alongside the derived block, and `goldenset` imports `drift`, not the
other way round. render_digest.py now calls this rather than carrying its
own copy.

THE LAYERING THIS COMPLETES. One raw agent-day supports two derivations:

    raw day  {goal, sessions, turns, chat, memory}
      |- features.py  -> facts + context   the DERIVED layer (Stage 1)
      `- evidence()   -> bash, chat, memory, reasoning   (Stage 2 adds this)

Before, the two were rendered by separate files that each re-derived the
shared sections, and Stage 2 glued a sliced-off block head to a digest to
get both. That slicing function is gone.

Everything here SAMPLES, and every sample says so in the text it emits. A
reader who is not told a channel was sampled will read a gap as a silence.
"""
from __future__ import annotations

import collections

BASH_TURNS   = 100   # ditto — p90 was 225 KB of shell log per day
CMD_CHARS = 160      # enough to see intent and redirect target
OUT_CHARS = 400      # results, not intent
CHAT_PEERS   = 40    # peer messages; human/operator msgs are never sampled
CHAT_CTX_BEFORE = 3  # messages of antecedent kept around each selected one
CHAT_CTX_AFTER  = 1
CHAT_CHARS = 400
MEM_CHARS = 4000     # the last snapshot of the day
REASON_TURNS = 30    # systematic sample; see note below
REASON_CHARS = 420   # per turn


def _systematic(items: list, n: int) -> list:
    """Every k-th item. Bounded, reproducible, and embeds no judgement."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def _addressed_to(text: str, agent: str, roster: set) -> bool:
    """Is this human/operator message for THIS agent?

    Show it if it names the agent, or names no agent at all (a broadcast like
    "resume the village for today"). Suppress it if it names only other agents.

    Without this, the nudger drowns the section: it is auto-generated and
    @-addressed, so on 2026-08-03 Claude Opus 4.6's digest carried 18 nudges
    sent to Luna, Terra, DeepSeek and six others, and one message actually for
    everyone. A labeller skimming a wall of "repeatedly idling" can easily
    mis-attribute it to the agent whose digest it is.
    """
    named = {a for a in roster if _names_agent(text, a)}
    return not named or agent in named


def _names_agent(text: str, agent: str) -> bool:
    """Does this message name the agent? Word-boundary on BOTH sides.

    `@GPT-5` matching inside `@GPT-5.6` silently mis-attributed a nudge count
    earlier in this project — that is what the trailing guard prevents. The
    leading guard is the mirror image and matters just as much: without it,
    agent "A" matched the final letter of "Luna", and "Sol" would match the end
    of "parasol". `.` and `-` are excluded as well as \\w because agent names
    contain both (GPT-5.6, Claude Opus 4.7).
    """
    import re
    return bool(re.search(r"(?<![\w.\-])" + re.escape(agent) + r"(?![\w.\-])",
                          text or "", re.I))


def _clip_cmd(s, n):
    """Head+tail within the same budget, for COMMANDS specifically.

    Plain truncation drops the end of the line, and the end of a shell
    command is where it says where the output went -- the redirect, the
    push, the upload target. Measured on 3,661 commands: 23% exceed the
    160-char budget, and 23% of those carry a redirect/push/upload in the
    last 80 characters. So ~5% of all commands were having their
    DESTINATION cut off, in a pipeline whose central question is whether
    anything reached anyone.

    The shape is features.cap_bash's, which had this right and was never
    wired to anything. This keeps cap_bash's head+tail at the digest's
    tighter budget rather than its 500-char one, so the fix costs no tokens.
    """
    s = str(s or "")
    if len(s) <= n:
        return s
    head, tail = int(n * 0.7), n - int(n * 0.7) - 3
    return s[:head] + "…[+%dc]…" % (len(s) - head - tail) + s[-tail:]


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + f" …[+{len(s) - n}c]"


def evidence(agent: str, data: dict, roster: set = frozenset(),
             standalone: bool = True) -> str:
    """BASH, CHAT, MEMORY and REASONING for one agent-day, from the raw record.

    `standalone=False` drops the ACTIVITY block and the MEMORY count line,
    because the DERIVED layer already carries both and carries them better
    -- its ACTIVITY has turns_vs_own_median and gaps_over_30min, which this
    cannot compute from one day. Set it False when rendering after a block;
    leave it True for a digest, which stands alone.

    Without this the composed artifact said ACTIVITY and MEMORY twice, in
    two renderings, which is the duplication the composition was meant to
    remove."""
    L: list[str] = []
    A = L.append
    turns = data["turns"]
    if standalone:
        mix = collections.Counter(t["kind"] for t in turns)
        A(f"## ACTIVITY — {len(turns)} turns")
        if turns:
            A(f"  span {str(turns[0]['ts'])[11:16]}–{str(turns[-1]['ts'])[11:16]}")
        A("  " + " · ".join(f"{k} {v}" for k, v in mix.most_common()))
        A("")

    bash = [t for t in turns if t["kind"] == "bash"]
    bsamp = _systematic(bash, BASH_TURNS)
    A(f"## BASH — {len(bash)} commands, showing {len(bsamp)} "
      f"(every {max(1, len(bash)//max(1,len(bsamp)))}th), {CMD_CHARS} chars each")
    if len(bsamp) < len(bash):
        A("   (sampled mechanically, NOT by interest — all of it is in eval/raw/)")
    for t in bsamp:
        A(f"  {str(t['ts'])[11:16]}  {_clip_cmd(t['command'], CMD_CHARS)}")
        if t.get("output") or t.get("error"):
            A(f"         -> {_clip((t.get('output') or '') + (t.get('error') or ''), OUT_CHARS)}")
    A("")

    # Own messages and operator messages addressed to this agent (or to nobody)
    # are never sampled: an operator instruction is the most common external
    # cause of a day changing direction. Peer messages that name it are sampled.
    keep = [c for c in data["chat"]
            if c["own"] or (c["human"] and _addressed_to(c["content"], agent, roster))]
    peers = [c for c in data["chat"]
             if not (c["own"] or c["human"]) and _names_agent(c["content"], agent)]
    # Selecting messages by addressee alone keeps a reply and discards what it
    # replied to, which reads as a non-sequitur: a peer offering "I can take one
    # of the playback checks" is meaningless without the exchange that prompted
    # it. So every selected message drags its immediate antecedent along.
    chron = sorted(data["chat"], key=lambda c: str(c["ts"]))
    sel = {id(c) for c in keep + _systematic(peers, CHAT_PEERS)}
    idx = sorted(i for i, c in enumerate(chron) if id(c) in sel)
    with_ctx: dict = {}
    for i in idx:
        for j in range(max(0, i - CHAT_CTX_BEFORE),
                       min(len(chron), i + CHAT_CTX_AFTER + 1)):
            with_ctx.setdefault(j, j in idx or with_ctx.get(j, False))
        with_ctx[i] = True
    shown = [(chron[j], with_ctx[j]) for j in sorted(with_ctx)]
    hidden = len(data["chat"]) - len(shown)
    A(f"## CHAT — {sum(1 for c, _ in shown if c['own'])} sent by this agent, "
      f"{sum(1 for c, sel in shown if sel and not c['own'])} to it or from a human, "
      f"{sum(1 for _, sel in shown if not sel)} lines of surrounding context")
    A(f"   ({hidden} other messages in the shared room not shown — "
      f"full transcript in eval/raw/)")
    A(f"   (lines marked · are surrounding context, kept so replies have their "
      f"antecedent)")
    for c, selected in shown:
        arrow = "→" if c["own"] else ("←" if selected else "·")
        A(f"  {str(c['ts'])[11:16]}  {arrow} {c['speaker']}: {_clip(c['content'], CHAT_CHARS)}")
    A("")

    A(f"## MEMORY — {len(data['memory'])} snapshots; last one of the day below"
      if standalone else "## MEMORY — last snapshot of the day")
    if data["memory"]:
        A(_clip(data["memory"][-1]["content"], MEM_CHARS))
    A("")

    r = [t for t in turns if t.get("reasoning")]
    samp = _systematic(r, REASON_TURNS)
    A(f"## REASONING — recorded for {len(r)} of {len(turns)} turns; "
      f"showing {len(samp)}, every {max(1, len(r)//max(1,len(samp)))}th")
    A("   (availability varies 28–98% by provider; absence is not silence.")
    A("    Sampled mechanically, NOT by interest. All of it is in eval/raw/.)")
    for t in samp:
        A(f"  {str(t['ts'])[11:16]}  {_clip(t['reasoning'], REASON_CHARS)}")
    return "\n".join(L) + "\n"
