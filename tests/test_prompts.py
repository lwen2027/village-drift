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

import arena  # noqa: E402

PROMPTS = ("rubric", "screen", "extract")


def test_every_prompt_file_exists_and_loads():
    for name in PROMPTS:
        assert arena.prompt(name).strip(), name


def test_comments_are_stripped_before_sending():
    """And that the files still HAVE comments, or this tests nothing."""
    documented = 0
    for name in PROMPTS:
        raw = open(os.path.join(ROOT, "eval", f"{name}.md")).read()
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
    path = os.path.join(ROOT, "eval", "rubric.md")
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
