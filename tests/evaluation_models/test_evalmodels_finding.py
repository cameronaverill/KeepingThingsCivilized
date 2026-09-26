"""evaluation.Finding at model level: quote == target_text[start:end] (exact, one off, unicode, emoji), the span bounds,
intensity 0 to 4 or null with a not-scorable reason (and vice versa), taxonomy dimensions, covered dimensions,
local_id uniqueness per rating, overlap of findings on different dimensions."""
import evalmodels_testkit as kit
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

pytestmark = pytest.mark.django_db

TEXT = "The city has 40 parks and all of them are closed. Nobody could go."


@pytest.fixture
def world():
    message, conv = kit.message_with_text(TEXT)
    rater = kit.make_rater("llm")
    rating = kit.make_rating(rater, message)
    return type("World", (), dict(message=message, conv=conv, rater=rater, rating=rating))


def refused(rating, start, end, **kwargs):
    with pytest.raises(ValidationError), transaction.atomic():
        kit.make_finding(rating, start, end, **kwargs)


# --- fields ------------------------------------------------------------------------------------------------------------
def test_the_model_has_the_contract_fields():
    from evaluation.models import Finding

    assert {"rating", "local_id", "dimension", "start", "end", "quote", "intensity", "not_scorable_reason", "confidence", "detail"} <= kit.field_names(Finding)


def test_choices_and_defaults():
    from evaluation.models import Finding

    assert kit.choice_values(Finding, "not_scorable_reason") - {""} == {"unverifiable", "contested", "needs_context"}
    assert Finding._meta.get_field("intensity").null is True
    assert Finding._meta.get_field("confidence").null is True
    assert isinstance(Finding._meta.get_field("detail"), models.JSONField)


def test_a_finding_round_trips(world):
    from evaluation.models import Finding

    finding = kit.make_finding(world.rating, 4, 8, local_id="f-1", intensity=3, confidence=0.75, detail={"claim": "x"})
    loaded = Finding.objects.get(pk=finding.pk)
    assert (loaded.rating_id, loaded.local_id, loaded.dimension, loaded.start, loaded.end, loaded.quote) == (
        world.rating.pk, "f-1", "factual_accuracy", 4, 8, "city",
    )  # fmt: skip
    assert (loaded.intensity, loaded.confidence, loaded.detail) == (3, 0.75, {"claim": "x"})
    assert not loaded.not_scorable_reason


def test_confidence_and_detail_are_optional(world):
    finding = kit.make_finding(world.rating, 4, 8)
    finding.refresh_from_db()
    assert finding.confidence is None and finding.detail == {}


# --- quote equals the text slice --------------------------------------------------------------------------------------
def test_an_exact_quote_is_accepted(world):
    finding = kit.make_finding(world.rating, 0, len(TEXT))
    assert finding.quote == TEXT


def test_a_quote_at_the_very_end_is_accepted(world):
    kit.make_finding(world.rating, len(TEXT) - 3, len(TEXT))


@pytest.mark.parametrize(
    "start,end",
    [(4, 8), (0, 3), (12, 14), (0, len(TEXT))],
)
def test_the_slice_of_the_text_is_accepted(world, start, end):
    assert kit.make_finding(world.rating, start, end).quote == TEXT[start:end]


def test_a_quote_one_character_short_is_refused(world):
    refused(world.rating, 4, 8, quote="cit")


def test_a_quote_one_character_long_is_refused(world):
    refused(world.rating, 4, 8, quote="city ")


def test_a_quote_shifted_by_one_is_refused(world):
    refused(world.rating, 5, 9, quote="city")  # "city" is at 4:8, not 5:9


def test_a_quote_with_the_wrong_case_is_refused(world):
    refused(world.rating, 4, 8, quote="City")


def test_a_quote_that_differs_only_in_trailing_whitespace_is_refused(world):
    refused(world.rating, 4, 8, quote="city ")
    refused(world.rating, 3, 8, quote="city")  # slice is " city"


