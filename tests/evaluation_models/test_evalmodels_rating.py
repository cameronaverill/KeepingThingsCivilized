"""evaluation.Rating: one rater's pass over one target. Generic target reference validated to exist, replicate >= 1,
llm_call a foreign key (column llm_call_id) for LLM raters only, status choices, covered dimensions."""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    message = kit.user_msg(conv, parts, "A", "The city has 40 parks and all of them are closed.")
    act = kit.make_act(kit.make_run(message))
    return type("World", (), dict(conv=conv, parts=parts, message=message, act=act, llm=kit.make_rater("llm"), human=kit.make_rater("human")))


# --- fields, defaults, choices -----------------------------------------------------------------------------------------
def test_the_model_has_the_contract_fields():
    from evaluation.models import Rating

    names = kit.field_names(Rating)
    assert {"rater", "target_type", "target_id", "replicate", "llm_call", "guideline_version", "status", "started_at", "finished_at"} <= names
    assert kit.covered_field_name() in names


def test_target_type_choices_are_message_and_intervention_act():
    from evaluation.models import Rating

    assert kit.choice_values(Rating, "target_type") == {"message", "intervention_act"}


def test_status_choices_are_pending_done_failed():
    from evaluation.models import Rating

    assert kit.choice_values(Rating, "status") == {"pending", "done", "failed"}


def test_defaults(world):
    rating = kit.make_rating(world.llm, world.message)
    rating.refresh_from_db()
    assert rating.status == "pending"
    assert rating.replicate == 1
    assert rating.started_at is None and rating.finished_at is None


def test_llm_call_is_a_nullable_protected_foreign_key_to_the_ledger_with_the_old_column_name():
    from django.db import models

    from evaluation.models import Rating

    field = Rating._meta.get_field("llm_call")
    assert field.is_relation and isinstance(field, models.ForeignKey)
    assert field.related_model._meta.label == "moderation.LLMCall"
    assert field.null is True and field.remote_field.on_delete is models.PROTECT
    assert field.column == "llm_call_id" and field.attname == "llm_call_id"


def test_the_covered_dimensions_field_is_json_and_round_trips(world):
    from evaluation.models import Rating

    field = Rating._meta.get_field(kit.covered_field_name())
    assert isinstance(field, models.JSONField)
    rating = kit.make_rating(world.llm, world.message, dimensions=("abusiveness",))
    assert getattr(Rating.objects.get(pk=rating.pk), kit.covered_field_name()) == ["abusiveness"]


def test_a_finished_llm_rating_round_trips_every_field(world):
    from django.utils import timezone

    from evaluation.models import Rating

    call = kit.make_llm_call()
    start = timezone.now()
    rating = kit.make_rating(
        world.llm, world.message, replicate=3, llm_call_id=call.pk, guideline_version="g2", status="done",
        started_at=start, finished_at=start,
    )  # fmt: skip
    loaded = Rating.objects.get(pk=rating.pk)
    assert (loaded.rater_id, loaded.target_type, loaded.target_id, loaded.replicate, loaded.llm_call_id) == (
        world.llm.pk, "message", world.message.pk, 3, call.pk,
    )  # fmt: skip
    assert (loaded.guideline_version, loaded.status, loaded.started_at, loaded.finished_at) == ("g2", "done", start, start)


def test_a_rating_of_an_intervention_act_is_allowed(world):
    rating = kit.make_rating(world.human, world.act)
    assert (rating.target_type, rating.target_id) == ("intervention_act", world.act.pk)


def test_replicates_of_one_rater_and_target_may_share_a_target_with_different_numbers(world):
    kit.make_rating(world.llm, world.message, replicate=1)
    kit.make_rating(world.llm, world.message, replicate=2)


def test_llm_and_human_raters_may_both_rate_a_target(world):
    kit.make_rating(world.llm, world.message)
    kit.make_rating(world.human, world.message)


# --- target validation -------------------------------------------------------------------------------------------------
def test_a_missing_message_target_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.message, target_id=world.message.pk + 1000)


def test_a_missing_intervention_act_target_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.act, target_id=world.act.pk + 1000)


def test_the_target_must_exist_in_the_table_the_type_names(world):
    """An id that exists among messages but not among intervention acts is not a valid intervention_act target."""
    from moderation.models import InterventionAct

    later = [kit.user_msg(world.conv, world.parts, "B", f"another message {i} here") for i in range(10)]
    only_a_message = max(m.pk for m in later)
    assert not InterventionAct.objects.filter(pk=only_a_message).exists()
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.act, target_id=only_a_message)


def test_an_unknown_target_type_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rating(world.llm, world.message, target_type="conversation")


def test_target_type_is_checked_by_the_database_too(world):
    from evaluation.models import Rating

    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.message, target_type="conversation")])


def test_a_target_id_of_zero_or_below_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rating(world.llm, world.message, target_id=0)


# --- replicate, status, llm_call_id ------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [0, -1])
def test_replicate_must_be_at_least_one(world, bad):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rating(world.llm, world.message, replicate=bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_replicate_at_least_one_is_checked_by_the_database(world, bad):
    from evaluation.models import Rating

    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.message, replicate=bad)])


