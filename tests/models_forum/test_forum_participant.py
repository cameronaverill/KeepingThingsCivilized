"""Participant: fields, label and join_order rules, uniqueness (model and database), human/synthetic rules,
MAX_PARTICIPANTS (brief 4a)."""
import pytest
from django.core.exceptions import ValidationError
from django.db import models

from forum_testkit import (
    add_participant, invalid, make_conversation, make_user, refused, refused_by_database,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def participant(conv, label, join_order, user=None):
    from forum.models import Participant

    return Participant(conversation=conv, user=user, label=label, join_order=join_order)


# --- fields --------------------------------------------------------------------------------------------------------


def test_participant_has_every_contract_field():
    from forum.models import Participant

    names = {f.name for f in Participant._meta.get_fields()}
    assert {"conversation", "user", "label", "join_order", "joined_at"} <= names


def test_joined_at_is_set_automatically_and_user_is_optional():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    p = add_participant(conv, "A", 1)
    assert p.joined_at is not None
    assert p.user is None
    assert Participant.objects.get(pk=p.pk).user_id is None


def test_a_human_participant_stores_the_user():
    conv = make_conversation("human")
    user = make_user()
    p = add_participant(conv, "A", 1, user=user)
    p.refresh_from_db()
    assert p.user_id == user.pk


@pytest.mark.parametrize("label", list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
def test_every_uppercase_letter_is_a_valid_label(label):
    conv = make_conversation("synthetic")
    participant(conv, label, 1).full_clean()


@pytest.mark.parametrize("label", ["a", "z", "", "AB", "1", " ", "A ", "\u00c9", "\u0391", "-", "_", "AA"])
def test_anything_but_one_uppercase_ascii_letter_is_an_invalid_label(label):
    conv = make_conversation("synthetic")
    with pytest.raises(ValidationError) as excinfo:
        participant(conv, label, 1).full_clean()
    assert "label" in excinfo.value.message_dict


def test_join_order_zero_and_negative_are_refused_somewhere():
    """join_order is a positive integer: full_clean or save must refuse 0 and negatives."""
    conv = make_conversation("synthetic")
    for bad in (0, -1):
        p = participant(conv, "A", bad)
        try:
            p.full_clean()
        except ValidationError:
            continue
        with refused():
            p.save()


def test_join_order_one_is_valid():
    conv = make_conversation("synthetic")
    participant(conv, "A", 1).full_clean()


# --- uniqueness: model level (full_clean) ------------------------------------------------------------------------


def test_a_label_cannot_repeat_in_a_conversation_model_level():
    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with pytest.raises(ValidationError):
        participant(conv, "A", 2).full_clean()


def test_a_join_order_cannot_repeat_in_a_conversation_model_level():
    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with pytest.raises(ValidationError):
        participant(conv, "B", 1).full_clean()


def test_a_user_cannot_join_the_same_conversation_twice_model_level():
    conv = make_conversation("human")
    user = make_user()
    add_participant(conv, "A", 1, user=user)
    with pytest.raises(ValidationError):
        participant(conv, "B", 2, user=user).full_clean()


# --- uniqueness: on save -------------------------------------------------------------------------------------------


def test_saving_a_repeated_label_is_refused():
    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with refused():
        add_participant(conv, "A", 2)


def test_saving_a_repeated_join_order_is_refused():
    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with refused():
        add_participant(conv, "B", 1)


def test_saving_a_user_twice_in_one_conversation_is_refused():
    conv = make_conversation("human")
    user = make_user()
    add_participant(conv, "A", 1, user=user)
    with refused():
        add_participant(conv, "B", 2, user=user)


# --- uniqueness: database level (bypass save with bulk_create and update) -----------------------------------------


def test_the_database_refuses_a_repeated_label():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    refused_by_database(lambda: Participant.objects.bulk_create([participant(conv, "A", 2)]))


def test_the_database_refuses_a_repeated_join_order():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    refused_by_database(lambda: Participant.objects.bulk_create([participant(conv, "B", 1)]))


def test_the_database_refuses_the_same_user_twice_in_one_conversation():
    from forum.models import Participant

    conv = make_conversation("human")
    user = make_user()
    add_participant(conv, "A", 1, user=user)
    refused_by_database(lambda: Participant.objects.bulk_create([participant(conv, "B", 2, user=user)]))


def test_the_database_refuses_duplicates_created_by_queryset_update():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    b = add_participant(conv, "B", 2)
    refused_by_database(lambda: Participant.objects.filter(pk=b.pk).update(label="A"))
    refused_by_database(lambda: Participant.objects.filter(pk=b.pk).update(join_order=1))


def test_the_database_refuses_a_user_repeat_created_by_queryset_update():
    from forum.models import Participant

    conv = make_conversation("human")
    user = make_user()
    add_participant(conv, "A", 1, user=user)
    b = add_participant(conv, "B", 2)
    refused_by_database(lambda: Participant.objects.filter(pk=b.pk).update(user=user))


def test_several_participants_without_a_user_can_share_a_conversation():
    """The user uniqueness applies only when a user is set: synthetic participants have none."""
    from forum.models import Participant

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    add_participant(conv, "B", 2)
    Participant.objects.bulk_create([participant(conv, "C", 3), participant(conv, "D", 4)])
    assert Participant.objects.filter(conversation=conv, user__isnull=True).count() == 4


def test_the_same_label_join_order_and_user_may_appear_in_different_conversations():
    user = make_user()
    for _ in range(2):
        conv = make_conversation("human")
        p = add_participant(conv, "A", 1, user=user)
        p.full_clean()


def test_resaving_a_participant_does_not_collide_with_itself():
    conv = make_conversation("human")
    p = add_participant(conv, "A", 1)
    p.save()
    p.full_clean()


# --- human / synthetic rules -------------------------------------------------------------------------------------


def test_a_participant_of_a_human_conversation_must_have_a_user():
    conv = make_conversation("human")
    with invalid():
        add_participant(conv, "A", 1, user=None)


def test_a_participant_of_a_synthetic_conversation_must_not_have_a_user():
    conv = make_conversation("synthetic")
    with invalid():
        add_participant(conv, "A", 1, user=make_user())


def test_the_refused_participants_are_not_stored():
    from forum.models import Participant

    human = make_conversation("human")
    synthetic = make_conversation("synthetic")
    with invalid():
        add_participant(human, "A", 1, user=None)
    with invalid():
        add_participant(synthetic, "A", 1, user=make_user())
    assert Participant.objects.count() == 0


def test_a_human_conversation_accepts_users_and_a_synthetic_one_accepts_none():
    human = make_conversation("human")
    add_participant(human, "A", 1, user=make_user())
    synthetic = make_conversation("synthetic")
    add_participant(synthetic, "A", 1, user=None)


# --- MAX_PARTICIPANTS ---------------------------------------------------------------------------------------------


def test_the_default_limit_is_two_participants():
    from django.conf import settings

    assert settings.MAX_PARTICIPANTS == 2
    conv = make_conversation("human")
    add_participant(conv, "A", 1)
    add_participant(conv, "B", 2)
    with invalid():
        add_participant(conv, "C", 3)


def test_the_participant_over_the_limit_is_not_stored():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    add_participant(conv, "B", 2)
    with invalid():
        add_participant(conv, "C", 3)
    assert Participant.objects.filter(conversation=conv).count() == 2


def test_the_limit_follows_the_setting(settings):
    settings.MAX_PARTICIPANTS = 3
    conv = make_conversation("human")
    for i, label in enumerate("ABC", start=1):
        add_participant(conv, label, i)
    with invalid():
        add_participant(conv, "D", 4)


def test_a_limit_of_one_allows_exactly_one(settings):
    settings.MAX_PARTICIPANTS = 1
    conv = make_conversation("synthetic")
    add_participant(conv, "A", 1)
    with invalid():
        add_participant(conv, "B", 2)


def test_the_limit_is_per_conversation():
    for _ in range(3):
        conv = make_conversation("human")
        add_participant(conv, "A", 1)
        add_participant(conv, "B", 2)


def test_resaving_an_existing_participant_in_a_full_conversation_is_allowed():
    conv = make_conversation("human")
    a = add_participant(conv, "A", 1)
    add_participant(conv, "B", 2)
    a.save()
    a.refresh_from_db()
    assert a.label == "A"


def test_lowering_the_limit_later_does_not_break_existing_participants(settings):
    conv = make_conversation("human")
    a = add_participant(conv, "A", 1)
    add_participant(conv, "B", 2)
    settings.MAX_PARTICIPANTS = 1
    a.save()  # an update of an existing row is not a new participant
    with invalid():
        add_participant(conv, "C", 3)


def test_participant_relations_use_protect_and_are_required():
    from forum.models import Participant

    assert Participant._meta.get_field("conversation").null is False
    assert Participant._meta.get_field("conversation").remote_field.on_delete is models.PROTECT
    assert Participant._meta.get_field("user").remote_field.on_delete is models.PROTECT


# --- database checks on label and join_order (bypassing save) ---------------------------------------------------


@pytest.mark.parametrize("label", ["a", "z", "", "AB", "1", " ", "\u00c9"])
def test_the_database_refuses_a_label_that_is_not_one_uppercase_letter(label):
    from forum.models import Participant

    conv = make_conversation("synthetic")
    refused_by_database(lambda: Participant.objects.bulk_create([participant(conv, label, 1)]))


def test_the_database_refuses_a_bad_label_set_by_queryset_update():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    p = add_participant(conv, "A", 1)
    refused_by_database(lambda: Participant.objects.filter(pk=p.pk).update(label="b"))
    refused_by_database(lambda: Participant.objects.filter(pk=p.pk).update(label="AB"))


@pytest.mark.parametrize("label", ["A", "M", "Z"])
def test_the_database_accepts_the_edge_letters(label):
    from forum.models import Participant

    conv = make_conversation("synthetic")
    Participant.objects.bulk_create([participant(conv, label, 1)])


@pytest.mark.parametrize("join_order", [0, -1])
def test_the_database_refuses_a_join_order_below_one(join_order):
    from forum.models import Participant

    conv = make_conversation("synthetic")
    refused_by_database(lambda: Participant.objects.bulk_create([participant(conv, "A", join_order)]))


def test_the_database_refuses_join_order_zero_set_by_queryset_update():
    from forum.models import Participant

    conv = make_conversation("synthetic")
    p = add_participant(conv, "A", 1)
    refused_by_database(lambda: Participant.objects.filter(pk=p.pk).update(join_order=0))


def test_a_bad_label_is_refused_when_saving_directly():
    conv = make_conversation("synthetic")
    for bad in ("a", "AB", ""):
        with invalid():  # save() checks the label itself (coordinator clarification), before the database does
            participant(conv, bad, 1).save()
