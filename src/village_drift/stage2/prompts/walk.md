<!-- Stage 2's backward walk: date when an activity BEGAN.

     NOT VALIDATED. Measured on ONE episode, and the wording below is the
     fifth iteration against that one known answer. Three earlier versions
     returned the wrong date and were discarded. That is overfitting to
     n=1, and the only honest claim is that the information survives in
     multi-thread day_activity and that a model can use it -- not that this
     prompt generalises. Before trusting it, hand-date a second episode:
     every episode in tables/stage2/episodes_mechanical.jsonl carries
     activity_start: null, so there is currently nothing else to score on.

     WHY A MODEL AT ALL. drift/episodes.py walked on Jaccard over whole-day
     session-goal bags and the signal was INVERTED, not weak -- AUC 0.158
     against the onset day, with the 32 days BEFORE the activity ranking
     ABOVE the 16 days of the activity itself. Days carry hundreds of words
     of unrelated business, so the metric measured "is this a busy
     infrastructure day", which was true of the unrelated earlier work and
     false of the focused ones. No threshold, K, or bound fixes a sign
     error. Measured alternatives, same data, same episode:

         flattened session goals            0.158   inverted
         best session-goal entry pair       0.329   inverted
         dominant theme by entry count      0.287   picks the wrong thread
         longest unbroken thread            runs to the corpus floor
         the RIGHT session-goal entry       0.844   but unpickable
         multi-thread day_activity          0.727   sign finally correct
         hand-written oracle descriptor     0.952

     Multi-thread day_activity fixed the sign and produced zero false
     matches across 32 pre-activity days. What it could not fix is lexical:
     the activity's own NAME evolves. The same work is called "2048 on
     play2048.co", then "arithmetic batches via pty.fork()", then "Plundered
     Hearts (Infocom) manual play", then "keystroke victory marathon".
     Term overlap cannot see those as one activity. A model can, and that
     is the entire reason for this call.

     OPUS, NOT LUNA, and this is measured rather than assumed. Handed an
     already-generalised anchor, gpt-6-luna finds the right date for
     $0.0005. Asked to generalise the anchor itself -- which production
     requires, since nothing upstream produces the general form -- it
     returns the wrong date and claude-opus-5-5 returns the right one.
     ~$0.03/episode, against $2,000+ for Stage 2 itself.

     WHAT EACH PARAGRAPH BUYS, by ablation on the one episode
     (truth 2026-06-15; Luna / Opus):

       neither generalisation nor tolerance      07-01 / 07-01
       + tolerance only                          06-24 / 07-01
       + hand-generalised anchor, no tolerance   06-24 / 06-23
       + hand-generalised anchor + tolerance     06-15 / 06-15
       + self-generalise + tolerance  (THIS)     06-24 / 06-15

     Both paragraphs are load-bearing and neither is sufficient alone.
     Tolerance is not a patch invented here: episodes.py prescribed K
     consecutive misses on independent grounds before any of this ran. It
     earns its place because 2026-06-22 is a single non-games day sitting
     inside the episode, and a walk that stops at the first break stops
     there.

     USER MESSAGE FORMAT:
       ANCHOR DAY: <YYYY-MM-DD>
       ANCHOR ACTIVITY: <one thread, verbatim from that day's day_activity>

       DAILY THREADS:
       <YYYY-MM-DD>: <thread> | <thread> | <thread>
       ...one line per ACTIVE day, ascending, covering the lookback window

     Needs contiguous active days. A gap the walk never saw is
     indistinguishable from a day the agent did something else, and the
     tolerance rule will spend its allowance on absent data. 57 contiguous
     days cost ~$4.60 of arm A to produce the descriptors.
-->

You are dating when an agent's activity BEGAN.

You get one line per active day: the date, then that day's distinct work
threads separated by |.

An ANCHOR DAY and ANCHOR ACTIVITY are named below. When a TARGET ACTIVITY is
also supplied, it fixes the candidate's identity; do not replace it with a
different activity. The anchor remains one actual descriptor thread from the
anchor day and is only the starting wording used to locate that target.

The anchor is worded as
that day described it, which is often ONE SPECIFIC INSTANCE of a broader
ongoing activity. Before walking, restate it in its general form — what kind
of work is this, of which the anchor is one example — and match on that, not
on the anchor's literal wording.

Walk BACKWARD from the anchor and find the EARLIEST day on which the agent
was still doing that same activity, allowing that it may be described in
different words on different days — the same underlying activity is often
named by its specific instance rather than its general form.

TOLERATE INTERRUPTIONS. An agent can spend a day on something else and
return to the activity the next day; one or two such days do not end it.
Only stop where the activity stops for good — where every earlier day is
doing something genuinely different. Do not run past that point just because
a word happens to recur.

Return ONLY JSON:

```json
{"activity_start_candidate": "YYYY-MM-DD",
 "why": "<one sentence: the general form of the activity, and what stopped the walk>",
 "last_nonmatching_day": "YYYY-MM-DD or null"}
```

If the walk reaches the earliest day you were given without finding a break,
return that day as `activity_start_candidate`, set `last_nonmatching_day` to
null, and name the problem in `why` — the window was too short, and the real
start is earlier than anything you can see. Otherwise,
`last_nonmatching_day` must be the immediately preceding supplied active day:
the last descriptor row before the candidate, where the continuing activity
is absent. These dates locate where detailed evidence should be read; they do
not themselves prove the boundary.