def test_an_unknown_status_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rating(world.llm, world.message, status="running")


def test_the_status_is_checked_by_the_database(world):
    from evaluation.models import Rating

    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.message, status="running")])


@pytest.mark.parametrize("status", ["pending", "done", "failed"])
def test_every_status_is_accepted(world, status):
    assert kit.make_rating(world.llm, world.message, status=status).status == status


def test_an_llm_call_id_is_allowed_for_an_llm_rater(world):
    call = kit.make_llm_call()
    assert kit.make_rating(world.llm, world.message, llm_call_id=call.pk).llm_call_id == call.pk


def test_a_human_rating_cannot_carry_an_llm_call_id(world):
    call = kit.make_llm_call()
    with pytest.raises(ValidationError):
        kit.make_rating(world.human, world.message, llm_call_id=call.pk)


def test_a_human_rating_without_an_llm_call_id_is_fine(world):
    assert kit.make_rating(world.human, world.message).llm_call_id is None


def test_the_ledger_row_a_rating_points_at_cannot_be_deleted(world):
    """PROTECT: a spend-ledger row that a rating cites stays until the rating is gone."""
    from django.db.models import ProtectedError

    from evaluation.models import Rating
    from moderation.models import LLMCall

    call = kit.make_llm_call()
    rating = kit.make_rating(world.llm, world.message, llm_call=call)
    with pytest.raises(ProtectedError):
        call.delete()
    assert LLMCall.objects.filter(pk=call.pk).exists() and Rating.objects.get(pk=rating.pk).llm_call_id == call.pk


def test_a_ledger_row_no_rating_cites_can_still_be_deleted(world):
    from moderation.models import LLMCall

    kit.make_rating(world.llm, world.message, llm_call=kit.make_llm_call())
    free = kit.make_llm_call()
    free.delete()
    assert LLMCall.objects.count() == 1


def test_the_ledger_row_is_reachable_through_the_relation_and_the_reverse_accessor(world):
    call = kit.make_llm_call()
    rating = kit.make_rating(world.llm, world.message, llm_call=call)
    assert rating.llm_call == call
    accessor = type(rating)._meta.get_field("llm_call").remote_field.get_accessor_name()
    assert list(getattr(call, accessor).all()) == [rating]


def test_a_made_up_ledger_id_is_refused_by_the_database(world):
    from django.db import IntegrityError, connection, transaction

    from evaluation.models import Rating

    made_up = kit.make_llm_call().pk + 5000
    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.message, llm_call_id=made_up)])
        connection.check_constraints()
    assert Rating.objects.count() == 0


def test_a_queryset_update_cannot_point_a_rating_at_a_made_up_ledger_id(world):
    from django.db import IntegrityError, connection, transaction

    from evaluation.models import Rating

    call = kit.make_llm_call()
    rating = kit.make_rating(world.llm, world.message, llm_call=call)
    with pytest.raises(IntegrityError), transaction.atomic():
        Rating.objects.filter(pk=rating.pk).update(llm_call_id=call.pk + 5000)
        connection.check_constraints()
    assert Rating.objects.get(pk=rating.pk).llm_call_id == call.pk


def test_a_made_up_ledger_id_is_refused_by_save_or_the_database_never_stored(world):
    """Through save() the refusal may be the friendly ValidationError or, at the latest, the database check."""
    from django.db import connection, transaction

    from evaluation.models import Rating

    made_up = kit.make_llm_call().pk + 5000
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_rating(world.llm, world.message, llm_call_id=made_up)
        connection.check_constraints()
    assert Rating.objects.count() == 0


# --- covered dimensions ------------------------------------------------------------------------------------------------
def test_an_unknown_covered_dimension_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.message, dimensions=("factual_accuracy", "sarcasm"))


@pytest.mark.parametrize("dims", [("factual_accuracy",), ("abusiveness",), ("factual_accuracy", "abusiveness")])
def test_taxonomy_dimensions_are_accepted(world, dims):
    kit.make_rating(world.llm, world.message, dimensions=dims)


# --- PROTECT -----------------------------------------------------------------------------------------------------------
def test_a_rater_with_ratings_cannot_be_deleted(world):
    kit.make_rating(world.llm, world.message)
    with pytest.raises(ProtectedError):
        world.llm.delete()


def test_a_rating_with_findings_cannot_be_deleted(world):
    rating = kit.make_rating(world.llm, world.message)
    kit.make_finding(rating, 4, 8)
    with pytest.raises(ProtectedError):
        rating.delete()


def test_a_rating_without_findings_can_be_deleted(world):
    from evaluation.models import Rating

    rating = kit.make_rating(world.llm, world.message)
    rating.delete()
    assert Rating.objects.count() == 0


# --- update ------------------------------------------------------------------------------------------------------------
def test_the_rules_apply_on_update(world):
    rating = kit.make_rating(world.human, world.message)
    rating.llm_call_id = kit.make_llm_call().pk
    with pytest.raises(ValidationError):
        rating.save()
    rating = kit.make_rating(world.llm, world.message)
    rating.target_id = world.message.pk + 1000
    with pytest.raises(ValidationError):
        rating.save()
