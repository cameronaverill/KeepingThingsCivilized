"""evaluation.CalibrationSet, CalibrationItem and Annotation (the tables only; the builder is in
test_evalmodels_calibration.py)."""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    conv, parts = kit.make_conversation()
    m1 = kit.user_msg(conv, parts, "A", "first message text")
    m2 = kit.user_msg(conv, parts, "B", "second message text")
    act = kit.make_act(kit.make_run(m1))
    return type("World", (), dict(conv=conv, m1=m1, m2=m2, act=act))


def make_set(**extra):
    from evaluation.models import CalibrationSet

    values = dict(name=f"set{kit.n()}", seed=42, strata={"no_issue": 5})
    values.update(extra)
    return CalibrationSet.objects.create(**values)


def make_item(cset, target, **extra):
    from evaluation.models import CalibrationItem

    values = {kit.item_set_field_name(): cset, "stratum": "no_issue", "order": 1, **kit.target_ref(target)}
    values.update(extra)
    return CalibrationItem.objects.create(**values)


# --- CalibrationSet ----------------------------------------------------------------------------------------------------
def test_a_calibration_set_round_trips():
    from evaluation.models import CalibrationSet

    strata = {"no_issue": {"target": 3, "achieved": 3}, "planted_high": {"target": 3, "achieved": 1, "shortfall": 2}}
    made = make_set(name="calib-1", seed=987654321, strata=strata)
    loaded = CalibrationSet.objects.get(pk=made.pk)
    assert (loaded.name, loaded.seed, loaded.strata) == ("calib-1", 987654321, strata)
    assert loaded.created_at is not None


def test_the_set_fields():
    from evaluation.models import CalibrationSet

    assert {"name", "seed", "strata", "created_at"} <= kit.field_names(CalibrationSet)
    assert isinstance(CalibrationSet._meta.get_field("strata"), models.JSONField)
    assert isinstance(CalibrationSet._meta.get_field("seed"), models.BigIntegerField | models.IntegerField)


def test_a_seed_larger_than_32_bits_is_stored_exactly():
    made = make_set(seed=2**40 + 7)
    made.refresh_from_db()
    assert made.seed == 2**40 + 7


def test_strata_default_to_an_empty_mapping():
    from evaluation.models import CalibrationSet

    made = CalibrationSet.objects.create(name="no-strata", seed=1)
    made.refresh_from_db()
    assert made.strata == {}


# --- CalibrationItem ---------------------------------------------------------------------------------------------------
def test_the_item_fields():
    from evaluation.models import CalibrationItem

    assert {"target_type", "target_id", "stratum", "order", kit.item_set_field_name()} <= kit.field_names(CalibrationItem)
    assert kit.choice_values(CalibrationItem, "target_type") == {"message", "intervention_act"}


def test_an_item_round_trips(world):
    from evaluation.models import CalibrationItem

    cset = make_set()
    made = make_item(cset, world.m1, stratum="planted_high", order=7)
    loaded = CalibrationItem.objects.get(pk=made.pk)
    assert (loaded.target_type, loaded.target_id, loaded.stratum, loaded.order) == ("message", world.m1.pk, "planted_high", 7)
    assert getattr(loaded, kit.item_set_field_name() + "_id") == cset.pk


def test_an_intervention_act_can_be_an_item(world):
    made = make_item(make_set(), world.act)
    assert made.target_type == "intervention_act"


def test_a_target_is_unique_per_set(world):
    cset = make_set()
    make_item(cset, world.m1, order=1)
    with pytest.raises(kit.REFUSED), transaction.atomic():
        make_item(cset, world.m1, order=2, stratum="other")


def test_a_target_is_unique_per_set_at_database_level(world):
    from evaluation.models import CalibrationItem

    cset = make_set()
    make_item(cset, world.m1)
    duplicate = CalibrationItem(**{kit.item_set_field_name(): cset, "stratum": "x", "order": 2, **kit.target_ref(world.m1)})
    with pytest.raises(IntegrityError), transaction.atomic():
        CalibrationItem.objects.bulk_create([duplicate])


def test_the_same_target_may_be_in_two_sets_and_two_targets_in_one_set(world):
    first, second = make_set(), make_set()
    make_item(first, world.m1, order=1)
    make_item(second, world.m1, order=1)
    make_item(first, world.m2, order=2)
    assert len(kit.items_of(first)) == 2 and len(kit.items_of(second)) == 1


def test_a_message_and_an_act_with_the_same_id_are_different_targets(world):
    """Uniqueness is over (set, target_type, target_id), not over target_id alone."""
    cset = make_set()
    make_item(cset, world.m1, order=1)
    made = make_item(cset, world.act, order=2, target_id=world.act.pk)
    assert made.pk


def test_a_missing_target_is_refused(world):
    with pytest.raises(ValidationError):
        make_item(make_set(), world.m1, target_id=world.m1.pk + 1000)


def test_an_unknown_target_type_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        make_item(make_set(), world.m1, target_type="conversation")


def test_a_set_with_items_cannot_be_deleted(world):
    cset = make_set()
    make_item(cset, world.m1)
    with pytest.raises(ProtectedError):
        cset.delete()


