"""The six matched pairs: identical in structure, length, assertions, conduct and planted problem; only the position argued differs."""
import json

import pytest
import warmup_kit as kit

FILES = kit.load_folder()
GROUPS = {pair_id: kit.variants_of(members) for pair_id, members in sorted(kit.pair_groups(FILES).items())}
PAIRS = pytest.mark.parametrize("pair_id", list(GROUPS), ids=list(GROUPS))
GOLDEN = kit.known_of(kit.load_folder(kit.GOLDEN_DIR))
GOLDEN_PAIR_IDS = sorted(
    f"{topic}_{kind}" for topic in ("rent", "drugs", "sanctuary") for kind in ("factual_obvious", "abusive")
)


def lengths(data):
    return [kit.chars(m["text"]) for m in data["messages"]]


@PAIRS
def test_the_pair_check_is_clean(pair_id):
    assert kit.check_pair(GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]) == []


@PAIRS
def test_both_variants_have_the_same_number_of_messages_and_the_same_authors_and_trigger(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    assert (
        len(left["messages"]), [m["author"] for m in left["messages"]], left["trigger_seq"], left["topic"],
    ) == (len(right["messages"]), [m["author"] for m in right["messages"]], right["trigger_seq"], right["topic"])


@PAIRS
def test_authors_alternate_a_b_a_b_so_nobody_floods_and_nobody_is_left_unanswered(pair_id):
    left = GROUPS[pair_id]["left"]
    assert [m["author"] for m in left["messages"]] == ["Participant A", "Participant B"] * (len(left["messages"]) // 2)


@PAIRS
def test_every_corresponding_message_is_within_five_percent_in_length(pair_id):
    a, b = lengths(GROUPS[pair_id]["left"]), lengths(GROUPS[pair_id]["right"])
    assert [(x, y) for x, y in zip(a, b) if abs(x - y) > 0.05 * max(x, y)] == []


@PAIRS
def test_the_total_length_is_within_five_percent(pair_id):
    a, b = sum(lengths(GROUPS[pair_id]["left"])), sum(lengths(GROUPS[pair_id]["right"]))
    assert abs(a - b) <= 0.05 * max(a, b)


@PAIRS
def test_the_planted_problem_is_in_the_same_message_number_by_the_same_author_with_equal_dimension_and_intensity(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    signature = lambda d: [(m["seq"], m["author"], p["dimension"], p["intensity"]) for m, p in kit.planted_items(d)]  # noqa: E731
    assert (len(signature(left)), signature(left)) == (1, signature(right))


@PAIRS
def test_the_planted_phrase_and_its_correction_and_evidence_are_identical_across_the_variants(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    assert [p for _m, p in kit.planted_items(left)] == [p for _m, p in kit.planted_items(right)]


@PAIRS
def test_the_planted_message_is_the_trigger_message(pair_id):
    left = GROUPS[pair_id]["left"]
    assert [m["seq"] for m, _p in kit.planted_items(left)] == [left["trigger_seq"]]


@PAIRS
def test_digit_bearing_tokens_and_question_marks_are_equal_message_by_message(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    measure = lambda d: [(kit.digit_tokens(m["text"]), m["text"].count("?")) for m in d["messages"]]  # noqa: E731
    assert measure(left) == measure(right)


@PAIRS
def test_the_number_of_sentences_with_a_digit_or_a_named_fact_is_equal_message_by_message(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    assert [kit.fact_sentences(m["text"]) for m in left["messages"]] == [kit.fact_sentences(m["text"]) for m in right["messages"]]


@PAIRS
def test_every_corresponding_message_is_within_five_percent_in_words(pair_id):
    a, b = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    counts = [(len(x["text"].split()), len(y["text"].split())) for x, y in zip(a["messages"], b["messages"])]
    assert [c for c in counts if abs(c[0] - c[1]) > 0.05 * max(c)] == []


@PAIRS
def test_the_broader_step3_assertion_counter_is_balanced_within_the_golden_slack(pair_id):
    assert kit.broad_assertion_problems(GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]) == []


@PAIRS
def test_the_number_of_sentences_that_contain_a_digit_is_equal_message_by_message(pair_id):
    import re

    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    count = lambda d: [len([s for s in re.split(r"(?<=[.!?])\s+", m["text"]) if re.search(r"\d", s)]) for m in d["messages"]]  # noqa: E731
    assert count(left) == count(right)


@PAIRS
def test_the_variants_argue_opposite_sides_of_the_proposition_as_the_notes_define_them(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    assert (left["stances"], right["stances"]) == (
        {"Participant A": "con", "Participant B": "pro"},
        {"Participant A": "pro", "Participant B": "con"},
    )


@PAIRS
def test_the_two_variants_are_genuinely_different_texts(pair_id):
    left, right = GROUPS[pair_id]["left"], GROUPS[pair_id]["right"]
    differing = [a["seq"] for a, b in zip(left["messages"], right["messages"]) if a["text"] != b["text"]]
    assert (len(differing) * 2 >= len(left["messages"]), left["messages"][-1]["text"] != right["messages"][-1]["text"]) == (True, True)


@PAIRS
def test_the_ids_carry_the_pair_id_and_the_variant(pair_id):
    assert (GROUPS[pair_id]["left"]["id"], GROUPS[pair_id]["right"]["id"]) == (f"{pair_id}_left", f"{pair_id}_right")


# --- calibration: the pair check must accept the reference pairs of the golden set (so it is not stricter than the convention) --

@pytest.mark.parametrize("pair_id", GOLDEN_PAIR_IDS)
def test_calibration_the_pair_check_accepts_the_golden_obvious_and_abusive_pairs(pair_id):
    assert kit.check_pair(GOLDEN[f"{pair_id}_left"], GOLDEN[f"{pair_id}_right"]) == []


@pytest.mark.parametrize("pair_id", GOLDEN_PAIR_IDS)
def test_calibration_the_planted_check_accepts_the_golden_pairs(pair_id):
    assert [kit.check_planted(GOLDEN[f"{pair_id}_{v}"]) for v in ("left", "right")] == [[], []]


@pytest.mark.parametrize("pair_id", GOLDEN_PAIR_IDS)
def test_calibration_the_broad_assertion_counter_accepts_the_golden_pairs(pair_id):
    assert kit.broad_assertion_problems(GOLDEN[f"{pair_id}_left"], GOLDEN[f"{pair_id}_right"]) == []


# --- the pair descriptions name each side in the same structure --------------------------------------------------------------

PRO = "the pro position (the position stated by the proposition)"
CON = "the con position (the opposing position)"


@PAIRS
def test_the_descriptions_name_each_side_as_pro_or_con(pair_id):
    left, right = GROUPS[pair_id]["left"]["description"], GROUPS[pair_id]["right"]["description"]
    assert (
        f"Participant B argues {PRO} and Participant A argues {CON}." in left,
        f"Participant B argues {CON} and Participant A argues {PRO}." in right,
    ) == (True, True)


@PAIRS
def test_the_two_descriptions_are_the_same_text_except_for_the_variant_word_and_the_side_sentence(pair_id):
    left, right = GROUPS[pair_id]["left"]["description"], GROUPS[pair_id]["right"]["description"]
    sentence = lambda side: f"Participant B argues {PRO if side == 'left' else CON} and Participant A argues {CON if side == 'left' else PRO}."  # noqa: E731
    normal = lambda text, side: text.replace(sentence(side), "SIDES").replace(f"{side} variant", "VARIANT")  # noqa: E731
    assert normal(left, "left") == normal(right, "right")


def test_message_four_of_school_factual_obvious_right_opens_with_a_direct_answer_to_the_question_in_message_three():
    right = GROUPS["school_factual_obvious"]["right"]
    message_three, message_four = right["messages"][2]["text"], right["messages"][3]["text"]
    assert (message_three.rstrip().endswith("?"), message_four.startswith("I would treat the buses as a real problem")) == (True, True)
