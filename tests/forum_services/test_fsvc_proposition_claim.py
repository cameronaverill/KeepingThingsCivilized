"""Step 7c: create_proposition stores the typed text as the claim: leading "My position is that" removed (any case, whole
words only) and the first letter upper-cased; the limit applies to the stored claim."""
import pytest

from fsvc_testkit import counts, make_prop, make_user, rejection, svc

pytestmark = pytest.mark.django_db


def stored(text):
    return svc().create_proposition(make_user(), text).proposition


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("cats make better pets than dogs", "Cats make better pets than dogs"),
        ("Cats make better pets than dogs", "Cats make better pets than dogs"),
        ("my position is that cats make better pets", "Cats make better pets"),
        ("My position is that cats make better pets", "Cats make better pets"),
        ("MY POSITION IS THAT cats make better pets", "Cats make better pets"),
        ("mY pOsItIoN iS tHaT rents are too high", "Rents are too high"),
        ("  My position is that   rents  are\ttoo high  ", "Rents are too high"),
        ("My position is that\nrents are too high", "Rents are too high"),
        ("my position is that my position is that rents", "My position is that rents"),
        ("1 in 3 renters cannot afford a home", "1 in 3 renters cannot afford a home"),
        ("élections should be held on weekends", "Élections should be held on weekends"),
    ],
)
def test_the_claim_is_stripped_of_the_fixed_start_and_capitalised(typed, expected):
    assert stored(typed) == expected


@pytest.mark.parametrize("typed", ["My position is thatcher was right", "My position is that's odd", "my position is thatx"])
def test_the_start_is_only_removed_as_whole_words(typed):
    result = stored(typed)
    assert result.lower().startswith("my position is that")


def test_the_stored_claim_never_contains_the_fixed_start_at_its_beginning():
    for i, typed in enumerate(("my position is that x is true", "MY POSITION IS THAT y is true", "My Position Is That z is true")):
        assert not stored(typed).lower().startswith("my position is that"), i


def test_only_the_start_is_removed_the_phrase_elsewhere_is_kept():
    assert stored("people say that my position is that rents are high") == "People say that my position is that rents are high"


@pytest.mark.parametrize("typed", ["My position is that", "my position is that   ", "MY POSITION IS THAT\n\t", "   "])
def test_nothing_after_the_start_is_an_empty_proposition(typed):
    before = counts()
    exc = rejection(svc().create_proposition, make_user(), typed)
    assert exc.code == "empty"
    assert counts() == before


def test_the_limit_applies_to_the_stored_claim_not_the_typed_text():
    assert len(stored("My position is that " + "a" * 200)) == 200
    exc = rejection(svc().create_proposition, make_user(), "My position is that " + "a" * 201)
    assert exc.code == "too_long" and "201" in exc.message and "200" in exc.message


def test_a_typed_start_does_not_count_against_the_limit_at_the_boundary():
    assert svc().create_proposition(make_user(), "my position is that " + "b" * 200)
    assert svc().create_proposition(make_user(), "c" * 200)
    assert rejection(svc().create_proposition, make_user(), "d" * 201).code == "too_long"


def test_the_duplicate_check_compares_the_stored_claims():
    first = svc().create_proposition(make_user(), "cats make better pets than dogs")
    for typed in ("My position is that cats make better pets than dogs", "CATS MAKE BETTER PETS THAN DOGS", "Cats make better pets than dogs"):
        exc = rejection(svc().create_proposition, make_user(), typed)
        assert exc.code == "duplicate" and exc.details["topic_id"] == first.pk


def test_the_duplicate_check_meets_a_seeded_proposition_typed_with_the_fixed_start():
    seeded = make_prop("Rents should be capped", title="Rent control")
    exc = rejection(svc().create_proposition, make_user(), "my position is that rents should be capped")
    assert exc.details["topic_id"] == seeded.pk


def test_a_user_created_proposition_has_no_opposing_position():
    topic = svc().create_proposition(make_user(), "my position is that trains should be free")
    assert topic.opposing_position == ""
    from forum.models import Topic

    assert Topic.objects.get(pk=topic.pk).opposing_position == ""


def test_the_stored_claim_keeps_its_case_apart_from_the_first_letter():
    assert stored("my position is that NASA should fund Mars trips") == "NASA should fund Mars trips"
    assert stored("iPhones should be repairable") == "IPhones should be repairable"
