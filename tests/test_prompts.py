"""The prompt files must not leak their own documentation to the model.

Two things went wrong here and both were silent.

`arena.prompt()` reads a whole .md file and sends it. The files carry an HTML
comment block explaining the design decisions behind them — useful to whoever
edits them, and actively harmful to send. screen.md was 31% comment and
opened by telling the model it was "arm C's cheap stage", that a baseline ran
on opus-4-8, and that "its recall is a hard ceiling": announcing the
benchmark and naming the one metric a screening model can game by flagging
every day. On a 10-positive sample that could have manufactured a perfect
screen recall that meant nothing.

The second is the leak guard. AUDIT_PROTOCOL.md illustrates every trap with
the agent-day it came from and names 62 of the 100 eval rows alongside their
labels, so a paste from it hands the judge the answer key. Two earlier
versions of the guard were vacuous — they scrubbed the agent names and THEN
checked for agent names, so they passed an injected leak. This asserts the
guard still fires.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))

PROMPT_DIR = os.path.join(ROOT, "audit")   # prompts moved out of eval/

from audit import run as arena  # noqa: E402

# screen.md went with arm C. rubric.md is the judge, extract.md the
# cheap stage.
PROMPTS = ("rubric", "extract")


def test_every_prompt_file_exists_and_loads():
    for name in PROMPTS:
        assert arena.prompt(name).strip(), name


def test_comments_are_stripped_before_sending():
    """And that the files still HAVE comments, or this tests nothing."""
    documented = 0
    for name in PROMPTS:
        raw = open(os.path.join(PROMPT_DIR, f"{name}.md")).read()
        sent = arena.prompt(name)
        assert "<!--" not in sent and "-->" not in sent, \
            f"{name}.md leaks its comment block to the model"
        documented += "<!--" in raw
    assert documented, \
        "no prompt file carries a comment, so this test is vacuous — it must " \
        "exercise the stripping, not merely observe its absence"


def test_stripping_does_not_eat_the_prompt():
    """A strip that removes everything also passes the check above."""
    for name in PROMPTS:
        sent = arena.prompt(name)
        assert len(sent) > 600, f"{name}.md sent only {len(sent)} chars"
        assert "JSON" in sent, f"{name}.md lost its output contract"


def test_leak_guard_fires_on_an_injected_eval_row():
    """The guard that two earlier versions got wrong. If the eval table is
    absent (it is gitignored) there is nothing to leak, so skip."""
    labels = os.path.join(ROOT, "eval", "tables", "stage1", "eval_100.jsonl")
    if not os.path.exists(labels):
        return
    import json
    row = json.loads(open(labels).readline())
    path = os.path.join(PROMPT_DIR, "rubric.md")
    original = open(path).read()
    try:
        with open(path, "a") as fh:
            fh.write(f"\n\nWorked example: {row['agent']} on {row['day']} "
                     f"was drift.\n")
        try:
            arena.prompt("rubric")
            raise AssertionError(
                "leak guard did not fire on an eval agent-day in the prompt")
        except SystemExit:
            pass
    finally:
        with open(path, "w") as fh:
            fh.write(original)
    # and the restore worked
    assert arena.prompt("rubric").strip()


def test_arm_b_caps_are_enforced_not_requested():
    """extract.md asked the cheap model to truncate session goals; it didn't.

    Told "truncate to 200 chars", it obeyed on 49 of 785 entries (6%),
    exceeded the cap on 442 (56%), and ran to 3,484 chars — a mean of 314
    against arm A's 200. So arm A and arm B were compared at different
    budgets on session_goals_today, which supplies 78% of BOTH arms'
    decisive quotes, and the tie between them carried that confound.

    A cap written in a prompt is a request. This asserts the code applies
    one, so the arms are matched on the field that decides the verdict.
    """
    from drift import config, features as F
    # session goals: no per-goal cap, a whole-day budget instead
    assert F.session_goals_today(["g" * 5000]) == ["g" * 5000]
    over = ["q" * 30000, "r" * 30000, "s" * 30000]
    assert any("omitted" in x for x in F.session_goals_today(over)), \
        "the day budget must bite once the day is large enough"
    # operator messages keep their per-message cap
    assert len(("m" * 5000)[: config.OPERATOR_MSG_CHARS]) == config.OPERATOR_MSG_CHARS
    assert "TODAY_GOAL_CHARS" not in open(
        os.path.join(PROMPT_DIR, "extract.md")).read()
