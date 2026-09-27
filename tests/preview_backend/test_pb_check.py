"""`preview.check_draft`: the model calls it makes, what it sends, the outcome it records, that it validates in memory exactly as
the live pipeline does, and that it writes nothing to the moderation or forum tables (docs/step19_backend_brief.md)."""
import pipeline_run_kit as prk
import preview_kit as pk
import pytest

pytestmark = pytest.mark.django_db

ISSUE_QUOTE_1 = pk.ISSUE_QUOTE_1


concern, two_notes, one_bad_one_good, all_acts_invalid, old_repetition, acts_capped = (
    pk.concern, pk.two_notes, pk.one_bad_one_good, pk.all_acts_invalid, pk.old_repetition, pk.acts_capped,
)


def run_check(fake, factory, who="B", text=pk.DRAFT, specs=None):
    w = pk.world(specs)
    client = fake(*factory(w, pk.PLACEHOLDER))
    returned, stored = pk.check(w, who, text)
    return w, client, returned, stored


run_live = pk.run_live


class TestTheCallsMade:
    def test_both_agents_are_called_once_each_in_order_for_a_concern(self, fake):
        w, client, returned, stored = run_check(fake, concern)
        assert [c["output_format"].__name__ for c in client.calls] == ["MasterOutput", "IntervenorOutput"]

    def test_llm_call_gets_purpose_moderation_no_run_id_and_the_conversation_id(self, fake, monkeypatch):
        from moderation import llm

        recorded = []
        original = llm.call

        def spy(**kwargs):
            recorded.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(llm, "call", spy)
        w = pk.world()
        fake(*concern(w, pk.PLACEHOLDER))
        pk.check(w, "B")
        assert [(k["agent"], k["purpose"], k["run_id"], k["conversation_id"], k["attempt"]) for k in recorded] == [
            ("master", "moderation", None, w.conv.pk, 1),
            ("intervenor", "moderation", None, w.conv.pk, 1),
        ]

    def test_the_ledger_rows_are_moderation_calls_of_the_conversation_without_a_run(self, fake, settings):
        w, client, returned, stored = run_check(fake, concern)
        rows = pk.ledger_of(w.conv)
        assert [(r.purpose, r.agent, r.attempt, r.status, r.run_id, r.conversation_id) for r in rows] == [
            ("moderation", "master", 1, "ok", None, w.conv.pk),
            ("moderation", "intervenor", 1, "ok", None, w.conv.pk),
        ]
        assert [r.model for r in rows] == [settings.MASTER_MODEL, settings.INTERVENOR_MODEL]

    def test_the_ledger_ids_are_recorded_on_the_check_in_order(self, fake):
        w, client, returned, stored = run_check(fake, concern)
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert len(stored.llm_call_ids) == 2
        assert returned.llm_call_ids == stored.llm_call_ids

    def test_a_retried_master_lists_every_ledger_row_including_the_unusable_one(self, fake):
        w = pk.world()
        script = concern(w, pk.PLACEHOLDER)
        fake(prk.BAD_MASTER, *script)
        stored = pk.check(w, "B")[1]
        assert stored.outcome == "concern"
        assert [(r.agent, r.attempt) for r in pk.ledger_of(w.conv)] == [("master", 1), ("master", 2), ("intervenor", 1)]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)

    def test_the_calls_use_the_live_prompts_models_and_request_settings(self, fake):
        w_check, client_check, _, _ = run_check(fake, concern)
        w_live, client_live, _ = run_live(fake, concern)
        for check_call, live_call in zip(client_check.calls, client_live.calls, strict=True):
            assert {k: v for k, v in check_call.items() if k != "messages"} == {
                k: v for k, v in live_call.items() if k != "messages"
            }

    def test_the_system_prompts_are_the_unchanged_static_prompt_files(self, fake):
        from moderation import prompting

        w, client, _, _ = run_check(fake, concern)
        assert [c["system"][0]["text"] for c in client.calls] == [prompting.load_prompt("master").text, prompting.load_prompt("intervenor").text]


