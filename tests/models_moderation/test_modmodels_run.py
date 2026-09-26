"""ModerationRun (brief 4b): fields, defaults, choices, model validation in save(), constraints (model and database),
posted-message rules, replays, the unique live run per trigger, llm_calls()."""
import modmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

pytestmark = pytest.mark.django_db

RUN_FIELDS = [
    "conversation", "trigger_message", "snapshot_seq", "kind", "replay_of", "replicate", "status", "attempts", "is_stale",
    "decision", "rationale", "posted_message", "failure_reason", "error", "config_snapshot", "discussion_map", "claimed_at",
    "started_at", "finished_at", "created_at",
]  # fmt: skip


def choice_values(model, name):
    return [value for value, _label in model._meta.get_field(name).choices or []]


@pytest.fixture
def world():
    """A conversation: seq 1 user, seq 2 moderator (reply to 1), seq 3 user."""
    conv, parts, first, reply, second = kit.scenario()
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, second=second))


# --- shape ---------------------------------------------------------------------------------------------------------
def test_run_has_every_contract_field():
    from moderation.models import ModerationRun

    names = {f.name for f in ModerationRun._meta.get_fields()}
    assert [n for n in RUN_FIELDS if n not in names] == []


def test_every_relation_on_the_run_is_protect_and_the_right_kind():
    from forum.models import Conversation, Message
    from moderation.models import ModerationRun

    meta = ModerationRun._meta
    for name, target in [("conversation", Conversation), ("trigger_message", Message), ("replay_of", ModerationRun), ("posted_message", Message)]:
        field = meta.get_field(name)
        assert field.remote_field.on_delete is models.PROTECT, name
        assert field.related_model is target, name
    assert isinstance(meta.get_field("posted_message"), models.OneToOneField)
    assert not isinstance(meta.get_field("trigger_message"), models.OneToOneField)
    assert meta.get_field("trigger_message").null is False
    assert meta.get_field("conversation").null is False


def test_nullable_fields():
    from moderation.models import ModerationRun

    meta = ModerationRun._meta
    for name in ("replay_of", "posted_message", "claimed_at", "started_at", "finished_at"):
        assert meta.get_field(name).null is True, name
    for name in ("trigger_message", "snapshot_seq", "kind", "replicate", "status", "attempts", "is_stale"):
        assert meta.get_field(name).null is False, name


def test_choices_come_from_the_contract():
    from moderation import taxonomy
    from moderation.models import ModerationRun

    assert set(choice_values(ModerationRun, "kind")) == {"live", "replay"}
    assert set(choice_values(ModerationRun, "status")) == {"pending", "running", "done", "failed", "skipped_budget", "skipped_disabled"}
    assert set(choice_values(ModerationRun, "decision")) == {"", *taxonomy.DECISIONS}
    assert set(taxonomy.DECISIONS) == {"intervene", "no_intervention"}


def test_defaults(world):
    run = kit.make_run(world.first)
    run.refresh_from_db()
    assert run.status == "pending"
    assert run.attempts == 0
    assert run.replicate == 1
    assert run.is_stale is False
    assert run.decision == ""
    assert run.rationale == ""
    assert run.failure_reason == ""
    assert run.error == ""
    assert run.config_snapshot == {}
    assert run.discussion_map == {}
    assert run.posted_message is None
    assert run.replay_of is None
    assert run.claimed_at is None and run.started_at is None and run.finished_at is None
    assert run.created_at is not None
    assert run.kind == "live"
    assert run.snapshot_seq == world.first.seq_no


def test_defaults_are_not_shared_between_runs(world):
    a = kit.make_run(world.first)
    b = kit.make_run(world.second)
    a.config_snapshot["x"] = 1
    a.discussion_map["y"] = 1
    assert b.config_snapshot == {} and b.discussion_map == {}


