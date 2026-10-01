"""Step 21, part 2: when the agreement/disagreement note is due and what the pipeline then does."""
import dim21_kit as dk
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db

DUE_COUNTS = {4, 8, 12}


def assert_not_due(client, stored, world):
    assert dk.intervenor_calls(client) == []
    assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")
    assert stored.posted_message is None
    assert kit.message_count(world.conv) == len(world.msgs)


def assert_due_and_posted(client, stored):
    assert (stored.status, stored.decision, stored.failure_reason, stored.error) == ("done", "intervene", "", "")
    assert len(dk.intervenor_calls(client)) == 1
    assert stored.posted_message is not None
    assert stored.posted_message.content == dk.SUMMARY_TEXT
    assert stored.posted_message.author_type == "moderator"


class TestCadence:
    @pytest.mark.parametrize("count", range(1, 14))
    def test_it_is_due_after_every_fourth_user_message_and_only_then(self, fake, count):
        world, trigger = dk.world_with_users(count)
        if count in DUE_COUNTS:
            client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
            assert_due_and_posted(client, stored)
        else:
            client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
            assert_not_due(client, stored, world)

    def test_the_note_is_stored_as_one_valid_identify_agreement_disagreement_act(self, fake):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        (act,) = kit.acts_of(stored)
        assert (act.act_type, act.addressee, act.subject, act.validity, act.rejection_reason) == (
            dk.SUMMARY_TYPE, "all", "both", "valid", "",
        )
        assert act.text == dk.SUMMARY_TEXT
        assert list(act.source_issues.all()) == []
        assert kit.issues_of(stored) == []

    def test_the_note_is_an_ordinary_moderator_reply_to_the_trigger(self, fake):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        message = stored.posted_message
        assert (message.author_type, message.in_reply_to_id, message.participant_id) == ("moderator", trigger.pk, None)
        assert [m.pk for m in kit.moderator_messages(world.conv)] == [message.pk]

    def test_the_discussion_map_is_stored_on_the_run(self, fake):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert stored.discussion_map == {"agreements": [dk.AGREEMENT], "disagreements": [dk.DISAGREEMENT]}

    def test_the_ledger_has_one_master_and_one_intervenor_call(self, fake):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert [(r.agent, r.attempt, r.status) for r in kit.ledger(stored)] == [("master", 1, "ok"), ("intervenor", 1, "ok")]


class TestOnlyUserMessagesAreCounted:
    def test_moderator_messages_do_not_count_toward_the_total(self, fake):
        # three user messages and one moderator message: four messages, but only three user messages
        world, trigger = dk.world_with_users(3, mods_after=(2,))
        assert trigger.seq_no == 4
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)

    def test_the_fourth_user_message_is_due_even_with_moderator_messages_in_between(self, fake):
        world, trigger = dk.world_with_users(4, mods_after=(1, 3))
        assert trigger.seq_no == 6
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)

    def test_the_eighth_user_message_is_due_with_one_moderator_message(self, fake):
        world, trigger = dk.world_with_users(8, mods_after=(5,))
        assert trigger.seq_no == 9
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)

    def test_the_ninth_message_overall_is_not_due_when_it_is_only_the_seventh_user_message(self, fake):
        # seven user messages and two moderator messages: the trigger has seq_no 9 but is the 7th user message
        world, trigger = dk.world_with_users(7, mods_after=(2, 4))
        assert trigger.seq_no == 9
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)

    def test_the_messages_counted_are_those_up_to_the_trigger_not_the_whole_conversation(self, fake):
        world, trigger = dk.world_with_users(4, extra_users=4)  # eight user messages exist, the trigger is the fourth
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)

    def test_a_trigger_that_is_the_fifth_user_message_of_eight_is_not_due(self, fake):
        world, trigger = dk.world_with_users(5, extra_users=3)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)


