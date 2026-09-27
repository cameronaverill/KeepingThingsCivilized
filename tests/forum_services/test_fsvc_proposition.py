"""create_proposition: normalisation, the 200-character limit, duplicates, the per-user UTC-day limit (plan section 2,
step 7 brief)."""
import pytest

from fsvc_testkit import (
    assert_plain, at, counts, make_prop, make_user, rejection, svc, uniq,
)  # fmt: skip

pytestmark = pytest.mark.django_db


def text_of(n, ch="a"):
    return ch * n


# --- what is created ---------------------------------------------------------------------------------------------------


def test_a_proposition_is_created_visible_and_attributed():
    user = make_user()
    topic = svc().create_proposition(user, "Cats make better pets than dogs")
    from forum.models import Topic

    stored = Topic.objects.get(pk=topic.pk)
    assert stored.proposition == "Cats make better pets than dogs"
    assert stored.created_by_id == user.pk
    assert stored.hidden is False
    assert stored.title == ""
    assert stored.description == ""
    assert stored.leans == {}
    assert stored.created_at is not None


def test_the_text_is_stripped_whitespace_collapsed_and_nfc_normalised():
    user = make_user()
    topic = svc().create_proposition(user, "  Cats   make\nbetter\t\tpets \r\n than dogs   ")
    assert topic.proposition == "Cats make better pets than dogs"
    composed = svc().create_proposition(user, "caf" + "é is nicer than tea")
    assert composed.proposition == "Café is nicer than tea"


def test_case_and_punctuation_are_kept_as_typed():
    topic = svc().create_proposition(make_user(), "Rents Should Be Capped, Everywhere!")
    assert topic.proposition == "Rents Should Be Capped, Everywhere!"


def test_the_topic_appears_in_the_database_immediately_with_no_review_step():
    from forum.models import Topic

    topic = svc().create_proposition(make_user(), "Trains should be free")
    assert Topic.objects.filter(pk=topic.pk, hidden=False).exists()


# --- empty --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t \r\n", "   "])
def test_an_empty_proposition_is_refused_with_the_empty_code(text):
    before = counts()
    exc = rejection(svc().create_proposition, make_user(), text)
    assert exc.code == "empty"
    assert "empty" in exc.message.lower()
    assert_plain(exc)
    assert counts() == before


# --- too long: 200 and 201 ------------------------------------------------------------------------------------------------


def test_exactly_the_limit_is_accepted():
    topic = svc().create_proposition(make_user(), text_of(200))
    assert len(topic.proposition) == 200


def test_one_over_the_limit_is_refused_naming_the_count_the_limit_and_the_excess():
    before = counts()
    exc = rejection(svc().create_proposition, make_user(), text_of(201))
    assert exc.code == "too_long"
    assert "201" in exc.message and "200" in exc.message
    assert "proposition" in exc.message.lower()
    assert "shorten" in exc.message.lower()
    assert "1 character" in exc.message
    assert_plain(exc)
    assert counts() == before


def test_the_message_states_the_real_excess_for_a_long_text():
    exc = rejection(svc().create_proposition, make_user(), text_of(450))
    assert "450" in exc.message and "250" in exc.message


def test_length_is_counted_on_the_normalised_text_not_the_raw_text():
    # 200 characters once whitespace is collapsed, far more as typed.
    raw = "  " + ("a" * 99 + "   ") + ("b" * 99) + "   \n\n  "
    topic = svc().create_proposition(make_user(), raw)
    assert len(topic.proposition) == 99 + 1 + 99


def test_length_counts_code_points_not_bytes_or_utf16_units():
    assert len(svc().create_proposition(make_user(), "\U0001F600" * 200).proposition) == 200
    exc = rejection(svc().create_proposition, make_user(), "\U0001F600" * 201)
    assert exc.code == "too_long"
    composed = svc().create_proposition(make_user(), "é" * 200)  # 200 characters once composed
    assert len(composed.proposition) == 200