class TestTheTranscriptSent:
    def test_the_draft_is_the_newest_message_of_the_drafters_label_with_the_placeholder_id(self, fake):
        w, client, _, _ = run_check(fake, concern, who="B")
        expected = [(m.pk, f"Participant {m.participant.label}") for m in w.msgs] + [(pk.PLACEHOLDER, "Participant B")]
        assert pk.rendered_ids(client.calls[0]) == expected
        assert pk.rendered_ids(client.calls[1]) == expected

    def test_the_draft_text_is_in_the_master_input_and_the_newest_message_marker_names_the_placeholder(self, fake):
        w, client, _, _ = run_check(fake, concern)
        for call in client.calls:
            assert pk.DRAFT in prk.user_input(call)
            assert f'<newest_message id="{pk.PLACEHOLDER}" />' in prk.user_input(call)

    def test_the_drafters_label_follows_the_participant(self, fake):
        w, client, _, _ = run_check(fake, concern, who="A")
        assert pk.rendered_ids(client.calls[0])[-1] == (pk.PLACEHOLDER, "Participant A")

    def test_the_intervenor_sees_the_issue_on_the_placeholder_message(self, fake):
        w, client, _, _ = run_check(fake, concern)
        shown = prk.block(prk.user_input(client.calls[1]), "issues")
        assert '<issue id="i1">' in shown
        assert f"<message_id>{pk.PLACEHOLDER}</message_id>" in shown

    def test_moderator_messages_of_the_conversation_are_labelled_moderator(self, fake):
        specs = [("A", "A first point about rent."), ("mod", "A neutral question."), ("B", "A reply about rent.")]
        w, client, _, _ = run_check(fake, concern, who="A", specs=specs)
        labels = [label for _, label in pk.rendered_ids(client.calls[0])]
        assert labels == ["Participant A", "Moderator", "Participant B", "Participant A"]

    def test_only_the_last_transcript_max_messages_are_sent_and_the_draft_is_one_of_them(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=3)
        specs = [("A", "One."), ("B", "Two."), ("A", "Three."), ("B", "Four."), ("A", "Five.")]
        w, client, _, _ = run_check(fake, concern, who="B", specs=specs)
        assert pk.rendered_ids(client.calls[0]) == [(w[4].pk, "Participant B"), (w[5].pk, "Participant A"), (pk.PLACEHOLDER, "Participant B")]

    def test_a_first_message_is_checked_against_an_empty_conversation(self, fake):
        w = pk.world([])
        client = fake(*concern(w, pk.PLACEHOLDER))
        stored = pk.check(w, "A")[1]
        assert pk.rendered_ids(client.calls[0]) == [(pk.PLACEHOLDER, "Participant A")]
        assert (stored.outcome, stored.snapshot_seq) == ("concern", 0)

    def test_the_inputs_equal_those_of_a_live_run_on_the_same_text_apart_from_the_draft_id(self, fake):
        w_check, client_check, _, _ = run_check(fake, concern)
        w_live, client_live, _ = run_live(fake, concern)
        for check_call, live_call in zip(client_check.calls, client_live.calls, strict=True):
            assert pk.canonical_input(check_call, w_check, draft_id=pk.PLACEHOLDER) == pk.canonical_input(live_call, w_live)

    def test_the_proposition_is_sent_as_in_a_live_run(self, fake):
        w, client, _, _ = run_check(fake, concern)
        assert w.topic.proposition in prk.user_input(client.calls[0])
        assert w.topic.proposition in prk.user_input(client.calls[1])

    def test_the_master_gets_process_facts_including_the_draft(self, fake):
        w, client, _, _ = run_check(fake, concern, who="A")
        facts = prk.block(prk.user_input(client.calls[0]), "process_facts")
        assert '<fact name="message_count">4</fact>' in facts
        assert '<fact name="latest_author_label">Participant A</fact>' in facts


class TestEqualTreatment:
    def test_the_same_draft_under_both_label_assignments_gives_identical_inputs_apart_from_the_labels(self, fake):
        swapped = [("B" if who == "A" else "A", text) for who, text in pk.SPECS]
        w1, client1, _, s1 = run_check(fake, concern, who="B")
        w2, client2, _, s2 = run_check(fake, concern, who="A", specs=swapped)
        assert len(client1.calls) == len(client2.calls) == 2
        for call1, call2 in zip(client1.calls, client2.calls, strict=True):
            assert pk.canonical_input(call1, w1, draft_id=pk.PLACEHOLDER, swap_labels=True) == pk.canonical_input(
                call2, w2, draft_id=pk.PLACEHOLDER
            )
        assert (s1.outcome, s1.note_texts) == (s2.outcome, s2.note_texts)

    def test_the_two_assignments_really_differ_before_the_labels_are_swapped(self, fake):
        """Non-vacuity: without the swap the two inputs are different, so the equality above is not trivially true."""
        swapped = [("B" if who == "A" else "A", text) for who, text in pk.SPECS]
        w1, client1, _, _ = run_check(fake, concern, who="B")
        w2, client2, _, _ = run_check(fake, concern, who="A", specs=swapped)
        assert pk.canonical_input(client1.calls[0], w1, draft_id=pk.PLACEHOLDER) != pk.canonical_input(
            client2.calls[0], w2, draft_id=pk.PLACEHOLDER
        )


