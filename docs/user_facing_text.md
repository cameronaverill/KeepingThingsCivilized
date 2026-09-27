# User-facing text inventory

Generated 2026-09-26 by `scripts/make_text_inventory.py` (run `.venv/bin/python scripts/make_text_inventory.py` to regenerate). Read-only analysis: no code, test or database was changed.

**How to read this.** Every table row is one thing a participant can see, shown exactly as it appears (in a code span), with the place it appears in and the source location (`file:line`) to edit. Pages were rendered for real with the Django test client in a throwaway in-memory database, then reduced to visible text: one line per block element; a link shows as `[link: text]`, a button as `[button: text]`, and `(aria-label) ...`, `(placeholder) ...`, `(title) ...`, `(alt) ...` are attributes. A line starting `(hidden until the page needs it)` is in the page but only shown by the page's JavaScript. The site header (brand, navigation) is left out of page tables and listed once in section 8. Source pointers are found by matching text against templates, Python string literals and the seeded topics file: treat them as hints; a `- (dynamic text)` pointer means the line is not literal source text (a proposition, a message, a username, a number). Numbers such as 3,000 or 30 seconds come from `config/tunables.py`. The demonstration usernames (`zelda_mox`, `quincy_ray`, `maple_fern` and so on) exist only in the throwaway database. The moderator's own message text is written by the AI model at run time and cannot be listed; only its frame (badge, heading, link) is fixed. The Django admin, exports and command-line tools are not participant-facing and are not covered. Not yet covered: the step 19 preview panel.

Strings captured: 837 rows (356 distinct).

## Table of contents

