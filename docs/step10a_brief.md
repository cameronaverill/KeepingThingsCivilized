# Step 10a brief: `seed_topics` (the README and the golden command come later, after steps 8 and 9)

Status: contract for the agents, written 2026-09-26. Plan references: section 2 (stance, position, `Topic.leans`), section 8 (`Topic`), section 9 (topics of the golden set and the non-political warm-up), section 14 step 10, section 18 (labelling user-created propositions).

| Agent | Owns | Tests folder |
|---|---|---|
| 10a builder | `forum/management/__init__.py`, `forum/management/commands/__init__.py`, `forum/management/commands/seed_topics.py`, `forum/seed_topics.json` | (writes no tests) |
| 10a tester | tests only | `tests/seed_topics/` |

Rules for everyone: use `/workspace/.venv/bin/python`; no real API calls, no network; never read, print or create `.env` or any key; do not commit or change git state; unique scratch folders; mutation copies are `rsync -a` copies outside the repo tree, deleted at the end.

## What it does
`manage.py seed_topics [--dry-run]` creates or updates the seeded topics from `forum/seed_topics.json`. Seeded topics have `created_by = None`, `hidden = False`. It is **idempotent**: keyed by `title`; running it twice creates no duplicates; a second run updates `description`, `proposition` and `leans` if the file changed, and reports "created n, updated m, unchanged k". `--dry-run` writes nothing and prints what it would do. It never touches user-created propositions (`created_by` not null) and never deletes anything. Invalid file content (missing key, proposition over `MAX_PROPOSITION_CHARS`, `leans` not matching the shape below, duplicate titles) aborts before any write, with a clear message naming the entry.

## The data file
`forum/seed_topics.json`: a list of objects `{title, description, proposition, leans}`.
- Six entries: **rent control** ("Cities should cap how much landlords can raise rents each year."), **drug decriminalization**, **sanctuary cities** (these three: use the exact `title` and `proposition` strings of `golden/transcripts/*.json`, so the golden set and the seeded topics agree; read them from the files), and the **non-political warm-up** topics **school start times**, **bike lanes**, **remote work** (propositions of at most 200 characters, phrased as a claim people can agree or disagree with).
- `leans` shape (plan section 2): `{"pro": {<scheme>: {<axis>: {"value": float in [-1, 1], "rationale": str}}}, "con": {...}}` for the schemes `compass` (axes `economic`, `social`) and `us_partisan` (axis `party`). Every rationale is one or two plain sentences saying WHY that side's typical position sits there on that axis, with no name-calling and no claims about individuals. For the three warm-up topics the builder sets every value to `0.0` and the rationale to "No clear left/right coding: a low-stakes topic used to validate the pipeline (plan section 9)."; a test pins this.
- **These `leans` are a DRAFT for the owner's review (plan step 17: scenario scrutiny).** The `description` field of the file's first entry, and the command's output, state this plainly; the commit message will too. Do not present them as authoritative.

## Tests (tester, `tests/seed_topics/`)
Creates the six topics with `created_by=None`, `hidden=False`; idempotent (run twice: same rows, same ids, second report "unchanged"); an edited file updates in place (use a temp file via a `--file` option, which the builder adds, default the packaged file); dry-run writes nothing; user-created propositions are untouched; validation failures abort atomically with the entry named (missing key, over-long proposition, bad leans shape, values outside [-1, 1], unknown scheme/axis, duplicate titles); every packaged proposition is <= `MAX_PROPOSITION_CHARS`; golden transcript titles/propositions match the seeded ones exactly (read the transcript files in the test); warm-up leans are all 0.0; the packaged file contains no email address, URL or personal name; the topics appear on the home page for a logged-in user (use the Django test client) and can be entered (`forum.services.enter_proposition`); no `.env` access.
