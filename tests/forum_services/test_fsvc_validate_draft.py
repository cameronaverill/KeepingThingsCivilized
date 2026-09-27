"""Step 19 composer: services.validate_draft(user, conversation, text) checks a draft with exactly the rules, messages and
order of post_message, and creates nothing. post_message itself is unchanged (the rest of this folder pins that)."""
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser

from fsvc_testkit import (
    assert_plain, counts, enter, make_active, make_active_via_service, make_prop, make_user, make_waiting,
    orm_user_message, participant_of, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db

SAY = "a perfectly ordinary message here"


@pytest.fixture
def world(clock):
    return make_active_via_service()


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


def same_refusal(user, conv, text):
    """validate_draft and post_message refuse identically (code, message, retry_after, details), or both accept."""
    before = counts()
    try:
        svc().validate_draft(user, conv, text)
        draft = None
    except svc().PostRejected as exc:
        draft = exc
    assert counts() == before, "validate_draft created something"
    try:
        svc().post_message(user, conv, text)
        posted = None
    except svc().PostRejected as exc:
        posted = exc
    if draft is None or posted is None:
        assert draft is None and posted is None, (draft, posted)
        return None
    assert (draft.code, draft.message, draft.retry_after, draft.details) == (
        posted.code, posted.message, posted.retry_after, posted.details,
    )  # fmt: skip
    return draft


# --- the function exists and accepts a good draft ------------------------------------------------------------------------------


def test_validate_draft_exists_and_is_exported():
    import forum.services as services

    assert callable(services.validate_draft)
    assert "validate_draft" in getattr(services, "__all__", ["validate_draft"])


def test_a_good_draft_is_accepted_and_nothing_is_created(world):
    before = counts()
    svc().validate_draft(world.u1, world.conv, SAY)
    assert counts() == before


def test_a_good_draft_is_accepted_in_a_waiting_conversation_too():
    user = make_user()
    conv = enter(user, make_prop())
    before = counts()
    svc().validate_draft(user, conv, SAY)
    assert counts() == before


def test_validating_twice_or_many_times_changes_nothing(world):
    before = counts()
    for _ in range(5):
        svc().validate_draft(world.u1, world.conv, SAY)
    assert counts() == before
    assert svc().post_message(world.u1, world.conv, SAY).seq_no == 1


def test_a_check_is_not_a_message_the_next_post_is_not_too_fast(world):
    """The gap is measured from the participant's previous MESSAGE; a validated draft leaves no trace."""
    svc().validate_draft(world.u1, world.conv, SAY)
    svc().validate_draft(world.u1, world.conv, SAY)
    assert svc().post_message(world.u1, world.conv, SAY)


def test_a_check_right_after_a_post_reports_the_wait_and_does_not_extend_it(world, clock):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(10)
    exc = rejection(svc().validate_draft, world.u1, world.conv, "and another")
    assert exc.code == "too_fast" and exc.retry_after == 20
    exc = rejection(svc().validate_draft, world.u1, world.conv, "and another")
    assert exc.retry_after == 20  # asking again does not restart the clock
    clock.advance(20)
    assert svc().post_message(world.u1, world.conv, "and another")


def test_validate_draft_creates_no_run_and_never_calls_the_pipeline(world, settings, fake_pipeline):
    from moderation.models import ModerationRun

    settings.MODERATION_RUN_MODE = "sync"
    svc().validate_draft(world.u1, world.conv, SAY)
    assert ModerationRun.objects.count() == 0
    assert fake_pipeline.calls == []


def test_validate_draft_makes_no_model_call(world, monkeypatch):
    from moderation import llm

    def boom(*a, **k):
        raise AssertionError("validate_draft must not call the model")

    monkeypatch.setattr(llm, "call", boom, raising=False)
    svc().validate_draft(world.u1, world.conv, SAY)


# --- every refusal, identical to post_message ------------------------------------------------------------------------------------


def test_not_a_participant(world):
    exc = same_refusal(make_user(), world.conv, SAY)
    assert exc.code == "not_participant"
    assert "You are not a participant in this conversation." in exc.message


def test_a_participant_of_another_conversation_is_not_a_participant_here(world):
    other = make_active_via_service()
    assert same_refusal(other.u1, world.conv, SAY).code == "not_participant"


def test_an_anonymous_visitor_is_refused_like_a_post(world):
    exc = same_refusal(AnonymousUser(), world.conv, SAY)
    assert exc.code in ("login_required", "not_participant")


def test_closed_by_the_other_person_and_by_yourself(world):
    svc().end_conversation(world.u1, world.conv)
    mine = same_refusal(world.u1, reload(world.conv), SAY)
    assert mine.code == "closed" and "You ended this conversation." in mine.message


def test_closed_names_the_other_person_for_the_one_who_did_not_end_it(world):
    svc().end_conversation(world.u1, world.conv)
    theirs = same_refusal(world.u2, reload(world.conv), SAY)
    assert theirs.code == "closed" and "The other participant ended this conversation." in theirs.message


def test_closed_by_the_message_limit_says_so(world, clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 2
    svc().post_message(world.u1, world.conv, "one")
    clock.advance(5)
    svc().post_message(world.u2, world.conv, "two")
    clock.advance(60)
    exc = same_refusal(world.u1, reload(world.conv), SAY)
    assert exc.code == "closed" and "limit of 2 messages" in exc.message


def test_a_waiting_conversation_ended_by_its_creator_is_closed():
    user = make_user()
    conv = enter(user, make_prop())
    svc().end_conversation(user, conv)
    assert same_refusal(user, reload(conv), SAY).code == "closed"


@pytest.mark.parametrize("text", ["", " ", "\n\n", "\t \r\n \t", " ", None])
def test_empty(world, text):
    exc = same_refusal(world.u1, world.conv, text)
    assert exc.code == "empty" and "Your message is empty." in exc.message


def test_too_long_at_the_boundary(world):
    assert same_refusal(world.u1, world.conv, "a" * 3000) is None


def test_one_over_the_limit_is_too_long_with_the_plan_wording(world):
    exc = same_refusal(world.u1, world.conv, "a" * 3001)
    assert exc.code == "too_long"
    assert "Your message is 3,001 characters; the limit is 3,000." in exc.message
    assert "keep the discussion readable" in exc.message
    assert_plain(exc)


def test_the_plan_example_of_412_too_many(world):
    exc = same_refusal(world.u1, world.conv, "a" * 3412)
    assert "Please shorten it by 412 characters." in exc.message


@pytest.mark.parametrize(
    "text, ok",
    [
        ("a\r\n" * 1499 + "aa", True),  # 3,000 once CRLF counts as one
        ("a\r\n" * 1499 + "aaa", False),  # 3,001
        ("  \n" + "a" * 3000 + "\n  ", True),  # outer whitespace is not counted
        ("é" * 3000, True),  # composed before counting
        ("\U0001F600" * 3000, True),  # an emoji is one character
        ("\U0001F600" * 3001, False),
    ],
)
def test_the_counting_rule_is_the_one_post_message_uses(world, text, ok):
    exc = same_refusal(world.u1, world.conv, text)
    assert (exc is None) is ok


def test_the_limit_and_its_number_come_from_settings(world, settings):
    settings.MAX_MESSAGE_CHARS = 10
    exc = same_refusal(world.u1, world.conv, "x" * 25)
    assert "25 characters" in exc.message and "limit is 10" in exc.message and "shorten it by 15 characters" in exc.message
    assert "3,000" not in exc.message


def test_too_fast_with_the_plan_wording(world, clock):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(18)
    exc = rejection(svc().validate_draft, world.u1, world.conv, "and another")
    assert exc.code == "too_fast" and exc.retry_after == 12
    assert exc.message.startswith("Please wait 12 more seconds before posting again.")
    assert "Messages are limited to one every 30 seconds" in exc.message
    posted = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert (exc.code, exc.message, exc.retry_after) == (posted.code, posted.message, posted.retry_after)


@pytest.mark.parametrize("elapsed", [0, 0.4, 17.3, 29.5, 29.999999, 30, 30.0001, 45])
def test_the_gap_boundary_agrees_with_post_message(world, clock, elapsed):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(elapsed)
    same_refusal(world.u1, world.conv, "and another")


def test_the_gap_comes_from_settings(world, clock, settings):
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 45
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(10)
    exc = rejection(svc().validate_draft, world.u1, world.conv, "again")
    assert exc.retry_after == 35 and "one every 45 seconds" in exc.message


def test_the_gap_is_measured_from_the_same_participants_own_message(world, clock):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(5)
    svc().post_message(world.u2, world.conv, "the other side")
    clock.advance(5)
    assert rejection(svc().validate_draft, world.u1, world.conv, "again").retry_after == 20
    assert same_refusal(world.u2, world.conv, "again").retry_after == 25  # u2 spoke at t=5, now t=10


def test_the_conversation_full_refusal_in_an_active_conversation_that_already_holds_the_limit(clock):
    w = make_active()
    for i in range(30):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i}", when=clock() - timedelta(hours=1))
    exc = same_refusal(w.ua, w.conv, "one more for the road")
    assert exc.code == "conversation_full"
    assert "reached its limit of 30 messages" in exc.message and "start a new one" in exc.message


def test_the_thirtieth_message_would_be_accepted_and_the_thirty_first_would_not(clock, settings):
    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    w = make_active()
    for i in range(2):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message {i}", when=clock() - timedelta(hours=1))
    assert same_refusal(w.ua, w.conv, "the third") is None  # accepted by both; the post also closes the conversation
    assert reload(w.conv).status == "closed"


# --- order of the checks: the same as post_message ----------------------------------------------------------------------------------


def test_not_a_participant_before_closed_and_before_empty(world):
    svc().end_conversation(world.u1, world.conv)
    assert same_refusal(make_user(), reload(world.conv), "").code == "not_participant"


def test_closed_before_empty_and_before_too_long(world):
    svc().end_conversation(world.u1, world.conv)
    assert same_refusal(world.u2, reload(world.conv), "").code == "closed"
    assert same_refusal(world.u2, reload(world.conv), "a" * 5000).code == "closed"


def test_empty_before_too_fast(world, clock):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(1)
    assert same_refusal(world.u1, world.conv, "  ").code == "empty"


def test_too_long_before_too_fast(world, clock):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(1)
    assert same_refusal(world.u1, world.conv, "a" * 3001).code == "too_long"


def test_too_fast_before_conversation_full(clock):
    w = make_active()
    for i in range(29):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i}", when=clock() - timedelta(hours=1))
    orm_user_message(w.conv, w.a, "just now", when=clock())
    assert rejection(svc().validate_draft, w.ua, w.conv, "one more").code == "too_fast"
    assert rejection(svc().validate_draft, w.ub, w.conv, "one more").code == "conversation_full"
    assert same_refusal(w.ua, w.conv, "one more").code == "too_fast"


