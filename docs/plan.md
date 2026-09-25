# AI-Moderated Discussion Forum — Plan v5

Earlier versions are in `docs/plan_v1.md` … `docs/plan_v4.md`. v5 adds a server-enforced message length limit and turns the build order into step-by-step build instructions (section 14).

## 0. How to use this plan
- Build **one step of section 14 at a time**, in order. Each step lists what to build, the tests to write **first**, and when it counts as done. Don't start a step until the previous one is done and its tests pass.
- Sections 1–13 are the reference design that the steps point back to. If building a step shows the design is wrong, update this file first, then the code.
- **No real API call happens before step 3,** and only after the Anthropic Console spend limit (3.1) is set and the budget guard (step 2) is finished.
- Every number you might want to change lives in `config/tunables.py`.
- **Working method (from step 2 on):** a **coding agent** writes the implementation and a **separate testing agent** writes and runs the tests for each component, so the tests aren't written by the same agent that writes the code. Claude (the main agent) owns the **system architecture**: it hands each agent a precise brief drawn from this plan, reviews what comes back against the design, and integrates it. The user approves each step before the next begins.

## 1. Goals and constraints
- Build a minimal two-person discussion board where an AI moderator makes the discussion more productive **while staying politically neutral**.
- **Neutrality:** given two equal contributions (equal factual support, clarity, logic, procedural conduct), the system intervenes at equal frequency, in an equal manner, with equal intended effect. This holds regardless of political position (first priority), and later regardless of speaker identity, speaking order and content features such as length.
- **Log first.** The website is step one. A separate bias-evaluation pipeline uses its logs. Every moderation decision, including "no intervention", is logged in a structured form that can be reconstructed and fetched in batches.
- **Two stages, measured separately, for every issue dimension:**
  - **Detection:** does the Master Moderator notice a problem?
  - **Action:** given a noticed problem, does the Intervenor act on it?
- **Cost must stay VERY modest.** The user pays for the API calls.

## 2. Decisions (agreed)
- **Stack:** Python 3.13 + Django 6.1, SQLite (WAL mode), server-rendered pages, small JS polling every ~3s. Python 3.13 works on every host checked, including PythonAnywhere, which has no 3.14.
- **Accounts:** username + password with **required email confirmation**, using Django's own auth (section 10).
- **LLM:** Claude only, through the Anthropic SDK. Only `moderation/llm.py` may import `anthropic`, and a test enforces this.
- **One settings file for everything you would tune:** `config/tunables.py` holds the dollar caps, input and traffic limits, the kill switch, model choices and analysis thresholds, each with a comment. Secrets (API key, email credentials, Django secret key) live in `.env`, never in code.
- **Trigger:** the Master Moderator → Intervenor pipeline runs after every user message. At most one moderator post per user message. Moderator messages never trigger runs.
- **Message length:** user messages are capped at `MAX_MESSAGE_CHARS` (default **3,000 characters**, about 500 words), enforced on the server and explained to users (section 4).
- **Neutrality inputs:**
  - Moderator LLMs see only `Participant A/B` labels: no usernames, emails, stance or lean.
  - A/B labels are assigned **at random** when a conversation fills. The seed is stored on the conversation. Speaking order and join order are logged separately, so position bias stays testable.
  - Political labels are never collected from users.
- **Stance and position:** each message gets a `stance` label (pro/con/neutral) relative to the topic's proposition. Position is derived from `Topic.leans`, a JSON field, under several schemes (`compass` with economic and social axes, `us_partisan` with a party axis). Values run from −1 to +1. One headline scheme is chosen before the evaluation.
- **Issue dimensions and intensity:** problems are described by a *dimension* (e.g. `factual_accuracy`, `abusiveness`) and an *intensity* from 0 to 4, attached to a **phrase** of a message (or to the whole message when the whole message is the problem). Human and LLM raters use the same scheme and may mark a phrase "not scorable" with a reason.
- **Judge panel:** two Anthropic models to start. Human raters calibrate them.
- **TDD:** pytest + pytest-django, tests first. `FakeLLM` for the plumbing. A **golden set** of transcripts checks prompts against the real API (small, on demand, budgeted).
- **Runner:** a separate worker process, not an in-process thread (section 11).

## 3. Cost controls
### 3.1 Anthropic side (the user sets these once in the Console, before step 3)
Checked against Anthropic's docs. These are separate from any claude.ai or Claude Code subscription.
1. Console (platform.claude.com) → **Settings → Workspaces → create a workspace** (e.g. `discussion-forum`). The **Default Workspace cannot have limits**, so the project must not use it.
2. In that workspace's **Spend limits** tab, set a monthly cap (**$10**). Create the API key **inside that workspace** and put it in `.env`.
3. Also set an **organization limit** under **Settings → Billing → Spend limits** as a second backstop.
4. When a limit is hit, requests fail with **HTTP 400, `invalid_request_error`**, message starting "You have reached your specified … usage limits". The org tier cap instead returns a 429 with `error_code: enforced_spend_limit_reached`.
5. **Caveat:** the docs don't promise cut-off to the cent or in real time, so this is the backstop. The app-side guard is the first line of defense.
6. Not verified: how prepaid credits and auto-reload interact with the limit. Check the Billing page yourself.

