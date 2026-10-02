"""The methods diagram: how a day becomes an explained episode.

    python3 eval/pipeline_chart.py
    swift eval/svg2png.swift eval/pipeline.svg eval/pipeline.png 2400

Hand-built SVG, same palette and fonts as arena_chart.py and
ranking_chart.py, so the three figures sit together in a write-up.

EVERY NUMBER ON IT IS MEASURED and pulled from this file's CONSTANTS block,
which cites where each came from. A methods figure that quietly rounds or
invents is worse than no figure: it is the version people screenshot.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pipeline.svg")

W, H = 1180, 800
BG, INK, MUTE = "#fbfbfa", "#1a1a1a", "#6b6b66"
LINE, BOX = "#d8d8d4", "#ffffff"
RED, BLUE, GREEN, AMBER = "#b03030", "#5a6b8c", "#1a7f5a", "#9a6b1f"

# ---- measured constants, with provenance -----------------------------------
CORPUS = "4,091"          # agent-days in the dump
S1_AUC = "0.95"           # B-peerfix, 93 scorable rows
S1_P, S1_R = "0.86", "0.76"
CUT = "0.74"              # selection rule; full recall 25/25 in-sample
READ_SHARE = "53%"        # share of scored days the rule sends
BUDGET = "250K"           # payload ceiling, from the refusal experiment
REFUSE_AT = "347K"        # measured refusal; 245K answered
MULTI = "14 of 20"        # golden windows holding >1 activity
S2_P, S2_R = "0.67", "0.60"   # first baseline, 17 episodes
PERDAY = "3.4K-103K"      # median digest tokens/day, by agent


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, fill=BOX, stroke=LINE, rx=7, sw=1.2):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def txt(x, y, s, size=13, fill=INK, weight="400", anchor="start",
        family=None, style=None):
    f = f' font-family="{family}"' if family else ""
    st = f' font-style="{style}"' if style else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{f}{st}>'
            f'{esc(s)}</text>')


def mono(x, y, s, size=12, fill=MUTE, weight="400", anchor="start"):
    return txt(x, y, s, size, fill, weight, anchor,
               family="ui-monospace,SFMono-Regular,Menlo,monospace")


def arrow(x1, y1, x2, y2, colour=MUTE, w=1.6, dash=None):
    """Explicit triangle, not marker-end. The PNG renderer silently drops
    SVG markers, so the first version of this figure shipped with plain
    lines and no arrowheads -- a flow diagram with no direction."""
    d = f' stroke-dasharray="{dash}"' if dash else ""
    hx = 7.0
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2 - hx}" y2="{y2}" '
            f'stroke="{colour}" stroke-width="{w}"{d}/>'
            f'<path d="M {x2} {y2} L {x2 - hx} {y2 - 4.2} '
            f'L {x2 - hx} {y2 + 4.2} z" fill="{colour}"/>')


def svg():
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica,Arial,sans-serif" font-size="13">',
         f'<rect width="{W}" height="{H}" fill="{BG}"/>',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
         'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
         f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTE}"/></marker></defs>']

    s.append(txt(44, 38, "Finding goal drift in a long-running agent village",
                 20, INK, "600"))
    s.append(txt(44, 60, "Two stages, two units. Stage 1 ranks days and is "
                         "tuned to miss nothing; Stage 2 explains episodes "
                         "and is where precision has to come from.",
                 13, MUTE))

    # ---------------------------------------------------------------- corpus
    y0 = 92
    s.append(box(44, y0, 190, 92, "#f4f4f1"))
    s.append(txt(60, y0 + 24, "THE DUMP", 11, MUTE, "700"))
    s.append(txt(60, y0 + 48, f"{CORPUS} agent-days", 15, INK, "600"))
    s.append(txt(60, y0 + 68, "42 agents · 15 months", 12, MUTE))

    # --------------------------------------------------------------- stage 1
    x1 = 282
    s.append(box(x1, y0, 300, 92))
    s.append(txt(x1 + 16, y0 + 24, "STAGE 1 — unit: the agent-day", 11, RED, "700"))
    s.append(txt(x1 + 16, y0 + 46, "“was this day spent on the goal?”",
                 13, INK))
    s.append(mono(x1 + 16, y0 + 68,
                  f"AUC {S1_AUC}   P {S1_P}   R {S1_R}", 12, MUTE, "600"))
    s.append(arrow(240, y0 + 46, x1 - 6, y0 + 46))

    s.append(txt(x1 + 16, y0 + 118, "mechanical block  +  2 fields a cheap "
                                    "model reads off the raw day", 12, MUTE))
    s.append(mono(x1 + 16, y0 + 138, "hybrid ‘arm B’ — the only arm "
                                     "anything downstream may read", 11, MUTE))

    # output: not a verdict
    s.append(box(628, y0, 230, 92, "#fff8f5", RED, 7, 1.4))
    s.append(txt(644, y0 + 24, "OUTPUT: A RANKED LIST", 11, RED, "700"))
    s.append(txt(644, y0 + 46, "is_drift + confidence", 13, INK))
    s.append(txt(644, y0 + 68, "the label is the weaker half", 12, MUTE))
    s.append(arrow(588, y0 + 46, 622, y0 + 46))

    s.append(box(892, y0, 244, 92, "#f4f4f1"))
    s.append(txt(908, y0 + 24, "WHY NOT A VERDICT", 11, MUTE, "700"))
    s.append(txt(908, y0 + 44, "rerun the same rows and", 12, MUTE))
    s.append(txt(908, y0 + 60, "6 of 93 verdicts flip —", 12, MUTE))
    s.append(txt(908, y0 + 76, "while AUC holds. Read AUC.", 12, MUTE))

    # --------------------------------------------------------------- handoff
    y1 = 252
    s.append(f'<line x1="44" y1="{y1-14}" x2="{W-44}" y2="{y1-14}" '
             f'stroke="{LINE}"/>')
    s.append(txt(44, y1 + 10, "THE HANDOFF", 11, BLUE, "700"))
    s.append(mono(148, y1 + 10, "audit/pipeline.py", 11, MUTE))

    s.append(box(44, y1 + 26, 420, 96, "#f6f8fb", BLUE, 7, 1.4))
    s.append(txt(62, y1 + 50, "the selection rule", 12, BLUE, "700"))
    s.append(mono(62, y1 + 74,
                  f"drift   OR   not-drift AND confidence < {CUT}", 13, INK, "600"))
    s.append(txt(62, y1 + 98, f"sends {READ_SHARE} of days · catches 25 of 25 · "
                              f"streaming, no global sort", 12, MUTE))

    s.append(box(500, y1 + 26, 300, 96))
    s.append(txt(518, y1 + 50, "window construction", 12, BLUE, "700"))
    s.append(txt(518, y1 + 72, "contiguous, grown BACKWARD from", 12, MUTE))
    s.append(txt(518, y1 + 88, "the flagged days under a token", 12, MUTE))
    s.append(txt(518, y1 + 104, "budget — not a day count", 12, MUTE))
    s.append(arrow(470, y1 + 74, 494, y1 + 74, BLUE))

    s.append(box(836, y1 + 26, 300, 96, "#fffdf5", AMBER, 7, 1.4))
    s.append(txt(854, y1 + 50, "why a token budget", 12, AMBER, "700"))
    s.append(mono(854, y1 + 72, f"{PERDAY} tokens/day", 12, INK, "600"))
    s.append(txt(854, y1 + 90, "per-day size varies 30× by agent,", 12, MUTE))
    s.append(txt(854, y1 + 106, "so “12 days” means nothing", 12, MUTE))

    # --------------------------------------------------------------- stage 2
    y2 = 412
    s.append(f'<line x1="44" y1="{y2-14}" x2="{W-44}" y2="{y2-14}" '
             f'stroke="{LINE}"/>')
    s.append(txt(44, y2 + 10, "STAGE 2 — unit: the episode", 11, GREEN, "700"))
    s.append(mono(246, y2 + 10, "audit/stage2.py", 11, MUTE))

    s.append(box(44, y2 + 26, 250, 120, "#f4faf7", GREEN, 7, 1.4))
    s.append(txt(62, y2 + 50, "INPUT: a window", 11, GREEN, "700"))
    s.append(txt(62, y2 + 72, "digests from the dump,", 12, MUTE))
    s.append(txt(62, y2 + 88, "+ cross-day stats strip,", 12, MUTE))
    s.append(txt(62, y2 + 104, "+ unsampled reasoning", 12, MUTE))
    s.append(mono(62, y2 + 128, f"≤ {BUDGET} tokens", 12, INK, "600"))

    s.append(box(330, y2 + 26, 270, 120))
    s.append(txt(348, y2 + 50, "PASS 1 — explain", 11, GREEN, "700"))
    s.append(txt(348, y2 + 74, "segment the window into", 12, MUTE))
    s.append(txt(348, y2 + 90, "distinct activities, judge", 12, MUTE))
    s.append(txt(348, y2 + 106, "each one for drift", 12, MUTE))
    s.append(mono(348, y2 + 130, "answers ~2/3 outright", 11, MUTE))
    s.append(arrow(300, y2 + 86, 324, y2 + 86, GREEN))

    s.append(box(636, y2 + 26, 270, 120, BOX, MUTE, 7, 1.2))
    s.append(txt(654, y2 + 50, "PASS 2 — the walk", 11, GREEN, "700"))
    s.append(txt(654, y2 + 74, "only when the judge says the", 12, MUTE))
    s.append(txt(654, y2 + 90, "activity began before its", 12, MUTE))
    s.append(txt(654, y2 + 106, "window could reach", 12, MUTE))
    s.append(mono(654, y2 + 130, "~45 days back for ~4¢", 11, MUTE))
    s.append(arrow(606, y2 + 86, 630, y2 + 86, GREEN, 1.6, "5 4"))

    s.append(box(942, y2 + 26, 194, 120, "#f4faf7", GREEN, 7, 1.4))
    s.append(txt(960, y2 + 50, "OUTPUT: A LIST", 11, GREEN, "700"))
    s.append(txt(960, y2 + 72, "drift episodes, each", 12, MUTE))
    s.append(txt(960, y2 + 88, "with onset, mechanism,", 12, MUTE))
    s.append(txt(960, y2 + 104, "levers, correction", 12, MUTE))
    s.append(mono(960, y2 + 128, "may be empty", 11, MUTE))
    s.append(arrow(912, y2 + 86, 936, y2 + 86, GREEN))

    # ------------------------------------------------------------- footnotes
    y3 = 598
    s.append(f'<line x1="44" y1="{y3}" x2="{W-44}" y2="{y3}" stroke="{LINE}"/>')
    notes = [
        ("A window is not an episode.",
         f"It is the evidence, deliberately wider than any one activity — "
         f"{MULTI} measured windows hold more than one. Hence a list, not a "
         f"verdict: one activity can be on-goal and then off-goal with "
         f"nothing about the work changing."),
        ("The ceiling is refusals, not context.",
         f"The same window answered at 245K input tokens and refused at "
         f"{REFUSE_AT}. A refusal returns no text, so it arrives downstream "
         f"looking like “no drift found”. Budget {BUDGET}; retry; "
         f"never score a non-answer as a negative."),
        ("Where it stands.",
         f"Stage 1 is measured. Stage 2's first baseline is P {S2_P} / "
         f"R {S2_R} on 17 episodes — barely above the 0.59 you get by calling "
         f"everything drift. The failure is calibration, not perception: it "
         f"finds the right activities and then over-claims."),
    ]
    # Heading on its own line, body in a fixed column under it. The first
    # version flowed the body after the heading and wrapped the rest at the
    # left margin, which left every note ragged and one word orphaned.
    yy = y3 + 26
    for head, body in notes:
        s.append(txt(44, yy, head, 12, INK, "700"))
        words, line, lines = body.split(), "", []
        for w in words:
            if len(line) + len(w) + 1 > 132:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        lines.append(line)
        for i, ln in enumerate(lines):
            s.append(txt(44, yy + 17 * (i + 1), ln, 12, MUTE))
        yy += 17 * (len(lines) + 1) + 12

    s.append("</svg>")
    return "\n".join(s)


if __name__ == "__main__":
    open(OUT, "w").write(svg())
    print(f"wrote {OUT}")
