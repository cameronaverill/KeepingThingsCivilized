"""When a check is NOT reusable the live run behaves exactly as it does today: it calls the model itself, stores what that
answer gives, records no `preview_check_id` and leaves the check unused. Each clause of the reuse rule is tested alone."""
from datetime import timedelta

import pipeline_run_kit as prk
import preview_kit as pk
import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def previewed(fake, who="B", text=pk.DRAFT, specs=None):
    w = pk.world(specs)
    fake(*pk.concern_script())
    return w, pk.check(w, who, text)[1]


def reload_check(check):
    from moderation.models import PreviewCheck

    return PreviewCheck.objects.get(pk=check.pk)


def live_run(fake, w, message, kind="live"):
    """The run for `message`, answered by a fresh scripted model. Returns (run, client)."""
    client = fake(*pk.concern(w, message.pk))
    _, stored = prk.go(prk.new_run(message, kind=kind))
    return stored, client


def assert_ran_as_today(run, client, check):
    assert len(client.calls) == 2
    assert [(r.agent, r.status, r.run_id) for r in prk.ledger(run)] == [("master", "ok", run.pk), ("intervenor", "ok", run.pk)]
    assert "preview_check_id" not in run.config_snapshot
    assert (run.status, run.decision, run.posted_message.content) == ("done", "intervene", pk.NOTE)
    assert reload_check(check).reused_by_run_id is None


class TestTextThatIsNotTheSame:
    @pytest.mark.parametrize(
        "posted",
        [
            pk.DRAFT + " Also more.",
            pk.DRAFT.replace("failed", "flopped"),
            pk.DRAFT.replace("Rent", "rent"),
            pk.DRAFT.replace(", ", ",  "),
            f"Well, {pk.DRAFT}",
        ],
        ids=["appended", "word_changed", "case_changed", "inner_space_doubled", "prefixed"],
    )
    def test_an_edited_text_gets_a_fresh_run(self, fake, posted):
        w, check = previewed(fake)
        message = w.add_user("B", posted)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)


class TestTheNewestMessageMustBeTheOneTheCheckSaw:
    def test_a_message_of_the_other_participant_in_between_gets_a_fresh_run(self, fake):
        w, check = previewed(fake, who="B")
        w.add_user("A", "While you were writing, I added a point about tenants.")
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_a_moderator_message_in_between_gets_a_fresh_run(self, fake):
        w, check = previewed(fake, who="B")
        w.add_moderator("A neutral question for both.", in_reply_to=w[3])
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_an_earlier_message_of_the_same_author_in_between_gets_a_fresh_run(self, fake):
        w, check = previewed(fake, who="B")
        w.add_user("B", "A short extra point first.")
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_a_check_made_earlier_in_a_shorter_conversation_is_not_used_for_the_same_text_later(self, fake):
        w, check = previewed(fake, who="B")
        w.add_user("A", "Something new.")
        w.add_user("B", "Something else.")
        w.add_user("A", "Another point.")
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)


class TestTheCheckMustBeOfTheSameAuthor:
    def test_the_other_participant_posting_the_same_text_right_after_gets_a_fresh_run(self, fake):
        w, check = previewed(fake, who="A")
        message = w.add_user("B", pk.DRAFT)
        assert message.seq_no - 1 == check.snapshot_seq
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_a_direct_call_to_claim_reusable_check_finds_nothing_for_the_wrong_participant(self, fake):
        """Bypasses the live-run dispatch entirely (no `prk.go`, no model call scripted): calls the guard function
        itself, the way `moderation.pipeline` does, to prove the refusal at that exact entry point rather than through
        the full pipeline's side effects."""
        from moderation import preview

        w, check = previewed(fake, who="A")
        message = w.add_user("B", pk.DRAFT)
        run = prk.new_run(message)

        result = preview.claim_reusable_check(run)

        assert result is None
        assert reload_check(check).reused_by_run_id is None


class TestAWrongParticipantCandidateNeverWinsOverTheRightOne:
    def test_the_right_participants_check_is_claimed_even_though_a_same_shaped_check_of_the_other_participant_exists_too(
        self, fake
    ):
        """Two checks of the same conversation, same snapshot_seq and the same draft hash, one per participant: the
        participant filter must be a real filter (not a matter of which row happens to be unclaimed or newest), so both
        rows are live candidates by every other key at once, not "the one candidate happens to be wrong"."""
        from moderation.models import PreviewCheck

        w = pk.world()
        fake(*pk.concern(w, pk.PLACEHOLDER))
        check = pk.check(w, "B", pk.DRAFT)[1]
        fake(*pk.two_notes(w, pk.PLACEHOLDER))
        decoy = pk.check(w, "A", pk.DRAFT)[1]
        assert (decoy.snapshot_seq, decoy.draft_sha256) == (check.snapshot_seq, check.draft_sha256)
        assert decoy.pk > check.pk  # created later, so it would be tried first were the candidates ordering alone relied on

        client = fake()
        message, run = pk.post(w, "B", pk.DRAFT)
        _, stored = prk.go(run)

        assert client.calls == []
        assert stored.config_snapshot["preview_check_id"] == check.pk
        assert stored.posted_message.content == pk.NOTE
        assert PreviewCheck.objects.get(pk=check.pk).reused_by_run_id == stored.pk
        assert PreviewCheck.objects.get(pk=decoy.pk).reused_by_run_id is None


