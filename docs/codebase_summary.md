# Codebase summary: the AI-moderated discussion forum

This page explains how the app is built, in plain language. The design reasoning lives in `docs/plan.md`; this page is the
map of what exists in the code. It was written on 2026-10-01 from the code as it stood then.

## What the app does

Two people debate a short statement (a "proposition"), one for it and one against. An AI moderator reads every message
and, only when it would clearly help, adds a short note. The moderator's main rule is neutrality: equal contributions get
equal treatment, whichever side they come from. Because that claim has to be provable, the app records every moderation
decision, including the decision to stay silent, so a separate evaluation can test the moderator for bias.

## The pieces, in one picture

The code is a Django web app (Python) with one database file. Each folder has one job:

| Folder | Job |
|---|---|
| `accounts/` | Sign-up, login and passwords. |
| `forum/` | The website: propositions, pairing people, the conversation page, posting, blocking. |
| `moderation/` | The AI moderator, the cost guards and the background worker. |
| `evaluation/`, `seeding/`, `analysis/`, `golden/` | Testing the moderator for bias (not used by visitors). |
| `config/` | Settings, URLs and `tunables.py`, the one file holding every adjustable number. |
| `rubrics/`, `moderation/prompts/` | The plain-text scoring guides and the instructions given to the AI models. |
| `tests/` | The automated tests (over 11,000). |

## What a visitor sees

1. **Register and log in** with a username and password. There is no email at all in this version, and a forgotten
   password is reset by the owner with a command.
2. **Browse or propose a proposition.** Anyone logged in can write one (up to 200 characters, 20 a day). Example:
   "Cities should limit their local police's cooperation with federal immigration enforcement."
3. **Pick a side, "pro" or "con".** The site then pairs the visitor with someone waiting on the *opposite* side of the same
   proposition. If nobody is waiting, a new conversation is created and waits for a partner. Two people on the same side
   never pair.
4. **Debate.** Each message is capped at 3,000 characters, one message every 30 seconds per person, and 30 user messages in total per
   conversation. Anyone who cannot post is told exactly why. Either person can end the conversation or block the other.
5. **Read moderator notes.** The page checks for new messages every 3 seconds. Visitors see "You" and "The other
   participant"; the moderator itself only ever sees the letters A and B.

## What happens when someone posts a message

This is the heart of the app, so here it is step by step, with an example.

1. The post goes through one function (`forum/services.py`), which checks the length, the speed limit and whether the
   conversation is still open, then saves the message and queues a moderation job.
2. A **background worker** (`moderation/worker.py`) picks up the job. The website never waits for the AI.
3. The **Master Moderator** (an AI model) reads the recent conversation and lists problems, each tied to an exact phrase.
   Example: it flags "about 80 million" as a possible factual error, with an intensity from 0 to 4.
4. The code checks each flagged item: the quoted phrase must really appear in the message. A bad item is thrown out on its
   own, and the rest of the run is kept.
5. The **Intervenor** (a second AI model) sees those flags and chooses whether to post. Staying silent is a normal and
   common choice. If it speaks, it posts up to three short "acts", each one of 11 kinds, such as asking for a source
   ("Could a source be given for the figure in message 4?"), correcting a fact, or asking for clarification.
6. The code checks the wording too. For instance, a note may not contain "Participant A" or "the other side", because
   each reader would take that to mean someone different.
7. The note is posted as a moderator message and the whole run (every flag, every decision, every reason) is stored.

A related feature is **web research**. When the moderator asks for a source, either participant can click "Provide
factual background". A second job then runs a web search and posts a short, sourced note. That research step is never
told who clicked or which side the claim favors.

A third feature is the **preview check**: before posting, a user can see whether the moderator would step in on their
draft. The same Master and Intervenor judge the draft, and if the draft is posted unchanged the stored answer is reused.

## Keeping the cost under control

The owner pays for every AI call, so there are four layers of protection.
- **One doorway.** Every AI call goes through a single function (`moderation/llm.py`) that checks a budget first and
  refuses anything that would go over: a total for the site, a daily cap, a per-conversation cap, and a separate budget
  for evaluation (currently $25).
- **A kill switch** (`LLM_ENABLED`) that turns all real calls off.
- **A circuit breaker** (`moderation/breaker.py`) that stops calls after repeated errors and tries again after a cooldown.
- **A full ledger.** Every call is recorded in the `LLMCall` table with its cost, so spending can always be audited.

## How the data is stored

The database holds these main tables (full detail is in `docs/database_schema.md`):

