"""The Finding rules at database level: save() is bypassed with bulk_create and queryset update, so only constraints and
triggers can stop a bad row. Single-table rules (span order, intensity, not-scorable, dimension, uniqueness) are check
constraints; the rules that look at the target's text (quote == slice, end <= length) cross tables and need a trigger."""
import evalmodels_testkit as kit
import pytest
from django.db import IntegrityError, connection, transaction

pytestmark = pytest.mark.django_db

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


@pytest.fixture
def world():
    message, conv = kit.message_with_text(TEXT)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    return type("World", (), dict(message=message, rating=rating))


def insert(world, start, end, **kwargs):
    """bulk_create one Finding built without validation."""
    from evaluation.models import Finding

    Finding.objects.bulk_create([kit.unsaved_finding(world.rating, start, end, **kwargs)])


def refused_insert(world, start, end, **kwargs):
    from evaluation.models import Finding

    with pytest.raises(IntegrityError), transaction.atomic():
        insert(world, start, end, **kwargs)
    assert Finding.objects.count() == 0


def test_a_valid_row_gets_through_bulk_create(world):
    from evaluation.models import Finding

    insert(world, 4, 8)
    assert Finding.objects.get().quote == "city"


# --- single-table rules ------------------------------------------------------------------------------------------------
def test_start_must_be_before_end(world):
    refused_insert(world, 8, 4, quote="")
    refused_insert(world, 4, 4, quote="")


def test_start_cannot_be_negative(world):
    refused_insert(world, -1, 3, quote="")


@pytest.mark.parametrize("value", [-1, 5])
def test_intensity_must_be_zero_to_four(world, value):
    refused_insert(world, 4, 8, intensity=value)


def test_a_null_intensity_needs_a_reason(world):
    refused_insert(world, 4, 8, intensity=None, not_scorable_reason="")


def test_a_reason_needs_a_null_intensity(world):
    refused_insert(world, 4, 8, intensity=2, not_scorable_reason="contested")


def test_an_unknown_reason_is_refused(world):
    refused_insert(world, 4, 8, intensity=None, not_scorable_reason="dunno")


def test_an_unknown_dimension_is_refused(world):
    refused_insert(world, 4, 8, dimension="sarcasm")


def test_local_id_is_unique_per_rating(world):
    from evaluation.models import Finding

    insert(world, 4, 8, local_id="x")
    with pytest.raises(IntegrityError), transaction.atomic():
        insert(world, 10, 14, local_id="x")
    assert Finding.objects.count() == 1


@pytest.mark.parametrize(
    "change",
    [dict(intensity=5), dict(start=9), dict(dimension="sarcasm"), dict(not_scorable_reason="contested")],
    ids=["intensity", "start-after-end", "dimension", "reason-with-intensity"],
)
def test_a_queryset_update_cannot_break_a_single_table_rule(world, change):
    from evaluation.models import Finding

    insert(world, 4, 8)
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.update(**change)
    finding = Finding.objects.get()
    assert (finding.start, finding.end, finding.intensity, finding.dimension) == (4, 8, 2, "factual_accuracy")


def test_a_queryset_update_cannot_null_the_intensity_without_a_reason(world):
    from evaluation.models import Finding

    insert(world, 4, 8)
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.update(intensity=None)


# --- rules that look at the target's text (a trigger) --------------------------------------------------------------------
@pytest.mark.parametrize("quote", ["cit", "city ", "City", "park", ""])
def test_a_quote_that_is_not_the_slice_is_refused(world, quote):
    refused_insert(world, 4, 8, quote=quote)


def test_a_span_past_the_end_of_the_text_is_refused(world):
    refused_insert(world, len(TEXT) - 3, len(TEXT) + 1, quote=TEXT[-3:])


def test_a_queryset_update_cannot_move_the_span_away_from_the_quote(world):
    from evaluation.models import Finding

    insert(world, 4, 8)
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.update(start=5, end=9)
    finding = Finding.objects.get()
    assert (finding.start, finding.end, finding.quote) == (4, 8, "city")


def test_a_queryset_update_cannot_change_the_quote(world):
    from evaluation.models import Finding

    insert(world, 4, 8)
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.update(quote="town")


def test_a_raw_insert_with_a_wrong_quote_is_refused(world):
    from evaluation.models import Finding

    finding = kit.unsaved_finding(world.rating, 4, 8, quote="town")
    columns = [f for f in Finding._meta.concrete_fields if not f.primary_key]
    names = ", ".join(connection.ops.quote_name(f.column) for f in columns)
    marks = ", ".join(["%s"] * len(columns))
    values = [f.get_db_prep_save(f.pre_save(finding, True), connection) for f in columns]
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {connection.ops.quote_name(Finding._meta.db_table)} ({names}) VALUES ({marks})", values)
    assert Finding.objects.count() == 0


@pytest.mark.parametrize(
    "text,needle",
    [
        ("Prices rose \U0001F600 sharply, owners said", "\U0001F600"),
        ("Café owners said the rent rose", "Café"),
        ("   leading space then a claim", "leading"),
    ],
    ids=["emoji", "combining", "leading-space"],
)
def test_the_database_slices_by_code_point_like_python(text, needle):
    """SQLite's substr counts characters (code points), the same unit as Python's slice: a correct row gets in, one
    character off does not."""
    from evaluation.models import Finding

    message, _ = kit.message_with_text(text)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    start = text.index(needle)
    Finding.objects.bulk_create([kit.unsaved_finding(rating, start, start + len(needle))])
    assert Finding.objects.get().quote == needle
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.bulk_create([kit.unsaved_finding(rating, start + 1, start + len(needle) + 1, quote=needle, local_id="off")])
    assert Finding.objects.count() == 1


def test_the_quote_rule_also_covers_an_intervention_acts_text():
    from evaluation.models import Finding

    text = "Please note that the tax rose in 2020."
    rating = kit.make_rating(kit.make_rater("human"), kit.act_with_text(text))
    start = text.index("tax")
    Finding.objects.bulk_create([kit.unsaved_finding(rating, start, start + 3)])
    with pytest.raises(IntegrityError), transaction.atomic():
        Finding.objects.bulk_create([kit.unsaved_finding(rating, start, start + 3, quote="rose", local_id="bad")])
    assert Finding.objects.count() == 1
