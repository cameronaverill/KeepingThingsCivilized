"""Failure paths of run_moderation (brief "5b details" step 7): structural failure, refusals, API errors. The run row always
ends in a terminal status, nothing is posted, and rows that were already stored stay."""
import logging
from datetime import timedelta

import pipeline_run_kit as kit
import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

SECRET_TOKEN = "sk-ant-" + "Ab1_-" * 8


def setup():
    world = kit.build()
    return world, kit.new_run(world.last)


def master_ok(world):
    return kit.master_d(kit.issue_d("i1", world.last), agreements=["Both want stable housing."])


def intervene_script(world):
    return kit.simple_success(world)


def assert_nothing_posted(world, stored, before):
    assert stored.posted_message is None
    assert kit.message_count(world.conv) == before


def assert_no_acts_or_dispositions(stored):
    from moderation.models import IssueDisposition

    assert kit.acts_of(stored) == []
    assert IssueDisposition.objects.filter(issue__run=stored).count() == 0


class TestStructuralFailure:
    @pytest.mark.parametrize("bad", [kit.BAD_MASTER, kit.invalid_json], ids=["schema_mismatch", "invalid_json"])
    def test_a_master_that_fails_twice_fails_the_run_after_exactly_one_retry(self, fake, bad):
        world, run = setup()
        client = fake(bad, bad)
        before = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert (returned.status, stored.status, stored.failure_reason) == ("failed", "failed", "structural")
        assert len(client.calls) == 2
        assert [(r.agent, r.attempt) for r in kit.ledger(run)] == [("master", 1), ("master", 2)]
        assert kit.issues_of(stored) == []
        assert_nothing_posted(world, stored, before)

    def test_a_master_that_fails_once_and_then_succeeds_completes_the_run(self, fake):
        world, run = setup()
        script = intervene_script(world)
        client = fake(kit.BAD_MASTER, *script)
        _, stored = kit.go(run)
        assert (stored.status, stored.decision) == ("done", "intervene")
        assert [(r.agent, r.attempt) for r in kit.ledger(run)] == [("master", 1), ("master", 2), ("intervenor", 1)]
        assert len(client.calls) == 3

    def test_an_intervenor_that_fails_twice_fails_the_run_but_keeps_the_masters_issues(self, fake):
        world, run = setup()
        client = fake(master_ok(world), kit.BAD_INTERVENOR, kit.BAD_INTERVENOR)
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("failed", "structural")
        assert len(client.calls) == 3
        assert [(r.agent, r.attempt) for r in kit.ledger(run)] == [("master", 1), ("intervenor", 1), ("intervenor", 2)]
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_no_acts_or_dispositions(stored)
        assert_nothing_posted(world, stored, before)

    def test_the_masters_discussion_map_is_kept_when_only_the_intervenor_fails(self, fake):
        world, run = setup()
        fake(master_ok(world), kit.invalid_json, kit.invalid_json)
        _, stored = kit.go(run)
        assert stored.discussion_map == {"agreements": ["Both want stable housing."], "disagreements": []}

    def test_a_failed_run_can_not_be_run_again(self, fake):
        world, run = setup()
        client = fake(kit.BAD_MASTER, kit.BAD_MASTER)
        kit.go(run)
        _, stored = kit.go(run)
        assert stored.status == "failed"
        assert len(client.calls) == 2


