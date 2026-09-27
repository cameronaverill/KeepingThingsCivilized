# Step 3 cleanup brief (leftovers, no new features)

Status: contract for the agents, written 2026-09-26. Three small, independent jobs. Two agents: a builder and a tester.

| Agent | Owns | Tests |
|---|---|---|
| Builder | `moderation/management/commands/spike.py` (the one counter fix), `moderation/replay.py` (remove the CRLF workaround only if it becomes redundant), comment cleanup in already-tracked files (list below), `docs/step3_brief.md` (append the freeze section) | none |
| Tester | tests only | `tests/moderation/` (new files `test_step3_cleanup_*.py`), and updates to `tests/replay/` only where a test pins the removed workaround |

Rules: `/workspace/.venv/bin/python`; no real API calls, no network; never read, print or create `.env` or any key; no git commands; unique scratch folders under `/tmp/claude-1000/-workspace/26b48491-740d-41f7-af98-d9c9c69e12c2/scratchpad/`; mutation copies are `rsync -a` copies OUTSIDE the repo tree, deleted at the end. A dev server is running against `/workspace/db.sqlite3`: run nothing against it. Other agents are editing `forum/` and `tests/forum_*`: do not touch those.

## Job 1: the CRLF counting bug in the spike
`spike.validate_transcript` counts message characters without normalising line endings (CRLF counts as two), unlike `forum.limits.count_message_chars` (CRLF to LF, NFC, strip, count code points), which is the rule real users are held to. Make the spike's check use `count_message_chars` (import it; do not copy it). `moderation/replay.py` has a workaround (`_newlines_normalized` and its use in `_spike_validate`); once the spike is right, remove that workaround if it is redundant and keep every replay behaviour and test green. Do not change any other validation rule. Tests: a message that is over the limit only because of CRLFs is accepted by the spike (80 with a CRLF passes at limit 80, 81 fails), NFC/emoji/whitespace cases match `count_message_chars` exactly for a table of about 40 strings, the golden transcripts still all validate, and the replay tests still pass.

## Job 2: record the schema freeze
The owner approved freezing the Master and Intervenor output schemas (`moderation/schemas.py`) on 2026-09-25 but it was never recorded. (a) Append a section "Schema freeze (recorded 2026-09-26)" to `docs/step3_brief.md` stating what is frozen (`MasterOutput`, `IntervenorOutput` and their nested models) and that changing them requires a new prompt version, re-running the golden checks and the owner's approval. (b) Add a test file `tests/moderation/test_step3_cleanup_schema_freeze.py` that pins the schemas: the sha256 of `json.dumps(Model.model_json_schema(), sort_keys=True)` for `MasterOutput` and `IntervenorOutput`, computed NOW from the current code (compute the two hashes with the venv Python, write them into the test with a comment saying how to regenerate and that a change needs the owner's approval), plus the sorted field names of each nested model. The test fails on any schema change.

## Job 3: duplicated `# secret-scan: allow` markers
An outside tool repeated the marker many times on some lines in tracked files (for example `tests/test_accounts_user.py` lines with eight copies, and `config/settings.py`). Find every tracked or untracked-but-committed-candidate file under `/workspace` (not `.venv`, not `.git`, not `scratchpad`) where a line contains the marker more than once (also where it appears with no space, `#secret-scan: allow`), and reduce each such line to exactly ONE `# secret-scan: allow` at the end, changing nothing else on the line. Then the secret scanner tests must still pass: `tests/test_secret_repo_protection.py`, `tests/test_secret_scanner.py`, `tests/test_review2_scanner_and_hooks.py`, `tests/test_repo_hygiene.py`, and the scanner run `python scripts/check_secrets.py --all` must report clean. Never remove the marker from a line that needs it. List every file you touched in your report.

## Not in this job
Generating `golden/expected/` snapshots needs real API calls and is NOT part of this job (it waits for the owner).

## Reports
Builder: files changed, before/after examples, test status. Tester: tests written, non-vacuity evidence, a short mutation pass (spike counter using `len`, workaround removal breaking replay, freeze hash unchanged after a schema edit, marker reduction that drops a needed marker), survivors explained.
