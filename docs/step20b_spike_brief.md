# Step 20b spike brief — minimal, guarded `web_search` support in `moderation/llm.py`

Purpose: answer the open API questions blocking Step 20b's real design (does structured output compose with `tools`
in one call, or does this need two calls; what does `web_search`'s own metered fee look like in `usage`; real
latency) — via a tiny number of real, guarded calls, never bypassing the money-safety gateway.

This is NOT the final Research component. It is the smallest safe addition that lets one guarded, real,
`web_search`-enabled call happen, so the spike can run and Step 20b's actual contract can be written from facts
instead of guesses. No new `ModerationRun` kind, no worker wiring, no UI, no `moderation/research.py` yet.

## Why a new function, not an extended `call()`

`call()` has an extensive, load-bearing test suite covering every safety path (kill switch, breaker, budget,
ledger). Rather than add an optional `tools` parameter to it (risking a regression in the one function every real
call already depends on), the plan is:

1. Extract the shared preflight logic `call()` already runs (steps 1-4 in its own numbered comments: kill switch and
   API key check, circuit breaker check, model allow-list check, budget reservation + ledger row creation) into a
   private helper, e.g. `_preflight(*, purpose, agent, model, system, messages, max_tokens, ..., request_extra=None)`
   that returns `(row, probe, reserved)` or raises exactly the same exceptions `call()` raises today at each of those
   steps. `request_extra` lets the caller add fields (like `tools`) to the logged `request` dict without the helper
   needing to know what they mean.
2. `call()` itself is rewritten to call `_preflight()` then do exactly what it does today (steps 5-6: `messages.parse()`,
   price the real usage, finish the row) — its own behavior must be **byte-for-byte unchanged**. The existing test
   suite for `call()` (all of it) must pass with zero changes to any test file, proving the refactor is behavior-preserving.
3. A new function, `call_with_web_search(*, purpose, agent, model, system, messages, max_tokens, max_uses=3,
   conversation_id=None, run_id=None, session=None, cache_system=True)` calls the same `_preflight()`, then makes its
   own call via `client.messages.create(tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses":
   max_uses}], ...)` (NOT `.parse()` — no `output_schema` yet; this spike function returns plain text plus whatever
   `web_search_tool_result` blocks came back, so the spike can inspect them directly). It finishes the ledger row the
   same way `call()` does (price real usage from `message.usage`, `_finish_row`), but must also capture whatever
   field(s) report the web-search-specific fee (verify the exact field name against Anthropic's current docs/SDK
   before writing this — do not guess or copy a stale one from training data; `shared/live-sources.md` in the
   `claude-api` skill has the pricing page). If a per-search fee field exists in `usage` and isn't priced by
   `moderation/pricing.py` yet, do NOT invent pricing logic for it in this step — just log the raw usage object into
   the ledger row's existing `request`/`raw_response`-adjacent fields (or a new column if truly needed — ask Claude
   before adding one) so the spike can see the real numbers. Getting pricing exactly right is Step 20b's job, once
   the numbers are known.
4. Return type: a small dataclass or reuse-adjacent shape (`call_id`, the response's text content, the raw
   `web_search_tool_result` blocks if any, `stop_reason`, token usage, latency) — whatever shape makes the spike
   script easy to write and read; this is explicitly provisional, not a locked API.

## Safety invariants that must not change

- Only `moderation/llm.py` imports `anthropic` — the new function lives in this file, nothing new imports the SDK
  elsewhere. The existing test enforcing this must still pass unchanged.
- `LLM_FORBID_ATOMIC_CALLS` still applies — `call_with_web_search` must refuse to run inside a transaction, exactly
  like `call()`.
- Kill switch, circuit breaker, model allow-list, and budget caps all still apply, unchanged, via `_preflight()`.
- A refused or errored `call_with_web_search` still writes a ledger row (via the same `_write_refusal`/`_finish_row`
  helpers `call()` already uses) — no silent, unlogged attempt is possible.
- `purpose` must still be one of the existing `ALL_PURPOSES` — do not add a new purpose value in this step; use
  `"spike"` for the real spike run (already budget-capped separately via `BUDGET_SPIKE_USD_TOTAL`).

## Tests (testing agent, from this brief, FakeLLM only — no real call in any test)

- `call()`'s entire existing test suite passes with zero test-file changes (proves the refactor preserved behavior).
- `_preflight()` in isolation: kill switch off, breaker open, and an unknown model each refuse AND write a refusal
  row (`_write_refusal`, exactly as `call()` already does for each of these today — correction, 2026-09-27: an
  earlier draft of this brief said kill-switch-off refuses "before any ledger row," which contradicted `call()`'s
  own unchanged, already-passing behavior; the refusal row has always been written for that case too); over-budget
  refuses (reservation not double-counted); a successful preflight
  returns a `pending` ledger row with the right `purpose`/`agent`/`model`/`request` (including whatever
  `request_extra` was passed, e.g. a `tools` key, so it's visible in the ledger for audit).
- `call_with_web_search()`: refuses under the same conditions as `call()` (kill switch, breaker, budget, bad model,
  atomic-block); on a `FakeLLM`-style stub returning a scripted `web_search_tool_result`-shaped response, finishes
  the ledger row with `status="ok"`, real usage priced, and returns the tool-result content intact; a provider error
  finishes the row `status="error"` and does not raise past what `call()` raises for the equivalent failure today.
- Whatever `FakeLLM`/stub extension is needed to script a `web_search_tool_result`-shaped reply — check
  `moderation/fake_llm.py`'s existing conventions first and extend it consistently rather than building a separate
  fake client.

## What Claude does after this lands (not the agents' job)

Personally run a tiny number (2-3) of real, guarded `call_with_web_search` calls — via `config.settings_live`
(`LLM_ENABLED=True` for that process only, exactly like `scripts/dev.sh --live` already does; `config/tunables.py`
itself is never edited), `purpose="spike"`, a `SessionBudget` capped well under $0.50 — to learn: whether
`web_search` results come back within one response or need a client-side loop; the exact per-search fee and its
`usage` field name; real latency; whether a follow-up structured-output call is needed to format a final note, or
whether one call can do both. Findings become the actual Step 20b contract (the Research component, the new
`ModerationRun` kind, pricing/budget changes, worker wiring, the UI control).

## Coordination

- Claude does the `moderation/llm.py` refactor and addition directly (not delegated), given how safety-critical this
  file is.
- A testing agent writes the tests above independently, from this brief, without reading Claude's diff, and never
  edits `moderation/llm.py`, `moderation/fake_llm.py` (except to extend it per the brief), `moderation/pricing.py`,
  or `moderation/budget.py`.
- Do not touch `moderation/pipeline.py`, `moderation/worker.py`, `moderation/agents.py`, taxonomy, schemas, or
  prompts — none of that is in scope here.
