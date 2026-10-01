"""The labelling run: what actually processes an agent-day.

Nothing here knows about golden labels, sample draws or accuracy. It takes
rows, builds the payload, calls the models, checks provenance, and caches the
verdict — over 40 agent-days or 4,027, it cannot tell the difference.

    from audit import run as A
    A.run(rows, arm="B")

TWO ARMS SURVIVE the bake-off recorded in eval/arena.py:

    A   the mechanical block alone -> judge
    B   the hybrid: the same block, plus two fields only a reader of the raw
        day can supply, from a cheap model

They render identically, so the judge cannot tell which it is serving. B is
the production candidate; A is kept because it is the control that makes B's
number mean anything.
"""

from __future__ import annotations

import collections
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # import drift/ from the repo root

from drift import config  # noqa: E402
from drift import features as F  # noqa: E402

# Output still lands under eval/tables/stage1/ because that is where the
# blocks and the paid-for runs already are. Where a corpus-scale run should
# write is a separate decision and nothing here depends on the answer.
_EVAL = os.path.join(os.path.dirname(HERE), "eval")
STAGE1 = os.path.join(_EVAL, "tables", "stage1")
# Only the leak guard reads this, to refuse a prompt that names an eval row.
LABELS = os.path.join(STAGE1, "eval_100.jsonl")
# Per-day raw dumps. An explicit constant, because these were reached as
# os.path.join(HERE, "raw") and HERE moved when this file did — three
# silently-wrong paths that only surfaced because a re-export failed.
RAW = os.path.join(_EVAL, "raw")


