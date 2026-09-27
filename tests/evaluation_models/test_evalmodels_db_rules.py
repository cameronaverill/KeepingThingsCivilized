"""Rules that cross tables, at the database level (bulk_create, queryset update and the many-to-many through table all
skip save()) and their model-level twins: target existence, LLM call only for LLM raters, a rating's covered dimensions
and target cannot move under its findings, a consensus span lies inside its target text, merged findings share target and
dimension. The brief asks for each rule twice, 'where expressible'; SQLite triggers make these expressible."""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


@pytest.fixture
def world():
    message, conv = kit.message_with_text(TEXT)
    other = kit.user_msg(conv, {"A": message.participant}, "A", "A second message about the parks.")
    act = kit.make_act(kit.make_run(message), text="Please note the parks were closed for repairs.")
    llm, human = kit.make_rater("llm"), kit.make_rater("human")
    return type("World", (), dict(message=message, other=other, act=act, llm=llm, human=human, conv=conv))


def refused(callable_):
    with pytest.raises(IntegrityError), transaction.atomic():
        callable_()


# --- target existence --------------------------------------------------------------------------------------------------
def missing_ids(world):
    return world.message.pk + 1000, world.act.pk + 1000


def test_a_rating_of_a_missing_target_is_refused_by_the_database(world):
    from evaluation.models import Rating

    ghost, ghost_act = missing_ids(world)
    refused(lambda: Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.message, target_id=ghost)]))
    refused(lambda: Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.act, target_id=ghost_act)]))
    assert Rating.objects.count() == 0


def test_a_queryset_update_cannot_point_a_rating_at_a_missing_target(world):
    from evaluation.models import Rating

    rating = kit.make_rating(world.llm, world.message)
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(target_id=missing_ids(world)[0]))
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(target_type="intervention_act", target_id=missing_ids(world)[1]))
    rating.refresh_from_db()
    assert (rating.target_type, rating.target_id) == ("message", world.message.pk)


def test_a_message_id_is_not_an_act_id_for_the_database_either(world):
    """An id that is a message but no act, filed as an intervention act, is refused."""
    from evaluation.models import Rating
    from moderation.models import InterventionAct

    extra = [kit.user_msg(world.conv, {"A": world.message.participant}, "A", f"one more message {i} here") for i in range(8)]
    only_message = max(m.pk for m in extra)
    assert not InterventionAct.objects.filter(pk=only_message).exists()
    refused(lambda: Rating.objects.bulk_create([kit.unsaved_rating(world.llm, world.act, target_id=only_message)]))


def test_the_other_target_tables_check_existence_in_the_database_too(world):
    from evaluation.models import Annotation, CalibrationItem, CalibrationSet, ConsensusFinding

    ghost = missing_ids(world)[0]
    panel = kit.make_panel([world.llm])
    cset = CalibrationSet.objects.create(name="ghost-set", seed=1)
    refused(lambda: Annotation.objects.bulk_create([Annotation(target_type="message", target_id=ghost, dimension="stance", value="pro")]))
    refused(
        lambda: CalibrationItem.objects.bulk_create(
            [CalibrationItem(**{kit.item_set_field_name(): cset, "target_type": "message", "target_id": ghost, "stratum": "x", "order": 1})]
        )
    )
    refused(
        lambda: ConsensusFinding.objects.bulk_create(
            [ConsensusFinding(panel=panel, target_type="message", target_id=ghost, dimension="factual_accuracy", start=0, end=3, n_raters=1)]
        )
    )


# --- LLM call only for LLM raters --------------------------------------------------------------------------------------
def test_a_human_rating_with_an_llm_call_id_is_refused_by_the_database(world):
    from evaluation.models import Rating

    call = kit.make_llm_call()
    refused(lambda: Rating.objects.bulk_create([kit.unsaved_rating(world.human, world.message, llm_call_id=call.pk)]))
    assert Rating.objects.count() == 0


def test_a_queryset_update_cannot_give_a_human_rating_an_llm_call_id(world):
    from evaluation.models import Rating

    rating = kit.make_rating(world.human, world.message)
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(llm_call_id=kit.make_llm_call().pk))


