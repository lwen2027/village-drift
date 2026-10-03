"""How the 100 golden labels were made.

Finished work, kept for provenance and reproducibility rather than because
anything runs it. The 100 rows are labelled; these are the tools that drew
them, rendered what the labeller read, and recorded the judgements.

    frame.py            every agent-day and the attributes we stratify on
    sample_eval_set.py  drew the 100, seeded and reproducible
    render_digest.py    the ONLY surface a labeller saw — and it writes the
                        digest_sha back into eval_100.jsonl, so each label is
                        bound to the exact text it was made from
    show_day.py         one day rendered COMPLETE, for when the digest's
                        sampling hid the thing in question
    verify.py           records an audit into verification.jsonl
    build_train_set.py  the 23 hand-read cases the codebook came from, split
                        out so nothing scores against its own training data

WHY THIS IS SEPARATE FROM THE STAGE EVALUATORS. They measure a labelling method
against these labels; this package made the labels. Mixing them is how you end
up scoring a method
against rows it was tuned on — build_train_set.py exists precisely to stop
that, and it should not sit in the directory it is defending against.

Nothing in `village_drift/` imports any of this. Production stays independent
of its answer keys.
"""
