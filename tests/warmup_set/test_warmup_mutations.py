"""Mutation pass on copies of the warm-up files: each corruption named in the brief (and more) must be caught by the checks.

Every case copies the real folder to a scratch folder, damages ONE file (or removes or adds one), runs `check_folder` on the
copy and asserts that the expected complaint is NEW compared with the untouched copy. The real files are never modified.
"""
import json
import shutil

import pytest
import warmup_kit as kit

FILES = kit.load_folder()
FACTUAL = "factual_accuracy"
ABUSIVE = "abusiveness"
LONG_TEXT = (" " + "This extra sentence only pads the message with more plain words. " * 2).rstrip()


def left(dimension):
    return kit.pair_stem(FILES, dimension, "left")


def right(dimension):
    return kit.pair_stem(FILES, dimension, "right")


def series(factor, side, level=None):
    return kit.series_stem(FILES, factor, side, level)


def message(d, seq):
    return next(m for m in d["messages"] if m["seq"] == seq)


# --- the mutations: each takes the loaded dict and changes it in place -----------------------------------------------------

def lengthen_msg2(d):
    message(d, 2)["text"] += LONG_TEXT


def planted_on_wrong_message(d):
    message(d, 3)["planted"] = message(d, 4)["planted"]
    message(d, 4)["planted"] = []


def planted_span_moved_with_its_phrase(d):
    item = message(d, 4)["planted"][0]
    message(d, 4)["text"] = message(d, 4)["text"].replace(item["phrase"], "and that is all")
    message(d, 3)["text"] += " " + item["phrase"] + "."
    message(d, 3)["planted"] = [item]
    message(d, 4)["planted"] = []


def phrase_altered_in_text_only(d):
    message(d, 4)["text"] = message(d, 4)["text"].replace(message(d, 4)["planted"][0]["phrase"], "something else entirely")


def phrase_changed_everywhere(d):
    item = message(d, 4)["planted"][0]
    message(d, 4)["text"] = message(d, 4)["text"].replace(item["phrase"], item["phrase"] + " indeed")
    item["phrase"] += " indeed"


def swap_dimension(d):
    message(d, 4)["planted"][0]["dimension"] = "abusiveness"


def flip_intensity(d):
    item = message(d, 4)["planted"][0]
    item["intensity"] = {3: 4, 4: 3}[item["intensity"]]


def intensity_two(d):
    message(d, 4)["planted"][0]["intensity"] = 2


def drop_evidence(d):
    del message(d, 4)["planted"][0]["evidence"]


def add_digits(d):
    message(d, 2)["text"] += " In 1999 there were 40 lanes."


def add_question_mark(d):
    message(d, 2)["text"] = message(d, 2)["text"].replace(".", "?", 1)


def add_url(d):
    message(d, 2)["text"] += " See https://example.org/lanes for details."


def add_www(d):
    message(d, 2)["text"] += " See www.lanes-guide.com for details."


def add_email(d):
    message(d, 2)["text"] += " Write to someone@example.org about it."


def add_handle(d):
    message(d, 2)["text"] += " Ask @lanefan about it."


def add_label(d):
    message(d, 2)["text"] += " Participant A said so."


def add_possessive_label(d):
    message(d, 2)["text"] += " B's message was clear."


def add_name(d):
    message(d, 2)["text"] += " Sarah told me so."


def add_unreviewed_capital(d):
    message(d, 2)["text"] += " I read about Zorbo lately."


def add_study_mention(d):
    message(d, 2)["text"] += " This transcript was written for a warm-up."


def add_politics(d):
    message(d, 2)["text"] += " Democrats would not agree."


def add_profanity(d):
    message(d, 2)["text"] += " That is bullshit."


def add_insult_outside_planted(d):
    message(d, 2)["text"] += " Only an idiot would say otherwise."


def add_non_ascii(d):
    message(d, 2)["text"] += " A café stop."


def both_pro(d):
    d["stances"] = {"Participant A": "pro", "Participant B": "pro"}


def wrong_direction_stances(d):
    d["stances"] = {"Participant A": d["stances"]["Participant B"], "Participant B": d["stances"]["Participant A"]}


def change_proposition(d):
    d["topic"]["proposition"] += " Always."


def change_title(d):
    d["topic"]["title"] = "Parking"


