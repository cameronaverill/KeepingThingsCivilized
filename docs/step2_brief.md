# Step 2 brief: LLM gateway and budget guard

Status: DRAFT for the user's approval. Nothing has been built. When approved, the two agents work from this file, then `docs/plan.md` is updated to match (section 8).

Goal (plan section 14, step 2): every call to Claude goes through one function that refuses anything unsafe or over budget, logs everything, and prices every call. No real API call is made in this step.

## 1. Decisions locked in by the user
- The guard allows exactly two models: `claude-sonnet-5` and `claude-haiku-4-5`. Any other model is refused.
- Caps: per conversation **$1.25**, per day **$1.50**, site total **$5.00**, evaluation total **$10.00** (separate). The "day" is a UTC day.
- The user reviews this brief before any agent starts. Order of work: testing agent writes failing tests, Claude reviews them, coding agent implements, testing agent verifies independently, Claude reviews, the user approves, then commit.

## 2. Architecture decisions (Claude's; each has a reason)
1. **Spend history is never deleted with its context.** `LLMCall` keeps plain nullable integer columns `conversation_id` and `run_id`, not foreign keys. If a conversation is ever deleted, the cost records stay, so the budget can't shrink. (Plan section 8 said FK; step 4 will not add one.)
2. **Reserve before you call.** Inside one `transaction.atomic()` (SQLite uses IMMEDIATE, so this serializes web, worker and commands): compute the worst-case cost, check every applicable cap counting in-flight calls, insert an `LLMCall` row with status `pending` and `reserved_usd`. After the call the row becomes `ok` or `error` with the real `cost_usd`. Pending rows count at their reserved amount, so two callers can't both squeeze under a cap.
3. **Worst case uses the cache-write price for all input** (1.25x base for the 5-minute cache), plus `max_tokens` of output at the output price. Input tokens are estimated locally (no network): `ceil(chars / TOKEN_ESTIMATE_CHARS_PER_TOKEN) + TOKEN_ESTIMATE_OVERHEAD_TOKENS`, deliberately over-counting. Anthropic's token-count endpoint is not used (it is a real network call).
4. **Fail closed.** Unknown model, ledger unreadable, breaker open, kill switch off, missing API key: no call is made.
5. **Exceptions are typed** (`moderation/errors.py`) and only `llm.py` imports `anthropic`. SDK exceptions are normalized inside `llm.py`. The fake client raises a plain `FakeProviderError` with the same attributes, so `fake_llm.py` never imports `anthropic`.
6. **Prompt caching is on (automatic caching) but never counted on.** It lasts 5 minutes, Haiku 4.5 needs a 4,096-token prefix, and the Master and Intervenor have different system prompts. Budgets assume no cache hits.
7. **Circuit breaker resets by hand only** (`manage.py reset_breaker`).
8. **Structural failures are the pipeline's job.** The gateway makes one attempt per call and records `attempt`. Retrying once on invalid output is step 5.

## 3. Interface contract (tests and code must both match this exactly)

### `moderation/errors.py`
- `LLMRefused(Exception)` with class attribute `status` (the `LLMCall.status` value it is logged with). Subclasses: `LLMDisabled` (`refused_disabled`; also used for a missing API key), `BreakerOpen` (`refused_breaker`), `ModelNotAllowed` (`refused_model`), `BudgetExceeded` (`refused_budget`; attributes `cap_name`, `limit`, `spent`, `requested`).
- `BudgetUnavailable(Exception)`: the ledger could not be read.
- `LLMAPIError(Exception)`: provider call failed; attributes `status_code`, `error_type`, `error_code`, `message`, `call_id`.
- `LLMOutputError(Exception)`: the call succeeded but the output is unusable (truncated at `max_tokens`, refusal, invalid JSON, fails the schema); attributes `reason`, `stop_reason`, `call_id`. The call is still billed and logged `ok`.

### `moderation/clock.py`
- `now()` returns `django.utils.timezone.now()`. All step 2 code calls `moderation.clock.now()` so tests can patch time.

### `moderation/pricing.py`
- `ModelPrice` (frozen dataclass of `Decimal` USD per million tokens): `input`, `output`, `cache_write_5m`, `cache_write_1h`, `cache_read`.
- `ALLOWED_MODELS: dict[str, ModelPrice]` with exactly:
  - `claude-sonnet-5`: 2, 10, 2.50, 4, 0.20
  - `claude-haiku-4-5`: 1, 5, 1.25, 2, 0.10
