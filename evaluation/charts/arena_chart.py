"""Render the bake-off result as an SVG — cost against accuracy.

Deliberately NOT a single cost/accuracy ratio. A ratio collapses two
dimensions into one middling number and hides which of them is moving. A
Pareto scatter shows domination directly — the arm that is up and to the
left wins outright, and an arm down and to the right has no argument left.

The first version of this chart plotted arm B at 0.70 and the note called it
"the exception that is not a tie". That was an artefact: arm B had been given
a schema containing 3,195 of arm A's 25,547 chars and no Context sections at
all. Re-run with parity it scores 0.84, the same as arm A. The lesson is in
the file because the chart was persuasive while it was wrong.

Cost is computed per MODEL, not per arm, because the arms split their spend
very differently: arm C sends 47% of its input to the expensive judge (a
flagged day is read twice, once to screen and once to confirm), while arm B
sends 1.4% (its judge reads a tiny filled block). Pricing an arm at one blended
rate would erase that.

    python3 -m evaluation.charts.arena_chart
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os

from village_drift import paths
from village_drift.stage1 import run as A
from evaluation.stage1.arena import PRICES

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = str(paths.STAGE1_RUNS)
OUT = str(paths.STAGE1_ARTIFACTS / "arena_results.svg")

# Prices live in arena.py, beside the usage data they multiply, so the
# scorer and this chart cannot drift apart.

# Measured on the 40-row sample. Accuracy is over the 37 DEFINED rows;
# open-goal rows are scored separately as abstention and excluded here,
# because "undefined" is not a class in a binary confusion matrix.
ACC = {"A": 0.84, "B": 0.84, "C": 0.81, "D": 0.78, "0": 0.84}
LABEL = {
    "A": "A  mechanical block",
    "B": "B  cheap model fills the schema",
    "C": "C  screen → judge",
    "D": "D  context only, facts stripped",
    "0": "0  incumbent monitor",
}
# Arm 0 ran in production, so there is no usage record. Estimated from its
# own input recipe at 3.48 chars/token, calibrated on arm C's 40 recorded
# (chars, tokens) pairs for the same content type. A FLOOR: the monitor's own
# prompt, its 13-category taxonomy and its JSON schema are not counted.
ARM0_EST = {"claude-opus-5-5": {"in": 11_480_555, "out": 60_000}}

CORPUS_DAYS, SAMPLE_DAYS = 4027, 40
SKEW = 0.839          # sampled rows are ~19% larger than the corpus average

# Prompt caching, verified working (a repeat call reports cache_read == the
# rubric's 1,986 tokens). 90% of the rubric becomes a 0.1x read on every
# judge call after the first. Applied to A, B and C because it is measured.
#
# NOT applied to arm 0, because we never instrumented it. Its own notes say
# the ~100K shared village transcript caches across the ~23 agents in a day,
# which would take it from ~$3,980 to ~$1,871 and narrow arm A's advantage
# from 23x to ~12x. Plotted as a RANGE rather than a point: crediting our own
# arms with verified caching while denying arm 0 its claimed caching would
# flatter the result.
RUBRIC_TOKENS = 1986
ARM0_CACHED_FLOOR = 1871


JUDGE_CALLS = {}


def measured():
    out = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    jc = collections.Counter()
    for f in glob.glob(os.path.join(RUNS, "*.json")):
        r = json.load(open(f))
        for c in r.get("calls") or []:
            u = c.get("usage") or {}
            out[r["arm"]][c["model"]]["in"] += u.get("input_tokens") or 0
            out[r["arm"]][c["model"]]["out"] += u.get("output_tokens") or 0
            out[r["arm"]][c["model"]]["read"] += u.get("cache_read_input_tokens") or 0
            out[r["arm"]][c["model"]]["write"] += u.get("cache_creation_input_tokens") or 0
            if c["stage"] == "judge":
                jc[r["arm"]] += 1
    out["0"] = {m: collections.Counter(v) for m, v in ARM0_EST.items()}
    JUDGE_CALLS.update(jc)
    return out


def cost(by_model, judge_calls=0):
    """None if any needed price is missing — better no number than a made-up one.

    The multipliers live in A.call_cost, not here. An earlier version of this
    function counted cache reads and writes as FREE and then also subtracted
    the assumed rubric discount, so any arm that genuinely cached was
    discounted twice: arm D came out at $111 against the scorer's $138.

    What stays local is the PROJECTION — the assumed-caching credit below,
    which is this chart's own modelling and has no place in a function that
    prices a measured call.
    """
    total = 0.0
    for model, tok in by_model.items():
        tok = dict(tok)
        p = PRICES.get(model)
        if not p or p["in"] is None or p["out"] is None:
            return None
        # The rubric caches on ~90% of judge calls at scale. Credit only the
        # part NOT already sitting in measured reads, or the same saving is
        # taken twice.
        if judge_calls and model == "claude-opus-5-5":
            assumed = RUBRIC_TOKENS * judge_calls * 0.9
            tok["in"] = max(0.0, tok["in"] - max(0.0, assumed - tok.get("read", 0)))
        total += A.call_cost(
            {"input_tokens": tok["in"], "output_tokens": tok["out"],
             "cache_read_input_tokens": tok.get("read", 0),
             "cache_creation_input_tokens": tok.get("write", 0)}, model)
    return total


def svg():
    data = measured()
    priced = all(cost(v) is not None for v in data.values())
    # x is token count when unpriced, dollars when priced; log either way,
    # because the arms span ~30x and a linear axis would stack three of them.
    scale = CORPUS_DAYS / SAMPLE_DAYS * SKEW
    # Plot the CORPUS figure, not the 40-row sample bill. The question is what
    # auditing the whole village costs; the sample total is an artefact of how
    # many rows we happened to draw. Ratios are identical either way.
    xval = {a: ((cost(v, JUDGE_CALLS.get(a, 0)) if priced
                 else sum(t["in"] + t["out"] for t in v.values())) * scale)
            for a, v in data.items()}
    # H leaves a caption band BELOW the axis label; an earlier version put
    # both at y=572 and they overlapped.
    W, H, L, R, T, B = 940, 680, 92, 300, 74, 160
    lo = math.log10(min(xval.values()) * 0.55)
    hi = math.log10(max(xval.values()) * 1.9)
    ylo, yhi = 0.64, 0.90

    def px(v):
        return L + (math.log10(v) - lo) / (hi - lo) * (W - L - R)

    def py(v):
        return H - B - (v - ylo) / (yhi - ylo) * (H - T - B)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica,Arial,sans-serif" font-size="13">',
         f'<rect width="{W}" height="{H}" fill="#fbfbfa"/>',
         f'<text x="{L}" y="32" font-size="19" font-weight="600">'
         f'Goal-drift labelling: what the judge should read</text>',
         f'<text x="{L}" y="52" font-size="12.5" fill="#666">'
         f'accuracy on 40 sampled agent-days · 37 with a closed goal, 10 drift '
         f'· cost from API usage, extrapolated with caching</text>']

    for g in (0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        y = py(g)
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" '
                 f'stroke="#e6e6e3"/>')
        s.append(f'<text x="{L-12}" y="{y+4:.1f}" text-anchor="end" fill="#666">'
                 f'{g:.2f}</text>')
    d = math.ceil(lo)
    while d <= hi:
        x = px(10 ** d)
        if L <= x <= W - R:
            lab = (f'{10**d/1e6:g}M' if d >= 6 else f'{10**d/1e3:g}K') if not priced \
                  else f'${10**d:,.0f}'
            s.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{H-B}" '
                     f'stroke="#e6e6e3"/>')
            s.append(f'<text x="{x:.1f}" y="{H-B+20}" text-anchor="middle" '
                     f'fill="#666">{lab}</text>')
        d += 1

    s.append(f'<text x="{(L+W-R)/2:.0f}" y="{H-118}" text-anchor="middle" '
             f'fill="#333">'
             f'{"cost" if priced else "tokens"} to label all '
             f'{CORPUS_DAYS:,} agent-days — log scale →</text>')
    s.append(f'<text transform="translate(26,{(T+H-B)/2:.0f}) rotate(-90)" '
             f'text-anchor="middle" fill="#333">accuracy</text>')

    # arm 0's caching is claimed but unmeasured, so show the interval rather
    # than pick an end of it.
    if priced:
        x0, xf, y0 = px(xval["0"]), px(ARM0_CACHED_FLOOR), py(ACC["0"])
        s.append(f'<line x1="{xf:.1f}" y1="{y0:.1f}" x2="{x0:.1f}" y2="{y0:.1f}" '
                 f'stroke="#5a6b8c" stroke-width="7" opacity="0.18"/>')
        s.append(f'<line x1="{xf:.1f}" y1="{y0-7:.1f}" x2="{xf:.1f}" '
                 f'y2="{y0+7:.1f}" stroke="#5a6b8c" stroke-width="1.6" opacity="0.6"/>')
        s.append(f'<text x="{xf-8:.1f}" y="{y0+26:.1f}" text-anchor="end" '
                 f'font-size="11" fill="#5a6b8c" opacity="0.85">'
                 f'${ARM0_CACHED_FLOOR:,} if its shared transcript caches</text>')

    # A and arm 0 land on the same accuracy. That is the finding, so draw it
    # rather than leaving the reader to notice two dots share a gridline.
    if ACC["A"] == ACC["0"]:
        # A and B tie on accuracy AND land within 9% on cost, so their markers
        # nearly coincide. Start the rule to the right of BOTH, or it is drawn
        # straight through whichever sits further right.
        tied = [px(xval[a]) for a in ACC if ACC[a] == ACC["A"] and a != "0"]
        x1, x2, yy = max(tied), px(xval["0"]), py(ACC["A"])
        s.append(f'<line x1="{x1+14:.1f}" y1="{yy:.1f}" x2="{x2-14:.1f}" '
                 f'y2="{yy:.1f}" stroke="#1a7f5a" stroke-width="1.4" '
                 f'stroke-dasharray="6 4" opacity="0.55"/>')
        mult = xval["0"] / min(xval[a] for a in ACC
                               if ACC[a] == ACC["A"] and a != "0")
        s.append(f'<text x="{x1 + (x2-x1)*0.75:.1f}" y="{yy-13:.1f}" text-anchor="middle" '
                 f'font-size="12.5" font-weight="600" fill="#1a7f5a">'
                 f'same accuracy, {mult:.0f}x the cost</text>')

    colour = {"A": "#1a7f5a", "B": "#1a7f5a", "C": "#c98a1e",
              "D": "#b03030", "0": "#5a6b8c"}
    for a in ("D", "C", "B", "0", "A"):
        x, y = px(xval[a]), py(ACC[a])
        est = a == "0"
        dash = 'stroke-dasharray="4 3"' if est else ""
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{11 if a=="A" else 9}" '
                 f'fill="{"none" if est else colour[a]}" stroke="{colour[a]}" '
                 f'stroke-width="{2.5 if est else 1}" '
                 f'{dash}/>')
        # A and B tie on accuracy and sit within 9% on cost, so their markers
        # nearly overlap. Separate the labels VERTICALLY and keep both to the
        # right: an earlier version put A's label to the LEFT, where it ran
        # back under the y-axis tick labels. A goes up far enough that its
        # cost string clears B's marker; B goes down far enough to clear the
        # arm-0 caching note, with a leader line saying which dot is which.
        up = a in ("A", "0")
        nudge = {"0": -22, "A": -20, "B": 44}.get(a, 0)
        tx = x + 15
        anchor = ""
        # A label pushed this far from its marker needs a leader, or the
        # reader cannot tell which of the two adjacent green dots it names.
        if abs(nudge) > 30:
            s.append(f'<line x1="{x:.1f}" y1="{y + 11:.1f}" x2="{x:.1f}" '
                     f'y2="{y + nudge + 6:.1f}" stroke="{colour[a]}" '
                     f'stroke-width="1" opacity="0.35"/>')
        s.append(f'<text x="{tx:.1f}" y="{y + nudge + (-6 if up else 18):.1f}"'
                 f'{anchor} font-weight="600" fill="{colour[a]}">{LABEL[a]}'
                 f'{" (est.)" if est else ""}</text>')
        v = xval[a]
        # cents matter at this end of the scale: arms A and B both round to
        # "$2" and the whole point is that they differ.
        sub = (f'${v:,.0f}  ·  ${v/CORPUS_DAYS:.3f}/day' if priced else
               (f'{v/1e6:,.0f}M tok' if v >= 1e6 else f'{v/1e3:,.0f}K tok'))
        s.append(f'<text x="{tx:.1f}" y="{y + nudge + (10 if up else 34):.1f}"'
                 f'{anchor} font-size="11.5" fill="#777">{sub}</text>')

    note = ("A, B and the incumbent all score 0.84; C and D sit a little "
            "below, still inside the ±23pt band that 10 drift days buys. Treat "
            "every accuracy gap here as a tie. Cost is not noisy — it is "
            "measured, and separates the arms by more than an order of "
            "magnitude, so it is what should decide. A and B tie on accuracy "
            "and nearly on cost; A wins on being deterministic.")
    for i, line in enumerate(_wrap(note, 108)):
        s.append(f'<text x="{L}" y="{H-72+i*16}" font-size="11.5" fill="#555">'
                 f'{line}</text>')
    if not priced:
        s.append(f'<text x="{W-R+8}" y="{T-12}" font-size="11" fill="#b03030">'
                 f'PRICES UNVERIFIED — showing tokens</text>')
    s.append("</svg>")
    return "\n".join(s)


def _wrap(t, n):
    out, line = [], ""
    for w in t.split():
        if len(line) + len(w) + 1 > n:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return out + ([line] if line else [])


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(svg())
    print(f"wrote {OUT}")
