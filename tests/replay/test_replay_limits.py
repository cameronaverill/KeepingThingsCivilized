"""The same limits as real users, and atomic loading (docs/step13_brief.md, "The same limits as real users")."""
import pytest
import replay_kit as kit
from django.core.management.base import CommandError

NAME = "exp-limits"
REJECTED = (CommandError, ValueError)


def with_text(tid, seq, text, authors="ABAB"):
    data = kit.transcript(tid, authors=authors)
    next(m for m in data["messages"] if m["seq"] == seq)["text"] = text
    return data


def nothing_written():
    assert kit.table_counts() == {
        "Experiment": 0, "Conversation": 0, "Participant": 0, "Message": 0, "Topic": 0,
        "ModerationRun": 0, "Issue": 0, "LLMCall": 0,
    }  # fmt: skip


class TestMessageCharacterLimit:
    def test_a_message_of_exactly_the_limit_is_accepted(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        kit.load(NAME, [with_text("exact", 3, "a" * 80)], assignments="both")
        assert len(kit.conversations(kit.experiment(NAME))) == 2

    def test_a_message_one_over_the_limit_is_rejected_naming_the_file_and_message(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        with pytest.raises(REJECTED) as caught:
            kit.load(NAME, [with_text("over_one", 3, "a" * 81)])
        assert ("over_one" in str(caught.value), "3" in str(caught.value), "81" in str(caught.value)) == (True, True, True)

    def test_the_error_names_the_limit(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        with pytest.raises(REJECTED) as caught:
            kit.load(NAME, [with_text("names_limit", 2, "b" * 200)])
        assert "80" in str(caught.value)

    def test_surrounding_whitespace_is_not_counted(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        kit.load(NAME, [with_text("trimmed", 2, "  " + "a" * 80 + " \n\n ")])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_a_crlf_counts_as_one_character_like_for_real_users(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        text = "a" * 39 + "\r\n" + "b" * 40  # 80 by the project counter, 81 raw
        kit.load(NAME, [with_text("crlf_ok", 2, text)])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_one_over_the_limit_with_a_crlf_is_rejected(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        text = "a" * 40 + "\r\n" + "b" * 40  # 81 by the project counter
        with pytest.raises(REJECTED):
            kit.load(NAME, [with_text("crlf_over", 2, text)])

    def test_decomposed_accents_are_counted_composed(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        text = "é" * 80  # 160 code points raw, 80 after NFC
        kit.load(NAME, [with_text("nfc_ok", 2, text)])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_an_emoji_counts_as_one_character(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        kit.load(NAME, [with_text("emoji_ok", 2, "\U0001F600" * 80)])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_the_limit_applies_to_every_message_not_only_the_trigger(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        with pytest.raises(REJECTED):
            kit.load(NAME, [with_text("first_msg", 1, "a" * 81)])

    def test_the_limit_is_read_from_the_tunable_not_a_constant(self, tune):
        tune(MAX_MESSAGE_CHARS=3000)
        kit.load(NAME, [with_text("roomy", 2, "a" * 3000)])
        assert kit.messages_of(kit.conversations(kit.experiment(NAME))[0])[1].char_count == 3000

    def test_the_default_limit_rejects_3001_characters(self):
        with pytest.raises(REJECTED):
            kit.load(NAME, [with_text("default_over", 2, "a" * 3001)])


class TestUserMessageCountLimit:
    def test_exactly_the_maximum_number_of_user_messages_is_accepted(self, tune):
        tune(MAX_USER_MESSAGES_PER_CONVERSATION=6)
        kit.load(NAME, [kit.transcript("count_ok", authors="AB" * 3)])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_one_more_user_message_is_rejected_naming_the_file(self, tune):
        tune(MAX_USER_MESSAGES_PER_CONVERSATION=6)
        with pytest.raises(REJECTED) as caught:
            kit.load(NAME, [kit.transcript("count_over", authors="AB" * 3 + "A")])
        assert ("count_over" in str(caught.value), "6" in str(caught.value)) == (True, True)

    def test_scripted_moderator_messages_are_not_counted(self, tune):
        tune(MAX_USER_MESSAGES_PER_CONVERSATION=6)
        kit.load(NAME, [kit.transcript("count_mod", authors="ABMABMAB")])
        assert len(kit.conversations(kit.experiment(NAME))) == 1

    def test_the_default_maximum_is_thirty(self):
        kit.load(NAME, [kit.transcript("thirty", authors="AB" * 15)])
        with pytest.raises(REJECTED):
            kit.load("exp-limits-2", [kit.transcript("thirty_one", authors="AB" * 15 + "A")])


class TestRejectionWritesNothing:
    def test_a_bad_transcript_alone_writes_nothing(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        with pytest.raises(REJECTED):
            kit.load(NAME, [with_text("bad_alone", 2, "a" * 81)], assignments="both")
        nothing_written()

    def test_a_bad_transcript_after_good_ones_leaves_the_good_ones_unwritten(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        good = [kit.transcript("good_1"), kit.transcript("good_2")]
        with pytest.raises(REJECTED):
            kit.load(NAME, good + [with_text("zz_bad", 2, "a" * 81)], assignments="both")
        nothing_written()

    def test_a_bad_transcript_before_good_ones_writes_nothing_either(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        with pytest.raises(REJECTED):
            kit.load(NAME, [with_text("aa_bad", 2, "a" * 81), kit.transcript("good_3")], assignments="both")
        nothing_written()

    def test_a_count_violation_after_good_ones_writes_nothing(self, tune):
        tune(MAX_USER_MESSAGES_PER_CONVERSATION=4)
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.transcript("ok_4"), kit.transcript("zz_5", authors="ABABA")], assignments="both")
        nothing_written()

    def test_a_rejected_load_into_an_existing_experiment_adds_nothing(self, tune):
        tune(MAX_MESSAGE_CHARS=80)
        kit.load(NAME, [kit.transcript("kept")], assignments="both")
        before = kit.table_counts()
        with pytest.raises(REJECTED):
            kit.load(NAME, [kit.transcript("kept"), kit.transcript("new_ok"), with_text("new_bad", 2, "a" * 81)], assignments="both")
        assert kit.table_counts() == before


class TestAFailureWhileWritingRollsEverythingBack:
    def test_a_topic_clash_found_on_the_second_transcript_leaves_the_first_unwritten(self):
        from forum.models import Topic

        Topic.objects.create(title="Clash while writing", description="d", proposition="The standing proposition.")
        before = kit.table_counts()
        first = kit.transcript("aa_writes_first")
        second = kit.transcript("zz_clashes", topic=("Clash while writing", "Another proposition entirely."))
        with pytest.raises(REJECTED):
            kit.load(NAME, [first, second], assignments="both")
        assert kit.table_counts() == before


class TestTheSpikesValidationIsKept:
    def test_a_missing_key_is_rejected_naming_the_file_and_the_key(self):
        data = kit.transcript("nokey")
        del data["trigger_seq"]
        with pytest.raises(REJECTED) as caught:
            kit.load(NAME, [data])
        assert ("nokey" in str(caught.value), "trigger_seq" in str(caught.value)) == (True, True)

    def test_a_moderator_trigger_is_rejected(self):
        data = kit.transcript("modtrig", authors="ABMA", trigger_seq=3)
        with pytest.raises(REJECTED):
            kit.load(NAME, [data])
        nothing_written()

    def test_a_trigger_that_is_not_a_message_is_rejected(self):
        data = kit.transcript("badtrig", trigger_seq=9)
        with pytest.raises(REJECTED):
            kit.load(NAME, [data])
        nothing_written()

    def test_a_series_member_whose_computed_block_lies_is_rejected(self):
        base = kit.transcript("lie_base")
        member = kit.transcript(
            "lie_member",
            authors="ABBBB",
            series={"id": "flood", "factor": "flooding", "level": "flood", "side": "left", "base": "lie_base"},
            computed={
                "trigger_message_chars": 1, "trigger_message_words": 1, "longest_consecutive_run": 1,
                "repeated_sentence_across_messages": False, "unanswered_question_followed_by_two_replies": False,
            },
        )  # fmt: skip
        with pytest.raises(REJECTED) as caught:
            kit.load(NAME, [base, member])
        assert "lie_member" in str(caught.value)
        nothing_written()
