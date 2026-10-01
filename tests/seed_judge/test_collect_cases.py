"""collect_cases: one Case per conversation of an experiment, read from what a replay left in the database."""
import judge_kit as kit
import pytest


def collect(name=kit.EXPERIMENT):
    from seeding import judge

    return judge.collect_cases(name)


def by_key(cases):
    return {(c.conversation_id, c.assignment): c for c in cases}


class TestParsingTheTranscriptId:
    @pytest.mark.parametrize(
        "fact_id, side, arm, level, is_error",
        [
            ("range_fact", "left", "true", None, False),
            ("range_fact", "right", "l1", 1, True),
            ("range_fact", "left", "l2", 2, True),
            ("range_fact", "right", "l3", 3, True),
            ("law_fact", "left", "err", None, True),
            ("law_fact", "right", "true", None, False),
        ],
    )
    def test_fact_side_arm_level_and_error_flag(self, fact_id, side, arm, level, is_error):
        kit.add_conversation(fact_id=fact_id, side=side, arm=arm)
        (case,) = collect()
        assert (case.conversation_id, case.fact_id, case.side, case.arm, case.level, case.is_error_arm) == (
            f"{fact_id}_{side}_{arm}", fact_id, side, arm, level, is_error)

    def test_a_fact_id_with_underscores_is_kept_whole(self):
        kit.add_conversation(fact_id="law_fact", side="right", arm="err")
        (case,) = collect()
        assert case.fact_id == "law_fact"


class TestClaims:
    @pytest.mark.parametrize("side, arm", [("left", "l1"), ("left", "l3"), ("right", "l2")])
    def test_error_arm_claims_come_from_the_planted_item(self, side, arm):
        kit.add_conversation(side=side, arm=arm)
        (case,) = collect()
        assert (case.false_claim, case.true_claim) == (kit.RANGE_FALSE[(side, arm)], kit.RANGE_TRUE)

    def test_a_non_statistic_error_arm_uses_its_hand_written_error(self):
        kit.add_conversation(fact_id="law_fact", side="left", arm="err")
        (case,) = collect()
        assert (case.false_claim, case.true_claim) == (kit.LAW_FALSE["left"], kit.LAW_TRUE)

    def test_a_true_arm_has_no_false_claim_and_takes_the_true_claim_from_the_fact_bank(self):
        kit.add_conversation(side="left", arm="true")
        (case,) = collect()
        assert (case.false_claim, case.true_claim) == (None, kit.RANGE_TRUE)


class TestResponse:
    def test_the_response_text_joins_the_valid_acts_with_a_blank_line(self):
        kit.add_conversation(acts=[("correct_factual_error", "First act text."), ("provide_information", "Second act text.")])
        (case,) = collect()
        assert case.response_text == "First act text.\n\nSecond act text."

    def test_counts_words_types_and_intervened(self):
        kit.add_conversation(acts=[("correct_factual_error", "One two three."), ("provide_information", "Four five.")],
                             issues=["Between 750 and 840 things exist", "another quote"])
        (case,) = collect()
        assert (case.intervened, case.n_acts, case.n_issues, case.response_words, case.act_types, case.issue_quotes) == (
            True, 2, 2, 5, ["correct_factual_error", "provide_information"],
            ["Between 750 and 840 things exist", "another quote"])

    def test_no_act_means_no_intervention_and_an_empty_response(self):
        kit.add_conversation(acts=[], issues=["quoted"])
        (case,) = collect()
        assert (case.intervened, case.n_acts, case.n_issues, case.response_text, case.response_words, case.act_types) == (
            False, 0, 1, "", 0, [])

    def test_a_rejected_act_is_not_a_posted_response(self):
        kit.add_conversation(acts=[("correct_factual_error", "Posted text.")], rejected_acts=["Never posted text."])
        (case,) = collect()
        assert (case.response_text, case.n_acts) == ("Posted text.", 1)

    def test_the_run_status_is_recorded(self):
        kit.add_conversation()
        (case,) = collect()
        assert case.run_status == "done"


class TestWhichConversationsAreCollected:
    def test_both_assignments_give_a_case_each(self):
        kit.add_conversation(assignment="as-is", acts=[("correct_factual_error", "As is text.")])
        kit.add_conversation(assignment="swapped", acts=[("correct_factual_error", "Swapped text.")])
        cases = by_key(collect())
        assert {k: c.response_text for k, c in cases.items()} == {
            ("range_fact_left_l2", "as-is"): "As is text.", ("range_fact_left_l2", "swapped"): "Swapped text."}

    def test_the_swapped_case_is_named_by_the_plain_transcript_id(self):
        kit.add_conversation(assignment="swapped")
        (case,) = collect()
        assert (case.conversation_id, case.assignment) == ("range_fact_left_l2", "swapped")

    @pytest.mark.parametrize("status", ["failed", "pending", "running", "skipped_budget", None])
    def test_a_conversation_without_a_finished_run_is_skipped(self, status):
        kit.add_conversation(run=status)
        assert collect() == []

    def test_only_the_finished_conversations_are_kept(self):
        kit.add_conversation(side="left", arm="l1", run="done")
        kit.add_conversation(side="left", arm="l2", run="failed")
        kit.add_conversation(side="right", arm="l3", run=None)
        assert [c.conversation_id for c in collect()] == ["range_fact_left_l1"]

    def test_the_skipped_conversations_are_reported(self):
        from seeding import judge

        kit.add_conversation(side="left", arm="l1", run="done")
        kit.add_conversation(side="left", arm="l2", run="failed")
        cases, skipped = judge.collect(kit.EXPERIMENT)
        assert ([c.conversation_id for c in cases], skipped) == (["range_fact_left_l1"], ["range_fact_left_l2"])

    def test_another_experiment_is_not_mixed_in(self):
        kit.add_conversation(experiment="other", side="right", arm="l1")
        kit.add_conversation(side="left", arm="l3")
        assert [c.conversation_id for c in collect()] == ["range_fact_left_l3"]

    def test_an_unknown_experiment_is_an_error(self):
        with pytest.raises(Exception):
            collect("no_such_experiment")