class TestOutcomes:
    def test_a_concern_records_the_notes_and_both_validated_outputs_with_the_placeholder_id(self, fake):
        from moderation.schemas import IntervenorOutput, MasterOutput

        w, client, returned, stored = run_check(fake, concern)
        for check in (returned, stored):
            assert (check.outcome, check.unavailable_reason) == ("concern", "")
            assert check.note_texts == [pk.NOTE]
            assert check.master_output["issues"][0]["message_id"] == pk.PLACEHOLDER
            assert check.master_output["issues"][0]["id"] == "i1"
            assert check.intervenor_output["decision"] == "intervene"
            assert check.intervenor_output["acts"][0]["source_message_ids"] == [pk.PLACEHOLDER]
            assert MasterOutput.model_validate(check.master_output).model_dump(mode="json") == check.master_output
            assert IntervenorOutput.model_validate(check.intervenor_output).model_dump(mode="json") == check.intervenor_output

    def test_the_check_row_records_who_what_and_when(self, fake):
        from django.utils import timezone

        w = pk.world()
        fake(*concern(w, pk.PLACEHOLDER))
        before = timezone.now()
        stored = pk.check(w, "B")[1]
        after = timezone.now()
        assert (stored.conversation_id, stored.participant_id, stored.mode) == (w.conv.pk, w.parts["B"].pk, "on")
        assert stored.draft_text == pk.DRAFT
        assert (stored.action, stored.resulting_message_id, stored.reused_by_run_id, stored.resolved_at) == ("", None, None, None)
        assert before <= stored.created_at <= after

    def test_the_time_passed_in_is_the_time_of_the_check(self, fake):
        from datetime import datetime, timezone

        moment = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        w = pk.world()
        fake(pk.quiet_master())
        returned, stored = pk.check(w, "B", now=moment)
        assert stored.created_at == moment
        assert returned.created_at == moment

    def test_char_count_and_hash_follow_the_forum_normalisation(self, fake):
        from forum.limits import count_message_chars

        draft = "  Café rent, line one.\r\nLine two \U0001F600 \r\n"
        w = pk.world()
        fake(pk.quiet_master())
        stored = pk.check(w, "B", draft)[1]
        assert stored.draft_text == draft
        assert stored.char_count == count_message_chars(draft)
        assert stored.draft_sha256 == pk.sha(draft)
        assert len(stored.draft_sha256) == 64

    def test_equivalent_drafts_hash_alike_and_different_drafts_differ(self, fake):
        w = pk.world()
        fake(pk.quiet_master(), pk.quiet_master(), pk.quiet_master())
        a = pk.check(w, "B", "Café is nice.\r\nSecond line. ")[1]
        b = pk.check(w, "B", "Café is nice.\nSecond line.")[1]
        c = pk.check(w, "B", "Café is nice.\nSecond  line.")[1]
        assert a.draft_sha256 == b.draft_sha256
        assert c.draft_sha256 != a.draft_sha256

    @pytest.mark.parametrize(
        "specs,expected",
        [(pk.SPECS, 3), (pk.SPECS + [("mod", "A neutral question.")], 4), ([], 0)],
        ids=["three_messages", "moderator_message_counts", "empty"],
    )
    def test_snapshot_seq_is_the_highest_seq_no_of_the_conversation(self, fake, specs, expected):
        w = pk.world(specs)
        fake(pk.quiet_master())
        assert pk.check(w, "B")[1].snapshot_seq == expected

    def test_a_check_without_valid_issue_is_no_concern_and_never_calls_the_intervenor(self, fake):
        w = pk.world()
        client = fake(pk.quiet_master())
        stored = pk.check(w, "B")[1]
        assert (stored.outcome, stored.unavailable_reason, stored.note_texts) == ("no_concern", "", [])
        assert len(client.calls) == 1
        assert stored.master_output["issues"] == []
        assert stored.intervenor_output is None
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert len(stored.llm_call_ids) == 1

    def test_an_intervenor_that_declines_is_no_concern_after_two_calls(self, fake):
        w = pk.world()
        client = fake(*pk.declined_script())
        stored = pk.check(w, "B")[1]
        assert (stored.outcome, stored.note_texts) == ("no_concern", [])
        assert len(client.calls) == 2
        assert stored.intervenor_output["decision"] == "no_intervention"
        assert len(stored.llm_call_ids) == 2

    def test_a_decision_of_no_intervention_with_acts_still_shows_no_note(self, fake):
        w = pk.world()
        script = pk.concern_script()
        script[1]["decision"] = "no_intervention"
        fake(*script)
        assert pk.check(w, "B")[1].outcome == "no_concern"

    def test_several_valid_acts_are_shown_in_order(self, fake):
        w, client, returned, stored = run_check(fake, two_notes)
        assert (stored.outcome, stored.note_texts) == ("concern", [pk.NOTE, pk.NOTE_2])

    def test_only_valid_acts_are_shown(self, fake):
        w, client, returned, stored = run_check(fake, one_bad_one_good)
        assert (stored.outcome, stored.note_texts) == ("concern", [pk.NOTE_2])
        assert len(stored.intervenor_output["acts"]) == 2

    def test_an_intervention_whose_acts_are_all_invalid_is_no_concern(self, fake):
        w, client, returned, stored = run_check(fake, all_acts_invalid)
        assert (stored.outcome, stored.note_texts) == ("no_concern", [])
        assert len(client.calls) == 2

    def test_the_act_cap_applies_to_the_notes(self, fake, tune):
        tune(MAX_ACTS_PER_INTERVENTION=2)
        w, client, returned, stored = run_check(fake, acts_capped)
        assert stored.note_texts == [pk.NOTE, pk.NOTE_2]

    def test_an_issue_on_an_older_message_of_a_cross_message_type_is_valid(self, fake):
        w, client, returned, stored = run_check(fake, old_repetition)
        assert (stored.outcome, stored.note_texts) == ("concern", [pk.NOTE])

    @pytest.mark.parametrize(
        "issue_type,message,quote",
        [
            ("unsupported_claim", "old", ISSUE_QUOTE_1),
            ("unsupported_claim", "draft", "a phrase the draft does not contain"),
            ("unsupported_claim", "unknown", pk.QUOTE),
            ("unsupported_claim", "moderator", "A neutral question."),
        ],
        ids=["not_new_message", "quote_not_found", "unknown_message", "moderator_message"],
    )
    def test_an_invalid_issue_gives_no_concern_and_no_intervenor_call(self, fake, issue_type, message, quote):
        specs = pk.SPECS + [("mod", "A neutral question.")]
        w = pk.world(specs)
        ids = {"old": w[1].pk, "draft": pk.PLACEHOLDER, "unknown": 987654321, "moderator": w[4].pk}
        client = fake(prk.master_d(prk.issue_d("i1", ids[message], issue_type, quote)))
        stored = pk.check(w, "B")[1]
        assert (stored.outcome, stored.note_texts) == ("no_concern", [])
        assert len(client.calls) == 1

    def test_a_source_message_outside_the_transcript_window_makes_the_act_invalid(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=2)
        w = pk.world()
        script = pk.concern_script(act_messages=[w[1].pk])
        fake(*script)
        assert pk.check(w, "B")[1].outcome == "no_concern"

    def test_a_source_message_inside_the_window_keeps_the_act_valid(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=2)
        w = pk.world()
        fake(*pk.concern_script(act_messages=[w[3].pk, pk.PLACEHOLDER]))
        assert pk.check(w, "B")[1].outcome == "concern"

    @pytest.mark.parametrize("field,value", [("addressee", "Z"), ("subject", "Participant Z")], ids=["addressee", "subject"])
    def test_an_act_with_a_label_that_is_not_in_the_conversation_is_invalid(self, fake, field, value):
        w = pk.world()
        script = pk.concern_script()
        script[1]["acts"][0][field] = value
        fake(*script)
        assert pk.check(w, "B")[1].outcome == "no_concern"

    @pytest.mark.parametrize(
        "factory",
        [concern, two_notes, one_bad_one_good, all_acts_invalid, old_repetition, acts_capped],
        ids=lambda f: f.__name__,
    )
    def test_the_notes_shown_are_exactly_what_the_live_pipeline_posts(self, fake, tune, factory):
        tune(MAX_ACTS_PER_INTERVENTION=2)
        _, _, _, shown = run_check(fake, factory)
        _, _, live = run_live(fake, factory)
        posted = "" if live.posted_message is None else live.posted_message.content
        assert "\n\n".join(shown.note_texts) == posted
        assert (shown.outcome == "concern") == (live.decision == "intervene")


