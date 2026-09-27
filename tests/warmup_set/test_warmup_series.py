"""The bike-lanes mechanical series: message length, label swap, flooding, repetition, unanswered question (both variants each)."""
import pytest
import warmup_kit as kit

FILES = kit.load_folder()
KNOWN = kit.known_of(FILES)
MEMBERS = kit.series_members(FILES)
GOLDEN_FILES = kit.load_folder(kit.GOLDEN_DIR)
GOLDEN_KNOWN = kit.known_of(GOLDEN_FILES)
GOLDEN_SERIES = kit.series_members(GOLDEN_FILES)
BY_STEM = pytest.mark.parametrize("path, data", MEMBERS, ids=[p.stem for p, _d in MEMBERS])


def of_factor(factor):
    return [d for _p, d in MEMBERS if d["series"]["factor"] == factor]


FACTORS = pytest.mark.parametrize("factor", ["message_length", "label_swap", "flooding", "repetition", "unanswered_question"])


@BY_STEM
def test_the_series_member_check_is_clean(path, data):
    assert kit.check_series_member(data, KNOWN) == []


@BY_STEM
def test_the_computed_block_equals_moderation_series_compute_features_on_the_messages(path, data):
    from moderation.series import compute_features

    assert data["computed"] == compute_features(data["messages"], data["trigger_seq"])


@BY_STEM
def test_the_spikes_series_validation_accepts_the_member_with_its_base_in_the_folder(path, data):
    assert kit.check_validators(path, data, KNOWN) == []


@BY_STEM
def test_the_base_is_a_pair_transcript_of_the_same_side_and_topic(path, data):
    base = KNOWN[data["series"]["base"]]
    assert (base["variant"], base["topic"], base.get("series")) == (data["series"]["side"], data["topic"], None)


@BY_STEM
def test_the_member_has_no_pair_id_and_no_variant_and_no_top_level_planted_key(path, data):
    assert (data["pair_id"], data["variant"], "planted" in data) == (None, None, False)


@BY_STEM
def test_the_series_object_has_exactly_the_documented_keys(path, data):
    assert sorted(data["series"]) == ["base", "factor", "id", "level", "side"]


def test_the_group_check_is_clean():
    assert kit.check_series_groups(FILES) == []


def test_every_series_id_and_level_has_exactly_one_left_and_one_right_member():
    seen = {}
    for _p, d in MEMBERS:
        seen.setdefault((d["series"]["id"], d["series"]["level"]), []).append(d["series"]["side"])
    assert {key: sorted(sides) for key, sides in seen.items()} == {key: ["left", "right"] for key in seen}


@FACTORS
def test_each_factor_has_members_on_both_sides(factor):
    sides = [d["series"]["side"] for d in of_factor(factor)]
    assert (sides.count("left") > 0, sides.count("left") == sides.count("right"), set(sides)) == (True, True, {"left", "right"})


def test_message_length_members_use_the_documented_ladder_levels_for_their_base_kind():
    levels = {}
    for d in of_factor("message_length"):
        (_m, item), = kit.planted_items(d)
        levels.setdefault(item["dimension"], set()).add(d["series"]["level"])
    assert all(found <= {"short", "long", "very_long"} for found in levels.values())


def test_every_length_member_hits_its_target_length_within_ten_percent():
    kind = {"factual_accuracy": "factual", "abusiveness": "abusive"}
    measured = [
        (d["id"], kit.chars(kit.trigger_of(d)["text"]), kit.LENGTH_TARGETS[kind[kit.planted_items(d)[0][1]["dimension"]]][d["series"]["level"]])
        for d in of_factor("message_length")
    ]
    assert (len(measured) > 0, [m for m in measured if abs(m[1] - m[2]) > 0.1 * m[2]]) == (True, [])


def test_length_members_equal_their_base_before_the_trigger_message_and_keep_the_planted_item():
    for d in of_factor("message_length"):
        base = KNOWN[d["series"]["base"]]
        cut = d["trigger_seq"] - 1
        assert [(m["seq"], m["author"], m["text"]) for m in d["messages"][:cut]] == [
            (m["seq"], m["author"], m["text"]) for m in base["messages"][:cut]
        ]
        assert [p for _m, p in kit.planted_items(d)] == [p for _m, p in kit.planted_items(base)]


def test_length_elaboration_adds_no_digit_question_or_checkable_assertion():
    for d in of_factor("message_length"):
        new, old = kit.trigger_of(d)["text"], kit.trigger_of(KNOWN[d["series"]["base"]])["text"]
        assert (kit.digit_tokens(new), new.count("?"), kit.assertions(new)) == (kit.digit_tokens(old), old.count("?"), kit.assertions(old))


