"""Unit tests for the parts that have silently broken before."""
from drift import features as F
from drift.load import split_messages


def test_split_all_four_provider_shapes():
    anthropic = {"content": [{"type": "thinking", "thinking": "R"},
                             {"type": "text", "text": "N"}]}
    gemini = {"candidates": [{"content": {"parts": [
        {"text": "R", "thought": True}, {"text": "N"}]}}]}
    oai_chat = {"reasoning": "R", "content": "N"}
    oai_resp = [{"type": "reasoning", "summary": [{"text": "R"}]},
                {"type": "message", "content": [{"text": "N"}]}]
    for shape in (anthropic, gemini, oai_chat, oai_resp):
        assert split_messages(shape) == ("R", "N"), shape


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