class TestNothingIsWrittenToTheLiveTables:
    @pytest.mark.parametrize("factory_name", ["concern", "declined", "quiet"])
    def test_a_check_creates_no_message_run_issue_disposition_or_act(self, fake, factory_name):
        factories = {
            "concern": lambda w, i: pk.concern_script(i),
            "declined": lambda w, i: pk.declined_script(i),
            "quiet": lambda w, i: [pk.quiet_master()],
        }
        w = pk.world()
        fake(*factories[factory_name](w, pk.PLACEHOLDER))
        before = pk.counts()
        pk.check(w, "B")
        assert pk.counts() == before

    def test_a_check_adds_one_check_row_and_only_ledger_rows_besides(self, fake):
        from moderation.models import PreviewCheck

        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        assert PreviewCheck.objects.count() == 1
        assert len(pk.ledger_of(w.conv)) == 2

    def test_the_conversations_messages_are_untouched(self, fake):
        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        assert [m.content for m in w.conv.messages.order_by("seq_no")] == [m.content for m in w.msgs]
        assert w.conv.next_seq() == 4


class TestNoTransactionDuringTheCall:
    @pytest.mark.django_db(transaction=True)
    def test_the_gateways_own_guard_never_fires_for_a_check(self, fake, settings):
        settings.LLM_FORBID_ATOMIC_CALLS = True
        w = pk.world()
        client = fake(*pk.concern_script())
        stored = pk.check(w, "B")[1]
        assert (stored.outcome, len(client.calls)) == ("concern", 2)