class TestTunable:
    def test_a_smaller_n_changes_the_cadence(self, fake, tune):
        tune(**{dk.TUNABLE: 2})
        for count, due in [(1, False), (2, True), (3, False), (4, True), (6, True)]:
            world, trigger = dk.world_with_users(count)
            if due:
                client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
                assert_due_and_posted(client, stored)
            else:
                client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
                assert_not_due(client, stored, world)

    def test_n_of_three_is_due_at_three_and_not_at_four(self, fake, tune):
        tune(**{dk.TUNABLE: 3})
        world, trigger = dk.world_with_users(3)
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)

    def test_n_of_one_is_due_at_every_user_message(self, fake, tune):
        tune(**{dk.TUNABLE: 1})
        world, trigger = dk.world_with_users(1)
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)

    @pytest.mark.parametrize("off", [0, None])
    @pytest.mark.parametrize("count", [1, 4, 8])
    def test_zero_or_none_turns_it_off(self, fake, tune, off, count):
        tune(**{dk.TUNABLE: off})
        world, trigger = dk.world_with_users(count)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)

    def test_the_value_is_read_at_call_time_so_one_process_can_change_it(self, fake, tune):
        world, trigger = dk.world_with_users(4)
        tune(**{dk.TUNABLE: 0})
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert_not_due(client, stored, world)
        tune(**{dk.TUNABLE: 4})
        world2, trigger2 = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world2, trigger2, dk.due_script())
        assert_due_and_posted(client, stored)

    def test_the_default_is_used_when_nothing_is_overridden(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        assert_due_and_posted(client, stored)


class TestTheMapMustHaveSomethingToSay:
    def test_an_empty_map_is_never_due(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.empty_map_master()])
        assert_not_due(client, stored, world)

    def test_agreements_alone_make_it_due(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(
            fake, world, trigger, [dk.map_master(disagreements=()), dk.summary_intervenor()]
        )
        assert_due_and_posted(client, stored)

    def test_disagreements_alone_make_it_due(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(
            fake, world, trigger, [dk.map_master(agreements=()), dk.summary_intervenor()]
        )
        assert_due_and_posted(client, stored)

    def test_an_empty_map_at_the_eighth_message_is_not_due_either(self, fake):
        world, trigger = dk.world_with_users(8)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.empty_map_master()])
        assert_not_due(client, stored, world)


class TestLiveOnly:
    def test_a_replay_run_is_never_due_and_ends_with_no_valid_issues(self, fake):
        world, trigger = dk.world_with_users(4)
        live = kit.new_run(trigger)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()], kind="replay", replay_of=live, replicate=1)
        assert dk.intervenor_calls(client) == []
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")
        assert stored.posted_message is None

    @pytest.mark.parametrize("count", [4, 8])
    def test_a_replay_with_a_valid_issue_gets_an_intervenor_input_without_the_summary_block(self, fake, count):
        world, trigger = dk.world_with_users(count)
        live = kit.new_run(trigger)
        master = dk.map_master(kit.issue_d("i1", trigger, "unclear_statement", "Message number", intensity=1))
        script = [master, kit.interv_d("no_intervention", "Nothing to add.", [kit.disp_d("i1", "declined")])]
        client, stored = dk.run_trigger(fake, world, trigger, script, kind="replay", replay_of=live, replicate=1)
        (call,) = dk.intervenor_calls(client)
        assert dk.summary_block_lines(), "summary_due=True must render something"
        assert not dk.sent_summary_flag(call)
        assert stored.posted_message is None

    def test_a_live_run_with_a_valid_issue_on_a_due_message_gets_the_summary_block(self, fake):
        world, trigger = dk.world_with_users(4)
        master = dk.map_master(kit.issue_d("i1", trigger, "unclear_statement", "Message number", intensity=1))
        script = [master, kit.interv_d("no_intervention", "Nothing to add.", [kit.disp_d("i1", "declined")])]
        client, stored = dk.run_trigger(fake, world, trigger, script)
        (call,) = dk.intervenor_calls(client)
        assert dk.sent_summary_flag(call)

    def test_a_live_run_with_a_valid_issue_on_a_message_that_is_not_due_gets_no_summary_block(self, fake):
        world, trigger = dk.world_with_users(5)
        master = dk.map_master(kit.issue_d("i1", trigger, "unclear_statement", "Message number", intensity=1))
        script = [master, kit.interv_d("no_intervention", "Nothing to add.", [kit.disp_d("i1", "declined")])]
        client, stored = dk.run_trigger(fake, world, trigger, script)
        (call,) = dk.intervenor_calls(client)
        assert not dk.sent_summary_flag(call)

    def test_a_research_run_never_calls_the_intervenor_even_when_the_cadence_would_fire(self, fake, tune):
        rk = dk.research_kit()

        tune(**{dk.TUNABLE: 1})
        scenario = rk.make_research_scenario()
        run = rk.new_research_run(scenario)
        client = fake(rk.success_item())
        _, stored = rk.go(run)
        assert stored.status == "done"
        assert [c for c in client.calls if c["output_format"].__name__ in ("MasterOutput", "IntervenorOutput")] == []
        assert dk.SUMMARY_TYPE not in "".join(a.act_type for a in stored.acts.all())