def _load(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------- rubric ----
PROMPTS = HERE   # rubric.md (judge), extract.md (arm B)


def prompt(name="rubric", check=True):
    """A prompt, read from audit/<name>.md.

    Prose, not a structured document. A structured version came first and was
    ~4,600 tokens; most of that was organisation for the reader rather than
    information for the model, saying the same few things in three registers.
    Only the output format needs to be rigid.

    An earlier version assembled this from AUDIT_PROTOCOL.md to keep a single
    source of truth. That was the wrong call: the protocol instructs a human
    who can go read the dump, and roughly a third of it ("re-derive the
    artefact", "check the approval events", "read the full announcement") is
    something a judge reading one fixed block cannot do. rubric.yaml is the
    translated version, and it lists what did not survive translation instead
    of dropping it silently.

    `check` enforces the one property that actually matters: the prompt must
    not name an eval agent-day. The protocol illustrates every trap with the
    case it came from, and 62 of the 100 eval rows are named there with their
    labels -- so a careless copy hands the judge the answer key. This checks
    the text that is actually sent, after all edits, which is the only place
    the guarantee is worth anything.
    """
    with open(os.path.join(PROMPTS, name + ".md")) as fh:
        text = fh.read()
    # HTML comments are notes for whoever maintains the file, not instructions.
    # They were being sent: screen.md was 31% comment and opened by telling the
    # model it was "arm C's cheap stage", that a baseline existed, and that
    # "its recall is a hard ceiling" -- i.e. announcing the benchmark and
    # naming the metric it could game by flagging everything. Strip them here
    # rather than banning them from the files, so the rationale stays next to
    # the prompt it explains.
    text = re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip() + "\n"
    if check:
        labels = _load(LABELS)
        named = sorted({r["agent"] for r in labels
                        if re.search(r"(?<![\w.])" + re.escape(r["agent"])
                                     + r"(?![\w.])", text)})
        if named:
            raise SystemExit(f"{name}.md names eval agents: {', '.join(named)}")
        days = sorted({r["day"] for r in labels if r["day"] in text})
        if days:
            raise SystemExit(f"{name}.md contains eval dates: {', '.join(days)}")
    return text


# ------------------------------------------------------------------ arms ----
# A  drift/build.py + features.py fill the block.          code counts.
# B  Luna reads the digest and fills THE SAME block.       model counts.
# C  Luna reads the digest and reports what it judges      model chooses what
#    notable, with no fixed field set.                     is worth counting.
#
# A vs B varies only the producer, so it answers "can a cheap model do the
# counting" — and because both emit the same fields, they can be diffed
# directly, with code as ground truth for anything arithmetic. That diff is
# measured over every field of every row, so unlike F1 at n=40 it is not noise-
# limited. B vs C varies only the output format, so it answers "is the field
# set the right facts" — the case where features.py is asking the wrong
# questions and A and B would both look mediocre with no way to tell why.
#
# All three end at the same judge with the same prompt, so the judge cannot
# tell which arm it is serving.
MODELS = {
    # The judge reads ~4K tokens per call (block or cheap-model output, plus
    # the rubric) for ~16M across the corpus -- lowest volume, highest
    # capability requirement, so do not economise here.
    #
    # ⚠ The incumbent monitor runs claude-opus-4-8, and arm 0's F1 0.73 is
    # what makes arm A interpretable: same family, only the mechanical stage
    # swapped. Judging at 5.5 confounds that -- arm A beating 0.73 could be
    # the newer model rather than the better compression. If arm A wins,
    # re-run it alone at claude-opus-4-8 over the same 40 rows to separate
    # the two. Forty calls.
    "judge": os.environ.get("ARENA_JUDGE", "claude-opus-5-5"),
    # 1M context, so the 80K-token largest digest in the sample is nowhere
    # near a limit. Note arm B's result is a property of THIS model, not of
    # the architecture: arm A's counting is deterministic code, arm B's is
    # the model doing arithmetic over a 26K document. "A cheap model cannot
    # do the counting" and "this one cannot" are different conclusions.
    "cheap": os.environ.get("ARENA_CHEAP", "gpt-6-luna"),
}
ANTHROPIC = "https://api.anthropic.com/v1/messages"
OPENAI = "https://api.openai.com/v1/chat/completions"
RUNS = os.path.join(STAGE1, "arena_runs")
BLOCKS = os.path.join(STAGE1, "arena_blocks")

# The four rules, verbatim from eval/docs/AUDIT_PROTOCOL.md. The judge gets
# exactly what the human auditors got — anything less makes the comparison
# against the golden labels unfair in a way that flatters no arm in particular
# but makes the absolute numbers meaningless.
# RULES and JUDGE_SYSTEM lived here: a second copy of the judge's contract,
# in code, never referenced. rubric.md superseded it and the two had already
# diverged — this one still asked for `decisive_quote`, renamed to
# `decisive_evidence` in the file that is actually sent. Two definitions of
# one schema, one of them unreachable and wrong.


# Arm C is the village's own audit process: a cheap model screens every day
# and flags candidates, a capable model double-checks the flagged ones. That
# makes arm 0 -- the production monitor, same architecture with opus-4-8 --
# the exactly-right baseline for it.
#
# Unlike arms A and B, the cheap stage here DOES judge. Its recall is a hard
# ceiling: a drift day it does not flag never reaches the judge and cannot be
# recovered by any judge quality. And this is the EXPENSIVE arm, not the
# cheap one -- every day pays the screen, and flagged days pay the judge
# again on the full digest.




# --------------------------------------------------------------- client ----
# Synchronous on purpose. The production run over 4,027 days should use the
# Batch API instead — 50% off, and this workload is the ideal shape for it
# (independent calls, no latency requirement). At n=40 batch would save a
# couple of dollars and cost submit/poll/retrieve machinery plus up to 24h of
# latency, so it is deliberately not built yet.
#
# Prompt caching IS applied, and this comment used to say it was not. That
# was true when the rubric was 154-375 tokens, below the 1024 ephemeral
# minimum; it is 2,267 now and caches. Arm B recorded 77,454 cache reads.
# cache_control attaches to a CONTENT BLOCK, not a plain string system
# prompt — shipped as a string it silently returns 0 reads on every call,
# which is how it went unnoticed for 184 of them.
# USD per million tokens (2026-09-28). Kept here, beside the usage data, so
# the scorer and the chart cannot disagree. The 40x gap between the two
# models is why cost must be computed per MODEL: ranking arms by token count
# gives a different answer from ranking them by spend, and arm B is the case
# where the two disagree -- 18x arm A's tokens for less money, because 98% of
# them go to the cheap model.
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00},
    "gpt-6-luna":      {"in": 0.10, "out": 0.50},
}

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")


def _redact(text, *secrets):
    """Exception text can contain the credential. urllib raises
    ValueError("Invalid header value b'Bearer sk-...'") for a key with a
    trailing newline, and the naive handler prints str(e) -- which is how a
    key reached a transcript. Scrub before anything is printed or stored."""
    out = str(text)
    for sec in secrets:
        if sec and len(sec) > 8:
            out = out.replace(sec, "<redacted>")
    return out