# --- state is read fresh, and nothing leaks --------------------------------------------------------------------------------------------


def test_a_stale_conversation_object_does_not_fool_it(world):
    stale = world.conv
    svc().end_conversation(world.u2, reload(world.conv))
    assert stale.status == "active"
    assert rejection(svc().validate_draft, world.u1, stale, SAY).code == "closed"


def test_the_refusal_message_holds_no_internal_text_and_no_identity(world):
    for user, text in ((make_user(), SAY), (world.u1, ""), (world.u1, "a" * 4000)):
        exc = rejection(svc().validate_draft, user, world.conv, text)
        assert_plain(exc)
        assert world.u1.username not in exc.message and world.u2.username not in exc.message


def test_a_refused_draft_leaves_the_text_untouched_nothing_is_stored(world):
    before = counts()
    rejection(svc().validate_draft, world.u1, world.conv, "a" * 4000)
    assert counts() == before


def test_a_blocked_pair_sees_the_closed_refusal(world):
    svc().block_user(world.u1, world.u2)
    assert same_refusal(world.u2, reload(world.conv), SAY).code == "closed"


def test_the_two_functions_agree_across_a_random_walk_of_states(clock):
    """A short scripted walk through states; at every step both functions give the same answer."""
    w = make_active_via_service()
    texts = ["", SAY, "a" * 3001, "  ", "short", "b" * 3000]
    who = [w.u1, w.u2, w.u1, w.u1, w.u2, w.u2, w.u1]
    for i, user in enumerate(who):
        for text in texts:
            before = counts()
            try:
                svc().validate_draft(user, reload(w.conv), text)
                verdict = None
            except svc().PostRejected as exc:
                verdict = (exc.code, exc.message, exc.retry_after)
            assert counts() == before
            # what a post would say right now, checked on a savepoint so that the walk itself does not move on
            from django.db import transaction

            try:
                with transaction.atomic():
                    svc().post_message(user, reload(w.conv), text)
                    raise _Rollback
            except _Rollback:
                actual = None
            except svc().PostRejected as exc:
                actual = (exc.code, exc.message, exc.retry_after)
            assert verdict == actual, (i, text[:10], verdict, actual)
        clock.advance(31 if i % 2 == 0 else 3)
        try:
            svc().post_message(user, reload(w.conv), f"walk message {i}")
        except svc().PostRejected:
            pass