@pytest.mark.parametrize("name,bad", [("kind", "bogus"), ("status", "bogus"), ("decision", "bogus")])
def test_choice_fields_reject_unknown_values_in_full_clean(world, name, bad):
    run = kit.make_run(world.first)
    setattr(run, name, bad)
    with pytest.raises(ValidationError) as caught:
        run.full_clean()
    assert name in caught.value.message_dict


@pytest.mark.parametrize("status", ["pending", "running", "done", "failed", "skipped_budget", "skipped_disabled"])
def test_every_status_can_be_stored(world, status):
    run = kit.make_run(world.first, status=status)
    run.refresh_from_db()
    assert run.status == status


def test_a_run_stores_its_work_fields(world):
    run = kit.make_run(
        world.first, status="done", decision="intervene", rationale="why", failure_reason="", error="",
        config_snapshot={"model": "x", "temps": [0, 1]}, discussion_map={"agreements": ["a"]}, attempts=2, replicate=3, is_stale=True,
    )  # fmt: skip
    run.refresh_from_db()
    assert run.decision == "intervene" and run.rationale == "why"
    assert run.config_snapshot == {"model": "x", "temps": [0, 1]}
    assert run.discussion_map == {"agreements": ["a"]}
    assert (run.attempts, run.replicate, run.is_stale) == (2, 3, True)


def test_failed_run_keeps_reason_and_error(world):
    run = kit.make_run(world.first, status="failed", failure_reason="invalid_trigger", error="boom")
    run.refresh_from_db()
    assert (run.failure_reason, run.error) == ("invalid_trigger", "boom")


# --- model validation ------------------------------------------------------------------------------------------------
def test_a_run_on_a_user_message_works_and_updates_cleanly(world):
    run = kit.make_run(world.first)
    run.status = "running"
    run.attempts = 1
    run.save()  # an ordinary update must not trip the validation
    run.refresh_from_db()
    assert (run.status, run.attempts) == ("running", 1)


def test_moderator_trigger_is_refused_by_model_validation_and_nothing_is_stored(world):
    from moderation.models import ModerationRun

    with pytest.raises(ValidationError):
        kit.make_run(world.reply)
    assert ModerationRun.objects.count() == 0


def test_moderator_trigger_is_refused_for_replays_too(world):
    from moderation.models import ModerationRun

    live = kit.make_run(world.first)
    with pytest.raises(ValidationError):
        kit.make_run(world.reply, kind="replay", replay_of=live)
    assert ModerationRun.objects.count() == 1


def test_changing_the_trigger_of_a_saved_run_to_a_moderator_message_is_refused(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first)
    run.trigger_message = world.reply
    run.snapshot_seq = world.reply.seq_no
    with pytest.raises(ValidationError):
        run.save()
    assert ModerationRun.objects.get(pk=run.pk).trigger_message_id == world.first.pk


def test_trigger_must_belong_to_the_runs_conversation(world):
    other_conv, other_parts = kit.make_conversation()
    foreign = kit.user_msg(other_conv, other_parts)
    with pytest.raises(ValidationError):
        kit.make_run(foreign, conversation=world.conv, snapshot_seq=foreign.seq_no)


def test_snapshot_seq_must_equal_the_triggers_seq(world):
    from moderation.models import ModerationRun

    for bad in (world.first.seq_no - 1, world.first.seq_no + 1, 99):
        with pytest.raises(ValidationError):
            kit.make_run(world.first, snapshot_seq=bad)
    assert ModerationRun.objects.count() == 0
    assert kit.make_run(world.first, snapshot_seq=world.first.seq_no).snapshot_seq == 1
    assert kit.make_run(world.second).snapshot_seq == 3


def test_snapshot_seq_must_stay_in_step_when_a_saved_run_is_edited(world):
    run = kit.make_run(world.second)
    run.snapshot_seq = 1
    with pytest.raises(ValidationError):
        run.save()


def test_a_live_run_has_no_replay_of(world):
    live = kit.make_run(world.first)
    with pytest.raises(ValidationError):
        kit.make_run(world.second, kind="live", replay_of=live)