class TestPrivacy:
    def test_usernames_and_emails_never_reach_a_prompt_or_the_ledger(self, fake):
        import json

        from moderation.models import LLMCall

        w = pk.world(human=True, names={"A": "zebulonquist", "B": "quillfeather"})
        client = fake(*pk.concern_script())
        pk.check(w, "B")
        sent = prk.whole_request_text(client)
        ledger_text = json.dumps([[r.request, r.raw_response, r.parsed, r.error] for r in LLMCall.objects.all()], default=str)
        for identity in ("zebulonquist", "quillfeather", "example.org"):
            assert identity not in sent
            assert identity not in ledger_text

    def test_nothing_about_previews_drafts_or_modes_is_in_what_the_model_is_sent(self, fake):
        w = pk.world()
        client = fake(*pk.concern_script())
        pk.check(w, "B")
        turns = pk.request_messages_text(client).lower()
        for word in ("preview", "draft", "previewmode", "previewcheck", "unposted"):
            assert word not in turns

    def test_the_ledger_request_of_a_check_call_carries_no_preview_wording(self, fake):
        import json

        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        text = json.dumps([r.request["messages"] for r in pk.ledger_of(w.conv)]).lower()
        assert "preview" not in text
        assert "draft" not in text

    @pytest.mark.parametrize(
        "factory",
        [pk.concern, pk.declined, pk.quiet, pk.all_acts_invalid],
        ids=lambda f: f.__name__,
    )
    def test_no_log_line_or_print_carries_the_draft_text_for_any_answer(self, fake, caplog, capsys, factory):
        import logging

        caplog.set_level(logging.DEBUG)
        w = pk.world()
        fake(*factory(w, pk.PLACEHOLDER))
        pk.check(w, "B")
        captured = capsys.readouterr()
        logged = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("django.db"))
        assert pk.MARKER not in logged
        assert pk.MARKER not in captured.out + captured.err

    @pytest.mark.parametrize(
        "script",
        [[prk.BAD_MASTER, prk.BAD_MASTER], [pk.provider_error()], [pk.concern_script()[0], pk.provider_error()]],
        ids=["structural", "api_error", "api_error_in_intervenor"],
    )
    def test_no_log_line_or_print_carries_the_draft_text_when_the_check_fails(self, fake, caplog, capsys, script):
        import logging

        caplog.set_level(logging.DEBUG)
        w = pk.world()
        fake(*script)
        pk.check(w, "B")
        captured = capsys.readouterr()
        logged = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("django.db"))
        assert pk.MARKER not in logged
        assert pk.MARKER not in captured.out + captured.err

    def test_no_log_line_carries_the_draft_text_in_an_off_conversation_or_a_rate_limited_check(self, fake, caplog, tune):
        import logging

        caplog.set_level(logging.DEBUG)
        tune(PREVIEW_SHARE=0.0)
        off_world = pk.world()
        fake()
        pk.check(off_world, "B")
        tune(PREVIEW_SHARE=1.0, PREVIEW_MAX_CHECKS_PER_MINUTE=1)
        w = pk.world()
        fake(pk.quiet_master())
        pk.check(w, "B", "An earlier draft.")
        pk.check(w, "B")
        logged = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("django.db"))
        assert pk.MARKER not in logged

    def test_the_default_export_of_the_conversation_does_not_hold_the_draft(self, fake):
        from moderation.queries import export_conversation

        w = pk.world()
        fake(*pk.concern_script())
        pk.check(w, "B")
        assert pk.MARKER not in export_conversation(w.conv.pk)
