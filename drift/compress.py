"""Deterministic text compression shared by every pipeline surface.

These helpers select and clip text without deciding what it means.  Keeping
them below the stage-specific renderers prevents the human digest, Stage 1,
and Stage 2 from quietly acquiring different sampling algorithms.
"""

from __future__ import annotations

from collections.abc import Callable


def evenly_spaced_sample(items: list, limit: int) -> list:
    """Return an evenly spaced, reproducible sample starting at item zero."""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[min(len(items) - 1, int(i * step))]
            for i in range(limit)]


def clip_words(text, limit: int) -> str:
    """Collapse whitespace, then retain a disclosed prefix of at most limit."""
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit] + f" …[+{len(value) - limit}c]"


def clip_head_tail(text, head: int, tail: int,
                   marker: Callable[[int], str]) -> str:
    """Keep both ends of text, disclosing the size of the omitted middle."""
    value = str(text or "")
    if len(value) <= head + tail:
        return value
    dropped = len(value) - head - tail
    return value[:head] + marker(dropped) + value[-tail:]