- `PRICES_VERIFIED_ON = "2026-09-25"`, `PRICES_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"`.
- `get_price(model)` raises `ModelNotAllowed` for anything else.
- `compute_cost(model, *, input_tokens=0, output_tokens=0, cache_write_tokens=0, cache_read_tokens=0) -> Decimal`: USD, rounded UP to 6 decimal places. Cache writes are priced at the 5-minute rate.

### `moderation/models.py`
- `LLMCall`: `purpose` (`moderation`|`spike`|`golden`|`replay`|`judge`), `run_id`, `conversation_id` (nullable integers, indexed), `agent`, `attempt` (default 1), `provider` (default `anthropic`), `model`, `prompt_version`, `prompt_sha256`, `temperature` (nullable), `max_tokens`, `request` (JSON: exactly what was sent, with the output schema as JSON schema, never the API key), `raw_response` (text), `parsed` (JSON, nullable), `tokens_in`, `tokens_out`, `cache_write_tokens`, `cache_read_tokens`, `reserved_usd`, `cost_usd` (nullable until finished), `latency_ms`, `stop_reason`, `provider_request_id`, `status` (`pending`|`ok`|`error`|`refused_budget`|`refused_breaker`|`refused_disabled`|`refused_model`), `error`, `error_code`, `created_at`, `finished_at`. Money fields are `DecimalField(max_digits=12, decimal_places=6)`.
- `GuardState`: one row (`pk=1`): `breaker_tripped`, `tripped_at`, `trip_reason` (`spend_limit`|`consecutive_errors`|`manual`), `trip_detail`, `consecutive_errors`, `last_error_at`. `GuardState.load()` returns the row, creating it if needed. A second row must be impossible.

### `moderation/budget.py`
- `SITE_PURPOSES = ("moderation", "spike", "golden")`, `EVAL_PURPOSES = ("replay", "judge")`.
- `SessionBudget(limit_usd)`: an in-memory cap for one command (`--max-usd`); attributes `limit`, `spent` (includes reservations).
- `estimate_input_tokens(*, system, messages, schema=None) -> int`.
- `reservation_usd(model, *, estimated_input_tokens, max_tokens) -> Decimal`.
- `spend(*, purposes=None, since=None, until=None, conversation_id=None) -> Decimal`: ledger sum. `ok`/`error` count at `cost_usd`, `pending` at `reserved_usd`, `refused_*` never.
- `check_caps(*, purpose, conversation_id, amount, session=None)`: raises `BudgetExceeded`. Caps: site purposes against `BUDGET_SITE_USD_TOTAL` (all time) and `BUDGET_SITE_USD_PER_DAY` (current UTC day); eval purposes against `BUDGET_EVAL_USD_TOTAL`; purpose `moderation` with a `conversation_id` also against `BUDGET_PER_CONVERSATION_USD`; plus the session budget if given.
- `worst_case_run_cost() -> dict`: per model in use, the reservation-basis cost of one Master call and one Intervenor call at `TRANSCRIPT_MAX_MESSAGES` messages of `MAX_MESSAGE_CHARS` characters plus `SYSTEM_PROMPT_TOKENS_ESTIMATE`, and their sum.

### `moderation/breaker.py`
- `is_open() -> bool`, `record_success()`, `record_error(*, status_code, error_type, error_code, message)`, `trip(reason, detail="")`, `reset()`.
- Trips immediately on a spend-limit error: HTTP 400 `invalid_request_error` whose message starts "You have reached your specified", or HTTP 429 with `error_code == "enforced_spend_limit_reached"`. Otherwise trips after `BREAKER_MAX_CONSECUTIVE_ERRORS` errors in a row, where an error more than `BREAKER_ERROR_WINDOW_SECONDS` after the previous one starts the count again. A success resets the count. State lives in `GuardState`, so it survives restarts.

