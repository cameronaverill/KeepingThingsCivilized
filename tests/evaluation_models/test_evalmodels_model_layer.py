"""The friendly layer, strictly: rules the brief says are enforced in save() must raise ValidationError there, not merely
be caught later by the database (other tests accept either through kit.REFUSED; these pin the first layer), plus the
queryset-update twins for the target-existence and span triggers on the tables that are not Rating."""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


@pytest.fixture
def world():
    message, conv = kit.message_with_text(TEXT)
    return type("World", (), dict(message=message, llm=kit.make_rater("llm"), human=kit.make_rater("human")))


def test_a_rater_of_an_unknown_kind_gets_a_validation_error():
    from evaluation.models import Rater

    with pytest.raises(ValidationError):
        Rater(name="odd", kind="robot", model="m").save()


@pytest.mark.parametrize("status", ["running", "", "DONE"])
def test_a_rating_with_an_unknown_status_gets_a_validation_error(world, status):
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.message, status=status)


@pytest.mark.parametrize("replicate", [0, -1])
def test_a_rating_with_replicate_below_one_gets_a_validation_error(world, replicate):
    with pytest.raises(ValidationError):
        kit.make_rating(world.llm, world.message, replicate=replicate)


@pytest.mark.parametrize("value", [0, -0.5, 1.01])
def test_a_panel_with_an_impossible_span_threshold_gets_a_validation_error(value):
    with pytest.raises(ValidationError):
        kit.make_panel(span_match_min_iou=value)


def test_a_panel_with_a_zero_disagreement_threshold_gets_a_validation_error():
    with pytest.raises(ValidationError):
        kit.make_panel(intensity_disagreement_threshold=0)


@pytest.mark.parametrize("source", ["bogus", "", "rater:", "rater"])
def test_an_annotation_with_a_malformed_source_gets_a_validation_error(world, source):
    from evaluation.models import Annotation

    with pytest.raises(ValidationError):
        Annotation(dimension="stance", value="pro", source=source, **kit.target_ref(world.message)).save()


def test_a_finding_on_a_dimension_outside_the_taxonomy_gets_a_validation_error_even_if_the_rating_lists_it(world):
    """Rating rows made without validation may list any dimension; the finding still must use a taxonomy dimension."""
    from evaluation.models import Rating

    rating = kit.unsaved_rating(world.llm, world.message, dimensions=("sarcasm",))
    Rating.objects.bulk_create([rating])
    rating = Rating.objects.get()
    with pytest.raises(ValidationError):
        kit.make_finding(rating, 4, 8, dimension="sarcasm")


# --- merged findings: the friendly layer, not the trigger --------------------------------------------------------------------
def merge_world(world):
    other = kit.user_msg(world.message.conversation, {"A": world.message.participant}, "A", "A second message about the parks.")
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message, dimension="factual_accuracy")
    same = kit.make_rating(world.llm, world.message)
    return type(
        "MW",
        (),
        dict(
            consensus=consensus,
            wrong_dimension=kit.make_finding(same, 4, 8, dimension="abusiveness", intensity=1),
            wrong_target=kit.make_finding(kit.make_rating(world.llm, other), 2, 5),
        ),
    )


@pytest.mark.parametrize("which", ["wrong_dimension", "wrong_target"])
def test_add_raises_a_validation_error_for_an_inconsistent_finding(world, which):
    mw = merge_world(world)
    with pytest.raises(ValidationError), transaction.atomic():
        kit.merged_findings(mw.consensus).add(getattr(mw, which))


@pytest.mark.parametrize("which", ["wrong_dimension", "wrong_target"])
def test_the_reverse_add_raises_a_validation_error_for_an_inconsistent_finding(world, which):
    mw = merge_world(world)
    with pytest.raises(ValidationError), transaction.atomic():
        kit.reverse_consensus_accessor(getattr(mw, which)).add(mw.consensus)


@pytest.mark.parametrize("which", ["wrong_dimension", "wrong_target"])
def test_creating_a_through_row_raises_a_validation_error_for_an_inconsistent_finding(world, which):
    mw = merge_world(world)
    through = kit.findings_field().remote_field.through
    from evaluation.models import ConsensusFinding, Finding

    fks = {f.related_model: f.name for f in through._meta.concrete_fields if f.is_relation}
    with pytest.raises(ValidationError), transaction.atomic():
        through.objects.create(**{fks[ConsensusFinding]: mw.consensus, fks[Finding]: getattr(mw, which)})


# --- queryset-update twins for the other target tables ---------------------------------------------------------------------
def test_a_queryset_update_cannot_point_other_target_tables_at_a_missing_target(world):
    from evaluation.models import Annotation, CalibrationItem, CalibrationSet, ConsensusFinding

    ghost = world.message.pk + 1000
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message)
    annotation = Annotation.objects.create(dimension="stance", value="pro", source="self", **kit.target_ref(world.message))
    cset = CalibrationSet.objects.create(name="upd-set", seed=1)
    item = CalibrationItem.objects.create(**{kit.item_set_field_name(): cset, "stratum": "x", "order": 1, **kit.target_ref(world.message)})
    for model, row in ((ConsensusFinding, consensus), (Annotation, annotation), (CalibrationItem, item)):
        with pytest.raises(IntegrityError), transaction.atomic():
            model.objects.filter(pk=row.pk).update(target_id=ghost)
        with pytest.raises(IntegrityError), transaction.atomic():
            model.objects.filter(pk=row.pk).update(target_type="intervention_act", target_id=ghost)
        assert model.objects.get(pk=row.pk).target_id == world.message.pk


def test_a_queryset_update_cannot_stretch_a_consensus_span_past_the_text(world):
    from evaluation.models import ConsensusFinding

    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message, start=0, end=5)
    with pytest.raises(IntegrityError), transaction.atomic():
        ConsensusFinding.objects.filter(pk=consensus.pk).update(end=len(TEXT) + 1)
    assert ConsensusFinding.objects.get(pk=consensus.pk).end == 5
