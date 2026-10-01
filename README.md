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

## 1. Requirements and setup

You need Python 3.13 in a virtual environment at `.venv` (the scripts and git hooks call `.venv/bin/python`
directly) and, for the browser-script tests only, optionally Node.js.

```
python3.13 -m venv .venv                                  # or any way that puts Python 3.13 at .venv
.venv/bin/python -m pip install -r requirements-dev.txt   # everything, including the test tools
cp .env.example .env                                      # then edit .env (see below)
.venv/bin/python manage.py migrate                        # creates db.sqlite3
.venv/bin/python manage.py seed_topics                    # optional: the six seeded propositions
.venv/bin/python manage.py createsuperuser                # optional: an admin login for /admin/
scripts/install_hooks.sh                                  # once per clone: turns on the secret-scanning git hooks
```

- `requirements.txt` holds what the site needs to run (Django, anthropic, pydantic, argon2-cffi, django-axes,
  python-dotenv), pinned to exact versions. `requirements-dev.txt` includes it and adds pytest, pytest-django,
  pandas and numpy. `.python-version` says `3.13`.
- The database is SQLite in WAL mode (`db.sqlite3` in the project folder). `manage.py migrate` creates it.
- `seed_topics` is safe to run again: it creates or updates the seeded topics and never deletes anything.

### The `.env` file

`.env` holds secrets and per-machine values. **It is never committed** (`.gitignore` blocks it and the git hooks
refuse it). Real environment variables win over values in `.env`. `manage.py`, `wsgi.py` and `asgi.py` load it;
`config/settings.py` does not, so the tests never depend on your local `.env`. A blank value counts as not set. The
variables, all listed in `.env.example`:

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key. Needed only for real moderation. Create it inside a Console workspace that has a spend limit (section 4). |
| `DJANGO_ENV` | `development` (default) or `production`. A deployed site must say `production`, which turns on strict checks: a secret key of at least 50 characters, an explicit host list, secure cookies. |
| `DJANGO_SECRET_KEY` | Django's secret key. Required in production. In development a public placeholder is used if it is blank. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names. Required in production; `localhost`, `127.0.0.1` and `[::1]` are allowed by default in development. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated `https://...` origins, only if the site is served from another origin than its host name. |
| `DJANGO_DB_PATH` | Where the SQLite file lives. Default `db.sqlite3` in the project folder. A relative path is relative to the project folder, and its folder must already exist. |
| `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL` | The mail backend (default: print to the terminal) and sender address. Only the circuit-breaker alert uses email (section 4). |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Only used when `EMAIL_BACKEND` is the SMTP backend; left out of the mailer's options entirely when blank. See `.env.example` for a worked example. |
| `ALERT_EMAIL` | The address that gets one email when the AI moderator pauses itself. Blank means no email; the pause is only logged. Put your own address in `.env`, never in a tracked file. |

The kill switch `LLM_ENABLED` is **not** in `.env`; it is in `config/tunables.py` (section 3).

## 2. Running it

```
scripts/dev.sh
```

This starts two processes and prefixes their output with `[web]` and `[worker]`: the web server
(`manage.py runserver`) and the moderation worker (`manage.py run_moderator`). Ctrl-C, or either process ending,
stops the other one. Open **http://127.0.0.1:8000/** (Django's default address; `localhost` also works).

- `dev.sh` needs **bash 4.3 or newer** because it uses `wait -n`. The bash that ships with macOS is older (3.2) and
  will not work. Install a newer bash (for example with Homebrew) and run the script with it.
- At start it prints a reminder that moderation runs are recorded as skipped while `LLM_ENABLED` is off (the default).
- You can also run the two processes yourself in two terminals: `manage.py runserver` and `manage.py run_moderator`.
  (`MODERATION_RUN_MODE = "sync"` in `config/tunables.py` runs the moderator inside the web request instead; the
  tests use it, and `run_moderator` refuses to start in that mode.)

### Accounts

