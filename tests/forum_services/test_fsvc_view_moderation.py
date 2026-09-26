"""conversation_view: the per-viewer heading of a moderator post, and the moderation notice."""
import pytest

from fsvc_testkit import (
    assert_no_identity, make_active, make_prop, orm_act, orm_moderator_message, orm_run, orm_user_message, views,
    walk_strings,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def reload(conv):
    from forum.models import Conversation

    return Conversation.objects.get(pk=conv.pk)


def view(user, conv):
    return views().conversation_view(user, reload(conv))


def moderator_post(result):
    posts = [m for m in result["messages"] if m["kind"] == "moderator"]
    assert len(posts) == 1, result["messages"]
    return posts[0]


def scene(labels):
    """Person ua (label labels[0]) wrote message 1, person ub (label labels[1]) wrote message 2, and the moderator's
    post (message 3) answers message 2. Returns (world, m1, m2, run)."""
    w = make_active(make_prop("Cats make better pets than dogs"), labels=labels)
    m1 = orm_user_message(w.conv, w.a, "first person here")
    m2 = orm_user_message(w.conv, w.b, "second person here")
    mod = orm_moderator_message(w.conv, in_reply_to=m2, content="Both of you may wish to define the term.")
    run = orm_run(w.conv, m2, status="done", posted_message=mod)
    return w, m1, m2, run


# (addressee, subject, index of the source message: 0 = message 1 by the FIRST person (label labels[0]), 1 = message 2)
# expected heading for the first person and for the second person. The act's addressee and subject are written with
# the actual letters, which depend on the labels, so cases are built from roles: "first", "second", "all", "both", "none".
CASES = [
    # addressee, subject, source, heading for first person, heading for second person
    ("first", "first", 0, "About your message 1", "About the other participant's message 1"),
    ("second", "first", 0, "About your message 1", "About the other participant's message 1"),
    ("first", "second", 1, "About the other participant's message 2", "About your message 2"),
    ("second", "second", 1, "About the other participant's message 2", "About your message 2"),
    ("all", "first", 0, "For both of you", "For both of you"),
    ("all", "second", 1, "For both of you", "For both of you"),
    ("all", "both", 1, "For both of you", "For both of you"),
    ("first", "both", 1, "For both of you", "For both of you"),
    ("second", "both", 0, "For both of you", "For both of you"),
    ("first", "none", 1, "About the conversation", "About the conversation"),
    ("second", "none", 0, "About the conversation", "About the conversation"),
]


@pytest.mark.parametrize("labels", [("A", "B"), ("B", "A")])
@pytest.mark.parametrize("addressee, subject, source, first_heading, second_heading", CASES)
def test_the_heading_is_computed_per_viewer(labels, addressee, subject, source, first_heading, second_heading):
    w, m1, m2, run = scene(labels)
    letters = {"first": labels[0], "second": labels[1]}
    resolve = lambda role: letters.get(role, role)  # noqa: E731  ("all", "both", "none" stay as they are)
    orm_act(run, resolve(addressee), resolve(subject), [(m1, m2)[source]])
    assert moderator_post(view(w.ua, w.conv))["heading"] == first_heading
    assert moderator_post(view(w.ub, w.conv))["heading"] == second_heading


def test_headings_use_no_label_letter_and_no_name():
    w, m1, m2, run = scene(("A", "B"))
    orm_act(run, "A", "B", [m2])
    for user in (w.ua, w.ub):
        assert_no_identity(view(user, w.conv), w.ua, w.ub)


def test_the_number_is_the_source_messages_seq_no_not_the_trigger_or_the_moderators():
    w = make_active(make_prop("Cats make better pets than dogs"))
    for i in range(4):
        orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message number {i + 1}")
    m5 = orm_user_message(w.conv, w.b, "the fifth message")
    mod = orm_moderator_message(w.conv, in_reply_to=m5)
    run = orm_run(w.conv, m5, status="done", posted_message=mod)
    from forum.models import Message

    third = Message.objects.get(conversation=w.conv, seq_no=3)
    orm_act(run, "all", "A", [third])  # subject A (w.ua) wrote message 3; addressee all -> both of you
    assert moderator_post(view(w.ua, w.conv))["heading"] == "For both of you"
    run.acts.all().delete()
    orm_act(run, "B", "A", [third])
    assert moderator_post(view(w.ua, w.conv))["heading"] == "About your message 3"
    assert moderator_post(view(w.ub, w.conv))["heading"] == "About the other participant's message 3"


def test_a_rejected_act_is_ignored_when_choosing_the_heading():
    """Reading: only acts that were actually posted (valid ones) decide the heading."""
    w, m1, m2, run = scene(("A", "B"))
    orm_act(run, "A", "B", [m2], order=1, validity="rejected")
    orm_act(run, "B", "none", [], order=2)
    # the valid act is (B, none); the rejected one would have said "About your message 2" for viewer B
    assert moderator_post(view(w.ub, w.conv))["heading"] == "About the conversation"
    assert moderator_post(view(w.ua, w.conv))["heading"] == "About the conversation"


def test_user_messages_carry_no_moderator_heading():
    w, m1, m2, run = scene(("A", "B"))
    orm_act(run, "all", "both", [m2])
    result = view(w.ua, w.conv)
    for message in result["messages"]:
        if message["kind"] != "moderator":
            assert not message.get("heading")


# --- the moderation notice -----------------------------------------------------------------------------------------------


def with_runs(w, *statuses, **extra_for_last):
    """One user message and one run per given status, oldest first. Returns the runs."""
    from datetime import timedelta

    from django.utils import timezone
    from moderation.models import ModerationRun

    made = []
    for i, status in enumerate(statuses):
        message = orm_user_message(w.conv, w.a if i % 2 == 0 else w.b, f"message for run {i}")
        extra = extra_for_last if i == len(statuses) - 1 else {}
        run = orm_run(w.conv, message, status=status, **extra)
        ModerationRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(minutes=len(statuses) - i))
        made.append(run)
    return made