def test_the_limit_and_its_number_come_from_settings(settings):
    settings.MAX_PROPOSITION_CHARS = 12
    assert svc().create_proposition(make_user(), "x" * 12)
    exc = rejection(svc().create_proposition, make_user(), "y" * 30)
    assert "12" in exc.message and "30" in exc.message and "18" in exc.message
    assert "200" not in exc.message


# --- duplicates ---------------------------------------------------------------------------------------------------------


def test_a_duplicate_of_an_existing_proposition_is_refused_with_its_topic_id():
    first = svc().create_proposition(make_user(), "Cats make better pets than dogs")
    before = counts()
    exc = rejection(svc().create_proposition, make_user(), "Cats make better pets than dogs")
    assert exc.code == "duplicate"
    assert exc.details["topic_id"] == first.pk
    assert "already exists" in exc.message
    assert "Choose it from the list" in exc.message
    assert_plain(exc)
    assert counts() == before


@pytest.mark.parametrize(
    "variant",
    [
        "CATS MAKE BETTER PETS THAN DOGS",
        "cats   make\tbetter\npets than   dogs",
        "  Cats make better pets than dogs  ",
        "ＣＡＴＳ make better pets than dogs",  # full-width letters (NFKC)
    ],
)
def test_duplicates_match_after_nfkc_casefold_and_whitespace(variant):
    first = svc().create_proposition(make_user(), "Cats make better pets than dogs")
    exc = rejection(svc().create_proposition, make_user(), variant)
    assert exc.code == "duplicate"
    assert exc.details["topic_id"] == first.pk


def test_casefold_goes_beyond_lower_the_german_sharp_s():
    first = svc().create_proposition(make_user(), "Straße rents are too high")
    exc = rejection(svc().create_proposition, make_user(), "STRASSE rents are too high")
    assert exc.code == "duplicate" and exc.details["topic_id"] == first.pk


def test_composed_and_decomposed_accents_are_the_same_proposition():
    first = svc().create_proposition(make_user(), "Café prices are too high")
    exc = rejection(svc().create_proposition, make_user(), "Café prices are too high")
    assert exc.code == "duplicate" and exc.details["topic_id"] == first.pk


def test_a_different_text_is_not_a_duplicate():
    svc().create_proposition(make_user(), "Cats make better pets than dogs")
    other = svc().create_proposition(make_user(), "Cats make better pets than dogs.")  # punctuation differs
    assert other.pk


def test_a_seeded_proposition_counts_for_the_duplicate_check():
    seeded = make_prop("Rents should be capped", title="Rent control")
    exc = rejection(svc().create_proposition, make_user(), "rents should be CAPPED")
    assert exc.code == "duplicate" and exc.details["topic_id"] == seeded.pk


def test_the_same_user_repeating_a_proposition_is_a_duplicate_too():
    user = make_user()
    svc().create_proposition(user, "Trains should be free")
    assert rejection(svc().create_proposition, user, "trains should be free").code == "duplicate"


def test_a_hidden_proposition_does_not_block_a_new_identical_one():
    hidden = make_prop("Trains should be free", hidden=True)
    new = svc().create_proposition(make_user(), "Trains should be free")
    assert new.pk != hidden.pk


def test_a_duplicate_is_refused_even_when_the_user_is_over_the_daily_limit_or_it_is_the_daily_limit(settings):
    """Both codes are true then; the contract lists duplicate before daily_limit but pins no test on the overlap.
    Whichever is reported, nothing is created and the message is plain."""
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 1
    user = make_user()
    svc().create_proposition(user, "Trains should be free")
    before = counts()
    exc = rejection(svc().create_proposition, user, "trains should be free")
    assert exc.code in ("duplicate", "daily_limit")
    assert_plain(exc)
    assert counts() == before


# --- daily limit ----------------------------------------------------------------------------------------------------------


