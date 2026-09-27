"""`preview.check_draft` when the check cannot run: every failure kind becomes an `unavailable` check with the right reason and
never raises; the per-participant rate cap; the budget caps and the breaker apply to previews as to any run."""
from datetime import timedelta
from decimal import Decimal

import pipeline_run_kit as prk
import preview_kit as pk
import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def assert_unavailable(stored, reason):
    assert (stored.outcome, stored.unavailable_reason) == ("unavailable", reason)
    assert stored.note_texts == []
    assert stored.master_output is None
    assert stored.intervenor_output is None
    assert (stored.action, stored.resulting_message_id, stored.reused_by_run_id, stored.resolved_at) == ("", None, None, None)


def run_unavailable(fake, *script, world=None, who="B"):
    """Check a draft with the scripted answers; returns (world, client, returned, stored, counts before)."""
    w = world or pk.world()
    client = fake(*script)
    before = pk.counts()
    returned, stored = pk.check(w, who)
    return w, client, returned, stored, before


class TestRefusalsOfTheGuard:
    def test_the_kill_switch_gives_llm_disabled_without_a_call(self, fake, settings):
        settings.LLM_ENABLED = False
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "llm_disabled")
        assert_unavailable(returned, "llm_disabled")
        assert client.calls == []
        assert [(r.agent, r.status, r.purpose, r.run_id) for r in pk.ledger_of(w.conv)] == [
            ("master", "refused_disabled", "moderation", None)
        ]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_a_missing_api_key_counts_as_disabled(self, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "llm_disabled")
        assert client.calls == []

    def test_an_exhausted_conversation_budget_gives_budget(self, fake, tune):
        tune(BUDGET_PER_CONVERSATION_USD=Decimal("0.0001"))
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "budget")
        assert client.calls == []
        assert [r.status for r in pk.ledger_of(w.conv)] == ["refused_budget"]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    @pytest.mark.parametrize("cap", ["BUDGET_SITE_USD_TOTAL", "BUDGET_SITE_USD_PER_DAY"])
    def test_the_site_caps_apply_too(self, fake, tune, cap):
        tune(**{cap: Decimal("0.0001")})
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "budget")
        assert client.calls == []

    def test_a_ledger_that_cannot_be_read_gives_budget(self, fake, monkeypatch):
        from moderation import budget
        from moderation.errors import BudgetUnavailable

        def broken(**kwargs):
            raise BudgetUnavailable("ledger unreadable")

        monkeypatch.setattr(budget, "check_caps", broken)
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "budget")
        assert client.calls == []

    def test_earlier_checks_count_against_the_conversation_cap(self, fake, tune):
        from moderation import budget

        w = pk.world()
        fake(pk.quiet_master(), pk.quiet_master())
        assert pk.check(w, "B")[1].outcome == "no_concern"
        spent = budget.spend(purposes=("moderation",), conversation_id=w.conv.pk)
        assert spent > 0
        tune(BUDGET_PER_CONVERSATION_USD=spent)
        client = fake()
        stored = pk.check(w, "B")[1]
        assert_unavailable(stored, "budget")
        assert client.calls == []

    def test_a_tripped_breaker_gives_breaker(self, fake):
        from moderation import breaker

        breaker.trip("manual", "tripped by the test")
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "breaker")
        assert client.calls == []
        assert [r.status for r in pk.ledger_of(w.conv)] == ["refused_breaker"]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_a_soft_breaker_in_cooldown_gives_breaker(self, fake):
        from moderation.models import GuardState

        GuardState.objects.update_or_create(
            pk=1,
            defaults=dict(
                breaker_tripped=True, trip_kind="soft", trip_reason="consecutive_errors",
                tripped_at=timezone.now(), cooldown_until=timezone.now() + timedelta(hours=1), cooldown_seconds=3600,
            ),
        )  # fmt: skip
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "breaker")
        assert client.calls == []

    def test_a_model_the_guard_refuses_gives_refused(self, fake, tune):
        tune(MASTER_MODEL="not-a-real-model")
        w, client, returned, stored, before = run_unavailable(fake)
        assert_unavailable(stored, "refused")
        assert client.calls == []
        assert pk.counts() == before


