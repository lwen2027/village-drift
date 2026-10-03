"""The evidence layer: what the agent actually did, said and thought.

Moved here from evaluation/goldens/render_digest.py on 2026-10-01. It lived there
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
from dataclasses import dataclass, replace

from .compress import clip_head_tail, clip_words, evenly_spaced_sample


@dataclass(frozen=True)
class EvidencePolicy:
    """Mechanical evidence limits; none of these choices judge relevance."""

    bash_turns: int = 100
    command_chars: int = 160
    output_chars: int = 400
    own_chat: int | None = None
    peer_chat: int = 40
    chat_context_before: int = 3
    chat_context_after: int = 1
    chat_chars: int = 400
    memory_chars: int = 4000
    reasoning_turns: int = 30
    reasoning_chars: int = 420
    include_reasoning: bool = True

    def without_reasoning(self):
        return replace(self, include_reasoning=False)


# The labelling surface keeps its established, deliberately generous view.
HUMAN_EVIDENCE = EvidencePolicy()

# Stage 2 receives the Stage-1 projection as well as this evidence layer. Its
# quotas are tighter so several days fit in one causal read. Selection remains
# systematic and every omitted count is printed into the payload.
STAGE2_EVIDENCE = EvidencePolicy(
    bash_turns=50,
    own_chat=60,
    peer_chat=24,
    chat_context_before=2,
    reasoning_turns=18,
)


def addressed_to(text: str, agent: str, roster: set) -> bool:
    """Is this human/operator message for THIS agent?

    Show it if it names the agent, or names no agent at all (a broadcast like
    "resume the village for today"). Suppress it if it names only other agents.

    Without this, the nudger drowns the section: it is auto-generated and
    @-addressed, so on 2026-08-03 Claude Opus 4.6's digest carried 18 nudges
    sent to Luna, Terra, DeepSeek and six others, and one message actually for
    everyone. A labeller skimming a wall of "repeatedly idling" can easily
    mis-attribute it to the agent whose digest it is.
    """
    named = {a for a in roster if names_agent(text, a)}
    return not named or agent in named


def names_agent(text: str, agent: str) -> bool:
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
    head, tail = int(n * 0.7), n - int(n * 0.7) - 3
    return clip_head_tail(str(s or ""), head, tail,
                          lambda dropped: f"…[+{dropped}c]…")


def _clip(s, n):
    return clip_words(s, n)


def evidence(agent: str, data: dict, roster: set = frozenset(),
             standalone: bool = True,
             policy: EvidencePolicy = HUMAN_EVIDENCE) -> str:
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
    bsamp = evenly_spaced_sample(bash, policy.bash_turns)
    A(f"## BASH — {len(bash)} commands, showing {len(bsamp)} "
      f"(every {max(1, len(bash)//max(1,len(bsamp)))}th), "
      f"{policy.command_chars} chars each")
    if len(bsamp) < len(bash):
        A("   (sampled mechanically, NOT by interest — all of it is in evaluation/evidence/raw/)")
    for t in bsamp:
        A(f"  {str(t['ts'])[11:16]}  {_clip_cmd(t['command'], policy.command_chars)}")
        if t.get("output") or t.get("error"):
            A(f"         -> {_clip((t.get('output') or '') + (t.get('error') or ''), policy.output_chars)}")
    A("")

    # Own messages and operator messages addressed to this agent (or to nobody)
    # are never sampled: an operator instruction is the most common external
    # cause of a day changing direction. Peer messages that name it are sampled.
    own = [c for c in data["chat"] if c["own"]]
    own_sample = (own if policy.own_chat is None
                  else evenly_spaced_sample(own, policy.own_chat))
    operators = [c for c in data["chat"]
                 if c["human"] and addressed_to(c["content"], agent, roster)]
    keep = own_sample + operators
    peers = [c for c in data["chat"]
             if not (c["own"] or c["human"]) and names_agent(c["content"], agent)]
    # Selecting messages by addressee alone keeps a reply and discards what it
    # replied to, which reads as a non-sequitur: a peer offering "I can take one
    # of the playback checks" is meaningless without the exchange that prompted
    # it. So every selected message drags its immediate antecedent along.
    chron = sorted(data["chat"], key=lambda c: str(c["ts"]))
    sel = {id(c) for c in keep
           + evenly_spaced_sample(peers, policy.peer_chat)}
    idx = sorted(i for i, c in enumerate(chron) if id(c) in sel)
    with_ctx: dict = {}
    for i in idx:
        for j in range(max(0, i - policy.chat_context_before),
                       min(len(chron), i + policy.chat_context_after + 1)):
            with_ctx.setdefault(j, j in idx or with_ctx.get(j, False))
        with_ctx[i] = True
    shown = [(chron[j], with_ctx[j]) for j in sorted(with_ctx)]
    hidden = len(data["chat"]) - len(shown)
    A(f"## CHAT — {sum(1 for c, _ in shown if c['own'])} sent by this agent, "
      f"{sum(1 for c, sel in shown if sel and not c['own'])} to it or from a human, "
      f"{sum(1 for _, sel in shown if not sel)} lines of surrounding context")
    A(f"   ({hidden} other messages in the shared room not shown — "
      f"full transcript in evaluation/evidence/raw/)")
    if len(own_sample) < len(own):
        A(f"   ({len(own)} messages were sent by this agent; showing "
          f"{len(own_sample)} selected systematically, not by interest)")
    A(f"   (lines marked · are surrounding context, kept so replies have their "
      f"antecedent)")
    for c, selected in shown:
        arrow = "→" if c["own"] else ("←" if selected else "·")
        A(f"  {str(c['ts'])[11:16]}  {arrow} {c['speaker']}: "
          f"{_clip(c['content'], policy.chat_chars)}")
    A("")

    A(f"## MEMORY — {len(data['memory'])} snapshots; last one of the day below"
      if standalone else "## MEMORY — last snapshot of the day")
    if data["memory"]:
        A(_clip(data["memory"][-1]["content"], policy.memory_chars))
    A("")

    if not policy.include_reasoning:
        A("## REASONING — omitted here because this day's reasoning is supplied "
          "once in the dedicated boundary section")
        return "\n".join(L) + "\n"

    r = [t for t in turns if t.get("reasoning")]
    samp = evenly_spaced_sample(r, policy.reasoning_turns)
    A(f"## REASONING — recorded for {len(r)} of {len(turns)} turns; "
      f"showing {len(samp)}, every {max(1, len(r)//max(1,len(samp)))}th")
    A("   (availability varies 28–98% by provider; absence is not silence.")
    A("    Sampled mechanically, NOT by interest. All of it is in evaluation/evidence/raw/.)")
    for t in samp:
        A(f"  {str(t['ts'])[11:16]}  {_clip(t['reasoning'], policy.reasoning_chars)}")
    return "\n".join(L) + "\n"


# Compatibility names for callers and tests that inspect the human policy.
BASH_TURNS = HUMAN_EVIDENCE.bash_turns
CMD_CHARS = HUMAN_EVIDENCE.command_chars
OUT_CHARS = HUMAN_EVIDENCE.output_chars
CHAT_PEERS = HUMAN_EVIDENCE.peer_chat
CHAT_CTX_BEFORE = HUMAN_EVIDENCE.chat_context_before
CHAT_CTX_AFTER = HUMAN_EVIDENCE.chat_context_after
CHAT_CHARS = HUMAN_EVIDENCE.chat_chars
MEM_CHARS = HUMAN_EVIDENCE.memory_chars
REASON_TURNS = HUMAN_EVIDENCE.reasoning_turns
REASON_CHARS = HUMAN_EVIDENCE.reasoning_chars

# Private aliases retained while downstream callers migrate to the public
# shared primitives.
_addressed_to = addressed_to
_names_agent = names_agent