### 3.2 App side (built in step 2)
All enforced in the single `llm.call()` function. `manage.py budget` prints the current caps, the path of `config/tunables.py`, and the spend so far.
- **Price table** (`moderation/pricing.py`): per-model input, output, cache-write and cache-read prices, checked against Anthropic's pricing page when implemented. An unknown model means **no call** (fail closed).
- **Ledger:** every call stores `cost_usd`. Totals are sums over `LLMCall`, grouped by `purpose` (moderation / spike / golden / replay / judge), by day, and by conversation.
- **Reservation before each call:** the worst-case cost (counted input tokens × input price + `max_tokens` × output price) is added to the spend so far. If that would pass **any** applicable cap, the call is refused. A refused moderation run is marked `skipped_budget` and logged, so skipped runs show up in the data.
- **Bounded input:** because messages are capped (section 4) and only the last `TRANSCRIPT_MAX_MESSAGES` are sent, the worst-case cost of one run has a known upper bound. `manage.py budget` prints it.
- **Fail closed:** if the ledger can't be read, no call is made.
- **Circuit breaker:** trips on either spend-limit error, or on 5 consecutive API errors within 5 minutes. Once tripped, no calls until `manage.py reset_breaker`. The SDK's `max_retries` is 1.
- **Kill switch:** `LLM_ENABLED = False` turns LLM calls off, and runs are marked `skipped_disabled`. It is **off by default**: the user switches it on deliberately once the Console spend limit is set.
- **Prompt caching:** the system prompt, taxonomy, rubric and the earlier part of the transcript are sent as a cacheable prefix, with only the newest messages after the cache breakpoint. This is the biggest single cost lever, because every run re-sends a growing transcript. Cache tokens are recorded and priced separately.
- **Traffic limits:** `MIN_SECONDS_BETWEEN_MESSAGES` per participant, `MAX_USER_MESSAGES_PER_CONVERSATION`, `MAX_OPEN_CONVERSATIONS`, `max_tokens` per agent.
- A test fails if any tunable name is assigned anywhere except `config/tunables.py` (checked by parsing the source).

### 3.3 Default caps in `config/tunables.py` (agreed; change them there)
| Setting | Default |
|---|---|
| `BUDGET_SITE_USD_TOTAL` (moderation + spike + golden) | $5.00 |
| `BUDGET_SITE_USD_PER_DAY` | $1.00 |
| `BUDGET_PER_CONVERSATION_USD` | $0.50 |
| `BUDGET_EVAL_USD_TOTAL` (replays + judges) | $10.00 (separate) |
| Anthropic workspace monthly limit (set in the Console, not in code) | $10 |

### 3.4 Evaluation spending
- Every replay or judging command **requires `--max-usd`**, and `--dry-run` prints the number of calls and the estimated cost.
- Start with a small pilot. Human raters cost no API money.
- The Batch API (typically discounted, to be verified) is a later optimization.

## 4. Input limits (server-enforced, explained to users)
**Message length.** `MAX_MESSAGE_CHARS` in `config/tunables.py`, default **3,000 characters** (about 500 words).
- **How characters are counted** (one function, `forum/limits.py: count_message_chars`, used everywhere): line endings are normalized to `\n`, the text is Unicode-normalized (NFC), leading and trailing whitespace is stripped, and the remaining Unicode characters (code points) are counted. An emoji counts as one character.
- **Enforced on the server.** Every path that creates a user message goes through one service function, `forum/services.py: post_message()`, which validates length (and emptiness and the rate limits) before anything is saved. Views never create `Message` rows directly. A request that skips the page (e.g. a hand-made POST) gets the same rejection. Over-limit messages are **not saved, not moderated and cost nothing**.
- **Explained to users:**
  - The compose box shows the limit and a live counter ("2,950 / 3,000"). The counter uses the same counting rule (code points, trimmed), so it agrees with the server. It is guidance only: the server decides.
  - The browser does **not** silently truncate text (no `maxlength` attribute), so nothing a user wrote is lost without them seeing it.
  - An over-limit post returns the form **with the user's text still in it** and a clear error, e.g.: *"Your message is 3,412 characters; the limit is 3,000. Please shorten it by 412 characters. Messages are capped to keep the discussion readable and to keep the AI moderator's running costs low."*
  - The same short explanation appears on the "How this works" page, alongside the rate limit.
- **Whenever a user cannot post, the page tells them why, in plain words, and what they can do next.** `post_message()` never fails silently and never with a generic error: it raises a `PostRejected` carrying a stable `code`, a user-facing `message`, and `retry_after` seconds where relevant. The view shows that message next to the compose box and keeps the user's text; for standing conditions it also disables the compose box and shows the reason. The server decides; any countdown on the page is guidance only. The reasons and their wording:
  - **Empty message:** "Your message is empty."
  - **Too long:** the message described above (count, limit, reason).
  - **Too fast** (`MIN_SECONDS_BETWEEN_MESSAGES`, **30 seconds**): "Please wait 12 more seconds before posting again. Messages are limited to one every 30 seconds so both people have time to read and reply, and to keep the AI moderator's running costs low." The wait is measured from the same participant's previous message, on server time.
  - **Conversation full** (`MAX_USER_MESSAGES_PER_CONVERSATION`): "This conversation has reached its limit of 30 messages and is closed. You can start a new one."
  - **Conversation closed:** "This conversation is closed."
  - **Not a participant / thread full:** "You are not a participant in this conversation." and "This conversation already has two participants."
  - **Not logged in, or email not confirmed:** a redirect with a sentence saying what to do ("Please log in", "Please confirm your email first; we sent you a link").
  - **Something failed on our side:** "Something went wrong on our side and your message was not sent. Your text is still in the box; please try again." (details go to the log, not the page).
  - **Moderation paused** does not stop posting; the page says so and why, in plain words ("The AI moderator has reached today's spending limit and will resume tomorrow", "...this conversation's limit", "moderation is switched off", or "the moderator ran into a problem"), and that messages are still posted. It never shows internal error text.