class TestTheCheckMustHaveAnAnswer:
    def test_an_unavailable_check_is_never_reused(self, fake, tune):
        tune(PREVIEW_SHARE=0.0)
        w = pk.world()
        fake()
        check = pk.check(w, "B")[1]
        assert check.outcome == "unavailable"
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_a_check_that_failed_with_an_api_error_is_never_reused(self, fake):
        w = pk.world()
        fake(pk.provider_error())
        check = pk.check(w, "B")[1]
        assert (check.outcome, check.unavailable_reason) == ("unavailable", "api_error")
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_a_check_refused_by_the_rate_cap_is_never_reused(self, fake, tune):
        tune(PREVIEW_MAX_CHECKS_PER_MINUTE=1)
        w = pk.world()
        fake(pk.quiet_master())
        pk.check(w, "B", "An earlier draft.")
        fake()
        check = pk.check(w, "B")[1]
        assert (check.outcome, check.unavailable_reason) == ("unavailable", "rate_limited")
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)


class TestOnlyAnAnsweredCheckCountsWhateverItHolds:
    @pytest.mark.parametrize("reason", ["api_error", "budget", "off", "rate_limited", "internal_error"])
    def test_a_check_marked_unavailable_is_not_reused_even_if_it_still_holds_outputs(self, fake, reason):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(outcome="unavailable", unavailable_reason=reason)
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)


class TestTheCheckMustBeFresh:
    def test_an_expired_check_gets_a_fresh_run(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(created_at=timezone.now() - timedelta(seconds=950))
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_the_window_is_the_tunable_when_it_is_shorter(self, fake, tune):
        from moderation.models import PreviewCheck

        tune(PREVIEW_REUSE_SECONDS=60)
        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(created_at=timezone.now() - timedelta(seconds=90))
        message = w.add_user("B", pk.DRAFT)
        run, client = live_run(fake, w, message)
        assert_ran_as_today(run, client, check)

    def test_the_window_is_the_tunable_when_it_is_longer(self, fake, tune):
        from moderation.models import PreviewCheck

        tune(PREVIEW_REUSE_SECONDS=5000)
        w, check = previewed(fake)
        PreviewCheck.objects.filter(pk=check.pk).update(created_at=timezone.now() - timedelta(seconds=3000))
        fake()
        message = w.add_user("B", pk.DRAFT)
        _, run = prk.go(prk.new_run(message))
        assert run.config_snapshot["preview_check_id"] == check.pk


class TestTheCheckIsUsedOnce:
    def test_a_check_that_was_already_reused_is_not_reused_again(self, fake):
        from moderation.models import PreviewCheck

        w, check = previewed(fake)
        fake()
        first = w.add_user("B", pk.DRAFT)
        _, first_run = prk.go(prk.new_run(first))
        assert reload_check(check).reused_by_run_id == first_run.pk
        second = w.add_user("B", pk.DRAFT)
        PreviewCheck.objects.filter(pk=check.pk).update(snapshot_seq=second.seq_no - 1)
        run, client = live_run(fake, w, second)
        assert len(client.calls) == 2
        assert "preview_check_id" not in run.config_snapshot
        assert reload_check(check).reused_by_run_id == first_run.pk

    def test_reused_by_run_id_is_set_once_and_never_changed(self, fake):
        w, check = previewed(fake)
        fake()
        message = w.add_user("B", pk.DRAFT)
        run = prk.new_run(message)
        prk.go(run)
        prk.go(run)  # a run that is already done is returned unchanged
        assert reload_check(check).reused_by_run_id == run.pk


class TestReplaysNeverReuse:
    def test_a_replay_of_the_message_calls_the_model_and_leaves_the_check_for_the_live_run(self, fake):
        w, check = previewed(fake)
        message = w.add_user("B", pk.DRAFT)
        replay, client = live_run(fake, w, message, kind="replay")
        assert len(client.calls) == 2
        assert {r.purpose for r in prk.ledger(replay)} == {"replay"}
        assert "preview_check_id" not in replay.config_snapshot
        assert reload_check(check).reused_by_run_id is None
        fake()
        _, live = prk.go(prk.new_run(message))
        assert live.config_snapshot["preview_check_id"] == check.pk
