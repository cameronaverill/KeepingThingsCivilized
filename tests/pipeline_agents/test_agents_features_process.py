"""process_facts(transcript): computed in code from hand-built transcripts (runs, gaps, counts), no LLM, no database."""
import copy
import json
import random
from itertools import groupby

import pytest

import pipeline_agents_kit as kit


def facts_of(labels, **kwargs):
    return kit.features().process_facts(kit.hand_transcript(labels, **kwargs))


# (letters, current run of the newest author, longest run, per-participant counts)
CASES = [
    ("AABAAABB", 2, 3, {"A": 5, "B": 3}),
    ("ABAB", 1, 1, {"A": 2, "B": 2}),
    ("AAAA", 4, 4, {"A": 4}),
    ("A", 1, 1, {"A": 1}),
    ("ABBB", 3, 3, {"A": 1, "B": 3}),
    ("BBBA", 1, 3, {"A": 1, "B": 3}),
    ("AAB", 1, 2, {"A": 2, "B": 1}),
    ("ABBAAAAB", 1, 4, {"A": 5, "B": 3}),
    ("AABB", 2, 2, {"A": 2, "B": 2}),
]


@pytest.mark.parametrize("labels,current,longest,counts", CASES)
def test_runs_and_counts(labels, current, longest, counts):
    facts = facts_of(labels)
    assert kit.fact_current_run(facts) == current
    assert kit.fact_longest_run(facts) == longest
    assert kit.fact_counts(facts) == counts


def test_single_message_transcript():
    facts = facts_of("B")
    assert kit.fact_current_run(facts) == 1
    assert kit.fact_longest_run(facts) == 1
    assert kit.fact_counts(facts) == {"B": 1}
    assert kit.fact_gap_seconds(facts) in (None, 0)  # no previous message to measure against
    json.dumps(facts)


def test_single_author_transcript_has_one_long_run():
    facts = facts_of("BBBBBB")
    assert kit.fact_current_run(facts) == 6 == kit.fact_longest_run(facts)
    assert kit.fact_counts(facts) == {"B": 6}


def test_empty_transcript_gives_zeros_and_no_gap():
    facts = kit.features().process_facts([])
    assert kit.fact_current_run(facts) == 0
    assert kit.fact_longest_run(facts) == 0
    assert kit.fact_gap_seconds(facts) is None
    json.dumps(facts)
    counts = kit.fact_counts(facts)
    assert sum(counts.values()) == 0


def test_the_current_run_is_the_newest_authors_not_the_longest():
    facts = facts_of("AAAAB")
    assert kit.fact_current_run(facts) == 1 and kit.fact_longest_run(facts) == 4


def test_flooding_shape_of_four_in_a_row():
    """Four messages in a row by one participant (the shape the retired flooding series used)."""
    labels = ["A", "B", "B", "B", "B"]
    transcript = []
    for i, label in enumerate(labels):
        transcript.append({"id": i + 1, "seq_no": i + 1, "author_type": "user", "label": label, "text": "x", "created_at": kit.T0})
    facts = kit.features().process_facts(transcript)
    assert kit.fact_longest_run(facts) == 4
    assert kit.fact_current_run(facts) == 4  # the trigger is the fourth message of the run


@pytest.mark.parametrize(
    "labels,gaps,expected",
    [
        ("AB", [47], 47),
        ("ABA", [10, 600], 600),
        ("ABA", [3600, 5], 5),
        ("AB", [0], 0),
        ("ABABA", [1, 1, 1, 90061], 90061),
    ],
)
def test_seconds_between_the_last_two_messages(labels, gaps, expected):
    facts = facts_of(labels, gaps=gaps)
    assert kit.fact_gap_seconds(facts) == pytest.approx(expected, abs=0.5)


def test_gap_uses_only_the_last_two_messages_regardless_of_earlier_gaps():
    facts = facts_of("ABABAB", gaps=[1000, 2000, 3000, 4000, 12])
    assert kit.fact_gap_seconds(facts) == pytest.approx(12, abs=0.5)


def test_gap_can_be_fractional_seconds():
    from datetime import timedelta

    transcript = kit.hand_transcript("AB")
    transcript[1]["created_at"] = transcript[0]["created_at"] + timedelta(seconds=2, milliseconds=500)
    gap = kit.fact_gap_seconds(kit.features().process_facts(transcript))
    assert gap == pytest.approx(2.5, abs=0.6)