@pytest.fixture
def w():
    return make_active(make_prop("Cats make better pets than dogs"))


def test_no_runs_no_notice(w):
    assert view(w.ua, w.conv)["moderation_notice"] is None


@pytest.mark.parametrize("status", ["pending", "running", "done"])
def test_a_healthy_latest_run_gives_no_notice(w, status):
    with_runs(w, status)
    assert view(w.ua, w.conv)["moderation_notice"] is None


def test_skipped_budget_says_the_moderator_has_reached_a_spending_limit(w):
    with_runs(w, "skipped_budget")
    for user in (w.ua, w.ub):
        notice = view(user, w.conv)["moderation_notice"]
        assert isinstance(notice, str)
        assert "AI moderator" in notice
        assert "limit" in notice
        assert "resume" in notice
        assert "still posted" in notice


def test_skipped_disabled_says_moderation_is_switched_off(w):
    with_runs(w, "skipped_disabled")
    notice = view(w.ua, w.conv)["moderation_notice"]
    assert "AI moderation is switched off right now" in notice
    assert "still posted" in notice


def test_failed_says_the_moderator_ran_into_a_problem(w):
    with_runs(w, "failed")
    notice = view(w.ua, w.conv)["moderation_notice"]
    assert "AI moderator ran into a problem" in notice
    assert "still posted" in notice


def test_the_notice_is_the_same_for_both_participants(w):
    with_runs(w, "failed")
    assert view(w.ua, w.conv)["moderation_notice"] == view(w.ub, w.conv)["moderation_notice"]


def test_a_run_still_in_flight_does_not_change_the_notice(w):
    """Ruling: pending and running runs are skipped; the notice follows the latest FINISHED live run, so it does not
    flicker while the newest message is being moderated."""
    with_runs(w, "failed", "pending")
    assert "ran into a problem" in view(w.ua, w.conv)["moderation_notice"]
    with_runs(w, "running")
    assert "ran into a problem" in view(w.ua, w.conv)["moderation_notice"]


def test_a_finished_run_after_in_flight_ones_takes_over(w):
    with_runs(w, "failed", "pending", "done")
    assert view(w.ua, w.conv)["moderation_notice"] is None


def test_replays_never_speak_to_the_users(w):
    """Only live runs decide the notice: a failed replay of an old message is research work, not the page's business."""
    from moderation.models import ModerationRun

    (live,) = with_runs(w, "done")
    ModerationRun.objects.create(
        conversation=w.conv, trigger_message=live.trigger_message, snapshot_seq=live.snapshot_seq, kind="replay",
        status="failed", replay_of=live,
    )  # fmt: skip
    assert view(w.ua, w.conv)["moderation_notice"] is None


def test_only_the_latest_run_counts_a_later_success_clears_the_notice(w):
    with_runs(w, "skipped_budget", "done")
    assert view(w.ua, w.conv)["moderation_notice"] is None


def test_a_later_problem_replaces_an_earlier_success(w):
    with_runs(w, "done", "failed")
    assert "ran into a problem" in view(w.ua, w.conv)["moderation_notice"]


def test_a_later_problem_replaces_an_earlier_different_problem(w):
    with_runs(w, "skipped_disabled", "failed")
    assert "ran into a problem" in view(w.ua, w.conv)["moderation_notice"]
    assert "switched off" not in view(w.ua, w.conv)["moderation_notice"]


def test_another_conversations_runs_are_not_this_conversations_notice(w):
    other = make_active(w.topic)
    with_runs(other, "failed")
    assert view(w.ua, w.conv)["moderation_notice"] is None


@pytest.mark.parametrize("status", ["failed", "skipped_budget", "skipped_disabled"])
def test_internal_error_text_never_reaches_the_page(w, status):
    with_runs(
        w, status, error="Traceback (most recent call last): SECRET_INTERNAL_TRACE api_key=sk-xyz",
        failure_reason="weird_internal_reason_123",
    )  # fmt: skip
    result = view(w.ua, w.conv)
    text = "\n".join(walk_strings(result))
    for internal in ("Traceback", "SECRET_INTERNAL_TRACE", "sk-xyz", "weird_internal_reason_123", "api_key"):
        assert internal not in text
    assert isinstance(result["moderation_notice"], str) and result["moderation_notice"]


def test_the_notice_does_not_stop_posting(w):
    with_runs(w, "skipped_budget")
    result = view(w.ua, w.conv)
    assert result["can_post"] is True and result["cannot_post_reason"] is None


def test_the_notice_never_names_a_label_or_a_person(w):
    with_runs(w, "failed")
    assert_no_identity(view(w.ua, w.conv), w.ua, w.ub)
