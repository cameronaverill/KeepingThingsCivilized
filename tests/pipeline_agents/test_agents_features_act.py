"""act_text_features(text): the same four values InterventionAct.save() stores (asserted equal to the model)."""
import random

import pytest

import pipeline_agents_kit as kit

KEYS = {"char_len", "word_count", "is_question", "quotes_participant"}

TEXTS = [
    "",
    "Could a source be given for the figure in message 1?",
    "Possible factual error in message 4: the population figure is about ten times too high.",
    'The phrase "about 80 million" appears in message 4.',
    "The phrase “about 80 million” appears in message 4.",
    'An empty pair of quotes "" is not a quoted span.',
    'A lone quote " never closes.',
    'Two "quoted" and "spans" here?',
    "What? Why? How?",
    "No question here.",
    "   leading and trailing spaces   ",
    "tabs\tand\nnewlines\r\nand   runs of   spaces",
    "non breaking spaces and em spaces",
    "emoji \U0001F600 counts as one character",
    "é combining mark",
    "“unclosed curly",
    "curly “” empty",
    'mixed "straight” curly',
    "x" * 2999,
    "?",
    '"',
    '"a"',
    "  ",
    "one",
    "A question inside quotes: \"is this a question?\" and nothing else.",
]


def model_features(text):
    """What the model computes for the same text (an unsaved instance; compute_features is what save() calls)."""
    from moderation.models import InterventionAct

    act = InterventionAct(text=text)
    act.compute_features()
    return {
        "char_len": act.char_len, "word_count": act.word_count, "is_question": act.is_question,
        "quotes_participant": act.quotes_participant,
    }  # fmt: skip


@pytest.mark.parametrize("text", TEXTS, ids=lambda t: repr(t)[:40])
def test_equals_the_models_features(text):
    got = kit.features().act_text_features(text)
    assert set(got) == KEYS
    assert got == model_features(text)


@pytest.mark.parametrize("text", TEXTS[:12], ids=lambda t: repr(t)[:40])
def test_equals_what_save_stores(text):
    """The end-to-end version: an act saved to the database has exactly these values."""
    from moderation.models import InterventionAct

    sc = kit.make_human_scenario(human=False)
    act = InterventionAct.objects.create(
        run=sc.run, order=1, act_type="request_information", tone="neutral", text=text or "placeholder", addressee="all", subject="none"
    )
    act.refresh_from_db()
    got = kit.features().act_text_features(text or "placeholder")
    assert got == {
        "char_len": act.char_len, "word_count": act.word_count, "is_question": act.is_question,
        "quotes_participant": act.quotes_participant,
    }  # fmt: skip


def test_known_values():
    f = kit.features().act_text_features('Is "the other figure" right?  Yes')
    assert f == {"char_len": 33, "word_count": 6, "is_question": True, "quotes_participant": True}
    g = kit.features().act_text_features("Just a statement.")
    assert g == {"char_len": 17, "word_count": 3, "is_question": False, "quotes_participant": False}


def test_value_types_are_int_and_bool():
    f = kit.features().act_text_features('a "b" c?')
    assert type(f["char_len"]) is int and type(f["word_count"]) is int
    assert f["is_question"] is True and f["quotes_participant"] is True
    h = kit.features().act_text_features("plain")
    assert h["is_question"] is False and h["quotes_participant"] is False


def test_char_len_is_not_stripped_and_counts_code_points():
    assert kit.features().act_text_features("  ab  ")["char_len"] == 6
    assert kit.features().act_text_features("\U0001F600\U0001F600")["char_len"] == 2


def test_word_count_splits_on_any_whitespace():
    assert kit.features().act_text_features("a\tb\nc  d e")["word_count"] == 5
    assert kit.features().act_text_features("")["word_count"] == 0
    assert kit.features().act_text_features("   ")["word_count"] == 0


def test_question_mark_anywhere_counts_including_inside_quotes():
    assert kit.features().act_text_features('He wrote "why?" and stopped.')["is_question"] is True
    assert kit.features().act_text_features("no mark")["is_question"] is False
    assert kit.features().act_text_features("？ fullwidth is not a question mark")["is_question"] is False


def test_quoted_span_needs_content_and_closing_marks():
    f = kit.features().act_text_features
    assert f('say "x" now')["quotes_participant"] is True
    assert f("say “x” now")["quotes_participant"] is True
    assert f('say "" now')["quotes_participant"] is False
    assert f('say "unclosed now')["quotes_participant"] is False
    assert f("say “” now")["quotes_participant"] is False


def test_random_texts_match_the_model():
    rng = random.Random(5150)
    alphabet = list('ab c?\n\t"“” .\'') + ["\U0001F600", "é"]
    for _ in range(400):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        assert kit.features().act_text_features(text) == model_features(text), repr(text)


def test_pure_function_no_database_no_model_call(django_assert_num_queries, install_fake):
    fake = install_fake()
    with django_assert_num_queries(0):
        kit.features().act_text_features('x "y" z?')
    assert fake.calls == []


def test_unsaved_model_instance_is_not_created():
    from moderation.models import InterventionAct

    kit.features().act_text_features("hello?")
    assert InterventionAct.objects.count() == 0
