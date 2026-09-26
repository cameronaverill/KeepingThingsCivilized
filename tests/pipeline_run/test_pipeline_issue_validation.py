"""Item-level validation of the Master's issues (plan section 6, brief "5b details" step 3): a bad issue is stored rejected
with a reason and the run goes on."""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db

TEXT_4 = "Landlords don’t ever leave; nobody disagrees on that. Costs are up 40% overall."
SPECS = [
    ("A", "Rents rose sharply in my city last year."),
    ("B", "That is not what the data shows, and you are being unreasonable."),
    ("mod", "Please keep to the substance of the proposition."),
    ("A", TEXT_4),
]
NON_CROSS = ["unsupported_claim", "possible_factual_error", "unclear_statement", "fallacy", "abusive_language"]
CROSS = ["repetition", "strawman", "process_violation"]
ALL_TYPES = NON_CROSS + CROSS


def setup():
    world = kit.build(SPECS)
    return world, kit.new_run(world[4])


def declining(*ids):
    return kit.interv_d("no_intervention", "Nothing to add.", [kit.disp_d(i, "declined") for i in ids])


def only_rejected(fake, world, run, *issues):
    """Run with a Master that reports `issues`, expecting that none is valid (so no Intervenor call is scripted)."""
    client = fake(kit.master_d(*issues))
    _, stored = kit.go(run)
    assert len(client.calls) == 1
    return stored