def test_a_set_without_items_can_be_deleted():
    from evaluation.models import CalibrationSet

    make_set().delete()
    assert CalibrationSet.objects.count() == 0


# --- Annotation --------------------------------------------------------------------------------------------------------
def make_annotation(target, **extra):
    from evaluation.models import Annotation

    values = dict(dimension="stance", value="pro", **kit.target_ref(target))
    values.update(extra)
    return Annotation.objects.create(**values)


def test_the_annotation_fields():
    from django.db import models

    from evaluation.models import Annotation

    names = kit.field_names(Annotation)
    assert {"target_type", "target_id", "dimension", "value", "rater", "rating", "confidence", "created_at"} <= names
    assert "source" not in names
    assert Annotation._meta.get_field("rating").null is True
    assert Annotation._meta.get_field("confidence").null is True
    rater = Annotation._meta.get_field("rater")
    assert isinstance(rater, models.ForeignKey) and rater.null is True
    assert rater.related_model._meta.label == "evaluation.Rater" and rater.remote_field.on_delete is models.PROTECT


def test_an_annotation_round_trips(world):
    from evaluation.models import Annotation

    made = make_annotation(world.m1, dimension="directional_effect", value="toward_pro", confidence=0.9)
    loaded = Annotation.objects.get(pk=made.pk)
    assert (loaded.target_type, loaded.target_id, loaded.dimension, loaded.value) == (
        "message", world.m1.pk, "directional_effect", "toward_pro",
    )  # fmt: skip
    assert loaded.confidence == 0.9 and loaded.rating_id is None and loaded.created_at is not None


@pytest.mark.parametrize("dimension", ["stance", "tone", "directional_effect"])
def test_the_message_level_dimensions_are_accepted(world, dimension):
    assert make_annotation(world.m1, dimension=dimension).dimension == dimension


def test_no_rater_means_the_researchers_own_label(world):
    from evaluation.models import Annotation

    made = make_annotation(world.m1)
    assert Annotation.objects.get(pk=made.pk).rater_id is None
    assert Annotation.objects.filter(rater__isnull=True).count() == 1


@pytest.mark.parametrize("kind", ["llm", "human"])
def test_a_rater_of_either_kind_can_label(world, kind):
    rater = kit.make_rater(kind)
    made = make_annotation(world.m1, rater=rater)
    made.refresh_from_db()
    assert made.rater == rater


def test_an_annotation_by_a_rater_can_belong_to_that_raters_rating_and_protects_it(world):
    from django.db.models import ProtectedError

    rater = kit.make_rater("llm", name="judge-two")
    rating = kit.make_rating(rater, world.m1)
    make_annotation(world.m1, rating=rating, rater=rater)
    with pytest.raises(ProtectedError):
        rating.delete()


def test_a_rater_with_annotations_cannot_be_deleted(world):
    """PROTECT: the rater has no ratings here, so only the annotation blocks the delete."""
    from django.db.models import ProtectedError

    from evaluation.models import Rater

    rater = kit.make_rater("llm", name="judge-one")
    make_annotation(world.m1, rater=rater)
    with pytest.raises(ProtectedError):
        rater.delete()
    assert Rater.objects.filter(pk=rater.pk).exists()


def test_a_rater_without_annotations_can_be_deleted(world):
    from evaluation.models import Rater

    kit.make_rater("llm", name="unused").delete()
    assert Rater.objects.count() == 0


def test_a_made_up_rater_id_is_refused_by_the_database(world):
    from django.db import IntegrityError, connection, transaction

    from evaluation.models import Annotation

    real = kit.make_rater("llm")
    bad = Annotation(dimension="stance", value="pro", rater_id=real.pk + 5000, **kit.target_ref(world.m1))
    with pytest.raises(IntegrityError), transaction.atomic():
        Annotation.objects.bulk_create([bad])
        connection.check_constraints()
    assert Annotation.objects.count() == 0


def test_a_queryset_update_cannot_point_an_annotation_at_a_made_up_rater(world):
    from django.db import IntegrityError, connection, transaction

    from evaluation.models import Annotation

    rater = kit.make_rater("llm")
    made = make_annotation(world.m1, rater=rater)
    with pytest.raises(IntegrityError), transaction.atomic():
        Annotation.objects.filter(pk=made.pk).update(rater_id=rater.pk + 5000)
        connection.check_constraints()
    assert Annotation.objects.get(pk=made.pk).rater_id == rater.pk


def test_the_text_source_is_gone(world):
    from evaluation.models import Annotation

    with pytest.raises(TypeError):
        Annotation(dimension="stance", value="pro", source="self", **kit.target_ref(world.m1))


def test_a_missing_target_is_refused_for_an_annotation(world):
    with pytest.raises(ValidationError):
        make_annotation(world.m1, target_id=world.m1.pk + 1000)


def test_several_annotations_may_share_a_target_and_dimension(world):
    other = kit.make_rater("llm", name="judge-three")
    make_annotation(world.m1, value="pro")
    make_annotation(world.m1, rater=other, value="con")
    make_annotation(world.m1, rater=other, value="pro")
