"""Mechanical balance of the matched variants: per message position, the number of checkable assertions and hedges (the
shared measuring stick in step3_testkit: count_assertions, count_hedges). A measuring stick, not the truth."""
import re

import pytest
from step3_testkit import (
    count_assertions,
    count_hedges,
    is_series,
    load_transcripts,
    pairs,
    planted_items,
    real_results_untouched,  # noqa: F401
)


@pytest.fixture(scope="module")
def pair_list():
    transcripts = load_transcripts()
    grouped = pairs(transcripts)
    assert len(grouped) == 9, f"expected 9 pairs, found {len(grouped)}"
    return [(pid, {t["variant"]: t for t in members}) for pid, members in sorted(grouped.items())]


def flawed_without_phrase(transcript):
    (message, planted), = planted_items(transcript)
    assert planted["phrase"] in message["text"]
    return message["text"].replace(planted["phrase"], " ")


def test_the_measuring_stick_is_deterministic_and_sane():
    assert count_assertions("") == 0
    assert count_assertions("Do you have a source for that?") == 0
    assert count_assertions("Rents rose 12 percent in 2019.") >= 1
    assert count_assertions("I feel strongly about this.") == 0
    assert count_assertions("A cap discourages building, because builders expect lower returns.") == 2
    assert count_assertions("Portugal decriminalized drugs in 2001.") == 1
    assert count_hedges("I think it might work, and some people say it can.") == 4
    assert count_hedges("It works.") == 0
    text = "San Francisco passed a rule in 1989, and it has lasted. I think some cities may copy it."
    assert count_assertions(text) == count_assertions(text) and count_hedges(text) == count_hedges(text)


def test_message_four_makes_the_same_number_of_checkable_assertions_in_both_variants(pair_list):
    for pid, v in pair_list:
        a, b = count_assertions(v["left"]["messages"][3]["text"]), count_assertions(v["right"]["messages"][3]["text"])
        assert a == b, f"{pid} message 4: {a} (left) vs {b} (right) checkable assertions"


def test_messages_one_to_three_differ_by_at_most_one_assertion(pair_list):
    for pid, v in pair_list:
        for i in range(3):
            a, b = count_assertions(v["left"]["messages"][i]["text"]), count_assertions(v["right"]["messages"][i]["text"])
            assert abs(a - b) <= 1, f"{pid} message {i + 1}: {a} (left) vs {b} (right)"


def test_hedges_differ_by_at_most_one_per_message(pair_list):
    for pid, v in pair_list:
        for i, (ml, mr) in enumerate(zip(v["left"]["messages"], v["right"]["messages"]), start=1):
            a, b = count_hedges(ml["text"]), count_hedges(mr["text"])
            assert abs(a - b) <= 1, f"{pid} message {i}: {a} (left) vs {b} (right) hedges"


def test_totals_across_a_pair_agree_within_one_assertion_and_three_hedges(pair_list):
    for pid, v in pair_list:
        totals = {
            side: (sum(count_assertions(m["text"]) for m in t["messages"]), sum(count_hedges(m["text"]) for m in t["messages"]))
            for side, t in v.items()
        }
        assert abs(totals["left"][0] - totals["right"][0]) <= 1, (pid, totals)
        assert abs(totals["left"][1] - totals["right"][1]) <= 3, (pid, totals)


def test_the_flawed_message_has_no_checkable_assertion_beyond_the_planted_phrase_and_matches_across_variants(pair_list):
    for pid, v in pair_list:
        a, b = count_assertions(flawed_without_phrase(v["left"])), count_assertions(flawed_without_phrase(v["right"]))
        assert a == b, f"{pid}: flawed message without the planted phrase: {a} (left) vs {b} (right)"
        assert a <= count_assertions(v["left"]["messages"][3]["text"])


# --- mechanical-series members (skipped while there are none; test_step3_series_format.py insists they exist) ---------------

def series_groups():
    transcripts = load_transcripts()
    grouped = {}
    for t in transcripts.values():
        if is_series(t):
            grouped.setdefault((t["series"]["id"], t["series"]["level"]), {})[t["series"]["side"]] = t
    return grouped


def test_series_left_and_right_members_are_balanced_per_message_position():
    groups = series_groups()
    if not groups:
        pytest.skip("no mechanical series yet")
    for (sid, level), sides in sorted(groups.items()):
        assert set(sides) == {"left", "right"}, (sid, level)
        left, right = sides["left"], sides["right"]
        assert len(left["messages"]) == len(right["messages"]), (sid, level)
        for i, (ml, mr) in enumerate(zip(left["messages"], right["messages"]), start=1):
            a, b = count_assertions(ml["text"]), count_assertions(mr["text"])
            limit = 0 if i == len(left["messages"]) else 1
            assert abs(a - b) <= limit, f"{sid}/{level} message {i}: {a} (left) vs {b} (right) assertions"
            # a long message may carry proportionally more hedges: allow one more per 500 characters
            hedge_limit = 1 + len(ml["text"]) // 500
            ha, hb = count_hedges(ml["text"]), count_hedges(mr["text"])
            assert abs(ha - hb) <= hedge_limit, f"{sid}/{level} message {i}: {ha} vs {hb} hedges"