def test_a_replay_points_at_its_original_and_may_be_saved(world):
    live = kit.make_run(world.first)
    replay = kit.make_run(world.first, kind="replay", replay_of=live, replicate=1)
    replay.refresh_from_db()
    assert replay.kind == "replay" and replay.replay_of_id == live.pk


# --- posted message ---------------------------------------------------------------------------------------------------
def test_a_live_run_may_post_a_moderator_reply_to_its_trigger(world):
    run = kit.make_run(world.first)
    run.posted_message = world.reply
    run.save()
    run.refresh_from_db()
    assert run.posted_message_id == world.reply.pk
    from moderation.models import ModerationRun

    assert ModerationRun.objects.get(posted_message=world.reply) == run


def test_posted_message_can_be_set_at_creation(world):
    run = kit.make_run(world.first, posted_message=world.reply)
    assert run.posted_message_id == world.reply.pk


def test_posted_message_must_be_a_moderator_message(world):
    run = kit.make_run(world.first)
    run.posted_message = world.second  # a user message
    with pytest.raises(ValidationError):
        run.save()
    run.refresh_from_db()
    assert run.posted_message is None


def test_posted_message_must_be_in_the_same_conversation(world):
    other_conv, other_parts = kit.make_conversation()
    other_first = kit.user_msg(other_conv, other_parts)
    foreign_reply = kit.mod_msg(other_conv, in_reply_to=other_first)
    run = kit.make_run(world.first)
    run.posted_message = foreign_reply
    with pytest.raises(ValidationError):
        run.save()


def test_posted_message_must_be_in_the_same_conversation_even_when_it_claims_to_reply_to_the_trigger(world):
    """Only the conversation check can catch this one: bulk_create skips forum's own in_reply_to rules."""
    from forum.models import Message

    other_conv, _ = kit.make_conversation()
    sneaky = Message(conversation=other_conv, seq_no=1, author_type="moderator", participant=None, in_reply_to=world.first,
                     content="sneaky", char_count=6)  # fmt: skip
    Message.objects.bulk_create([sneaky])
    sneaky = Message.objects.get(conversation=other_conv)
    run = kit.make_run(world.first)
    run.posted_message = sneaky
    with pytest.raises(ValidationError):
        run.save()


def test_posted_message_must_reply_to_the_trigger(world):
    stray = kit.mod_msg(world.conv, in_reply_to=None)
    replying_elsewhere = kit.mod_msg(world.conv, in_reply_to=world.second)
    run = kit.make_run(world.first)
    for candidate in (stray, replying_elsewhere):
        run.posted_message = candidate
        with pytest.raises(ValidationError):
            run.save()
    run.refresh_from_db()
    assert run.posted_message is None


def test_one_moderator_message_cannot_be_posted_by_two_runs(world):
    kit.make_run(world.first, posted_message=world.reply)
    live_two = kit.make_run(world.second)
    live_two.posted_message = world.reply
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        live_two.save()


def test_posted_message_is_unique_in_the_database(world):
    a = kit.unsaved_run(world.first, posted_message=world.reply)
    b = kit.unsaved_run(world.second, posted_message=world.reply)
    from moderation.models import ModerationRun

    ModerationRun.objects.bulk_create([a])
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([b])


# --- replays never post -----------------------------------------------------------------------------------------------
def test_a_replay_cannot_post_a_message(world):
    live = kit.make_run(world.first)
    with pytest.raises(ValidationError):
        kit.make_run(world.first, kind="replay", replay_of=live, posted_message=world.reply)


def test_a_saved_replay_cannot_be_given_a_posted_message(world):
    live = kit.make_run(world.first)
    replay = kit.make_run(world.first, kind="replay", replay_of=live)
    replay.posted_message = world.reply
    with pytest.raises(ValidationError):
        replay.save()


