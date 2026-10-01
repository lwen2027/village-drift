"""Render the Stage-1 ranking as a figure — the deliverable, not the verdict.

One panel: what you find as you read down the ranked list, against what it
costs. A per-row strip sat above it and was removed — it rendered the same
rows the curve integrates, and a picture of individual rows invites
eyeballing a cutoff, which is the one thing this data cannot support.

Deliberately NOT a precision/recall curve or an ROC. Both are read as
"pick a threshold", and every threshold tried scored ~0.86 accuracy while
differing by hundreds of Stage-2 reads — accuracy is blind to the only
choice that matters here. The axis is corpus reads instead, because that is
the decision: how far down do you read.

SUPERSEDED 2026-09-30. Three figures used to be drawn — holdout 60,
bake-off 40, and the two POOLED. The pooling was a defect being reported as
a measurement: the 40 were judged by a rubric that had never heard of
`reached_audience`, against v1 blocks that did not contain it, and stitched
to 58 rows from a different prompt. B-all100 is the first run of one
configuration over every labelled row, so there is now one figure and it
needs no footnote about which half a point came from.

What replaces the pooling caveat is a different one, and it is real: the
confidence threshold that picks the operating point was tuned on these same
rows. The held-back 60 is spent. Any cut read off this curve is in-sample.

    python3 eval/ranking_chart.py      # -> eval/ranking_all100.svg
    swift eval/svg2png.swift eval/ranking_all100.svg eval/ranking_all100.png 2400
"""
from __future__ import annotations

import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "tables", "stage1", "arena_runs")
LABELS = os.path.join(HERE, "tables", "stage1", "eval_100.jsonl")
# 4,091, not the 4,027 quoted all through the bake-off: that figure predated
# the holdout, whose range opens eleven days earlier. prep reports the real
# count for the range it built, and this is it.
CORPUS = 4091

# One run, one rubric, one block version, every labelled row. The three
# earlier entries (holdout / arena40 / pooled) are deleted rather than kept
# for comparison: they were drawn from runs whose prompts no longer exist in
# the tree, so re-rendering them would produce figures nothing can reproduce.
RUNS_SPEC = {
    "all100": ("B-all100__*.json", "ranking_all100.svg",
               "every labelled agent-day · one rubric, v2 blocks"),
    # The same rows run TWICE under the final rubric, averaged per row. Two
    # repeats is not a sample size -- it is a variance estimate, and the
    # reason for drawing this one is that the repeats disagree on 6 rows.
    "final": (["B-final_a__*.json", "B-final_b__*.json"], "ranking_final.svg",
              "final rubric · mean of two identical runs"),
}


def ranked(pat):
    """Rows as (P(drift), gold), best first.

    AVERAGES over runs covering the same (agent, day) rather than
    concatenating them. The earlier multi-pattern case was two DISJOINT
    samples, where each row appeared once and concatenation was right. Two
    repeats of the same rows is the opposite situation: concatenating would
    enter every row twice and halve the apparent variance for free.

    Averaging is worth doing because the repeats disagree. Measured on
    final_a vs final_b: 6 of 93 verdicts flip with an identical prompt, all
    of them at confidence 0.45-0.60. Confidence itself is stable (median
    delta 0.02), so the mean of two runs is a better estimate of each row's
    P(drift) than either run alone -- and the ranking is what this figure
    scores.
    """
    pats = pat if isinstance(pat, list) else [pat]
    gold = {}
    for line in open(LABELS):
        r = json.loads(line)
        gold[(r["agent"], r["day"])] = r.get("is_drift")
    acc = {}
    for f in [x for p in pats for x in glob.glob(os.path.join(RUNS, p))]:
        r = json.load(open(f))
        if r.get("error"):
            continue
        k = (r["agent"], r["day"])
        g = gold.get(k)
        if not isinstance(g, bool):
            continue
        v = r.get("verdict") or {}
        c = v.get("confidence") or 0
        said = str(v.get("is_drift")).lower() == "true"
        acc.setdefault(k, [[], g])[0].append(c if said else 1 - c)
    rows = [(sum(ps) / len(ps), g) for ps, g in acc.values()]
    rows.sort(key=lambda x: -x[0])
    return rows


