"""The 30-second gap (measured from the same participant's previous message, on server time), several messages in a row,
and the absence of any turn-taking rule."""
import pytest

from fsvc_testkit import (
    assert_plain, counts, make_active_via_service, orm_moderator_message, rejection, svc,
)  # fmt: skip

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(clock):
    return make_active_via_service()


def say(user, world, text="another thought on this"):
    return svc().post_message(user, world.conv, text)


# --- the gap -------------------------------------------------------------------------------------------------------------


def test_a_participants_first_message_has_no_wait(world):
    assert say(world.u1, world)


def test_a_second_message_at_once_is_refused_for_the_whole_gap(world):
    say(world.u1, world)
    before = counts()
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.code == "too_fast"
    assert exc.retry_after == 30
    assert "Please wait 30 more seconds before posting again." in exc.message
    assert counts() == before


def test_the_message_matches_the_plan_wording(world, clock):
    say(world.u1, world)
    clock.advance(18)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.retry_after == 12
    assert exc.message.startswith("Please wait 12 more seconds before posting again.")
    assert "Messages are limited to one every 30 seconds" in exc.message
    assert "keep the discussion readable" in exc.message
    assert "cost" not in exc.message.lower()
    assert_plain(exc)


def test_retry_after_is_a_whole_number_of_seconds(world, clock):
    say(world.u1, world)
    clock.advance(17.3)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert isinstance(exc.retry_after, int) and not isinstance(exc.retry_after, bool)


@pytest.mark.parametrize("elapsed", [0, 0.4, 1, 10, 17.3, 29, 29.5, 29.999])
def test_waiting_the_stated_seconds_is_always_enough_and_never_far_too_long(world, clock, elapsed):
    """retry_after rounds the remaining time UP: after that many seconds the post is accepted, and the figure is
    never a whole second more than needed."""
    say(world.u1, world)
    clock.advance(elapsed)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    remaining = 30 - elapsed
    assert exc.retry_after >= remaining - 1e-9
    assert exc.retry_after < remaining + 1
    clock.advance(exc.retry_after)
    assert svc().post_message(world.u1, world.conv, "and another")


def test_exactly_the_gap_is_enough(world, clock):
    say(world.u1, world)
    clock.advance(30)
    assert say(world.u1, world, "exactly thirty seconds later")


def test_a_hair_under_the_gap_is_not(world, clock):
    say(world.u1, world)
    clock.advance(29.999999)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.code == "too_fast" and exc.retry_after == 1
    assert "Please wait 1 more second" in exc.message


def test_the_gap_and_its_number_come_from_settings(world, clock, settings):
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 45
    say(world.u1, world)
    clock.advance(10)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.retry_after == 35
    assert "35 more seconds" in exc.message
    assert "one every 45 seconds" in exc.message
    assert "30" not in exc.message
    clock.advance(35)
    assert say(world.u1, world)


def test_a_gap_of_zero_means_no_limit(world, settings):
    settings.MIN_SECONDS_BETWEEN_MESSAGES = 0
    for i in range(3):
        assert say(world.u1, world, f"quick message {i}")


# --- measured from the SAME participant's previous message ---------------------------------------------------------------


def test_the_other_persons_message_resets_nothing(world, clock):
    say(world.u1, world)  # t = 0
    clock.advance(5)
    say(world.u2, world, "a message from the other side")  # t = 5
    clock.advance(5)  # t = 10
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.retry_after == 20  # measured from t = 0, not from the other person's t = 5


def test_the_other_persons_message_does_not_extend_the_wait_after_the_gap(world, clock):
    say(world.u1, world)  # t = 0
    clock.advance(29)
    say(world.u2, world, "a message from the other side")  # t = 29
    clock.advance(1)  # t = 30
    assert say(world.u1, world, "thirty seconds after my own")


def test_the_other_person_may_answer_at_once_no_wait_from_someone_elses_message(world):
    say(world.u1, world)
    assert say(world.u2, world, "an immediate answer")


def test_a_refused_attempt_does_not_restart_the_clock(world, clock):
    say(world.u1, world)  # t = 0
    clock.advance(10)
    assert rejection(svc().post_message, world.u1, world.conv, "too soon").code == "too_fast"
    clock.advance(20)  # t = 30
    assert say(world.u1, world, "thirty seconds after the last accepted one")


def test_a_moderator_post_between_messages_resets_nothing(world, clock):
    first = say(world.u1, world)
    clock.advance(20)
    orm_moderator_message(world.conv, in_reply_to=first)
    clock.advance(10)
    assert say(world.u1, world)


def test_each_participant_has_their_own_clock(world, clock):
    say(world.u1, world)
    clock.advance(10)
    say(world.u2, world, "the other side speaks")
    clock.advance(10)  # u1 at 20 s, u2 at 10 s
    assert rejection(svc().post_message, world.u1, world.conv, "again").retry_after == 10
    assert rejection(svc().post_message, world.u2, world.conv, "again").retry_after == 20


def test_the_wait_is_measured_on_server_time_not_on_anything_the_caller_supplies(world, clock):
    """The stored created_at is the patched django.utils.timezone.now, and the gap is computed from it."""
    message = say(world.u1, world)
    assert message.created_at == clock()
    clock.advance(12)
    assert rejection(svc().post_message, world.u1, world.conv, "again").retry_after == 18


# --- several in a row, no turn-taking ------------------------------------------------------------------------------------


def test_one_participant_can_post_several_messages_in_a_row_without_a_reply(world, clock):
    seqs = []
    for i in range(4):
        seqs.append(say(world.u1, world, f"monologue part {i}").seq_no)
        clock.advance(31)
    assert seqs == [1, 2, 3, 4]


def test_the_other_participant_can_then_do_the_same(world, clock):
    for i in range(3):
        say(world.u1, world, f"first monologue {i}")
        clock.advance(31)
    for i in range(3):
        say(world.u2, world, f"second monologue {i}")
        clock.advance(31)
    assert svc().post_message(world.u1, world.conv, "back to the first").seq_no == 7


def test_a_reply_is_never_required_before_posting_again(world, clock):
    say(world.u2, world, "the second person speaks first")
    clock.advance(31)
    say(world.u2, world, "and again without waiting for anyone")
    clock.advance(31)
    say(world.u1, world, "now the first person")
    clock.advance(31)
    say(world.u1, world, "and again")
    from forum.models import Message

    assert Message.objects.filter(conversation=world.conv).count() == 4


@pytest.mark.parametrize("elapsed", [0.2, 5.6, 17.7, 25.1, 29.4])
def test_the_wait_always_rounds_up(world, clock, elapsed):
    import math

    say(world.u1, world)
    clock.advance(elapsed)
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert exc.retry_after == math.ceil(30 - elapsed)


def test_the_wait_never_exceeds_the_gap_even_if_the_server_clock_stepped_back(world, clock):
    say(world.u1, world)
    clock.advance(-100)  # the clock jumped backwards; the previous message now lies in the future
    exc = rejection(svc().post_message, world.u1, world.conv, "and another")
    assert 1 <= exc.retry_after <= 30