def test_a_queryset_update_cannot_move_an_llm_call_rating_to_a_human_rater(world):
    from evaluation.models import Rating

    rating = kit.make_rating(world.llm, world.message, llm_call_id=kit.make_llm_call().pk)
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(rater=world.human))
    rating.refresh_from_db()
    assert rating.rater_id == world.llm.pk


def test_an_llm_rater_with_llm_call_ratings_cannot_be_turned_into_a_human_by_update(world):
    from evaluation.models import Rater

    kit.make_rating(world.llm, world.message, llm_call_id=kit.make_llm_call().pk)
    refused(lambda: Rater.objects.filter(pk=world.llm.pk).update(kind="human", user=kit.make_user(), provider="", model="", temperature=None))
    world.llm.refresh_from_db()
    assert world.llm.kind == "llm"


# --- a rating's dimensions and target cannot move under its findings ------------------------------------------------------
def rated_with_finding(world):
    rating = kit.make_rating(world.llm, world.message, dimensions=("factual_accuracy", "abusiveness"))
    kit.make_finding(rating, 4, 8, dimension="abusiveness", intensity=1)
    return rating


def test_a_dimension_with_findings_cannot_be_removed_from_its_rating(world):
    rating = rated_with_finding(world)
    setattr(rating, kit.covered_field_name(), ["factual_accuracy"])
    with pytest.raises(ValidationError):
        rating.save()


def test_a_dimension_without_findings_can_be_removed_from_its_rating(world):
    rating = rated_with_finding(world)
    setattr(rating, kit.covered_field_name(), ["abusiveness"])
    rating.save()


def test_a_queryset_update_cannot_remove_a_dimension_that_has_findings(world):
    from evaluation.models import Rating

    rating = rated_with_finding(world)
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(**{kit.covered_field_name(): ["factual_accuracy"]}))
    assert getattr(Rating.objects.get(pk=rating.pk), kit.covered_field_name()) == ["factual_accuracy", "abusiveness"]


def test_a_finding_on_a_dimension_the_rating_does_not_cover_is_refused_by_the_database(world):
    from evaluation.models import Finding

    rating = kit.make_rating(world.llm, world.message, dimensions=("factual_accuracy",))
    refused(lambda: Finding.objects.bulk_create([kit.unsaved_finding(rating, 4, 8, dimension="abusiveness", intensity=1)]))
    assert Finding.objects.count() == 0


def test_the_target_of_a_rating_with_findings_cannot_change(world):
    rating = rated_with_finding(world)
    rating.target_id = world.other.pk
    with pytest.raises(ValidationError):
        rating.save()


def test_a_queryset_update_cannot_change_the_target_of_a_rating_with_findings(world):
    from evaluation.models import Rating

    rating = rated_with_finding(world)
    refused(lambda: Rating.objects.filter(pk=rating.pk).update(target_id=world.other.pk))
    rating.refresh_from_db()
    assert rating.target_id == world.message.pk


def test_the_target_of_a_rating_without_findings_may_change(world):
    rating = kit.make_rating(world.llm, world.message)
    rating.target_id = world.other.pk
    rating.save()
    rating.refresh_from_db()
    assert rating.target_id == world.other.pk


# --- consensus span inside the text ------------------------------------------------------------------------------------------
def test_a_consensus_span_past_the_end_of_the_text_is_refused_by_the_model_and_the_database(world):
    from evaluation.models import ConsensusFinding

    panel = kit.make_panel([world.llm])
    with pytest.raises(ValidationError):
        kit.make_consensus(panel, world.message, start=0, end=len(TEXT) + 1)
    bad = ConsensusFinding(panel=panel, target_type="message", target_id=world.message.pk, dimension="factual_accuracy", start=0, end=len(TEXT) + 1, n_raters=1)
    refused(lambda: ConsensusFinding.objects.bulk_create([bad]))
    assert ConsensusFinding.objects.count() == 0


