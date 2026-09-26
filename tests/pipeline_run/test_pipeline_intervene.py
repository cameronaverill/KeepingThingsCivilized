"""The intervene path and the no-intervention paths of run_moderation (plan sections 2 and 6, brief "5b details" 2 to 6)."""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def intervene_world():
    world = kit.build()
    return world, kit.new_run(world.last)


class TestIntervenePath:
    def test_the_run_finishes_done_with_the_intervenors_decision_and_rationale(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("done", "done")
        assert stored.decision == "intervene"
        assert stored.rationale == "A source would help both readers."
        assert stored.failure_reason == ""
        assert stored.error == ""

    def test_both_agents_are_called_once_each_and_both_calls_are_ledgered_against_the_run(self, fake):
        world, run = intervene_world()
        client = fake(*kit.simple_success(world))
        kit.go(run)
        assert [len(kit.calls_of(client, "master")), len(kit.calls_of(client, "intervenor"))] == [1, 1]
        rows = kit.ledger(run)
        assert [(r.agent, r.attempt, r.status, r.purpose) for r in rows] == [
            ("master", 1, "ok", "moderation"),
            ("intervenor", 1, "ok", "moderation"),
        ]
        assert run.llm_calls().count() == 2

    def test_the_issue_is_stored_valid_with_offsets_from_the_quote_locator(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        text = world.last.content
        assert (issue.local_id, issue.message_id, issue.issue_type) == ("i1", world.last.pk, "unsupported_claim")
        assert (issue.validity, issue.rejection_reason) == ("valid", "")
        assert issue.quote_match == "exact"
        assert (issue.quote_start, issue.quote_end) == (text.index(kit.QUOTE_1), text.index(kit.QUOTE_1) + len(kit.QUOTE_1))
        assert text[issue.quote_start:issue.quote_end] == kit.QUOTE_1
        assert (issue.confidence, issue.explanation) == (0.8, "The claim is stated without support.")
        assert issue.dimension == ""
        assert issue.intensity is None

    def test_every_valid_issue_gets_its_disposition_with_the_models_reason(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        (issue,) = kit.issues_of(stored)
        assert (issue.disposition.disposition, issue.disposition.reason) == ("acted", "It matters for the discussion.")

    def test_the_act_is_stored_valid_with_its_fields_sources_and_features(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        (act,) = kit.acts_of(stored)
        assert (act.order, act.act_type, act.tone, act.text) == (1, "request_information", "neutral", kit.CLEAN_TEXT)
        assert (act.addressee, act.subject) == ("A", "A")
        assert (act.validity, act.rejection_reason) == ("valid", "")
        assert [i.local_id for i in act.source_issues.all()] == ["i1"]
        assert [m.pk for m in act.source_messages.all()] == [world.last.pk]
        assert (act.char_len, act.word_count, act.is_question, act.quotes_participant) == (
            len(kit.CLEAN_TEXT), 11, True, False,
        )  # fmt: skip

    def test_the_stored_features_equal_the_features_module_and_see_a_quoted_span(self, fake):
        from moderation import features

        world, run = intervene_world()
        text = 'The phrase "nobody disagrees on that" is a claim, not a source.'
        fake(*kit.simple_success(world, texts=(text,)))
        _, stored = kit.go(run)
        (act,) = kit.acts_of(stored)
        assert (act.is_question, act.quotes_participant) == (False, True)
        expected = features.act_text_features(text)
        assert (act.char_len, act.word_count, act.is_question, act.quotes_participant) == (
            expected["char_len"], expected["word_count"], expected["is_question"], expected["quotes_participant"],
        )  # fmt: skip

    def test_the_moderator_message_is_posted_as_a_reply_to_the_trigger(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        posted = stored.posted_message
        assert posted is not None
        assert (posted.author_type, posted.participant_id) == ("moderator", None)
        assert posted.in_reply_to_id == world.last.pk
        assert posted.conversation_id == world.conv.pk
        assert posted.content == kit.CLEAN_TEXT
        assert posted.seq_no == before + 1
        assert kit.message_count(world.conv) == before + 1
        assert [m.pk for m in kit.moderator_messages(world.conv)] == [posted.pk]

    def test_several_valid_acts_are_posted_in_order_separated_by_a_blank_line(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world, texts=(kit.CLEAN_TEXT, kit.CLEAN_TEXT_2, kit.CLEAN_TEXT_3)))
        _, stored = kit.go(run)
        assert stored.posted_message.content == f"{kit.CLEAN_TEXT}\n\n{kit.CLEAN_TEXT_2}\n\n{kit.CLEAN_TEXT_3}"
        assert [a.order for a in kit.acts_of(stored)] == [1, 2, 3]

    def test_posting_creates_no_new_run_and_no_second_message(self, fake):
        from moderation.models import ModerationRun

        world, run = intervene_world()
        fake(*kit.simple_success(world))
        kit.go(run)
        assert ModerationRun.objects.count() == 1
        assert ModerationRun.objects.filter(trigger_message__author_type="moderator").count() == 0
        assert len(kit.moderator_messages(world.conv)) == 1

    def test_the_run_records_its_times(self, fake):
        from django.utils import timezone

        world, run = intervene_world()
        fake(*kit.simple_success(world))
        started = timezone.now()
        _, stored = kit.go(run)
        finished = timezone.now()
        assert stored.attempts == 1
        assert started <= stored.started_at <= stored.finished_at <= finished

    def test_the_run_is_not_stale_when_no_newer_user_message_exists_even_after_its_own_post(self, fake):
        world, run = intervene_world()
        fake(*kit.simple_success(world))
        _, stored = kit.go(run)
        assert stored.posted_message is not None
        assert stored.is_stale is False

    def test_the_discussion_map_is_stored_as_the_master_gave_it(self, fake):
        world, run = intervene_world()
        master = kit.master_d(
            kit.issue_d("i1", world.last),
            agreements=["Both want stable housing."],
            disagreements=[{"summary": "Whether caps reduce supply.", "kind": "factual"}],
        )
        interv = kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(issues=["i1"])])
        fake(master, interv)
        _, stored = kit.go(run)
        assert stored.discussion_map == master["discussion_map"]

    def test_the_intervenor_is_shown_the_discussion_map_and_the_valid_issue(self, fake):
        world, run = intervene_world()
        master = kit.master_d(
            kit.issue_d("i1", world.last),
            agreements=["Both want stable housing."],
            disagreements=[{"summary": "Whether caps reduce supply.", "kind": "factual"}],
        )
        client = fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(issues=["i1"])]))
        kit.go(run)
        (call,) = kit.calls_of(client, "intervenor")
        text = kit.user_input(call)
        assert "Both want stable housing." in text
        assert "Whether caps reduce supply." in text
        assert kit.block(text, "issues") is not None
        assert kit.QUOTE_1 in kit.block(text, "issues")

    def test_the_addressee_and_subject_values_a_label_all_both_and_none_are_kept(self, fake):
        world, run = intervene_world()
        master = kit.master_d(kit.issue_d("i1", world.last))
        acts = [
            kit.act_d(kit.CLEAN_TEXT, addressee="A", subject="B"),
            kit.act_d(kit.CLEAN_TEXT_2, addressee="all", subject="both"),
            kit.act_d(kit.CLEAN_TEXT_3, addressee="B", subject="none"),
        ]
        fake(master, kit.interv_d(dispositions=[kit.disp_d("i1")], acts=acts))
        _, stored = kit.go(run)
        assert [(a.validity, a.addressee, a.subject) for a in kit.acts_of(stored)] == [
            ("valid", "A", "B"), ("valid", "all", "both"), ("valid", "B", "none"),
        ]  # fmt: skip


