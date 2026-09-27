"""load_experiment: what a loaded transcript looks like in the database (docs/step13_brief.md, "What it does")."""
import pytest
import replay_kit as kit

NAME = "exp-load"


def one(tid="solo", **kwargs):
    return kit.transcript(tid, **kwargs)


class TestExperimentRow:
    def test_a_new_experiment_is_a_replay_experiment_by_default(self):
        kit.load(NAME, [one()])
        exp = kit.experiment(NAME)
        assert (exp.name, exp.kind) == (NAME, "replay")

    def test_kind_paired_is_stored_when_asked_for(self):
        kit.load(NAME, kit.pair("p1"), kind="paired")
        assert kit.experiment(NAME).kind == "paired"

    def test_the_experiment_has_a_description(self):
        kit.load(NAME, [one()])
        assert kit.experiment(NAME).description.strip() != ""

    def test_config_records_every_transcript_id(self):
        import json

        kit.load(NAME, [one("alpha"), one("beta")])
        config_text = json.dumps(kit.experiment(NAME).config)
        assert ("alpha" in config_text, "beta" in config_text) == (True, True)

    @pytest.mark.parametrize("assignments", ["as-is", "swapped", "both"])
    def test_config_records_the_assignments(self, assignments):
        import json

        kit.load(NAME, [one()], assignments=assignments)
        assert assignments in json.dumps(kit.experiment(NAME).config)

    def test_config_records_the_prompt_fingerprint_of_both_agents(self):
        import json

        from moderation import agents

        kit.load(NAME, [one()])
        config_text = json.dumps(kit.experiment(NAME).config)
        fingerprint = agents.prompt_fingerprint()
        assert (fingerprint["master"]["sha256"] in config_text, fingerprint["intervenor"]["sha256"] in config_text) == (
            True,
            True,
        )

    def test_config_records_the_tunables_in_force(self, tune):
        import json

        tune(MAX_MESSAGE_CHARS=777)
        kit.load(NAME, [one()])
        config_text = json.dumps(kit.experiment(NAME).config)
        assert ("MAX_MESSAGE_CHARS" in config_text, "777" in config_text) == (True, True)

    def test_config_records_the_replicate_count(self):
        kit.load(NAME, [one()], replicates=7)
        assert 7 in _all_values(kit.experiment(NAME).config)


def _all_values(node):
    if isinstance(node, dict):
        return [v for value in node.values() for v in _all_values(value)] + list(node.values())
    if isinstance(node, list):
        return [v for value in node for v in _all_values(value)] + list(node)
    return [node]


