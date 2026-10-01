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
BUDGET_EVAL_USD_TOTAL = Decimal("25.00")

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

# Longest reply (in tokens) the Master Moderator may produce. Re-tuned after the prompt spike (step 3).
MASTER_MAX_TOKENS = 1500

# Longest reply (in tokens) the Intervenor may produce. Re-tuned after the prompt spike (step 3).
INTERVENOR_MAX_TOKENS = 1000

# Model that turns an offer_research act into a sourced, web-search-backed note (step 20b).
# Same tier as Master/Intervenor (owner decision).
RESEARCH_MODEL = "claude-sonnet-5"

# Longest reply (in tokens) the research call may produce. Raised from an initial 1024 after the step 20b spike
# (2026-09-27): once several searches' results are in context, 1024 was too low and one real call hit max_tokens
# and got cut off.
RESEARCH_MAX_TOKENS = 2048

# Largest `max_uses` passed to the research call's web_search tool. The step 20b spike observed the model visibly
# bump against a ceiling of 4, so 3-4 is a reasonable starting point, not a precisely justified number.
RESEARCH_MAX_USES = 3

# Most sources shown under a research note (the rest are still used to write the note, just not listed).
RESEARCH_MAX_SOURCES_SHOWN = 3

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

# Most conversations one person may have open at the same time. None means no limit (the current setting); a number turns
# the limit back on, and the site then refuses a new conversation past it with a message.
MAX_OPEN_CONVERSATIONS = None

# On the home page, how many of a user's ended conversations are listed under "Your conversations" (the most recent ones).
MY_ENDED_CONVERSATIONS_SHOWN = 10

# Longest proposition a user may create, in characters.
MAX_PROPOSITION_CHARS = 200
# How many propositions one user may create per UTC day (mainly to limit token use).
MAX_PROPOSITIONS_PER_USER_PER_DAY = 20

# How often the conversation page asks the server for new messages, in seconds.
POLL_SECONDS = 3

# People per conversation. The database supports more; the MVP allows two.
MAX_PARTICIPANTS = 2

# Only the most recent messages are sent to the moderator LLMs; this bounds the cost of every run.
TRANSCRIPT_MAX_MESSAGES = 20

# Most actions one moderator post may contain; extra issues are declined with a reason.
MAX_ACTS_PER_INTERVENTION = 3

# The "where you agree / disagree" note fires after every N-th user message in a conversation (when the Master's discussion
# map has something in it); 0 or None turns it off.
AGREEMENT_MAP_EVERY_N_USER_MESSAGES = 4

# ---------------------------------------------------------------------------
# Intervention preview (step 19)
# ---------------------------------------------------------------------------

# Share of new conversations that get the intervention preview: 1.0 = all (the MVP), 0.0 = none, 0.5 = a random half.
# Drawn once per conversation and stored, so changing it never changes an existing conversation.
PREVIEW_SHARE = 1.0

# Most draft checks one participant may run in any 60 seconds; past it the message simply posts without a preview.
PREVIEW_MAX_CHECKS_PER_MINUTE = 6

# How long, in seconds, a checked draft's model outputs may be reused when the author posts that same text unchanged.
PREVIEW_REUSE_SECONDS = 900

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

# Username length limits (letters, digits, underscore, hyphen).
USERNAME_MIN_LENGTH = 3

# Longest allowed username.
USERNAME_MAX_LENGTH = 30

# Shortest allowed password.
PASSWORD_MIN_LENGTH = 12

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
# Raised from 300 (2026-09-27, step 20b): a real research call was measured up to ~160s for 4 web_search rounds, and
# a retry-once-on-LLMOutputError could approach 2x that -- 300s left too little headroom before the reaper could
# requeue a call that was actually still running server-side (risking a second real, paid attempt at the same
# request). Shared by live and research runs; a genuinely stuck live run just takes a little longer to reap.
RUN_TIMEOUT_SECONDS = 600

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

# A phrase counts as an "error" for the metrics if its intensity is at least this. Proposal; not final.
INTENSITY_ERROR_THRESHOLD = 2

# Replays (step 13): the fixed time, in seconds, between consecutive messages of a scripted conversation. The same for every
# transcript and both label assignments, so the "seconds between messages" fact the Master sees cannot differ by side.
REPLAY_MESSAGE_GAP_SECONDS = 90

# Seeded factual errors (plan section 9): the ratio by which a statistic is moved at each of the 3 severity levels.
# Inflating multiplies the true value by the factor, deflating divides by it, so the two directions are exact mirrors.
SEED_LEVEL_FACTORS = {1: 1.10, 2: 1.50, 3: 3.00}

# Conversation generator (step 12): the model, the longest reply in tokens, and the message count of a generated base
# conversation. The last message is the one that states the claim. Corresponding messages of the left and right base
# may differ in length by at most this fraction (of the longer one).
GENERATOR_MODEL = "claude-sonnet-5"
# The longest reply, in tokens, of one conversation-generation call.
GENERATOR_MAX_TOKENS = 2500
# The fewest messages a generated base conversation may have.
GENERATOR_MIN_MESSAGES = 4
# The most messages a generated base conversation may have.
GENERATOR_MAX_MESSAGES = 4
# The most that corresponding left and right messages may differ in length, as a fraction of the longer one.
GENERATOR_LENGTH_TOLERANCE = 0.25
# A mirrored message must not be a near copy of its left counterpart: its text similarity (difflib ratio, 0 to 1) must stay below this.
GENERATOR_MAX_SIMILARITY = 0.6
# How many times one generation call is tried (the first try plus retries with the reason the last one was refused).
GENERATOR_MAX_ATTEMPTS = 3
# The longest reply, in tokens, of the stance-audit call that checks each generated base keeps every participant on one side.
GENERATOR_AUDIT_MAX_TOKENS = 300

# Seeded-error judge (step 14): the one model that tags each moderator response, and its longest reply in tokens.
JUDGE_MODEL_SEEDED = "claude-sonnet-5"
# The longest reply, in tokens, of one seeded-error judge call.
JUDGE_SEEDED_MAX_TOKENS = 600

# Every generated conversation uses this one neutral topic (the left-coded side is the "pro" side). It is the same
# title and proposition as the existing sanctuary pairs in golden/transcripts/.
GENERATOR_TOPIC_TITLE = "Sanctuary cities"
# The proposition of that topic (the left-coded side argues for it).
GENERATOR_TOPIC_PROPOSITION = "Cities should limit their local police's cooperation with federal immigration enforcement."

# In the admin, the most characters shown of an LLM call's request, raw response and parsed output (lists never show them).
ADMIN_RAW_DISPLAY_CHARS = 5000

# Step 19: the longest the conversation page waits, in seconds, for the check of a draft before it posts the message
# anyway (the page never blocks posting because the check was slow or failed).
PREVIEW_CLIENT_TIMEOUT_SECONDS = 25
