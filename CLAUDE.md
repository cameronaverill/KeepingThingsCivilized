# AI-Moderated Discussion Forum

The full design and build plan is in `docs/plan.md`. Read it before doing any work. It is the source of truth; older versions (`docs/plan_v1.md` … `plan_v4.md`) are history only. `docs/neutrality.md` holds the user's own neutrality criteria (the standard every moderation and evaluation design must meet; don't reword the user's sections). `docs/plan_summary.md` is a short summary for the user; if you change the plan, update the summary too.

- Build one step of section 14 at a time, in order, tests first. Don't start a step until the previous one's "done when" items are true.
- If a step shows the design is wrong, update `docs/plan.md` first, then the code.
- The user pays for API calls: make no real Anthropic API call before step 3, and only after the user confirms the Console spend limit (section 3.1) is set. Every tunable number lives in `config/tunables.py`.
- **Working method:** for each step, spawn a separate coding agent (writes the implementation) and a separate testing agent (writes and runs the tests). Claude owns the system architecture: write precise briefs from the plan, review what the agents return against the design, and integrate. Wait for the user's approval before starting the next step.
- Use `.venv/bin/python` (a full Python 3.13 installed with uv); the container's system Python is stripped down and can't run Django.
- Git commits use the repo-local name `caverill` and end with the Co-Authored-By line from the session's attribution guidance.
