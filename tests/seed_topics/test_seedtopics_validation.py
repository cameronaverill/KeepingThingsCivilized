"""10a: invalid file content aborts before any write, with a message that names the entry
(docs/step10a_brief.md, "What it does" and "Tests"). Every case puts the bad entry SECOND, after a good one, so an
implementation that writes as it goes leaves a row behind and fails."""
import copy
import json
import math
import re

import pytest
from django.conf import settings
from django.core.management.base import CommandError

import seedtopics_kit as K

GOOD = "Good first topic"
BAD = "Bad second topic"


def with_bad(**overrides):
    """[a good entry, the bad entry]; the bad entry is made valid, then the overrides are applied."""
    return [K.make_entry(GOOD), K.make_entry(BAD, **overrides)]


def drop(key):
    entries = with_bad()
    del entries[1][key]
    return entries


def bad_leans(mutate):
    entries = with_bad()
    mutate(entries[1]["leans"])
    return entries


def cell(leans, side="pro", scheme="compass", axis="economic"):
    return leans[side][scheme][axis]


def set_value(value):
    return lambda leans: cell(leans).update(value=value)


def set_rationale(text):
    return lambda leans: cell(leans).update(rationale=text)


def del_cell_key(key):
    return lambda leans: cell(leans).pop(key)


def rename_key(parent_path, old, new):
    def mutate(leans):
        node = leans
        for part in parent_path:
            node = node[part]
        node[new] = node.pop(old)

    return mutate


def drop_key(parent_path, old):
    def mutate(leans):
        node = leans
        for part in parent_path:
            node = node[part]
        del node[old]

    return mutate


def assert_aborts(tmp_path, data, names=(BAD,), extra_output=()):
    before = K.snapshot()
    with pytest.raises(CommandError) as caught:
        K.run_file(K.write_file(tmp_path, data), *extra_output)
    assert K.snapshot() == before
    message = str(caught.value)
    assert [n for n in names if n not in message] == []
    assert GOOD not in message


# --- a missing key ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["description", "proposition", "opposing_position", "leans"])
def test_a_missing_key_aborts_with_the_entry_and_the_key_named(tmp_path, key):
    assert_aborts(tmp_path, drop(key), names=(BAD, key))
    assert K.topic_count() == 0


def test_a_missing_title_aborts_and_writes_nothing(tmp_path):
    entries = with_bad()
    del entries[1]["title"]
    with pytest.raises(CommandError, match="title"):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.topic_count() == 0


@pytest.mark.parametrize("title", ["", "   ", None, 7])
def test_a_blank_or_non_text_title_aborts_and_writes_nothing(tmp_path, title):
    entries = with_bad()
    entries[1]["title"] = title
    with pytest.raises(CommandError):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.topic_count() == 0


# --- the proposition -------------------------------------------------------------------------------------------------------

def test_a_proposition_one_over_the_limit_aborts(tmp_path):
    over = "x" * (settings.MAX_PROPOSITION_CHARS + 1)
    assert_aborts(tmp_path, with_bad(proposition=over))
    assert K.topic_count() == 0


def test_a_proposition_of_exactly_the_limit_is_accepted(tmp_path):
    exact = "x" * settings.MAX_PROPOSITION_CHARS
    assert K.counts(K.run_file(K.write_file(tmp_path, with_bad(proposition=exact)))) == (2, 0, 0)
    assert K.topic_by_title(BAD).proposition == exact


def test_the_proposition_limit_follows_the_tunable(tmp_path, settings):
    settings.MAX_PROPOSITION_CHARS = 30
    short_good = K.make_entry(GOOD, proposition="A short good claim.", opposing_position="A short other claim.")
    short_bad = {"opposing_position": "A short other claim, too."}
    assert_aborts(tmp_path, [short_good, K.make_entry(BAD, proposition="y" * 31, **short_bad)])
    ok = [short_good, K.make_entry(BAD, proposition="y" * 30, **short_bad)]
    assert K.counts(K.run_file(K.write_file(tmp_path, ok))) == (2, 0, 0)


@pytest.mark.parametrize("proposition", ["", "   ", None, 12, ["a claim"]])
def test_an_empty_or_non_text_proposition_aborts(tmp_path, proposition):
    assert_aborts(tmp_path, with_bad(proposition=proposition))
    assert K.topic_count() == 0


