"""Every warm-up transcript, one file at a time: shape, the spike's validators, limits, topics, planted items, hygiene."""
import json
import re

import pytest
import warmup_kit as kit

FILES = kit.load_folder()
KNOWN = kit.known_of(FILES)
BY_STEM = pytest.mark.parametrize("path, data", FILES, ids=[p.stem for p, _d in FILES])
PAIR_FILES = kit.pair_members(FILES)


@BY_STEM
def test_the_json_shape_is_the_golden_shape(path, data):
    assert kit.check_format(path, data) == []


@BY_STEM
def test_the_spikes_own_validate_transcript_accepts_it_with_the_folder_as_the_known_set(path, data):
    assert kit.check_validators(path, data, KNOWN) == []


@BY_STEM
def test_message_lengths_and_the_user_message_count_are_within_the_limits(path, data):
    assert kit.check_limits(data) == []


@BY_STEM
def test_the_topic_title_and_proposition_are_the_seeded_ones(path, data):
    assert kit.check_topic(data) == []


@BY_STEM
def test_planted_items_are_well_formed_and_located_exactly_once(path, data):
    assert kit.check_planted(data) == []


@BY_STEM
def test_the_text_has_no_email_url_name_label_political_word_or_stray_insult(path, data):
    assert kit.check_hygiene(data) == []


@pytest.mark.parametrize("path, data", PAIR_FILES, ids=[p.stem for p, _d in PAIR_FILES])
def test_a_pair_transcript_plants_exactly_one_item_in_its_trigger_message(path, data):
    assert kit.check_pair_planting(data) == []


@BY_STEM
def test_the_spike_finds_every_planted_phrase_with_offsets_inside_its_message(path, data):
    from moderation.management.commands import spike

    spans = spike.planted_spans(data)
    texts = {m["seq"]: m["text"] for m in data["messages"]}
    assert [(s["phrase"], texts[s["message_id"]][s["start"]:s["end"]]) for s in spans] == [(s["phrase"], s["phrase"]) for s in spans]


@BY_STEM
def test_the_file_is_valid_utf8_json_with_the_transcript_read_back_unchanged(path, data):
    assert json.loads(path.read_text(encoding="utf-8")) == data


@BY_STEM
def test_no_message_text_carries_a_participant_label_in_any_spelling(path, data):
    from moderation.label_check import names_a_label

    joined = "\n".join(m["text"] for m in data["messages"])
    assert (names_a_label(joined), re.search(r"\bParticipants?\b|\bparticipant [a-z]\b", joined, re.I)) == (False, None)


@BY_STEM
def test_message_texts_are_plain_ascii(path, data):
    assert [m["seq"] for m in data["messages"] if not m["text"].isascii()] == []


def test_the_topic_titles_used_are_exactly_the_three_warmup_topics():
    assert {d["topic"]["title"] for _p, d in FILES} == set(kit.TOPIC_TITLES)


def test_the_seeded_propositions_are_what_the_brief_names():
    """The topics are read from forum/seed_topics.json, never retyped: the files' propositions must be the seeded strings."""
    seeded = json.loads(kit.SEED_PATH.read_text(encoding="utf-8"))
    expected = {t["title"]: t["proposition"] for t in seeded if t["title"] in kit.TOPIC_TITLES}
    used = {d["topic"]["title"]: d["topic"]["proposition"] for _p, d in FILES}
    assert used == expected


def test_no_two_transcripts_share_a_long_message_text_across_topics():
    seen = {}
    for _p, d in FILES:
        for m in d["messages"]:
            if len(m["text"]) > 80:
                seen.setdefault(m["text"], set()).add(d["topic"]["title"])
    assert [text[:40] for text, titles in seen.items() if len(titles) > 1] == []


def test_the_trigger_message_of_a_pair_is_unique_to_that_pair():
    """Within a topic the two pairs may share their opening messages (the golden set does the same), but the flawed message is
    each pair's own: no trigger text is shared between two different pair ids."""
    owners = {}
    for _p, d in kit.pair_members(FILES):
        owners.setdefault(kit.trigger_of(d)["text"], set()).add(d["pair_id"])
    assert [text[:40] for text, ids in owners.items() if len(ids) > 1] == []