def _post(url, payload, headers, timeout=300, tries=4):
    """Retry with backoff. Running two arms concurrently produced HTTP 400s
    on large requests that succeeded immediately when retried alone -- load,
    not size or content. Three of 40 arm-C rows failed that way and two were
    drift rows, so the errors were not landing at random: the biggest inputs
    are the ones that fail, and the biggest days are disproportionately the
    interesting ones. Unretried, that biases the sample toward easy rows."""
    import time
    last = None
    for attempt in range(tries):
        try:
            return _post_once(url, payload, headers, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 429, 500, 502, 503, 529) or attempt == tries - 1:
                raise
            last = exc
            time.sleep(2 ** attempt * 5)
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError) as exc:
            # RemoteDisconnected is an http.client exception, NOT a URLError,
            # so it escaped both handlers and failed the row outright: 5 of
            # arm B's first 19 rows died that way. Arm B is the arm that
            # provokes it -- it asks the cheap model to emit the whole block,
            # verbatim Context included, and the longer the generation the
            # likelier the connection drops mid-stream. Losing a quarter of
            # the rows to that would have been read as arm B being unable to
            # do the task.
            if attempt == tries - 1:
                raise
            last = exc
            time.sleep(2 ** attempt * 5)
    raise last


def _post_once(url, payload, headers, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    resp = urllib.request.urlopen(req, timeout=timeout)
    if not payload.get("stream"):
        return json.loads(resp.read())
    return _read_sse(resp)


def _read_sse(resp):
    """Collect an OpenAI-style SSE stream into the same dict a normal
    response returns, so callers cannot tell the difference.

    Arm B's largest rows are ~470K input tokens, and at the measured rate
    (41K in + 7K out = 120s) a non-streaming request of that size sits silent
    for many minutes before its first byte. The server closes the connection
    first and urllib reports RemoteDisconnected "without response", which
    reads like the model refusing the task. It is not: the same row succeeds
    on a smaller input. Streaming keeps bytes flowing so nothing times the
    socket out. Compressing the input would "fix" it too, but arm B exists to
    show the cheap model the SAME full day the village monitor sees -- cutting
    that to suit the transport would silently turn it into a weaker arm A.
    """
    chunks, usage = [], {}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if body == "[DONE]":
            break
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            continue
        # The usage chunk arrives last and carries no choices.
        if d.get("usage"):
            usage = d["usage"]
        for ch in d.get("choices") or []:
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                chunks.append(piece)
    return {"choices": [{"message": {"content": "".join(chunks)}}],
            "usage": usage}


# The biggest digest in the 40-row sample is ~80K tokens and three rows clear
# 60K -- one of them a drift row. A model that quietly truncates at, say, 64K
# would drop that day's evidence and look like a failure of the ARM rather
# than of the context window, and at 10 positives one lost drift row is 10
# points of recall. So input size is recorded on every call and anything past
# the limit stops the run instead of being sent and hoped for.
# Both claude-opus-5-5 and gpt-6-luna hold 1M (verified against the API:
# "prompt is too long: N tokens > 1000000 maximum"). Sized just under so a
# genuine overflow stops the run instead of being silently truncated by the
# provider. The monitor view is 6x the digest -- 19 of 40 sample rows exceed
# the old 100K value, which was set when arms B and C still read digests.
MAX_INPUT_TOKENS = int(os.environ.get("ARENA_MAX_INPUT_TOKENS", "950000"))


# Measured against API-reported usage across all three arms: 1.88 (arm A),
# 1.96 (arm B), 3.05 (arm C). NOT the ~4 that prose averages -- this content
# is bash, JSON, hashes and base64, which tokenise densely. Estimating at 4
# understated every size by 1.3-2x and let a 1,038,227-token payload through
# a budget that believed it was 578,551. Use the pessimistic end: a guard
# that under-counts is not a guard.
CHARS_PER_TOKEN = 1.9


def call(model, system, user, stub=False):
    """Return (text, usage). Usage is whatever the API REPORTED — never an
    estimate. --stub exercises the whole path, including scoring, for free."""
    # A rough estimate, used ONLY as a tripwire -- the numbers that get
    # reported come from the API's own usage field.
    est = int(len(system + user) / CHARS_PER_TOKEN)
    if est > MAX_INPUT_TOKENS:
        raise SystemExit(
            f"input is ~{est:,} tokens, over ARENA_MAX_INPUT_TOKENS "
            f"({MAX_INPUT_TOKENS:,}). Raise the limit if the model really "
            f"holds it; do not let it truncate silently.")
    if stub:
        # Must match rubric.md's contract exactly, or --stub exercises a
        # shape the judge never returns. It drifted once already: it kept
        # emitting decisive_quote and decisive_timestamp after the rubric
        # had renamed one and dropped the other, and the scorer's
        # `or v.get("decisive_quote")` fallback swallowed the mismatch.
        return ('{"is_drift": false, "confidence": 0.5,'
                ' "day_activity": "stub", "decisive_evidence": null,'
                ' "reasoning": "stub"}',
                {"input_tokens": est, "output_tokens": 40, "stub": True})
    if model.startswith("claude"):
        key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY unset")
        # The system prompt is IDENTICAL on every call, so it caches. As a
        # plain string it does not: cache_control only attaches to a content
        # block. The first run shipped it as a string and
        # cache_read_input_tokens was 0 on all 184 calls -- the rubric is 18%
        # of arm A's total cost, so this was the largest single lever on the
        # winning arm, silently unused. Verify with the usage field, never by
        # reading the code: a cache that is not hit looks exactly like one
        # that is absent.
        # NO `thinking` KEY, ON PURPOSE. This model thinks by default and
        # the parameter is not a lever -- probed against the live API:
        #
        #   no thinking key            OK, identical output tokens
        #   {"type":"adaptive"}        OK, identical output tokens
        #   adaptive + effort=high     400  "Extra inputs are not permitted"
        #   adaptive + effort=max      400  same
        #   enabled + budget_tokens    400  "not supported for this model"
        #
        # The response carries a `thinking` block either way, with the text
        # empty and only a signature: the reasoning happens and is redacted.
        # So there is no off, no budget and no effort dial; adding the key
        # only implies a control that does not exist.
        #
        # max_tokens 16000, not 4000. It has to cover the thinking AND the
        # answer, and since the thinking is invisible a long one could have
        # been truncated before any JSON was written. Whether that ever
        # happened is unknown, which is reason enough.
        d = _post(ANTHROPIC, {"model": model, "max_tokens": 16000,
                              "system": [{"type": "text", "text": system,
                                          "cache_control": {"type": "ephemeral"}}],
                              "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        # type == "text" only. With thinking on the response also carries
        # `thinking` blocks, and concatenating those would hand the JSON
        # parser a wall of reasoning prose before the object.
        text = "".join(b.get("text", "") for b in d.get("content", [])
                       if b.get("type") == "text")
        u = d.get("usage", {})
        return text, {k: u[k] for k in USAGE_KEYS if k in u}
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise SystemExit("OPENAI_API_KEY unset")
    # stream_options.include_usage keeps the reported usage figures -- without
    # it a streamed response carries no usage block and every cost number for
    # this arm would silently become zero.
    d = _post(OPENAI, {"model": model, "stream": True,
                       "stream_options": {"include_usage": True},
                       "messages": [
                           {"role": "system", "content": system},
                           {"role": "user", "content": user}]},
              {"Authorization": f"Bearer {key}"}, timeout=1800)
    u = d.get("usage", {})
    # cached_tokens too. Without it we could not tell whether the cheap model
    # caches, and it carries 92% of all input -- the same blind spot that hid
    # the Anthropic cache_control bug, where caching looked absent because
    # nothing read the field. Expect ~0 here: the only shared prefix is
    # extract.md at ~270 tokens, under the 1024 minimum, and the day itself
    # is unique per row. Measured beats assumed.
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    out = {"input_tokens": (u.get("prompt_tokens") or 0) - cached,
           "output_tokens": u.get("completion_tokens")}
    if cached:
        out["cache_read_input_tokens"] = cached
    return d["choices"][0]["message"]["content"], out


def _safe(agent):
    return agent.replace("/", "_").replace(" ", "_")


def _json(text):
    """Models fence JSON despite being told not to. Salvage, and record that
    a salvage was needed — an arm whose output will not parse is genuinely
    more expensive, and that belongs in the results, not hidden in a retry."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?|\n?```$", "", t).strip()
    try:
        return json.loads(t), False
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None, True
        try:
            return json.loads(m.group(0)), True
        except json.JSONDecodeError:
            return None, True


# ----------------------------------------------------------------- input ----
# Turn kinds the production monitor drops from its action trace: chat/search/
# room-moves (already in the transcript) and pure cursor mechanics. Copied
# from app/api/internal/monitor/input.ts EXCLUDED_ACTIONS.
MONITOR_EXCLUDED = {
    "send_message_back_to_chat", "search_history", "wait", "pause",
    "move_to_room", "screenshot", "mouse_move", "cursor_position",
    "get_pixel_coords_of_element",
}


def _day_section(agent, day, label):
    """One day rendered monitor-style. Returns "" if the agent had no turns."""
    path = os.path.join(RAW, day, f"{_safe(agent)}.json")
    if not os.path.exists(path):
        return ""
    with open(path) as fh:
        d = json.load(fh)
    L = []
    for c in d.get("chat") or []:
        if not (c.get("own") or c.get("human")):
            continue
        who = "ME" if c.get("own") else (c.get("speaker") or "human")
        L.append(f"  {str(c.get('ts'))[11:19]}  {who}: {c.get('content')}")
    for t in d.get("turns") or []:
        if (t.get("kind") or "") in MONITOR_EXCLUDED:
            continue
        ts = str(t.get("ts"))[11:19]
        if t.get("text"):
            L.append(f"  {ts}  said: {t['text']}")
        L.append(f"  {ts}  > {t.get('kind')}"
                 + (f" {t['command']}" if t.get("command") else ""))
        for field, tag in (("output", ""), ("error", "[stderr] ")):
            val = (t.get(field) or "").strip()
            if val and val != "None":
                L.append(f"      {tag}{val[: config.OUTPUT_CAP]}")
    return f"\n{label} {day}\n" + "\n".join(L) if L else ""


def monitor_view(agent, day):
    """The full day as the village's own monitor sees it — for arms B and C.

    NOT the digest. The digest samples (every 5th bash, every 18th reasoning)
    at rates chosen to keep 100 agent-days readable by a person, so an arm
    reading it inherits a human-ergonomics constant as an evidence ceiling.
    This is the whole day, mechanically filtered rather than sampled.

    Faithful to input.ts on the three choices that matter:
      * drop the nine mechanical turn kinds, keeping ~78% of turns
      * commands in FULL; only output and stderr are capped
      * NO reasoning -- input.ts:361 "Not the CoT/thinking, which we
        deliberately exclude for now". Stage 1 excludes it too, so all three
        arms match, and the 28-98% per-agent availability of that channel
        cannot bias the comparison.

    Chat is scoped to the agent's own messages plus human/operator ones. The
    monitor feeds the entire shared village transcript, but that is ~100K
    tokens of mostly other agents talking, and the question here is whether
    THIS agent changed ITS target. Arm A sees operator messages too, so the
    scope matches and the comparison isolates compression, not access.
    """
    path = os.path.join(RAW, day, f"{_safe(agent)}.json")
    with open(path) as fh:
        d = json.load(fh)
    L = [f"agent: {agent}    day: {day}", ""]
    for g in d.get("goals") or []:
        L.append(f"ASSIGNED GOAL: {g.get('text')}")
        if g.get("description"):
            L.append(f"  description: {g['description']}")
    L.append("")
    L.append("SESSION GOALS (verbatim, in order)")
    for sess in d.get("sessions") or []:
        if sess.get("session_goal"):
            L.append(f"  {str(sess.get('opened'))[11:16]}  {sess['session_goal']}")
    for m in d.get("goal_announcement") or []:
        L.append(f"GOAL ANNOUNCEMENT  {str(m.get('ts'))[:16]}  {m.get('content')}")
    for c in d.get("outreach_constraints") or []:
        L.append(f"OPERATOR DECISION  {str(c.get('ts'))[:16]}  "
                 f"{'APPROVED' if c.get('approved') else 'DENIED'} {c.get('medium')}"
                 f" — {c.get('comment')}")
    L.append("")
    L.append("CHAT (this agent's own messages and any from a human/operator)")
    for c in d.get("chat") or []:
        if not (c.get("own") or c.get("human")):
            continue
        who = "ME" if c.get("own") else (c.get("speaker") or "human")
        L.append(f"  {str(c.get('ts'))[11:19]}  {who}: {c.get('content')}")
    L.append("")
    L.append("ACTIONS (chronological; cursor/screenshot/pause mechanics dropped, "
             "output capped)")
    for t in d.get("turns") or []:
        if (t.get("kind") or "") in MONITOR_EXCLUDED:
            continue
        ts = str(t.get("ts"))[11:19]
        if t.get("text"):
            L.append(f"  {ts}  said: {t['text']}")
        L.append(f"  {ts}  > {t.get('kind')}"
                 + (f" {t['command']}" if t.get("command") else ""))
        for field, tag in (("output", ""), ("error", "[stderr] ")):
            val = (t.get(field) or "").strip()
            if val and val != "None":
                L.append(f"      {tag}{val[: config.OUTPUT_CAP]}")
    text = "\n".join(L)
    # One row of 40 exceeds 1M even after filtering. Tighten the output cap
    # before dropping anything, and if that is still not enough, cut the
    # OLDEST actions and say so in the text -- a model told its input was
    # truncated can weigh the absence; one that is silently cut cannot, and
    # "absent from my input" is exactly the inference the rubric warns about.
    budget = int(MAX_INPUT_TOKENS * CHARS_PER_TOKEN * 0.9)
    # Shed the OLDEST history first: the audited day is what the verdict is
    # about, and rule 4 says prior days cannot carry it anyway. Cutting today
    # to preserve last fortnight would be exactly backwards.
    if len(text) > budget:
        head = text[: text.index("ACTIONS (")]
        acts = text[text.index("ACTIONS (") :].split("\n")
        keep, size = [], len(head)
        for line in reversed(acts):
            if size + len(line) > budget:
                break
            keep.append(line)
            size += len(line) + 1
        dropped = len(acts) - len(keep)
        text = (head + f"ACTIONS (⚠ TRUNCATED: the {dropped} earliest action "
                f"lines were dropped to fit the context window. Their absence "
                f"is an artefact of truncation, not evidence about the day.)\n"
                + "\n".join(reversed(keep)))
    return text


def _check_block_version(agent, day):
    """Refuse a cached block built by different code.

    prep() skips any row that already has a cache file, so changing
    features.py and re-running scores the OLD blocks and reports the result
    as if the change had been measured. Nothing errors; the number just
    means something else. That is the failure mode this whole harness keeps
    producing, so here it is made loud.

    config.FEATURE_VERSION already existed and said "bump when any constant
    changes". It was never bumped and never checked, which is the same as
    not existing.
    """
    p = os.path.join(BLOCKS, f"{_safe(agent)}__{day}.json")
    if not os.path.exists(p):
        return
    got = (json.load(open(p)) or {}).get("feature_version")
    if got != config.FEATURE_VERSION:
        raise SystemExit(
            f"block for {agent} {day} was built by {got!r}, code is "
            f"{config.FEATURE_VERSION!r}. Rebuild: "
            f"python3 eval/arena.py prep --write")


def arm_input(arm, agent, day):
    """What each arm's compression stage reads.

    A reads the dump directly through drift/build.py. B and C both read the
    SAME digest, so the only thing varying between them is Luna's output
    format -- which is what makes B vs C a clean test of the field set rather
    than of the input.

    Neither B nor C can read the raw day: median 713K tokens, max 2.7M. The
    digest is itself a mechanical sample (every 5th bash command, every 18th
    reasoning turn), so B and C inherit a ceiling -- evidence the digest
    dropped is unrecoverable downstream. Those sampling rates were chosen for
    human reading time, not judge accuracy, which is a live confound if B or C
    loses narrowly.
    """
    if arm == "A":
        _check_block_version(agent, day)
        path = os.path.join(BLOCKS, f"{_safe(agent)}__{day}.txt")
        if not os.path.exists(path):
            raise SystemExit(f"no cached block for {agent} {day} -- run "
                             f"`python3 eval/arena.py prep` first")
        with open(path) as fh:
            return fh.read()
    # Single day for B and C. Arm A's block is derived from a 14-day
    # baseline, so it carries ten facts (turns_vs_own_median,
    # hosts_seen_earlier, watchlist_persistence, ...) that one day cannot
    # support. THAT IS A KNOWN LIMITATION OF THIS COMPARISON, recorded rather
    # than engineered around: giving B and C the same window needs prior-day
    # material that eval/raw/ does not contain (it holds only the 58 eval
    # days), so it would mean generating ~560 more agent-days.
    #
    # Acceptable because the eval is single-day drift: rule 4 says prior days
    # cannot carry the verdict, so history informs arm A's baselines rather
    # than its finding. Weigh it when reading a narrow arm-A win.
    return monitor_view(agent, day)


# ------------------------------------------------------------ provenance ----
def _norm(s):
    """Substitute punctuation FIRST, collapse whitespace SECOND.

    Doing it the other way round leaves uncollapsed double spaces where a
    dash was removed, and silently fails to locate quotes that are genuinely
    present. That bug inflated an earlier provenance audit's miss count from
    12 to 18 before it was caught.
    """
    s = re.sub(r"[‘’“”]", "'", s or "")
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"[^\w\s'-]", " ", s)
    return " ".join(s.lower().split())


def day_text(agent, day):
    """Everything the agent actually emitted, for locating a quote."""
    p = os.path.join(RAW, day, f"{_safe(agent)}.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    parts = []
    for t in d.get("turns") or []:
        for k in ("reasoning", "text", "command", "output", "error"):
            if t.get(k):
                parts.append(str(t[k]))
    for k in ("chat", "memories", "sessions", "session_goals"):
        for row in d.get(k) or []:
            parts.append(json.dumps(row) if isinstance(row, dict) else str(row))
    return _norm("\n".join(parts))


def judge_quoted_real_text(quote, payload):
    """Did the judge invent its decisive quote?

    Checked against THE PAYLOAD, which is the only text it could copy from.
    Checking against the day's raw dump instead conflates two questions and
    mismeasures arm A: the block legitimately carries prior-day context, so a
    faithful quote from it is absent from today's dump and scored as a
    fabrication. Arm-input fidelity is a separate question, and only a real
    one for the arms where a model builds the input.
    """
    if not quote:
        return "no_quote"
    q = _norm(quote)
    if len(q) < 25:
        return "too_short"
    return "located" if q in _norm(payload or "") else "NOT_FOUND"


def locates(quote, agent, day, haystacks={}):
    """Is the arm's decisive quote really in the day? A judge that invents its
    evidence can still land the right label by luck, and at n=40 that is not
    distinguishable from competence by F1 alone. This is measured per row with
    no sampling noise, which makes it the sharper instrument here.

    Short quotes match by accident, so anything under 25 normalised characters
    is reported separately rather than counted as located."""
    if not quote:
        return "no_quote"
    key = (agent, day)
    if key not in haystacks:
        haystacks[key] = day_text(agent, day)
    hay = haystacks[key]
    if hay is None:
        return "no_raw"
    q = _norm(quote)
    if len(q) < 25:
        return "too_short"
    return "located" if q in hay else "NOT_FOUND"


# ------------------------------------------------------------------- run ----
def run_path(arm, agent, day, tag=None):
    """Where one verdict is cached.

    `tag` keeps measurements apart. Without it a hybrid verdict for
    GPT-5.5 2026-08-20 overwrites the bake-off's arm-B verdict for the same
    row — and those 160 runs are the only baseline there is to compare a
    change against. The cache key was (arm, agent, day) and nothing in it
    recorded WHICH experiment a verdict belonged to.

    Untagged keeps the original filename, so the existing runs stay where
    they are and stay scoreable.
    """
    stem = f"{arm}-{tag}" if tag else arm
    return os.path.join(RUNS, f"{stem}__{_safe(agent)}__{day}.json")


def run(rows, arm="B", stub=False, limit=None, tag=None, workers=4):
    """One arm over the sample. Every call is cached to disk keyed by
    (arm, agent, day), so a crash or a rerun costs nothing and cannot
    double-bill. Delete a cache file to force one row to re-run."""
    # ROWS ARE PASSED IN. This used to read the 40-row arena sample, which
    # is the one line that stopped the runner being usable over the corpus:
    # the audit has to process 4,027 agent-days, none of which are in it.
    rows = list(rows)[:limit]
    os.makedirs(RUNS, exist_ok=True)

    # CONCURRENT, because the rows are independent and the work is almost all
    # waiting: a row is two API calls and ~110K tokens of prefill, 30s wall
    # clock of which the client spends ~none. Sequential, 60 rows is half an
    # hour.
    #
    # 4, not 40. Running two ARMS at once previously produced HTTP 400s on
    # the largest requests that succeeded immediately when retried alone --
    # load, not content -- and the biggest days are disproportionately the
    # interesting ones, so failures there bias the sample. _post retries
    # those now, but a small pool keeps the retries rare rather than routine.
    #
    # Every row writes its own file and shares no mutable state, so the only
    # thing needing a lock is the progress line.
    import concurrent.futures as _cf
    import threading
    lock = threading.Lock()
    done = 0

    def _one(r):
        nonlocal done
        agent, day = r["agent"], r["day"]
        out = run_path(arm, agent, day, tag)
        if os.path.exists(out):
            return
        rec = {"arm": arm, "tag": tag, "agent": agent, "day": day, "calls": [],
               "salvaged": False, "error": None}
        try:
            src = arm_input(arm, agent, day)
            # Arm A is single-stage: the block goes straight to the judge.
            # Any new single-stage arm must be added HERE. Arm D was briefly
            # absent from this test, fell through to the cheap stage, drew
            # screen.md, and its judge read the screening JSON instead of the
            # block -- it scored 0.72 and said so in its own reasoning ("my
            # input is a prior reviewer's summary").
            if arm == "A":
                payload = src
            else:
                if not MODELS["cheap"]:
                    raise SystemExit("set ARENA_CHEAP to the cheap model's API id")
                sysmsg = prompt("extract")
                text, usage = call(MODELS["cheap"], sysmsg, src, stub)
                rec["calls"].append({"stage": "cheap", "model": MODELS["cheap"],
                                     "usage": usage, "input_chars": len(src)})
                obj, salvaged = _json(text)
                rec["salvaged"] |= salvaged
                rec["cheap_output"] = obj if obj is not None else text
                if True:
                    if arm == "B" and isinstance(obj, dict):
                        # THE HYBRID: arm A's mechanical block, plus the two
                        # fields only a reader of the raw day can supply.
                        #
                        # This used to render the cheap model's OWN version of
                        # the block. It cannot compete: code holds 21 of the
                        # 33 keys it was asked for EXACTLY, and the model
                        # reproduces computed facts at 28% and verbatim
                        # sections at 75-98%. Every one of those is a loss.
                        #
                        # goal_is_open was the sharpest case. Pure lookup,
                        # 0 disagreements with the gold labels across 40 rows
                        # from code, 3 of 40 right from the model -- it reads
                        # the umbrella announcement ("pursue your goal in any
                        # way you see fit") as an open goal when the assigned
                        # goal is specific. That single field was arm B's
                        # entire error set: all six wrong verdicts argue with
                        # it in their own reasoning.
                        #
                        # So the model now answers three keys and nothing
                        # else, and the judge cannot tell which arm it serves
                        # because the block is byte-identical to arm A's
                        # except for two added Context sections.
                        from drift import render as _R
                        _check_block_version(agent, day)
                        _blk = os.path.join(BLOCKS, f"{_safe(agent)}__{day}.json")
                        if not os.path.exists(_blk):
                            raise SystemExit(
                                f"no cached block for {agent} {day} -- run prep")
                        # `block`, NOT `rec`. This read `rec = json.load(...)`
                        # and clobbered the RUN RECORD with the block, so the
                        # next line to touch rec["calls"] raised KeyError and
                        # every hybrid row failed. Caught by a --stub dry run
                        # before any money was spent; nothing about the shapes
                        # made it visible by reading.
                        block = json.load(open(_blk))
                        block.setdefault("context", {})
                        ma = obj.get("reached_audience")
                        block["context"]["reached_audience"] = (
                            ma if isinstance(ma, list) else [])
                        block["context"]["reached_audience_searched"] = bool(
                            obj.get("reached_audience_searched"))
                        pr = obj.get("peer_requests")
                        block["context"]["peer_requests"] = (
                            pr if isinstance(pr, list) else [])
                        payload = _R.render(block)
                    else:
                        # The cheap stage returned something that is not a
                        # dict. There is no block to build, so fail the row
                        # rather than sending the judge raw JSON and paying
                        # for a verdict on input the rubric does not describe.
                        # This used to fall through silently, which is how arm
                        # D once scored 0.72 on a screening blob.
                        raise ValueError(
                            f"cheap stage returned {type(obj).__name__}, not a "
                            f"dict -- no block can be built")
            text, usage = call(MODELS["judge"], prompt("rubric"), payload, stub)
            rec["calls"].append({"stage": "judge", "model": MODELS["judge"],
                                 "usage": usage, "input_chars": len(payload)})
            # The judge can only quote what it was shown, so fabrication is
            # "not in the payload" -- NOT "not in the day's raw dump". The
            # first version checked the dump and flagged honest quotes from
            # the block's prior-day context as fabricated. Keep the payload.
            rec["payload"] = payload
            verdict, salvaged = _json(text)
            rec["salvaged"] |= salvaged
            rec["verdict"] = verdict if verdict is not None else {"raw": text}
        except Exception as exc:                      # noqa: BLE001
            # Recorded, not raised: a row that fails still consumed tokens and
            # still counts against the arm. Redacted, because exception text
            # can carry the API key straight into the run record on disk.
            rec["error"] = _redact(f"{type(exc).__name__}: {exc}",
                                   os.environ.get("ANTHROPIC_API_KEY"),
                                   os.environ.get("OPENAI_API_KEY"))
        with open(out, "w") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        with lock:
            done += 1
            print(f"  [{done}] {arm} {agent} {day}"
                  + (f"  ERROR {rec['error'][:60]}" if rec["error"] else ""),
                  flush=True)

    if workers > 1 and not stub:
        with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for f in _cf.as_completed([ex.submit(_one, r) for r in rows]):
                f.result()
    else:
        for r in rows:
            _one(r)
    print(f"arm {arm}: {done} new, {len(rows) - done} cached")