def test_the_limit_counts_characters_by_the_projects_one_rule_not_by_raw_length(tmp_path):
    # count_message_chars puts text in NFC first: 200 letters typed as e + combining accent are 200 characters, not 400.
    accented = "e\u0301" * settings.MAX_PROPOSITION_CHARS
    assert K.counts(K.run_file(K.write_file(tmp_path, with_bad(proposition=accented)))) == (2, 0, 0)


def test_the_limit_counts_a_combining_accent_text_one_over_as_over(tmp_path):
    accented = "e\u0301" * (settings.MAX_PROPOSITION_CHARS + 1)
    assert_aborts(tmp_path, with_bad(proposition=accented))
    assert K.topic_count() == 0


# --- the opposing position -------------------------------------------------------------------------------------------------

def test_an_opposing_position_one_over_the_limit_aborts_naming_the_entry(tmp_path):
    assert_aborts(tmp_path, with_bad(opposing_position="x" * (settings.MAX_PROPOSITION_CHARS + 1)))
    assert K.topic_count() == 0


def test_an_opposing_position_of_exactly_the_limit_is_accepted_and_stored(tmp_path):
    exact = "x" * settings.MAX_PROPOSITION_CHARS
    assert K.counts(K.run_file(K.write_file(tmp_path, with_bad(opposing_position=exact)))) == (2, 0, 0)
    assert K.topic_by_title(BAD).opposing_position == exact


def test_the_opposing_position_limit_follows_the_tunable(tmp_path, settings):
    settings.MAX_PROPOSITION_CHARS = 30
    good = K.make_entry(GOOD, proposition="A short good claim.", opposing_position="A short other claim.")
    assert_aborts(tmp_path, [good, K.make_entry(BAD, proposition="A short claim.", opposing_position="y" * 31)])
    ok = [good, K.make_entry(BAD, proposition="A short claim.", opposing_position="y" * 30)]
    assert K.counts(K.run_file(K.write_file(tmp_path, ok))) == (2, 0, 0)


def test_the_opposing_position_limit_counts_by_the_projects_one_rule(tmp_path):
    accented = "e\u0301" * settings.MAX_PROPOSITION_CHARS
    assert K.counts(K.run_file(K.write_file(tmp_path, with_bad(opposing_position=accented)))) == (2, 0, 0)
    assert_aborts(tmp_path, with_bad(opposing_position="e\u0301" * (settings.MAX_PROPOSITION_CHARS + 1)))


@pytest.mark.parametrize("opposing", ["", "   ", "\n", None, 12, ["a claim"]])
def test_an_empty_or_non_text_opposing_position_aborts(tmp_path, opposing):
    assert_aborts(tmp_path, with_bad(opposing_position=opposing))
    assert K.topic_count() == 0


def test_an_opposing_position_identical_to_the_proposition_aborts_naming_the_entry(tmp_path):
    assert_aborts(tmp_path, with_bad(proposition="Same words on both sides.", opposing_position="Same words on both sides."))
    assert K.topic_count() == 0


@pytest.mark.parametrize(
    "opposing",
    ["SAME WORDS ON BOTH SIDES.", "  Same   words on\tboth sides.  ", "\uff33ame words on both sides."],
    ids=["other_case", "other_whitespace", "full_width_letter"],
)
def test_an_opposing_position_identical_after_normalisation_aborts(tmp_path, opposing):
    assert_aborts(tmp_path, with_bad(proposition="Same words on both sides.", opposing_position=opposing))
    assert K.topic_count() == 0


def test_an_opposing_position_that_merely_resembles_the_proposition_is_accepted(tmp_path):
    entries = with_bad(proposition="Same words on both sides.", opposing_position="Same words on both sides, not.")
    assert K.counts(K.run_file(K.write_file(tmp_path, entries))) == (2, 0, 0)