def test_an_empty_quote_is_refused(world):
    refused(world.rating, 4, 8, quote="")


def test_a_quote_from_elsewhere_in_the_text_is_refused(world):
    refused(world.rating, 4, 8, quote="park")


def test_offsets_count_from_the_raw_text_including_leading_whitespace():
    message, _ = kit.message_with_text("   leading space then a claim")
    rating = kit.make_rating(kit.make_rater("llm"), message)
    assert kit.make_finding(rating, 3, 10).quote == "leading"
    refused(rating, 0, 7, quote="leading")


def test_the_quote_is_checked_against_an_intervention_acts_text():
    text = "Please note that the tax rose in 2020."
    act = kit.act_with_text(text)
    rating = kit.make_rating(kit.make_rater("human"), act)
    start = text.index("tax")
    assert kit.make_finding(rating, start, start + 3).quote == "tax"
    refused(rating, start, start + 3, quote="rose")


@pytest.mark.parametrize(
    "text,needle",
    [
        ("Prices rose énormously in the café last week", "café"),
        ("Prices rose \U0001F600 sharply, owners said", "\U0001F600"),
        ("Prices rose \U0001F600\U0001F600 sharply", "\U0001F600\U0001F600 sharply"),
        ("Family \U0001F468‍\U0001F469‍\U0001F467 said no", "\U0001F468‍\U0001F469‍\U0001F467"),
        ("Café owners said the rent rose", "Café"),
        ("日本語のテキスト is 100% wrong", "100% wrong"),
    ],
    ids=["accented", "emoji", "two-emoji", "zwj-emoji-sequence", "combining-mark", "cjk"],
)
def test_offsets_are_code_point_offsets_for_unicode_text(text, needle):
    message, _ = kit.message_with_text(text)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    start = text.index(needle)
    finding = kit.make_finding(rating, start, start + len(needle))
    assert finding.quote == needle


def test_an_emoji_quote_one_code_point_off_is_refused():
    text = "Prices rose \U0001F600 sharply, owners said"
    message, _ = kit.message_with_text(text)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    start = text.index("\U0001F600")
    refused(rating, start, start + 1, quote="\U0001F600 ")
    refused(rating, start + 1, start + 2, quote="\U0001F600")


def test_a_composed_quote_does_not_match_decomposed_text():
    """No Unicode normalisation: the quote must be exactly the slice of the stored text."""
    text = "Café owners said no"
    message, _ = kit.message_with_text(text)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    refused(rating, 0, 5, quote="Café")  # same text after NFC, but not the same characters as text[0:5]
    assert kit.make_finding(rating, 0, 5).quote == "Café"


def test_a_utf16_style_offset_is_wrong_for_astral_characters():
    """An emoji is one code point, so an offset counted in UTF-16 units (2 per emoji) is refused."""
    text = "\U0001F600 wrong at the start"
    message, _ = kit.message_with_text(text)
    rating = kit.make_rating(kit.make_rater("llm"), message)
    refused(rating, 3, 8, quote="wrong")  # UTF-16 offsets; the code point offsets are 2:7
    assert kit.make_finding(rating, 2, 7).quote == "wrong"


def test_a_saved_finding_is_revalidated_on_update(world):
    finding = kit.make_finding(world.rating, 4, 8)
    finding.end = 9
    with pytest.raises(ValidationError):
        finding.save()
    finding.refresh_from_db()
    assert finding.end == 8


# --- span bounds -------------------------------------------------------------------------------------------------------
def test_start_must_be_before_end(world):
    refused(world.rating, 8, 4, quote="")
    refused(world.rating, 4, 4, quote="")


def test_start_cannot_be_negative(world):
    refused(world.rating, -1, 3, quote=TEXT[-1:3])


def test_end_cannot_pass_the_end_of_the_text(world):
    refused(world.rating, len(TEXT) - 3, len(TEXT) + 1, quote=TEXT[-3:])
    refused(world.rating, 0, len(TEXT) + 5, quote=TEXT)


