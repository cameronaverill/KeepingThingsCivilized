"""Every number you might want to change lives in THIS file, and nowhere else.

Edit a value here and restart the site (and the worker, once it exists).
Secrets (API keys, email credentials, the Django secret key) do NOT go here:
they live in .env, which is never committed to git.

Run `python manage.py budget` (from step 2 on) to see the current caps and how much has been spent.
"""
from decimal import Decimal

# ---------------------------------------------------------------------------
# Money caps, in US dollars. These are enforced by the app before every API call.
# They are a first line of defense; also set a spend limit in the Anthropic
# Console (docs/plan.md, section 3.1), which is the outer backstop.
# ---------------------------------------------------------------------------

# Most the live site (moderation, plus the prompt spike and golden-set runs) may spend, all time.
BUDGET_SITE_USD_TOTAL = Decimal("5.00")

# Most the live site may spend in one calendar day (UTC).
BUDGET_SITE_USD_PER_DAY = Decimal("1.50")

# Most one conversation may cost in moderation calls.
BUDGET_PER_CONVERSATION_USD = Decimal("1.25")

# Most evaluation work (replays and LLM judges) may spend, all time. Kept separate from the site's budget.
BUDGET_EVAL_USD_TOTAL = Decimal("10.00")

# Most all prompt-spike calls (purpose "spike", step 3) may spend together, all time, whatever --max-usd says.
BUDGET_SPIKE_USD_TOTAL = Decimal("1.50")  # raised from 0.50 by the user for the larger trial set

# ---------------------------------------------------------------------------
# Master switch and models
# ---------------------------------------------------------------------------

# Kill switch. False = no LLM call is ever made; moderation runs are marked "skipped".
# Deliberately OFF by default: turn it on only once your Console spend limit is set.
LLM_ENABLED = False

# Model that reads the discussion and flags problems (Master Moderator).
# Verify the exact model ids against Anthropic's docs in step 2.
MASTER_MODEL = "claude-sonnet-5"

# Model that decides whether and how to intervene (Intervenor).
INTERVENOR_MODEL = "claude-sonnet-5"

# Cheaper model used for the prompt spike and other development runs.
SPIKE_MODEL = "claude-haiku-4-5"

# The two models that act as LLM judges in the evaluation (phase B).
JUDGE_MODELS = ("claude-sonnet-5", "claude-haiku-4-5")

# Longest reply (in tokens) the Master Moderator may produce. Re-tuned after the prompt spike (step 3).
MASTER_MAX_TOKENS = 1500

# Longest reply (in tokens) the Intervenor may produce. Re-tuned after the prompt spike (step 3).
INTERVENOR_MAX_TOKENS = 1000

# How many times the Anthropic SDK retries a failed request. Kept at 1: spend-limit errors never succeed on retry.
LLM_MAX_RETRIES = 1

# Seconds the Anthropic SDK waits for one request before giving up (a timeout counts as an API error for the breaker).
LLM_REQUEST_TIMEOUT_SECONDS = 120

# Largest max_tokens the gateway accepts for one call; anything above is refused before it is logged or priced.
LLM_MAX_TOKENS_LIMIT = 32000

# Largest estimated input, in tokens, that one call may send; a legitimate worst case is about 31,000.
# Larger requests are refused before they are logged or priced.
LLM_MAX_INPUT_TOKENS = 50000

# Safety guard: refuse LLM calls made inside a database transaction (a rollback would erase the cost record).
# Leave True; only tests turn it off.
LLM_FORBID_ATOMIC_CALLS = True

# Circuit breaker: trip after this many API errors in a row...
BREAKER_MAX_CONSECUTIVE_ERRORS = 5

# ...within this many seconds. Spend-limit and authentication errors trip it "hard" (no calls until
# `manage.py reset_breaker`); repeated ordinary errors trip it "soft" (it retries by itself after a cooldown).
BREAKER_ERROR_WINDOW_SECONDS = 300

# Soft trip: seconds without any call before one probe call is let through. Doubles after each failed probe.
BREAKER_COOLDOWN_SECONDS = 300

# Soft trip: longest the cooldown may grow to, in seconds.
BREAKER_COOLDOWN_MAX_SECONDS = 3600