def test_a_bad_opposing_position_stops_updates_of_earlier_entries_too(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    edited = copy.deepcopy(entries)
    edited[0]["opposing_position"] = "This edit must not land."
    edited[1]["opposing_position"] = edited[1]["proposition"]
    with pytest.raises(CommandError, match="Testing topic B"):
        K.run_file(K.write_file(tmp_path, edited))
    assert K.snapshot() == before


@pytest.mark.parametrize("description", [None, 5, ["text"]])
def test_a_non_text_description_aborts(tmp_path, description):
    assert_aborts(tmp_path, with_bad(description=description))
    assert K.topic_count() == 0


# --- the shape of leans ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("leans", [None, [], {}, "pro", 3, [{"pro": {}}]], ids=["none", "list", "empty", "str", "int", "listed"])
def test_leans_that_are_not_the_full_object_abort(tmp_path, leans):
    assert_aborts(tmp_path, with_bad(leans=leans))
    assert K.topic_count() == 0


def extra_cell_key(leans):
    cell(leans)["source"] = "somewhere"


def extra_entry_key():
    entries = with_bad()
    entries[1]["slug"] = "bad-second-topic"
    return entries


BAD_LEANS = {
    "cell_has_an_extra_key": bad_leans(extra_cell_key),
    "entry_has_an_extra_key": extra_entry_key(),
    "missing_con": bad_leans(drop_key([], "con")),
    "missing_pro": bad_leans(drop_key([], "pro")),
    "extra_side": bad_leans(lambda leans: leans.update(neutral=copy.deepcopy(leans["pro"]))),
    "renamed_side": bad_leans(rename_key([], "pro", "for")),
    "missing_compass": bad_leans(drop_key(["pro"], "compass")),
    "missing_us_partisan": bad_leans(drop_key(["con"], "us_partisan")),
    "unknown_scheme": bad_leans(lambda leans: leans["pro"].update(tribe=copy.deepcopy(leans["pro"]["compass"]))),
    "renamed_scheme": bad_leans(rename_key(["pro"], "compass", "compas")),
    "missing_economic": bad_leans(drop_key(["pro", "compass"], "economic")),
    "missing_social": bad_leans(drop_key(["con", "compass"], "social")),
    "missing_party": bad_leans(drop_key(["pro", "us_partisan"], "party")),
    "unknown_axis": bad_leans(lambda leans: leans["pro"]["compass"].update(religion=copy.deepcopy(cell(leans)))),
    "axis_in_wrong_scheme": bad_leans(rename_key(["pro", "us_partisan"], "party", "economic")),
    "value_above_one": bad_leans(set_value(1.01)),
    "value_below_minus_one": bad_leans(set_value(-1.01)),
    "value_far_out": bad_leans(set_value(7)),
    "value_is_text": bad_leans(set_value("0.5")),
    "value_is_null": bad_leans(set_value(None)),
    "value_is_a_bool": bad_leans(set_value(True)),
    "value_is_nan": bad_leans(set_value(math.nan)),
    "value_is_infinite": bad_leans(set_value(math.inf)),
    "value_missing": bad_leans(del_cell_key("value")),
    "rationale_missing": bad_leans(del_cell_key("rationale")),
    "rationale_empty": bad_leans(set_rationale("")),
    "rationale_blank": bad_leans(set_rationale("   ")),
    "rationale_null": bad_leans(set_rationale(None)),
    "rationale_number": bad_leans(set_rationale(5)),
    "cell_is_a_number": bad_leans(lambda leans: leans["con"]["us_partisan"].update(party=0.5)),
    "cell_is_a_list": bad_leans(lambda leans: leans["con"]["us_partisan"].update(party=[0.5, "why"])),
    "side_is_a_list": bad_leans(lambda leans: leans.update(pro=[])),
    "scheme_is_a_string": bad_leans(lambda leans: leans["pro"].update(compass="economic")),
}


@pytest.mark.parametrize("entries", BAD_LEANS.values(), ids=BAD_LEANS.keys())
def test_a_leans_shape_or_value_problem_aborts_naming_the_entry_and_writes_nothing(tmp_path, entries):
    assert_aborts(tmp_path, entries)
    assert K.topic_count() == 0


@pytest.mark.parametrize("value", [1.0, -1.0, 0.0, 0.999, -0.5])
def test_values_inside_the_range_including_both_ends_are_accepted(tmp_path, value):
    entries = with_bad()
    cell(entries[1]["leans"]).update(value=value)
    assert K.counts(K.run_file(K.write_file(tmp_path, entries))) == (2, 0, 0)
    assert cell(K.topic_by_title(BAD).leans)["value"] == value


def test_the_problem_is_reported_with_the_side_scheme_and_axis_it_is_in(tmp_path):
    with pytest.raises(CommandError) as caught:
        K.run_file(K.write_file(tmp_path, bad_leans(set_value(2.5))))
    message = str(caught.value)
    assert [word for word in (BAD, "pro", "compass", "economic") if word not in message] == []


# --- duplicate titles, file-level problems ---------------------------------------------------------------------------------

def test_duplicate_titles_abort_naming_the_title_and_write_nothing(tmp_path):
    entries = [K.make_entry("Alpha"), K.make_entry("Twin"), K.make_entry("Twin")]
    before = K.snapshot()
    with pytest.raises(CommandError, match="Twin"):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.snapshot() == before
    assert K.topic_count() == 0


def test_titles_that_differ_only_in_case_are_different_titles(tmp_path):
    entries = [K.make_entry("Alpha topic"), K.make_entry("alpha topic")]
    assert K.counts(K.run_file(K.write_file(tmp_path, entries))) == (2, 0, 0)


def test_duplicates_abort_even_when_the_database_already_holds_the_topic(tmp_path):
    entries = [K.make_entry("Alpha"), K.make_entry("Beta")]
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    duplicated = [K.make_entry("Alpha", description="Changed."), K.make_entry("Alpha", description="Changed again.")]
    with pytest.raises(CommandError, match="Alpha"):
        K.run_file(K.write_file(tmp_path, duplicated))
    assert K.snapshot() == before


@pytest.mark.parametrize(
    "content",
    ["", "not json at all", "[{]", '{"title": "x"}', '"a string"', "42", "null", '["just a string"]', "[[]]", "[1, 2]"],
    ids=["empty", "garbage", "broken", "object", "string", "number", "null", "list_of_string", "list_of_list", "list_of_ints"],
)
def test_a_file_that_is_not_a_list_of_entry_objects_aborts_and_writes_nothing(tmp_path, content):
    with pytest.raises(CommandError):
        K.run_file(K.write_file(tmp_path, content))
    assert K.topic_count() == 0


def test_a_missing_file_aborts_with_the_path_in_the_message(tmp_path):
    missing = str(tmp_path / "no-such-file.json")
    with pytest.raises(CommandError, match=re.escape("no-such-file.json")):
        K.run("--file", missing)
    assert K.topic_count() == 0


# --- atomic across the whole file, updates included ------------------------------------------------------------------------

def test_a_bad_entry_stops_updates_of_earlier_entries_too(tmp_path):
    entries = K.make_entries(3)
    K.run_file(K.write_file(tmp_path, entries))
    before = K.snapshot()
    edited = copy.deepcopy(entries)
    edited[0]["description"] = "This edit must not land."
    edited[1]["proposition"] = "z" * (settings.MAX_PROPOSITION_CHARS + 1)
    edited.append(K.make_entry("Testing topic Z"))
    with pytest.raises(CommandError, match="Testing topic B"):
        K.run_file(K.write_file(tmp_path, edited))
    assert K.snapshot() == before


def test_a_bad_entry_last_in_the_file_still_writes_nothing(tmp_path):
    entries = [*K.make_entries(3), K.make_entry("Testing topic Z", leans={})]
    with pytest.raises(CommandError, match="Testing topic Z"):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.topic_count() == 0


def test_a_bad_entry_first_in_the_file_writes_nothing(tmp_path):
    entries = [K.make_entry("Testing topic Z", proposition=""), *K.make_entries(3)]
    with pytest.raises(CommandError, match="Testing topic Z"):
        K.run_file(K.write_file(tmp_path, entries))
    assert K.topic_count() == 0


def test_the_first_bad_entry_is_the_one_named_when_several_are_bad(tmp_path):
    entries = [K.make_entry("Bad one", proposition=""), K.make_entry("Bad two", proposition="")]
    with pytest.raises(CommandError, match="Bad one"):
        K.run_file(K.write_file(tmp_path, entries))


def test_a_dry_run_validates_the_file_too(tmp_path):
    assert_aborts(tmp_path, with_bad(proposition=""), extra_output=("--dry-run",))
    assert K.topic_count() == 0


def test_user_created_rows_are_untouched_when_a_file_is_refused(tmp_path):
    user = K.make_user()
    K.make_user_topic(user, "Coffee is better than tea.")
    before = K.snapshot()
    with pytest.raises(CommandError):
        K.run_file(K.write_file(tmp_path, with_bad(leans={})))
    assert K.snapshot() == before


def test_a_refused_file_is_not_changed_by_the_command(tmp_path):
    path = K.write_file(tmp_path, with_bad(leans={}))
    before = open(path, encoding="utf-8").read()
    with pytest.raises(CommandError):
        K.run_file(path)
    assert open(path, encoding="utf-8").read() == before
    assert json.loads(before)[1]["title"] == BAD


def test_a_valid_file_after_a_refused_one_works_normally(tmp_path):
    with pytest.raises(CommandError):
        K.run_file(K.write_file(tmp_path, with_bad(leans={})))
    assert K.counts(K.run_file(K.write_file(tmp_path, with_bad()))) == (2, 0, 0)