# --- intensity and not scorable ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [0, 1, 2, 3, 4])
def test_intensity_zero_to_four_is_accepted(world, value):
    assert kit.make_finding(world.rating, 4, 8, intensity=value).intensity == value


@pytest.mark.parametrize("value", [-1, 5, 10])
def test_intensity_outside_zero_to_four_is_refused(world, value):
    refused(world.rating, 4, 8, intensity=value)


@pytest.mark.parametrize("reason", ["unverifiable", "contested", "needs_context"])
def test_a_null_intensity_with_a_reason_is_accepted(world, reason):
    finding = kit.make_finding(world.rating, 4, 8, intensity=None, not_scorable_reason=reason)
    assert finding.intensity is None and finding.not_scorable_reason == reason


def test_a_null_intensity_needs_a_reason(world):
    refused(world.rating, 4, 8, intensity=None, not_scorable_reason="")


def test_a_null_intensity_with_an_unknown_reason_is_refused(world):
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_finding(world.rating, 4, 8, intensity=None, not_scorable_reason="dunno")


@pytest.mark.parametrize("reason", ["unverifiable", "contested", "needs_context"])
def test_a_reason_without_a_null_intensity_is_refused(world, reason):
    refused(world.rating, 4, 8, intensity=2, not_scorable_reason=reason)


def test_intensity_zero_with_a_reason_is_refused(world):
    """0 is a score ('accurate'), not 'not scorable'."""
    refused(world.rating, 4, 8, intensity=0, not_scorable_reason="contested")


# --- dimensions --------------------------------------------------------------------------------------------------------
def test_an_unknown_dimension_is_refused(world):
    with pytest.raises(ValidationError):
        kit.make_finding(world.rating, 4, 8, dimension="sarcasm")


@pytest.mark.parametrize("dimension", ["factual_accuracy", "abusiveness"])
def test_taxonomy_dimensions_are_accepted(world, dimension):
    assert kit.make_finding(world.rating, 4, 8, dimension=dimension).dimension == dimension


def test_a_dimension_the_rating_does_not_cover_is_refused():
    message, _ = kit.message_with_text(TEXT)
    rating = kit.make_rating(kit.make_rater("llm"), message, dimensions=("factual_accuracy",))
    kit.make_finding(rating, 4, 8, dimension="factual_accuracy")
    refused(rating, 4, 8, dimension="abusiveness")


def test_findings_on_different_dimensions_may_overlap(world):
    a = kit.make_finding(world.rating, 4, 30, dimension="factual_accuracy", local_id="a")
    b = kit.make_finding(world.rating, 4, 30, dimension="abusiveness", local_id="b")
    c = kit.make_finding(world.rating, 10, 20, dimension="abusiveness", local_id="c", intensity=1)
    from evaluation.models import Finding

    assert Finding.objects.filter(rating=world.rating).count() == 3 and len({a.pk, b.pk, c.pk}) == 3


# --- local_id ----------------------------------------------------------------------------------------------------------
def test_local_id_is_unique_per_rating(world):
    kit.make_finding(world.rating, 4, 8, local_id="one")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_finding(world.rating, 10, 14, local_id="one")


def test_the_same_local_id_may_be_used_in_another_rating(world):
    other = kit.make_rating(kit.make_rater("llm"), world.message)
    kit.make_finding(world.rating, 4, 8, local_id="one")
    kit.make_finding(other, 4, 8, local_id="one")


def test_local_id_may_not_be_reused_even_on_another_dimension(world):
    kit.make_finding(world.rating, 4, 8, local_id="one", dimension="factual_accuracy")
    with pytest.raises(kit.REFUSED), transaction.atomic():
        kit.make_finding(world.rating, 4, 8, local_id="one", dimension="abusiveness")


def test_a_refused_finding_writes_nothing(world):
    from evaluation.models import Finding

    refused(world.rating, 4, 8, quote="nope")
    assert Finding.objects.count() == 0