class TestTheIntervenorInput:
    def test_the_summary_flag_renders_a_block_that_the_default_does_not(self):
        from moderation import prompting

        messages = [(1, "Participant A", "one"), (2, "Participant B", "two")]
        default = prompting.render_intervenor_input(messages, [])
        off = prompting.render_intervenor_input(messages, [], summary_due=False)
        on = prompting.render_intervenor_input(messages, [], summary_due=True)
        assert default == off
        assert on != off
        assert "summary" in on.lower()

    def test_the_flag_changes_only_by_adding_text(self):
        from moderation import prompting

        messages = [(1, "Participant A", "one")]
        off = prompting.render_intervenor_input(messages, [])
        on = prompting.render_intervenor_input(messages, [], summary_due=True)
        assert set(off.splitlines()) <= set(on.splitlines())

    def test_a_due_run_sends_the_discussion_map_along_with_the_flag(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script())
        (call,) = dk.intervenor_calls(client)
        text = kit.user_input(call)
        assert dk.sent_summary_flag(call)
        assert dk.AGREEMENT in text and 'kind="factual"' in text

    def test_the_intervenor_is_not_called_when_it_is_not_due_and_there_are_no_issues(self, fake):
        world, trigger = dk.world_with_users(3)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master()])
        assert len(client.calls) == 1
        assert_not_due(client, stored, world)


class TestTheNoteWithTheRestOfTheIntervention:
    def issue_master(self, trigger):
        return dk.map_master(kit.issue_d("i1", trigger, "unsupported_claim", "Message number 4"))

    def note_act(self, trigger):
        return kit.act_d(kit.CLEAN_TEXT, issues=["i1"], messages=[trigger], addressee="all", subject="none")

    def test_a_due_run_with_an_issue_posts_the_issue_note_and_the_summary_in_one_message(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = dk.summary_intervenor(extra_acts=[self.note_act(trigger)], dispositions=[kit.disp_d("i1")])
        client, stored = dk.run_trigger(fake, world, trigger, [self.issue_master(trigger), interv])
        assert stored.posted_message.content == kit.CLEAN_TEXT + "\n\n" + dk.SUMMARY_TEXT
        assert [a.act_type for a in kit.acts_of(stored)] == ["request_information", dk.SUMMARY_TYPE]
        assert len(client.calls) == 2

    def test_the_summary_counts_toward_the_three_act_limit(self, fake):
        world, trigger = dk.world_with_users(4)
        acts = [
            kit.act_d(text, issues=["i1"], messages=[trigger], addressee="all", subject="none")
            for text in (kit.CLEAN_TEXT, kit.CLEAN_TEXT_2, kit.CLEAN_TEXT_3)
        ]
        interv = dk.summary_intervenor(extra_acts=acts, dispositions=[kit.disp_d("i1")])
        client, stored = dk.run_trigger(fake, world, trigger, [self.issue_master(trigger), interv])
        assert kit.act_summary(stored) == [(1, "valid", ""), (2, "valid", ""), (3, "valid", ""), (4, "rejected", "act_cap")]
        assert dk.SUMMARY_TEXT not in stored.posted_message.content

    def test_a_missing_summary_act_is_not_an_error_when_there_is_an_issue_to_act_on(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[self.note_act(trigger)])
        client, stored = dk.run_trigger(fake, world, trigger, [self.issue_master(trigger), interv])
        assert (stored.status, stored.decision, stored.failure_reason, stored.error) == ("done", "intervene", "", "")
        assert stored.posted_message.content == kit.CLEAN_TEXT

    def test_a_missing_summary_act_is_not_an_error_when_the_intervenor_posts_nothing(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d("no_intervention", "Nothing worth adding.")
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), interv])
        assert (stored.status, stored.decision, stored.failure_reason, stored.error) == ("done", "no_intervention", "", "")
        assert stored.rationale == "Nothing worth adding."
        assert stored.posted_message is None and kit.message_count(world.conv) == len(world.msgs)
        assert len(dk.intervenor_calls(client)) == 1

    def test_an_intervene_decision_with_no_acts_posts_nothing_and_is_not_an_error(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d("intervene", "Intervening.", acts=[])
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), interv])
        assert (stored.status, stored.decision, stored.failure_reason) == ("done", "no_intervention", "")
        assert stored.posted_message is None

    def test_a_failing_intervenor_on_a_due_run_fails_the_run_structurally_and_posts_nothing(self, fake):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), kit.BAD_INTERVENOR, kit.BAD_INTERVENOR])
        assert (stored.status, stored.failure_reason) == ("failed", "structural")
        assert stored.posted_message is None