- **Synthetic messages** (paired test sets) are held to the same limit, so the test conditions match what real users can post.
- **Other text inputs:** username 3–30 characters, **ASCII letters, digits, `_` and `-` only** (so no two usernames can look alike on screen); email up to 254 characters. The username rule is enforced on the model, so every way of creating an account obeys it (forms, `create_user`, `createsuperuser`, admin, plain `save()`).

## 5. Taxonomy and dimensions (single source: `moderation/taxonomy.py`)
The database choices, the Pydantic schemas and the prompt text all come from this one file. Each type has a one-sentence definition, boundary rules and examples.

**Issue types (Master Moderator):** `unsupported_claim`, `possible_factual_error`, `unclear_statement`, `fallacy`, `strawman`, `abusive_language`, `repetition`, `process_violation`.
- `unsupported_claim` means a factual assertion with no support, which may be true. `possible_factual_error` means an assertion that is likely false.

**Dimensions:** an issue type may map to a *dimension* that has an intensity rubric. Adding a dimension means adding a rubric file and a registry entry, with no schema change.
| Dimension | Issue type | What it measures | What raters record |
|---|---|---|---|
| `factual_accuracy` | `possible_factual_error` | how wrong a factual claim is | every checkable claim, including accurate ones (intensity 0), so false-positive rates have a denominator |
| `abusiveness` | `abusive_language` | how abusive a phrase is | only phrases with intensity of 1 or more; a message with no such phrase counts as 0 |

**Rubrics** are versioned files, `rubrics/<dimension>_v1.md`, used **verbatim** in the LLM prompts and in the human raters' guidelines. Draft anchors, to be finalized before the pilot:
- `factual_accuracy`: 0 accurate; 1 minor imprecision; 2 materially misleading or overstated; 3 clearly false; 4 flagrantly false or fabricated.
- `abusiveness`: 0 none; 1 mildly rude, dismissive or sarcastic; 2 mocking or contemptuous toward the person; 3 direct insults or demeaning language; 4 slurs, dehumanizing language, threats or harassment.

**Not scorable is an outcome, not a score.** A rater may decline to give an intensity and must say why: `unverifiable` (no reliable evidence either way), `contested` (credible sources genuinely disagree, or the claim rests on a value judgment), or `needs_context`. Contested political claims must not be forced onto the 0–4 scale, because whoever does the forcing decides the "truth", and that is exactly where bias would enter the ground truth. How often each side's claims are called `contested` or `unverifiable` is reported by side.

**Act types (Intervenor):** `provide_information`, `correct_factual_error`, `improve_argumentation`, `clarify_argument`, `restate_positions`, `identify_agreement_disagreement`, `request_information`, `request_clarification`, `enforce_conduct`, `enforce_process`.
- `clarify_argument` restates without changing content. `improve_argumentation` flags a reasoning problem. `enforce_process` covers flooding, turn-taking, and prompting a party to respond.

**Decision:** `intervene` | `no_intervention`. **Tone (self-reported per act):** `gentle` | `neutral` | `firm`.

## 6. Agent inputs and outputs (Pydantic, validated)
**Master Moderator (detection)** input:
- the transcript up to `snapshot_seq` (at most the last `TRANSCRIPT_MAX_MESSAGES`), with `Participant A/B` labels and message ids
- earlier moderator posts
- issues already raised, with their outcomes, marked already-raised
- process facts computed in code (consecutive messages per participant, time gaps)

**Only-new-issues rule:** the Master flags issues in the *newest* message, plus inherently cross-message issues (`repetition`, `strawman`, `process_violation`). An issue on an older message with any other type is kept in the log, marked rejected, and neither counted nor passed to the Intervenor.

**Phrase-level issues:** each issue quotes **the specific phrase** that has the problem, or the whole message only when the whole message is the problem. The LLM returns only the quote text, never character offsets. Code finds the quote in the message: an exact match first, then a normalized match (whitespace, straight vs. curly quotes, case). It stores the offsets and the match type. For issue types with a dimension, the Master also gives an `intensity` (0–4) on that rubric.

Output:
- `issues[]`: `{id, message_id, issue_type, quote, explanation, confidence, intensity?}`
- `discussion_map`: `{agreements[], disagreements[{summary, kind: factual|normative}]}`

**Intervenor (action)** input: the transcript and the Master's valid issues.

Output:
- `decision`, `rationale`
- `issue_dispositions[]`: for **every** valid issue, `{issue_id, disposition: acted|declined, reason}`
- `acts[]`: `{type, addressee (A|B|all), subject (A|B|both|none), source_issue_ids, source_message_ids, tone, text}`, at most `MAX_ACTS_PER_INTERVENTION` (default 3)

