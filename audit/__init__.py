"""What runs over all 4,027 agent-days.

The split from eval/ is by JOB, not by stage:

    drift/   mechanical extraction — the block
    audit/   the labelling run itself — prompts, client, payload, verdicts
    eval/    measuring whether the run is any good — sampling, scoring, charts

Everything here is production. Nothing here knows about golden labels, sample
draws or accuracy, and it must stay that way: the audit has to be runnable
over days that have no label, which is all but 100 of them.

eval/ imports audit/. Never the reverse.
"""