class _Rollback(Exception):
    pass


# --- post_message is built on validate_draft (one source of rules) -------------------------------------------------------------------------


def test_post_message_uses_validate_draft_as_its_one_source_of_rules(world, monkeypatch):
    import forum.services as services

    def refuse(*args, **kwargs):
        raise services.PostRejected("stub_code", "A stubbed refusal.")

    monkeypatch.setattr(services, "validate_draft", refuse)
    before = counts()
    exc = rejection(services.post_message, world.u1, world.conv, SAY)
    assert exc.code == "stub_code"
    assert counts() == before


# --- the tunable -------------------------------------------------------------------------------------------------------------------------


def test_the_client_timeout_tunable_defaults_to_25_seconds(settings):
    assert settings.PREVIEW_CLIENT_TIMEOUT_SECONDS == 25
    assert isinstance(settings.PREVIEW_CLIENT_TIMEOUT_SECONDS, int)


def test_the_tunable_lives_in_tunables_with_a_comment_above_it():
    from pathlib import Path

    import config.tunables as tunables

    assert tunables.PREVIEW_CLIENT_TIMEOUT_SECONDS == 25
    lines = Path(tunables.__file__).read_text().splitlines()
    index = next(i for i, l in enumerate(lines) if l.startswith("PREVIEW_CLIENT_TIMEOUT_SECONDS"))
    assert lines[index - 1].lstrip().startswith("#"), "the tunable needs a one-line comment above it"