# A probe call still in flight after this many seconds is considered abandoned and the next caller may probe.
BREAKER_PROBE_TIMEOUT_SECONDS = 180

# The alert email for a soft trip is sent at most once per this many seconds (a hard trip always sends).
ALERT_MIN_SECONDS_BETWEEN_EMAILS = 3600

# Budget guard: input tokens are estimated locally as ceil(characters / this) + the overhead below.
# Deliberately low so the estimate over-counts (real text is roughly 3 to 4 characters per token).
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 2.5

# Budget guard: tokens added to every estimate to cover request framing and the injected structured-output instructions.
TOKEN_ESTIMATE_OVERHEAD_TOKENS = 1000

# Budget guard: assumed size of an agent's system prompt (prompt, taxonomy, rubric) when computing the worst-case cost of
# one moderation run. A placeholder until step 3 measures the real prompts.
SYSTEM_PROMPT_TOKENS_ESTIMATE = 6000

# `manage.py budget` reports calls still "pending" after this many minutes (a crashed process may have left them).
PENDING_CALL_STALE_MINUTES = 15

# ---------------------------------------------------------------------------
# Message and conversation limits (all checked on the server)
# ---------------------------------------------------------------------------

# Longest user message, in characters (about 500 words). Longer messages are rejected with an explanation.
MAX_MESSAGE_CHARS = 3000

# A participant must wait this many seconds between messages (the page always tells them how long, and why).
MIN_SECONDS_BETWEEN_MESSAGES = 30

# Most user messages one conversation may contain before it is closed.
MAX_USER_MESSAGES_PER_CONVERSATION = 30

# Most conversations that may be open at the same time.
MAX_OPEN_CONVERSATIONS = 5

# People per conversation. The database supports more; the MVP allows two.
MAX_PARTICIPANTS = 2

# Only the most recent messages are sent to the moderator LLMs; this bounds the cost of every run.
TRANSCRIPT_MAX_MESSAGES = 20

# Most actions one moderator post may contain; extra issues are declined with a reason.
MAX_ACTS_PER_INTERVENTION = 3

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

# Username length limits (letters, digits, underscore, hyphen).
USERNAME_MIN_LENGTH = 3

# Longest allowed username.
USERNAME_MAX_LENGTH = 30

# Shortest allowed password.
PASSWORD_MIN_LENGTH = 12

# How long an email confirmation link stays valid.
EMAIL_CONFIRM_MAX_AGE_DAYS = 3

# Minimum wait before another confirmation email can be requested.
RESEND_CONFIRMATION_MIN_SECONDS = 60

# How long a login session lasts, in seconds (30 days).
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

# Failed logins allowed before the account/IP is temporarily locked out.
LOGIN_MAX_FAILURES = 5

# Length of that lockout, in minutes.
LOGIN_COOLOFF_MINUTES = 15

# ---------------------------------------------------------------------------
# Background worker and page updates
# ---------------------------------------------------------------------------

# "worker" = a separate `manage.py run_moderator` process handles runs (normal use).
# "sync" = runs happen inline in the web request (used by tests).
MODERATION_RUN_MODE = "worker"

# How often the worker looks for new runs, in seconds.
WORKER_POLL_SECONDS = 2

# A run stuck in "running" longer than this many seconds is considered abandoned and is retried.
RUN_TIMEOUT_SECONDS = 300

# A run is retried at most this many times before it is marked failed.
RUN_MAX_ATTEMPTS = 3

# How often the thread page asks the server for new messages, in milliseconds.
POLL_INTERVAL_MS = 3000

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# How long SQLite waits for another writer (the web server or the worker) before giving up, in seconds.
DB_BUSY_TIMEOUT_SECONDS = 20

# ---------------------------------------------------------------------------
# Evaluation thresholds (phase B; final values are fixed in the pre-registration)
# ---------------------------------------------------------------------------

# Two raters' highlighted phrases count as the same finding if their overlap (intersection / union) is at least this.
SPAN_MATCH_MIN_IOU = 0.5

# Intensity scores (0-4) that differ by at least this much are sent to a human to adjudicate.
INTENSITY_DISAGREEMENT_THRESHOLD = 2

# A phrase counts as an "error" for the metrics if its intensity is at least this. Proposal; not final.
INTENSITY_ERROR_THRESHOLD = 2
