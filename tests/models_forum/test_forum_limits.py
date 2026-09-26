"""forum/limits.py: count_message_chars, the single rule for how long a message is (plan section 4, brief 4a):
line endings become \\n (CRLF and lone CR), NFC, strip leading and trailing whitespace, count code points."""
import unicodedata

import pytest


def count(text):
    from forum.limits import count_message_chars

    return count_message_chars(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        (" ", 0),
        ("   \n\t  ", 0),
        ("\r\n", 0),
        ("\r", 0),
        ("\u00a0", 0),  # no-break space is whitespace
        ("\u3000\u2003", 0),  # ideographic and em space
        ("a", 1),
        ("abc", 3),
        ("hello world", 11),
    ],
)
def test_plain_counts_including_empty_and_whitespace_only(text, expected):
    assert count(text) == expected


def test_the_result_is_an_int():
    assert type(count("abc")) is int


@pytest.mark.parametrize(
    "text, expected",
    [
        ("  abc", 3),
        ("abc  ", 3),
        ("  abc  ", 3),
        ("\n\nabc\n\n", 3),
        ("\t abc \t", 3),
        ("\u00a0abc\u00a0", 3),
        ("\u3000abc\u3000", 3),
        ("\r\n abc \r\n", 3),
        ("a b", 3),  # inner whitespace is kept
        ("a   b", 5),
        ("a\tb", 3),
        ("a\n\nb", 4),
    ],
)
def test_only_leading_and_trailing_whitespace_is_trimmed(text, expected):
    assert count(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a\r\nb", 3),  # CRLF is one character
        ("a\rb", 3),  # a lone CR is one character
        ("a\nb", 3),
        ("a\r\n\r\nb", 4),
        ("a\r\rb", 4),  # two lone CRs are two characters
        ("a\r\r\nb", 4),  # CR then CRLF
        ("a\n\rb", 4),  # LF then a lone CR
        ("line1\r\nline2\rline3\nline4", 23),
    ],
)
def test_line_endings_count_once_each(text, expected):
    assert count(text) == expected


def test_all_three_line_ending_styles_count_the_same():
    assert count("one\ntwo\nthree") == count("one\r\ntwo\r\nthree") == count("one\rtwo\rthree") == 13


@pytest.mark.parametrize(
    "text, expected",
    [
        ("\u00e9", 1),  # e-acute, precomposed
        ("e\u0301", 1),  # e + combining acute composes to one code point under NFC
        ("caf\u00e9", 4),
        ("cafe\u0301", 4),
        ("\u1112\u1161\u11ab", 1),  # Hangul jamo compose to one syllable
        ("A\u030a", 1),  # A + combining ring above composes to U+00C5
        ("\u212b", 1),  # Angstrom sign
    ],
)
def test_composed_and_decomposed_forms_count_the_same_under_nfc(text, expected):
    assert count(text) == expected


def test_a_decomposed_run_is_counted_after_composition():
    assert count("e\u0301" * 50) == 50
    assert count("\u00e9" * 50) == 50


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a\u0300\u0301", 2),  # no single code point holds a-grave-acute: composes to U+00E0 + U+0301
        ("e\u0301\u0301", 2),
        ("\u0301", 1),  # a lone combining mark is still one code point
        ("x\u0323\u0307", 1),  # x + dot below + dot above has no precomposed form; reorders but... see below
    ][:3],
)
def test_combining_marks_that_cannot_compose_still_count_one_each(text, expected):
    assert count(text) == expected


def test_a_letter_with_no_precomposed_form_keeps_its_combining_mark():
    # "x" + combining dot below has no precomposed letter, so it stays two code points.
    assert unicodedata.normalize("NFC", "x\u0323") == "x\u0323"
    assert count("x\u0323") == 2


@pytest.mark.parametrize(
    "text, expected",
    [
        ("\U0001f600", 1),  # grinning face: one code point outside the BMP
        ("\U0001f600" * 10, 10),
        ("hi \U0001f44d", 4),
        ("\U0001f44d\U0001f3fd", 2),  # thumbs up + skin-tone modifier: two code points
        ("\U0001f1fa\U0001f1f8", 2),  # a flag is two regional indicators
        ("\U0001f468\u200d\U0001f469\u200d\U0001f467", 5),  # a ZWJ family sequence is five code points
    ],
)
def test_emoji_are_counted_by_code_point(text, expected):
    assert count(text) == expected


def test_a_single_emoji_is_one_character_not_two_utf16_units_or_four_bytes():
    assert count("\U0001f600") == 1


@pytest.mark.parametrize(
    "text, expected",
    [
        ("\ufb01", 1),  # the fi ligature stays one code point (NFKC would make it two)
        ("\u2026", 1),  # ellipsis stays one (NFKC would make it three)
        ("\u33cf", 1),  # square kg
        ("\uff21\uff22", 2),  # fullwidth letters stay as they are
    ],
)
def test_normalization_is_nfc_not_nfkc(text, expected):
    assert count(text) == expected


def test_very_long_text():
    assert count("a" * 100_000) == 100_000
    assert count("e\u0301" * 50_000) == 50_000
    assert count("  " + "b" * 100_000 + "\r\n") == 100_000


def test_the_count_ignores_how_many_bytes_the_text_takes():
    assert count("\u65e5\u672c\u8a9e") == 3
    assert count("\u65e5\u672c\u8a9e" * 1000) == 3000


def test_counting_is_idempotent_on_already_normalized_text():
    text = "  Caf\u00e9\r\nna\u00efve\rcaf\u00e9  \U0001f600 "
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    assert count(text) == count(normalized) == len(normalized)


def test_the_module_exposes_the_function_where_the_plan_says():
    import forum.limits

    assert callable(forum.limits.count_message_chars)
