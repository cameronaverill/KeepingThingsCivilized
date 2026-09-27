"""Reuse in the live pipeline: posting the previewed text unchanged makes the live run take the check's outputs (no model call,
no ledger rows), and what it stores and posts is exactly what a fresh run of the same outputs would store and post."""
from datetime import timedelta

import pipeline_run_kit as prk
import preview_kit as pk
import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def previewed(fake, factory=pk.concern, who="B", text=pk.DRAFT, specs=None):
    """A world and the check of a draft in it."""
    w = pk.world(specs)
    fake(*factory(w, pk.PLACEHOLDER))
    check = pk.check(w, who, text)[1]
    return w, check


def reload_check(check):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.get(pk=check.pk)


def post_and_run(fake, w, who="B", text=pk.DRAFT):
    """The author posts; the live run happens with a model that must not be called. Returns (message, run, client, ledger rows before)."""
    client = fake()  # an empty script: any model call raises
    message, run = pk.post(w, who, text)
    rows_before = pk.total_calls()
    _, stored = prk.go(run)
    return message, stored, client, rows_before


class TestReuseAfterAConcern:
    def test_no_model_call_is_made_and_no_ledger_row_is_written(self, fake):
        w, check = previewed(fake)
        message, run, client, rows_before = post_and_run(fake, w)
        assert client.calls == []
        assert pk.total_calls() == rows_before
        assert prk.ledger(run) == []

    def test_the_run_finishes_done_and_posts_the_previewed_note_as_a_reply_to_the_message(self, fake):
        w, check = previewed(fake)
        message, run, client, _ = post_and_run(fake, w)
        assert (run.status, run.decision, run.failure_reason, run.error) == ("done", "intervene", "", "")
        assert run.posted_message.content == "\n\n".join(check.note_texts) == pk.NOTE
        assert (run.posted_message.author_type, run.posted_message.in_reply_to_id) == ("moderator", message.pk)
        assert [m.pk for m in prk.moderator_messages(w.conv)] == [run.posted_message_id]

    def test_the_run_records_the_check_in_its_config_snapshot_next_to_the_usual_entries(self, fake):
        w, check = previewed(fake)
        message, run, client, _ = post_and_run(fake, w)
        assert run.config_snapshot["preview_check_id"] == check.pk
        for key in ("models", "max_tokens", "prompts", "tunables"):
            assert key in run.config_snapshot

    def test_the_check_records_the_run_that_reused_it_and_is_otherwise_unchanged(self, fake):
        w, check = previewed(fake)
        message, run, client, _ = post_and_run(fake, w)
        stored = reload_check(check)
        assert stored.reused_by_run_id == run.pk
        for name in ("outcome", "unavailable_reason", "note_texts", "master_output", "intervenor_output", "llm_call_ids", "draft_text", "created_at"):
            assert getattr(stored, name) == getattr(check, name), name

    def test_the_issue_hangs_on_the_new_message_with_the_placeholder_replaced(self, fake):
        w, check = previewed(fake)
        message, run, client, _ = post_and_run(fake, w)
        (issue,) = prk.issues_of(run)
        assert (issue.message_id, issue.local_id, issue.validity, issue.rejection_reason) == (message.pk, "i1", "valid", "")
        assert message.content[issue.quote_start:issue.quote_end] == pk.QUOTE
        (act,) = prk.acts_of(run)
        assert [m.pk for m in act.source_messages.all()] == [message.pk]
        assert [i.local_id for i in act.source_issues.all()] == ["i1"]

    def test_real_message_ids_in_the_outputs_are_left_alone(self, fake):
        w, check = previewed(fake, pk.old_repetition)
        message, run, client, _ = post_and_run(fake, w)
        (issue,) = prk.issues_of(run)
        (act,) = prk.acts_of(run)
        assert (issue.message_id, issue.validity) == (w[1].pk, "valid")
        assert sorted(m.pk for m in act.source_messages.all()) == sorted([w[1].pk, message.pk])

    @pytest.mark.parametrize(
        "factory,decision",
        [
            (pk.concern, "intervene"),
            (pk.two_notes, "intervene"),
            (pk.one_bad_one_good, "intervene"),
            (pk.all_acts_invalid, "no_intervention"),
            (pk.old_repetition, "intervene"),
            (pk.acts_capped, "intervene"),
            (pk.declined, "no_intervention"),
            (pk.quiet, "no_intervention"),
        ],
        ids=lambda v: getattr(v, "__name__", v),
    )
    def test_what_is_stored_and_posted_equals_a_fresh_run_of_the_same_outputs(self, fake, tune, factory, decision):
        tune(MAX_ACTS_PER_INTERVENTION=2)
        w, check = previewed(fake, factory)
        message, run, client, _ = post_and_run(fake, w)
        w_fresh, client_fresh, fresh = pk.run_live(fake, factory)
        assert client.calls == []
        assert client_fresh.calls != []
        assert run.config_snapshot["preview_check_id"] == check.pk
        assert "preview_check_id" not in fresh.config_snapshot
        assert pk.fingerprint(run) == pk.fingerprint(fresh)
        assert run.decision == fresh.decision == decision

    def test_the_comparison_is_not_vacuous_a_fresh_run_of_other_outputs_differs(self, fake):
        w, check = previewed(fake, pk.concern)
        message, run, client, _ = post_and_run(fake, w)
        _, _, other = pk.run_live(fake, pk.two_notes)
        assert pk.fingerprint(run) != pk.fingerprint(other)

    def test_a_no_concern_check_is_reused_too(self, fake):
        w, check = previewed(fake, pk.quiet)
        assert check.outcome == "no_concern"
        message, run, client, _ = post_and_run(fake, w)
        assert client.calls == []
        assert (run.status, run.decision, run.rationale, run.posted_message) == ("done", "no_intervention", "no valid issues", None)
        assert run.config_snapshot["preview_check_id"] == check.pk
        assert reload_check(check).reused_by_run_id == run.pk

    def test_a_declined_intervention_is_reused_with_the_intervenors_rationale(self, fake):
        w, check = previewed(fake, pk.declined)
        message, run, client, _ = post_and_run(fake, w)
        assert client.calls == []
        assert (run.decision, run.rationale, run.posted_message) == ("no_intervention", "Not worth a post.", None)

    def test_the_first_message_of_a_conversation_reuses_its_check(self, fake):
        w = pk.world([])
        fake(*pk.concern_script())
        check = pk.check(w, "A")[1]
        message, run, client, _ = post_and_run(fake, w, who="A")
        assert (check.snapshot_seq, message.seq_no) == (0, 1)
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == check.pk

    def test_the_moderators_reply_to_an_earlier_message_before_the_draft_does_not_stop_reuse(self, fake):
        specs = pk.SPECS + [("mod", "A neutral question.")]
        w, check = previewed(fake, specs=specs)
        assert check.snapshot_seq == 4
        message, run, client, _ = post_and_run(fake, w)
        assert (message.seq_no, run.config_snapshot["preview_check_id"]) == (5, check.pk)