class TestTheSummaryActIsValidatedLikeAnyAct:
    @pytest.mark.parametrize("text", [
        "Participant A holds one view; Participant B holds another.",
        "One position holds that caps help; the other side holds that they hurt.",
        "A's claim and B's reply disagree about the supply of housing.",
        "One view holds that caps help; the other participant disagrees.",
        "One view holds that caps help; another participant disagrees.",
        "The other person disagrees on facts.",
    ])
    def test_a_summary_naming_a_participant_or_using_viewer_relative_words_is_rejected(self, fake, text):
        world, trigger = dk.world_with_users(4)
        client, stored = dk.run_trigger(fake, world, trigger, dk.due_script(text))
        assert kit.act_summary(stored) == [(1, "rejected", "names_participant")]
        assert (stored.status, stored.decision) == ("done", "no_intervention")
        assert stored.posted_message is None

    def test_the_text_of_the_standard_summary_passes_the_label_check(self):
        from moderation import label_check

        assert label_check.names_a_label(dk.SUMMARY_TEXT) is False

    @pytest.mark.parametrize("text", kit.NAMING_TEXTS)
    def test_the_shared_naming_texts_are_rejected_on_a_summary_act_too(self, fake, text):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script(text))
        assert kit.act_summary(stored) == [(1, "rejected", "names_participant")]

    def test_a_summary_act_addressed_to_a_label_other_than_all_still_validates_as_any_act_would(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d(acts=[dk.summary_act(addressee="A")])
        _, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), interv])
        (act,) = kit.acts_of(stored)
        assert (act.addressee, act.validity) == ("A", "valid")

    def test_a_summary_act_with_an_unknown_addressee_is_rejected_as_a_bad_label(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d(acts=[dk.summary_act(addressee="Q")])
        _, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), interv])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_label")]

    def test_a_summary_act_with_an_unknown_source_issue_is_rejected(self, fake):
        world, trigger = dk.world_with_users(4)
        interv = kit.interv_d(acts=[dk.summary_act(issues=["nope"])])
        _, stored = dk.run_trigger(fake, world, trigger, [dk.map_master(), interv])
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_issue")]

    def test_an_empty_summary_text_is_rejected(self, fake):
        world, trigger = dk.world_with_users(4)
        _, stored = dk.run_trigger(fake, world, trigger, dk.due_script("   "))
        assert kit.act_summary(stored) == [(1, "rejected", "empty_text")]