def auc_ci(rows, n_boot=4000, seed=11):
    """AUC with a ROW-level bootstrap interval.

    Resample rows, not pairs. 23 drift x 68 not-drift is 1,564 comparisons
    but only 91 observations — each drift row sits in 68 pairs — so treating
    the pairs as independent gives +/-1.6 points where the truth is +/-7.2.
    Quoting AUC to three decimals was false precision on a 15-point interval.
    """
    import random
    rng = random.Random(seed)
    n = len(rows)
    out = []
    for _ in range(n_boot):
        s = [rows[rng.randrange(n)] for _ in range(n)]
        p = [x for x, g in s if g]
        q = [x for x, g in s if not g]
        if p and q:
            out.append(sum((a > b) + 0.5 * (a == b) for a in p for b in q)
                       / (len(p) * len(q)))
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out))]


def auc(rows):
    pos = [p for p, g in rows if g]
    neg = [p for p, g in rows if not g]
    w = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return w / (len(pos) * len(neg)), len(pos), len(neg)


def bootstrap_cut(rows, n_boot=4000, seed=11):
    """Where does full recall land if you had drawn different rows?

    The single number everyone wants — "read the top X%" — is set by ONE
    observation, the worst-ranked drift day. Resampling with replacement
    shows how far that moves, and it is the only honest way to present a
    cut derived from 23 positives.
    """
    import random
    rng = random.Random(seed)
    n = len(rows)
    cuts = []
    for _ in range(n_boot):
        s = sorted((rows[rng.randrange(n)] for _ in range(n)),
                   key=lambda x: -x[0])
        h = [i + 1 for i, (_, g) in enumerate(s) if g]
        if h:
            cuts.append(h[-1] / len(s))
    cuts.sort()
    return cuts