- Registration is a **username and a password only**; there is **no email** anywhere. Registering creates an active
  account and logs you in at once. Pages: `/accounts/register/`, `/accounts/login/`, `/accounts/logout/`,
  `/accounts/password-change/`.
- Usernames are 3 to 30 characters: ASCII letters, digits, `_` and `-`. Two names that differ only in case
  count as the same name. Passwords need at least 12 characters, must not be common, all
  digits or similar to the username, and are stored only as salted Argon2 hashes.
- After 5 failed logins the username, or the IP address, is locked out for 15 minutes (django-axes). Sessions last 30
  days. `manage.py axes_reset` clears all lockouts.
- **There is no password reset by email.** If someone forgets their password, the owner resets it:
  `.venv/bin/python manage.py changepassword <username>`.
- The Django admin is at `/admin/` (log in with a `createsuperuser` account). It shows runs, LLM calls, issues,
  acts, users and conversations, and can hide or unhide a proposition.

### What people can do on the site

The home page lists the positions that someone is waiting for a person to disagree with. You take the other side,
or start something new on the "propose" page (write your own position, or pick a seeded topic and a side). Two
people on opposite sides of one proposition make a conversation. Your own conversations are on "Your discussions".
You can block a person from a conversation or a waiting card; blocking ends any conversation you share. Limits, all
in `config/tunables.py` and enforced on the server: 3,000 characters per message, one message every 30 seconds per
person, 30 user messages per conversation (then it closes), 200 characters per proposition, 20 new propositions per
person per day. Whenever someone cannot post, the page says why and what to do next. The moderator sees only
"Participant A" and "Participant B" (assigned at random), never usernames or which side someone holds.

### When `LLM_ENABLED` is off (the default)

No Anthropic call is ever made. The site works normally: people post and read messages. Each message still gets a
moderation run, but the run is recorded as `skipped_disabled`, nothing is posted by the moderator, and the
conversation page says AI moderation is switched off and that messages are still posted. To get real moderation you
must set `ANTHROPIC_API_KEY` in `.env`, set `LLM_ENABLED = True` in `config/tunables.py`, and restart the site and the
worker. Do this only after setting the Console spend limit (section 4).

## 3. Tunables: where every number lives

**Every number you might want to change is in `config/tunables.py`, and nowhere else.** Each has a comment. Edit the
file and restart the web server and the worker. A test fails if a tunable is assigned anywhere else. Secrets are the
opposite: they live only in `.env`.

The most important ones (current values are in the file; read it for the truth):

| Group | Names |
|---|---|
| Kill switch | `LLM_ENABLED` (False by default) |
| Money caps (USD) | `BUDGET_SITE_USD_TOTAL`, `BUDGET_SITE_USD_PER_DAY`, `BUDGET_PER_CONVERSATION_USD`, `BUDGET_EVAL_USD_TOTAL`, `BUDGET_SPIKE_USD_TOTAL` |
| Models | `MASTER_MODEL`, `INTERVENOR_MODEL`, `SPIKE_MODEL`, `JUDGE_MODEL_SEEDED` |
| Message and traffic limits | `MAX_MESSAGE_CHARS`, `MIN_SECONDS_BETWEEN_MESSAGES`, `MAX_USER_MESSAGES_PER_CONVERSATION`, `MAX_OPEN_CONVERSATIONS` (None = no limit), `MAX_PROPOSITION_CHARS`, `MAX_PROPOSITIONS_PER_USER_PER_DAY` |
| What the moderator gets | `TRANSCRIPT_MAX_MESSAGES` (only the newest messages are sent), `MAX_ACTS_PER_INTERVENTION`, `AGREEMENT_MAP_EVERY_N_USER_MESSAGES` (the agree/disagree note fires after every N-th user message; 0 turns it off), `MASTER_MAX_TOKENS`, `INTERVENOR_MAX_TOKENS` |
| Circuit breaker | `BREAKER_MAX_CONSECUTIVE_ERRORS`, `BREAKER_ERROR_WINDOW_SECONDS`, `BREAKER_COOLDOWN_SECONDS`, `BREAKER_COOLDOWN_MAX_SECONDS`, `ALERT_MIN_SECONDS_BETWEEN_EMAILS` |
| Intervention preview | `PREVIEW_SHARE` (share of new conversations that get it: 1.0 = all, 0.0 = none), `PREVIEW_MAX_CHECKS_PER_MINUTE`, `PREVIEW_REUSE_SECONDS`, `PREVIEW_CLIENT_TIMEOUT_SECONDS` |
| Worker | `MODERATION_RUN_MODE`, `WORKER_POLL_SECONDS`, `RUN_TIMEOUT_SECONDS`, `RUN_MAX_ATTEMPTS`, `POLL_SECONDS` |
| Accounts | `USERNAME_MIN_LENGTH`, `USERNAME_MAX_LENGTH`, `PASSWORD_MIN_LENGTH`, `SESSION_COOKIE_AGE`, `LOGIN_MAX_FAILURES`, `LOGIN_COOLOFF_MINUTES` |
| Evaluation (phase B) | `INTENSITY_ERROR_THRESHOLD`, `REPLAY_MESSAGE_GAP_SECONDS` |

