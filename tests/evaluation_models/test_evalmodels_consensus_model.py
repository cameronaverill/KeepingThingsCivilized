"""evaluation.ConsensusFinding, its many-to-many to Finding (all merged findings share target and dimension) and
IssueFindingLink. (build_consensus itself is tested in test_evalmodels_consensus.py.)"""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

pytestmark = pytest.mark.django_db

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


@pytest.fixture
def world():
    message, conv = kit.message_with_text(TEXT)
    other_message = kit.user_msg(conv, {"A": message.participant}, "A", "A second message about the parks.")
    r1, r2 = kit.make_rater("llm"), kit.make_rater("llm")
    panel = kit.make_panel([r1, r2])
    rating1, rating2 = kit.make_rating(r1, message), kit.make_rating(r2, message)
    other_rating = kit.make_rating(r1, other_message)
    return type(
        "World",
        (),
        dict(message=message, other=other_message, panel=panel, r1=r1, r2=r2, rating1=rating1, rating2=rating2, other_rating=other_rating),
    )


# --- fields ------------------------------------------------------------------------------------------------------------
def test_the_model_has_the_contract_fields():
    from evaluation.models import ConsensusFinding

    names = kit.field_names(ConsensusFinding)
    assert {
        "panel", "target_type", "target_id", "dimension", "start", "end", "n_raters", "intensity_mean", "intensity_range",
        "needs_adjudication", "adjudicated_intensity", "adjudicated_by",
    } <= names  # fmt: skip
    assert isinstance(kit.findings_field(), models.ManyToManyField)


def test_defaults_and_nullable_fields(world):
    from evaluation.models import ConsensusFinding

    consensus = kit.make_consensus(world.panel, world.message)
    loaded = ConsensusFinding.objects.get(pk=consensus.pk)
    assert loaded.needs_adjudication is False
    assert loaded.adjudicated_intensity is None and loaded.adjudicated_by_id is None
    assert loaded.intensity_mean is None and loaded.intensity_range is None


def test_a_consensus_finding_round_trips(world):
    from evaluation.models import ConsensusFinding

    person = kit.make_user()
    made = kit.make_consensus(
        world.panel, world.message, dimension="abusiveness", start=4, end=20, n_raters=2, intensity_mean=2.5,
        intensity_range=1, needs_adjudication=True, adjudicated_intensity=3, adjudicated_by=person,
    )  # fmt: skip
    loaded = ConsensusFinding.objects.get(pk=made.pk)
    assert (loaded.panel_id, loaded.target_type, loaded.target_id, loaded.dimension) == (world.panel.pk, "message", world.message.pk, "abusiveness")
    assert (loaded.start, loaded.end, loaded.n_raters, loaded.intensity_mean, loaded.intensity_range) == (4, 20, 2, 2.5, 1)
    assert (loaded.needs_adjudication, loaded.adjudicated_intensity, loaded.adjudicated_by_id) == (True, 3, person.pk)


def test_target_and_dimension_choices():
    from evaluation.models import ConsensusFinding

    assert kit.choice_values(ConsensusFinding, "target_type") == {"message", "intervention_act"}


def test_an_unknown_dimension_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_consensus(world.panel, world.message, dimension="sarcasm")


def test_a_missing_target_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_consensus(world.panel, world.message, target_id=world.message.pk + 1000)


# --- many-to-many consistency ------------------------------------------------------------------------------------------
def test_findings_on_the_same_target_and_dimension_can_be_merged(world):
    consensus = kit.make_consensus(world.panel, world.message, n_raters=2)
    a = kit.make_finding(world.rating1, 4, 8)
    b = kit.make_finding(world.rating2, 4, 8)
    kit.merged_findings(consensus).add(a, b)
    assert set(kit.merged_findings(consensus).all()) == {a, b}
    assert set(kit.reverse_consensus_accessor(a).all()) == {consensus}


def test_a_finding_on_another_dimension_cannot_be_merged(world):
    consensus = kit.make_consensus(world.panel, world.message, dimension="factual_accuracy")
    wrong = kit.make_finding(world.rating1, 4, 8, dimension="abusiveness")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.merged_findings(consensus).add(wrong)
    assert kit.merged_findings(consensus).count() == 0


def test_a_finding_on_another_target_cannot_be_merged(world):
    consensus = kit.make_consensus(world.panel, world.message)
    wrong = kit.make_finding(world.other_rating, 2, 5)
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.merged_findings(consensus).add(wrong)
    assert kit.merged_findings(consensus).count() == 0


def test_one_bad_finding_in_an_add_call_refuses_the_whole_call(world):
    consensus = kit.make_consensus(world.panel, world.message)
    good = kit.make_finding(world.rating1, 4, 8)
    wrong = kit.make_finding(world.rating1, 4, 8, dimension="abusiveness")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.merged_findings(consensus).add(good, wrong)
    assert kit.merged_findings(consensus).count() == 0


def test_consistency_is_enforced_from_the_reverse_side_too(world):
    consensus = kit.make_consensus(world.panel, world.message, dimension="factual_accuracy")
    wrong = kit.make_finding(world.rating1, 4, 8, dimension="abusiveness")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.reverse_consensus_accessor(wrong).add(consensus)
    assert kit.merged_findings(consensus).count() == 0


