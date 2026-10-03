You are revising an initial episode analysis after a backward timeline scan
found that relevant activity began before the first detailed window.

The INITIAL DRAFT is a hypothesis, not evidence. The BACKWARD WALK RESULT and
COMPACT DAILY SPINE are navigation aids, not evidence. Do not quote them and do
not preserve a draft conclusion merely because it is already written down.

The packet repeats the detailed source days cited by already-supported
episodes. Preserve those episodes unchanged. The expansion resolves one
different activity; uncertainty about that activity is not a reason to discard
or rewrite a separately supported episode.

Reconsider every field using the DETAILED SOURCE EXCERPTS: whether each
candidate is drift at all, activity_start, onset, mechanism, available levers,
correction, dissent, and both confidences. Return the complete Stage-2 JSON
object, not a patch. You may remove, split, or add episodes.

Evidence must use the structured `{day, quote}` form and be copied from the
detailed source excerpts for that named day. The initial draft, backward walk
and compact spine are not quotable sources. Keep `examined` true when clipping,
missing days, or omitted boundary candidates affect only one activity. Retain
that activity as an incomplete episode and name each unresolved field in its
`missing_evidence_for`. Set `examined` false only when a problem affects the
whole window, such as an unreadable goal or unusable detailed evidence for
every candidate. Uncertainty about the expanded activity must not discard a
separately supported episode.

The walk's `activity_start_candidate` is a locator, not a fact. Set
`activity_start_supported` true only when the detailed source for that day
shows the activity and the detailed source for `last_nonmatching_day` shows it
absent or materially different. Cite exact excerpts from both days. If the
predecessor is null or either side does not verify the transition, keep the
candidate only as an observed lower bound, set `activity_start_supported`
false, and include `"activity_start"` in `missing_evidence_for`. Do not replace
the candidate with another supposedly supported date from the compact spine.

Return `history_request: null` when the expanded candidate is resolved. If it
remains too uncertain to report even as an incomplete drift episode, repeat
the incoming `history_request` unchanged as an unresolved-candidate marker.
The runner may make at most two bounded, identity-locked expansions; the marker
records what remains unresolved for that state machine. It is not permission
to request arbitrary evidence or to recurse without a fixed limit. Do not
invent a different activity or anchor.
