"""golden/warmup/transcripts/: what is in the folder (docs/warmup_brief.md, 'What to write')."""
import json
from collections import Counter

import pytest
import warmup_kit as kit

FILES = kit.load_folder()
GROUPS = kit.pair_groups(FILES)


def test_the_warmup_folder_exists_and_holds_only_json_files():
    assert kit.WARMUP_DIR.is_dir()
    assert [e.name for e in kit.WARMUP_DIR.iterdir() if not (e.is_file() and e.suffix == ".json")] == []


def test_every_json_file_parses_and_is_an_object_with_a_string_id():
    on_disk = sorted(kit.WARMUP_DIR.glob("*.json"))
    parsed = [json.loads(p.read_text(encoding="utf-8")) for p in on_disk]
    assert (len(on_disk) > 0, all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in parsed)) == (True, True)


def test_about_twenty_two_files_in_total():
    assert 20 <= len(FILES) <= 26


def test_twelve_pair_transcripts_in_six_pairs():
    assert (len(kit.pair_members(FILES)), len(GROUPS)) == (12, 6)


def test_between_eight_and_fourteen_series_members():
    assert 8 <= len(kit.series_members(FILES)) <= 14


def test_ids_are_unique_and_equal_to_the_file_names():
    ids = [d["id"] for _p, d in FILES]
    assert (len(set(ids)) == len(ids), [p.stem for p, _d in FILES] == ids) == (True, True)


def test_ids_do_not_clash_with_the_golden_ids():
    assert {d["id"] for _p, d in FILES} & kit.golden_ids() == set()


def test_no_singles_exist_every_file_is_a_pair_member_or_a_series_member():
    assert [d["id"] for _p, d in FILES if not d.get("pair_id") and not d.get("series")] == []


def test_every_pair_has_exactly_two_members_a_left_and_a_right():
    assert {pair_id: sorted(m["variant"] for m in members) for pair_id, members in GROUPS.items()} == {
        pair_id: ["left", "right"] for pair_id in GROUPS
    }


def test_the_pair_groups_check_is_clean():
    assert kit.check_pair_groups(FILES) == []


def test_each_of_the_three_topics_has_two_pairs_one_factual_and_one_abusive():
    kinds = {}
    for members in GROUPS.values():
        (message, item), = kit.planted_items(members[0])
        kinds.setdefault(members[0]["topic"]["title"], []).append(item["dimension"])
    assert {title: sorted(dims) for title, dims in kinds.items()} == {
        title: ["abusiveness", "factual_accuracy"] for title in kit.TOPIC_TITLES
    }


def test_the_pair_planting_is_at_intensity_three_or_four_in_every_pair_transcript():
    intensities = Counter(item["intensity"] for d in [d for _p, d in kit.pair_members(FILES)] for _m, item in kit.planted_items(d))
    assert set(intensities) <= {3, 4} and sum(intensities.values()) == 12


def test_no_hard_factual_pairs_and_no_singles_only_obvious_factual_and_abusive_dimensions():
    dims = {item["dimension"] for _p, d in FILES for _m, item in kit.planted_items(d)}
    assert dims == {"factual_accuracy", "abusiveness"}


def test_the_series_is_on_the_bike_lanes_topic_only():
    assert {d["topic"]["title"] for _p, d in kit.series_members(FILES)} == {"Bike lanes"}


def test_every_series_base_is_in_the_folder_and_is_a_pair_member():
    known = kit.known_of(FILES)
    bases = [known.get(d["series"]["base"]) for _p, d in kit.series_members(FILES)]
    assert [b["id"] for b in bases if b is not None and b.get("pair_id")] == [b["id"] for b in bases]


def test_all_five_factors_are_present():
    assert {d["series"]["factor"] for _p, d in kit.series_members(FILES)} == {
        "message_length", "label_swap", "flooding", "repetition", "unanswered_question",
    }


def test_the_inventory_check_is_clean():
    assert kit.check_inventory(kit.WARMUP_DIR, FILES, kit.golden_ids()) == []