def test_the_tunable_is_positive_and_smaller_than_the_moderator_run_timeout(settings):
    assert 0 < settings.PREVIEW_CLIENT_TIMEOUT_SECONDS <= settings.RUN_TIMEOUT_SECONDS


def test_the_tunable_follows_an_override(settings):
    settings.PREVIEW_CLIENT_TIMEOUT_SECONDS = 7
    from django.conf import settings as live

    assert live.PREVIEW_CLIENT_TIMEOUT_SECONDS == 7


def test_the_tunable_carries_no_cost_wording_in_its_comment():
    from pathlib import Path

    import config.tunables as tunables

    lines = Path(tunables.__file__).read_text().splitlines()
    index = next(i for i, l in enumerate(lines) if l.startswith("PREVIEW_CLIENT_TIMEOUT_SECONDS"))
    comment = lines[index - 1].lower()
    for word in ("cost", "budget", "spend", "token", "api"):
        assert word not in comment.split() and f" {word} " not in f" {comment} "


# --- added after the mutation pass ---------------------------------------------------------------------------------------------------------


def snapshot():
    """Every stored value of every forum and moderation row a draft check could conceivably touch."""
    from forum.models import Block, Conversation, Message, Participant, Topic
    from moderation.models import LLMCall, ModerationRun

    return {
        model.__name__: list(model.objects.order_by("pk").values())
        for model in (Topic, Conversation, Participant, Message, Block, ModerationRun, LLMCall)
    }


@pytest.mark.parametrize("who", ["u1", "u2"])
def test_a_check_changes_no_stored_value_in_any_table(world, clock, who):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(31)
    before = snapshot()
    user = getattr(world, who)
    for text in (SAY, "", "a" * 4000, "  "):
        try:
            svc().validate_draft(user, world.conv, text)
        except svc().PostRejected:
            pass
    assert snapshot() == before


def test_a_check_on_a_waiting_or_closed_conversation_changes_no_stored_value():
    lone = make_user()
    waiting = enter(lone, make_prop())
    closed = enter(lone, make_prop())
    svc().end_conversation(lone, closed)
    before = snapshot()
    for conv in (waiting, reload(closed)):
        try:
            svc().validate_draft(lone, conv, SAY)
        except svc().PostRejected:
            pass
    assert snapshot() == before


@pytest.mark.parametrize(
    "elapsed, expected",
    [(0, 30), (0.4, 30), (17.3, 13), (17.7, 13), (25.1, 5), (29.4, 1), (29.999999, 1)],
)
def test_the_wait_is_stated_in_whole_seconds_rounded_up(world, clock, elapsed, expected):
    svc().post_message(world.u1, world.conv, SAY)
    clock.advance(elapsed)
    exc = rejection(svc().validate_draft, world.u1, world.conv, "and another")
    assert exc.retry_after == expected
    assert isinstance(exc.retry_after, int)


def test_moderator_messages_do_not_count_toward_the_limit_when_checking_a_draft(clock, settings):
    from fsvc_testkit import orm_moderator_message

    settings.MAX_USER_MESSAGES_PER_CONVERSATION = 3
    w = make_active()
    for i in range(2):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message {i}", when=clock() - timedelta(hours=1))
    for _ in range(5):
        orm_moderator_message(w.conv)
    svc().validate_draft(w.ua, w.conv, "the third and last")  # accepted: only two user messages so far


def test_a_draft_over_the_limit_uses_the_normalised_text_the_stored_message_would_have(world):
    """CRLF pairs and outer whitespace do not count, so a draft that a post would store at 3,000 is accepted."""
    svc().validate_draft(world.u1, world.conv, "  \r\n" + "a\r\n" * 1499 + "aa" + "\r\n  ")
    message = svc().post_message(world.u1, world.conv, "  \r\n" + "a\r\n" * 1499 + "aa" + "\r\n  ")
    assert message.char_count == 3000