def svg(which="holdout") -> str:
    pat, _out, sub = RUNS_SPEC[which]
    rows = ranked(pat)
    n = len(rows)
    a, npos, nneg = auc(rows)
    alo, ahi = auc_ci(rows)
    hits = [i + 1 for i, (_, g) in enumerate(rows) if g]

    W, H = 1000, 660
    L, R = 78, 40
    plot = W - L - R
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="Helvetica,Arial,sans-serif" font-size="13">',
         f'<rect width="{W}" height="{H}" fill="#fbfbfa"/>',
         f'<text x="{L}" y="34" font-size="19" font-weight="600">'
         f'Stage 1 as a ranked list, not a verdict</text>',
         # No row count in the subtitle. The curve scores 93 rows -- the 7
         # open-goal days carry no true/false label -- but the labelled set
         # is quoted as 100 everywhere else, and a bare "93" in the headline
         # reads as a contradiction. The caption carries the reconciliation.
         f'<text x="{L}" y="55" font-size="12.5" fill="#666">'
         f'{sub} · ordered by the judge’s own P(drift) '
         f'· {npos} drift days · AUC {a:.2f} ({alo:.2f}–{ahi:.2f})</text>']

    # The ranked strip that used to sit here is gone. It showed the same 91
    # rows the curve below already integrates, so it was a second rendering
    # of one fact -- and being a per-row picture it invited exactly the
    # reading the caption warns against, eyeballing where the red runs out.
    # ---- panel 2: what reading down buys ----------------------------------
    gy0, gh = 110, 300
    gx1 = W - R
    s.append(f'<text x="{L}" y="{gy0-16}" font-size="12.5" font-weight="600">'
             f'what you find as you read down the ranked list</text>')
    for k in range(0, npos + 1, 2):
        y = gy0 + gh - k / npos * gh
        s.append(f'<line x1="{L}" y1="{y:.1f}" x2="{gx1}" y2="{y:.1f}" '
                 f'stroke="#ececea"/>')
        s.append(f'<text x="{L-10}" y="{y+4:.1f}" text-anchor="end" fill="#666" '
                 f'font-size="11.5">{k}</text>')
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        x = L + frac * plot
        s.append(f'<line x1="{x:.1f}" y1="{gy0}" x2="{x:.1f}" y2="{gy0+gh}" '
                 f'stroke="#ececea"/>')
        s.append(f'<text x="{x:.1f}" y="{gy0+gh+18}" text-anchor="middle" '
                 f'fill="#666" font-size="11.5">{100*frac:.0f}%</text>')
        s.append(f'<text x="{x:.1f}" y="{gy0+gh+34}" text-anchor="middle" '
                 f'fill="#aaa" font-size="10.5">{CORPUS*frac:,.0f}</text>')
    s.append(f'<text x="{L+plot/2:.0f}" y="{gy0+gh+56}" text-anchor="middle" '
             f'fill="#333">share of the corpus read, in rank order '
             f'(and agent-days at {CORPUS:,})</text>')
    s.append(f'<text transform="translate(24,{gy0+gh/2:.0f}) rotate(-90)" '
             f'text-anchor="middle" fill="#333">drift days found</text>')

    pts = []
    found = 0
    for i in range(n + 1):
        if i and rows[i-1][1]:
            found += 1
        pts.append((L + i / n * plot, gy0 + gh - found / npos * gh))
    s.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
             + '" fill="none" stroke="#b03030" stroke-width="2.4"/>')
    # perfect-ordering reference
    xp = L + npos / n * plot
    s.append(f'<polyline points="{L},{gy0+gh} {xp:.1f},{gy0} {gx1},{gy0}" '
             f'fill="none" stroke="#1a7f5a" stroke-width="1.3" '
             f'stroke-dasharray="5 4" opacity="0.75"/>')
    s.append(f'<text x="{gx1:.1f}" y="{gy0-4}" text-anchor="end" font-size="11.5" '
             f'fill="#1a7f5a">perfect ordering (AUC 1.00)</text>')

    # bootstrap band: where the full-recall cut lands under resampling
    if which in ("all100", "final"):
        cuts = bootstrap_cut(rows)
        lo, hi = cuts[int(.025 * len(cuts))], cuts[int(.975 * len(cuts))]
        xl, xh = L + lo * plot, L + hi * plot
        s.append(f'<rect x="{xl:.1f}" y="{gy0}" width="{xh-xl:.1f}" '
                 f'height="{gh}" fill="#b03030" opacity="0.10"/>')
        # Sits BELOW the plot, not inside it: inside, this label ran straight
        # through the two cut lines it is describing.
        s.append(f'<text x="{(xl+xh)/2:.1f}" y="{gy0+gh+78}" '
                 f'text-anchor="middle" font-size="11.5" font-weight="600" '
                 f'fill="#b03030" opacity="0.85">'
                 f'95% of resamples put the full-recall cut in '
                 f'{100*lo:.0f}%–{100*hi:.0f}%</text>')

    # the two cuts
    for pos, lab, col, dy in (
            (hits[-2], f"{100*hits[-2]/n:.0f}% → {npos-1} of {npos}", "#5a6b8c", 62),
            (hits[-1], f"{100*hits[-1]/n:.0f}% → all {npos}", "#b03030", 38)):
        x = L + pos / n * plot
        s.append(f'<line x1="{x:.1f}" y1="{gy0}" x2="{x:.1f}" y2="{gy0+gh}" '
                 f'stroke="{col}" stroke-width="1.5" stroke-dasharray="4 3"/>')
        s.append(f'<text x="{x+7:.1f}" y="{gy0+dy}" font-size="11.5" '
                 f'font-weight="600" fill="{col}">{lab}</text>')

    cut = 100 * hits[-1] / n
    note = (f"AUC {a:.2f} (95% {alo:.2f}–{ahi:.2f}) is a pairwise win rate: pick "
            f"one drift day and one non-drift day, and the drift day ranks "
            f"higher {100*a:.0f}% of the time. It scores the ORDER only, which "
            f"is what a ranked deliverable needs. Run the same rows twice and 6 "
            f"verdicts flip while AUC holds to two decimals — the order "
            f"reproduces, the labels do not, which is why this is a ranked "
            f"list and not a verdict. "
            f"The {cut:.0f}% mark is set by the "
            f"single worst-ranked drift day; resampling puts "
            f"it anywhere from "
            + (f"{100*bootstrap_cut(rows)[100]:.0f}% to "
               f"{100*bootstrap_cut(rows)[-100]:.0f}%. "
               if which in ("all100", "final") else "far lower to far higher. ")
            + f"With {npos} drift days the curve's SHAPE is the finding and "
            f"every particular cut is noise — and the confidence threshold "
            f"that would pick one was tuned on these same rows, so it is "
            f"in-sample besides. The labelled set is 100 days; "
            f"{100 - n} of them are open-goal, where drift is undefined by "
            f"construction, so the curve scores the {n} that carry a "
            f"true/false label.")
    for i, line in enumerate(_wrap(note, 116)):
        s.append(f'<text x="{L}" y="{H-150+i*15}" font-size="11.5" fill="#555">'
                 f'{line}</text>')
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
    import sys
    for which in (sys.argv[1:] or list(RUNS_SPEC)):
        out = os.path.join(HERE, RUNS_SPEC[which][1])
        open(out, "w").write(svg(which))
        print(f"wrote {out}")
