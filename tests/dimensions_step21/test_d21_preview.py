"""Step 21, part 2: a draft check never fires the note; a due live run reuses the preview's Master output but asks the
Intervenor afresh (the stored Intervenor output has no summary act)."""
import dim21_kit as dk
import pipeline_run_kit as prk
import preview_kit as pk
import pytest

pytestmark = pytest.mark.django_db

MAP = dict(agreements=[dk.AGREEMENT])


def due_world():
    """Three user messages (A, B, A); B's draft would be the fourth, so a live run for it is summary due."""
    return pk.world()


def previewed(fake, script):
    w = due_world()
    client = fake(*script(w, pk.PLACEHOLDER))
    check = pk.check(w, "B")[1]
    return w, check, client


def concern_with_map(w, draft_id):
    return pk.concern_script(draft_id, **MAP)


def quiet_with_map(w, draft_id):
    return [dk.map_master()]


def fresh_intervenor(message, *, with_issue=True):
    acts = []
    dispositions = []
    if with_issue:
        acts.append(prk.act_d(pk.NOTE_2, issues=["i1"], messages=[message], addressee="all", subject="none"))
        dispositions.append(prk.disp_d("i1"))
    acts.append(dk.summary_act())
    return prk.interv_d(dispositions=dispositions, acts=acts)


class TestADraftCheckNeverFiresTheNote:
    def test_a_draft_that_would_be_the_fourth_user_message_with_a_map_and_no_issue_makes_no_intervenor_call(self, fake):
        w = due_world()
        client = fake(dk.map_master())
        returned, stored = pk.check(w, "B")
        assert stored.outcome == "no_concern"
        assert dk.intervenor_calls(client) == [] and len(client.calls) == 1
        assert stored.note_texts == []

    def test_with_a_concern_the_intervenor_input_carries_no_summary_block(self, fake):
        w, check, client = previewed(fake, concern_with_map)
        assert check.outcome == "concern"
        (call,) = dk.intervenor_calls(client)
        assert dk.summary_block_lines()
        assert not dk.sent_summary_flag(call)

    def test_a_summary_act_the_model_adds_on_its_own_is_not_special_in_a_check_and_nothing_is_written_to_the_run_tables(self, fake):
        w = due_world()
        before = pk.counts()
        fake(*concern_with_map(w, pk.PLACEHOLDER))
        pk.check(w, "B")
        assert pk.counts() == before

    def test_the_check_stores_a_master_output_that_keeps_the_map(self, fake):
        w, check, client = previewed(fake, concern_with_map)
        assert check.master_output["discussion_map"]["agreements"] == [dk.AGREEMENT]


class TestADueLiveRunAfterAPreview:
    def test_it_reuses_the_stored_master_output_and_calls_the_intervenor_afresh(self, fake):
        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        client = fake(fresh_intervenor(message))
        _, stored = prk.go(run)
        assert dk.master_calls(client) == []
        (call,) = dk.intervenor_calls(client)
        assert dk.sent_summary_flag(call)
        assert (stored.status, stored.decision) == ("done", "intervene")

    def test_the_posted_message_is_the_fresh_output_not_the_previewed_note(self, fake):
        w, check, _ = previewed(fake, concern_with_map)
        assert check.note_texts == [pk.NOTE]
        message, run = pk.post(w, "B", pk.DRAFT)
        fake(fresh_intervenor(message))
        _, stored = prk.go(run)
        assert stored.posted_message.content == pk.NOTE_2 + "\n\n" + dk.SUMMARY_TEXT
        assert pk.NOTE not in stored.posted_message.content
        assert [a.act_type for a in prk.acts_of(stored)] == ["request_information", dk.SUMMARY_TYPE]

    def test_the_issue_from_the_stored_master_output_lands_on_the_new_message(self, fake):
        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        fake(fresh_intervenor(message))
        _, stored = prk.go(run)
        (issue,) = prk.issues_of(stored)
        assert (issue.message_id, issue.local_id, issue.validity) == (message.pk, "i1", "valid")
        assert stored.discussion_map["agreements"] == [dk.AGREEMENT]

    def test_the_check_is_claimed_and_recorded_in_the_config_snapshot(self, fake):
        from moderation.models import PreviewCheck

        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        fake(fresh_intervenor(message))
        _, stored = prk.go(run)
        assert stored.config_snapshot["preview_check_id"] == check.pk
        assert PreviewCheck.objects.get(pk=check.pk).reused_by_run_id == stored.pk

    def test_only_the_intervenor_call_is_ledgered_for_the_live_run(self, fake):
        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        fake(fresh_intervenor(message))
        _, stored = prk.go(run)
        assert [(r.agent, r.status) for r in prk.ledger(stored)] == [("intervenor", "ok")]

    def test_a_preview_with_no_issue_but_a_map_gives_a_due_live_run_a_fresh_intervenor_call(self, fake):
        w, check, _ = previewed(fake, quiet_with_map)
        assert check.outcome == "no_concern" and check.intervenor_output is None
        message, run = pk.post(w, "B", pk.DRAFT)
        client = fake(prk.interv_d(acts=[dk.summary_act()]))
        _, stored = prk.go(run)
        assert dk.master_calls(client) == []
        assert len(dk.intervenor_calls(client)) == 1
        assert stored.posted_message.content == dk.SUMMARY_TEXT

    def test_a_due_run_whose_preview_map_is_empty_reuses_everything_and_makes_no_call(self, fake):
        w, check, _ = previewed(fake, lambda w, d: [prk.master_d()])
        message, run = pk.post(w, "B", pk.DRAFT)
        client = fake()
        _, stored = prk.go(run)
        assert client.calls == []
        assert (stored.status, stored.decision, stored.rationale) == ("done", "no_intervention", "no valid issues")

    def test_a_due_run_with_a_reused_master_and_a_missing_summary_act_posts_what_the_intervenor_gave(self, fake):
        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        fake(prk.interv_d(
            dispositions=[prk.disp_d("i1")],
            acts=[prk.act_d(pk.NOTE_2, issues=["i1"], messages=[message], addressee="all", subject="none")],
        ))
        _, stored = prk.go(run)
        assert (stored.status, stored.failure_reason, stored.error) == ("done", "", "")
        assert stored.posted_message.content == pk.NOTE_2


class TestANotDueLiveRunStillReusesBothOutputs:
    def test_at_the_third_user_message_nothing_is_called(self, fake):
        w = pk.world(pk.SPECS[:2])  # A, B; the draft is the third user message
        fake(*concern_with_map(w, pk.PLACEHOLDER))
        check = pk.check(w, "A")[1]
        assert check.outcome == "concern"
        message, run = pk.post(w, "A", pk.DRAFT)
        client = fake()
        _, stored = prk.go(run)
        assert client.calls == []
        assert stored.posted_message.content == pk.NOTE

    def test_with_the_cadence_turned_off_a_would_be_due_run_reuses_the_stored_intervenor_output(self, fake, tune):
        tune(**{dk.TUNABLE: 0})
        w, check, _ = previewed(fake, concern_with_map)
        message, run = pk.post(w, "B", pk.DRAFT)
        client = fake()
        _, stored = prk.go(run)
        assert client.calls == []
        assert stored.posted_message.content == pk.NOTE