def test_the_default_daily_limit_is_twenty(settings):
    assert settings.MAX_PROPOSITIONS_PER_USER_PER_DAY == 20
    assert settings.MAX_PROPOSITION_CHARS == 200


def test_the_twentieth_proposition_is_accepted_and_the_twenty_first_refused(clock):
    user = make_user()
    for i in range(20):
        assert svc().create_proposition(user, f"Proposition number {i} is worth debating {uniq('p')}")
    before = counts()
    exc = rejection(svc().create_proposition, user, "One more proposition than allowed")
    assert exc.code == "daily_limit"
    assert "You have created 20 propositions today" in exc.message
    assert "daily limit" in exc.message
    assert "Try again tomorrow" in exc.message and "UTC" in exc.message
    assert "existing proposition" in exc.message
    assert_plain(exc)
    assert counts() == before


def test_the_limit_and_its_number_come_from_settings_for_the_daily_limit(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 3
    user = make_user()
    for i in range(3):
        svc().create_proposition(user, f"Small limit proposition {i}")
    exc = rejection(svc().create_proposition, user, "Small limit proposition four")
    assert exc.code == "daily_limit"
    assert "3 propositions today" in exc.message
    assert "20" not in exc.message


def test_the_limit_is_per_user(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    first, second = make_user(), make_user()
    for i in range(2):
        svc().create_proposition(first, f"First user proposition {i}")
    assert rejection(svc().create_proposition, first, "First user proposition x").code == "daily_limit"
    assert svc().create_proposition(second, "Second user proposition 0")


def test_seeded_and_other_peoples_propositions_do_not_use_up_a_users_allowance(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    for i in range(5):
        make_prop(f"Seeded proposition {i}")
    other = make_user()
    for i in range(2):
        svc().create_proposition(other, f"Other person proposition {i}")
    user = make_user()
    assert svc().create_proposition(user, "My first proposition")
    assert svc().create_proposition(user, "My second proposition")


def test_hidden_propositions_still_count_toward_the_daily_limit(settings, clock):
    """Reading: the count is over Topic.created_by / created_at with no visibility filter, so an admin hiding a
    proposition does not hand the user a fresh allowance."""
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    user = make_user()
    first = svc().create_proposition(user, "Hidden later one")
    svc().create_proposition(user, "Visible later two")
    from forum.models import Topic

    Topic.objects.filter(pk=first.pk).update(hidden=True)
    assert rejection(svc().create_proposition, user, "Third attempt today").code == "daily_limit"


def test_the_allowance_resets_at_utc_midnight(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 3
    user = make_user()
    clock.set(at(10, 23, 59, 30))
    for i in range(3):
        svc().create_proposition(user, f"Late night proposition {i}")
    clock.set(at(10, 23, 59, 59, 999999))
    assert rejection(svc().create_proposition, user, "Still the same day").code == "daily_limit"
    clock.set(at(11, 0, 0, 0))
    assert svc().create_proposition(user, "First proposition of the new day")


def test_propositions_created_at_exactly_midnight_belong_to_the_new_day(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 3
    user = make_user()
    clock.set(at(11, 0, 0, 0))
    for i in range(3):
        svc().create_proposition(user, f"Midnight proposition {i}")
    clock.set(at(11, 23, 59, 59))
    assert rejection(svc().create_proposition, user, "Too many for the eleventh").code == "daily_limit"
    clock.set(at(12, 0, 0, 0))
    assert svc().create_proposition(user, "Fine on the twelfth")


def test_yesterdays_propositions_do_not_count_today(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    user = make_user()
    clock.set(at(10, 23, 59, 58))
    svc().create_proposition(user, "Yesterday one")
    svc().create_proposition(user, "Yesterday two")
    clock.set(at(11, 0, 0, 1))
    assert svc().create_proposition(user, "Today one")
    assert svc().create_proposition(user, "Today two")
    assert rejection(svc().create_proposition, user, "Today three").code == "daily_limit"


def test_the_day_is_a_utc_day_not_a_local_day(settings, clock):
    """Late evening UTC on the 10th is already the 11th in Tokyo; the count must still be per UTC day."""
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    user = make_user()
    clock.set(at(10, 20, 0, 0))  # 05:00 on the 11th in Tokyo
    svc().create_proposition(user, "Evening one")
    svc().create_proposition(user, "Evening two")
    clock.set(at(10, 23, 0, 0))  # still the 10th in UTC
    assert rejection(svc().create_proposition, user, "Evening three").code == "daily_limit"


def test_a_refused_proposition_creates_no_conversation_or_message():
    before = counts()
    rejection(svc().create_proposition, make_user(), "")
    assert counts() == before


def test_the_daily_limit_says_how_long_until_utc_midnight(settings, clock):
    """Ruling: retry_after for daily_limit is the whole seconds until the next UTC midnight."""
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 1
    user = make_user()
    clock.set(at(10, 12, 0, 0))
    svc().create_proposition(user, "The only one today")
    assert rejection(svc().create_proposition, user, "A second one today").retry_after == 12 * 3600
    clock.set(at(10, 23, 59, 30))
    assert rejection(svc().create_proposition, user, "A second one today").retry_after == 30
    clock.set(at(10, 23, 59, 59, 500000))
    assert rejection(svc().create_proposition, user, "A second one today").retry_after == 1


def test_a_proposition_refusal_other_than_the_daily_limit_has_no_retry_after():
    exc = rejection(svc().create_proposition, make_user(), "")
    assert exc.retry_after is None
    exc = rejection(svc().create_proposition, make_user(), "x" * 300)
    assert exc.retry_after is None


# --- added after the mutation pass -----------------------------------------------------------------------------------------


def test_duplicates_ignore_extra_whitespace_inside_an_existing_seeded_proposition():
    """The stored side of the comparison is normalised too: a seeded text with doubled spaces still matches."""
    seeded = make_prop("Rents   should\tbe  capped", title="Rent control")
    exc = rejection(svc().create_proposition, make_user(), "Rents should be capped")
    assert exc.code == "duplicate" and exc.details["topic_id"] == seeded.pk


def test_a_proposition_dated_in_the_future_does_not_count_against_today(settings, clock):
    """The count is bounded on both sides (the UTC day), so a row from a later day (the clock was moved back) is not
    today's."""
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 2
    user = make_user()
    clock.set(at(11, 10, 0, 0))
    svc().create_proposition(user, "Made on the eleventh one")
    svc().create_proposition(user, "Made on the eleventh two")
    clock.set(at(10, 10, 0, 0))
    assert svc().create_proposition(user, "Made on the tenth")


def test_retry_after_for_the_daily_limit_rounds_up_to_whole_seconds(settings, clock):
    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 1
    user = make_user()
    clock.set(at(10, 12, 0, 0))
    svc().create_proposition(user, "The only one today")
    clock.set(at(10, 23, 59, 58, 500000))  # 1.5 seconds to midnight
    assert rejection(svc().create_proposition, user, "A second one today").retry_after == 2


def test_the_utc_day_is_used_even_when_the_clock_speaks_another_time_zone(settings, clock):
    from datetime import timedelta, timezone

    settings.MAX_PROPOSITIONS_PER_USER_PER_DAY = 1
    user = make_user()
    tokyo = timezone(timedelta(hours=9))
    clock.set(at(10, 10, 0, 0))  # 10:00 UTC on the 10th
    svc().create_proposition(user, "First one of the tenth")
    # 05:00 on the 11th in Tokyo is 20:00 UTC on the 10th: still the same UTC day.
    clock.set(at(11, 5, 0, 0).replace(tzinfo=tokyo))
    assert rejection(svc().create_proposition, user, "Second one of the tenth").code == "daily_limit"