class TestRejectionReasons:
    def test_an_unknown_message_id_is_rejected_and_stored(self, fake):
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", 10**9, "unsupported_claim", kit.QUOTE_1))
        assert stored.status == "done"
        assert kit.issue_summary(stored) == [("i1", "rejected", "unknown_message")]
        (issue,) = kit.issues_of(stored)
        assert issue.issue_type == "unsupported_claim"
        assert issue.message.conversation_id == world.conv.pk

    def test_a_message_older_than_the_transcript_window_is_rejected_as_outside_window(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=2)  # the window is messages 3 and 4
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[1], "repetition", "Rents rose sharply"))
        assert kit.issue_summary(stored) == [("i1", "rejected", "outside_window")]
        assert kit.issues_of(stored)[0].message_id == world[1].pk

    def test_outside_window_is_reported_before_the_only_new_issues_rule(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=2)
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[1], "unsupported_claim", "Rents rose sharply"))
        assert kit.issue_summary(stored) == [("i1", "rejected", "outside_window")]

    def test_a_message_inside_a_window_of_the_configured_size_is_accepted(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=3)  # the window is messages 2, 3 and 4
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[2], "repetition", "you are being unreasonable")), declining("i1"))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "valid", "")]

    def test_a_message_newer_than_the_snapshot_is_outside_the_window(self, fake):
        world = kit.build(SPECS)
        run = kit.new_run(world[2])  # a stale run: messages 3 and 4 came later
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[4], "repetition", kit.QUOTE_1))
        assert kit.issue_summary(stored) == [("i1", "rejected", "outside_window")]

    def test_a_message_of_another_conversation_is_rejected(self, fake):
        world, run = setup()
        foreign = kit.other_world_message()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", foreign, "repetition", "parking"))
        (summary,) = kit.issue_summary(stored)
        assert summary[:2] == ("i1", "rejected")
        assert summary[2] in ("unknown_message", "outside_window")
        assert kit.issues_of(stored)[0].message.conversation_id == world.conv.pk

    @pytest.mark.parametrize("issue_type", ["repetition", "unsupported_claim"])
    def test_an_issue_on_a_moderator_message_is_rejected_as_moderator_message(self, fake, issue_type):
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[3], issue_type, "substance of the proposition"))
        assert kit.issue_summary(stored) == [("i1", "rejected", "moderator_message")]
        assert kit.issues_of(stored)[0].message_id == world[3].pk

    def test_an_issue_on_a_moderator_message_never_reaches_the_intervenor(self, fake):
        world, run = setup()
        master = kit.master_d(
            kit.issue_d("i1", world[3], "repetition", "substance of the proposition"),
            kit.issue_d("i2", world[4]),
        )
        client = fake(master, declining("i2"))
        _, stored = kit.go(run)
        (call,) = kit.calls_of(client, "intervenor")
        assert "substance of the proposition" not in kit.block(kit.user_input(call), "issues")
        assert kit.issue_summary(stored) == [("i1", "rejected", "moderator_message"), ("i2", "valid", "")]

    @pytest.mark.parametrize("issue_type", NON_CROSS)
    def test_an_old_message_with_a_type_that_is_not_cross_message_is_rejected_as_not_new(self, fake, issue_type):
        world, run = setup()
        stored = only_rejected(
            fake, world, run, kit.issue_d("i1", world[2], issue_type, "you are being unreasonable")
        )
        assert kit.issue_summary(stored) == [("i1", "rejected", "not_new")]
        assert kit.issues_of(stored)[0].message_id == world[2].pk

    @pytest.mark.parametrize("issue_type", CROSS)
    def test_an_old_message_with_a_cross_message_type_is_accepted(self, fake, issue_type):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[2], issue_type, "you are being unreasonable")), declining("i1"))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "valid", "")]

    @pytest.mark.parametrize("issue_type", ALL_TYPES)
    def test_the_newest_message_accepts_every_issue_type(self, fake, issue_type):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[4], issue_type, kit.QUOTE_1)), declining("i1"))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert kit.issues_of(stored)[0].issue_type == issue_type

    def test_the_newest_message_is_the_trigger_even_when_newer_messages_exist(self, fake):
        world = kit.build(SPECS)
        run = kit.new_run(world[2])  # stale: the newest message of ITS transcript is message 2
        fake(kit.master_d(kit.issue_d("i1", world[2], "unsupported_claim", "you are being unreasonable")), declining("i1"))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [("i1", "valid", "")]

    def test_a_moderator_message_is_reported_before_the_only_new_issues_rule(self, fake):
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[3], "fallacy", "no such phrase"))
        assert kit.issue_summary(stored) == [("i1", "rejected", "moderator_message")]

    def test_the_only_new_issues_rule_is_reported_before_a_missing_quote(self, fake):
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[2], "fallacy", "no such phrase"))
        assert kit.issue_summary(stored) == [("i1", "rejected", "not_new")]

    def test_a_duplicate_local_id_is_rejected_and_the_first_issue_stays_valid(self, fake):
        world, run = setup()
        master = kit.master_d(
            kit.issue_d("i1", world[4], "unsupported_claim", kit.QUOTE_1),
            kit.issue_d("i1", world[4], "fallacy", "Costs are up 40% overall"),
        )
        fake(master, declining("i1"))
        _, stored = kit.go(run)
        rows = kit.issues_of(stored)
        assert [(i.validity, i.rejection_reason) for i in rows] == [("valid", ""), ("rejected", "duplicate_id")]
        assert (rows[0].local_id, rows[0].issue_type, rows[0].quote) == ("i1", "unsupported_claim", kit.QUOTE_1)
        assert rows[1].local_id != rows[0].local_id

    def test_an_intensity_on_an_issue_type_without_a_dimension_is_rejected(self, fake):
        world, run = setup()
        stored = only_rejected(
            fake, world, run, kit.issue_d("i1", world[4], "unsupported_claim", kit.QUOTE_1, intensity=2)
        )
        assert kit.issue_summary(stored) == [("i1", "rejected", "intensity_without_dimension")]


