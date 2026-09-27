"""Step 7c: Participant.side and Topic.opposing_position (forum migration 0003), at model level and at database level
(bulk_create and queryset update skip save(), so they show what the database itself refuses)."""
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

from forum_testkit import add_participant, make_conversation, make_topic, make_user, refused_by_database

pytestmark = pytest.mark.django_db


def participant(conv, label, order, side, user="auto"):
    from forum.models import Participant

    return Participant(
        conversation=conv, user=make_user() if user == "auto" else user, label=label, join_order=order, side=side
    )


# --- fields -----------------------------------------------------------------------------------------------------------------


def test_side_field_definition():
    from forum.models import Participant

    field = Participant._meta.get_field("side")
    assert isinstance(field, models.CharField)
    assert field.max_length == 3 and field.blank is True and field.default == ""
    assert [value for value, _ in field.choices] == ["pro", "con"]


def test_opposing_position_field_definition():
    from forum.models import Topic

    field = Topic._meta.get_field("opposing_position")
    assert isinstance(field, models.TextField)
    assert field.blank is True and field.default == ""


def test_defaults_are_blank_for_new_rows():
    conv = make_conversation()
    part = add_participant(conv, "A", 1)
    assert part.side == ""
    assert make_topic().opposing_position == ""


def test_an_opposing_position_can_be_stored_and_read_back():
    from forum.models import Topic

    topic = Topic.objects.create(title="t1", proposition="Rents should be capped", opposing_position="Rents should not be capped")
    assert Topic.objects.get(pk=topic.pk).opposing_position == "Rents should not be capped"


def test_the_opposing_position_may_be_left_blank_for_user_created_topics():
    from forum.models import Topic

    topic = Topic.objects.create(title="", proposition="A claim typed by a person")
    assert Topic.objects.get(pk=topic.pk).opposing_position == ""


def test_the_new_constraints_are_declared():
    from forum.models import Participant

    names = {c.name for c in Participant._meta.constraints}
    assert "forum_participant_side_unique_per_conversation" in names
    assert any("side" in n and n != "forum_participant_side_unique_per_conversation" for n in names)


def test_the_existing_participant_constraints_are_still_there():
    from forum.models import Participant

    names = {c.name for c in Participant._meta.constraints}
    assert {"forum_participant_label_unique", "forum_participant_join_order_unique", "forum_participant_user_unique",
            "forum_participant_label_a_to_z", "forum_participant_join_order_positive"} <= names  # fmt: skip


# --- model level ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["", "pro", "con"])
def test_save_accepts_a_blank_pro_or_con_side(side):
    conv = make_conversation()
    part = participant(conv, "A", 1, side)
    part.save()
    part.refresh_from_db()
    assert part.side == side


@pytest.mark.parametrize("side", ["PRO", "Con", "left", "both", " pro", "pro ", "none", "x", "prox"])
def test_save_refuses_any_other_side_with_a_friendly_error(side):
    conv = make_conversation()
    with pytest.raises(ValidationError) as excinfo, transaction.atomic():
        participant(conv, "A", 1, side).save()
    assert "side" in excinfo.value.message_dict


def test_an_update_through_save_to_a_bad_side_is_refused_too():
    conv = make_conversation()
    part = add_participant(conv, "A", 1)
    part.side = "middle"
    with pytest.raises(ValidationError), transaction.atomic():
        part.save()
    part.refresh_from_db()
    assert part.side == ""


def test_two_participants_of_a_conversation_cannot_hold_the_same_side():
    conv = make_conversation()
    participant(conv, "A", 1, "pro").save()
    with pytest.raises((ValidationError, IntegrityError)), transaction.atomic():
        participant(conv, "B", 2, "pro").save()


def test_opposite_sides_in_one_conversation_are_fine():
    conv = make_conversation()
    participant(conv, "A", 1, "pro").save()
    participant(conv, "B", 2, "con").save()
    assert conv.participants.count() == 2


def test_the_same_side_in_different_conversations_is_fine():
    for _ in range(3):
        conv = make_conversation()
        participant(conv, "A", 1, "pro").save()
        participant(conv, "B", 2, "con").save()


def test_two_blank_sides_in_one_conversation_are_fine_old_rows_and_synthetic_participants():
    conv = make_conversation()
    participant(conv, "A", 1, "").save()
    participant(conv, "B", 2, "").save()
    synthetic = make_conversation("synthetic")
    add_participant(synthetic, "A", 1)
    add_participant(synthetic, "B", 2)
    assert synthetic.participants.filter(side="").count() == 2


def test_a_blank_side_and_a_stated_side_can_share_a_conversation():
    conv = make_conversation()
    participant(conv, "A", 1, "").save()
    participant(conv, "B", 2, "con").save()
    assert conv.participants.count() == 2


# --- database level ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("side", ["left", "PRO", "pro ", "both", "x"])
def test_the_database_refuses_an_unknown_side_through_bulk_create(side):
    from forum.models import Participant

    conv = make_conversation()
    part = participant(conv, "A", 1, side)
    refused_by_database(lambda: Participant.objects.bulk_create([part]))


def test_the_database_refuses_an_unknown_side_through_a_queryset_update():
    from forum.models import Participant

    conv = make_conversation()
    part = add_participant(conv, "A", 1)
    refused_by_database(lambda: Participant.objects.filter(pk=part.pk).update(side="middle"))
    part.refresh_from_db()
    assert part.side == ""


def test_the_database_refuses_two_pros_in_one_conversation_through_bulk_create():
    from forum.models import Participant

    conv = make_conversation()
    a, b = participant(conv, "A", 1, "pro"), participant(conv, "B", 2, "pro")
    refused_by_database(lambda: Participant.objects.bulk_create([a, b]))


def test_the_database_refuses_two_cons_in_one_conversation_through_a_queryset_update():
    from forum.models import Participant

    conv = make_conversation()
    a = add_participant(conv, "A", 1)
    b = add_participant(conv, "B", 2)
    Participant.objects.filter(pk=a.pk).update(side="con")
    refused_by_database(lambda: Participant.objects.filter(pk=b.pk).update(side="con"))


def test_the_database_accepts_pro_and_con_through_bulk_create_and_update():
    from forum.models import Participant

    conv = make_conversation()
    Participant.objects.bulk_create([participant(conv, "A", 1, "pro"), participant(conv, "B", 2, "con")])
    assert sorted(conv.participants.values_list("side", flat=True)) == ["con", "pro"]
    Participant.objects.filter(conversation=conv, side="pro").update(side="")
    assert sorted(conv.participants.values_list("side", flat=True)) == ["", "con"]


def test_the_database_accepts_many_blank_sides_in_one_conversation_through_bulk_create():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    Participant.objects.bulk_create(
        [participant(conv, "A", 1, "", user=None), participant(conv, "B", 2, "", user=None)]
    )
    assert conv.participants.count() == 2


def test_the_side_can_be_cleared_and_reused_by_the_other_person():
    from forum.models import Participant

    conv = make_conversation()
    a = participant(conv, "A", 1, "pro")
    a.save()
    b = participant(conv, "B", 2, "con")
    b.save()
    Participant.objects.filter(pk=a.pk).update(side="")
    Participant.objects.filter(pk=b.pk).update(side="pro")
    assert conv.participants.get(pk=b.pk).side == "pro"


def test_protect_and_cascade_behaviour_of_participants_is_unchanged():
    from django.db.models import ProtectedError

    conv = make_conversation()
    part = participant(conv, "A", 1, "pro")
    part.save()
    with pytest.raises(ProtectedError):
        conv.delete()