def test_label_swap_members_swap_every_label_and_the_stances_and_keep_the_text():
    for d in of_factor("label_swap"):
        base = KNOWN[d["series"]["base"]]
        flip = {"Participant A": "Participant B", "Participant B": "Participant A"}
        assert d["series"]["level"] == "swapped"
        assert [m["text"] for m in d["messages"]] == [m["text"] for m in base["messages"]]
        assert [m["author"] for m in d["messages"]] == [flip[m["author"]] for m in base["messages"]]
        assert d["stances"] == {flip[k]: v for k, v in base["stances"].items()}
        assert d["messages"][0]["author"] == "Participant B"


def test_in_a_flooding_member_one_participant_posts_four_in_a_row_and_the_fourth_is_the_trigger():
    for d in of_factor("flooding"):
        authors = [m["author"] for m in d["messages"]]
        run = kit._runs(d["messages"])
        assert (len(run), run[-1]["seq"], d["trigger_seq"], d["computed"]["longest_consecutive_run"], len(set(m["author"] for m in run))) == (
            4, d["trigger_seq"], d["trigger_seq"], 4, 1,
        ), authors


def test_in_a_repetition_member_the_same_sentence_appears_in_three_messages_of_one_participant():
    for d in of_factor("repetition"):
        repeats = kit._repeats(d["messages"])
        assert (len(repeats), [len(seqs) for seqs in repeats.values()], d["computed"]["repeated_sentence_across_messages"]) == (
            1, [3], True,
        )


def test_in_an_unanswered_question_member_a_asks_and_b_replies_twice_without_a_question_mark():
    for d in of_factor("unanswered_question"):
        assert (kit._unanswered(d["messages"]), d["computed"]["unanswered_question_followed_by_two_replies"]) == (
            ("Participant A", "Participant B"), True,
        )


@pytest.mark.parametrize("factor", ["flooding", "repetition", "unanswered_question"])
def test_the_participant_who_breaks_the_process_is_pro_on_the_left_member_and_con_on_the_right(factor):
    got = {d["series"]["side"]: d["stances"][kit.violator_of(d)] for d in of_factor(factor)}
    assert got == {"left": "pro", "right": "con"}


@pytest.mark.parametrize("factor", ["flooding", "repetition", "unanswered_question"])
def test_process_members_plant_nothing_and_add_no_digits(factor):
    assert [(d["id"], len(kit.planted_items(d)), sum(kit.digit_tokens(m["text"]) for m in d["messages"])) for d in of_factor(factor)] == [
        (d["id"], 0, 0) for d in of_factor(factor)
    ]


@pytest.mark.parametrize("factor", ["flooding", "repetition", "unanswered_question"])
def test_process_members_mirror_each_other_across_the_sides_in_length(factor):
    by_side = {d["series"]["side"]: d for d in of_factor(factor)}
    left, right = by_side["left"], by_side["right"]
    ratios = [(a["seq"], kit.chars(a["text"]), kit.chars(b["text"])) for a, b in zip(left["messages"], right["messages"])]
    assert (len(left["messages"]), left["trigger_seq"], [t for t in ratios if abs(t[1] - t[2]) > 0.1 * max(t[1], t[2])]) == (
        len(right["messages"]), right["trigger_seq"], [],
    )


def test_every_length_member_plants_exactly_the_one_item_of_its_base():
    """Each message_length member plants exactly one item (the base's), never a second one."""
    assert [len(kit.planted_items(d)) for d in of_factor("message_length")] == [1] * len(of_factor("message_length"))


# --- calibration: the same checks accept the reviewed golden series (so they are not stricter than the convention) ------------

@pytest.mark.parametrize("path, data", GOLDEN_SERIES, ids=[p.stem for p, _d in GOLDEN_SERIES])
def test_calibration_the_series_member_check_accepts_every_golden_series_member(path, data):
    assert kit.check_series_member(data, GOLDEN_KNOWN, topic_title=None) == []


@pytest.mark.parametrize("factor, shared", [("flooding", 1), ("repetition", 3), ("unanswered_question", 3)])
def test_process_members_reuse_the_opening_messages_of_their_base_word_for_word(factor, shared):
    """The writer's notes say the process series keep the base's opening (1, 3 and 3 messages); the base is a reviewed text."""
    for d in of_factor(factor):
        base = KNOWN[d["series"]["base"]]
        assert [(m["seq"], m["author"], m["text"]) for m in d["messages"][:shared]] == [
            (m["seq"], m["author"], m["text"]) for m in base["messages"][:shared]
        ]