class TestQuotes:
    def test_an_unfindable_quote_is_rejected_with_no_offsets_while_the_rest_of_the_run_succeeds(self, fake):
        world, run = setup()
        master = kit.master_d(
            kit.issue_d("i1", world[4], "unsupported_claim", "zzz absent phrase"),
            kit.issue_d("i2", world[4], "fallacy", "Costs are up 40% overall"),
        )
        interv = kit.interv_d(
            dispositions=[kit.disp_d("i2")], acts=[kit.act_d(issues=["i2"], messages=[world[4]])]
        )
        client = fake(master, interv)
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert stored.decision == "intervene"
        bad, good = kit.issues_of(stored)
        assert (bad.validity, bad.rejection_reason, bad.quote_match) == ("rejected", "quote_not_found", "not_found")
        assert (bad.quote_start, bad.quote_end) == (None, None)
        assert bad.quote == "zzz absent phrase"
        assert (good.validity, good.quote_match) == ("valid", "exact")
        (call,) = kit.calls_of(client, "intervenor")
        shown = kit.block(kit.user_input(call), "issues")
        assert "Costs are up 40% overall" in shown
        assert "zzz absent phrase" not in shown

    @pytest.mark.parametrize("quote", ["", "   "])
    def test_an_empty_quote_is_not_found(self, fake, quote):
        world, run = setup()
        stored = only_rejected(fake, world, run, kit.issue_d("i1", world[4], "unsupported_claim", quote))
        assert kit.issue_summary(stored) == [("i1", "rejected", "quote_not_found")]

    def test_a_quote_differing_in_apostrophe_style_is_a_normalized_match_with_offsets_into_the_original(self, fake):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[4], "unsupported_claim", "don't ever leave")), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.quote_match) == ("valid", "normalized")
        assert TEXT_4[issue.quote_start:issue.quote_end] == "don’t ever leave"

    def test_a_quote_differing_in_case_is_a_normalized_match(self, fake):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[4], "unsupported_claim", "NOBODY DISAGREES ON THAT")), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.quote_match) == ("valid", "normalized")
        assert TEXT_4[issue.quote_start:issue.quote_end] == "nobody disagrees on that"

    def test_a_quote_that_is_the_whole_message_is_valid(self, fake):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[4], "unsupported_claim", TEXT_4)), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.quote_match, issue.quote_start, issue.quote_end) == ("valid", "exact", 0, len(TEXT_4))

    def test_an_exact_match_uses_the_first_occurrence(self, fake):
        world = kit.build([("A", "It is so. It is so. It is so."), ("B", "Why? It is so. It is so.")])
        run = kit.new_run(world[2])
        fake(kit.master_d(kit.issue_d("i1", world[2], "unsupported_claim", "It is so.")), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.quote_start, issue.quote_end) == (5, 14)


class TestDimensionsAndIntensity:
    def test_an_intensity_of_zero_is_kept_for_a_dimension_type(self, fake):
        world, run = setup()
        fake(
            kit.master_d(kit.issue_d("i1", world[4], "possible_factual_error", "Costs are up 40% overall", intensity=0)),
            declining("i1"),
        )
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.dimension, issue.intensity) == ("valid", "factual_accuracy", 0)

    def test_an_abusive_language_issue_carries_its_dimension_and_intensity(self, fake):
        world, run = setup()
        fake(
            kit.master_d(kit.issue_d("i1", world[4], "abusive_language", kit.QUOTE_1, intensity=3)), declining("i1")
        )
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.dimension, issue.intensity) == ("abusiveness", 3)

    def test_a_dimension_type_without_an_intensity_is_valid_with_a_null_intensity(self, fake):
        world, run = setup()
        fake(kit.master_d(kit.issue_d("i1", world[4], "possible_factual_error", "Costs are up 40% overall")), declining("i1"))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.validity, issue.dimension, issue.intensity) == ("valid", "factual_accuracy", None)


class TestMixedRuns:
    def test_a_mix_of_valid_and_rejected_issues_keeps_all_of_them_and_shows_the_intervenor_only_the_valid_ones(self, fake):
        world, run = setup()
        master = kit.master_d(
            kit.issue_d("i1", world[4], "unsupported_claim", kit.QUOTE_1),
            kit.issue_d("i2", world[4], "fallacy", "zzz absent phrase"),
            kit.issue_d("i3", 10**9, "fallacy", "zzz unknown message phrase"),
            kit.issue_d("i4", world[2], "unclear_statement", "you are being unreasonable"),
        )
        client = fake(master, declining("i1"))
        _, stored = kit.go(run)
        assert kit.issue_summary(stored) == [
            ("i1", "valid", ""), ("i2", "rejected", "quote_not_found"),
            ("i3", "rejected", "unknown_message"), ("i4", "rejected", "not_new"),
        ]  # fmt: skip
        (call,) = kit.calls_of(client, "intervenor")
        shown = kit.block(kit.user_input(call), "issues")
        assert kit.QUOTE_1 in shown
        for absent in ("zzz absent phrase", "zzz unknown message phrase", "you are being unreasonable"):
            assert absent not in shown
        from moderation.models import IssueDisposition

        assert list(IssueDisposition.objects.filter(issue__run=stored).values_list("issue__local_id", flat=True)) == ["i1"]
