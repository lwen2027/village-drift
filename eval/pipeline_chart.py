"""The methods figure: how an agent-day becomes an explained episode.

    python3 eval/pipeline_chart.py
    swift eval/svg2png.swift eval/pipeline.svg eval/pipeline.png 2000

A PAPER FIGURE, NOT A POSTER. It carries the flow, the units and the
parameters that define the method -- nothing else. No results, no rationale,
no caveats: those belong in the text, where they can be qualified and dated.
An earlier version carried three paragraphs of findings and a status note,
and went stale twice in one day while the prose around it stayed correct.

Rules, so later edits do not creep:
  * every box is a STEP or an ARTIFACT, never a comment
  * parameters appear only where they define behaviour (0.74, 250K)
  * no measured OUTCOME appears -- AUC and precision are results, not method
  * gates annotate the flow; they do not compete with it for boxes

A GATE IS A CHECK BEFORE A CALL THAT CAN STOP THE CALL. Do not label them
as "refusing to spend": two of the three do, but the cache gate forces a
re-run as often as it saves one, and a label that is true of most of a
group reads as true of all of it.

The long-form version, with every measurement and what was tried and
rejected, is eval/docs/PIPELINE.md.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pipeline.svg")

W, H = 940, 700
BG, INK, MUTE, FAINT = "#ffffff", "#111111", "#5f5f5c", "#9a9a95"
LINE, PANEL = "#cfcfca", "#f7f7f5"
S1, S2, GATE = "#9c2b2b", "#1a6b4e", "#8a6a1a"

# Parameters that DEFINE the method. Anything measured ABOUT it stays out.
CUT = "0.74"              # selection threshold
BUDGET = "250K"           # payload ceiling, in tokens
CORPUS = "4,091"          # agent-days in the dump


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, fill="none", stroke=LINE, rx=6, sw=1.1):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def txt(x, y, s, size=13, fill=INK, weight="400", mono=False):
    fam = ("ui-monospace,SFMono-Regular,Menlo,monospace" if mono
           else "Helvetica,Arial,sans-serif")
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" font-family="{fam}">{esc(s)}</text>')


def down(x, y1, y2, colour=MUTE):
    """Vertical connector with an explicit arrowhead -- the PNG renderer
    silently drops SVG markers, which once shipped a flow diagram with no
    direction at all."""
    return (f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2 - 7}" '
            f'stroke="{colour}" stroke-width="1.3"/>'
            f'<path d="M {x} {y2} L {x - 4} {y2 - 7} L {x + 4} {y2 - 7} z" '
            f'fill="{colour}"/>')


def svg():
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica,Arial,sans-serif" font-size="13">',
         f'<rect width="{W}" height="{H}" fill="{BG}"/>']

    bw, bx = 420, 56
    cx = bx + bw // 2

    s.append(txt(bx, 36, "Two-stage goal-drift detection", 17, INK, "600"))

    y = 58
    s.append(txt(bx, y + 14, f"{CORPUS} agent-days", 13, MUTE))
    s.append(txt(bx, y + 31, "computer use · chat · memory", 11, FAINT))
    s.append(down(cx, y + 40, y + 68))

    # ---------------------------------------------------------- stage 1 ----
    y = 130
    s.append(box(bx, y, bw, 74, PANEL, S1, 6, 1.3))
    s.append(txt(bx + 16, y + 22, "STAGE 1", 10, S1, "700"))
    s.append(txt(bx + 80, y + 22, "unit: the agent-day", 10, MUTE))
    s.append(txt(bx + 16, y + 45, "Was this day spent on the assigned goal?",
                 13, INK))
    s.append(txt(bx + 16, y + 64, "yes · no · can't tell — plus a confidence score",
                 11, MUTE))
    s.append(down(cx, y + 74, y + 104))

    # -------------------------------------------------------- selection ----
    y = 234
    s.append(box(bx, y, bw, 56))
    s.append(txt(bx + 16, y + 21, "SELECTION", 10, INK, "700"))
    s.append(txt(bx + 16, y + 42,
                 f"a 'yes', or a 'no' the judge was unsure of "
                 f"(confidence < {CUT})", 13, INK))
    s.append(down(cx, y + 56, y + 86))

    # ----------------------------------------------------------- window ----
    y = 320
    s.append(box(bx, y, bw, 56))
    s.append(txt(bx + 16, y + 21, "WINDOW", 10, INK, "700"))
    s.append(txt(bx + 16, y + 42,
                 f"the days before it, as far back as {BUDGET} tokens allow",
                 13, INK))
    s.append(down(cx, y + 56, y + 86))

    # ---------------------------------------------------------- stage 2 ----
    y = 406
    s.append(box(bx, y, bw, 146, PANEL, S2, 6, 1.3))
    s.append(txt(bx + 16, y + 22, "STAGE 2", 10, S2, "700"))
    s.append(txt(bx + 80, y + 22, "unit: the episode", 10, MUTE))
    steps = [("1", "explain", "split the window into separate activities", ""),
             ("2", "walk", "scan further back for the real start",
              "only if it began before the window starts"),
             ("3", "revise", "judge again, with the earlier start",
              "only if the scan found one")]
    for i, (n, name, what, cond) in enumerate(steps):
        yy = y + 50 + i * 33
        s.append(txt(bx + 18, yy, n, 11, S2, "700"))
        s.append(txt(bx + 34, yy, name, 12, INK, "600"))
        s.append(txt(bx + 96, yy, what, 12, MUTE))
        if cond:
            s.append(txt(bx + 34, yy + 14, cond, 10, FAINT))
    s.append(down(cx, y + 146, y + 176))

    # ---------------------------------------------------------- episodes ---
    y = 582
    s.append(box(bx, y, bw, 56, PANEL, S2, 6, 1.3))
    s.append(txt(bx + 16, y + 21, "EPISODES", 10, S2, "700"))
    s.append(txt(bx + 16, y + 42,
                 "the activity · when it drifted · why · what else it could do",
                 11, INK))

    # ----------------------------------- gates, annotating the spine --------
    gx = bx + bw + 54
    s.append(txt(gx, 152, "GATES", 10, GATE, "700"))
    s.append(txt(gx, 168, "asked before each step; any can stop it", 10, FAINT))
    for yy, head, l1, l2 in (
            (234, "Is Stage 1 finished?",
             "list anything missing", "before starting Stage 2"),
            (320, "Answered already?",
             "reuse an old result only", "if nothing has changed"),
            (470, "Can step 3 work?",
             "skip it if the evidence", "it needs is missing")):
        s.append(f'<line x1="{bx + bw + 10}" y1="{yy + 24}" x2="{gx - 12}" '
                 f'y2="{yy + 24}" stroke="{GATE}" stroke-width="1" '
                 f'stroke-dasharray="3 3"/>')
        s.append(txt(gx, yy + 20, head, 11, GATE, "600"))
        s.append(txt(gx, yy + 36, l1, 10, MUTE))
        s.append(txt(gx, yy + 50, l2, 10, MUTE))

    s.append(txt(bx, H - 20,
                 "Stage 1 reads every day and is tuned to miss nothing. "
                 "Stage 2 reads whole activities and decides what is real.",
                 11, MUTE))
    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    open(OUT, "w").write(svg())
    print(f"wrote {OUT}")