The plan's section 3.3 lists older default dollar caps; `config/tunables.py` is what the code uses.

## 4. Cost controls (you pay for the API)

In plain words, in the order a call meets them:

1. **Kill switch.** With `LLM_ENABLED = False` (the default), or no API key, no call is made.
2. **One gate.** Every model call goes through one function, `moderation/llm.py: call()`. It is the only file that
   imports the Anthropic SDK (a test enforces this).
3. **Circuit breaker.** After a spend-limit, authentication or billing error, all calls stop until you run
   `manage.py reset_breaker` (a "hard" trip). After `BREAKER_MAX_CONSECUTIVE_ERRORS` ordinary API errors in a row
   within `BREAKER_ERROR_WINDOW_SECONDS`, calls pause and one probe call is tried after a cooldown that doubles up to a
   limit (a "soft" trip). If `ALERT_EMAIL` is set, a trip sends one email. The SDK retries a failed request at most
   once (`LLM_MAX_RETRIES`).
4. **Budget check before every call.** The worst-case cost of the call (estimated input plus `max_tokens` of output)
   is added to what has been spent, and the call is refused if it would pass any cap that applies:
   - **per conversation** (`BUDGET_PER_CONVERSATION_USD`, moderation calls only),
   - **per day** for the site (`BUDGET_SITE_USD_PER_DAY`, UTC day),
   - **site total**, all time (`BUDGET_SITE_USD_TOTAL`; moderation calls and the historical spike and golden-set purposes),
   - **spike total** (`BUDGET_SPIKE_USD_TOTAL`) for the historical `spike` purpose (the `spike` command was removed on 2026-10-01),
   - **evaluation total**, kept separate (`BUDGET_EVAL_USD_TOTAL`; replays and judges),
   - **the command's own `--max-usd`** for `replay`.
   A refused moderation run is recorded as `skipped_budget` and the page says moderation is paused. If the spending
   record cannot be read, no call is made (fail closed). An unknown model is never called.
5. **Bounded input.** At most `MAX_MESSAGE_CHARS` per message, only the newest `TRANSCRIPT_MAX_MESSAGES` messages are
   sent, output is limited by `max_tokens`, and the fixed instructions are sent with prompt caching.
6. **Traffic limits.** The 30-second gap, the 30-message cap per conversation and the daily proposition cap.
7. **Checked before you run.** `replay` has `--dry-run`, which makes no call and
   prints the number of calls and a worst-case cost. `replay` never spends real money unless you
   pass `--live`, `--max-usd`, and type `yes` (or `--yes`).

**The Anthropic Console spend limit is the outer backstop, and it is yours to set** (`docs/plan.md`, section 3.1):
create a dedicated workspace (the Default Workspace cannot have limits), set a monthly spend limit there (the plan
uses $10), create the API key inside that workspace, and consider an organization limit under Billing as a second
backstop. Anthropic does not promise to cut off to the cent or in real time, so the in-app caps come first. Set the
Console limit **before** turning on `LLM_ENABLED`, and check it again from time to time.

