"""Places where docs/step5_brief.md leaves a reading open. Each test states the reading chosen by the 5b tester; if the
architect settles it the other way, only the test named here changes. See the tester's report for the reasoning.

1. The Intervenor prompt (moderation/prompts/intervenor_v1.md) asks for `addressee` and `subject` as "Participant A" /
   "Participant B", but `InterventionAct` stores a bare letter (A to Z, `all`, `both`, `none`). Chosen reading: the pipeline
   accepts the prompt's own spelling and stores the letter. Without it every act a real model writes would be rejected as
   `bad_label`.
"""
import pipeline_run_kit as kit
import pytest

pytestmark = pytest.mark.django_db


def one_act(fake, **fields):
    world = kit.build()
    run = kit.new_run(world.last)
    fake(
        kit.master_d(kit.issue_d("i1", world.last)),
        kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(**fields)]),
    )
    _, stored = kit.go(run)
    return stored


class TestLabelsSpelledTheWayThePromptAsksFor:
    def test_an_addressee_written_participant_b_is_accepted_and_stored_as_the_letter(self, fake):
        stored = one_act(fake, addressee="Participant B", subject="none")
        (act,) = kit.acts_of(stored)
        assert (act.validity, act.addressee, act.subject) == ("valid", "B", "none")

    def test_a_subject_written_participant_a_is_accepted_and_stored_as_the_letter(self, fake):
        stored = one_act(fake, addressee="all", subject="Participant A")
        (act,) = kit.acts_of(stored)
        assert (act.validity, act.addressee, act.subject) == ("valid", "all", "A")

    def test_a_participant_that_does_not_exist_is_still_a_bad_label_in_either_spelling(self, fake):
        stored = one_act(fake, addressee="Participant C", subject="none")
        assert kit.act_summary(stored) == [(1, "rejected", "bad_label")]


class TestSourceMessagesTheModelWasNotShown:
    """2. "Unknown source message ids are rejected" (`bad_source_message`). Chosen reading: an id is acceptable only if the
    message was in the transcript the Intervenor was shown (up to the snapshot, inside the window), so a message that
    exists in the conversation but was cut from the window, or arrived after the snapshot, is rejected too."""

    def test_a_message_older_than_the_window_is_rejected(self, fake, tune):
        tune(TRANSCRIPT_MAX_MESSAGES=2)
        world = kit.build()
        run = kit.new_run(world.last)
        fake(
            kit.master_d(kit.issue_d("i1", world.last)),
            kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(messages=[world[1]]), kit.act_d(kit.CLEAN_TEXT_2, messages=[world[2]])]),
        )
        _, stored = kit.go(run)
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_message"), (2, "valid", "")]

    def test_a_message_newer_than_the_snapshot_is_rejected(self, fake):
        world = kit.build()
        run = kit.new_run(world[2])
        fake(
            kit.master_d(kit.issue_d("i1", world[2], "unsupported_claim", "tenants gain a lot")),
            kit.interv_d(dispositions=[kit.disp_d("i1")], acts=[kit.act_d(messages=[world[3]]), kit.act_d(kit.CLEAN_TEXT_2, messages=[world[1]])]),
        )
        _, stored = kit.go(run)
        assert kit.act_summary(stored) == [(1, "rejected", "bad_source_message"), (2, "valid", "")]