def test_a_consensus_span_that_ends_exactly_at_the_end_of_the_text_is_accepted(world):
    panel = kit.make_panel([world.llm])
    assert kit.make_consensus(panel, world.message, start=len(TEXT) - 5, end=len(TEXT)).end == len(TEXT)


@pytest.mark.parametrize("start,end", [(5, 5), (8, 4), (-1, 3)])
def test_a_consensus_span_must_be_a_real_span(world, start, end):
    from evaluation.models import ConsensusFinding

    panel = kit.make_panel([world.llm])
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_consensus(panel, world.message, start=start, end=end)
    bad = ConsensusFinding(panel=panel, target_type="message", target_id=world.message.pk, dimension="factual_accuracy", start=start, end=end, n_raters=1)
    refused(lambda: ConsensusFinding.objects.bulk_create([bad]))


def test_n_raters_must_be_at_least_one(world):
    from evaluation.models import ConsensusFinding

    panel = kit.make_panel([world.llm])
    with pytest.raises(ValidationError):
        kit.make_consensus(panel, world.message, n_raters=0)
    bad = ConsensusFinding(panel=panel, target_type="message", target_id=world.message.pk, dimension="factual_accuracy", start=0, end=5, n_raters=0)
    refused(lambda: ConsensusFinding.objects.bulk_create([bad]))


@pytest.mark.parametrize("field", ["adjudicated_intensity"])
def test_an_adjudicated_intensity_is_zero_to_four(world, field):
    panel = kit.make_panel([world.llm])
    kit.make_consensus(panel, world.message, **{field: 4}, adjudicated_by=kit.make_user())
    with pytest.raises(ValidationError):
        kit.make_consensus(panel, world.message, **{field: 5})


# --- merged findings, through the M2M's own table -----------------------------------------------------------------------------
def through_parts():
    through = kit.findings_field().remote_field.through
    from evaluation.models import ConsensusFinding, Finding

    fks = {f.related_model: f.name for f in through._meta.concrete_fields if f.is_relation}
    return through, fks[ConsensusFinding], fks[Finding]


def test_the_through_table_cannot_bypass_the_same_dimension_rule(world):
    through, consensus_fk, finding_fk = through_parts()
    rating = kit.make_rating(world.llm, world.message)
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message, dimension="factual_accuracy")
    wrong = kit.make_finding(rating, 4, 8, dimension="abusiveness", intensity=1)
    refused(lambda: through.objects.bulk_create([through(**{consensus_fk: consensus, finding_fk: wrong})]))
    assert through.objects.count() == 0


def test_the_through_table_cannot_bypass_the_same_target_rule(world):
    through, consensus_fk, finding_fk = through_parts()
    other_rating = kit.make_rating(world.llm, world.other)
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message)
    wrong = kit.make_finding(other_rating, 2, 5)
    refused(lambda: through.objects.bulk_create([through(**{consensus_fk: consensus, finding_fk: wrong})]))


def test_the_through_table_accepts_a_consistent_pair_and_refuses_the_same_pair_twice(world):
    through, consensus_fk, finding_fk = through_parts()
    rating = kit.make_rating(world.llm, world.message)
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message)
    good = kit.make_finding(rating, 4, 8)
    through.objects.bulk_create([through(**{consensus_fk: consensus, finding_fk: good})])
    refused(lambda: through.objects.bulk_create([through(**{consensus_fk: consensus, finding_fk: good})]))
    assert through.objects.count() == 1


def test_a_queryset_update_of_the_through_table_cannot_break_consistency(world):
    through, consensus_fk, finding_fk = through_parts()
    rating = kit.make_rating(world.llm, world.message)
    consensus = kit.make_consensus(kit.make_panel([world.llm]), world.message, dimension="factual_accuracy")
    good = kit.make_finding(rating, 4, 8, dimension="factual_accuracy")
    wrong = kit.make_finding(rating, 10, 14, dimension="abusiveness", intensity=1)
    through.objects.bulk_create([through(**{consensus_fk: consensus, finding_fk: good})])
    refused(lambda: through.objects.update(**{finding_fk: wrong}))
    assert set(kit.merged_findings(consensus).all()) == {good}
