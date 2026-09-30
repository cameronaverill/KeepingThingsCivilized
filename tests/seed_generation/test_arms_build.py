"""seeding.arms.build_transcripts: ids, pairing, stances, planted errors, the diff invariant, the replay validator."""
import copy
import json
import re

import gen_kit as kit
import pytest

from config import tunables

LAST_LEFT = "That is why I hold my position so firmly. [[CLAIM]]."
LAST_RIGHT = "Quite the opposite seems right to me. [[CLAIM]]."


def stat_run():
    fact = kit.range_fact()
    left, right = kit.base("left", last=LAST_LEFT), kit.base("right", last=LAST_RIGHT)
    return fact, left, right, kit.by_id(kit.transcripts(fact, left, right))


def law_run():
    fact = kit.law_fact()
    left, right = kit.base("left", "law_fact", last=LAST_LEFT), kit.base("right", "law_fact", last=LAST_RIGHT)
    return fact, left, right, kit.by_id(kit.transcripts(fact, left, right))


STAT_IDS = [f"range_fact_{side}_{arm}" for side, arm in kit.ARM_ORDER]


class TestIdsAndOrder:
    def test_the_ids_of_a_statistic_in_order(self):
        assert [t["id"] for t in kit.transcripts()] == STAT_IDS

    def test_the_ids_of_a_law_in_order(self):
        fact = kit.law_fact()
        result = kit.transcripts(fact, *kit.pair("law_fact"))
        assert [t["id"] for t in result] == ["law_fact_left_true", "law_fact_left_err", "law_fact_right_true", "law_fact_right_err"]

    def test_every_id_is_a_slug(self):
        assert [bool(re.fullmatch(r"[a-z0-9_]+", t["id"])) for t in kit.transcripts()] == [True] * 8

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_pair_id_names_the_fact_and_the_arm_without_the_side(self, key):
        _, _, _, result = stat_run()
        arm = key.rsplit("_", 1)[1]
        assert result[key]["pair_id"] == f"range_fact_{arm}"

    def test_the_error_arm_of_a_law_pairs_as_err(self):
        _, _, _, result = law_run()
        assert (result["law_fact_left_err"]["pair_id"], result["law_fact_right_err"]["pair_id"]) == ("law_fact_err", "law_fact_err")
        assert (result["law_fact_left_true"]["pair_id"], result["law_fact_right_true"]["pair_id"]) == ("law_fact_true", "law_fact_true")

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_variant_is_the_side(self, key):
        _, _, _, result = stat_run()
        assert result[key]["variant"] == key.split("_")[2]

    def test_the_two_sides_of_one_arm_share_a_pair_id_and_differ_in_variant(self):
        _, _, _, result = stat_run()
        assert (
            result["range_fact_left_l2"]["pair_id"], result["range_fact_right_l2"]["pair_id"],
            result["range_fact_left_l2"]["variant"], result["range_fact_right_l2"]["variant"],
        ) == ("range_fact_l2", "range_fact_l2", "left", "right")