class TestRefusals:
    def test_the_kill_switch_marks_the_run_skipped_disabled_and_posts_nothing(self, fake, settings):
        settings.LLM_ENABLED = False
        world, run = setup()
        client = fake()
        before = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("skipped_disabled", "skipped_disabled")
        assert client.calls == []
        assert [r.status for r in kit.ledger(run)] == ["refused_disabled"]
        assert kit.issues_of(stored) == []
        assert_nothing_posted(world, stored, before)

    def test_a_missing_api_key_counts_as_disabled(self, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        world, run = setup()
        client = fake()
        _, stored = kit.go(run)
        assert stored.status == "skipped_disabled"
        assert client.calls == []

    def test_an_over_budget_guard_state_marks_the_run_skipped_budget(self, fake, tune):
        from decimal import Decimal

        tune(BUDGET_SITE_USD_TOTAL=Decimal("0.0001"))
        world, run = setup()
        client = fake()
        before = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("skipped_budget", "skipped_budget")
        assert client.calls == []
        assert [r.status for r in kit.ledger(run)] == ["refused_budget"]
        assert_nothing_posted(world, stored, before)

    def test_a_ledger_that_cannot_be_read_marks_the_run_skipped_budget(self, fake, monkeypatch):
        from moderation import budget
        from moderation.errors import BudgetUnavailable

        def broken(**kwargs):
            raise BudgetUnavailable("ledger unreadable")

        monkeypatch.setattr(budget, "check_caps", broken)
        world, run = setup()
        client = fake()
        _, stored = kit.go(run)
        assert stored.status == "skipped_budget"
        assert client.calls == []
        assert stored.posted_message is None

    def test_a_tripped_breaker_marks_the_run_skipped_budget_with_reason_breaker_open(self, fake):
        from moderation import breaker

        breaker.trip("manual", "tripped by the test")
        world, run = setup()
        client = fake()
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("skipped_budget", "breaker_open")
        assert client.calls == []
        assert [r.status for r in kit.ledger(run)] == ["refused_breaker"]
        assert_nothing_posted(world, stored, before)

    def test_a_soft_breaker_in_cooldown_is_also_breaker_open(self, fake):
        from moderation.models import GuardState

        GuardState.objects.update_or_create(
            pk=1,
            defaults=dict(
                breaker_tripped=True, trip_kind="soft", trip_reason="consecutive_errors",
                tripped_at=timezone.now(), cooldown_until=timezone.now() + timedelta(hours=1), cooldown_seconds=3600,
            ),
        )  # fmt: skip
        world, run = setup()
        client = fake()
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("skipped_budget", "breaker_open")
        assert client.calls == []

    def test_a_refusal_before_the_first_call_writes_no_issue_and_sets_no_decision(self, fake, settings):
        settings.LLM_ENABLED = False
        world, run = setup()
        fake()
        _, stored = kit.go(run)
        assert kit.issues_of(stored) == []
        assert stored.decision == ""


class TestRefusalBetweenTheTwoCalls:
    """The Master succeeded and its issues were stored; then the guard refuses the Intervenor call."""

    def run_with_change_before_intervenor(self, fake, change):
        """`change()` runs while the Master's call is being served, so it is in force for the Intervenor's call."""
        world, run = setup()
        script = intervene_script(world)
        client = fake(kit.reply_with(script[0], before=lambda kwargs: change()), script[1])
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        return world, stored, client, before

    def test_the_kill_switch_flipped_after_the_master_skips_the_run_and_keeps_the_issues(self, fake, settings):
        def change():
            settings.LLM_ENABLED = False

        world, stored, client, before = self.run_with_change_before_intervenor(fake, change)
        assert stored.status == "skipped_disabled"
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_no_acts_or_dispositions(stored)
        assert_nothing_posted(world, stored, before)
        assert [(r.agent, r.status) for r in kit.ledger(stored)] == [("master", "ok"), ("intervenor", "refused_disabled")]

    def test_the_budget_running_out_after_the_master_skips_the_run_and_keeps_the_issues(self, fake, tune):
        from decimal import Decimal

        def change():
            tune(BUDGET_PER_CONVERSATION_USD=Decimal("0.0001"))

        world, stored, client, before = self.run_with_change_before_intervenor(fake, change)
        assert stored.status == "skipped_budget"
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_nothing_posted(world, stored, before)
        assert [(r.agent, r.status) for r in kit.ledger(stored)] == [("master", "ok"), ("intervenor", "refused_budget")]

    def test_the_breaker_tripping_after_the_master_skips_the_run_with_breaker_open(self, fake):
        from moderation import breaker

        world, stored, client, before = self.run_with_change_before_intervenor(
            fake, lambda: breaker.trip("manual", "tripped mid-run")
        )
        assert (stored.status, stored.failure_reason) == ("skipped_budget", "breaker_open")
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_nothing_posted(world, stored, before)


class TestApiErrors:
    def provider_error(self, status=500, kind="api_error", message="upstream exploded"):
        from moderation.fake_llm import FakeProviderError

        return FakeProviderError(status, kind, message)

    def test_a_master_api_error_fails_the_run_after_a_single_call(self, fake):
        world, run = setup()
        client = fake(self.provider_error())
        before = kit.message_count(world.conv)
        returned, stored = kit.go(run)
        assert (returned.status, stored.status, stored.failure_reason) == ("failed", "failed", "api_error")
        assert len(client.calls) == 1
        assert [(r.agent, r.status) for r in kit.ledger(run)] == [("master", "error")]
        assert "upstream exploded" in stored.error
        assert kit.issues_of(stored) == []
        assert_nothing_posted(world, stored, before)

    def test_an_intervenor_api_error_fails_the_run_and_keeps_the_issues(self, fake):
        world, run = setup()
        client = fake(master_ok(world), self.provider_error())
        before = kit.message_count(world.conv)
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("failed", "api_error")
        assert len(client.calls) == 2
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_no_acts_or_dispositions(stored)
        assert_nothing_posted(world, stored, before)

    def test_the_termination_log_line_carries_the_failure_reason_but_not_the_error_text(self, fake, caplog):
        world, run = setup()
        fake(self.provider_error(500, "api_error", "a sensitive upstream detail"))
        with caplog.at_level(logging.WARNING, logger="moderation.pipeline"):
            _, stored = kit.go(run)
        assert stored.status == "failed"
        [record] = [r for r in caplog.records if r.name == "moderation.pipeline"]
        message = record.getMessage()
        assert f"status=failed" in message and "reason=api_error" in message
        assert str(run.pk) in message
        assert "a sensitive upstream detail" not in message

    def test_no_key_material_reaches_the_stored_error(self, fake, settings):
        configured = "configured-secret-value-0123456789"
        settings.ANTHROPIC_API_KEY = configured
        message = f"invalid x-api-key {SECRET_TOKEN}; authorization: Bearer {configured}; upstream exploded"
        world, run = setup()
        fake(self.provider_error(401, "authentication_error", message))
        _, stored = kit.go(run)
        assert stored.status == "failed"
        assert stored.error != ""
        for secret in (SECRET_TOKEN, configured, "Ab1_-Ab1_-"):
            assert secret not in stored.error

    def test_the_error_text_is_scrubbed_even_when_the_message_holds_a_key_in_a_header_style_line(self, fake):
        world, run = setup()
        fake(self.provider_error(500, "api_error", f"x-api-key: {SECRET_TOKEN}"))
        _, stored = kit.go(run)
        assert SECRET_TOKEN not in stored.error
        assert SECRET_TOKEN not in "".join(r.error for r in kit.ledger(run))

    def test_a_spend_limit_error_fails_the_run_and_the_next_run_is_skipped_by_the_breaker(self, fake):
        world = kit.build()
        first, second = kit.new_run(world[2]), kit.new_run(world[3])
        client = fake(
            self.provider_error(400, "invalid_request_error", "You have reached your specified API usage limits. Regain access soon.")
        )
        _, stored_first = kit.go(first)
        _, stored_second = kit.go(second)
        assert (stored_first.status, stored_first.failure_reason) == ("failed", "api_error")
        assert (stored_second.status, stored_second.failure_reason) == ("skipped_budget", "breaker_open")
        assert len(client.calls) == 1


class TestNothingEscapes:
    def test_a_model_the_guard_refuses_still_ends_in_a_terminal_status_without_raising(self, fake, tune):
        tune(MASTER_MODEL="not-a-real-model")
        world, run = setup()
        client = fake()
        _, stored = kit.go(run)
        assert stored.status in kit.TERMINAL
        assert stored.status != "done"
        assert client.calls == []
        assert stored.posted_message is None

    def test_every_failed_or_skipped_run_leaves_the_conversation_unchanged(self, fake, settings):
        settings.LLM_ENABLED = False
        world, run = setup()
        fake()
        before = kit.message_count(world.conv)
        kit.go(run)
        assert kit.message_count(world.conv) == before


class TestProgrammingErrors:
    """A bug may escape run_moderation as an exception, but the run row must still end in a terminal status and nothing
    may be posted."""

    def test_a_crash_before_the_first_call_leaves_the_run_failed(self, fake, monkeypatch):
        import contextlib

        from moderation import features

        def boom(transcript):
            raise RuntimeError("a bug in process_facts")

        monkeypatch.setattr(features, "process_facts", boom)
        world, run = setup()
        client = fake()
        before = kit.message_count(world.conv)
        with contextlib.suppress(RuntimeError):
            kit.go(run)
        stored = kit.reload(run)
        assert stored.status == "failed"
        assert client.calls == []
        assert_nothing_posted(world, stored, before)

    def test_a_crash_after_the_master_leaves_the_run_failed_keeps_the_issues_and_posts_nothing(self, fake, monkeypatch):
        import contextlib

        from moderation import label_check

        def boom(text):
            raise RuntimeError("a bug in the label check")

        monkeypatch.setattr(label_check, "names_a_label", boom)
        world, run = setup()
        fake(*intervene_script(world))
        before = kit.message_count(world.conv)
        with contextlib.suppress(RuntimeError):
            kit.go(run)
        stored = kit.reload(run)
        assert stored.status == "failed"
        assert kit.issue_summary(stored) == [("i1", "valid", "")]
        assert_nothing_posted(world, stored, before)
