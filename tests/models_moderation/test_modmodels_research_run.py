"""ModerationRun's new "research" kind (docs/step20b_brief.md, item 1): "research" as a valid `kind`, the new
`source_act`/`requested_by` fields, the two-way `source_act` requirement (`kind == "research"` iff `source_act` is
set), the "one research run per source act" unique constraint, that it is keyed on `source_act` alone (not on
`trigger_message`), that it does not interact with the existing "one live run per trigger" constraint (a live and a
research run may freely share a trigger message), and that `posted_message`'s existing validation (moderator
message, same conversation, replies to the trigger) is unchanged for a research run.

Written from the brief's contract, not from any coding agent's diff. Group A (docs/step20b_brief.md's split) owns
moderation/models.py and the new migration; this file is the separate testing-agent file for that same slice, so it
never edits non-test code. Until Group A's change lands (KIND_CHOICES += "research", the new source_act/
requested_by fields, the two validate_rules additions, and the new UniqueConstraint), every test below is expected
to fail cleanly (TypeError on an unknown `source_act`/`requested_by` kwarg, AttributeError on the missing field, or
a plain assertion failure on the kind choices) -- expected, not a bug in this file.

Conventions follow tests/models_moderation/test_modmodels_run.py and modmodels_testkit.py: kit.make_run/unsaved_run
already forward arbitrary kwargs (kind, source_act, requested_by) straight to ModerationRun(...), so no change to
the shared testkit is needed for any of this.
"""
import modmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models as djmodels, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    """A conversation: seq 1 user (`first`, the claim), seq 2 moderator (`reply`, replying to it, already posted by
    a done live run `run`), seq 3 user (`second`, a trigger with no run of its own yet). `act` is the `offer_research`
    act (order 1 on `run`) that a research run would fulfill."""
    conv, parts, first, reply, second = kit.scenario()
    run = kit.make_run(first, posted_message=reply, status="done", decision="intervene")
    act = kit.make_act(run, order=1, act_type="offer_research", addressee="all", subject="none")
    return type("World", (), dict(conv=conv, parts=parts, first=first, reply=reply, second=second, run=run, act=act))


# --- "research" is a valid kind, and the new fields exist with the right shape --------------------------------------
def test_research_is_a_valid_kind_choice():
    from moderation.models import ModerationRun

    values = [v for v, _ in ModerationRun._meta.get_field("kind").choices or []]
    assert "research" in values
    assert {"live", "replay", "research"} <= set(values)


def test_source_act_and_requested_by_field_shape():
    from forum.models import Participant
    from moderation.models import InterventionAct, ModerationRun

    meta = ModerationRun._meta
    source_act = meta.get_field("source_act")
    requested_by = meta.get_field("requested_by")

    assert source_act.related_model is InterventionAct
    assert source_act.null is True and source_act.blank is True
    assert source_act.remote_field.on_delete is djmodels.PROTECT

    assert requested_by.related_model is Participant
    assert requested_by.null is True and requested_by.blank is True
    assert requested_by.remote_field.on_delete is djmodels.PROTECT


def test_a_research_run_can_be_created_and_stored(world):
    from moderation.models import ModerationRun

    run = kit.make_run(world.first, kind="research", source_act=world.act, requested_by=world.parts["A"])
    run.refresh_from_db()
    assert run.kind == "research"
    assert run.source_act_id == world.act.pk
    assert run.requested_by_id == world.parts["A"].pk
    assert ModerationRun.objects.filter(pk=run.pk, kind="research").exists()


def test_a_research_run_may_omit_requested_by(world):
    run = kit.make_run(world.first, kind="research", source_act=world.act, requested_by=None)
    run.refresh_from_db()
    assert run.requested_by_id is None


def test_source_act_related_name_reaches_back_from_the_act(world):
    run = kit.make_run(world.first, kind="research", source_act=world.act)
    assert list(world.act.research_runs.all()) == [run]


# --- source_act required for research, forbidden otherwise (validate_rules, both directions) ------------------------
def test_a_research_run_without_a_source_act_is_rejected(world):
    from moderation.models import ModerationRun

    with pytest.raises(ValidationError) as caught:
        kit.make_run(world.first, kind="research")
    assert "source_act" in caught.value.message_dict
    assert ModerationRun.objects.filter(kind="research").count() == 0


def test_a_research_run_with_source_act_explicitly_none_is_rejected(world):
    with pytest.raises(ValidationError) as caught:
        kit.make_run(world.first, kind="research", source_act=None)
    assert "source_act" in caught.value.message_dict


def test_a_live_run_with_a_source_act_is_rejected(world):
    with pytest.raises(ValidationError) as caught:
        kit.make_run(world.second, kind="live", source_act=world.act)
    assert "source_act" in caught.value.message_dict


def test_a_replay_run_with_a_source_act_is_rejected(world):
    with pytest.raises(ValidationError) as caught:
        kit.make_run(world.first, kind="replay", replay_of=world.run, source_act=world.act)
    assert "source_act" in caught.value.message_dict


def test_changing_a_saved_research_runs_kind_away_from_research_without_clearing_source_act_is_rejected(world):
    run = kit.make_run(world.first, kind="research", source_act=world.act)
    run.kind = "live"
    with pytest.raises(ValidationError):
        run.save()


