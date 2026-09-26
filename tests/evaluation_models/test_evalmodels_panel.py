"""evaluation.Panel: fields, defaults from the tunables, uniqueness on (name, version), the raters many-to-many."""
import evalmodels_testkit as kit
import pytest
from config import tunables
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db


def test_the_model_has_the_contract_fields():
    from evaluation.models import Panel

    assert {"name", "version", "dimensions", "span_match_min_iou", "intensity_disagreement_threshold"} <= kit.field_names(Panel)
    assert "raters" in {f.name for f in Panel._meta.many_to_many}


def test_the_consensus_rule_defaults_are_the_tunables():
    assert tunables.SPAN_MATCH_MIN_IOU == 0.5 and tunables.INTENSITY_DISAGREEMENT_THRESHOLD == 2
    panel = kit.make_panel()
    panel.refresh_from_db()
    assert panel.span_match_min_iou == tunables.SPAN_MATCH_MIN_IOU
    assert panel.intensity_disagreement_threshold == tunables.INTENSITY_DISAGREEMENT_THRESHOLD


def test_the_defaults_follow_a_changed_tunable(settings):
    """The field defaults are callables that read the tunables, so they are never pinned to a constant."""
    from evaluation.models import Panel

    settings.SPAN_MATCH_MIN_IOU = 0.7
    settings.INTENSITY_DISAGREEMENT_THRESHOLD = 3
    panel = Panel()
    assert panel.span_match_min_iou == 0.7
    assert panel.intensity_disagreement_threshold == 3


def test_the_consensus_rule_can_be_set_per_panel():
    panel = kit.make_panel(span_match_min_iou=0.8, intensity_disagreement_threshold=3)
    panel.refresh_from_db()
    assert (panel.span_match_min_iou, panel.intensity_disagreement_threshold) == (0.8, 3)


def test_dimensions_round_trip_with_rubric_versions_and_hashes():
    dims = {
        "factual_accuracy": {"rubric_version": "v1", "sha256": "ab" * 32},
        "abusiveness": {"rubric_version": "v1", "sha256": "cd" * 32},
    }
    panel = kit.make_panel(dimensions=dims)
    panel.refresh_from_db()
    assert panel.dimensions == dims


def test_name_and_version_are_unique_together():
    kit.make_panel(name="p", version=1)
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_panel(name="p", version=1)


def test_a_new_version_of_a_name_is_allowed_and_so_is_the_same_version_under_another_name():
    from evaluation.models import Panel

    kit.make_panel(name="p", version=1)
    kit.make_panel(name="p", version=2)
    kit.make_panel(name="q", version=1)
    assert Panel.objects.count() == 3


def test_uniqueness_holds_at_database_level():
    from evaluation.models import Panel

    kit.make_panel(name="p", version=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Panel.objects.bulk_create([Panel(name="p", version=1)])


def test_raters_are_a_many_to_many_shared_between_panels():
    a, b, c = kit.make_rater("llm"), kit.make_rater("llm"), kit.make_rater("human")
    first = kit.make_panel([a, b], name="first")
    second = kit.make_panel([b, c], name="second")
    assert set(first.raters.all()) == {a, b}
    assert set(second.raters.all()) == {b, c}


def test_a_rater_in_a_panel_cannot_be_deleted():
    """Nothing cascades silently: a panel's membership is part of the record of how a consensus was reached."""
    from django.db.models import ProtectedError

    rater = kit.make_rater("llm")
    panel = kit.make_panel([rater])
    with pytest.raises(ProtectedError):
        rater.delete()
    assert set(panel.raters.all()) == {rater}


def test_a_panel_with_raters_cannot_be_deleted():
    from django.db.models import ProtectedError

    panel = kit.make_panel([kit.make_rater("llm")])
    with pytest.raises(ProtectedError):
        panel.delete()


# --- the consensus rule must be usable (builder's reading of 'consensus rule fields'; the brief gives no range) ------------
@pytest.mark.parametrize("value", [0, -0.5, 1.01, 2])
def test_the_span_match_threshold_must_be_above_zero_and_at_most_one(value):
    from evaluation.models import Panel

    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_panel(span_match_min_iou=value)
    with pytest.raises(IntegrityError), transaction.atomic():
        Panel.objects.bulk_create([Panel(name=f"bad{kit.n()}", version=1, span_match_min_iou=value)])


@pytest.mark.parametrize("value", [0.01, 0.5, 1, 1.0])
def test_a_usable_span_match_threshold_is_accepted(value):
    assert kit.make_panel(span_match_min_iou=value).span_match_min_iou == value


def test_the_disagreement_threshold_must_be_at_least_one():
    from evaluation.models import Panel

    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_panel(intensity_disagreement_threshold=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        Panel.objects.bulk_create([Panel(name=f"bad{kit.n()}", version=1, intensity_disagreement_threshold=0)])


def test_the_dimensions_of_a_panel_must_be_taxonomy_dimensions():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        kit.make_panel(dimensions={"sarcasm": {"rubric_version": "v1", "sha256": "ab"}})