def test_consistency_is_enforced_by_set(world):
    consensus = kit.make_consensus(world.panel, world.message)
    good = kit.make_finding(world.rating1, 4, 8)
    wrong = kit.make_finding(world.other_rating, 2, 5)
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.merged_findings(consensus).set([good, wrong])
    assert kit.merged_findings(consensus).count() == 0


def test_a_merged_finding_can_be_removed_and_the_set_cleared(world):
    consensus = kit.make_consensus(world.panel, world.message)
    a, b = kit.make_finding(world.rating1, 4, 8), kit.make_finding(world.rating2, 4, 8)
    kit.merged_findings(consensus).add(a, b)
    kit.merged_findings(consensus).remove(a)
    assert set(kit.merged_findings(consensus).all()) == {b}
    kit.merged_findings(consensus).clear()
    assert kit.merged_findings(consensus).count() == 0


def test_a_finding_may_belong_to_the_consensus_of_two_panels(world):
    other_panel = kit.make_panel([world.r1, world.r2])
    finding = kit.make_finding(world.rating1, 4, 8)
    for panel in (world.panel, other_panel):
        kit.merged_findings(kit.make_consensus(panel, world.message)).add(finding)
    assert kit.reverse_consensus_accessor(finding).count() == 2


# --- PROTECT -----------------------------------------------------------------------------------------------------------
def test_a_panel_with_consensus_findings_cannot_be_deleted(world):
    kit.make_consensus(world.panel, world.message)
    with pytest.raises(ProtectedError):
        world.panel.delete()


def test_an_adjudicating_user_cannot_be_deleted(world):
    person = kit.make_user()
    kit.make_consensus(world.panel, world.message, adjudicated_by=person, adjudicated_intensity=2)
    with pytest.raises(ProtectedError):
        person.delete()


def test_a_finding_that_a_consensus_merges_cannot_be_deleted(world):
    """The merge is the audit trail from ground truth back to each rater's finding."""
    consensus = kit.make_consensus(world.panel, world.message)
    finding = kit.make_finding(world.rating1, 4, 8)
    kit.merged_findings(consensus).add(finding)
    with pytest.raises(ProtectedError):
        finding.delete()
    assert set(kit.merged_findings(consensus).all()) == {finding}


def test_a_consensus_finding_with_merged_findings_cannot_be_deleted(world):
    consensus = kit.make_consensus(world.panel, world.message)
    kit.merged_findings(consensus).add(kit.make_finding(world.rating1, 4, 8))
    with pytest.raises(ProtectedError):
        consensus.delete()


# --- IssueFindingLink --------------------------------------------------------------------------------------------------
@pytest.fixture
def link_world(world):
    run = kit.make_run(world.message)
    issue = kit.make_issue(run, world.message)
    consensus = kit.make_consensus(world.panel, world.message)
    return type("LinkWorld", (), dict(world=world, run=run, issue=issue, consensus=consensus))


def make_link(lw, **extra):
    from evaluation.models import IssueFindingLink

    values = dict(issue=lw.issue, consensus_finding=lw.consensus, overlap=0.75)
    values.update(extra)
    return IssueFindingLink.objects.create(**values)


def test_a_link_round_trips(link_world):
    from evaluation.models import IssueFindingLink

    made = make_link(link_world, overlap=0.6)
    loaded = IssueFindingLink.objects.get(pk=made.pk)
    assert (loaded.issue_id, loaded.consensus_finding_id, loaded.overlap) == (link_world.issue.pk, link_world.consensus.pk, 0.6)


def test_the_issue_field_points_at_moderation_issue():
    from evaluation.models import IssueFindingLink

    assert IssueFindingLink._meta.get_field("issue").related_model._meta.label == "moderation.Issue"
    assert IssueFindingLink._meta.get_field("consensus_finding").related_model._meta.label == "evaluation.ConsensusFinding"


def test_an_issue_and_a_consensus_finding_link_only_once(link_world):
    make_link(link_world)
    with pytest.raises(kit.REFUSED), transaction.atomic():
        make_link(link_world, overlap=0.9)


def test_uniqueness_holds_at_database_level(link_world):
    from evaluation.models import IssueFindingLink

    make_link(link_world)
    with pytest.raises(IntegrityError), transaction.atomic():
        IssueFindingLink.objects.bulk_create(
            [IssueFindingLink(issue=link_world.issue, consensus_finding=link_world.consensus, overlap=0.1)]
        )


def test_one_issue_may_link_to_several_consensus_findings_and_back(link_world):
    second = kit.make_consensus(link_world.world.panel, link_world.world.message, start=10, end=20)
    other_issue = kit.make_issue(link_world.run, link_world.world.message)
    make_link(link_world)
    make_link(link_world, consensus_finding=second)
    make_link(link_world, issue=other_issue)


def test_a_linked_issue_cannot_be_deleted(link_world):
    make_link(link_world)
    with pytest.raises(ProtectedError):
        link_world.issue.delete()


def test_a_linked_consensus_finding_cannot_be_deleted(link_world):
    make_link(link_world)
    with pytest.raises(ProtectedError):
        link_world.consensus.delete()