### `moderation/llm.py` (the ONLY module that imports `anthropic`)
- `call(*, purpose, agent, model, system, messages, output_schema, max_tokens, prompt_version="", attempt=1, temperature=None, conversation_id=None, run_id=None, session=None) -> LLMResult`, where `output_schema` is a Pydantic model class and `LLMResult` has `parsed`, `call_id`, `stop_reason`, `input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `cost_usd`, `latency_ms`.
- Order of checks: unknown purpose (`ValueError`) → kill switch and API key → breaker → model allowed → ledger + caps (with reservation row). Every refusal writes a row with the matching `refused_*` status, makes no client call, and raises the matching `LLMRefused` subclass. If the ledger cannot be read, raise `BudgetUnavailable` and make no call.
- After a provider failure: row becomes `error` (with `error_code`), `breaker.record_error`, raise `LLMAPIError`. After a success: cost computed from real usage, `breaker.record_success`, session spend updated. Truncation, refusal or invalid output raises `LLMOutputError` (row `ok`, billed, `stop_reason` recorded).
- `prompt_sha256` = sha256 of the system prompt plus the output schema's JSON.
- Client access: `get_client()`, `set_client(client)`, `reset_client()`. The real client is built only by `_build_real_client()` with `api_key=settings.ANTHROPIC_API_KEY` and `max_retries=settings.LLM_MAX_RETRIES`.
- The coding agent must verify against the installed SDK source (anthropic 1.8.0) and Anthropic's docs, and record the answers in code comments: the exact call for structured output (`client.messages.parse(... output_format=...)`), how to enable automatic caching, whether `temperature` is accepted by both models, and whether extended or adaptive thinking is on by default (moderation calls must not run with thinking unless explicitly set, since it bills as output).

### `moderation/fake_llm.py`
- `FakeLLM(script)`: `client.messages.parse(**kwargs)` pops the next scripted item. An item is a dict (validated into the schema and returned), a `FakeProviderError`, or a callable `(kwargs) -> message`. It records every call in `.calls` and returns objects with `.parsed_output`, `.stop_reason`, `.usage` (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) and `.id`. Helpers: `make_message(parsed, *, input_tokens=..., ...)`.
- `FakeProviderError(status_code, error_type, message, error_code=None)`.

### Management commands (`moderation/management/commands/`)
- `budget`: prints the path of `config/tunables.py`, kill switch state, breaker state, the four caps, spend so far (site total, today, per conversation top 5, eval), remaining amounts, pending calls older than `PENDING_CALL_STALE_MINUTES`, calls whose real cost exceeded their reservation, and the worst-case cost of one moderation run per model. Read-only, exit code 0.
- `reset_breaker`: clears the breaker, prints what was tripped and why, and says to confirm the Console spend limit first if the reason was `spend_limit`.

### `config/tunables.py` changes
- `BUDGET_SITE_USD_PER_DAY = Decimal("1.50")`, `BUDGET_PER_CONVERSATION_USD = Decimal("1.25")` (site total stays 5.00, eval 10.00).
- New, each with a comment: `TOKEN_ESTIMATE_CHARS_PER_TOKEN = 2.5`, `TOKEN_ESTIMATE_OVERHEAD_TOKENS = 1000`, `SYSTEM_PROMPT_TOKENS_ESTIMATE = 6000` (a placeholder until step 3 measures the real prompts), `PENDING_CALL_STALE_MINUTES = 15`.
- `moderation` is added to `INSTALLED_APPS`.

## 4. Testing agent brief (runs FIRST; writes tests, no implementation)
Write failing tests from section 3 (they must fail because the code doesn't exist yet, not because of typos). Use only the fake client. Files and coverage:
- `tests/conftest.py`: autouse fixture making `moderation.llm._build_real_client` raise an `AssertionError` ("real client built in tests"), plus a fixture installing a `FakeLLM`; a fixture patching `moderation.clock.now`.
- `test_pricing.py`: exactly two allowed models with the exact prices; unknown model refused; `compute_cost` for input/output/cache-write/cache-read, rounding up, zeros.
- `test_budget.py`: ledger counting rules per status; each cap separately (site total, UTC-day boundary at 23:59:59 vs 00:00:00, per conversation only for `moderation`, eval total independent of site); session budget; in-flight `pending` rows count; the reservation formula (cache-write price for input, output at `max_tokens`); estimator over-counts and adds overhead; `worst_case_run_cost` against hand-computed numbers using monkeypatched tunables; cap invariants.
- `test_breaker.py`: both spend-limit forms trip immediately; five consecutive errors trip; errors spaced beyond the window don't; a success resets; state persists in `GuardState`; `reset()` works; single-row `GuardState`.
- `test_llm_gateway.py`: success path row contents (exact request JSON with schema and no API key, tokens, cost, latency, status, parsed, hash stable and sensitive to system text and schema, purpose/agent/attempt/ids recorded); a `pending` row exists during the call; every refusal path (disabled, missing key, breaker, unknown model, each cap, ledger unreadable) writes the right status, makes **no** client call, and raises the right exception; provider errors logged as `error` with `error_code` and fed to the breaker; truncation, refusal and invalid output raise `LLMOutputError` with the call billed and logged; `max_retries` and key passed to the real-client builder; a guard test that the autouse fixture really blocks the real client; the "only `llm.py` imports `anthropic`" AST scan (excluding `tests/`).
- `test_budget_commands.py`: `budget` output contains the tunables path, caps, zero spend, worst-case cost for both models, kill switch and breaker state, and changes after ledger rows exist; `reset_breaker` behavior.
- Update `tests/test_tunables.py`: new caps ($1.25/$1.50/$5/$10), invariants per-conversation ≤ per-day ≤ site total, new tunables exist.
- Rules: don't modify anything outside `tests/`; no network; no API key; run only your own tests to confirm they fail for the right reason; report the list of tests and any place where the contract is ambiguous.

## 5. Coding agent brief (runs AFTER Claude has reviewed the tests)
Implement section 3 so all tests pass, without editing any test. If a test looks wrong or the contract is ambiguous, stop and report to Claude instead of guessing. Also: create the `moderation` app (apps.py, migrations), add it to `INSTALLED_APPS`, make the tunables changes, write the two management commands, and record the SDK/doc verification answers as code comments. No real API call, no network use other than reading docs. Use `.venv/bin/python`.

## 6. Verification and done-when
- The full suite passes with no network access (the step 1 tests included, adjusted only for the new caps).
- `manage.py budget` prints caps, zero spend, and the worst-case cost per run for both models.
- Testing agent's independent pass: mutation testing of the guard (remove each check in turn and confirm a test fails), plus adversarial cases (negative or huge `max_tokens`, empty messages, Unicode text, concurrent-looking reservations).
- Claude reviews the code against sections 2 and 3, runs the suite, and updates `docs/plan.md`. Then the user approves and the step is committed.

## 7. Out of scope
Prompts, schemas, quote matching, the pipeline, real API calls (step 3 onward), the `Conversation` and `ModerationRun` models (step 4).

## 8. Amendments (architect, after the building agent's SDK and docs verification, 2026-09-25)
Verified by the building agent against the installed SDK (anthropic 1.8.0) and Anthropic's docs:
1. **Thinking is always disabled.** Sonnet 5 runs adaptive thinking by default when `thinking` is omitted, and thinking bills as output tokens. Every call therefore sends `thinking={"type": "disabled"}`, and the stored `request` JSON contains `"thinking": {"type": "disabled"}`. (Disabled is accepted by both models.) Revisit only if step 3 shows the moderator needs thinking, and then as an explicit, budgeted decision.
2. **Temperature.** `claude-sonnet-5` returns HTTP 400 for any non-default `temperature`, `top_p` or `top_k`; `claude-haiku-4-5` accepts temperature. `llm.py` refuses locally: `pricing.py` gets `ACCEPTS_TEMPERATURE = {"claude-sonnet-5": False, "claude-haiku-4-5": True}`, and `call(...)` with `temperature` not None for a model that does not accept it raises `ValueError` BEFORE any row, reservation or client call. With `temperature=None` nothing is sent and `LLMCall.temperature` stays NULL. Consequence for the plan: Sonnet 5 judges cannot be pinned to temperature 0; evaluation must estimate run-to-run variation with replicates instead.
3. **SDK shape.** `Messages.parse()` in 1.8.0 has no `cache_control` or `temperature` keyword, so those go through `extra_body`; the stored request JSON records them as top-level keys. The output schema comes from `parse(output_format=<Pydantic model>)`. When `parse()` raises a pydantic `ValidationError` (truncated or invalid JSON) the usage is lost, so the call is logged as `ok` with `error_code="invalid_output"` and billed at its full reservation (a conservative over-count; for truncation the true output cost is about the same). Step 3 measures real calls and decides whether to switch to `messages.create(output_config=...)` plus our own validation so usage is always known.
4. **Accepted decisions of the building agent** (settled ambiguities): `BudgetExceeded.cap_name` values are `site_total`, `site_day`, `conversation`, `eval_total`, `session`; a cap is exceeded only when `spent + amount > limit` (reaching it exactly is allowed); `spend()` treats `since` as inclusive and `until` as exclusive; `worst_case_run_cost()` returns `{model: {"master", "intervenor", "total"}}` for the distinct models among `MASTER_MODEL`, `INTERVENOR_MODEL`, `SPIKE_MODEL`; an HTTP error is costed at 0 but a connection or timeout failure (no status) keeps its reservation counted; any other exception (including the test guard) marks the row `error` with code `client_error`, is re-raised and does not feed the breaker; `max_tokens` that is not a positive int raises `ValueError` before anything is written; `reset_breaker` also clears `consecutive_errors`.
5. **Test infrastructure rule.** Test helper code that other test files import must live in a uniquely named module (for example `tests/repo_helpers.py`), never be imported as `from conftest import ...`, because two `conftest.py` files (top level and `tests/moderation/`) collide on that name when the whole suite runs.

## 9. Amendments after the architect's code review (2026-09-25)
6. **Request timeout.** The SDK's default timeout is 10 minutes, and a hung call would block the worker and could still bill. New tunable `LLM_REQUEST_TIMEOUT_SECONDS = 120` (commented, in `config/tunables.py`); `_build_real_client()` passes `timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS` along with the key and `max_retries`.
7. **Unexpected exceptions bill the reservation.** If `client.messages.parse()` raises something that is not an API error, a pydantic `ValidationError` or a fake provider error (an SDK bug, for example), the request may have been sent, so the row is `error` with `error_code="client_error"`, `cost_usd` = the reservation (never under-count), the exception is re-raised, and the breaker is NOT fed. An exception from `get_client()` itself (nothing was sent, for example the test guard) still releases the reservation at cost 0.
8. **`prompt_sha256`** = sha256 of `system + "\x00" + json.dumps(schema, sort_keys=True)`.
9. **Ledger must survive the caller.** LLM calls must never run inside a database transaction (`transaction.atomic()`): if the caller later fails and rolls back, the cost record would be erased and the budget would silently shrink. Documented in `llm.call`'s docstring; the step 5 pipeline is required to call the gateway outside any transaction and to write its own rows in short transactions afterwards (a step 5 test will check `connection.in_atomic_block` is false during the client call).
10. **Rulings on the testing agent's ambiguities:** `created_at`/`finished_at` come from `clock.now()`; `SPIKE_MODEL` counts as a model "in use" for `worst_case_run_cost`; production code reads `django.conf.settings` (the tunables are exposed there), not `config.tunables` directly; `estimate_input_tokens` accepts the schema as a JSON-schema dict or a Pydantic class; `check_caps` does not change the session (only `llm.call` does); `BudgetUnavailable` need not write a row; `temperature` on a model that rejects it raises `ValueError` even when the kill switch is off (the value check comes first).
11. **Required tests on real SDK error shapes.** Using real `anthropic` exception objects (tests may import `anthropic`): an HTTP 429 `RateLimitError` whose body is `{"type": "error", "error": {"type": "rate_limit_error", "message": "...", "details": {"error_code": "enforced_spend_limit_reached"}}}` trips the breaker immediately; HTTP 400 `invalid_request_error` with the messages "You have reached your specified API usage limits" and "You have reached your specified workspace API usage limits" trip it immediately; an ordinary HTTP 400 `invalid_request_error` (another message) and an ordinary 429 without that code do not trip it immediately; `APITimeoutError` and `APIConnectionError` normalize to status None with error types `timeout_error` / `connection_error` and keep the reservation counted.

## 10. Amendments: two-tier circuit breaker with email alerts (user decision, 2026-09-25)
The user found "five errors in a row, manual reset" fragile (a short Anthropic outage would disable moderation until someone ran a command) and asked for a two-tier breaker plus an email to the user when it trips. Replaces the breaker rules in section 3 (`moderation/breaker.py`); everything else in section 3 stays.

**Tiers**
- **Hard trip (manual reset only, needs a human):** a spend-limit error (both shapes, as before; reason `spend_limit`); an authentication or permission error (HTTP 401 or 403, or error type `authentication_error` / `permission_error`; reason `auth_error`, because retrying a bad or revoked key is pointless); `trip("manual")`. While hard-tripped every call is refused until `manage.py reset_breaker`.
- **Soft trip (recovers by itself):** `BREAKER_MAX_CONSECUTIVE_ERRORS` counted errors in a row within `BREAKER_ERROR_WINDOW_SECONDS` (as before: a gap longer than the window restarts the count, a success resets it). Counted errors are every provider failure that is not hard: no status (timeout, connection), 5xx including 529 overloaded, an ordinary 429 (no spend-limit code), and other 4xx. Reason `consecutive_errors`. On a soft trip: `cooldown_until = now + cooldown`, where the first cooldown is `BREAKER_COOLDOWN_SECONDS` (300) and each failed probe doubles it, capped at `BREAKER_COOLDOWN_MAX_SECONDS` (3600).
- **Half-open probe:** once `now >= cooldown_until`, exactly ONE call is let through (claimed atomically inside a transaction by setting `probe_in_flight` and `probe_started_at`); all other callers are refused while a probe is in flight; a probe left in flight longer than `BREAKER_PROBE_TIMEOUT_SECONDS` (180) is considered abandoned and the next caller may claim it. Probe success (any success, including an unusable-but-billed output) closes the breaker and resets the count and the cooldown to its base. Probe failure with a counted error re-opens it soft with the doubled cooldown. A spend-limit or auth error during a probe hard-trips.
- `LLMOutputError`, truncation, refusal and unexpected client exceptions behave as before (successes for the breaker / not fed).

**Gateway contract**
- `breaker` exposes `check()` returning a small result: closed (proceed, possibly as a probe), or open with `kind` (`hard` or `soft`) and `retry_at` (soft only). Keep `is_open()` (True when a call would be refused right now).
- `BreakerOpen` gains attributes `kind` and `retry_at`; the refused row is still `refused_breaker`; the message differs by kind ("paused until the administrator resumes it" vs "temporarily unavailable, will retry automatically after ...").
- The breaker's own database work must never run inside the caller's transaction problem: `llm.call` already forbids atomic blocks.

**`GuardState` new fields** (new migration `moderation/0002_*`; do not edit 0001): `trip_kind` (`hard`|`soft`|blank), `cooldown_until` (nullable datetime), `cooldown_seconds` (int, current backoff), `probe_in_flight` (bool), `probe_started_at` (nullable datetime), `last_alert_at` (nullable datetime), `last_alert_error` (text, blank). `trip_reason` gains `auth_error`. Still one row (pk=1).

**Alerts**
- New setting `ALERT_EMAIL` read from the environment in `config/settings.py` (`_env("ALERT_EMAIL")`; a per-machine value, so it is NOT a tunable and is NOT written in any tracked file); `.env.example` gets an empty `ALERT_EMAIL=` line with a comment. The user puts `<your own address>` in their own `.env`.
- When the breaker goes from closed to open (a hard trip, a soft trip, or a soft re-open after a failed probe) and `ALERT_EMAIL` is non-empty, send ONE plain-text email via Django 6.1's MAILERS API (check the installed Django 6.1 source and docs for the current, non-deprecated call; `send_mail(..., using=...)` or `EmailMessage(...).send()`; do NOT use the deprecated `get_connection()`), AFTER the trip's transaction has committed. Hard trips always send. Soft trips send only if `last_alert_at` is empty or at least `ALERT_MIN_SECONDS_BETWEEN_EMAILS` (3600) ago. Subject: `[Forum] AI moderator paused: <reason>` (`spend limit reached`, `API key rejected`, `repeated API errors`, `manually tripped`). Body: what happened (reason, HTTP status and error type and the provider's message text, time in UTC), what it means (moderation is paused; posting still works), what to do (for a spend limit: check the Console limit; for `auth_error`: check the API key in `.env`; for soft: it retries by itself, next attempt at `cooldown_until`; how to resume a hard trip: `manage.py reset_breaker`, or the admin page from step 9). Never include the API key, prompts, messages or any user data.
- Sending must NEVER raise into the caller or block long: catch every exception, log it, store the text in `last_alert_error` (and clear it on success), and set `last_alert_at` only when the email was sent. No email is sent when `ALERT_EMAIL` is empty (log only).
- `manage.py budget` also shows the breaker kind, reason, tripped_at, cooldown_until, whether a probe is in flight, whether `ALERT_EMAIL` is configured (never print the address; say "configured" or "not configured"), and the last alert time or error. `manage.py reset_breaker` clears everything (state, cooldown, probe, counts) and prints what it cleared.

**New tunables (commented, in `config/tunables.py`):** `BREAKER_COOLDOWN_SECONDS = 300`, `BREAKER_COOLDOWN_MAX_SECONDS = 3600`, `BREAKER_PROBE_TIMEOUT_SECONDS = 180`, `ALERT_MIN_SECONDS_BETWEEN_EMAILS = 3600`.