class TestRefusalBetweenTheTwoCalls:
    def change_before_intervenor(self, fake, change):
        w = pk.world()
        script = pk.concern_script()
        client = fake(prk.reply_with(script[0], before=lambda kwargs: change()), script[1])
        before = pk.counts()
        returned, stored = pk.check(w, "B")
        return w, client, stored, before

    def test_the_kill_switch_flipped_after_the_master_gives_llm_disabled_and_keeps_both_ledger_rows(self, fake, settings):
        def change():
            settings.LLM_ENABLED = False

        w, client, stored, before = self.change_before_intervenor(fake, change)
        assert_unavailable(stored, "llm_disabled")
        assert len(client.calls) == 1
        assert [(r.agent, r.status) for r in pk.ledger_of(w.conv)] == [("master", "ok"), ("intervenor", "refused_disabled")]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_the_budget_running_out_after_the_master_gives_budget(self, fake, tune):
        w, client, stored, before = self.change_before_intervenor(
            fake, lambda: tune(BUDGET_PER_CONVERSATION_USD=Decimal("0.0001"))
        )
        assert_unavailable(stored, "budget")
        assert [(r.agent, r.status) for r in pk.ledger_of(w.conv)] == [("master", "ok"), ("intervenor", "refused_budget")]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)

    def test_the_breaker_tripping_after_the_master_gives_breaker(self, fake):
        from moderation import breaker

        w, client, stored, before = self.change_before_intervenor(fake, lambda: breaker.trip("manual", "mid-check"))
        assert_unavailable(stored, "breaker")
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)


class TestStructuralFailures:
    @pytest.mark.parametrize("bad", [prk.BAD_MASTER, prk.invalid_json], ids=["schema_mismatch", "invalid_json"])
    def test_a_master_that_fails_twice_gives_structural_after_exactly_two_calls(self, fake, bad):
        w, client, returned, stored, before = run_unavailable(fake, bad, bad)
        assert_unavailable(stored, "structural")
        assert len(client.calls) == 2
        assert [(r.agent, r.attempt) for r in pk.ledger_of(w.conv)] == [("master", 1), ("master", 2)]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_an_intervenor_that_fails_twice_gives_structural_with_no_output_kept(self, fake):
        script = pk.concern_script()
        w, client, returned, stored, before = run_unavailable(fake, script[0], prk.BAD_INTERVENOR, prk.BAD_INTERVENOR)
        assert_unavailable(stored, "structural")
        assert len(client.calls) == 3
        assert [(r.agent, r.attempt) for r in pk.ledger_of(w.conv)] == [("master", 1), ("intervenor", 1), ("intervenor", 2)]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_a_model_refusal_stop_reason_twice_is_structural(self, fake):
        from moderation.fake_llm import make_message

        def refusal(kwargs):
            return make_message(None, stop_reason="refusal")

        w, client, returned, stored, before = run_unavailable(fake, refusal, refusal)
        assert_unavailable(stored, "structural")


class TestApiErrors:
    @pytest.mark.parametrize(
        "status,kind,message",
        [(500, "api_error", "upstream exploded"), (None, "timeout_error", "Request timed out."), (529, "overloaded_error", "busy")],
        ids=["server_error", "timeout", "overloaded"],
    )
    def test_a_master_api_error_gives_api_error_after_a_single_call(self, fake, status, kind, message):
        w, client, returned, stored, before = run_unavailable(fake, pk.provider_error(status, kind, message))
        assert_unavailable(stored, "api_error")
        assert len(client.calls) == 1
        assert [(r.agent, r.status) for r in pk.ledger_of(w.conv)] == [("master", "error")]
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_an_intervenor_api_error_gives_api_error(self, fake):
        script = pk.concern_script()
        w, client, returned, stored, before = run_unavailable(fake, script[0], pk.provider_error())
        assert_unavailable(stored, "api_error")
        assert len(client.calls) == 2
        assert stored.llm_call_ids == pk.ledger_ids(w.conv)
        assert pk.counts() == before

    def test_the_error_text_of_the_provider_is_not_kept_on_the_check(self, fake):
        w, client, returned, stored, before = run_unavailable(fake, pk.provider_error(500, "api_error", "distinctive-provider-text"))
        assert "distinctive-provider-text" not in repr(
            [getattr(stored, f.attname) for f in stored._meta.concrete_fields if f.attname != "draft_text"]
        )

    def test_a_spend_limit_error_gives_api_error_and_the_next_check_is_stopped_by_the_breaker(self, fake):
        w = pk.world()
        client = fake(pk.provider_error(400, "invalid_request_error", "You have reached your specified API usage limits. Regain access soon."))
        first = pk.check(w, "B")[1]
        second = pk.check(w, "A")[1]
        assert_unavailable(first, "api_error")
        assert_unavailable(second, "breaker")
        assert len(client.calls) == 1