def test_a_replay_cannot_post_even_when_the_database_is_reached_directly(world):
    """CheckConstraint: kind = replay implies posted_message is null (bulk_create skips save())."""
    from moderation.models import ModerationRun

    live = kit.make_run(world.first)
    bad = kit.unsaved_run(world.first, kind="replay", replay_of=live, posted_message=world.reply)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([bad])
    assert ModerationRun.objects.filter(kind="replay").count() == 0


def test_a_queryset_update_cannot_turn_a_posted_live_run_into_a_replay(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first, posted_message=world.reply)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.filter(pk=run.pk).update(kind="replay")


def test_a_queryset_update_cannot_give_a_replay_a_posted_message(world):
    from moderation.models import ModerationRun

    live = kit.make_run(world.first)
    replay = kit.make_run(world.first, kind="replay", replay_of=live)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.filter(pk=replay.pk).update(posted_message=world.reply)


# --- unique live run per trigger; replays repeat ---------------------------------------------------------------------
def test_only_one_live_run_per_trigger_message_at_model_level(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first)
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_run(world.first)
    assert ModerationRun.objects.filter(trigger_message=world.first, kind="live").count() == 1


def test_only_one_live_run_per_trigger_message_in_the_database(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([kit.unsaved_run(world.first)])


def test_live_runs_on_different_triggers_are_fine(world):
    kit.make_run(world.first)
    kit.make_run(world.second)


def test_replays_of_one_trigger_may_repeat_alongside_the_live_run(world):
    from moderation.models import ModerationRun

    live = kit.make_run(world.first)
    for replicate in (1, 2, 3):
        kit.make_run(world.first, kind="replay", replay_of=live, replicate=replicate)
    kit.make_run(world.first, kind="replay", replay_of=live, replicate=1)  # even the same replicate again
    assert ModerationRun.objects.filter(trigger_message=world.first).count() == 5
    assert ModerationRun.objects.filter(trigger_message=world.first, kind="replay").count() == 4


def test_a_replay_does_not_occupy_the_live_slot_of_its_trigger(world):
    """The unique condition is on live runs only: a replay of a trigger leaves room for that trigger's live run."""
    original = kit.make_run(world.second)
    kit.make_run(world.first, kind="replay", replay_of=original)
    kit.make_run(world.first)  # the live slot on `first` is still free


# --- numeric constraints ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,bad", [("attempts", -1), ("replicate", 0), ("replicate", -3)])
def test_numeric_checks_in_the_database(world, name, bad):
    from moderation.models import ModerationRun

    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([kit.unsaved_run(world.first, **{name: bad})])


@pytest.mark.parametrize("name,bad", [("attempts", -1), ("replicate", 0)])
def test_numeric_checks_at_save(world, name, bad):
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        kit.make_run(world.first, **{name: bad})


@pytest.mark.parametrize("name,ok", [("attempts", 0), ("attempts", 7), ("replicate", 1), ("replicate", 9)])
def test_numeric_boundaries_accepted(world, name, ok):
    run = kit.make_run(world.first, **{name: ok})
    assert getattr(run, name) == ok


# --- llm_calls() --------------------------------------------------------------------------------------------------------
def test_llm_calls_returns_only_this_runs_ledger_rows(world):
    from moderation.models import LLMCall

    run = kit.make_run(world.first)
    other = kit.make_run(world.second)
    mine = [LLMCall.objects.create(purpose="moderation", model="m", max_tokens=10, run_id=run.pk) for _ in range(2)]
    LLMCall.objects.create(purpose="moderation", model="m", max_tokens=10, run_id=other.pk)
    LLMCall.objects.create(purpose="moderation", model="m", max_tokens=10, run_id=None)
    result = run.llm_calls()
    assert isinstance(result, models.QuerySet)
    assert result.model is LLMCall
    assert sorted(c.pk for c in result) == sorted(c.pk for c in mine)
    assert run.llm_calls().filter(purpose="spike").count() == 0  # chainable


def test_llm_calls_is_empty_for_a_run_without_calls(world):
    assert kit.make_run(world.first).llm_calls().count() == 0