def test_moderator_messages_are_counted_under_their_own_label_and_end_runs():
    facts = facts_of("AAMAA")
    counts = kit.fact_counts(facts)
    assert counts["A"] == 4 and counts["Moderator"] == 1
    assert kit.fact_current_run(facts) == 2  # the moderator's message ended the participant's run
    assert kit.fact_longest_run(facts) == 2


def test_result_is_json_serializable_with_no_datetimes():
    facts = facts_of("AABAB")
    assert json.loads(json.dumps(facts)) == facts


def test_process_facts_does_not_modify_the_transcript():
    transcript = kit.hand_transcript("AABBA")
    before = copy.deepcopy(transcript)
    kit.features().process_facts(transcript)
    assert transcript == before


def test_process_facts_needs_no_database_and_no_model_call(django_assert_num_queries, install_fake):
    fake = install_fake()  # an empty script: any call would raise
    transcript = kit.hand_transcript("AABBA")
    with django_assert_num_queries(0):
        kit.features().process_facts(transcript)
    assert fake.calls == []


def test_text_of_the_messages_does_not_matter():
    a = facts_of("AABA", texts=["x", "y", "z", "w"])
    b = facts_of("AABA", texts=["a much longer message " * 20] * 4)
    assert a == b


def test_random_transcripts_match_an_independent_computation():
    rng = random.Random(20260926)
    for _ in range(150):
        length = rng.randint(1, 25)
        labels = "".join(rng.choice("AAB" if rng.random() < 0.5 else "AB") for _ in range(length))
        gaps = [rng.randint(0, 5000) for _ in range(length - 1)]
        facts = facts_of(labels, gaps=gaps)
        runs = [len(list(g)) for _, g in groupby(labels)]
        assert kit.fact_current_run(facts) == runs[-1], labels
        assert kit.fact_longest_run(facts) == max(runs), labels
        assert kit.fact_counts(facts) == {k: labels.count(k) for k in set(labels)}, labels
        if length >= 2:
            assert kit.fact_gap_seconds(facts) == pytest.approx(gaps[-1], abs=0.5), (labels, gaps)


def test_facts_from_a_database_transcript_match_the_hand_computation():
    sc = kit.make_human_scenario()  # A, B, moderator, A at 0, 40, 100, 130 seconds
    facts = kit.features().process_facts(sc.transcript)
    assert kit.fact_counts(facts)["A"] == 2 and kit.fact_counts(facts)["B"] == 1
    assert kit.fact_current_run(facts) == 1
    assert kit.fact_gap_seconds(facts) == pytest.approx(30, abs=0.5)


def test_facts_render_through_the_master_input_without_error():
    from moderation import prompting

    transcript = kit.hand_transcript("AABBA")
    facts = kit.features().process_facts(transcript)
    text = prompting.render_master_input(
        [(m["id"], m["label"], m["text"]) for m in transcript], proposition="p", process_facts=facts
    )
    assert "<process_facts>" in text


def test_architect_ruled_keys_and_labels():
    """message_count, latest_author_label and longest_run_label (the earliest run wins a tie), with the labels as given."""
    facts = facts_of("AABBB")
    assert facts["message_count"] == 5
    assert facts["latest_author_label"] == "Participant B"
    assert facts["longest_run_label"] == "Participant B"
    assert facts["messages_per_label"] == {"Participant A": 2, "Participant B": 3}
    tie = facts_of("AABB")
    assert tie["longest_run_label"] == "Participant A"  # the earliest of equal runs
    assert tie["latest_author_label"] == "Participant B"
    assert facts_of("ABBA")["longest_run_label"] == "Participant B"


def test_empty_transcript_keys():
    facts = kit.features().process_facts([])
    assert facts["message_count"] == 0
    assert facts["latest_author_label"] is None
    assert facts["seconds_between_last_two_messages"] is None
    assert facts["messages_per_label"] == {}


def test_gap_is_a_float_and_none_below_two_messages():
    assert isinstance(facts_of("AB", gaps=[47])["seconds_between_last_two_messages"], float)
    assert facts_of("A")["seconds_between_last_two_messages"] is None
