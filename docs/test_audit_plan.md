# Test-suite audit plan (final quality gate)

Status: planned, added 2026-09-25 at the owner's request. Referenced from `docs/plan.md` as Step 18. Nothing here has been run yet.

## What this is
The owner supplied an "audit and ruthlessly improve the test suite" prompt (kept verbatim in the appendix). This plan adapts it to this project: Python 3.13, Django 6.1, pytest and pytest-django, and the way we already work (separate agents, Claude as architect). It is the last step, after the site and the evaluation pipeline exist, and it applies to the whole suite (about 3,000+ tests by now).

It complements, and does not replace, the per-step mutation and break-it passes that each step's testing agent already does. Those check one step's tests against that step's code; this audit looks across the whole suite for gaps, false positives and unreadable tests.

## Ground rules
- **Analysis first, code last.** No test is written or changed until all three analysis agents have finished and Claude has consolidated their findings (the prompt's own rule).
- **Three separate analysis agents, never one agent doing all three roles**, and each works on a slice of the suite, so no agent is overloaded (the owner's standing instruction). The slices follow the code: `accounts` (`tests/`, `tests/accounts_registration/`, `tests/accounts_auth/`); `moderation` guard (`tests/moderation/` step 2 files); `moderation` prompts, schemas, series and spike (step 3 files); `forum` and moderation models (`tests/models_forum/`, `tests/models_moderation/`); then one slice per later step (pipeline, forum web, worker, queries and admin, evaluation). Each slice gets its own trio of agents, run in parallel where the slices are independent.
- **Analysis agents are read-only.** They write their findings to a file under the scratchpad and touch neither tests nor source. They use true `rsync -a` copies (never hard links or symlinks) for any experiment, each in a uniquely named folder, and never write to `/workspace`.
- **Nobody reads or prints `.env` or any key; no real API calls; no network.** Tests keep using `FakeLLM`.
- **Only testing agents edit tests, and only after the checklist is approved.** A separate builder agent fixes any source bug that a strengthened test reveals (code bug vs test bug vs contract ambiguity is classified by Claude, as in every step).
- **The owner approves the consolidated checklist before any test is changed** (the working method: the owner approves each step).

## Step 1: three analysis agents (per slice, in parallel)
1. **Code Coverage and Logic Auditor.** Maps the slice's tests against its source. Lists missing happy paths, edge cases, boundaries (None, empty strings, overflow, Unicode, off-by-one at every tunable), and unexercised error-handling branches. Uses `coverage` branch coverage as evidence but does not trust it as proof (a line can run without being asserted on). Output: a bulleted list of specific uncovered logic gaps, each naming the source lines.
2. **Chaos and Mutation Engineer.** Tries to break the tests: brittle mocks that pass while the implementation is broken, hardcoded values that hide bugs, tautological tests (assert nothing of value, or test only the mock or the test helper), over-mocking, tests coupled to implementation detail, order-dependent tests, and tests that pass against an empty skeleton. Evidence is real mutants run on a true copy, listing the survivors. Output: structural flaws, over-mocking violations and false positives, each with the mutant or reasoning that shows it.
3. **Style and Maintainability Reviewer.** Judges readability: messy setup, missing Arrange-Act-Assert or Given-When-Then structure, poor names (a name should state the behaviour and the expected outcome), duplicated helpers, giant tests, magic numbers that should read from `config/tunables.py`, and inconsistent helper naming. Output: concrete feedback and the specific style violations.

Each agent's findings appear as a separate, clearly labelled section, as in the original prompt.

## Step 2: consolidated remediation plan (Claude)
Claude merges the three reports per slice into one prioritized checklist of exactly what to fix or add: security and neutrality-critical gaps first (budget guard, input limits, no-self-reply rule, enumeration and lockout behaviour, quote matching), then false positives, then coverage, then style. Duplicates are merged; disagreements between agents are settled by Claude with the reason recorded. The checklist goes to the owner for approval.

## Step 3: execution (testing agents, then a review)
Testing agents rewrite or extend the tests to the approved checklist, one slice per agent, subject to these rules (from the original prompt, adapted):
- **No conditional logic (`if`/`else`, loops with branches, `try/except` that swallows) inside a test.** Use `pytest.mark.parametrize`, separate tests, or fixtures. Exception-expecting tests use `pytest.raises(..., match=...)`.
- **Strict, expressive assertions.** Assert exact values, exact exception types and messages, exact row counts and exact query results. Avoid bare `assert x` or `assert result` where an equality or specific check is possible, and avoid `assertTrue`-style vagueness.
- **Group tests logically** in classes (`class TestRegistrationEmail:`), one behaviour per test, Arrange-Act-Assert with blank lines between the parts, names like `test_confirm_link_rejected_after_first_use`.
- **No tautologies:** a test must fail against an empty or wrong implementation (shown by running it against a scratch copy without the feature, as each step already requires).
- Tunable-dependent tests read values from settings and never hard-code them.
- Tests stay deterministic, independent of order, and free of network access.

After the rewrite Claude checks the results: the full suite is green; the mutation survivors listed in Step 1 are now killed; branch coverage did not drop; a snapshot export test and the secret scanner are clean; then the owner approves and the changes are committed slice by slice.

## Done when
All slices have gone through the three analyses, the owner has approved the consolidated checklists, the approved changes are made and verified, the previously listed surviving mutants are killed or explained as equivalent, and the audit's summary (what was found, what changed, what remains) is recorded in `docs/test_audit_report.md`.

## Appendix: the original prompt (verbatim, as supplied by the owner)

> You are acting as a Lead Test Architect and Orchestrator. Your goal is to critically audit, stress-test, and ruthlessly improve the existing test suite provided below.
>
> To do this thoroughly, you will spawn three specialized virtual sub-agents to analyze the code from distinct perspectives. Do not write any code until all sub-agents have completed their analysis.
>
> ### STEP 1: SUB-AGENT ACTIVATION & ANALYSIS
> Spawn the following sub-agents and output their individual findings in clear, separate sections:
> 1. [The Code Coverage & Logic Auditor] Task: Map the current tests against the source code. Identify missing happy paths, edge cases, boundaries (nulls, empty strings, overflows), and error-handling branches. Output: A bulleted list of specific, uncovered logic gaps.
> 2. [The Chaos & Mutation Engineer] Task: Actively try to break the tests. Find brittle mocks that pass even if the implementation is broken, look for hardcoded values that hide bugs, and identify "tautological tests" (tests that assert nothing of value or just test the mock itself). Output: A list of structural flaws, over-mocking violations, or false positives in the current suite.
> 3. [The Style & Maintainability Reviewer] Task: Evaluate readability. Check for messy setups, lack of clear AAA (Arrange-Act-Assert) or Given-When-Then structure, and poor test naming conventions. Output: Concrete feedback on test readability and style violations.
>
> ### STEP 2: CONSOLIDATED REMEDIATION PLAN
> As the Lead Architect, synthesize the sub-agents' findings into a unified, prioritized checklist of exactly what needs to be fixed or added.
>
> ### STEP 3: EXECUTION (THE REFACTORED CODE)
> Provide the complete, updated test file. Ensure the new code strictly adheres to these rules:
> - No conditional logic (if/else) inside the tests.
> - Use strict, expressive assertions (avoid generic assertTrue/assertNotNull where specific equality or exception checks are better).
> - Group tests logically using nested suites/describe blocks if the framework supports it.
>
> ### CONTEXT FOR YOUR TASK:
> - Tech Stack & Testing Framework: [e.g., TypeScript / Vitest / MSW]
> - Source Code to Test: [PASTE YOUR SOURCE/IMPLEMENTATION CODE HERE]
> - Existing Test Code to Improve: [PASTE YOUR CURRENT TEST FILE HERE]

Context filled in for this project: Python 3.13, Django 6.1, pytest and pytest-django; the source is the slice's app code and the tests are the slice's test folders, as listed above.
