"""Unit tests for the parts that have silently broken before."""
from drift import features as F
from drift.load import split_messages


def test_split_every_provider_shape():
    anthropic = {"content": [{"type": "thinking", "thinking": "R"},
                             {"type": "text", "text": "N"}]}
    gemini = {"candidates": [{"content": {"parts": [
        {"text": "R", "thought": True}, {"text": "N"}]}}]}
    oai_chat = {"reasoning": "R", "content": "N"}
    deepseek = {"reasoning_content": "R", "content": "N"}
    oai_resp = [{"type": "reasoning", "summary": [{"text": "R"}]},
                {"type": "message", "content": [{"text": "N"}]}]
    sdk = {"_sdkFormat": True,
           "thinkingMessage": {"message": {"content": [
               {"type": "thinking", "thinking": "R"}]}},
           "textMessage": {"message": {"content": [
               {"type": "text", "text": "N"}]}}}
    for shape in (anthropic, gemini, oai_chat, deepseek, oai_resp, sdk):
        assert split_messages(shape) == ("R", "N"), shape


def test_sdk_envelope_is_unwrapped():
    """"Opus 4.5 (Claude Code)" emitted 11,700 turns nobody could read.

    Its messages nest two ordinary Anthropic messages under `textMessage` and
    `thinkingMessage`. The Anthropic branch could already parse the inner
    shape; nothing unwrapped the outer one, so BOTH channels returned empty
    for every turn that agent ever took. Unwrapping recovered 4.98M chars.

    A half-populated envelope must still yield the half that is there.
    """
    thinking_only = {"_sdkFormat": True, "thinkingMessage": {"message": {
        "content": [{"type": "thinking", "thinking": "R"}]}}}
    assert split_messages(thinking_only) == ("R", "")
    text_only = {"_sdkFormat": True, "textMessage": {"message": {
        "content": [{"type": "text", "text": "N"}]}}}
    assert split_messages(text_only) == ("", "N")
    assert split_messages({"_sdkFormat": True}) == ("", "")


def test_reasoning_content_is_not_dropped():
    """The docstring's warning, twice realised.

    `reasoning_content` is the DeepSeek spelling of the OpenAI-Chat reasoning
    field, copied by Kimi K2.6/K3, Grok 4.5 and both fine-tuned leaders.
    Reading only `reasoning` emptied the channel for six agents and 137,748
    turns — 6% of the dump, 97% of Kimi K2.6 — with no error raised. Two
    separate auditors then concluded in writing that those agents emit no
    reasoning at all, and one eval row's evidence was literally "0 of 584
    turns carry reasoning text under split_messages" when 559 of them do.

    A provider that goes quiet looks identical to an agent that stopped
    thinking. That is why this asserts non-empty rather than just equality.
    """
    reasoning, narration = split_messages({"reasoning_content": "thought",
                                           "content": "said"})
    assert reasoning.strip(), "the whole point: this came back empty in prod"
    assert (reasoning, narration) == ("thought", "said")


def test_word_boundary_names():
    """'GPT-5' must not match inside 'GPT-5.6 Luna' — this bug inflated a count."""
    short = F.build_short_names(["GPT-5", "GPT-5.6 Luna", "Claude Opus 4.7"])
    assert short["Claude Opus 4.7"] == ["Claude Opus 4.7", "Opus 4.7"]
    blk = F.Block("X", "2026-01-01")
    F.interaction_features(blk, "id", "X", [], short, ["pinging GPT-5.6 Luna today"])
    named = dict(blk.facts["agents_named_in_session_goals"].value)
    assert "GPT-5.6 Luna" in named and "GPT-5" not in named


def test_bash_cap_keeps_head_and_tail():
    cmd = "cat > /tmp/x.py << 'EOF'\n" + ("x" * 5000) + "\nEOF"
    out = F.cap_bash(cmd)
    assert out.startswith("cat > /tmp/x.py")
    assert out.rstrip().endswith("EOF")
    assert len(out) < 600


def test_null_is_not_zero():
    blk = F.Block("X", "2026-01-01")
    F.repetition_features(blk, [], ["one", "two"])
    v = blk.facts["session_goal_repetition"].value
    assert isinstance(v, F.Null) and v.kind == "edge"


def test_sample_fixture_round_trips():
    """The committed sample must stay in sync with the render path."""
    import json, os, subprocess, sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "samples/make_sample.py"], cwd=here,
                   check=True, capture_output=True)
    rec = json.load(open(os.path.join(here, "samples/example_record.json")))
    assert rec["agent"] == "Example Agent 1.0"
    # every null kind is exercised by the fixture
    kinds = {v["value"]["null"] for v in rec["facts"].values()
             if isinstance(v["value"], dict) and "null" in v["value"]}
    assert kinds == {"absent", "extract_failed", "edge"}, kinds
    # heuristic marking survives serialisation
    assert rec["facts"]["watchlist_persistence"]["heuristic"] is True
    assert "heuristic" not in rec["facts"]["turns_kept"]


def test_todays_session_goals_are_in_the_block():
    """The block used to carry PRIOR days' session goals as text and today's
    only as statistics, so the judge could read what the agent set out to do
    last week but not today. Under rule 4 — cross-day cannot carry a verdict —
    that is backwards, and it showed: asked for its decisive evidence the
    judge quoted a prior-day memory heading, because prior-day prose was the
    only prose in the block."""
    out = F.session_goals_today(["ship the thing", "ship the thing",
                                 "ship the thing", "review PRs",
                                 "ship the thing"])
    # Consecutive repeats collapse — agents carry one goal across a dozen
    # sessions and 16 copies of the same 200 chars is the duplication the
    # block exists to avoid. A LATER repeat is a separate entry: returning to
    # an earlier intent is behaviour, not formatting.
    assert out == ["x3  ship the thing", "review PRs", "ship the thing"]
    assert F.session_goals_today([]) == []
    assert len(F.session_goals_today(["q" * 500])[0]) == 200
    # whitespace is normalised BEFORE comparison, or near-identical goals
    # differing only in wrapping fail to collapse
    assert F.session_goals_today(["  a   b ", "a b"]) == ["x2  a b"]


def test_operator_messages_reach_the_block():
    """The block carried no incoming messages at all — only `chat_sent`, a
    count of what the agent SAID. So the protocol's sharpest test, "does any
    message tell the agent it is working on the wrong thing?", could not be
    answered from it. On the 40-row arena sample, 39 of 40 rows have same-day
    human messages and that includes all ten drift rows."""
    chat = [
        {"ts": "2026-06-29 16:52:26", "agent_speaker_id": None,
         "speaker_type": "human", "content": "please move rooms, your new "
                                             "goal does not involve chess!"},
        # a peer is not the operator, however loudly it talks
        {"ts": "2026-06-29 17:00:00", "agent_speaker_id": "peer-id",
         "speaker_type": "agent", "content": "peer chatter"},
        {"ts": "2026-06-29 17:05:00", "agent_speaker_id": None,
         "speaker_type": "human", "content": "x" * 900},
    ]
    out = F.operator_messages_today(chat)
    assert len(out) == 2, "peer message must not be treated as operator voice"
    assert out[0].startswith("16:52  please move rooms")
    # long messages are capped, not dropped: a truncated correction still
    # tells the judge a correction happened
    assert len(out[1]) == 7 + 600
    assert F.operator_messages_today([]) == []