class TestReuseSeesTheSameTextNormalised:
    @pytest.mark.parametrize(
        "draft,posted",
        [
            (f"Rent control has failed, {pk.QUOTE}.\r\nSecond line {pk.MARKER}. ", f"Rent control has failed, {pk.QUOTE}.\nSecond line {pk.MARKER}."),
            (f"Rent control has failed, {pk.QUOTE}.\rSecond line.", f"Rent control has failed, {pk.QUOTE}.\nSecond line."),
            (f"Café owners say, {pk.QUOTE}.", f"Café owners say, {pk.QUOTE}."),
            (f"  \n{pk.DRAFT}\n\n", pk.DRAFT),
            (pk.DRAFT, f"\t{pk.DRAFT}  "),
        ],
        ids=["crlf_and_trailing_space", "lone_cr", "nfc", "surrounding_whitespace_in_draft", "surrounding_whitespace_in_post"],
    )
    def test_the_same_message_in_another_spelling_reuses_the_check(self, fake, draft, posted):
        w, check = previewed(fake, text=draft)
        message, run, client, _ = post_and_run(fake, w, text=posted)
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == check.pk

    def test_the_stored_hash_is_the_hash_of_the_normalised_text_so_the_live_message_matches_it(self, fake):
        w, check = previewed(fake, text=f"  {pk.DRAFT}\r\n")
        assert check.draft_sha256 == pk.sha(pk.DRAFT)


class TestTheReuseWindow:
    def test_a_check_a_little_younger_than_the_default_window_is_reused(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(created_at=timezone.now() - timedelta(seconds=850))
        message, run, client, _ = post_and_run(fake, w)
        assert client.calls == []
        assert run.config_snapshot["preview_check_id"] == check.pk

    def test_the_window_is_the_tunable(self, fake, tune):
        from moderation.models import PreviewCheck

        tune(PREVIEW_REUSE_SECONDS=60)
        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(created_at=timezone.now() - timedelta(seconds=50))
        message, run, client, _ = post_and_run(fake, w)
        assert run.config_snapshot["preview_check_id"] == check.pk

    def test_the_default_window_is_fifteen_minutes(self):
        from config import tunables

        assert tunables.PREVIEW_REUSE_SECONDS == 900


class TestReuseStillValidates:
    """The stored outputs go through the normal validation, storage and posting; nothing is trusted because it is stored."""

    def test_an_issue_whose_quote_is_not_in_the_message_is_rejected(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        output = dict(check.master_output)
        output["issues"] = [dict(output["issues"][0], quote="a phrase the message never contained")]
        PreviewCheck.objects.filter(pk=check.pk).update(master_output=output)
        message, run, client, _ = post_and_run(fake, w)
        assert client.calls == []
        assert prk.issue_summary(run) == [("i1", "rejected", "quote_not_found")]
        assert (run.status, run.decision, run.posted_message) == ("done", "no_intervention", None)

    def test_an_act_that_names_a_participant_is_rejected_and_nothing_is_posted(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        output = dict(check.intervenor_output)
        output["acts"] = [dict(output["acts"][0], text="Participant B, please cite a source.")]
        PreviewCheck.objects.filter(pk=check.pk).update(intervenor_output=output)
        message, run, client, _ = post_and_run(fake, w)
        assert prk.act_summary(run) == [(1, "rejected", "names_participant")]
        assert (run.status, run.decision, run.posted_message) == ("done", "no_intervention", None)