class TestConversationRows:
    def test_as_is_makes_one_synthetic_closed_conversation_in_the_experiment(self):
        data = one()
        kit.load(NAME, [data], assignments="as-is")
        exp = kit.experiment(NAME)
        (conv,) = kit.conversations(exp)
        assert (conv.source, conv.status, conv.experiment_id) == ("synthetic", "closed", exp.pk)

    def test_the_conversation_carries_pair_id_and_variant_from_the_file(self):
        left, right = kit.pair("duo")
        kit.load(NAME, [left, right], assignments="as-is")
        exp = kit.experiment(NAME)
        got = sorted((c.pair_id, c.variant) for c in kit.conversations(exp))
        assert got == [("duo", "left"), ("duo", "right")]

    def test_a_single_transcript_has_a_blank_pair_id_and_variant(self):
        kit.load(NAME, [one()], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert (conv.pair_id, conv.variant) == ("", "")

    def test_a_label_seed_is_recorded(self):
        kit.load(NAME, [one()], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert conv.label_seed is not None

    def test_the_topic_carries_the_proposition_of_the_file(self):
        data = one(topic=("A title for it", "A proposition that is unique to this test."))
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert conv.topic.proposition == "A proposition that is unique to this test."

    def test_the_two_transcripts_of_a_pair_share_one_topic_row(self):
        from forum.models import Topic

        kit.load(NAME, kit.pair("shared"), assignments="both")
        exp = kit.experiment(NAME)
        topic_ids = {c.topic_id for c in kit.conversations(exp)}
        assert (len(topic_ids), Topic.objects.count()) == (1, 1)

    def test_a_new_topic_is_created_visible_with_no_creator(self):
        from forum.models import Topic

        kit.load(NAME, [one(topic=("A brand new title", "A brand new proposition for this test."))], assignments="as-is")
        topic = Topic.objects.get(title="A brand new title")
        assert (topic.hidden, topic.created_by_id, topic.proposition) == (False, None, "A brand new proposition for this test.")

    def test_an_existing_topic_with_the_same_title_and_proposition_is_reused(self):
        from forum.models import Topic

        existing = Topic.objects.create(title="Reused title", description="d", proposition="Reused proposition.")
        kit.load(NAME, [one("reuse", topic=("Reused title", "Reused proposition."))], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert (conv.topic_id, Topic.objects.count()) == (existing.pk, 1)

    def test_the_same_title_with_another_proposition_is_refused_and_writes_nothing(self):
        import pytest
        from django.core.management.base import CommandError

        from forum.models import Topic

        Topic.objects.create(title="Clash title", description="d", proposition="One proposition.")
        before = kit.table_counts()
        with pytest.raises((CommandError, ValueError)):
            kit.load(NAME, [one("clash", topic=("Clash title", "A different proposition."))], assignments="as-is")
        assert kit.table_counts() == before

    def test_it_never_touches_the_state_of_a_live_conversation(self):
        from forum.models import Conversation, Topic

        topic = Topic.objects.create(title="live topic", description="d", proposition="A live proposition.")
        live = Conversation.objects.create(topic=topic, source="human")
        kit.load(NAME, [one()], assignments="both")
        live.refresh_from_db()
        assert (live.status, live.source, live.experiment_id) == ("open", "human", None)


class TestParticipants:
    def test_every_conversation_has_participants_a_and_b_without_users(self):
        kit.load(NAME, [one()], assignments="both")
        for conv in kit.conversations(kit.experiment(NAME)):
            rows = sorted((p.label, p.join_order, p.user_id) for p in conv.participants.all())
            assert rows == [("A", 1, None), ("B", 2, None)]

    def test_no_user_account_is_created(self):
        from django.contrib.auth import get_user_model

        kit.load(NAME, [one()], assignments="both")
        assert get_user_model().objects.count() == 0


class TestMessages:
    def test_messages_have_the_texts_seq_numbers_and_authors_of_the_file(self):
        data = one(authors="ABAB")
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        stored = [(m.seq_no, kit.participant_label_of(m), m.author_type, m.content.strip()) for m in kit.messages_of(conv)]
        expected = [(m["seq"], label, "user", m["text"].strip()) for m, label in zip(data["messages"], "ABAB")]
        assert stored == expected

    def test_char_count_is_the_project_count_of_the_text(self):
        from forum.limits import count_message_chars

        text = "  padded text with trailing space and a café   "
        data = one()
        data["messages"][0]["text"] = text
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        first = kit.messages_of(conv)[0]
        assert (first.char_count, count_message_chars(text)) == (42, 42)

    def test_char_count_uses_nfc_and_normalized_line_endings(self):
        from forum.limits import count_message_chars

        text = "café\r\nsecond line"
        data = one()
        data["messages"][1]["text"] = text
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        second = kit.messages_of(conv)[1]
        assert (second.char_count, count_message_chars(text)) == (16, 16)

    def test_planted_items_are_stored_exactly_as_in_the_file(self):
        data = kit.with_planted(one(), 4)
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert [m.planted for m in kit.messages_of(conv)] == [m["planted"] for m in data["messages"]]

    def test_a_message_without_planted_items_has_an_empty_list(self):
        kit.load(NAME, [one()], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert [m.planted for m in kit.messages_of(conv)] == [[], [], [], []]

    def test_a_scripted_moderator_message_loads_as_a_moderator_message(self):
        data = one("modded", authors="ABMA")
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        stored = [(m.seq_no, m.author_type, m.participant_id) for m in kit.messages_of(conv)]
        assert stored == [(1, "user", stored[0][2]), (2, "user", stored[1][2]), (3, "moderator", None), (4, "user", stored[3][2])]

    def test_the_moderator_message_keeps_its_text(self):
        data = one("modtext", authors="ABMA")
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert kit.messages_of(conv)[2].content.strip() == data["messages"][2]["text"].strip()

    def test_messages_after_the_trigger_are_loaded_too(self):
        data = one("later", authors="ABABA", trigger_seq=3)
        kit.load(NAME, [data], assignments="as-is")
        (conv,) = kit.conversations(kit.experiment(NAME))
        assert len(kit.messages_of(conv)) == 5


class TestNothingElseIsCreated:
    def test_loading_creates_no_moderation_run_and_no_ledger_row(self, fake):
        from moderation.models import LLMCall, ModerationRun

        client = fake()
        kit.load(NAME, kit.pair("quiet"), assignments="both")
        assert (ModerationRun.objects.count(), LLMCall.objects.count(), client.calls) == (0, 0, [])

    def test_loading_works_with_the_kill_switch_off(self, settings):
        settings.LLM_ENABLED = False
        kit.load(NAME, [one()], assignments="both")
        assert len(kit.conversations(kit.experiment(NAME))) == 2