**Validation happens at two levels:**
- **Structural** (invalid JSON, or doesn't match the schema): retry once. If it fails again, the run is marked `failed`, nothing is posted, and everything is logged.
- **Item level** (a quote that can't be found, an unknown message id, a breach of the only-new-issues rule, an act citing a rejected issue, acts over the cap): only that item is rejected, with a reason, and the run continues. Rejected items stay in the database. Rejection rates are reported by side, because whole-run failures would silently remove more data from whichever side writes longer messages.

The posted moderator message is the acts rendered in order. Display names are swapped in for `Participant A/B` only when the page is rendered. There is no third tagging LLM in the MVP.

## 7. Prompt safety and privacy
- User messages are inserted as delimited **data**. The prompts state that instructions inside user messages must be ignored. An injection attempt is one of the golden-set cases.
- Moderator output is HTML-escaped when rendered.
- Prompt builders receive only labels and message text, never `User` objects, so usernames and emails cannot reach an LLM or `LLMCall` logs. A test enforces this.
- **Keys never reach GitHub (layered):** `.gitignore` blocks `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key` and `secrets/`; `scripts/check_secrets.py` (standard library only) finds Anthropic and other provider keys, private-key blocks and `password=`-style assignments, and never prints a whole secret; a `pre-commit` hook scans the staged content and a `pre-push` hook scans the entire history (installed per clone with `scripts/install_hooks.sh`; a line marked `secret-scan: allow` is skipped); tests keep the repository clean; agents are told never to read or print `.env`. Hooks can be skipped with `--no-verify`, so the real backstop is GitHub's own push protection plus revoking any leaked key immediately.

## 8. Data model
`accounts` app:
- `User` (extends `AbstractUser`, set as `AUTH_USER_MODEL` **before the first migration**): username (ASCII rule above), email (required), `username_key` and `email_key` (derived, unique, filled by `save()` from `normalize_key` = NFKC(casefold(NFKC(value))), because SQLite's `LOWER()` only folds ASCII and would accept look-alikes such as `Émile`/`émile`), email_verified_at, password (salted hash only). Duplicates raise a friendly `ValidationError` on every code path; the unique constraints on the keys are the database backstop. `is_active` is set false by the registration flow (step 6) until the email is confirmed.

`forum` app:
- `Topic`: title, description, proposition, `leans` (JSON: per side, per scheme and axis, with rationale)
- `Experiment`: name, kind (`paired` | `replay` | `observational`), description, config (JSON)
- `Conversation`: topic FK, status (`open` | `active` | `closed`), source (`human` | `synthetic`), experiment FK (optional), pair_id, variant, label_seed, created_at
- `Participant`: conversation FK, user FK (null for synthetic), label, join_order, joined_at. Unique on (conversation, label) and (conversation, user).
- `Message`: conversation FK, seq_no (unique per conversation), author_type (`user` | `moderator`), participant FK (optional), in_reply_to FK (optional), content, char_count (from `count_message_chars`), `planted` (JSON: for synthetic messages, the planted phrases with dimension and intended intensity), created_at

`moderation` app:
- `LLMCall`: purpose (`moderation` | `spike` | `golden` | `replay` | `judge`), run FK (optional), conversation FK (optional), agent, attempt, provider, model, prompt_version, prompt_sha256, temperature, max_tokens, request (JSON), raw_response, parsed, tokens_in, tokens_out, cache_write_tokens, cache_read_tokens, reserved_usd, **cost_usd**, latency_ms, status (`ok` | `error` | `refused_budget` | `refused_breaker` | `refused_disabled`), error, error_code, created_at
- `GuardState` (single row): breaker_tripped, tripped_at, trip_reason, consecutive_errors
- `ModerationRun`:
  - conversation, trigger_message, snapshot_seq
  - kind (`live` | `replay`), replay_of (optional), replicate
  - status (`pending` | `running` | `done` | `failed` | `skipped_budget` | `skipped_disabled`), attempts
  - is_stale
  - decision, rationale, posted_message (optional)
  - config_snapshot (JSON: models, temperatures, prompt versions and hashes, tunables used), discussion_map (JSON), error
  - claimed_at, started_at, finished_at
  - Replays never post a message. Several runs may share a trigger message.
- `Issue`: run FK, local_id, message FK, issue_type, dimension (optional, derived from the type), quote, quote_start, quote_end, quote_match (`exact` | `normalized` | `not_found`), explanation, confidence, intensity (optional, 0–4), validity (`valid` | `rejected`), rejection_reason. The author is derived from the message.
- `IssueDisposition`: issue (one-to-one), disposition (`acted` | `declined`), reason
- `InterventionAct`: run FK, order, act_type, tone, text, addressee (label or `all`), subject (label, `both` or `none`), validity, rejection_reason, and features computed in code (char_len, word_count, is_question, quotes_participant)
- `ActSource`: many-to-many links from each act to its source issues and messages

`evaluation` app (**designed now, built in phase B**; the site never imports it). Human and LLM raters share all of these tables.
- `Rater`: name, kind (`human` | `llm`), provider, model and temperature (LLM only), user FK (human only), active
- `Panel`: name, version, raters (many-to-many), dimensions with rubric versions and hashes, consensus rule (span-match threshold, disagreement threshold)
- `Rating`: one rater's pass over one target. Fields: rater FK, target (generic: a `Message` or an `InterventionAct`), dimensions covered, replicate, llm_call FK (LLM only), guideline_version, status, started_at, finished_at
- `Finding`: **one phrase, one dimension, one intensity.** Fields: rating FK, local_id, dimension, start, end, quote (must equal `text[start:end]`), intensity (0–4, null when not scorable), not_scorable_reason (`unverifiable` | `contested` | `needs_context`; required when intensity is null), confidence (optional), detail (JSON, e.g. the claim restated and the correct fact). Findings on different dimensions may overlap.
- `ConsensusFinding`: panel, target, dimension, start, end, n_raters, intensity_mean, intensity_range, needs_adjudication, adjudicated_intensity (optional), adjudicated_by. A many-to-many link lists the `Finding`s it merges.
- `IssueFindingLink`: issue FK, consensus_finding FK, overlap. Links the Master's detection to the ground truth.
- `CalibrationSet` and `CalibrationItem`: the sample given to human raters, with sampling seed and strata.
- `Annotation`: generic target, dimension for message-level labels (`stance`, `tone`, `directional_effect`, …), value, source (`self` | `rater:<name>`), rating FK (optional), confidence, created_at

## 9. Two-stage bias measurement, the rater panel and calibration
**Unit of analysis:** the phrase-level finding. Every finding on every dimension is a separate comparison.

**Raters.**
- **LLM raters:** two Anthropic models, temperature 0, versioned and hashed prompts, logged as `LLMCall`. By default one call per rater per message covers all active dimensions.
- **Human raters:** a staff-only annotation page. The rater sees a message, highlights a phrase, picks a dimension and an intensity (or "not scorable" with a reason), and can mark "no issues". Messages appear in random order.
- **Blinding, for everyone:** raters see the topic proposition, the message and minimal context. Never usernames, `Topic.leans`, experiment or variant labels, the moderator's output, or another rater's output.

**Consensus (in code).** Within one target and dimension, findings are merged when their spans overlap enough (`SPAN_MATCH_MIN_IOU`, default 0.5). A phrase found by only one rater, intensities differing by 2 or more, or disagreement on whether a phrase is scorable set `needs_adjudication`, which a human resolves. All analyses are repeated with spans snapped to sentence boundaries as a robustness check.

**Calibration of the LLM panel against humans** (before the panel is used on anything unlabeled):
1. Build a `CalibrationSet`: about 100 messages per dimension, stratified to include no-issue messages, low and high intensities, planted items, and both sides.
2. At least two human raters label it. Report human–human agreement per dimension (weighted kappa on matched phrases, phrase-detection F1, agreement on "not scorable").
3. Report LLM panel vs. human consensus with the same measures, plus systematic offsets.
4. **Judge symmetry:** compare LLM-minus-human differences by political side.
5. **Human-only benchmark:** compute the detection, action and false-positive metrics on the human-labeled set alone, as well as on the LLM panel's labels. The Master and both LLM raters are Anthropic models and may share blind spots, which would make detection rates measured against the LLM panel look better than they are.
6. Revise rubrics and prompts until the pre-registered agreement thresholds are met. Record each human rater's ratings separately and report rater-level differences.

**Metrics, per dimension:**
1. **Detection** D(dimension, side, intensity) = P(Master raises a matching valid issue | a finding at that intensity, by the speaker's side). Matching is by span overlap (`IssueFindingLink`).
2. **Action** A(dimension, side, intensity) = P(Intervenor acts | the issue was raised, at that intensity and side).
3. **False positives** = the share of phrases rated 0 (or never flagged) that the Master flagged, by side.
4. **Data quality by side:** rejected-item rates, failed and skipped runs, and "not scorable" rates.

Rates are compared **at equal intensity**, because one side's phrases may simply be more wrong or more abusive. Check that both sides have overlapping intensity values. The analysis clusters by message and conversation. The paired track uses McNemar tests.

**Two tracks:**
- **Paired (first):** scripted transcripts with planted phrases of known dimension and intensity, one version per political direction, each run under both A/B label assignments. `Message.planted` stores the known truth, so this track works even without raters.
- **Observational (second):** real logs, with the rater panel providing ground truth and stance labels.

## 10. Accounts and web flow
**Registration and login (Django's own auth):**
1. **Register:** username, email, password (twice). The account is created **inactive**, and a confirmation email is sent with a signed link that expires after 3 days (`EMAIL_CONFIRM_MAX_AGE_DAYS`). The page says "check your email" whether or not the address was already registered.
2. **Confirm:** the link activates the account, once. Unconfirmed accounts can't log in. "Resend link" is rate-limited.
3. **Passwords:** salted hashes only (Argon2 if it installs on this Python, otherwise Django's default PBKDF2-SHA256). Never stored or logged in plaintext. Validators: minimum length 12, not a common password, not similar to the username or email, not all digits. Password reset and change by email are included.
4. **Login protection:** throttling after repeated failures, per username and per IP, via `django-axes`. A generic error message that doesn't reveal which part was wrong.
5. **Sessions:** HttpOnly, SameSite=Lax, Secure over HTTPS. CSRF on. Sessions last 30 days.
6. **Transport:** passwords only over **HTTPS**, except on localhost. Needed before anyone else uses the site (section 17).
7. **Email:** configured through Django 6.1's `MAILERS` setting (the older `EMAIL_BACKEND` style is deprecated and removed in Django 7.0). Console backend in development (the link prints in the terminal), in-memory backend in tests, SMTP for real use with credentials in `.env`.
8. **Privacy:** exports use participant labels and pseudonymous ids, and include identities only with `--include-identities`.

**Forum flow:**
1. After login: the topic list and open threads. Start a thread on a topic or join an open one. Labels are assigned at random when the conversation fills. A third person gets "thread full".
2. The thread page shows the proposition, the messages, and the compose box with the character counter. A "How this works" link explains the moderator and the limits.
3. Posting goes through `post_message()` (section 4). On success, `transaction.on_commit` creates a `pending` run.
4. The page polls `/c/<id>/messages?after=<seq>` and shows moderator posts when ready, each marked "in reply to #N". If a run was skipped for budget reasons, the page says moderation is paused.

## 11. Runner
- `manage.py run_moderator` polls for `pending` runs and processes them **one at a time, oldest first**, which serializes runs per conversation. A run is claimed with an atomic update. SQLite runs in WAL mode.
- A **reaper** (run on worker startup and periodically) resets runs stuck in `running` past `RUN_TIMEOUT_SECONDS`: back to `pending`, or `failed` after `RUN_MAX_ATTEMPTS`.
- `scripts/dev.sh` starts the web server and the worker. `MODERATION_RUN_MODE = "sync"` runs the pipeline inline, for tests.
- **Late runs:** each run uses the transcript up to `snapshot_seq`. If a newer user message arrived first, the reply is still posted, flagged `is_stale`, and shown as "in reply to #N".
- The pipeline is a plain function, `run_moderation(run)`, reading everything it needs from the run row. Replays create runs with `kind = replay`, which never post.

## 12. Fetching and reconstruction
- `moderation/queries.py`: `get_conversation_bundle(id)`, `list_conversations(**filters)`, `export_conversation(id)`
- Management commands: `export_conversation`, `export_all`, `seed_topics`, `budget`, `reset_breaker`, `spike`, `golden`, `run_moderator`
- `analysis/metrics.py`: detection, action, false-positive and data-quality tables per dimension, plus the gap decomposition, as tested functions returning pandas frames.
- Minimal Django admin: runs, calls, issues, acts, users.

## 13. Module layout
```
/workspace
  manage.py, requirements.txt (runtime), requirements-dev.txt (adds pytest), .python-version, pytest.ini,
  README.md, .env.example, .gitignore, CLAUDE.md, scripts/dev.sh
  docs/plan.md
  config/            settings.py, urls.py, tunables.py   <- every number you might change
  accounts/          User model, registration, email confirmation, login throttling, templates/
  forum/             models, limits.py, services.py (post_message), views, urls, templates/, static/compose.js, static/poll.js, admin
  moderation/        taxonomy.py, schemas.py, prompts/, llm.py (only SDK import + budget guard), fake_llm.py,
                     pricing.py, budget.py, quotes.py, agents.py, pipeline.py, worker.py, features.py,
                     queries.py, admin.py, management/commands/
  evaluation/        (phase B) models, annotation UI, llm_rater.py, consensus.py, matching.py, calibration.py
  rubrics/           factual_accuracy_v1.md, abusiveness_v1.md
  analysis/          metrics.py, prereg.md
  golden/            transcripts/*.json, expected/*.json, results/ (git-ignored)
  tests/
```

## 14. Build steps
Each step: write the listed tests first and watch them fail, implement, get everything green, then commit. Don't start the next step until the "done when" items are all true.

### Phase A: the site and the logs

**Step 1 — Project skeleton** (built)
- Built:
  - **Tooling:** the container's system Python is stripped down (no `sqlite3`, `venv` or `pip`), so a full standalone Python 3.13 was installed with `uv` (release binary from GitHub, SHA-256 checked) into `/workspace/.venv`. `.python-version` records `3.13`.
  - **Repo:** `git init`, `.gitignore` (`.env`, SQLite files, `golden/results/`, `.venv/`), `pytest.ini`, `.env.example`.
  - **Dependencies:** `requirements.txt` (runtime, exact pins: Django 6.1.1, anthropic, pydantic, argon2-cffi, django-axes, python-dotenv) and `requirements-dev.txt` (adds pytest and pytest-django), so a deployed site doesn't install test tools.
  - **Settings:** `config/settings.py` reads secrets from environment variables. `.env` is loaded by `manage.py`, `wsgi.py` and `asgi.py`, not by settings, so tests never depend on a local `.env`. Blank values count as unset. `DJANGO_ENV` is `development` by default; `production` refuses to start without `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` and turns on secure cookies.
  - **Database:** SQLite in WAL mode with immediate transactions; `DJANGO_DB_PATH` moves the file (for a persistent disk).
  - **Tunables:** `config/tunables.py` holds every number with a comment. `LLM_ENABLED` is off by default.
  - **Accounts:** custom `accounts.User` (set as `AUTH_USER_MODEL` before the first migration), unique username and email ignoring case, Argon2 password hashing, password validators, `email_verified_at`.
  - **Email:** Django 6.1 `MAILERS`.
  - **Home page:** a placeholder at `/`.
- Tests (80, all passing): settings and production strictness, WAL on a real file, tunables (exposed as settings, assigned only in `tunables.py`, each commented, agreed defaults, Decimal money), the User model (case-insensitive uniqueness, required email, Argon2, superuser), the home page, system checks, no missing migrations, repo hygiene. Django's Django-7.0 deprecation warnings are errors in tests.
- Checked by hand: `migrate` on a fresh database, the database file really in WAL mode, a running server returning the home page.
- **Step 1 fixes** (a later review by an independent testing agent, then fixed by a building agent; each phase by a separate agent):
  - Case-insensitive uniqueness now uses derived `username_key`/`email_key` (migration `0002`, with a frozen normalizer and a loud failure if existing rows collide), and usernames are ASCII-only.
  - Production refuses to start with the public development key, a secret shorter than 50 characters, or `*` as an allowed host; development mode refuses a non-local host unless `DJANGO_ENV` is set; a relative `DJANGO_DB_PATH` resolves against the project folder and its parent directory must exist.
  - Secret protection (section 7): scanner, hooks, install script, `.gitignore` patterns. The scanner's "unquoted lowercase name is a variable" exemption applies only to Python source files.
  - Test hygiene: environment-independent tests, AST-based `.env` check, tunable-name collision test, exact pinned requirements.
- Known and expected: `manage.py check --deploy` still reports the console email backend (real SMTP arrives in step 6) and the HTTPS redirect and HSTS settings (the deployment step).
**Step 2 — LLM gateway and budget guard**
- Build: `LLMCall` and `GuardState` models; `pricing.py`; `budget.py` (ledger sums, caps, reservation, worst-case run cost); `llm.py` with a single `call(purpose, agent, model, system, messages, output_schema, max_tokens, run=None, conversation=None)`, which checks the kill switch, the breaker and the budget, makes the call with prompt caching and structured output, logs everything, prices it, and updates the breaker; `fake_llm.py` with scripted responses; `manage.py budget` and `manage.py reset_breaker`. Confirm the current SDK details for structured output, caching and token counting against Anthropic's docs while building.
- Tests first: an over-budget call is refused and logged as `refused_budget` for each cap (total, per day, per conversation, eval); an unknown model is refused; the kill switch refuses; the breaker trips on both spend-limit errors and on repeated errors, stays tripped, and resets only via the command; a ledger read failure refuses the call; cost is computed correctly including cache tokens; every call writes an `LLMCall` row with the exact request; no module except `llm.py` imports `anthropic`; the fake is used whenever tests run.
- Done when: all tests pass with no network access, and `manage.py budget` prints caps, spend (zero), and the worst-case run cost.

**Step 3 — Prompt spike and golden set** (first real API calls; needs the Console limit from 3.1 and the API key in `.env`)
- Build: `taxonomy.py`; `schemas.py` (Pydantic models for both agents' outputs); `prompts/master_v1.md` and `prompts/intervenor_v1.md`; draft `rubrics/factual_accuracy_v1.md` and `rubrics/abusiveness_v1.md`; `golden/transcripts/*.json` covering a planted factual error on each side (a pair), a planted abusive phrase on each side (a pair), a long message with one bad phrase in the middle, a message that is bad as a whole, a benign exchange, and an injection attempt; `quotes.py` (quote matching); `manage.py spike --max-usd 0.50 [--dry-run]`, which runs both agents on each transcript through `llm.call()` and writes readable results to `golden/results/<timestamp>/`.
- Tests first: the schemas accept valid examples and reject invalid ones; quote matching finds exact and normalized matches and reports not-found; transcripts load; `--dry-run` estimates cost without any API call; the command refuses to run without `--max-usd`.
- Then: run it for real, review the outputs together with the user, and iterate on prompts. Total spike spend stays under $0.50.
- Done when: the user approves the output format and quality; the approved expected outputs are saved in `golden/expected/`; the schemas are frozen for step 4.

**Step 4 — Database models**
- Build: `forum` models (`Topic`, `Experiment`, `Conversation`, `Participant`, `Message`) and the remaining `moderation` models (`ModerationRun`, `Issue`, `IssueDisposition`, `InterventionAct`, `ActSource`, plus `LLMCall.run`) with migrations and constraints.
- Tests first: `seq_no` is unique and increasing per conversation; labels are unique per conversation; a user can't join the same conversation twice; `quote == content[quote_start:quote_end]` for matched issues; one disposition per issue; replays can't have a posted message.
- Done when: migrations apply cleanly on a fresh database and all tests pass.

**Step 5 — Moderation pipeline**
- Build: `agents.py` (prompt building from labels and text only, calling `llm.call()`); `pipeline.py` with `run_moderation(run)`: build the Master input → validate → store issues (item-level rejection) → build the Intervenor input → validate → store dispositions and acts (item-level rejection, act cap) → compute act features → post the moderator message (live runs only) → finish the run. `features.py`.
- Tests first (all with `FakeLLM`): intervene path; no-intervention path; structural failure retries once and then fails without posting; an unfindable quote is rejected while the rest of the run succeeds; the only-new-issues rule rejects old-message issues of the wrong type; acts over the cap are rejected; every valid issue gets exactly one disposition; a budget refusal marks the run `skipped_budget`; a stale run is flagged; replays never post; prompts contain no usernames or emails; the config snapshot records models and prompt hashes.
- Done when: all tests pass, and running the pipeline on a golden transcript with scripted fake output produces the expected rows.

**Step 6 — Accounts**
- Build: registration, confirmation email with a signed expiring link, login and logout, `django-axes` throttling, password reset and change, templates.
- Tests first: registration creates an inactive user and sends one email; the link activates once, expires, and rejects tampering; unconfirmed users can't log in; duplicate usernames and emails are rejected regardless of case; passwords are stored hashed (never plaintext); weak passwords are rejected; repeated failed logins are throttled; the register page gives the same response for new and existing emails; password reset works end to end.
- Done when: all tests pass and a manual register → confirm (link in the terminal) → log in works.

**Step 7 — Forum web, including input limits**
- Build: topic and thread list; start and join (capacity, random labels on fill); `forum/limits.py` (`count_message_chars`); `forum/services.py` (`post_message()`: length, emptiness, rate limit, conversation cap, then save and enqueue the run on commit); thread page with the compose box, live counter (`static/compose.js`), the limit shown, and error display that keeps the user's text; polling endpoint and `static/poll.js`; "How this works" page.
- Tests first: posting exactly `MAX_MESSAGE_CHARS` is accepted; one character over is rejected with the error text naming the count, the limit and the reason, the text preserved in the form, **no message saved and no run created**; a hand-made POST that skips the page gets the same rejection; whitespace-only messages are rejected; emoji and accented characters are counted as the rules say (code points after NFC and trimming); posting too fast is rejected with the wait time and the reason for the 30-second gap, and the wait counts from that participant's own previous message; **every rejection reason in the list above has a test that checks its code, that the text names the reason and the next step, that the user's text is preserved, that nothing is saved and no run is created, and that no internal error text or stack trace is ever shown;** the conversation message cap is enforced; a third joiner gets "thread full"; labels are randomized across conversations with a recorded seed; non-participants can't post; the polling endpoint returns only newer messages; a skipped-budget run shows "moderation paused".
- Done when: all tests pass and two browser sessions can hold a conversation, with moderation running in `sync` mode against `FakeLLM`.

**Step 8 — Worker**
- Build: `manage.py run_moderator` (claim oldest pending run atomically, run it, repeat), the reaper, `scripts/dev.sh`.
- Tests first: runs are processed oldest first; two workers never claim the same run; a stuck run is reset and eventually failed after too many attempts; a crash inside one run marks it failed and the worker continues.
- Done when: `scripts/dev.sh` runs the site with the worker, and moderator posts appear via polling.

**Step 9 — Queries, exports, analysis functions, admin**
- Build: `queries.py`, the export commands, `analysis/metrics.py` on fixture data, the minimal admin.
- Tests first: an export round-trips the full nested structure, including calls and costs, with no emails or usernames unless `--include-identities`; the metrics functions give known answers on a small hand-built fixture (detection, action, false positives, data quality by side).
- Done when: all tests pass and one real conversation exports cleanly.

**Step 10 — Seed topics, README, golden run**
- Build: `manage.py seed_topics` (topics with propositions and `leans` with rationales); `README.md` (setup, `.env`, where the tunables are, how to run, one message traced end to end, the cost controls); `manage.py golden --max-usd <n>` comparing live outputs with `golden/expected/`.
- Done when: the verification checklist (section 16) passes.

### Phase B: the evaluation pipeline
**Step 11 — Rubrics:** finalize `factual_accuracy_v1` and `abusiveness_v1` with the user, and write the human rater guidelines from the same text.
**Step 12 — Evaluation models, annotation page, calibration-set builder.** Tests: findings enforce `quote == text[start:end]` and "not scorable requires a reason"; raters never see blinded fields.
**Step 13 — Paired-set loader and replay command:** `--max-usd`, `--dry-run`, replicates, both label assignments, the same message limit as real users.
**Step 14 — LLM rater runner,** consensus, and matching to Master issues.
**Step 15 — Calibration report:** human–human, LLM–human, not-scorable agreement, side symmetry, and the human-only benchmark.
**Step 16 — Analysis:** write `analysis/prereg.md` (headline scheme, headline metrics, intensity thresholds, agreement thresholds) **before** looking at results; then the per-dimension detection, action, false-positive and data-quality tables with clustering.

## 15. Testing conventions
- `pytest` never touches the network: `FakeLLM` is the default in tests, and a test fails if the real client is constructed.
- Tests that need the real API are separate management commands (`spike`, `golden`) with their own `--max-usd`, never part of `pytest`.
- Code-level guards: only `llm.py` imports `anthropic`; tunables are assigned only in `config/tunables.py`; prompt builders receive only labels and text.

## 16. End-to-end verification (after step 10)
- `pytest` passes. `manage.py budget` shows spend under the caps.
- Run `scripts/dev.sh` and, in two browser sessions:
  1. Register two users, confirm both via the link in the terminal, and log in. An unconfirmed account can't log in.
  2. Start and join a thread. Labels are randomized across several conversations.
  3. Try to post a message over the limit: it's rejected with the explanation, and the text stays in the box. Check that nothing was saved and no run was created.
  4. Post an unsupported claim and an insult: moderator posts quote the specific phrases. A benign message logs a no-intervention run.
  5. Set `BUDGET_SITE_USD_PER_DAY = 0` in `config/tunables.py`: runs show `skipped_budget` and the page shows "moderation paused".
- `export_conversation <id>` produces the full nested JSON, with calls and costs and no identities.

## 17. Open decisions
- **First push to GitHub.** No remote exists yet. Before the first push: keep the repository private, turn on secret scanning and push protection in its Code security settings, and clean the local history (the first local commit contains two harmless false positives that the pre-push scan will block: the public development key line and a test password literal), for example by squashing the local commits into a clean history, which is safe while nothing has been pushed. Revoke any real key that is ever committed.
- **Hosting and HTTPS** before real users. May cost money.
- **Email sending** (SMTP account) for real confirmation emails.
- **Rubric wording**, settled with the human raters in step 11.
- **Human annotation tool:** a staff-only page in the site (default) or an external labeling tool.
- **Models** (in `config/tunables.py`): development and spike default to `claude-haiku-4-5`; the live moderator defaults to `claude-sonnet-5`; judges are `claude-sonnet-5` and `claude-haiku-4-5`. Decide after the spike whether Haiku is good enough for the live moderator.
- **Intensity thresholds** and the **headline scheme**, fixed in the pre-registration.

## 18. Notes for later
- **Neutrality as equalized odds:** P(act T | trigger C, side X) ≈ P(act T | C, side Y). Also check unwarranted interventions per side.
- **Style:** clarity may legitimately change how the moderator responds. Register, dialect or formality at equal clarity must not.
- **Quality evaluation:** the same rater machinery can score the moderator's own factual acts (target = an `InterventionAct`), and whether flagged "unclear" statements really were unclear.
- **Intended effect:** from the Intervenor's self-tags, later from a rater's directional score on a sample.
- **Length bias:** the message cap bounds lengths, but length still varies below it; test it later as a content-feature bias.
- **More dimensions** (fallacy, unclarity, strawman, …) are added with a rubric file and a registry entry.
- **Future:** specialized fact-check agents, identity and ordering bias tests, a post-discussion survey, process rules enforced in code, and a task queue if the worker isn't enough.