def overlong_message(d):
    message(d, 2)["text"] = ("word " * 700).strip()


def too_many_messages(d):
    for seq in range(5, 36):
        d["messages"].append({"seq": seq, "author": "Participant A" if seq % 2 else "Participant B", "text": "A plain reply.", "planted": []})


def append_a_message(d):
    d["messages"].append({"seq": 5, "author": "Participant A", "text": "A plain reply.", "planted": []})


def bad_variant(d):
    d["variant"] = "center"


def add_extra_key(d):
    d["extra"] = 1


def seq_gap(d):
    message(d, 4)["seq"] = 9
    d["trigger_seq"] = 9


def computed_run(d):
    d["computed"]["longest_consecutive_run"] += 1


def computed_chars(d):
    d["computed"]["trigger_message_chars"] += 1


def computed_flag(d):
    d["computed"]["repeated_sentence_across_messages"] = {True: False, False: True}[d["computed"]["repeated_sentence_across_messages"]]


def computed_missing_key(d):
    del d["computed"]["trigger_message_words"]


def base_missing(d):
    d["series"]["base"] = "no_such_transcript"


def change_message_one(d):
    message(d, 1)["text"] += " More."


def keep_author_one(d):
    """Undo the swap of the first speaker only (the base opens with Participant A, a swapped member with Participant B)."""
    message(d, 1)["author"] = "Participant A"


def break_flooding_run(d):
    message(d, 3)["author"] = "Participant A"


def break_repetition(d):
    sentence = next(iter(kit._repeats(d["messages"])))[1]
    message(d, 4)["text"] = message(d, 4)["text"].replace(sentence, "A different sentence takes its place here.")


def add_question_to_reply(d):
    d["messages"][-1]["text"] += " Is that so?"


def same_side_twice(d):
    d["series"]["side"] = "left"


def violator_on_wrong_side(d):
    d["stances"] = {"Participant A": d["stances"]["Participant B"], "Participant B": d["stances"]["Participant A"]}


def shorten_very_long(d):
    message(d, 4)["text"] = message(d, 4)["text"][:1500].rsplit(" ", 1)[0] + "."


def bad_factor(d):
    d["series"]["factor"] = "whatever"