**How to check spend:** `.venv/bin/python manage.py budget` (read-only). It prints the path of `config/tunables.py`,
whether the kill switch is on, whether an API key is configured, the breaker state, each cap with spent and remaining,
the top conversations by spend, stale pending calls, and the worst-case cost of one moderation run per model.

## 5. One message, end to end

A person types a message in a conversation and presses Post. Code paths are named so you can open them.

1. **Post.** The page form posts to `c/<id>/post/` (`forum/views.py: post`), which calls
   `forum/services.py: post_message()`. That is the only way a user message is created. In one database transaction it
   checks the posting rules (`validate_draft`: participant, conversation not closed, not empty, not over
   `MAX_MESSAGE_CHARS`, not too fast, conversation not full), saves the `Message` (`author_type = "user"`) and creates
   one `ModerationRun` (`kind = live`, `status = pending`, `snapshot_seq` = the message's number). If any rule fails,
   nothing is saved, no run is created, and the page shows the reason with the person's text kept. Posting a
   moderator message never creates a run, so the moderator never replies to itself.
2. **Worker claim.** `manage.py run_moderator` runs `moderation/worker.py`. `claim_next_run` takes the oldest pending
   live run whose conversation has no run in progress, with a single conditional update to `running`, so two workers
   can never take the same run. A reaper puts runs stuck longer than `RUN_TIMEOUT_SECONDS` back to `pending`, or fails
   them after `RUN_MAX_ATTEMPTS`. A crash in one run marks it failed and the worker carries on.
3. **Pipeline.** `moderation/pipeline.py: run_moderation(run)` first re-checks that the trigger is a user message
   (otherwise `failed` with `invalid_trigger`, no call, nothing posted). It then builds the transcript: the last
   `TRANSCRIPT_MAX_MESSAGES` messages up to `snapshot_seq`, with only the labels "Participant A/B" and the text.
4. **Master Moderator.** `moderation/agents.py: call_master` calls `llm.call()` (kill switch, breaker, model list,
   caps, ledger row, then the real request). Output that does not match the schema is retried once; a second failure
   ends the run as `failed` with nothing posted. The Master returns problem items: an issue type, the exact phrase, an
   explanation, a confidence and, for some types, an intensity from 0 to 4.
5. **Validation, per item.** Each issue is checked and stored as `valid` or `rejected` with a reason (for example
   `quote_not_found`, `not_new`, `moderator_message`). A bad item is rejected on its own; the rest of the run is kept.
6. **Intervenor.** If there is no valid issue, the run ends as `done` with `no_intervention` and the Intervenor is
   never called. Otherwise `call_intervenor` decides whether to step in and proposes up to
   `MAX_ACTS_PER_INTERVENTION` acts. A disposition (`acted` or `declined`) is stored for each valid issue, and each act
   is validated (for example, text that names a participant label is rejected).
7. **Moderator message.** `_finish` writes, in one short transaction, a `Message` with `author_type = "moderator"`
   (the acts' text joined together, replying to the trigger message) and marks the run `done`. If a newer user
   message arrived meanwhile, the run is flagged `is_stale`. Replays never post.
8. **Polling.** In the browser, `poll.js` asks `c/<id>/messages/?after=<number>` every `POLL_SECONDS`. The server
   returns the newer messages, and the moderator's message appears with a heading worked out for the reader ("About
   your message 4", "For both of you"). Readers see "You" and "The other participant", never A/B labels. If the run
   was skipped or failed, the page shows a plain notice that moderation is paused, switched off or had a problem, and
   that messages are still posted.

**The preview check (before step 1).** When a conversation has the preview on (`PREVIEW_SHARE`, decided once per
conversation and stored) and the browser has JavaScript, pressing Post first sends the draft to `c/<id>/check/`
(`forum/views.py: check`). It applies the same posting rules without saving anything, then
`moderation/preview.py: check_draft` runs the same Master and Intervenor on the draft (the draft stands in as the
newest message), limited to `PREVIEW_MAX_CHECKS_PER_MINUTE` per person. It writes only a `PreviewCheck` row (plus the
conversation's stored mode the first time, and the usual ledger rows), never a message or a run. If there is no concern, or the check is unavailable or slow (`PREVIEW_CLIENT_TIMEOUT_SECONDS`),
the message posts as normal. If there is a concern the author sees the note the moderator would post and chooses
"Edit my message" or "Post as written". Posting as written follows steps 1 to 8, except that in step 4
`claim_reusable_check` finds the stored check of the same text (made within `PREVIEW_REUSE_SECONDS`, with nothing
posted in between) and reuses its outputs instead of calling the model again (so the reply is the one that was previewed). Without JavaScript there is no check.

## 6. Management commands

Run with `.venv/bin/python manage.py <command>`; `--help` on any of them lists everything. Options below are the main
ones. "Real API calls" only happen when the kill switch is on and a key is set (`replay` needs
`--live`, see section 4).

**Site**

| Command | Purpose and main options |
|---|---|
| `seed_topics` | Create or update the seeded propositions from `forum/seed_topics.json` (idempotent, keyed by title). `--dry-run` prints changes without writing; `--file FILE` uses another seed file. |
| `run_moderator` | The moderation worker (`scripts/dev.sh` starts it). `--once` processes what is claimable then exits; `--max-runs N`; `--poll-seconds S`. Refuses to start when `MODERATION_RUN_MODE` is `sync`. |
| `budget` | Read-only report of caps, spend, breaker state and worst-case run cost (section 4). No options of its own. |
| `reset_breaker` | Close the circuit breaker after a hard trip and say what had tripped it. Before re-enabling after a spend-limit trip, confirm the Console limit. |
| `export_conversation ID` | One conversation as JSON: messages, runs, issues, acts, LLM calls and costs. Usernames and emails only with `--include-identities`; raw prompts and replies only with `--include-raw`; `--output PATH` instead of the terminal. |
| `export_all --output-dir DIR` | One `conversation_<id>.json` per conversation plus `index.json`. Also `--include-identities`, `--include-raw`, filters `--source`, `--status`, `--experiment`, and `--force` to overwrite. |

**Prompt trial and evaluation (real API calls when enabled; always start with `--dry-run`)**

| Command | Purpose and main options |
|---|---|
| `replay --experiment NAME` | Load golden transcripts as synthetic conversations and replay the moderation pipeline on them (never posts). `--dry-run`; `--directory DIR`; `--set PATTERN` (repeatable); `--assignments {as-is,swapped,both}`; `--replicates N`; `--max-usd N`; `--live`; `--yes`. Without `--live` every run is recorded as `skipped_disabled` and costs nothing. |
| `generate_conversations` | Generate the seeded-error debates: two base conversations per fact (left and right), then the true and false-claim versions, written to `generated/`. `--facts IDS`; `--output-dir DIR`; `--overwrite`; `--max-usd N`; `--dry-run`; `--live`; `--yes`. |
| `judge_responses --experiment NAME` | Have one LLM judge tag each moderator response to a seeded claim (0 missed, 1 spotted, 2 wrong correction, 3 correct, N/A) and write one JSON Lines file per experiment under the `generated/judgments` folder. `--max-usd N`; `--overwrite`; `--dry-run`; `--live`; `--yes`. |
| `summarize_pilot --experiment NAME` | Turn the judged results into a preliminary summary table, printed and written to a markdown file in the `generated` folder. `--output PATH`. |
| `run_research_eval --experiment NAME` | Run the web-search research step on every replayed conversation where the moderator posted an eligible act (one per conversation), and keep the notes. `--set PATTERN` (repeatable); `--retry-failed`; `--max-usd N`; `--dry-run`; `--live`; `--yes`. |
| `judge_research --experiment NAME` | Have one LLM judge tag each research note against the true fact and say whether it confirms or disputes the claim. `--max-usd N`; `--overwrite`; `--dry-run`; `--live`; `--yes`. |
| `summarize_research --experiment NAME` | Turn the judged research notes into a preliminary summary table, printed and written to a markdown file in the `generated` folder. `--output PATH`. |

**Built into Django and its add-ons, used here:** `migrate`, `createsuperuser`, `changepassword <username>`, `check`,
`axes_reset` (clear login lockouts), `runserver`.

(The plan also mentions a `golden` command; it is not built yet.)

## 7. Tests

```
.venv/bin/python -m pytest                     # everything
.venv/bin/python -m pytest tests/worker        # one folder
.venv/bin/python -m pytest -k scanner          # by name
```

- `pytest.ini` points at `tests/`. Tests never touch the network: a fake LLM stands in for Anthropic, and a test fails
  if the real client is constructed. They use a throwaway test database, not `db.sqlite3`. Nothing in the tests needs
  your `.env`.
- There are over 9,000 tests (9,188 collected when this was written). A full run took about 5 minutes when this was written (4 min 44 s on one machine, while other work was
  running); use a folder or `-k` for quick feedback. The slower ones start subprocesses or threads (worker race tests, `dev.sh` tests, concurrency and
  migration tests), or run the browser scripts (`compose.js`, `poll.js`) in Node; those skip themselves if Node is not
  installed.
- **Secret-scanner tests** (`tests/test_secret_*.py`, `tests/test_review2_scanner_*.py`, `tests/test_repo_hygiene.py`)
  check the scanner, the hooks, `.gitignore`, and that the repository itself is clean. They build small throwaway git
  repositories to try the hooks. Run just these with
  `.venv/bin/python -m pytest tests/test_secret_scanner.py tests/test_secret_cli_and_hooks.py tests/test_secret_repo_protection.py tests/test_review2_scanner_and_hooks.py tests/test_repo_hygiene.py`.
- Some tests scan the source (for example: only `moderation/llm.py` imports `anthropic`; tunables are assigned only in
  `config/tunables.py`; prompts never receive usernames), so a change that breaks a design rule fails a test.
- Tests that use the real API are never part of `pytest`; they are the commands `replay` and the `evaluation/` commands,
  each with its own `--max-usd`.

## 8. Security rules

- **Never commit a key or secret.** Keys live in `.env`, which is git-ignored (along with `.env.*` except
  `.env.example`, `*.pem`, `*.key`, `secrets/` and the SQLite files). Do not paste keys into code, tests, docs or
  commit messages. Never print or share `.env`.
- **Git hooks.** Run `scripts/install_hooks.sh` once in each clone. It sets `core.hooksPath` to `.githooks`. The
  `pre-commit` hook scans the staged content with `scripts/check_secrets.py --staged`; the `pre-push` hook scans the
  whole history with `--history`. The scanner (standard library only) finds Anthropic and other provider keys,
  private-key blocks, URLs with passwords, bearer tokens and `password = "..."`-style assignments, and never prints a
  whole secret. `--all` scans every tracked file. Exit code 0 means clean.
- **The marker `# secret-scan: allow`.** A line that contains the text `secret-scan: allow` (in any comment style) is
  skipped by the scanner. Use it only for a value that is deliberately public or fake, such as the placeholder
  development key in `config/settings.py` or a fake key in a test that checks the scanner. **Never** use it to get
  a real key past the hooks.
- Hooks can be skipped with `git commit --no-verify`; do not. GitHub's own secret scanning and push protection are the
  real backstop once there is a remote, and a key that was ever committed or pushed must be **revoked** in the
  provider's console straight away (deleting it in a later commit does not remove it from history).
- **Passwords** are stored only as salted Argon2 hashes. Sessions are HttpOnly and SameSite=Lax; cookies are Secure in
  production. CSRF protection is on.
- **Privacy.** The moderator, the judge and the prompts never see usernames, emails, sides or political labels.
  Exports include usernames only with `--include-identities`.

## 9. Repository map

```
manage.py                 Django command line (loads .env)
config/                   settings.py, tunables.py (every number), urls.py, wsgi.py, asgi.py
accounts/                 the User model, registration, login, password change, login-throttling hooks, templates
forum/                    the site: models, services.py (post_message and friends), views, viewmodels, limits.py,
                          templates/, static/forum/ (compose.js, poll.js, site.css), seed_topics.json, admin
moderation/               the AI moderator and its guard rails:
                          llm.py (the one gate to Anthropic), budget.py, pricing.py, breaker.py,
                          taxonomy.py, schemas.py, prompts/ (master_v1.md, intervenor_v1.md),
                          agents.py, pipeline.py, worker.py, preview.py, quotes.py, features.py,
                          queries.py, replay.py, transcripts.py, fake_llm.py, models.py, admin.py, management/commands/
evaluation/               not deployed: only the seeded-error management commands (no tables or models)
analysis/                 metrics.py: bias metrics as pure functions on tables (pandas)
rubrics/                  factual_accuracy_v1.md, abusiveness_v1.md, clarity_v1.md (used word for word by the judge)
golden/                   transcripts/ (three scripted transcripts kept as replay fixtures)
scripts/                  dev.sh, install_hooks.sh, check_secrets.py, make_schema_doc.py,
                          make_text_inventory.py
.githooks/                pre-commit and pre-push secret scans
tests/                    the test suite, one folder per area
docs/                     plan, summary, neutrality criteria, briefs, schema, user-facing text
```

## 10. Documentation

- `docs/plan.md` - the full design and build plan (source of truth). Older versions `docs/archive/plan_v1.md` to `plan_v4.md` are
  history only.
- `docs/plan_summary.md` - a short summary of the plan for the owner.
- `docs/neutrality.md` - the owner's own neutrality criteria, the standard every moderation and evaluation design must
  meet.
- `docs/step*_brief.md` and `docs/warmup_brief.md` - the written contracts handed to the building and testing agents
  for each step. `docs/test_audit_plan.md` describes the planned final review of the tests.
- `docs/database_schema.md` (and `.mmd`) - every table and column, generated by `scripts/make_schema_doc.py` from a
  scratch database (never `db.sqlite3`); do not edit by hand.
- `docs/user_facing_text.md` - every piece of text a participant can see, with where it comes from, generated by
  `scripts/make_text_inventory.py`.

## 11. Known limits and what is deliberately not built yet

- **Real email sending.** The site still has no email at all for accounts (no verification, no password reset by
  email). The circuit-breaker alert can now send real email if `EMAIL_BACKEND`/`EMAIL_HOST`/etc. are set to a real
  SMTP provider (see `.env.example`); the default remains printing to the terminal. Whether to add account email
  (recovery, confirmation) is a separate, still-open deployment decision.
- **HTTPS and hosting.** Nothing is deployed. Passwords must only travel over HTTPS outside your own machine, so this
  is needed before anyone else uses the site. `manage.py check --deploy` currently reports the console mail backend as an error and the HTTPS, HSTS and
  secure-cookie settings as warnings (with `DJANGO_ENV` unset).
- **The annotation page for human raters.** There is no page or access model for
  human raters yet.
- **The calibration report** (human-human and LLM-human agreement, side symmetry, the positive control and the noise
  floor) is not built, nor is the pre-registered analysis (`analysis/prereg.md`). 
- **The test transcripts are unaudited.** The three scripted conversations in `golden/transcripts/` (the rest were removed on 2026-10-01) are accepted only as scaffolding to
  test the machinery. Until the owner has audited them (plan section 19), no result from them is evidence about bias.
- **GitHub.** Plan section 17 lists what to do before the repository is pushed or made public (keep it private,
  turn on push protection, clean the local history, choose the commit e-mail address). Push only with the owner's approval.
- **Cost wording.** By design, pages never mention API costs or budgets to users; a pause is described only as
  "moderation is paused".
