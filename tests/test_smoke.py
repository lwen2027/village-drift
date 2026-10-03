"""Unit tests for the parts that have silently broken before."""
from village_drift.shared import config
from village_drift.stage1 import features as F
from village_drift.shared.load import split_messages


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
    subprocess.run([sys.executable, "examples/make_sample.py"], cwd=here,
                   check=True, capture_output=True)
    rec = json.load(open(os.path.join(here, "examples/example_record.json")))
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
    assert F.session_goals_today(["q" * 900]) == ["q" * 900]


def test_todays_session_goals_are_never_truncated_per_goal():
    """They were, at 200 and then 400, and both were wrong.

    Intent IS front-loaded -- ten long goals across ten agents all name their
    target inside the first ~130 chars. The wrong conclusion was that the
    rest is padding. Past the intent line these carry "DONE this session
    (don't redo): (1)...", the agent's own record of what it spent the day
    ON, which for "did it pursue the assigned target" is the evidence.

    The history strip still caps, and must: it is cross-day, and rule 4
    forbids it carrying a verdict.
    """
    long = "z" * 5000
    assert F.session_goals_today([long]) == [long], "today's goals go whole"
    assert len(F.history_strip([("2026-01-01", long)])[0]) \
        == 12 + config.HISTORY_GOAL_CHARS, "cross-day still capped"


def test_day_budget_drops_whole_goals_from_the_middle():
    """One agent writes 310-387K chars of session goals a day; the corpus
    median is 7,008. Bound the DAY so that agent cannot bury the block,
    without truncating the 93% of days that are fine.

    Middle-out: the opening intent and where the day ended are the two
    informative ends. And the elision must be VISIBLE -- a judge told text is
    missing can weigh the absence; one silently cut cannot.
    """
    big = ["A" * 9000] + [f"{c}" * 9000 for c in "BCDEFGHIJ"] + ["Z" * 9000]
    out = F.session_goals_today(big)
    joined = " ".join(out)
    assert out[0].startswith("A"), "opening intent kept"
    assert out[-1].startswith("Z"), "where the day ended kept"
    assert "omitted" in joined and "artefact of truncation" in joined
    assert all(len(x) in (9000,) or "omitted" in x for x in out), \
        "goals are dropped entire, never cut mid-sentence"
    # a normal day is returned untouched
    small = ["plan the thing", "ship the thing"]
    assert F.session_goals_today(small) == small
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


def test_goal_period_messages_fills_the_operator_blind_spot():
    """Three operator channels, and only two of them existed.

        goal_announcement        the goal's START day
        goal_period_messages     every day in between      <- was missing
        operator_messages_today  TODAY

    An operator who changed the rules on day 3 of a five-week goal was
    invisible to every block after it. 37 of 40 arena rows had operator
    traffic in that window, 18,758 messages in total, and one of them decided
    an eval row: a 2026-05-19 message capping output at one video a day and
    explicitly permitting zero. The day audited three days later published
    none, which reads as a stall without that message and as compliance with
    it.

    Mechanical by necessity. It lives outside the audited day, so no
    day-scoped pass — including the cheap model's — can reach it.
    """
    goals = [{"text": "Run your own Youtube channel!", "start": "2026-05-18 16:00:00"}]
    short = F.build_short_names(["Agent One", "Agent Two"])
    ann = {
        # start day — belongs to goal_announcement, not here
        "2026-05-18": [{"ts": "2026-05-18 17:00:00", "content": "@Agent One your goal is X"}],
        "2026-05-19": [
            {"ts": "2026-05-19 10:00:00",
             "content": "@Agent One you may post zero videos on any given day"},
            # addressed to someone else
            {"ts": "2026-05-19 11:00:00", "content": "@Agent Two do the other thing"},
            # auto-nudge: templated, repeats, says nothing about the assignment
            {"ts": "2026-05-19 12:00:00",
             "content": "@Agent One — based on your recent activity, it looks like "
                        "you're repeatedly idling"},
            # session bookend
            {"ts": "2026-05-19 13:00:00", "content": "resume the village for today"},
            # addressed to nobody: village-wide, so it binds
            {"ts": "2026-05-19 14:00:00", "content": "everyone: one video per day max"},
        ],
        # today — belongs to operator_messages_today, not here
        "2026-05-22": [{"ts": "2026-05-22 09:00:00", "content": "@Agent One hello"}],
    }
    out = F.goal_period_messages(goals, ann, "Agent One", short, "2026-05-22")
    joined = " ".join(out)
    assert "zero videos" in joined, "the amendment that decides the day"
    assert "one video per day max" in joined, "un-addressed village-wide still binds"
    assert "Agent Two" not in joined, "addressed to someone else"
    assert "repeatedly idling" not in joined, "auto-nudge"
    assert "resume the village" not in joined, "session bookend"
    assert "your goal is X" not in joined, "start day is goal_announcement's job"
    assert "hello" not in joined, "today is operator_messages_today's job"


def test_goal_period_messages_word_boundary_and_cap():
    """"@GPT-5" is a substring of "@GPT-5.6 Luna". Matching loosely took one
    agent from 7 messages to 93 — the same bug already fixed once for session
    goals. And the cap drops the OLDEST, because a later amendment supersedes
    an earlier one."""
    short = F.build_short_names(["GPT-5", "GPT-5.6 Luna"])
    goals = [{"text": "g", "start": "2026-01-01 00:00:00"}]
    ann = {"2026-01-02": [{"ts": "2026-01-02 10:00:00",
                           "content": "@GPT-5.6 Luna a message for Luna only"}]}
    assert F.goal_period_messages(goals, ann, "GPT-5", short, "2026-01-10") == []

    # distinct content per day, or the content-dedup collapses them first —
    # which it should: the operator posts the same text into several rooms.
    big = {"2026-01-0%d" % d: [{"ts": "2026-01-0%d 10:00:00" % d,
                                "content": f"@GPT-5 day{d} " + (chr(96 + d) * 3000)}]
           for d in (2, 3, 4)}
    out = F.goal_period_messages(goals, big, "GPT-5", short, "2026-01-10")
    assert any("dropped to fit" in x for x in out), "elision must be visible"
    assert "2026-01-04" in " ".join(out), "newest kept"
    assert "2026-01-02" not in " ".join(out), "oldest dropped"