- [1. Pages, rendered for real](#1-pages-rendered-for-real)
  - [1.1 Home (waiting list): empty state](#11-home-waiting-list-empty-state)
  - [1.2 Home (waiting list): with cards](#12-home-waiting-list-with-cards)
  - [1.3 Home: a card without a username (template branch, not reachable today)](#13-home-a-card-without-a-username-template-branch-not-reachable-today)
  - [1.4 Your discussions: with rows of every kind](#14-your-discussions-with-rows-of-every-kind)
  - [1.5 Your discussions: search with no match](#15-your-discussions-search-with-no-match)
  - [1.5b Your discussions: search with a match](#15b-your-discussions-search-with-a-match)
  - [1.5c Your discussions: no discussions yet](#15c-your-discussions-no-discussions-yet)
  - [1.6 Blocked people: nobody blocked](#16-blocked-people-nobody-blocked)
  - [1.7 Start a new discussion (propose page), with the seeded topics](#17-start-a-new-discussion-propose-page-with-the-seeded-topics)
  - [1.8 Propose page: the section for a viewer who already has a conversation on a seeded topic](#18-propose-page-the-section-for-a-viewer-who-already-has-a-conversation-on-a-seeded-topic)
  - [1.9 position_phrase: how the start of a position is worded after `My position is that `](#19-position_phrase-how-the-start-of-a-position-is-worded-after-my-position-is-that)
  - [1.10 How this works (anonymous and logged in), in full](#110-how-this-works-anonymous-and-logged-in-in-full)
  - [1.11 Conversation: waiting for someone to take the other position (creator alone)](#111-conversation-waiting-for-someone-to-take-the-other-position-creator-alone)
  - [1.12 The position line under the title (the viewer's own position only)](#112-the-position-line-under-the-title-the-viewers-own-position-only)
  - [1.13 Conversation: active with a few messages](#113-conversation-active-with-a-few-messages)
  - [1.14 Conversation: active with moderator messages](#114-conversation-active-with-moderator-messages)
  - [1.15 Conversation: closed by the message limit](#115-conversation-closed-by-the-message-limit)
  - [1.16 Conversation: ended by you](#116-conversation-ended-by-you)
  - [1.17 Conversation: ended by the other participant](#117-conversation-ended-by-the-other-participant)
  - [1.18 Conversation: the side column with the block card](#118-conversation-the-side-column-with-the-block-card)
  - [1.19 After blocking someone from a waiting card](#119-after-blocking-someone-from-a-waiting-card)
  - [1.20 After blocking the person you are talking to](#120-after-blocking-the-person-you-are-talking-to)
  - [1.21 Blocked people: with people](#121-blocked-people-with-people)
  - [1.22 Conversation ended because the other person blocked you](#122-conversation-ended-because-the-other-person-blocked-you)
  - [1.23 After unblocking someone (one person still blocked)](#123-after-unblocking-someone-one-person-still-blocked)
  - [1.24 After unblocking the last person](#124-after-unblocking-the-last-person)
- [2. Every refusal a participant can get](#2-every-refusal-a-participant-can-get)
  - [2.1 Posting a message](#21-posting-a-message)
  - [2.2 Proposing, choosing a side and joining](#22-proposing-choosing-a-side-and-joining)
  - [2.3 Blocking](#23-blocking)
  - [2.4 Other refusals at page level](#24-other-refusals-at-page-level)
  - [2.5 Complete list of PostRejected codes (from the source) and whether the rows above cover it](#25-complete-list-of-postrejected-codes-from-the-source-and-whether-the-rows-above-cover-it)
- [3. Moderation notices and headings (forum/viewmodels.py)](#3-moderation-notices-and-headings-forumviewmodelspy)
  - [3.1 The notice banner ("About the AI moderator")](#31-the-notice-banner-about-the-ai-moderator)
  - [3.2 The banner as it appears on the page (circuit breaker example)](#32-the-banner-as-it-appears-on-the-page-circuit-breaker-example)
  - [3.3 Headings above a moderator message](#33-headings-above-a-moderator-message)
- [4. The moderator card and the message frame](#4-the-moderator-card-and-the-message-frame)
  - [4.1 Moderator card](#41-moderator-card)
  - [4.2 Moderator card without a heading (a message the server could not attribute)](#42-moderator-card-without-a-heading-a-message-the-server-could-not-attribute)
  - [4.3 Your message](#43-your-message)
  - [4.4 The other person's message (with their username)](#44-the-other-persons-message-with-their-username)
  - [4.5 The other person's message (view gives no username; fallback text)](#45-the-other-persons-message-view-gives-no-username-fallback-text)
- [5. Error pages and redirects](#5-error-pages-and-redirects)
  - [5.1 400.html](#51-400html)
  - [5.2 403.html](#52-403html)
  - [5.3 403_csrf.html](#53-403_csrfhtml)
  - [5.4 404.html](#54-404html)
  - [5.5 404.html (with the conversation text)](#55-404html-with-the-conversation-text)
  - [5.6 500.html](#56-500html)
  - [5.7 Login redirect](#57-login-redirect)
- [6. Client-side strings (forum/static/forum/compose.js, poll.js)](#6-client-side-strings-forumstaticforumcomposejs-polljs)
- [7. Accounts pages (username and password only; no email)](#7-accounts-pages-username-and-password-only-no-email)
  - [7.1 Register page](#71-register-page)
  - [7.2 Register: empty submit (validation messages)](#72-register-empty-submit-validation-messages)
  - [7.3 Register: bad username, weak password, mismatch (validation messages)](#73-register-bad-username-weak-password-mismatch-validation-messages)
  - [7.4 Register: username already taken (any case)](#74-register-username-already-taken-any-case)
  - [7.5 Login page](#75-login-page)
  - [7.6 Login: wrong username or password](#76-login-wrong-username-or-password)
  - [7.7 Login: lockout page (HTTP 429)](#77-login-lockout-page-http-429)
  - [7.8 Logout confirmation page (opening the logout address with a link)](#78-logout-confirmation-page-opening-the-logout-address-with-a-link)
  - [7.9 After logging out (flash message on the login page)](#79-after-logging-out-flash-message-on-the-login-page)
  - [7.10 Change password page](#710-change-password-page)
  - [7.11 Change password: wrong old password, weak new password](#711-change-password-wrong-old-password-weak-new-password)
  - [7.12 Change password: done page](#712-change-password-done-page)
- [8. Everything else a participant can see](#8-everything-else-a-participant-can-see)
  - [8.1 Site header, logged out (shown on every non-conversation page)](#81-site-header-logged-out-shown-on-every-non-conversation-page)
  - [8.2 Site header, logged in (shown on home, discussions, blocked, propose, how it works, accounts pages)](#82-site-header-logged-in-shown-on-home-discussions-blocked-propose-how-it-works-accounts-pages)
  - [8.3 Site header on a conversation page (no username of the viewer, deliberately)](#83-site-header-on-a-conversation-page-no-username-of-the-viewer-deliberately)
  - [8.4 Skip link (only visible to keyboard and screen-reader users)](#84-skip-link-only-visible-to-keyboard-and-screen-reader-users)
  - [8.5 Footer](#85-footer)
  - [8.6 Flash messages](#86-flash-messages)
  - [8.7 Page titles (<title>) of every page rendered above](#87-page-titles-title-of-every-page-rendered-above)
  - [8.8 Seeded topics (the suggested topics on the propose page and in the waiting list)](#88-seeded-topics-the-suggested-topics-on-the-propose-page-and-in-the-waiting-list)
  - [8.9 Other visible elements](#89-other-visible-elements)
- [9. Observations for the owner](#9-observations-for-the-owner)
  - [(a) Costs, spending, budgets, tokens, the API](#a-costs-spending-budgets-tokens-the-api)
  - [(b) Places where a person is refused but the text does not say why or what to do next](#b-places-where-a-person-is-refused-but-the-text-does-not-say-why-or-what-to-do-next)
  - [(c) Inconsistent wording for the same thing](#c-inconsistent-wording-for-the-same-thing)
  - [(d) Text that could reveal a participant label, a username or the other person's identity](#d-text-that-could-reveal-a-participant-label-a-username-or-the-other-persons-identity)
  - [(e) Leftover mentions of email confirmation or password reset by email](#e-leftover-mentions-of-email-confirmation-or-password-reset-by-email)
  - [Counts](#counts)

## 1. Pages, rendered for real


### 1.1 Home (waiting list): empty state

Route `/` (`forum:home`), logged in, nobody is waiting. The home page no longer has a search box.

| Visible text | Source (file:line) |
|---|---|
| `<title> Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `These are positions that someone holds and is waiting for someone to disagree with. Pick one to take the other side, or start a discussion of your own. An AI moderator reads along and may step in. [link: How this works]` | forum/templates/forum/home.html:7; forum/templates/forum/base.html:16 |
| `[button: Start a new discussion]` | forum/templates/forum/home.html:10 |
| `Nobody is waiting right now. You can [link: start a discussion of your own].` | forum/templates/forum/home.html:39 |


### 1.2 Home (waiting list): with cards

Each card says who is waiting, quotes the position they hold, and has ONE join button (it takes the opposite side) and a quiet `Block <username>` button. The cards here cover: waiting on a seeded topic holding the stated position (`maple_fern`) or the opposing one (`river_stone`); waiting on a user-written topic holding the position (`oak_lane`, whose join button has no opposing wording to quote so it says `I disagree with this position`) or the opposite of it (`sable_wren`, where the quote is `They disagree with: ...`); and two join labels showing how the start of a position is worded (section 1.9): an acronym and `I` keep their capital. Cards are ordered newest first. The viewer here is a person with no conversations.

| Visible text | Source (file:line) |
|---|---|
| `<title> Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `These are positions that someone holds and is waiting for someone to disagree with. Pick one to take the other side, or start a discussion of your own. An AI moderator reads along and may step in. [link: How this works]` | forum/templates/forum/home.html:7; forum/templates/forum/base.html:16 |
| `[button: Start a new discussion]` | forum/templates/forum/home.html:10 |
| `zelda_mox is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: Working from home should be the default for office jobs.` | forum/views.py:117 |
| `[button: My position is that working from home should be the default for office jobs.]` | forum/views.py:101, 106 |
| `[button: Block zelda_mox]` | forum/templates/forum/home.html:32 |
| `zelda_mox is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Cats make better pets than dogs.` | - (dynamic text) |
| `[button: I disagree with this position]` | forum/views.py:86 |
| `[button: Block zelda_mox]` | forum/templates/forum/home.html:32 |
| `cedar_finch is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: I think tipping should be banned.` | forum/views.py:117 |
| `[button: My position is that I think tipping should be banned.]` | forum/views.py:101, 106 |
| `[button: Block cedar_finch]` | forum/templates/forum/home.html:32 |
| `birch_hollow is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: NASA should get more funding than it does today.` | forum/views.py:117 |
| `[button: My position is that NASA should get more funding than it does today.]` | forum/views.py:101, 106 |
| `[button: Block birch_hollow]` | forum/templates/forum/home.html:32 |
| `sable_wren is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: Nuclear power is essential for a clean future.` | forum/views.py:117 |
| `[button: My position is that nuclear power is essential for a clean future.]` | forum/views.py:101, 106 |
| `[button: Block sable_wren]` | forum/templates/forum/home.html:32 |
| `oak_lane is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Homework should be abolished in primary schools.` | - (dynamic text) |
| `[button: I disagree with this position]` | forum/views.py:86 |
| `[button: Block oak_lane]` | forum/templates/forum/home.html:32 |
| `river_stone is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Possession of small amounts of illegal drugs for personal use should not be decriminalized.` | forum/seed_topics.json:50 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should be decriminalized.]` | forum/views.py:101, 106 |
| `[button: Block river_stone]` | forum/templates/forum/home.html:32 |
| `maple_fern is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Cities should cap how much landlords can raise rents each year.` | forum/seed_topics.json:5 |
| `[button: My position is that cities should not cap how much landlords can raise rents each year.]` | forum/views.py:101, 106 |
| `[button: Block maple_fern]` | forum/templates/forum/home.html:32 |


### 1.3 Home: a card without a username (template branch, not reachable today)

The template shows `Someone` when a card has no username and hides the Block button. The waiting-list query always supplies the username of the waiting person, so this branch is not reachable with real data.

| Visible text | Source |
|---|---|
| `Someone is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `A sample position.` | - (dynamic text) |
| `[button: I disagree with this position]` | forum/views.py:86 |


### 1.4 Your discussions: with rows of every kind

Route `/discussions/` (`forum:mine`), viewer `zelda_mox`. Each row: the viewer's own position line, `with <username>` once someone has joined, a status word, and an `Open` button. Rows: waiting holding the stated position (`Your position: ...`), waiting holding the opposing position of a user-written topic (`You disagree with this position: ...`), active on a seeded topic holding either wording, active holding the opposite of a user-written topic, and ended (listed last, in a quieter style). Without a search only the newest 10 ended conversations are listed; a search lists all matches. A row for an old conversation whose side was never recorded shows `Your position: ...` like the stated position.

| Visible text | Source (file:line) |
|---|---|
| `<title> Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `[button: Blocked people]` | forum/templates/forum/mine.html:9 |
| `Search your discussions` | forum/templates/forum/mine.html:14 |
| `[button: Search]` | forum/templates/forum/mine.html:17 |
| `Newest first.` | forum/templates/forum/mine.html:19 |
| `You disagree with this position: Public transport should be free.` | forum/views.py:94 |
| `with quincy_ray` | - (dynamic text) |
| `In discussion` | forum/views.py:83 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |
| `Your position: High schools should be free to start earlier than 8:30 a.m.` | forum/seed_topics.json:138; forum/views.py:93, 95 |
| `with quincy_ray` | - (dynamic text) |
| `In discussion` | forum/views.py:83 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |
| `Your position: Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181; forum/views.py:93, 95 |
| `with quincy_ray` | - (dynamic text) |
| `In discussion` | forum/views.py:83 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |
| `You disagree with this position: Working from home should be the default for office jobs.` | forum/views.py:94 |
| `Waiting for someone to take the other position` | forum/views.py:82 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |
| `Your position: Cats make better pets than dogs.` | forum/views.py:93, 95 |
| `Waiting for someone to take the other position` | forum/views.py:82 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |
| `Your position: Zoos do more good than harm.` | forum/views.py:93, 95 |
| `with quincy_ray` | - (dynamic text) |
| `Ended` | forum/views.py:84 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |


### 1.5 Your discussions: search with no match

`/discussions/?q=zzzz nothing matches`.

| Visible text | Source (file:line) |
|---|---|
| `<title> Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `[button: Blocked people]` | forum/templates/forum/mine.html:9 |
| `Search your discussions` | forum/templates/forum/mine.html:14 |
| `[button: Search]` | forum/templates/forum/mine.html:17 |
| `Newest first.` | forum/templates/forum/mine.html:19 |
| `No discussion matches your search.` | forum/templates/forum/mine.html:33 |


### 1.5b Your discussions: search with a match

`/discussions/?q=zoos` (an ended conversation).

| Visible text | Source (file:line) |
|---|---|
| `<title> Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `[button: Blocked people]` | forum/templates/forum/mine.html:9 |
| `Search your discussions` | forum/templates/forum/mine.html:14 |
| `[button: Search]` | forum/templates/forum/mine.html:17 |
| `Newest first.` | forum/templates/forum/mine.html:19 |
| `Your position: Zoos do more good than harm.` | forum/views.py:93, 95 |
| `with quincy_ray` | - (dynamic text) |
| `Ended` | forum/views.py:84 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |


### 1.5c Your discussions: no discussions yet

A person with no conversations.

| Visible text | Source (file:line) |
|---|---|
| `<title> Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `[button: Blocked people]` | forum/templates/forum/mine.html:9 |
| `Search your discussions` | forum/templates/forum/mine.html:14 |
| `[button: Search]` | forum/templates/forum/mine.html:17 |
| `Newest first.` | forum/templates/forum/mine.html:19 |
| `You have no discussions yet. Pick a waiting position on [link: the home page], or start [link: one of your own].` | forum/templates/forum/mine.html:35 |


### 1.6 Blocked people: nobody blocked

Route `/blocked/` (`forum:blocked`).

| Visible text | Source (file:line) |
|---|---|
| `<title> Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `People you have blocked cannot see your waiting positions, and you cannot see theirs.` | forum/templates/forum/blocked.html:5 |
| `You have not blocked anyone.` | forum/templates/forum/blocked.html:20 |
| `[link: ← Your discussions]` | forum/templates/forum/blocked.html:22 |


### 1.7 Start a new discussion (propose page), with the seeded topics

Route `/propose/` (`forum:propose`), viewer with no conversations. The label `My position is that` is fixed text in front of the box (a person who types it again does not store it twice). Below the form, each seeded topic has two buttons, one per side, each worded `My position is that <that side, first letter lower-cased>`.

| Visible text | Source (file:line) |
|---|---|
| `<title> Start a new discussion` | forum/templates/forum/propose.html:3, 5 |
| `Start a new discussion` | forum/templates/forum/propose.html:3, 5 |
| `State a position you hold and want to talk through. Whoever joins will take the opposing view, so you will not need to argue both sides.` | forum/templates/forum/propose.html:6 |
| `My position is that` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `0 / 200 characters` | forum/templates/forum/propose.html:25 |
| `[button: Publish and start discussing]` | forum/templates/forum/propose.html:26 |
| `It appears on the home page immediately. You can create up to 20 propositions per day.` | forum/templates/forum/propose.html:28 |
| `Or start from one of these topics` | forum/templates/forum/propose.html:33 |
| `Cities should cap how much landlords can raise rents each year.` | forum/seed_topics.json:5 |
| `[button: My position is that cities should cap how much landlords can raise rents each year.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that cities should not cap how much landlords can raise rents each year.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Possession of small amounts of illegal drugs for personal use should be decriminalized.` | forum/seed_topics.json:49 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should be decriminalized.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should not be decriminalized.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Cities should limit their local police's cooperation with federal immigration enforcement.` | forum/seed_topics.json:93 |
| `[button: My position is that cities should limit their local police's cooperation with federal immigration enforcement.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that cities should not limit their local police's cooperation with federal immigration enforcement.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `High schools should start no earlier than 8:30 a.m.` | forum/seed_topics.json:137 |
| `[button: My position is that high schools should start no earlier than 8:30 a.m.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that high schools should be free to start earlier than 8:30 a.m.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `[button: My position is that cities should build protected bike lanes even when it means removing some street parking.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that cities should not remove street parking in order to build protected bike lanes.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Employers should allow office workers to work from home most of the week.` | forum/seed_topics.json:225 |
| `[button: My position is that employers should allow office workers to work from home most of the week.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that employers should not have to allow office workers to work from home most of the week.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[link: ← Back to home]` | forum/templates/forum/propose.html:61 |


### 1.8 Propose page: the section for a viewer who already has a conversation on a seeded topic

Where the viewer already has an open or active conversation on a seeded topic, the two buttons are replaced by `You already have a conversation here.` and `Open your conversation` (viewer `zelda_mox`, who has conversations on two of the seeded topics). The other seeded topics keep their two buttons; only the section is shown.

| Visible text | Source |
|---|---|
| `Or start from one of these topics` | forum/templates/forum/propose.html:33 |
| `Cities should cap how much landlords can raise rents each year.` | forum/seed_topics.json:5 |
| `[button: My position is that cities should cap how much landlords can raise rents each year.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that cities should not cap how much landlords can raise rents each year.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Possession of small amounts of illegal drugs for personal use should be decriminalized.` | forum/seed_topics.json:49 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should be decriminalized.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should not be decriminalized.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `Cities should limit their local police's cooperation with federal immigration enforcement.` | forum/seed_topics.json:93 |
| `[button: My position is that cities should limit their local police's cooperation with federal immigration enforcement.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that cities should not limit their local police's cooperation with federal immigration enforcement.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `High schools should start no earlier than 8:30 a.m.` | forum/seed_topics.json:137 |
| `You already have a conversation here.` | forum/templates/forum/propose.html:39 |
| `[button: Open your conversation]` | forum/templates/forum/propose.html:43 |
| `Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `You already have a conversation here.` | forum/templates/forum/propose.html:39 |
| `[button: Open your conversation]` | forum/templates/forum/propose.html:43 |
| `Employers should allow office workers to work from home most of the week.` | forum/seed_topics.json:225 |
| `[button: My position is that employers should allow office workers to work from home most of the week.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |
| `[button: My position is that employers should not have to allow office workers to work from home most of the week.]` | forum/templates/forum/propose.html:21; forum/views.py:101, 106 |


### 1.9 position_phrase: how the start of a position is worded after `My position is that `

Defined in `forum/templatetags/forum_text.py:20`. Only the first letter is lower-cased and nothing else changes. The first letter is kept when the first word is an acronym or number-like (two or more capitals, or a digit), is `I` or starts `I'`, or is one of a fixed list of proper nouns (`forum/templatetags/forum_text.py:10`). Proper nouns outside the list are lower-cased (accepted by the owner for the MVP). The stored proposition is never changed; the phrase is used in the join buttons on the home page and in the propose page's seeded-topic buttons, always as `My position is that <phrase>`.

| Stored position | After `My position is that ` |
|---|---|
| Cities should cap how much landlords can raise rents each year. | `My position is that cities should cap how much landlords can raise rents each year.` |
| NASA should get more funding. | `My position is that NASA should get more funding.` |
| I think tipping should be banned. | `My position is that I think tipping should be banned.` |
| I'm sure that cats are better. | `My position is that I'm sure that cats are better.` |
| I’m sure that cats are better. | `My position is that I’m sure that cats are better.` |
| Trump should not be on the ballot. | `My position is that Trump should not be on the ballot.` |
| Germany should leave the EU. | `My position is that Germany should leave the EU.` |
| Bob's Burgers should be free. | `My position is that bob's Burgers should be free.` |
| 5G networks should be public. | `My position is that 5G networks should be public.` |
| The US should ban fireworks. | `My position is that the US should ban fireworks.` |
|    High schools should start later. | `My position is that    high schools should start later.` |
| already lower case | `My position is that already lower case` |
| élan should be valued. | `My position is that élan should be valued.` |
| (empty) | `(empty stays empty)` |
| 42 is the answer. | `My position is that 42 is the answer.` |


### 1.10 How this works (anonymous and logged in), in full

Route `/how-it-works/` (`forum:how_it_works`); it needs no login. Body text anonymous versus logged in: identical (only the site header differs, see section 8). The numbers (per-day proposition count, characters, seconds, messages) come from settings. This page is also the target of the moderator card's link.

| Visible text | Source (file:line) |
|---|---|
| `<title> How this works` | forum/templates/forum/how_it_works.html:2, 4; forum/templates/forum/base.html:16 |
| `How this works` | forum/templates/forum/how_it_works.html:2, 4; forum/templates/forum/base.html:16 |
| `What this site is` | forum/templates/forum/how_it_works.html:7 |
| `Two people discuss a proposition in writing. An AI moderator reads each message and may add notes of its own. The site is part of a research project on AI-facilitated discussion.` | forum/templates/forum/how_it_works.html:8 |
| `Propositions` | forum/templates/forum/how_it_works.html:12 |
| `Anyone with an account can propose a statement to discuss. It appears on the home page straight away; nobody at the site reviews it first, and listing one is not an endorsement. You can create up to 20 a day, and each can be up to 200 characters. If a proposition is abusive, tell the site admin [contact to be added].` | forum/templates/forum/how_it_works.html:13 |
| `Choosing a proposition puts you in a conversation on it, holding the position you chose. If you already have one open on that proposition, you return to it. If someone who holds the other position is already waiting, you are paired with them and can read everything they posted while they waited. Otherwise you wait, and the conversation starts when someone takes the other position. You can keep posting while you wait. A waiting conversation does not expire.` | forum/templates/forum/how_it_works.html:14 |
| `Every conversation is between two opposing positions. The home page lists positions that someone holds and is waiting for someone to disagree with. Join one to take the other side, or start a discussion of your own with your position or one of the suggested topics. The AI moderator sees only the topic as a neutral statement and does not know who holds which side. Conversations are private to their two participants. You can see the username of the person you are discussing with, and people waiting to discuss are shown by username on the home page. You can block anyone: blocking ends any conversation you share, and neither of you will see the other’s waiting positions.` | forum/templates/forum/how_it_works.html:15 |
| `Either of you can end a conversation at any time. That closes it for both of you: it stays readable, but nobody can post in it, and it is kept for the research record.` | forum/templates/forum/how_it_works.html:16 |
| `What the AI moderator does` | forum/templates/forum/how_it_works.html:20 |
| `After each message it looks for problems such as a factual error, abusive language, flooding the conversation, or an unanswered question, and decides whether to say anything. It can only reply to a person’s message, never to its own, and it posts at most once per message.` | forum/templates/forum/how_it_works.html:21 |
| `It is not a judge and it can be wrong. It is instructed to treat both sides of an argument the same way and never to take a side. If you think a note is mistaken, say so in the conversation.` | forum/templates/forum/how_it_works.html:22 |
| `Limits` | forum/templates/forum/how_it_works.html:26 |
| `Each message can be up to 3,000 characters (about 500 words). Longer messages are not sent; the page tells you by how much to shorten yours, and keeps your text.` | forum/templates/forum/how_it_works.html:28 |
| `You can post one message every 30 seconds, counted from your own previous message.` | forum/templates/forum/how_it_works.html:29 |
| `A conversation closes after 30 messages and has two participants.` | forum/templates/forum/how_it_works.html:30 |
| `Nothing stops you posting several messages in a row.` | forum/templates/forum/how_it_works.html:31 |
| `These limits keep the discussion readable. Whenever you cannot post, the page tells you why and what to do next.` | forum/templates/forum/how_it_works.html:33 |
| `Pauses` | forum/templates/forum/how_it_works.html:37 |
| `Sometimes the moderator is paused. When it is, the page says so and your messages are still posted.` | forum/templates/forum/how_it_works.html:38 |
| `What is recorded` | forum/templates/forum/how_it_works.html:42 |
| `Your messages, the moderator’s analysis and its replies are stored so the research team can study how moderation treats different viewpoints. [Retention period and who can see the data: to be written before launch.]` | forum/templates/forum/how_it_works.html:43 |
| `[button: Back to home]` | forum/templates/forum/how_it_works.html:46 |


### 1.11 Conversation: waiting for someone to take the other position (creator alone)

State `open`: one participant. The creator can keep posting while waiting (the message box is there). Route `/c/<id>/`. The page polls every 3 seconds and reloads itself when someone joins.

| Visible text | Source (file:line) |
|---|---|
| `<title> Cats make better pets than dogs.` | - (dynamic text) |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Cats make better pets than dogs.` | - (dynamic text) |
| `Waiting for someone to take the other position` | forum/templates/forum/conversation.html:13, 38; forum/views.py:82 |
| `Your position: Cats make better pets than dogs.` | forum/views.py:93, 95 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:10 UTC` | forum/templates/forum/_message.html:2 |
| `I will be here for a while; I think cats are calmer.` | - (dynamic text) |
| `Updates appear automatically. Nothing you type is sent until you press Post.` | forum/templates/forum/conversation.html:34 |
| `Waiting for someone to take the other position` | forum/templates/forum/conversation.html:13, 38; forum/views.py:82 |
| `You are the first one here. Whoever joins will take the opposing position and can read everything you have posted so far. You can keep posting while you wait.` | forum/templates/forum/conversation.html:39 |
| `Your message` | forum/templates/forum/conversation.html:56 |
| `0 / 3,000 characters` | forum/templates/forum/conversation.html:67 |
| `[button: Post message]` | forum/templates/forum/conversation.html:68 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `Only you so far. When someone joins, their username is shown here.` | forum/templates/forum/conversation.html:97 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Finished?` | forum/templates/forum/conversation.html:126 |
| `End conversation` | forum/templates/forum/conversation.html:128, 132 |
| `Ending closes this conversation before anyone joins it. It becomes read-only, is kept for the research record, and cannot be reopened. You can start a new one afterwards.` | forum/templates/forum/conversation.html:129 |
| `[button: End conversation]` | forum/templates/forum/conversation.html:128, 132 |


### 1.12 The position line under the title (the viewer's own position only)

Built by `forum/views.py:89`; the same rule gives the lines in Your discussions. Nothing is ever shown about the other person's position. Each row was read from a real conversation page.

| Situation | Line shown | Source |
|---|---|---|
| Holding the stated position of a user-written topic (waiting) | `Your position: Cats make better pets than dogs.` | forum/views.py:93, 95 |
| Holding the opposing position of a user-written topic (waiting) | `You disagree with this position: Working from home should be the default for office jobs.` | forum/views.py:94 |
| Holding the stated position of a seeded topic (active) | `Your position: Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181; forum/views.py:93, 95 |
| Holding the opposing position of a seeded topic (active) | `Your position: High schools should be free to start earlier than 8:30 a.m.` | forum/seed_topics.json:138; forum/views.py:93, 95 |
| Holding the opposing position of a user-written topic (active) | `You disagree with this position: Public transport should be free.` | forum/views.py:94 |
| The same seeded conversation as the other person sees it | `Your position: Cities should not remove street parking in order to build protected bike lanes.` | forum/seed_topics.json:182; forum/views.py:93, 95 |


### 1.13 Conversation: active with a few messages

State `active`, viewer can post. Messages 1 and 3 are the viewer's (`You`); message 2 shows the other person's username. The `Who is here` card names the other person, and the block card offers to block them (both are new; the owner decided the other person's username is shown here). The viewer's own username is not shown anywhere on this page, the header included.

| Visible text | Source (file:line) |
|---|---|
| `<title> Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `Active · 2 participants` | forum/templates/forum/conversation.html:13 |
| `Your position: Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181; forum/views.py:93, 95 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:10 UTC` | forum/templates/forum/_message.html:2 |
| `I think protected lanes make streets safer for everyone.` | - (dynamic text) |
| `quincy_ray message 2 · 09:15 UTC` | forum/templates/forum/_message.html:2 |
| `Losing parking would hurt small shops on the street.` | - (dynamic text) |
| `You message 3 · 09:20 UTC` | forum/templates/forum/_message.html:2 |
| `Shops usually see more customers on foot and by bike.` | - (dynamic text) |
| `Updates appear automatically. Nothing you type is sent until you press Post.` | forum/templates/forum/conversation.html:34 |
| `Your message` | forum/templates/forum/conversation.html:56 |
| `0 / 3,000 characters` | forum/templates/forum/conversation.html:67 |
| `[button: Post message]` | forum/templates/forum/conversation.html:68 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with quincy_ray. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `3 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block quincy_ray` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block quincy_ray]` | forum/templates/forum/conversation.html:115, 119 |
| `Finished?` | forum/templates/forum/conversation.html:126 |
| `End conversation` | forum/templates/forum/conversation.html:128, 132 |
| `Ending closes the conversation for both of you. It becomes read-only, is kept for the research record, and cannot be reopened. You can start a new one afterwards.` | forum/templates/forum/conversation.html:129 |
| `[button: End conversation]` | forum/templates/forum/conversation.html:128, 132 |


### 1.14 Conversation: active with moderator messages

Same conversation after the AI moderator posted twice. The moderator's own text is a placeholder (`SAMPLE MODERATOR TEXT`); the frame around it (badge, heading with the other person's username, `automated`, link) is fixed text. How the headings are chosen: section 3.3.

| Visible text | Source (file:line) |
|---|---|
| `<title> Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181 |
| `Active · 2 participants` | forum/templates/forum/conversation.html:13 |
| `Your position: Cities should build protected bike lanes even when it means removing some street parking.` | forum/seed_topics.json:181; forum/views.py:93, 95 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:10 UTC` | forum/templates/forum/_message.html:2 |
| `I think protected lanes make streets safer for everyone.` | - (dynamic text) |
| `quincy_ray message 2 · 09:15 UTC` | forum/templates/forum/_message.html:2 |
| `Losing parking would hurt small shops on the street.` | - (dynamic text) |
| `You message 3 · 09:20 UTC` | forum/templates/forum/_message.html:2 |
| `Shops usually see more customers on foot and by bike.` | - (dynamic text) |
| `AI MODERATOR About quincy_ray's message 2 · automated · 09:16 UTC` | forum/templates/forum/_message.html:2; forum/viewmodels.py:72, 73 |
| `SAMPLE MODERATOR TEXT: could you say what you mean by small shops?` | - (dynamic text) |
| `[link: Why is there an AI moderator, and can it be wrong?]` | forum/templates/forum/_message.html:2 |
| `AI MODERATOR For both of you · automated · 09:21 UTC` | forum/viewmodels.py:17; forum/templates/forum/_message.html:2 |
| `SAMPLE MODERATOR TEXT: a reminder for both of you to keep to the topic.` | - (dynamic text) |
| `[link: Why is there an AI moderator, and can it be wrong?]` | forum/templates/forum/_message.html:2 |
| `Updates appear automatically. Nothing you type is sent until you press Post.` | forum/templates/forum/conversation.html:34 |
| `Your message` | forum/templates/forum/conversation.html:56 |
| `0 / 3,000 characters` | forum/templates/forum/conversation.html:67 |
| `[button: Post message]` | forum/templates/forum/conversation.html:68 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with quincy_ray. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `3 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block quincy_ray` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block quincy_ray]` | forum/templates/forum/conversation.html:115, 119 |
| `Finished?` | forum/templates/forum/conversation.html:126 |
| `End conversation` | forum/templates/forum/conversation.html:128, 132 |
| `Ending closes the conversation for both of you. It becomes read-only, is kept for the research record, and cannot be reopened. You can start a new one afterwards.` | forum/templates/forum/conversation.html:129 |
| `[button: End conversation]` | forum/templates/forum/conversation.html:128, 132 |


### 1.15 Conversation: closed by the message limit

State `closed`, nobody ended it. `MAX_USER_MESSAGES_PER_CONVERSATION` overridden to 4 so the page is short; in production the number is 30.

| Visible text | Source (file:line) |
|---|---|
| `<title> Every citizen should do a year of national service.` | - (dynamic text) |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Every citizen should do a year of national service.` | - (dynamic text) |
| `Closed · 4 of 4 messages` | forum/templates/forum/conversation.html:13 |
| `Your position: Every citizen should do a year of national service.` | forum/views.py:93, 95 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `First point.` | - (dynamic text) |
| `quincy_ray message 2 · 09:05 UTC` | forum/templates/forum/_message.html:2 |
| `Second point.` | - (dynamic text) |
| `You message 3 · 09:10 UTC` | forum/templates/forum/_message.html:2 |
| `Third point.` | - (dynamic text) |
| `quincy_ray message 4 · 09:15 UTC` | forum/templates/forum/_message.html:2 |
| `Fourth point.` | - (dynamic text) |
| `Conversation closed` | forum/templates/forum/conversation.html:45 |
| `This conversation is closed. It reached its limit of 4 messages. You can start a new one by choosing a proposition.` | forum/services.py:184 |
| `The conversation stays here to read.` | forum/templates/forum/conversation.html:50 |
| `[button: Start a new conversation]` | forum/templates/forum/conversation.html:51 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with quincy_ray. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `4 of 4 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block quincy_ray` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block quincy_ray]` | forum/templates/forum/conversation.html:115, 119 |


### 1.16 Conversation: ended by you

State `closed`, the viewer pressed End conversation.

| Visible text | Source (file:line) |
|---|---|
| `<title> Zoos do more good than harm.` | - (dynamic text) |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Zoos do more good than harm.` | - (dynamic text) |
| `Closed · 1 of 30 messages` | forum/templates/forum/conversation.html:13 |
| `Your position: Zoos do more good than harm.` | forum/views.py:93, 95 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:30 UTC` | forum/templates/forum/_message.html:2 |
| `Let us stop here.` | - (dynamic text) |
| `Conversation closed` | forum/templates/forum/conversation.html:45 |
| `This conversation is closed. You ended this conversation. You can start a new conversation by choosing a proposition.` | forum/services.py:176, 179; forum/templates/forum/conversation.html:47 |
| `The conversation stays here to read.` | forum/templates/forum/conversation.html:50 |
| `[button: Start a new conversation]` | forum/templates/forum/conversation.html:51 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with quincy_ray. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block quincy_ray` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block quincy_ray]` | forum/templates/forum/conversation.html:115, 119 |


### 1.17 Conversation: ended by the other participant

The same conversation as the other person sees it. The sentence says `The other participant ended this conversation` although the page names them elsewhere.

| Visible text | Source (file:line) |
|---|---|
| `<title> Zoos do more good than harm.` | - (dynamic text) |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Zoos do more good than harm.` | - (dynamic text) |
| `Closed · 1 of 30 messages` | forum/templates/forum/conversation.html:13 |
| `You disagree with this position: Zoos do more good than harm.` | forum/views.py:94 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `zelda_mox message 1 · 09:30 UTC` | forum/templates/forum/_message.html:2 |
| `Let us stop here.` | - (dynamic text) |
| `Conversation closed` | forum/templates/forum/conversation.html:45 |
| `This conversation is closed. The other participant ended this conversation. You can start a new conversation by choosing a proposition.` | forum/services.py:178, 179; forum/templates/forum/conversation.html:48 |
| `The conversation stays here to read.` | forum/templates/forum/conversation.html:50 |
| `[button: Start a new conversation]` | forum/templates/forum/conversation.html:51 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with zelda_mox. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block zelda_mox` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block zelda_mox]` | forum/templates/forum/conversation.html:115, 119 |


### 1.18 Conversation: the side column with the block card

The right-hand column of an active conversation: `Who is here`, `Limits`, the block card (shown once the other person has joined) and the end card. Pressing the red button ends the conversation for both and blocks the other person. In a waiting conversation the block card is absent and `Who is here` says `Only you so far`.

| Visible text | Source |
|---|---|
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with loud_pigeon. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block loud_pigeon` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block loud_pigeon]` | forum/templates/forum/conversation.html:115, 119 |
| `Finished?` | forum/templates/forum/conversation.html:126 |
| `End conversation` | forum/templates/forum/conversation.html:128, 132 |
| `Ending closes the conversation for both of you. It becomes read-only, is kept for the research record, and cannot be reopened. You can start a new one afterwards.` | forum/templates/forum/conversation.html:129 |
| `[button: End conversation]` | forum/templates/forum/conversation.html:128, 132 |

| Visible text (waiting conversation) | Source |
|---|---|
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `Only you so far. When someone joins, their username is shown here.` | forum/templates/forum/conversation.html:97 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Finished?` | forum/templates/forum/conversation.html:126 |
| `End conversation` | forum/templates/forum/conversation.html:128, 132 |
| `Ending closes this conversation before anyone joins it. It becomes read-only, is kept for the research record, and cannot be reopened. You can start a new one afterwards.` | forum/templates/forum/conversation.html:129 |
| `[button: End conversation]` | forum/templates/forum/conversation.html:128, 132 |


### 1.19 After blocking someone from a waiting card

POST `/users/oak_lane/block/` from the home card: the person is added to the block list, the page returns to the home list (their card is gone) and a green message appears at the top (the flash text, section 8.6).

| Visible text | Source (file:line) |
|---|---|
| `<title> Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `You blocked oak_lane.` | forum/views.py:204, 206 |
| `Waiting to discuss` | forum/templates/forum/home.html:2, 6 |
| `These are positions that someone holds and is waiting for someone to disagree with. Pick one to take the other side, or start a discussion of your own. An AI moderator reads along and may step in. [link: How this works]` | forum/templates/forum/home.html:7; forum/templates/forum/base.html:16 |
| `[button: Start a new discussion]` | forum/templates/forum/home.html:10 |
| `zelda_mox is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: Working from home should be the default for office jobs.` | forum/views.py:117 |
| `[button: My position is that working from home should be the default for office jobs.]` | forum/views.py:101, 106 |
| `[button: Block zelda_mox]` | forum/templates/forum/home.html:32 |
| `zelda_mox is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Cats make better pets than dogs.` | - (dynamic text) |
| `[button: I disagree with this position]` | forum/views.py:86 |
| `[button: Block zelda_mox]` | forum/templates/forum/home.html:32 |
| `cedar_finch is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: I think tipping should be banned.` | forum/views.py:117 |
| `[button: My position is that I think tipping should be banned.]` | forum/views.py:101, 106 |
| `[button: Block cedar_finch]` | forum/templates/forum/home.html:32 |
| `birch_hollow is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: NASA should get more funding than it does today.` | forum/views.py:117 |
| `[button: My position is that NASA should get more funding than it does today.]` | forum/views.py:101, 106 |
| `[button: Block birch_hollow]` | forum/templates/forum/home.html:32 |
| `sable_wren is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `They disagree with: Nuclear power is essential for a clean future.` | forum/views.py:117 |
| `[button: My position is that nuclear power is essential for a clean future.]` | forum/views.py:101, 106 |
| `[button: Block sable_wren]` | forum/templates/forum/home.html:32 |
| `river_stone is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Possession of small amounts of illegal drugs for personal use should not be decriminalized.` | forum/seed_topics.json:50 |
| `[button: My position is that possession of small amounts of illegal drugs for personal use should be decriminalized.]` | forum/views.py:101, 106 |
| `[button: Block river_stone]` | forum/templates/forum/home.html:32 |
| `maple_fern is waiting to discuss:` | forum/templates/forum/home.html:22 |
| `Cities should cap how much landlords can raise rents each year.` | forum/seed_topics.json:5 |
| `[button: My position is that cities should not cap how much landlords can raise rents each year.]` | forum/views.py:101, 106 |
| `[button: Block maple_fern]` | forum/templates/forum/home.html:32 |


### 1.20 After blocking the person you are talking to

POST `/users/loud_pigeon/block/` from the block card of an active conversation: the conversation ends for both, the page goes to Your discussions with a message that says the conversation has ended.

| Visible text | Source (file:line) |
|---|---|
| `<title> Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `You blocked loud_pigeon. The conversation has ended.` | forum/views.py:204, 206 |
| `Your discussions` | forum/templates/forum/mine.html:2, 6; forum/templates/forum/base.html:18 |
| `[button: Blocked people]` | forum/templates/forum/mine.html:9 |
| `Search your discussions` | forum/templates/forum/mine.html:14 |
| `[button: Search]` | forum/templates/forum/mine.html:17 |
| `Newest first.` | forum/templates/forum/mine.html:19 |
| `Your position: Fireworks should be banned.` | forum/views.py:93, 95 |
| `with loud_pigeon` | - (dynamic text) |
| `Ended` | forum/views.py:84 |
| `[button: Open]` | forum/templates/forum/mine.html:28 |


### 1.21 Blocked people: with people

One row per person, newest first, with the date the block was made (format `j F Y`) and an `Unblock` button.

| Visible text | Source (file:line) |
|---|---|
| `<title> Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `People you have blocked cannot see your waiting positions, and you cannot see theirs.` | forum/templates/forum/blocked.html:5 |
| `loud_pigeon` | - (dynamic text) |
| `since 15 January 2026` | forum/templates/forum/blocked.html:11 |
| `[button: Unblock]` | forum/templates/forum/blocked.html:14 |
| `oak_lane` | - (dynamic text) |
| `since 15 January 2026` | forum/templates/forum/blocked.html:11 |
| `[button: Unblock]` | forum/templates/forum/blocked.html:14 |
| `[link: ← Your discussions]` | forum/templates/forum/blocked.html:22 |


### 1.22 Conversation ended because the other person blocked you

What the blocked person sees: the conversation is closed and says `The other participant ended this conversation.` They are not told they were blocked.

| Visible text | Source (file:line) |
|---|---|
| `<title> Fireworks should be banned.` | - (dynamic text) |
| `[link: ← Home]` | forum/templates/forum/conversation.html:10 |
| `Fireworks should be banned.` | - (dynamic text) |
| `Closed · 1 of 30 messages` | forum/templates/forum/conversation.html:13 |
| `You disagree with this position: Fireworks should be banned.` | forum/views.py:94 |
| `(hidden until the page needs it) About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `(hidden until the page needs it) This conversation has changed. [link: Reload to see the latest.]` | forum/templates/forum/conversation.html:26 |
| `(aria-label) Messages in this conversation` | forum/templates/forum/conversation.html:32 |
| `You message 1 · 09:40 UTC` | forum/templates/forum/_message.html:2 |
| `You are all wrong about fireworks.` | - (dynamic text) |
| `Conversation closed` | forum/templates/forum/conversation.html:45 |
| `This conversation is closed. The other participant ended this conversation. You can start a new conversation by choosing a proposition.` | forum/services.py:178, 179; forum/templates/forum/conversation.html:48 |
| `The conversation stays here to read.` | forum/templates/forum/conversation.html:50 |
| `[button: Start a new conversation]` | forum/templates/forum/conversation.html:51 |
| `(aria-label) About this conversation` | forum/templates/forum/conversation.html:93 |
| `Who is here` | forum/templates/forum/conversation.html:95 |
| `You are talking with blocker_bee. The moderator refers to messages by number, such as “About your message 4”.` | forum/templates/forum/conversation.html:99 |
| `Limits` | forum/templates/forum/conversation.html:103 |
| `1 of 30 messages used` | forum/templates/forum/conversation.html:105 |
| `3,000 characters per message` | forum/templates/forum/conversation.html:106 |
| `One message every 30 seconds` | forum/templates/forum/conversation.html:107 |
| `[link: Why these limits?]` | forum/templates/forum/conversation.html:109 |
| `Someone bothering you?` | forum/templates/forum/conversation.html:113 |
| `Block blocker_bee` | forum/templates/forum/conversation.html:115, 119 |
| `Blocking ends this conversation for both of you. You will not see each other’s waiting positions or be paired again.` | forum/templates/forum/conversation.html:116 |
| `[button: Block blocker_bee]` | forum/templates/forum/conversation.html:115, 119 |


### 1.23 After unblocking someone (one person still blocked)

POST `/users/oak_lane/unblock/`: back to the blocked list with a message at the top.

| Visible text | Source (file:line) |
|---|---|
| `<title> Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `You unblocked oak_lane.` | forum/views.py:220 |
| `Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `People you have blocked cannot see your waiting positions, and you cannot see theirs.` | forum/templates/forum/blocked.html:5 |
| `loud_pigeon` | - (dynamic text) |
| `since 15 January 2026` | forum/templates/forum/blocked.html:11 |
| `[button: Unblock]` | forum/templates/forum/blocked.html:14 |
| `[link: ← Your discussions]` | forum/templates/forum/blocked.html:22 |


### 1.24 After unblocking the last person

Same, the list is now empty.

| Visible text | Source (file:line) |
|---|---|
| `<title> Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `You unblocked loud_pigeon.` | forum/views.py:220 |
| `Blocked people` | forum/templates/forum/blocked.html:2, 4 |
| `People you have blocked cannot see your waiting positions, and you cannot see theirs.` | forum/templates/forum/blocked.html:5 |
| `You have not blocked anyone.` | forum/templates/forum/blocked.html:20 |
| `[link: ← Your discussions]` | forum/templates/forum/blocked.html:22 |


## 2. Every refusal a participant can get

Each row was produced through the real page path (a POST to the real URL) unless the last column says it is not reachable from any page. "Banner" is the red box next to the form; "Also on the page" is the state box the page shows with it. Numbers come from settings; the settings overridden to trigger a refusal are named. All refusals keep what the person typed and change nothing (no message, run, proposition or block is saved). Waiting people can now post, so the old `waiting` refusal no longer exists.


### 2.1 Posting a message

| Code | HTTP | How it was triggered | Where shown | Banner (title line, then message) | Also on the page | Source (file:line) |
|---|---|---|---|---|---|---|
| empty | 200 | Post an empty box | Conversation page, above the message box | `Your message is empty`<br>`Your message is empty.` |  | forum/services.py:681; forum/templates/forum/conversation.html:59 |
| empty | 200 | Post only spaces and blank lines | same | `Your message is empty`<br>`Your message is empty. Nothing was sent and your text is kept.` |  | forum/templates/forum/conversation.html:59, 60; forum/services.py:681 |
| too_long | 200 | Post 3,050 characters | Conversation page, above the message box | `This message is too long`<br>`Your message is 3,050 characters; the limit is 3,000. Please shorten it by 50 characters. Messages are capped to keep the discussion readable. Nothing was sent and your text is kept.` |  | forum/services.py:236, 686, 687; forum/templates/forum/conversation.html:59, 60, 67 |
| too_fast | 200 | Post twice within the gap (the second post) | Conversation page, above the message box (the number of seconds varies by a second or two) | `Too soon to post again`<br>`Please wait 30 more seconds before posting again. Messages are limited to one every 30 seconds to keep the discussion readable. Nothing was sent and your text is kept.` |  | forum/services.py:704, 705; forum/templates/forum/conversation.html:59, 60, 107 |
| closed | 200 | Post in a conversation closed by the message limit (nobody ended it) | Closed page (the number in the text is the production limit, not the 4 used for page 1.15) | `This conversation is closed. It reached its limit of 30 messages. You can start a new one by choosing a proposition.` | `Conversation closed`<br>`This conversation is closed. It reached its limit of 30 messages. You can start a new one by choosing a proposition.`<br>`The conversation stays here to read.`<br>`[button: Start a new conversation]` | forum/services.py:184; forum/templates/forum/conversation.html:45, 50, 51 |
| closed | 200 | Post in a conversation you ended | Closed page | `This conversation is closed. You ended this conversation. You can start a new conversation by choosing a proposition.` | `Conversation closed`<br>`This conversation is closed. You ended this conversation. You can start a new conversation by choosing a proposition.`<br>`The conversation stays here to read.`<br>`[button: Start a new conversation]` | forum/services.py:176, 179; forum/templates/forum/conversation.html:45, 47, 50, 51 |
| closed | 200 | Post in a conversation the other participant ended (or that the other person ended by blocking you) | Closed page | `This conversation is closed. The other participant ended this conversation. You can start a new conversation by choosing a proposition.` | `Conversation closed`<br>`This conversation is closed. The other participant ended this conversation. You can start a new conversation by choosing a proposition.`<br>`The conversation stays here to read.`<br>`[button: Start a new conversation]` | forum/services.py:178, 179; forum/templates/forum/conversation.html:45, 48, 50, 51 |
| closed | 200 | Press End on a conversation that is already closed | Closed page | `This conversation is closed. You ended this conversation. You can start a new conversation by choosing a proposition.` | `Conversation closed`<br>`This conversation is closed. You ended this conversation. You can start a new conversation by choosing a proposition.`<br>`The conversation stays here to read.`<br>`[button: Start a new conversation]` | forum/services.py:176, 179; forum/templates/forum/conversation.html:45, 47, 50, 51 |
| conversation_full | 200 | Conversation still active but already holds the maximum number of messages (limit overridden to 3; only reachable if the limit is lowered after messages exist, because posting the last allowed message closes the conversation) | Page shows the banner plus a 'You cannot post right now' box | `This conversation has reached its limit of 3 messages and is closed. You can start a new one.` | `You cannot post right now`<br>`This conversation has reached its limit of 3 messages and is closed. You can start a new one.` | forum/services.py:199; forum/templates/forum/conversation.html:49, 87 |
| not_participant | 404 | A logged-in stranger posts to someone else's conversation, or opens a conversation that does not exist (same page for both) | Full 404 page (section 5) | `Not found`<br>`This conversation was not found, or you are not a participant.` |  | forum/views.py:37; forum/templates/404.html:2, 4, 6 |
| server_error | 500 | Unexpected failure while saving a message (simulated) | Conversation page, above the message box, text kept | `Your message was not sent`<br>`Something went wrong on our side and your message was not sent. Your text is still in the box; please try again.` |  | forum/services.py:745; forum/views.py:39; forum/templates/forum/conversation.html:59 |


### 2.2 Proposing, choosing a side and joining

| Code | HTTP | How it was triggered | Where shown | Banner (title line, then message) | Also on the page | Source (file:line) |
|---|---|---|---|---|---|---|
| empty | 200 | Publish an empty box | Propose page, above the box | `Your proposition is empty`<br>`Your message is empty. Write the proposition you want people to discuss, in up to 200 characters. Nothing was published and your text is kept.` |  | forum/services.py:230; forum/templates/forum/propose.html:12, 13, 25 |
| too_long | 200 | Publish 230 characters | Propose page, above the box | `This proposition is too long`<br>`Your proposition is 230 characters; the limit is 200. Please shorten it by 30 characters. Propositions are capped so the list stays easy to read. Nothing was published and your text is kept.` |  | forum/services.py:236, 237, 686; forum/templates/forum/propose.html:12, 13, 25 |
| duplicate | 200 | Publish text that matches an existing proposition (any case/spacing; the typed 'My position is that' start is ignored too) | Propose page; the page also shows a button 'Discuss the existing proposition' | `This proposition already exists`<br>`This proposition already exists. Choose it from the list.` |  | forum/services.py:248; forum/templates/forum/propose.html:12 |
| daily_limit | 200 | Publish a second proposition on the same UTC day (limit overridden to 1) | Propose page, above the box | `You cannot publish another proposition today`<br>`You have created 1 proposition today, which is the daily limit. Try again tomorrow (UTC), or pick an existing proposition to discuss. Nothing was published and your text is kept.` |  | forum/services.py:258; forum/templates/forum/propose.html:12, 13 |
| too_many_open | 200 | Publish a proposition while at the open-conversation cap (cap overridden to 1; the production default is no cap, so this refusal does not occur unless the tunable is set; the proposition is not saved) | Propose page, above the box (title falls back to 'Your proposition was not published') | `Your proposition was not published`<br>`You already have 1 open conversation. End one before starting another. Nothing was published and your text is kept.` |  | forum/templates/forum/propose.html:12, 13; forum/services.py:337 |
| too_many_open | 200 | Press a join button on the home page while at the cap | Home page, banner above the list (no title line) | `You already have 1 open conversation. End one before starting another.` |  | forum/services.py:337 |
| hidden | 200 | Press a join button of a proposition that has been hidden since the page loaded | Home page, banner above the list | `This proposition is not available.` |  | forum/services.py:314 |
| invalid_side | 200 | Press a join button with no side, or an unknown side (a hand-made request; the buttons always send a side) | Home page, banner above the list | `Choose a position first.` |  | forum/services.py:310; forum/views.py:36 |
| server_error | 500 | Unexpected failure while saving a proposition (simulated) | Propose page, above the box, text kept | `Your proposition was not published`<br>`Something went wrong on our side and your proposition was not created. Your text is still in the box; please try again. Nothing was published and your text is kept.` |  | forum/views.py:43; forum/templates/forum/propose.html:12, 13 |


### 2.3 Blocking

Blocking has few refusals: it is idempotent, and it never tells the blocked person. The service's own text for an unknown person (`That person was not found.`) is not reachable from a page, because the view answers an unknown username with the 404 page first.

| Code | HTTP | How it was triggered | Where shown | Banner (title line, then message) | Also on the page | Source (file:line) |
|---|---|---|---|---|---|---|
| invalid_block | 200 | Block yourself (a hand-made request; there is no button for it) | Home page, banner above the list | `You cannot block yourself.` |  | forum/services.py:566; forum/views.py:197 |
| (404) | 404 | Block or unblock a username that does not exist | Full 404 page (section 5) | `Not found`<br>`This page was not found.` |  | forum/templates/404.html:2, 4, 5, 6 |


### 2.4 Other refusals at page level

| Situation | What happens | What the person sees | Source |
|---|---|---|---|
| Not logged in: any page except How this works | HTTP 302, Location: /accounts/login/?next=/ | Redirect to the login page; the login page shows only its form (no sentence explaining the redirect) | config/settings.py:149 |
| Not logged in: POST to a conversation | HTTP 302, Location: /accounts/login/?next=/c/15/post/ | Same redirect; nothing is saved | forum/views.py:210 |
| Wrong method (opening a POST-only address by link) | HTTP 405, page body length 0 bytes | Browser shows a blank page: no text at all | forum/views.py:191 |
| Missing or stale form token (CSRF) | HTTP 403 | Page: Your form expired / The form you sent has expired or came from another page, so nothing was changed. Please go back, reload the page, and try again. / [button: Back to home] | forum/templates/403_csrf.html:12 |


### 2.5 Complete list of PostRejected codes (from the source) and whether the rows above cover it

| Code | Message a person would see (as shown in the banner, including any page suffix) | How captured | Raised at |
|---|---|---|---|
| closed | `This conversation is closed. It reached its limit of 30 messages. You can start a new one by choosing a proposition.` | through a page (rows above) | forum/services.py:188 |
| conversation_full | `This conversation has reached its limit of 3 messages and is closed. You can start a new one.` | through a page (rows above) | forum/services.py:197 |
| daily_limit | `You have created 1 proposition today, which is the daily limit. Try again tomorrow (UTC), or pick an existing proposition to discuss. Nothing was published and your text is kept.` | through a page (rows above) | forum/services.py:256 |
| duplicate | `This proposition already exists. Choose it from the list.` | through a page (rows above) | forum/services.py:246 |
| empty | `Your message is empty.` | through a page (rows above) | forum/services.py:228; forum/services.py:681 |
| hidden | `This proposition is not available.` | through a page (rows above) | forum/services.py:314 |
| invalid_block | `You cannot block yourself.` | through a page (rows above) | forum/services.py:564; forum/services.py:566; forum/services.py:569; forum/views.py:197 (view-level) |
| invalid_reply | `The message you are replying to is not in this conversation.` | direct service call: no page can reach it today | forum/services.py:730 |
| invalid_side | `Choose a position first.` | through a page (rows above) | forum/services.py:310; forum/views.py:290 (view-level) |
| login_required | `Please log in first.` | direct service call: no page can reach it today | forum/services.py:113 |
| not_participant | `This conversation was not found, or you are not a participant.` | through a page (rows above) | forum/services.py:124; forum/viewmodels.py:109 |
| not_saved | `Something went wrong on our side and your message was not sent. Your text is still in the box; please try again.` | direct service call: no page can reach it today | forum/services.py:743 |
| too_fast | `Please wait 30 more seconds before posting again. Messages are limited to one every 30 seconds to keep the discussion readable. Nothing was sent and your text is kept.` | through a page (rows above) | forum/services.py:702 |
| too_long | `Your message is 3,050 characters; the limit is 3,000. Please shorten it by 50 characters. Messages are capped to keep the discussion readable. Nothing was sent and your text is kept.` | through a page (rows above) | forum/services.py:234; forum/services.py:684 |
| too_many_open | `You already have 1 open conversation. End one before starting another. Nothing was published and your text is kept.` | through a page (rows above) | forum/services.py:335 |

Notes: `server_error` is not a `PostRejected` code; it is the page-level fallback in `forum/views.py` (`OUR_SIDE_FAILED`, `PROPOSITION_OUR_SIDE_FAILED`). `not_participant` never shows its own message on a page: the views turn it into the 404 page. `invalid_side` and `invalid_block` are also raised by the views themselves for a hand-made request. The banner title lines ("Too soon to post again", "This message is too long", "Your message is empty", "Your message was not sent", "You cannot publish another proposition today", "This proposition is too long", "This proposition already exists", "Your proposition is empty", "Your proposition was not published") are chosen by the template from the code, forum/templates/forum/conversation.html:59 and forum/templates/forum/propose.html:12. When the person typed something, the template adds `Nothing was sent and your text is kept.` (messages) or `Nothing was published and your text is kept.` (propositions, except for duplicates). On the home page the banner has no title line and no suffix.


## 3. Moderation notices and headings (forum/viewmodels.py)


### 3.1 The notice banner ("About the AI moderator")

Shown above the thread when the latest finished live run did not produce a normal answer. Container: `forum/templates/forum/conversation.html:20`, heading `About the AI moderator`. The text is chosen by `moderation_notice_for` (forum/viewmodels.py:31): the newest run of kind live whose status is not pending or running; status `skipped_disabled` gives the switched-off text, `failed` gives the problem text, `skipped_budget` looks at words in `failure_reason` (breaker, conversation, day/daily, site/total), anything else gives the generic pause text; `done` (whether the moderator posted or stayed silent) gives no notice. While a run is pending or running the previous notice stays, so it does not flicker. The polling script updates the same banner. The table below was produced by creating runs with each status and reading the notice.

| Run status | failure_reason | Situation | Notice shown | Source of the text | Check |
|---|---|---|---|---|---|
| skipped_disabled | llm_disabled | Moderation switched off (LLM_ENABLED is false) | `AI moderation is switched off right now; your messages are still posted.` | forum/viewmodels.py:27 |  |
| failed | structural | A failure in the moderator's output | `The AI moderator ran into a problem on the last message; your messages are still posted.` | forum/viewmodels.py:28 |  |
| failed | api_error | Any failed run | `The AI moderator ran into a problem on the last message; your messages are still posted.` | forum/viewmodels.py:28 |  |
| skipped_budget | breaker_open | Circuit breaker open (real reason set by moderation/pipeline.py) | `The AI moderator is paused after repeated problems and will resume by itself; messages are still posted.` | forum/viewmodels.py:24 |  |
| skipped_budget | budget_exceeded | A cap was reached (real reason set by moderation/pipeline.py) | `AI moderation is paused right now and will resume when it can; messages are still posted.` | forum/viewmodels.py:20 |  |
| skipped_budget | budget_unavailable | Cap could not be checked (real reason) | `AI moderation is paused right now and will resume when it can; messages are still posted.` | forum/viewmodels.py:20 |  |
| skipped_budget | refused | Model refused (real reason) | `AI moderation is paused right now and will resume when it can; messages are still posted.` | forum/viewmodels.py:20 |  |
| skipped_budget | conversation cap | Reason text containing 'conversation' (NOT produced by the pipeline today) | `The AI moderator will not comment further in this conversation; messages are still posted.` | forum/viewmodels.py:22 |  |
| skipped_budget | daily cap | Reason text containing 'day' or 'daily' (NOT produced by the pipeline today) | `AI moderation is paused for today and will resume tomorrow; messages are still posted.` | forum/viewmodels.py:21 |  |
| skipped_budget | site total | Reason text containing 'site' or 'total' (NOT produced by the pipeline today) | `AI moderation is paused right now and will resume later; messages are still posted.` | forum/viewmodels.py:23 |  |
| skipped_budget | (empty) | skipped_budget with an empty reason | `AI moderation is paused right now and will resume when it can; messages are still posted.` | forum/viewmodels.py:20 |  |
| done | (empty) | Finished normally (moderator posted or said nothing) | `(no notice)` | - |  |
| pending | (empty) | Run still waiting (older notice, if any, stays; none here) | `(no notice)` | - |  |


### 3.2 The banner as it appears on the page (circuit breaker example)

| Visible text | Source |
|---|---|
| `About the AI moderator` | forum/templates/forum/conversation.html:21 |
| `The AI moderator is paused after repeated problems and will resume by itself; messages are still posted.` | forum/viewmodels.py:25 |


### 3.3 Headings above a moderator message

Chosen by `moderation_heading` / `_act_heading` (forum/viewmodels.py:62) from the valid acts of the run that posted the message, worked out per viewer (the same message has different headings for the two people). Rules: an act addressed to everyone or about both people gives `For both of you`; an act about no one gives `About the conversation`; otherwise it is `About your message N` when the act is about the viewer, or `About <username>'s message N` when it is about the other person (the other person's username; `the other participant's` only if the person has no username). N is the position number of the message the act quotes, preferring one written by the person the act is about, else the message that triggered the run if that person wrote it. When no such message can be named the heading is `About your messages` / `About <username>'s messages`. If a run has acts with different headings the result is `For both of you`; no run, or no valid act, gives `About the conversation`. The table was produced from real runs.

| Situation | Heading for the first person (zelda_mox; the other is quincy_ray) | Heading for the second person (quincy_ray; the other is zelda_mox) |
|---|---|---|
| Act about the first person's message (quoted) | `About your message 2` | `About zelda_mox's message 2` |
| Act about the second person's message (quoted) | `About quincy_ray's message 5` | `About your message 5` |
| Act about the first person, no quoted message, second person's message triggered the run | `About your messages` | `About zelda_mox's messages` |
| Act about the second person, no quoted message, first person's message triggered the run | `About quincy_ray's messages` | `About your messages` |
| Act about the first person, no quoted message, their own message triggered the run | `About your message 11` | `About zelda_mox's message 11` |
| Act addressed to everyone | `For both of you` | `For both of you` |
| Act about both people | `For both of you` | `For both of you` |
| Act about no one | `About the conversation` | `About the conversation` |
| Two acts with different headings | `For both of you` | `For both of you` |
| Moderator message with no run or no valid act behind it | `About the conversation` | `About the conversation` |

Source of the strings: `forum/viewmodels.py:17`; `forum/viewmodels.py:18`; `forum/viewmodels.py:72`; `forum/viewmodels.py:73`.


## 4. The moderator card and the message frame

Template: `forum/templates/forum/_message.html` (used by the page and by the polling endpoint, so both look the same). Every message on a conversation page is one of the kinds below. The other person's messages show their username; no label letter ever appears.


### 4.1 Moderator card

| Visible text | Source |
|---|---|
| `AI MODERATOR About quincy_ray's message 2 · automated · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `SAMPLE MODERATOR TEXT.` | - (dynamic text) |
| `[link: Why is there an AI moderator, and can it be wrong?]` | forum/templates/forum/_message.html:2 |


### 4.2 Moderator card without a heading (a message the server could not attribute)

| Visible text | Source |
|---|---|
| `AI MODERATOR automated · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `SAMPLE MODERATOR TEXT.` | - (dynamic text) |
| `[link: Why is there an AI moderator, and can it be wrong?]` | forum/templates/forum/_message.html:2 |


### 4.3 Your message

| Visible text | Source |
|---|---|
| `You message 1 · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `SAMPLE USER TEXT.` | - (dynamic text) |


### 4.4 The other person's message (with their username)

| Visible text | Source |
|---|---|
| `quincy_ray message 2 · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `SAMPLE USER TEXT.` | - (dynamic text) |


### 4.5 The other person's message (view gives no username; fallback text)

| Visible text | Source |
|---|---|
| `The other participant message 2 · 09:00 UTC` | forum/templates/forum/_message.html:2 |
| `SAMPLE USER TEXT.` | - (dynamic text) |

Elements of the moderator card: the badge `AI MODERATOR` (forum/templates/forum/_message.html:2), the heading (section 3.3), the word `automated`, the time as `HH:MM UTC`, then the moderator's text, then the link `Why is there an AI moderator, and can it be wrong?` which goes to `/how-it-works/` (no anchor, so it lands at the top of that page). Times of all messages are shown in UTC.


## 5. Error pages and redirects

Templates in `forum/templates/`. 400, 403_csrf and 500 are standalone pages (they do not depend on the database or the session); 403 and 404 extend the normal page shell. Rendered with `render_to_string`, and the CSRF page also live (section 2.4).


### 5.1 400.html

Bad request (for example an invalid Host header). Django's handler400.

| Visible text | Source (file:line) |
|---|---|
| `<title> Request not understood` | forum/templates/400.html:6, 12 |
| `Request not understood` | forum/templates/400.html:6, 12 |
| `The site could not understand that request. Please go back and try again.` | forum/templates/400.html:13 |
| `[button: Back to home]` | forum/templates/400.html:14 |


### 5.2 403.html

Permission denied (Django's handler403).

| Visible text | Source (file:line) |
|---|---|
| `<title> Not allowed` | forum/templates/403.html:2, 4 |
| `Not allowed` | forum/templates/403.html:2, 4 |
| `You do not have permission to do that. If you think this is a mistake, go back to the home page and try again.` | forum/templates/403.html:5 |
| `[button: Back to home]` | forum/templates/403.html:6 |


### 5.3 403_csrf.html

Missing or stale form token: Django shows this template for CSRF failures.

| Visible text | Source (file:line) |
|---|---|
| `<title> Your form expired` | forum/templates/403_csrf.html:6, 12 |
| `Your form expired` | forum/templates/403_csrf.html:6, 12 |
| `The form you sent has expired or came from another page, so nothing was changed. Please go back, reload the page, and try again.` | forum/templates/403_csrf.html:13 |
| `[button: Back to home]` | forum/templates/403_csrf.html:14 |


### 5.4 404.html

Any unknown address (for example /nothing-here/, or blocking a username that does not exist).

| Visible text | Source (file:line) |
|---|---|
| `<title> Page not found` | forum/templates/404.html:2 |
| `Not found` | forum/templates/404.html:4 |
| `This page was not found.` | forum/templates/404.html:5 |
| `[button: Back to home]` | forum/templates/404.html:6 |


### 5.5 404.html (with the conversation text)

A conversation that does not exist and a conversation the person is not in: the very same page, so nothing reveals which conversations exist.

| Visible text | Source (file:line) |
|---|---|
| `<title> Page not found` | forum/templates/404.html:2 |
| `Not found` | forum/templates/404.html:4 |
| `This conversation was not found, or you are not a participant.` | forum/views.py:37 |
| `[button: Back to home]` | forum/templates/404.html:6 |


### 5.6 500.html

Unexpected server error (Django's handler500).

| Visible text | Source (file:line) |
|---|---|
| `<title> Something went wrong` | forum/templates/500.html:6, 12 |
| `Something went wrong` | forum/templates/500.html:6, 12 |
| `Something went wrong on our side. Nothing you did caused it, and no details are shown here. Please try again in a moment.` | forum/templates/500.html:13 |
| `[button: Back to home]` | forum/templates/500.html:14 |

Live check: an unknown address gives HTTP 404 with `This page was not found.`; a bad Host header gives HTTP 400 with `Request not understood`; a crashing page gives HTTP 500 with `Something went wrong`. Each shows the same text as its template above.


### 5.7 Login redirect

A person who is not logged in and opens any page except How this works is redirected (HTTP 302) to `/accounts/login/?next=/` (config/settings.py:149, `@login_required` in forum/views.py). There is no text about the redirect: the login page just shows its normal content (title `Log in`, heading `Log in`). A logged-in person opening the login page is sent on to the home page (`redirect_authenticated_user`).


## 6. Client-side strings (forum/static/forum/compose.js, poll.js)

| Text | Where and when | Source |
|---|---|---|
| `Your message is 3,050 characters; the limit is 3,000. Please shorten it by 50 characters. You can still press the button; the site will explain if it cannot send it.` | Red advice line under the box, while the count is over the limit. The word `message` becomes `proposition` on the propose page. The number is formatted en-US (`3,050`). The word `characters` is always plural, even for 1. | forum/static/forum/compose.js:49 |
| `(empty: advice line hidden)` | Advice line when the count is at or under the limit | forum/static/forum/compose.js:53 |
| `<n> / <limit> characters` | Live counter next to the button: the number is updated by compose.js; the words come from the template | forum/templates/forum/conversation.html:67 |
| `This conversation has changed. [link: Reload to see the latest.]` | Blue banner (hidden by default). poll.js shows it when the conversation changed state (someone joined, someone ended it, posting became possible or impossible) while the person has text in the box, and keeps polling for messages; if the box is empty poll.js reloads the page without any message | forum/templates/forum/conversation.html:26 |
| `(server text) About the AI moderator: <notice>` | poll.js replaces the notice text with the server's notice and shows or hides the banner (texts: section 3.1) | forum/static/forum/poll.js:48 |
| `<n> of 30 messages used` | poll.js updates the number in the Limits card | forum/static/forum/poll.js:62 |
| `(server HTML) new messages` | poll.js inserts the server-rendered message partial (section 4), including the other person's username | forum/static/forum/poll.js:44 |

Everything else in the two scripts is technical (element ids, attribute names, HTTP header values). There is no message shown when polling fails: poll.js silently retries with a longer wait (up to 60 seconds) and the page just stops updating. String literals with words found by scanning the scripts: `forum/static/forum/compose.js:12: use strict`; `forum/static/forum/poll.js:13: use strict`.


## 7. Accounts pages (username and password only; no email)

Step 6c has landed: people register with a username and a password only. There is no email, no confirmation link and no password reset by email; a forgotten password is reset by the person running the site. The confirmation, resend and reset pages and their templates no longer exist (their old addresses give the normal 404 page). The register wording was fixed verbatim in `docs/step6c_brief.md` (docs/step6c_brief.md:18); the check below compares the rendered page with it.


### 7.1 Register page

Route `/accounts/register/`. On success the person is created active, logged in and sent to the home page.

| Visible text | Source (file:line) |
|---|---|
| `<title> Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Choose a username and a password. You do not need an email address.` | accounts/templates/accounts/register.html:5 |
| `Username` | accounts/registration.py:34 |
| `3 to 30 characters: letters A-Z, digits, hyphens and underscores.` | accounts/registration.py:39 |
| `Password` | accounts/registration.py:44 |
| `Your password must meet these rules:` | accounts/templates/accounts/register.html:15 |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `Password again` | accounts/registration.py:50 |
| `There is no password reset by email. If you forget your password, ask the person running this site to reset it.` | accounts/templates/accounts/register.html:19 |
| `[button: Create account]` | accounts/templates/accounts/register.html:20 |
| `[link: Already have an account? Log in]` | accounts/templates/accounts/register.html:22 |

Check against the brief's fixed wording: all five texts are present.


### 7.2 Register: empty submit (validation messages)

| Visible text | Source (file:line) |
|---|---|
| `<title> Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Choose a username and a password. You do not need an email address.` | accounts/templates/accounts/register.html:5 |
| `Username` | accounts/registration.py:34 |
| `3 to 30 characters: letters A-Z, digits, hyphens and underscores.` | accounts/registration.py:39 |
| `Password` | accounts/registration.py:44 |
| `Your password must meet these rules:` | accounts/templates/accounts/register.html:15 |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `Password again` | accounts/registration.py:50 |
| `There is no password reset by email. If you forget your password, ask the person running this site to reset it.` | accounts/templates/accounts/register.html:19 |
| `[button: Create account]` | accounts/templates/accounts/register.html:20 |
| `[link: Already have an account? Log in]` | accounts/templates/accounts/register.html:22 |


### 7.3 Register: bad username, weak password, mismatch (validation messages)

| Visible text | Source (file:line) |
|---|---|
| `<title> Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Choose a username and a password. You do not need an email address.` | accounts/templates/accounts/register.html:5 |
| `Username` | accounts/registration.py:34 |
| `Usernames may contain only the letters A-Z, digits, hyphens and underscores.` | accounts/validators.py:18 |
| `3 to 30 characters: letters A-Z, digits, hyphens and underscores.` | accounts/registration.py:39 |
| `Password` | accounts/registration.py:44 |
| `Your password must meet these rules:` | accounts/templates/accounts/register.html:15 |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `Password again` | accounts/registration.py:50 |
| `The two passwords do not match. Type the same password in both boxes.` | accounts/registration.py:67 |
| `There is no password reset by email. If you forget your password, ask the person running this site to reset it.` | accounts/templates/accounts/register.html:19 |
| `[button: Create account]` | accounts/templates/accounts/register.html:20 |
| `[link: Already have an account? Log in]` | accounts/templates/accounts/register.html:22 |


### 7.4 Register: username already taken (any case)

| Visible text | Source (file:line) |
|---|---|
| `<title> Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Create an account` | accounts/templates/accounts/register.html:2, 4; forum/templates/forum/base.html:25 |
| `Choose a username and a password. You do not need an email address.` | accounts/templates/accounts/register.html:5 |
| `Username` | accounts/registration.py:34 |
| `That username is taken.` | accounts/registration.py:28 |
| `3 to 30 characters: letters A-Z, digits, hyphens and underscores.` | accounts/registration.py:39 |
| `Password` | accounts/registration.py:44 |
| `Your password must meet these rules:` | accounts/templates/accounts/register.html:15 |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `Password again` | accounts/registration.py:50 |
| `There is no password reset by email. If you forget your password, ask the person running this site to reset it.` | accounts/templates/accounts/register.html:19 |
| `[button: Create account]` | accounts/templates/accounts/register.html:20 |
| `[link: Already have an account? Log in]` | accounts/templates/accounts/register.html:22 |

Removed addresses now answer with the ordinary 404 page: /accounts/register/check-email/: HTTP 404; /accounts/resend-confirmation/: HTTP 404; /accounts/confirm/x/: HTTP 404; /accounts/password-reset/: HTTP 404.


### 7.5 Login page

Route `/accounts/login/`. There is no 'Forgot your password?' link. The field labels are Django's `Username:` and `Password:`.

| Visible text | Source (file:line) |
|---|---|
| `<title> Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Username` | - (dynamic text, form label or Django built-in text) |
| `Password` | - (dynamic text, form label or Django built-in text) |
| `[button: Log in]` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |


### 7.6 Login: wrong username or password

The same message for a wrong username and a wrong password.

| Visible text | Source (file:line) |
|---|---|
| `<title> Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Incorrect username or password. Check both and try again.` | accounts/authviews.py:27 |
| `Username` | - (dynamic text, form label or Django built-in text) |
| `Password` | - (dynamic text, form label or Django built-in text) |
| `[button: Log in]` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |


### 7.7 Login: lockout page (HTTP 429)

After 5 failed attempts, for 15 minutes (both numbers from tunables). The minutes shown count down.

| Visible text | Source (file:line) |
|---|---|
| `<title> Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Too many failed attempts. Try again in 15 minutes. This pause protects accounts from password guessing.` | accounts/authviews.py:68 |
| `Username` | - (dynamic text, form label or Django built-in text) |
| `Password` | - (dynamic text, form label or Django built-in text) |
| `[button: Log in]` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |


### 7.8 Logout confirmation page (opening the logout address with a link)

HTTP 405: logging out needs a button press. The normal way out is the header button.

| Visible text | Source (file:line) |
|---|---|
| `<title> Log out` | accounts/templates/accounts/logout_confirm.html:2, 4, 8; forum/templates/forum/base.html:21 |
| `Log out` | accounts/templates/accounts/logout_confirm.html:2, 4, 8; forum/templates/forum/base.html:21 |
| `Logging out needs a button press, not a link, so a stray link on another site cannot log you out. Press the button to log out.` | accounts/templates/accounts/logout_confirm.html:5 |
| `[button: Log out]` | accounts/templates/accounts/logout_confirm.html:2, 4, 8; forum/templates/forum/base.html:21 |


### 7.9 After logging out (flash message on the login page)

The flash message `You have been logged out.` is in the `messages` list of the page shell (accounts/authviews.py:134).

| Visible text | Source (file:line) |
|---|---|
| `<title> Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `You have been logged out.` | accounts/authviews.py:134 |
| `Log in` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |
| `Username` | - (dynamic text, form label or Django built-in text) |
| `Password` | - (dynamic text, form label or Django built-in text) |
| `[button: Log in]` | accounts/templates/accounts/login.html:2, 4, 12; forum/templates/forum/base.html:24 |


### 7.10 Change password page

Logged in. Labels and help text come from Django's password-change form and the password rules in config/settings.py.

| Visible text | Source (file:line) |
|---|---|
| `<title> Change your password` | accounts/templates/accounts/password_change_form.html:2, 4 |
| `Change your password` | accounts/templates/accounts/password_change_form.html:2, 4 |
| `Enter your current password, then the new one twice.` | accounts/templates/accounts/password_change_form.html:5 |
| `Old password` | - (dynamic text, form label or Django built-in text) |
| `New password` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `New password confirmation` | - (dynamic text, form label or Django built-in text) |
| `Enter the same password as before, for verification.` | - (dynamic text, form label or Django built-in text) |
| `[button: Change password]` | accounts/templates/accounts/password_change_form.html:9 |


### 7.11 Change password: wrong old password, weak new password

Error messages are Django's built-in ones plus the password validators configured in settings.

| Visible text | Source (file:line) |
|---|---|
| `<title> Change your password` | accounts/templates/accounts/password_change_form.html:2, 4 |
| `Change your password` | accounts/templates/accounts/password_change_form.html:2, 4 |
| `Enter your current password, then the new one twice.` | accounts/templates/accounts/password_change_form.html:5 |
| `Old password` | - (dynamic text, form label or Django built-in text) |
| `Your old password was entered incorrectly. Please enter it again.` | - (dynamic text, form label or Django built-in text) |
| `New password` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be too similar to your other personal information.` | - (dynamic text, form label or Django built-in text) |
| `Your password must contain at least 12 characters.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be a commonly used password.` | - (dynamic text, form label or Django built-in text) |
| `Your password can’t be entirely numeric.` | - (dynamic text, form label or Django built-in text) |
| `New password confirmation` | - (dynamic text, form label or Django built-in text) |
| `The two password fields didn’t match. Enter the same password as before, for verification.` | - (dynamic text, form label or Django built-in text) |
| `[button: Change password]` | accounts/templates/accounts/password_change_form.html:9 |


### 7.12 Change password: done page

| Visible text | Source (file:line) |
|---|---|
| `<title> Password changed` | accounts/templates/accounts/password_change_done.html:2 |
| `Your password has been changed` | accounts/templates/accounts/password_change_done.html:4 |
| `You are still logged in on this device. Other devices where you were logged in have been logged out. [link: Back to the forum]` | accounts/templates/accounts/password_change_done.html:5 |

Old email templates still on disk: none.


## 8. Everything else a participant can see


### 8.1 Site header, logged out (shown on every non-conversation page)

| Visible text | Source |
|---|---|
| `[link: AI-Moderated Discussion Forum]` | forum/templates/forum/base.html:6, 13 |
| `(aria-label) Site` | forum/templates/forum/base.html:14 |
| `[link: How this works] [link: Log in] [link: Create an account]` | forum/templates/forum/base.html:16, 24, 25 |


### 8.2 Site header, logged in (shown on home, discussions, blocked, propose, how it works, accounts pages)

| Visible text | Source |
|---|---|
| `[link: AI-Moderated Discussion Forum]` | forum/templates/forum/base.html:6, 13 |
| `(aria-label) Site` | forum/templates/forum/base.html:14 |
| `Signed in as outsider_kit [link: How this works] [link: Your discussions]` | forum/templates/forum/base.html:15, 16, 18 |
| `[button: Log out]` | forum/templates/forum/base.html:21 |


### 8.3 Site header on a conversation page (no username of the viewer, deliberately)

| Visible text | Source |
|---|---|
| `[link: AI-Moderated Discussion Forum]` | forum/templates/forum/base.html:6, 13 |
| `(aria-label) Site` | forum/templates/forum/base.html:14 |
| `[link: How this works] [link: Your discussions]` | forum/templates/forum/base.html:16, 18 |
| `[button: Log out]` | forum/templates/forum/base.html:21 |


### 8.4 Skip link (only visible to keyboard and screen-reader users)

| Visible text | Source |
|---|---|
| `[link: Skip to the page content]` | forum/templates/forum/base.html:11 |


### 8.5 Footer

No template has a footer element.


### 8.6 Flash messages

The page shell (`forum/templates/forum/base.html:30`) prints any Django flash message in a status list. Flash messages set anywhere in the site code (`{name}` is the other person's username):

| Message text | Source |
|---|---|
| `You blocked {name}. The conversation has ended.` | forum/views.py:204 |
| `You blocked {name}.` | forum/views.py:206 |
| `You unblocked {name}.` | forum/views.py:220 |
| `You have been logged out.` | accounts/authviews.py:134 |


### 8.7 Page titles (<title>) of every page rendered above

| Title | Page |
|---|---|
| `Waiting to discuss` | 1.1 Home (waiting list): empty state |
| `Waiting to discuss` | 1.2 Home (waiting list): with cards |
| `Your discussions` | 1.4 Your discussions: with rows of every kind |
| `Your discussions` | 1.5 Your discussions: search with no match |
| `Your discussions` | 1.5b Your discussions: search with a match |
| `Your discussions` | 1.5c Your discussions: no discussions yet |
| `Blocked people` | 1.6 Blocked people: nobody blocked |
| `Start a new discussion` | 1.7 Start a new discussion (propose page), with the seeded topics |
| `How this works` | 1.10 How this works (anonymous and logged in), in full |
| `Cats make better pets than dogs.` | 1.11 Conversation: waiting for someone to take the other position (creator alone) |
| `Cities should build protected bike lanes even when it means removing some street parking.` | 1.13 Conversation: active with a few messages |
| `Cities should build protected bike lanes even when it means removing some street parking.` | 1.14 Conversation: active with moderator messages |
| `Every citizen should do a year of national service.` | 1.15 Conversation: closed by the message limit |
| `Zoos do more good than harm.` | 1.16 Conversation: ended by you |
| `Zoos do more good than harm.` | 1.17 Conversation: ended by the other participant |
| `Waiting to discuss` | 1.19 After blocking someone from a waiting card |
| `Your discussions` | 1.20 After blocking the person you are talking to |
| `Blocked people` | 1.21 Blocked people: with people |
| `Fireworks should be banned.` | 1.22 Conversation ended because the other person blocked you |
| `Blocked people` | 1.23 After unblocking someone (one person still blocked) |
| `Blocked people` | 1.24 After unblocking the last person |
| `Request not understood` | 5.1 400.html |
| `Not allowed` | 5.2 403.html |
| `Your form expired` | 5.3 403_csrf.html |
| `Page not found` | 5.4 404.html |
| `Page not found` | 5.5 404.html (with the conversation text) |
| `Something went wrong` | 5.6 500.html |
| `Create an account` | 7.1 Register page |
| `Create an account` | 7.2 Register: empty submit (validation messages) |
| `Create an account` | 7.3 Register: bad username, weak password, mismatch (validation messages) |
| `Create an account` | 7.4 Register: username already taken (any case) |
| `Log in` | 7.5 Login page |
| `Log in` | 7.6 Login: wrong username or password |
| `Log in` | 7.7 Login: lockout page (HTTP 429) |
| `Log out` | 7.8 Logout confirmation page (opening the logout address with a link) |
| `Log in` | 7.9 After logging out (flash message on the login page) |
| `Change your password` | 7.10 Change password page |
| `Change your password` | 7.11 Change password: wrong old password, weak new password |
| `Password changed` | 7.12 Change password: done page |

Default title of the page shell: `AI-Moderated Discussion Forum` (forum/templates/forum/base.html:6). The conversation page's title is the proposition text itself.


### 8.8 Seeded topics (the suggested topics on the propose page and in the waiting list)

Read from `forum/seed_topics.json` (loaded with `manage.py seed_topics`). Each topic has a stated position and an opposing position; the two are shown to people as join labels and in position lines.

| Stated position | Opposing position | Source |
|---|---|---|
| `Cities should cap how much landlords can raise rents each year.` | `Cities should not cap how much landlords can raise rents each year.` | forum/seed_topics.json:5 |
| `Possession of small amounts of illegal drugs for personal use should be decriminalized.` | `Possession of small amounts of illegal drugs for personal use should not be decriminalized.` | forum/seed_topics.json:49 |
| `Cities should limit their local police's cooperation with federal immigration enforcement.` | `Cities should not limit their local police's cooperation with federal immigration enforcement.` | forum/seed_topics.json:93 |
| `High schools should start no earlier than 8:30 a.m.` | `High schools should be free to start earlier than 8:30 a.m.` | forum/seed_topics.json:137 |
| `Cities should build protected bike lanes even when it means removing some street parking.` | `Cities should not remove street parking in order to build protected bike lanes.` | forum/seed_topics.json:181 |
| `Employers should allow office workers to work from home most of the week.` | `Employers should not have to allow office workers to work from home most of the week.` | forum/seed_topics.json:225 |


### 8.9 Other visible elements

- Browser address and tab title come from section 8.7. The proposition text, usernames and the messages are user-supplied, so they are not source text.
- Character counter format: `<n> / 3,000 characters` (message) and `<n> / 200 characters` (proposition), thousands separated with a comma.
- Time format on messages: `HH:MM UTC` (24 hour), with the full time in the `datetime` attribute. The date on the blocked list is `j F Y` (for example `15 January 2026`).
- Static pages other than those above: none. There is no privacy page, contact page or terms page (the How this works page says `[contact to be added]` and `[Retention period and who can see the data: to be written before launch.]` in square brackets: placeholders).


## 9. Observations for the owner

Automatic checks (a, d, e) are recomputed on every run over every string captured above and the template files. Items marked (manual) were read from the code as of the generation date and may need re-checking after changes.


### (a) Costs, spending, budgets, tokens, the API

No violations: no text a participant can see (rendered pages, refusals, notices, templates, error pages, client scripts, the account pages, the seeded topics) contains cost, spend, budget, token, API, price, billing or credit wording (scan pattern: `\b(costs?|spend\w*|spent|budgets?|tokens?|API|Anthropic|dollars?|USD|prices?|pricing|billing|credits?|paid|quota)\b|\$`).

- (manual) Indirect hints, not violations of the wording rule: the notices `AI moderation is paused for today and will resume tomorrow`, `...will resume later` and `The AI moderator will not comment further in this conversation` describe a cap without naming money. The pipeline sets only the reasons `breaker_open`, `budget_exceeded`, `budget_unavailable` and `refused` (moderation/pipeline.py), so a cap pause shows the generic text and those three specific notices are never chosen today (section 3.1).


### (b) Places where a person is refused but the text does not say why or what to do next

- (manual) Post refused as empty: banner title `Your message is empty` and the text `Your message is empty.` say what, not what to do next. Source forum/services.py:230. The propose-empty message does say what to do.
- (manual) `hidden` (a proposition hidden since the page loaded): `This proposition is not available.` gives neither a reason nor a next step, and appears as a bare banner on the home page. Source forum/services.py:314.
- (manual) `duplicate`: `This proposition already exists. Choose it from the list.` The home page now lists only positions someone is waiting on, so the existing proposition may not be in any list; the button `Discuss the existing proposition` is the real next step. Source forum/services.py:248.
- (manual) `too_many_open` (only if the tunable is set; the default is no cap): `End one before starting another.` does not say where the open conversations are; the header link `Your discussions` now lists them but the message does not mention it. Source forum/services.py:337.
- (manual) Being blocked: the blocked person's conversation simply shows `The other participant ended this conversation.` and the waiting cards of the blocker vanish. By design they are never told why; noted here because it is a place where a person cannot post and the text gives the (true but incomplete) reason only.
- (manual) Fallback box text `Posting is not available in this conversation at the moment.` (title `You cannot post right now`) has no reason or next step. It is shown only if the page cannot post and no reason exists, which the current view model never produces. Source forum/templates/forum/conversation.html:88.
- (manual) `invalid_side` `Choose a position first.` and `invalid_block` `You cannot block yourself.` are reachable only by a hand-made request; both say what is wrong. `That person was not found.` (service) is not reachable from a page.
- (manual) Wrong method (opening a POST-only address such as /c/<id>/post/ or /users/<name>/block/ by link): HTTP 405 with a completely blank page. Measured in section 2.4. Source forum/views.py:191.
- (manual) Not logged in: the redirect to the login page has no sentence saying that login is needed to see the page (section 5).
- (manual) Polling failure: if the connection drops, the page silently stops updating; nothing tells the person (section 6).
- (manual) Placeholders a person can read today: `[contact to be added]` in the How this works page (forum/templates/forum/how_it_works.html:13): the reader is told to report abuse to someone but not how.
- (manual) The login page after a lockout and the 403 page do say what to do; all closed states have a reason and a next step; too fast, too long, duplicate, daily limit have both; the empty states of the home page, Your discussions and Blocked people say what to do or that nothing is there.


### (c) Inconsistent wording for the same thing

- (manual) `discussion`, `conversation`, `debate` (gone), `proposition`, `position` and `topic` are all used for related things: the header link and page are `Your discussions`, the buttons are `Start a new discussion` (home) and `Start a new conversation` (closed conversation, which goes to the home page, not to the propose page), the end card says `End conversation`, the propose page publishes a `proposition` while its label says `My position is that`, and the seeded section says `topics`.
- (manual) Old wording that no longer matches the waiting-list design: the refusal texts say `You can start a new one by choosing a proposition` (forum/services.py:179) and `Choose it from the list`; the home page now offers positions to take.
- (auto) The way back to the home page is worded: `[button: Back to home]`, `[link: ← Back to home]`, `[link: ← Home]` (not one wording everywhere: the conversation page says `Home`, the others `Back to home`, and arrows differ).
- (manual) The empty-proposition refusal says `Your message is empty. Write the proposition you want people to discuss...` under the banner title `Your proposition is empty` (noun changes mid-message). Source forum/services.py:230.
- (manual) The message-limit-reached text exists in three wordings (forum/services.py:184, forum/services.py:199, and the template fallback forum/templates/forum/conversation.html:49, which is not shown today because a reason is always present when closed).
- (manual) Button and hint disagree: the button is `Post message` but the hint says `Nothing you type is sent until you press Post.` (forum/templates/forum/conversation.html:34).
- (manual) The other person is `<username>` on the conversation page, in the block card and in moderator headings, but `The other participant` in every closed-conversation sentence (`The other participant ended this conversation.`) and in the 404 text, `the other person` in the Who-is-here fallback, and `Someone` in the card fallback.
- (manual) Apostrophes: moderator headings use a straight apostrophe (`About quincy_ray's message 2`, from viewmodels.py) while the templates use a curly one (`moderator&rsquo;s`).
- (manual) `Signed in as ...` in the header versus `Log in` / `Log out` / `You have been logged out.`
- (manual) `1 characters`: the browser advice line always says `characters` (forum/static/forum/compose.js:50); the server says `1 character`.
- (manual) Message numbers versus the message count: headings say `About your message N` where N counts every message including the moderator's, while the Limits card says `X of 30 messages used`, which counts only people's messages.
- (manual) The Limits list on How this works says `You can post one message every 30 seconds` and, two lines below, `Nothing stops you posting several messages in a row.` (forum/templates/forum/how_it_works.html:31); it means no turn-taking, but reads as a contradiction.
- (manual) Hard-coded numbers in text that otherwise comes from settings: `about 500 words` (forum/templates/forum/how_it_works.html:28) and `Active · 2 participants` (forum/templates/forum/conversation.html:13); they go stale if the tunables change.
- (manual) The 404 page has the title `Page not found` but the heading `Not found` (forum/templates/404.html:4).
- (manual) `Blocked people` says `since 15 January 2026` (a date) while messages show `HH:MM UTC`.


### (d) Text that could reveal a participant label, a username or the other person's identity

- Automatic scan of 28 rendered pages (both viewers where relevant): no email address; no `Participant A/B`-style text; on conversation pages only the OTHER person's username (never the viewer's own, never a third person's); on propose, How this works, error pages only the viewer's own name (in the header); usernames of others only where they are meant to show (home cards, Your discussions, Blocked people, conversation). Result: nothing found.
- Automatic scan of the polling response (`/c/<id>/messages/`, viewer `zelda_mox`): no viewer username, email or label letter; it carries `other_username` and `author_name` (the other person) by design.
- (manual) Usernames are now shown on purpose: on every waiting card (to any logged-in person who is not blocked), in Your discussions (`with <username>`), in Blocked people, on the conversation page (other person only), in the block card and in moderator headings. The header shows the viewer's OWN username on all pages except the conversation page.
- (manual) The other person's username and the `Block <username>` card also stay on closed conversations (including the one the other person ended by blocking you, where the card offers to block them).
- (manual) A waiting card also shows the position the person holds (`<username> is waiting to discuss: <position>`), so any logged-in person can see which position a named person took. By design under the waiting-list model; noted for the neutrality review.
- (manual) Label letters never appear. The moderator heading says whose message a note is about by username or `your`; the moderator's own text is model-written; the pipeline rejects acts whose text names a label (moderation/label_check.py), but a person's own words can of course be quoted.
- (manual) The 404 page uses one text for a missing conversation and for someone else's conversation, so nothing reveals which exist. Blocking an unknown username gives the plain 404 page while blocking a real one succeeds, so the block address can be used to test whether a username exists (usernames are visible on waiting cards anyway).


### (e) Leftover mentions of email confirmation or password reset by email

Scan of all template text and the Python strings of the user-facing modules for email, confirmation, resend, password reset and forgot wording (`e-?mail|confirmation link|confirm your|resend|password reset|reset (your )?password|forgot`):

| Text | Source | Status |
|---|---|---|
| `There is no password reset by email. If you forget your password, ask the person running this site to reset it.` | accounts/templates/accounts/register.html:19 | intended: the step 6c target wording |
| `Choose a username and a password. You do not need an email address.` | accounts/templates/accounts/register.html:5 | intended: the step 6c target wording |
| `A user with that email address already exists.` | accounts/models.py:12 | model-level message; the register form has no email field, so only the admin can reach it |
| `email address` | accounts/models.py:46 | model-level message; the register form has no email field, so only the admin can reach it |

- No leftover mention of email confirmation or reset by email in any text a participant can see (the How this works page no longer mentions email).
- (manual) The register page's own wording about email is the fixed target text (docs/step6c_brief.md:18).
- (manual) `config/settings.py` still has a mail backend; the brief says it stays for the circuit-breaker alert, which no participant sees.


### Counts

Strings captured in this file: 837 table rows, of which 356 are distinct.