# (case id, which file, mutation, the complaint that must be new)
CASES = [
    ("length_mismatch", lambda: right(FACTUAL), lengthen_msg2, "pair: seq 2 length"),
    ("planted_on_wrong_message", lambda: right(ABUSIVE), planted_on_wrong_message, "occurs 0 times"),
    ("planted_span_moved_to_another_message", lambda: right(ABUSIVE), planted_span_moved_with_its_phrase, "planted in seq 4 vs seq 3"),
    ("planted_phrase_no_longer_in_text", lambda: left(FACTUAL), phrase_altered_in_text_only, "occurs 0 times"),
    ("planted_phrase_differs_between_variants", lambda: right(FACTUAL), phrase_changed_everywhere, "planted phrase differs"),
    ("planted_dimension_differs", lambda: right(FACTUAL), swap_dimension, "planted dimension differs"),
    ("planted_intensity_differs", lambda: right(ABUSIVE), flip_intensity, "planted intensity differs"),
    ("planted_intensity_two", lambda: left(ABUSIVE), intensity_two, "is not 3 or 4"),
    ("factual_evidence_missing", lambda: left(FACTUAL), drop_evidence, "keys"),
    ("digits_added_to_one_variant", lambda: left(ABUSIVE), add_digits, "digit tokens"),
    ("question_mark_added_to_one_variant", lambda: right(ABUSIVE), add_question_mark, "question marks"),
    ("url_added", lambda: left(FACTUAL), add_url, "contains a URL"),
    ("bare_web_address_added", lambda: left(ABUSIVE), add_www, "contains a URL"),
    ("email_added", lambda: right(FACTUAL), add_email, "contains an email address"),
    ("handle_added", lambda: right(ABUSIVE), add_handle, "contains an @handle"),
    ("participant_label_in_text", lambda: left(ABUSIVE), add_label, "spells a participant label"),
    ("possessive_label_in_text", lambda: left(FACTUAL), add_possessive_label, "spells a participant label"),
    ("personal_name_added", lambda: right(ABUSIVE), add_name, "personal name"),
    ("unreviewed_capitalised_word_added", lambda: left(FACTUAL), add_unreviewed_capital, "unreviewed mid-sentence capitalised words"),
    ("study_mention_added", lambda: right(FACTUAL), add_study_mention, "mentions the study"),
    ("political_word_added", lambda: left(ABUSIVE), add_politics, "political vocabulary"),
    ("profanity_added", lambda: right(FACTUAL), add_profanity, "profanity"),
    ("insult_outside_the_planted_phrase", lambda: left(FACTUAL), add_insult_outside_planted, "insult word"),
    ("non_ascii_added", lambda: left(ABUSIVE), add_non_ascii, "non-ASCII"),
    ("stances_not_opposite", lambda: left(FACTUAL), both_pro, "stances"),
    ("stances_in_the_wrong_direction", lambda: right(ABUSIVE), wrong_direction_stances, "reading R1"),
    ("proposition_changed", lambda: left(FACTUAL), change_proposition, "is not forum/seed_topics.json's"),
    ("title_changed", lambda: right(ABUSIVE), change_title, "is not one of"),
    ("message_over_the_character_limit", lambda: left(ABUSIVE), overlong_message, "limits: seq 2"),
    ("too_many_user_messages", lambda: right(FACTUAL), too_many_messages, "MAX_USER_MESSAGES_PER_CONVERSATION"),
    ("wrong_message_count", lambda: right(ABUSIVE), append_a_message, "message counts differ"),
    ("unknown_variant_name", lambda: left(FACTUAL), bad_variant, "variant"),
    ("extra_key_in_the_file", lambda: right(FACTUAL), add_extra_key, "differ from the golden shape"),
    ("seq_numbers_have_a_gap", lambda: left(ABUSIVE), seq_gap, "are not 1.."),
    ("computed_run_wrong", lambda: series("flooding", "left"), computed_run, "computed.longest_consecutive_run"),
    ("computed_trigger_chars_wrong", lambda: series("repetition", "right"), computed_chars, "computed.trigger_message_chars"),
    ("computed_flag_wrong", lambda: series("repetition", "left"), computed_flag, "computed.repeated_sentence_across_messages"),
    ("computed_key_missing", lambda: series("unanswered_question", "left"), computed_missing_key, "computed keys"),
    ("series_base_missing", lambda: series("flooding", "right"), base_missing, "series.base"),
    ("series_unknown_factor", lambda: series("flooding", "left"), bad_factor, "series.factor"),
    ("length_member_changed_before_the_trigger", lambda: series("message_length", "left", "short"), change_message_one, "must be identical to the base"),
    ("length_member_misses_its_target", lambda: series("message_length", "right", "very_long"), shorten_very_long, "target 2500"),
    ("swap_member_not_swapped", lambda: series("label_swap", "left"), keep_author_one, "label swapped"),
    ("flooding_run_broken", lambda: series("flooding", "left"), break_flooding_run, "flooding needs a run"),
    ("repetition_broken", lambda: series("repetition", "right"), break_repetition, "repetition needs"),
    ("unanswered_question_answered", lambda: series("unanswered_question", "left"), add_question_to_reply, "unanswered_question needs"),
    ("both_members_on_the_same_side", lambda: series("repetition", "right"), same_side_twice, "exactly one left and one right"),
    ("process_violator_on_the_wrong_side", lambda: series("flooding", "right"), violator_on_wrong_side, "should be con"),
]


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    copy = tmp_path_factory.mktemp("baseline") / "transcripts"
    shutil.copytree(kit.WARMUP_DIR, copy)
    return set(kit.check_folder(copy))


def damaged_copy(tmp_path, stem, mutate):
    copy = tmp_path / "transcripts"
    shutil.copytree(kit.WARMUP_DIR, copy)
    path = copy / f"{stem}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return copy


@pytest.mark.parametrize("name, stem, mutate, needle", CASES, ids=[c[0] for c in CASES])
def test_the_corruption_is_caught(tmp_path, baseline, name, stem, mutate, needle):
    stem_value = stem()
    new = [problem for problem in kit.check_folder(damaged_copy(tmp_path, stem_value, mutate)) if problem not in baseline]
    assert needle in "\n".join(new), f"{name}: no new complaint containing {needle!r}; new complaints: {new}"


# --- corruptions of the folder as a whole ------------------------------------------------------------------------------------

