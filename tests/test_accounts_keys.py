"""accounts.keys.normalize_key and accounts.validators.validate_username (step 1 fixes, section A)."""
import unicodedata

import pytest
from django.core.exceptions import ValidationError

from config import tunables

# Written as escapes so the source is unambiguous about which code points are meant.
KELVIN = "\u212a"  # KELVIN SIGN, casefolds to "k"
FULLWIDTH_ALICE = "\uff21\uff4c\uff29\uff23\uff25"  # fullwidth "AlICE"
E_ACUTE = "\u00e9"
E_PLUS_ACUTE = "e\u0301"


def normalize_key(value):
    from accounts.keys import normalize_key as function

    return function(value)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("Alice", "alice"),
        ("  Alice  ", "alice"),
        ("\talice\n", "alice"),
        ("", ""),
        ("Stra\u00dfe", "strasse"),  # sharp s folds to "ss"
        ("STRASSE", "strasse"),
        ("\u00c9mile", "\u00e9mile"),
        (FULLWIDTH_ALICE, "alice"),  # NFKC folds fullwidth forms
        (KELVIN, "k"),
        ("\u03a3", "\u03c3"),  # capital sigma
        ("\u03c2", "\u03c3"),  # final sigma casefolds to plain sigma
        ("\u0130", "i\u0307"),  # dotted capital I: "i" plus a combining dot, NOT plain "i"
        ("\ufb01", "fi"),  # "fi" ligature
    ],
)
def test_normalize_key_examples(value, expected):
    assert normalize_key(value) == expected


@pytest.mark.parametrize(
    "a, b",
    [
        ("Alice", "alice"),
        ("\u00c9mile", "\u00e9mile"),
        ("Stra\u00dfe", "STRASSE"),
        ("\u03a3", "\u03c2"),
        (KELVIN, "K"),
        (KELVIN, "k"),
        (FULLWIDTH_ALICE, "alice"),
        (E_ACUTE, E_PLUS_ACUTE),  # precomposed versus decomposed
        ("Alice@Example.COM", "alice@example.com"),
    ],
)
def test_lookalike_values_share_one_key(a, b):
    assert normalize_key(a) == normalize_key(b)


def test_different_values_get_different_keys():
    assert normalize_key("alice") != normalize_key("alice2")
    assert normalize_key("a-b") != normalize_key("a_b")
    assert normalize_key("\u0130") != normalize_key("i")


def test_normalize_key_is_nfkc_of_casefold_of_nfkc_of_the_stripped_value():
    for value in ("  \u1e9e\u00c5ngstr\u00f6m ", "\u2126", "\ufb00", "Ab\u2168", KELVIN + "elvin", "\u0345\u03a3"):
        stripped = unicodedata.normalize("NFKC", value.strip())
        expected = unicodedata.normalize("NFKC", stripped.casefold())
        assert normalize_key(value) == expected


def test_normalize_key_is_idempotent_and_returns_a_str():
    for value in ("Alice", "Stra\u00dfe", KELVIN, "\u0130", FULLWIDTH_ALICE, "\u03a3\u03c2"):
        once = normalize_key(value)
        assert isinstance(once, str)
        assert normalize_key(once) == once


# ---------------------------------------------------------------------------------------------------------------------


def validate_username(value):
    from accounts.validators import validate_username as function

    return function(value)


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "Alice",
        "a_b-c",
        "user_123",
        "-_-",
        "A" * tunables.USERNAME_MIN_LENGTH,
        "A" * tunables.USERNAME_MAX_LENGTH,
        "0" * tunables.USERNAME_MAX_LENGTH,
    ],
)
def test_valid_usernames_pass(value):
    assert validate_username(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * (tunables.USERNAME_MIN_LENGTH - 1),
        "a" * (tunables.USERNAME_MAX_LENGTH + 1),
        "has space",
        " leading",
        "trailing ",
        "dot.name",
        "at@name",
        "plus+name",
        "\u00c9mile",  # non-ASCII letter
        "stra\u00dfe",
        KELVIN * 3,
        FULLWIDTH_ALICE,
        "\u0661\u0662\u0663",  # Arabic-Indic digits: str.isdigit() is True, but they are not ASCII
        "caf" + E_PLUS_ACUTE,
        "abc\n",  # `$` in a regex would let a trailing newline through
        "ab\u200bc",  # zero-width space
    ],
)
def test_invalid_usernames_raise_validation_error(value):
    with pytest.raises(ValidationError):
        validate_username(value)


def test_the_length_limits_come_from_the_tunables():
    # 3 and 30 are the agreed values; the validator must follow config/tunables.py, not hard-code them.
    assert (tunables.USERNAME_MIN_LENGTH, tunables.USERNAME_MAX_LENGTH) == (3, 30)
    validate_username("a" * tunables.USERNAME_MIN_LENGTH)
    validate_username("a" * tunables.USERNAME_MAX_LENGTH)
    with pytest.raises(ValidationError):
        validate_username("a" * (tunables.USERNAME_MIN_LENGTH - 1))
    with pytest.raises(ValidationError):
        validate_username("a" * (tunables.USERNAME_MAX_LENGTH + 1))