- **User**: the account.
- **Topic**: a proposition, with an optional opposing wording.
- **Conversation**: one debate on a topic. It is `open` (waiting), `active` (two people) or `closed`.
- **Participant**: one person's seat in a conversation, with a random letter label (A or B) and their side.
- **Message**: one post, numbered in order, written either by a user or by the moderator.
- **Block**: who has blocked whom.
- **ModerationRun**: one moderation job, with its status (pending, running, done, failed or skipped) and the decision.
- **Issue**: one problem the Master flagged. **IssueDisposition** records whether the Intervenor acted on it or declined.
- **InterventionAct**: one note the moderator wrote, with its type, tone and text.
- **LLMCall**: one AI call, its cost and its raw reply. **GuardState**: the circuit breaker's memory.
- **PreviewMode / PreviewCheck**: the preview feature's settings and results.

The `evaluation/` app adds tables for an older, heavier bias-rating design (raters, ratings, consensus findings and a
calibration set). That design has been replaced by the simpler one below and is slated to be retired.

## Where the schema leaves room to grow

The first version is a two-person forum, but several columns already point past it. Nothing here is built out beyond what
the MVP needs; these are places where later features can be added without reshaping the database.

- **More than two participants.** The limit of two comes from a setting (`MAX_PARTICIPANTS`), not from the table layout,
  and each Participant has a `join_order`. A group discussion would mostly be a settings change plus pairing logic.
- **Other kinds of speaker or reply.** `Message.author_type` can take new values beyond "user" and "moderator", and
  `in_reply_to` already links a message to the one it answers, which could support threaded replies.
- **Topic metadata.** `Topic.leans` is an unused slot for notes on which way a topic leans on various scales,
  `opposing_position` lets a topic carry custom "con" wording, and `hidden` lets an admin pull a topic without deleting
  its conversations.
- **Email.** `User.email` and `email_verified_at` exist but are unused, so an email flow can be restored later.
- **More kinds of moderation job.** `ModerationRun.kind` already has "live", "replay" and "research"; a new job type is a
  new value. `source_act` and `requested_by` tie a research job to the note and the person that asked for it.
- **More things to check for.** Each Issue has a `dimension`. Three exist (factual accuracy, abusiveness and clarity), each with a
  scoring guide in `rubrics/`, so a new dimension is a new guide plus a new issue type.
- **A "where you agree and disagree" feature.** Built in step 21: after every 4th user message (`AGREEMENT_MAP_EVERY_N_USER_MESSAGES`)
  a live run also asks the Intervenor for an `identify_agreement_disagreement` note built from the stored `discussion_map`
  and posts it as an ordinary moderator message. Still open: a display of the map itself, and neutrality tests for it.
- **Different AI providers or models.** `LLMCall` records `provider`, `model`, `prompt_version` and a hash of the prompt,
  so another provider or a changed prompt can be compared run by run. `ModerationRun.config_snapshot` keeps the settings
  each run used.
- **Studies of any shape.** `Conversation.source` (human or synthetic), `experiment`, `pair_id`, `variant` and
  `transcript_id` let one table hold real debates and generated ones. `Experiment.kind` already names five uses: paired,
  series, replay, warmup and observational. `Message.planted` stores the problems deliberately placed in a synthetic
  message.
- **Rating anything.** The evaluation tables point at their target by a type plus an id, so they can score messages,
  moderator notes or something new.
- **Preview settings per conversation.** `PreviewMode` is an on/off switch per conversation, so the preview feature can
  be tried on some conversations and not others.

## How the moderator is tested for bias

The `seeding/` and `evaluation/` code builds a controlled experiment (more detail in `docs/eval_pipeline_summary.md`):

1. A **fact bank** of 12 owner-verified facts, such as "12 states have passed statewide sanctuary laws".
2. **Seeded errors**: each fact gets a left-favoring and a right-favoring false version. Numbers are multiplied or divided
   by 1.1, 1.5 or 3, so the two sides are exact mirrors (12 becomes 13 or 11, then 18 or 8, then 36 or 4).
3. **Generated debates**: matched four-message debates where one speaker states the claim, either true or false.
4. **Replay**: the real moderator runs on those debates without posting anything (`replay` command).
5. **A judge**: one AI model tags each reply 0 to 3 (missed, spotted, wrong fix, right fix), never told the side.
6. **Summary tables**: rates by side and severity, plus the left-minus-right gap.

So far the moderator mostly asks for sources rather than correcting, which treats both sides equally but does little to
inform; that finding is in `docs/evaluation_pipeline_todo.md`.

## Running and hosting

- **Local:** `.venv/bin/python manage.py runserver` for the site and `manage.py run_moderator` for the worker. Other
  commands handle exports (`export_conversation`, `export_all`), budget checks (`budget`), the breaker (`reset_breaker`)
  and the evaluation (`replay`, `generate_conversations`, `judge_responses`, `run_research_eval`, and so on). The README
  lists them all.
- **Hosting:** the plan is one small Fly.io machine running the web server and the worker together, with the SQLite
  database backed up continuously to Backblaze by Litestream (`fly.toml`, `litestream.yml`, `docs/deployment_plan.md`).
  It must stay a single machine because SQLite allows only one writer at a time.
- **Safety rules:** no secret or API key is ever stored in the repo, and git hooks in `.githooks/` enforce that.