# --- one research run per source_act: the unique constraint ----------------------------------------------------------
def test_two_research_runs_on_the_same_source_act_collide_via_ordinary_create(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first, kind="research", source_act=world.act, requested_by=world.parts["A"])
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_run(world.first, kind="research", source_act=world.act, requested_by=world.parts["B"])
    assert ModerationRun.objects.filter(source_act=world.act, kind="research").count() == 1


def test_two_research_runs_on_the_same_source_act_collide_in_the_database(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first, kind="research", source_act=world.act)
    dupe = kit.unsaved_run(world.first, kind="research", source_act=world.act)
    with pytest.raises(IntegrityError), transaction.atomic():
        ModerationRun.objects.bulk_create([dupe])


def test_the_unique_constraint_is_keyed_on_source_act_not_on_trigger_message(world):
    """A second research run for the same act still collides even when it names a different (valid) trigger."""
    kit.make_run(world.first, kind="research", source_act=world.act)
    with pytest.raises(IntegrityError), transaction.atomic():
        kit.make_run(world.second, kind="research", source_act=world.act)


def test_two_research_runs_on_different_source_acts_are_fine(world):
    from moderation.models import ModerationRun

    other_act = kit.make_act(world.run, order=2, act_type="offer_research", addressee="all", subject="none")
    kit.make_run(world.first, kind="research", source_act=world.act)
    kit.make_run(world.first, kind="research", source_act=other_act)
    assert ModerationRun.objects.filter(kind="research").count() == 2


# --- unaffected by / does not affect the existing "one live run per trigger" constraint ------------------------------
def test_a_live_and_a_research_run_may_share_the_same_trigger_message(world):
    from moderation.models import ModerationRun

    research = kit.make_run(world.first, kind="research", source_act=world.act)
    research.refresh_from_db()
    assert research.trigger_message_id == world.first.pk  # world.run (live) already shares this trigger
    assert ModerationRun.objects.filter(trigger_message=world.first).count() == 2


def test_the_live_per_trigger_constraint_still_fires_even_with_a_research_run_present(world):
    from moderation.models import ModerationRun

    kit.make_run(world.first, kind="research", source_act=world.act)
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        kit.make_run(world.first)  # a second live run on a trigger that already has one (world.run)
    assert ModerationRun.objects.filter(trigger_message=world.first, kind="live").count() == 1


def test_a_research_run_is_fine_on_a_trigger_with_no_live_run_at_all(world):
    run = kit.make_run(world.second, kind="research", source_act=world.act)
    run.refresh_from_db()
    assert run.trigger_message_id == world.second.pk


# --- posted_message validation is unchanged for a research run -------------------------------------------------------
def test_a_research_run_may_post_a_moderator_reply_to_its_trigger(world):
    research = kit.make_run(world.first, kind="research", source_act=world.act)
    note = kit.mod_msg(world.conv, in_reply_to=world.first, content="A sourced note.")
    research.posted_message = note
    research.save()
    research.refresh_from_db()
    assert research.posted_message_id == note.pk


def test_a_research_run_may_have_posted_message_set_at_creation(world):
    note = kit.mod_msg(world.conv, in_reply_to=world.first, content="A sourced note, set at creation.")
    research = kit.make_run(world.first, kind="research", source_act=world.act, posted_message=note)
    assert research.posted_message_id == note.pk


def test_a_research_runs_posted_message_must_be_a_moderator_message(world):
    research = kit.make_run(world.first, kind="research", source_act=world.act)
    research.posted_message = world.second  # a user message
    with pytest.raises(ValidationError):
        research.save()
    research.refresh_from_db()
    assert research.posted_message is None


def test_a_research_runs_posted_message_must_be_in_the_same_conversation(world):
    other_conv, other_parts = kit.make_conversation()
    other_first = kit.user_msg(other_conv, other_parts)
    foreign_reply = kit.mod_msg(other_conv, in_reply_to=other_first)
    research = kit.make_run(world.first, kind="research", source_act=world.act)
    research.posted_message = foreign_reply
    with pytest.raises(ValidationError):
        research.save()


def test_a_research_runs_posted_message_must_reply_to_the_trigger(world):
    stray = kit.mod_msg(world.conv, in_reply_to=None)
    replying_elsewhere = kit.mod_msg(world.conv, in_reply_to=world.second)
    research = kit.make_run(world.first, kind="research", source_act=world.act)
    for candidate in (stray, replying_elsewhere):
        research.posted_message = candidate
        with pytest.raises(ValidationError):
            research.save()
    research.refresh_from_db()
    assert research.posted_message is None


def test_a_research_run_cannot_take_a_posted_message_already_claimed_by_another_run(world):
    """OneToOneField uniqueness on posted_message is unaffected by kind (mirrors test_modmodels_run.py's live case).
    world.run (a live run) already owns world.reply, from the fixture."""
    from moderation.models import ModerationRun

    research = kit.make_run(world.second, kind="research", source_act=world.act)
    research.posted_message = world.reply
    with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
        research.save()
    assert ModerationRun.objects.get(posted_message=world.reply).kind == "live"
