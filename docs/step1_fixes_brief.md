# Step 1 fixes brief

Origin: the independent testing agent's step 1 report (verdict: pass with issues) plus the user's request that keys can never be uploaded to GitHub. The user approved: fix everything below now, ASCII-only usernames. Method: testing agent writes failing tests first, Claude reviews them, coding agent implements, testing agent verifies independently, Claude reviews and commits ("Step 1 fixes").

Already written and to be KEPT: `tests/test_review_gaps.py` (10 tests from the review, currently passing). The testing agent may edit or merge it, but must keep its coverage.

## A. Accounts: real case-insensitive uniqueness, ASCII usernames

Problem (reproduced): SQLite `LOWER()` only folds ASCII, so `Émile`/`émile`, `ß`/`SS`, `İ`/`i` are all accepted as different accounts.

Contract:
- `accounts/keys.py`: `normalize_key(value: str) -> str` = `NFKC(casefold(NFKC(value.strip())))`.
- `accounts/validators.py`: `validate_username(value)` raises `ValidationError` unless the value matches `^[A-Za-z0-9_-]+$` and its length is between `USERNAME_MIN_LENGTH` and `USERNAME_MAX_LENGTH` (tunables). The `User.username` field uses it (its `max_length` is `USERNAME_MAX_LENGTH`; drop Django's default `@.+-_` validator and help text).
- `User` gets two derived fields, `username_key` and `email_key` (`CharField`, `editable=False`, unique, never blank), set in `save()` from `normalize_key(username)` and `normalize_key(email)`. `save()` also strips whitespace from `email` and `username`.
- Enforced for EVERY code path (forms via `full_clean`, `create_user`, `create_superuser`, admin, plain `save()`): an invalid username or a username/email whose key already exists raises `ValidationError` with a friendly message ("A user with that username already exists." / "...email..."), so `createsuperuser --noinput` prints a CommandError, not a traceback. The unique constraints on the key columns stay as the database backstop.
- The old `Lower()` constraints are removed. New migration `0002` (do not edit `0001`): adds the key columns, backfills existing rows, drops the old constraints, adds the new unique constraints. `makemigrations --check` stays clean.
- Usernames that differ only by case (`Alice`/`alice`) and emails that differ only by case or Unicode form are duplicates.

## B. Production settings: fail closed
- In production (`DJANGO_ENV=production`) refuse to start (`ImproperlyConfigured`, message names the variable) if: `DJANGO_SECRET_KEY` equals the development fallback key or is shorter than 50 characters; `DJANGO_ALLOWED_HOSTS` contains `*`.
- In development mode, if `DJANGO_ENV` is not set (blank or absent) and `DJANGO_ALLOWED_HOSTS` contains any host other than `localhost`, `127.0.0.1`, `[::1]`, refuse to start with a message telling the user to set `DJANGO_ENV=production`. An explicit `DJANGO_ENV=development` is allowed.
- A relative `DJANGO_DB_PATH` resolves against the project directory (not the cwd); a path whose parent directory does not exist raises `ImproperlyConfigured` naming `DJANGO_DB_PATH`.

## C. Test hygiene
- `test_settings_load_without_an_api_key` must not depend on the developer's environment (run it in a subprocess with a clean environment, like the other settings tests).
- Remove the vacuous `test_case_insensitive_duplicates_fail_validation_too` (replaced by real tests under A).
- The "settings does not read `.env`" test uses the AST (no `dotenv` import or `load_dotenv` call in `config/settings.py`), not a substring match.
- Add: tunable names don't collide with Django setting names except the allowed set `{"SESSION_COOKIE_AGE"}`; `requirements.txt` contains exactly the expected packages (Django, anthropic, pydantic, argon2-cffi, django-axes, python-dotenv), all pinned.

## D. Keys must never reach GitHub (layered protection)
1. **`.gitignore`**: keep `.env`; add `.env.*` with `!.env.example`, `*.pem`, `*.key`, `secrets/`.
2. **`scripts/check_secrets.py`** (standard library only; runs on the minimal system Python too):
   - `find_secrets(text: str, path: str) -> list[Finding]`, `Finding(path, line, rule, redacted)`; the redacted snippet shows at most the first 6 characters then `…`, NEVER the full secret.
   - Rules: Anthropic key `sk-ant-` + 20 or more of `[A-Za-z0-9_-]`; generic `sk-` + 32 or more alphanumerics; AWS `AKIA` + 16 of `[0-9A-Z]`; GitHub tokens `gh[pousr]_` + 36 or more alphanumerics, and `github_pat_` + 50 or more of `[A-Za-z0-9_]`; Slack `xox[abprs]-` + 10 or more; Google `AIza` + 35; a PEM private key header; and a generic assignment (`api_key`, `secret`, `token`, `passwd`, `password`, followed by `=` or `:` and a 20-or-more-character token) except when the value contains `example`, `changeme`, `xxxx`, or is empty. A line containing `# secret-scan: allow` is skipped.
   - Filename rule: a path named `.env`, `.env.<anything>` other than `.env.example`, or ending `.pem` or `.key`, is a finding.
   - CLI: `--staged` (scans the staged content from the git index), `--all` (scans `git ls-files`), `--history` (scans lines added anywhere in `git log --all -p`). Exit code 1 with a readable list (`file:line rule redacted`) on any finding, 0 when clean.
   - The scanner's own source must not trigger it, and tests must build fake keys at runtime (for example `"sk-ant-" + "a" * 40`) so no test file contains a real-looking key.
3. **Git hooks in `.githooks/`** (executable): `pre-commit` runs `check_secrets.py --staged`; `pre-push` runs `check_secrets.py --history`. Each prefers `.venv/bin/python` and falls back to `python3`. Both block on a finding and print how to fix it and a reminder to REVOKE any key that was ever committed (deleting it later does not remove it from git history).
4. **`scripts/install_hooks.sh`** sets `git config core.hooksPath .githooks` for this clone (repo-local only). Claude runs it once now.
5. **Tests**: each rule fires and placeholders don't; redaction never contains the full secret; the allow marker works; `--all` on this repo finds nothing (tracked files); no `.env` is tracked; `.gitignore` patterns present; hooks exist and are executable; in a throwaway temporary git repo with the hooks installed, committing a file with a fake key is blocked and committing a clean file succeeds. Tests must set `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=<tmp path>` for subprocess git calls (this container sometimes reports "dubious ownership").
6. **Docs**: `CLAUDE.md` gets one line: never read, print or commit `.env` or any API key; the hooks enforce it. `.env.example` values stay empty.
7. **GitHub side (the user's actions, when a remote exists)**: keep the repo private; turn on secret scanning and push protection in the repository's Code security settings; if a key is ever pushed, revoke it in the Console at once.

## E. Testing agent brief (FIRST; tests only)
Write failing tests for A, B, C, D from this document. Touch only `tests/` (and you may keep `tests/test_review_gaps.py` merged or as is). Use the repo's `.venv/bin/python`; no network; no API key; no real `.env`; do not commit or change git state; the git "dubious ownership" workaround is `export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/workspace`. Confirm every new test fails for the right reason (missing feature, not a typo) and that the existing 90 tests otherwise still pass. Report the test list and any ambiguity in this contract.

## F. Coding agent brief (AFTER Claude reviews the tests)
Implement A, B, D (scanner, hooks, install script, `.gitignore`) so all tests pass, without editing tests. Report contract ambiguities to Claude instead of guessing. Do not commit. Do not run `scripts/install_hooks.sh` (Claude does that after review).

## G. Verification
Independent testing-agent pass (mutation testing of the new code: e.g. remove each scanner rule, skip the key-collision check, accept `*`, skip a hook), then Claude reviews and commits "Step 1 fixes" with the hooks active.