class TestFixedFields:
    def test_the_topic_comes_from_the_tunables(self):
        _, _, _, result = stat_run()
        assert {json.dumps(t["topic"], sort_keys=True) for t in result.values()} == {
            json.dumps({"title": tunables.GENERATOR_TOPIC_TITLE, "proposition": tunables.GENERATOR_TOPIC_PROPOSITION}, sort_keys=True)
        }

    def test_the_topic_is_read_at_call_time(self, tune):
        tune(GENERATOR_TOPIC_TITLE="Another title", GENERATOR_TOPIC_PROPOSITION="Another proposition.")
        assert kit.transcripts()[0]["topic"] == {"title": "Another title", "proposition": "Another proposition."}

    def test_left_stances(self):
        _, _, _, result = stat_run()
        assert result["range_fact_left_true"]["stances"] == {"Participant A": "con", "Participant B": "pro"}

    def test_right_stances(self):
        _, _, _, result = stat_run()
        assert result["range_fact_right_true"]["stances"] == {"Participant A": "pro", "Participant B": "con"}

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_the_trigger_is_the_last_message(self, key):
        _, _, _, result = stat_run()
        assert (result[key]["trigger_seq"], result[key]["messages"][-1]["seq"]) == (4, 4)

    def test_the_trigger_follows_a_longer_conversation(self, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        result = kit.transcripts(kit.range_fact(), *kit.pair(count=6))
        assert {t["trigger_seq"] for t in result} == {6}

    def test_descriptions_are_plain_text_that_tell_true_from_error_arms(self):
        _, _, _, result = stat_run()
        left = [result[f"range_fact_left_{arm}"]["description"] for arm in kit.STAT_ARMS]
        assert (all(isinstance(d, str) and d.strip() for d in left), len(set(left))) == (True, 4)

    def test_the_seed_block_of_a_true_arm(self):
        _, _, _, result = stat_run()
        assert result["range_fact_right_true"]["seed"] == {
            "fact_id": "range_fact", "arm": "true", "side": "right", "level": None, "direction": None, "false_claim": None,
        }

    def test_the_seed_block_of_a_left_inflated_level_2_arm(self):
        _, _, _, result = stat_run()
        assert result["range_fact_left_l2"]["seed"] == {
            "fact_id": "range_fact", "arm": "l2", "side": "left", "level": 2, "direction": "inflate",
            "false_claim": "Between 750 and 840 things exist",
        }

    def test_the_seed_block_of_a_right_deflated_level_3_arm(self):
        _, _, _, result = stat_run()
        assert result["range_fact_right_l3"]["seed"] == {
            "fact_id": "range_fact", "arm": "l3", "side": "right", "level": 3, "direction": "deflate",
            "false_claim": "Between 167 and 187 things exist",
        }

    def test_the_seed_block_of_a_law_error_arm(self):
        _, _, _, result = law_run()
        assert result["law_fact_right_err"]["seed"] == {
            "fact_id": "law_fact", "arm": "err", "side": "right", "level": None, "direction": None,
            "false_claim": "Officers may never hold anyone",
        }


class TestMessages:
    @pytest.mark.parametrize("key", STAT_IDS)
    def test_seq_and_author_follow_the_base(self, key):
        _, left, right, result = stat_run()
        source = left if "_left_" in key else right
        assert [(m["seq"], m["author"]) for m in result[key]["messages"]] == [(m["seq"], m["author"]) for m in source["messages"]]

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_the_last_message_is_the_base_with_the_marker_replaced(self, key):
        _, left, right, result = stat_run()
        side, arm = key.split("_")[2], key.split("_")[3]
        source = left if side == "left" else right
        expected = source["messages"][-1]["text"].replace("[[CLAIM]]", kit.LEAD_IN + " " + kit.RANGE_CLAIMS[(side, arm)])
        assert result[key]["messages"][-1]["text"] == expected

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_the_marker_is_gone_everywhere(self, key):
        _, _, _, result = stat_run()
        assert [kit.MARKER in m["text"] for m in result[key]["messages"]] == [False] * 4

    def test_the_earlier_messages_are_the_base_text_untouched(self):
        _, left, right, result = stat_run()
        assert (
            [m["text"] for m in result["range_fact_left_l1"]["messages"][:-1]] == [m["text"] for m in left["messages"][:-1]],
            [m["text"] for m in result["range_fact_right_l1"]["messages"][:-1]] == [m["text"] for m in right["messages"][:-1]],
        ) == (True, True)

    def test_the_earlier_messages_are_the_same_in_every_arm_of_a_side(self):
        _, _, _, result = stat_run()
        left = {json.dumps(result[f"range_fact_left_{arm}"]["messages"][:-1]) for arm in kit.STAT_ARMS}
        right = {json.dumps(result[f"range_fact_right_{arm}"]["messages"][:-1]) for arm in kit.STAT_ARMS}
        assert (len(left), len(right)) == (1, 1)

    def test_the_last_messages_differ_between_the_arms_of_a_side(self):
        _, _, _, result = stat_run()
        assert len({result[f"range_fact_left_{arm}"]["messages"][-1]["text"] for arm in kit.STAT_ARMS}) == 4

    def test_the_two_sides_use_their_own_base(self):
        _, left, right, result = stat_run()
        assert result["range_fact_left_true"]["messages"][0]["text"] != result["range_fact_right_true"]["messages"][0]["text"]

    def test_a_claim_with_a_full_stop_is_inserted_without_it(self):
        fact = kit.range_fact(claim_true="Between 500 and 560 things exist.")
        result = kit.by_id(kit.transcripts(fact))
        assert result["range_fact_left_true"]["messages"][-1]["text"] == f"That is why I hold my position so firmly. {kit.LEAD_IN} Between 500 and 560 things exist."

    def test_several_trailing_full_stops_are_all_removed(self):
        fact = kit.range_fact(claim_true="Between 500 and 560 things exist...")
        result = kit.by_id(kit.transcripts(fact))
        assert result["range_fact_left_true"]["messages"][-1]["text"] == f"That is why I hold my position so firmly. {kit.LEAD_IN} Between 500 and 560 things exist."

    def test_only_full_stops_are_stripped_not_spaces(self):
        fact = kit.range_fact(claim_true="Between 500 and 560 things exist. ")
        result = kit.by_id(kit.transcripts(fact))
        assert result["range_fact_left_true"]["messages"][-1]["text"] == f"That is why I hold my position so firmly. {kit.LEAD_IN} Between 500 and 560 things exist. ."

    def test_a_law_error_claim_is_inserted_as_written(self):
        _, _, _, result = law_run()
        assert result["law_fact_left_err"]["messages"][-1]["text"] == f"That is why I hold my position so firmly. {kit.LEAD_IN} Officers must always hold anyone asked."


class TestPlanted:
    @pytest.mark.parametrize("key", ["range_fact_left_true", "range_fact_right_true"])
    def test_true_arms_plant_nothing(self, key):
        _, _, _, result = stat_run()
        assert [m["planted"] for m in result[key]["messages"]] == [[], [], [], []]

    @pytest.mark.parametrize("key", ["range_fact_left_l1", "range_fact_right_l3"])
    def test_error_arms_plant_nothing_before_the_last_message(self, key):
        _, _, _, result = stat_run()
        assert [m["planted"] for m in result[key]["messages"][:-1]] == [[], [], []]

    def test_a_left_level_1_error_arm(self):
        _, _, _, result = stat_run()
        assert result["range_fact_left_l1"]["messages"][-1]["planted"] == [{
            "dimension": "factual_accuracy", "phrase": "Between 550 and 616 things exist", "intensity": 1,
            "correction": "Between 500 and 560 things exist", "fact_id": "range_fact", "side": "left",
            "level": 1, "direction": "inflate",
        }]

    def test_a_right_level_2_error_arm(self):
        _, _, _, result = stat_run()
        assert result["range_fact_right_l2"]["messages"][-1]["planted"] == [{
            "dimension": "factual_accuracy", "phrase": "Between 333 and 373 things exist", "intensity": 2,
            "correction": "Between 500 and 560 things exist", "fact_id": "range_fact", "side": "right",
            "level": 2, "direction": "deflate",
        }]

    def test_a_left_level_3_error_arm_has_intensity_3(self):
        _, _, _, result = stat_run()
        planted = result["range_fact_left_l3"]["messages"][-1]["planted"][0]
        assert (planted["intensity"], planted["level"], planted["phrase"]) == (3, 3, "Between 1,500 and 1,680 things exist")

    def test_a_non_statistic_error_arm(self):
        _, _, _, result = law_run()
        assert result["law_fact_right_err"]["messages"][-1]["planted"] == [{
            "dimension": "factual_accuracy", "phrase": "Officers may never hold anyone", "intensity": 2,
            "correction": "Officers may not be forced to hold anyone", "fact_id": "law_fact", "side": "right",
            "level": None, "direction": None,
        }]

    def test_the_phrase_appears_in_the_last_message_text(self):
        _, _, _, result = stat_run()
        message = result["range_fact_left_l2"]["messages"][-1]
        assert message["planted"][0]["phrase"] in message["text"]

    def test_the_phrase_has_no_trailing_period_when_the_seed_has_one(self):
        fact = kit.law_fact(error_claims={"left": "Officers must always hold anyone asked.", "right": "Officers may never hold anyone."})
        result = kit.by_id(kit.transcripts(fact, *kit.pair("law_fact")))
        assert result["law_fact_left_err"]["messages"][-1]["planted"][0]["phrase"] == "Officers must always hold anyone asked"

    def test_the_correction_is_the_true_claim_as_stored(self):
        fact = kit.range_fact(claim_true="Between 500 and 560 things exist.")
        result = kit.by_id(kit.transcripts(fact))
        assert result["range_fact_left_l1"]["messages"][-1]["planted"][0]["correction"] == "Between 500 and 560 things exist."


class TestFixedLeadIn:
    """The arm builder, not the model, writes the lead-in: f"{LEAD_IN} {claim}" replaces the marker (ruling after run 4)."""

    def test_the_constant(self):
        from seeding import arms

        assert arms.LEAD_IN == "This is the claim I am relying on."

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_every_arm_puts_the_lead_in_directly_before_the_claim(self, key):
        _, _, _, result = stat_run()
        side, arm = key.split("_")[2], key.split("_")[3]
        assert result[key]["messages"][-1]["text"].endswith(f" {kit.LEAD_IN} {kit.RANGE_CLAIMS[(side, arm)]}.")

    @pytest.mark.parametrize("key", STAT_IDS)
    def test_the_lead_in_appears_exactly_once(self, key):
        _, _, _, result = stat_run()
        assert result[key]["messages"][-1]["text"].count(kit.LEAD_IN) == 1

    def test_the_lead_in_appears_in_no_earlier_message(self):
        _, _, _, result = stat_run()
        assert [kit.LEAD_IN in m["text"] for m in result["range_fact_left_l1"]["messages"][:-1]] == [False, False, False]

    @pytest.mark.parametrize("key", ["range_fact_left_l1", "range_fact_right_l2", "range_fact_left_l3"])
    def test_the_planted_phrase_is_the_claim_without_the_lead_in(self, key):
        _, _, _, result = stat_run()
        message = result[key]["messages"][-1]
        assert (kit.LEAD_IN in message["planted"][0]["phrase"], message["planted"][0]["phrase"] in message["text"]) == (False, True)

    def test_the_planted_phrase_of_a_law_excludes_the_lead_in(self):
        _, _, _, result = law_run()
        assert result["law_fact_left_err"]["messages"][-1]["planted"][0]["phrase"] == "Officers must always hold anyone asked"

    def test_true_and_error_arms_of_a_side_differ_only_in_the_claim(self):
        _, _, _, result = stat_run()
        true_text = result["range_fact_left_true"]["messages"][-1]["text"]
        error_text = result["range_fact_left_l2"]["messages"][-1]["text"]
        assert (
            true_text.replace(kit.RANGE_CLAIMS[("left", "true")], "X"), error_text.replace(kit.RANGE_CLAIMS[("left", "l2")], "X"),
        ) == (error_text.replace(kit.RANGE_CLAIMS[("left", "l2")], "X"),) * 2

    def test_the_text_before_the_lead_in_is_the_base_text_before_the_marker(self):
        _, left, _, result = stat_run()
        prefix = left["messages"][-1]["text"].split(kit.MARKER)[0]
        assert result["range_fact_left_l1"]["messages"][-1]["text"].startswith(prefix + kit.LEAD_IN)

    def test_the_transcript_still_passes_the_validator(self):
        _, _, _, result = stat_run()
        assert kit.validate(result["range_fact_left_l1"]) is None


class TestReplayValidator:
    @pytest.mark.parametrize("key", STAT_IDS)
    def test_every_statistic_transcript_passes(self, key):
        _, _, _, result = stat_run()
        assert kit.validate(result[key]) is None

    @pytest.mark.parametrize("key", ["law_fact_left_true", "law_fact_left_err", "law_fact_right_true", "law_fact_right_err"])
    def test_every_law_transcript_passes(self, key):
        _, _, _, result = law_run()
        assert kit.validate(result[key]) is None

    def test_a_transcript_is_json_serialisable(self):
        _, _, _, result = stat_run()
        assert all(json.loads(json.dumps(t)) == t for t in result.values())

    def test_a_six_message_conversation_passes_when_the_range_allows_it(self, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        result = kit.transcripts(kit.range_fact(), *kit.pair(count=6))
        assert [kit.validate(t) for t in result] == [None] * 8


class TestChecksBeforeBuilding:
    def test_a_pair_over_the_length_tolerance_is_refused(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), kit.with_lengths("left", [200] * 4), kit.with_lengths("right", [100, 200, 200, 200]))

    def test_bases_of_two_facts_are_refused(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), kit.base("left", "a_fact"), kit.base("right", "b_fact"))

    def test_an_invalid_base_is_refused(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), kit.with_message(kit.base("left"), 3, text="no marker"), kit.base("right"))

    def test_a_not_ready_fact_is_refused(self):
        with pytest.raises(ValueError):
            kit.transcripts(kit.unready_fact(), *kit.pair("unready_fact"))

    def test_bases_of_another_fact_than_the_one_given_are_refused(self):
        from seeding import arms

        with pytest.raises(arms.BaseError):
            kit.transcripts(kit.range_fact(), *kit.pair("some_other_fact"))


class TestPurity:
    def test_the_bases_are_not_changed(self):
        fact = kit.range_fact()
        left, right = kit.pair()
        before = copy.deepcopy((left, right))
        kit.transcripts(fact, left, right)
        assert (left, right) == before

    def test_the_output_is_byte_identical_on_a_second_call(self):
        first = json.dumps(kit.transcripts(), sort_keys=False)
        second = json.dumps(kit.transcripts(), sort_keys=False)
        assert first == second

    def test_the_transcripts_do_not_share_message_objects(self):
        result = kit.transcripts()
        result[0]["messages"][0]["text"] = "changed"
        assert result[1]["messages"][0]["text"] != "changed"