def folder_copy(tmp_path):
    copy = tmp_path / "transcripts"
    shutil.copytree(kit.WARMUP_DIR, copy)
    return copy


def new_problems(copy, baseline):
    return "\n".join(problem for problem in kit.check_folder(copy) if problem not in baseline)


def test_a_missing_pair_member_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    (copy / f"{right(FACTUAL)}.json").unlink()
    assert "has 1 members, needs exactly 2" in new_problems(copy, baseline)


def test_a_third_pair_member_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    source = json.loads((copy / f"{left(ABUSIVE)}.json").read_text(encoding="utf-8"))
    source["id"] = source["id"] + "_copy"
    (copy / f"{source['id']}.json").write_text(json.dumps(source), encoding="utf-8")
    assert "has 3 members, needs exactly 2" in new_problems(copy, baseline)


def test_a_duplicate_id_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    source = json.loads((copy / f"{left(ABUSIVE)}.json").read_text(encoding="utf-8"))
    (copy / "another_name.json").write_text(json.dumps(source), encoding="utf-8")
    assert "duplicate transcript ids" in new_problems(copy, baseline)


def test_a_stray_non_json_file_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    (copy / "README.md").write_text("notes", encoding="utf-8")
    assert "something other than .json files" in new_problems(copy, baseline)


def test_an_unparseable_json_file_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    (copy / "broken.json").write_text("{not json", encoding="utf-8")
    assert "not valid UTF-8 JSON" in new_problems(copy, baseline)


def test_a_golden_transcript_copied_into_the_folder_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    shutil.copy(kit.GOLDEN_DIR / "flooding_left.json", copy / "flooding_left.json")
    assert "clash with golden/transcripts ids" in new_problems(copy, baseline)


def test_a_missing_series_factor_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    for side in ("left", "right"):
        (copy / f"{series('flooding', side)}.json").unlink()
    assert "factors present" in new_problems(copy, baseline)


def test_a_missing_series_side_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    (copy / f"{series('repetition', 'right')}.json").unlink()
    assert "exactly one left and one right member" in new_problems(copy, baseline)


def test_a_missing_topic_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    for path in list(copy.glob("remote_*.json")):
        path.unlink()
    assert "expected 6" in new_problems(copy, baseline)


def test_a_missing_base_file_is_caught(tmp_path, baseline):
    copy = folder_copy(tmp_path)
    (copy / f"{series('flooding', 'left')}.json").write_text(
        (copy / f"{series('flooding', 'left')}.json").read_text(encoding="utf-8").replace(
            json.loads((copy / f"{series('flooding', 'left')}.json").read_text(encoding="utf-8"))["series"]["base"], "gone"
        ),
        encoding="utf-8",
    )
    assert "is not a transcript in the folder" in new_problems(copy, baseline)


# --- corruptions of the notes ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def notes_text():
    return kit.NOTES_PATH.read_text(encoding="utf-8")


def test_the_notes_baseline_is_clean(notes_text):
    assert kit.check_notes(notes_text, FILES) == []


def test_a_notes_file_without_a_pair_paragraph_is_caught(notes_text):
    pair_id = kit.pair_stem(FILES, FACTUAL, "left").rsplit("_left", 1)[0]
    assert f"notes: no paragraph names pair {pair_id}" in kit.check_notes(notes_text.replace(pair_id, "REDACTED"), FILES)


def test_a_notes_file_without_the_weaknesses_is_caught(notes_text):
    cut = notes_text.lower().index("weakness")
    assert "notes: no list of known weaknesses" in kit.check_notes(notes_text[:cut] + notes_text[cut:].replace("eakness", "XXXXXXX"), FILES)


def test_a_notes_file_that_never_says_not_political_is_caught(notes_text):
    import re

    damaged = re.sub(r"\bnot\b([^.\n]{0,80})\bpolitical\b", r"\1 political", notes_text, flags=re.I)
    assert "notes: it does not state that the variants are not political codings" in kit.check_notes(damaged, FILES)


# --- the golden-untouched guard reacts (kit.folder_hash is exercised in test_warmup_replay) ---------------------------------------

def test_the_shared_inventory_check_rejects_the_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    problems = kit.check_files([], empty, [])
    assert ("expected about 22" in "\n".join(problems), "expected 12" in "\n".join(problems)) == (True, True)