class TestNoInterventionPaths:
    def test_no_issues_means_no_second_call_and_the_fixed_rationale(self, fake):
        world, run = intervene_world()
        client = fake(kit.master_d())
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert (stored.decision, stored.rationale) == ("no_intervention", "no valid issues")
        assert len(client.calls) == 1
        assert len(kit.ledger(run)) == 1
        assert stored.posted_message is None
        assert kit.message_count(world.conv) == before
        assert kit.issues_of(stored) == []
        assert kit.acts_of(stored) == []

    def test_only_rejected_issues_also_mean_no_second_call(self, fake):
        world, run = intervene_world()
        client = fake(kit.master_d(kit.issue_d("i1", world.last, quote="a phrase that is not in the message")))
        _, stored = kit.go(run)
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")
        assert len(client.calls) == 1
        assert kit.issue_summary(stored) == [("i1", "rejected", "quote_not_found")]

    def test_the_discussion_map_is_stored_even_when_there_is_no_valid_issue(self, fake):
        world, run = intervene_world()
        master = kit.master_d(agreements=["Both want stable housing."])
        fake(master)
        _, stored = kit.go(run)
        assert stored.discussion_map == master["discussion_map"]

    def test_the_intervenor_declining_stores_dispositions_and_posts_nothing(self, fake):
        world, run = intervene_world()
        master = kit.master_d(kit.issue_d("i1", world.last), kit.issue_d("i2", world.last, "fallacy", kit.QUOTE_2))
        interv = kit.interv_d(
            decision="no_intervention",
            rationale="Neither point needs a note yet.",
            dispositions=[kit.disp_d("i1", "declined", "Minor."), kit.disp_d("i2", "declined", "Minor too.")],
        )
        client = fake(master, interv)
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "Neither point needs a note yet.")
        assert stored.posted_message is None
        assert kit.message_count(world.conv) == before
        assert len(client.calls) == 2
        assert [(i.local_id, i.disposition.disposition, i.disposition.reason) for i in kit.issues_of(stored)] == [
            ("i1", "declined", "Minor."), ("i2", "declined", "Minor too."),
        ]  # fmt: skip
        assert kit.acts_of(stored) == []

    def test_a_model_that_says_intervene_with_no_acts_is_stored_as_no_intervention(self, fake):
        world, run = intervene_world()
        master = kit.master_d(kit.issue_d("i1", world.last))
        fake(master, kit.interv_d("intervene", "Something should be said.", [kit.disp_d("i1")], acts=[]))
        _, stored = kit.go(run)
        assert (stored.status, stored.decision) == ("done", "no_intervention")
        assert stored.posted_message is None
        assert len(kit.moderator_messages(world.conv)) == 0

    def test_a_model_that_says_no_intervention_never_posts_even_if_it_lists_a_valid_act(self, fake):
        world, run = intervene_world()
        master = kit.master_d(kit.issue_d("i1", world.last))
        interv = kit.interv_d("no_intervention", "Better to wait.", [kit.disp_d("i1", "declined")], acts=[kit.act_d()])
        fake(master, interv)
        _, stored = kit.go(run)
        assert (stored.status, stored.decision) == ("done", "no_intervention")
        assert stored.posted_message is None
        assert len(kit.moderator_messages(world.conv)) == 0
