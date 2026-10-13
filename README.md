# Think Together

A small two-person discussion site. Two people take opposite sides of a proposition and talk. After each message an
AI moderator (Claude) may add a short note, for example to point out an unsupported claim or abusive wording. The
goal is that the moderator stays **politically neutral**: given two equal contributions, it steps in equally often,
in an equal manner, with equal intended effect, whichever side wrote them.

The site logs every moderation decision, including the decision not to step in. A **separate bias evaluation**
(the `evaluation` and `analysis` folders) uses those logs to test the moderator for bias. The evaluation is not part
of the running site and is not deployed; the site's code never imports it (its tables are created by `migrate`).

This file says how to set the project up, run it, control its cost and find your way around. The design and the
reasons behind it are in `docs/plan.md`, which is the source of truth. If this file and the plan disagree, the code
and the plan win; please tell whoever maintains this file.

