"""moderation/research.py::run_research (docs/step20b_brief.md item 3): one guarded, web_search-enabled call that
turns an `offer_research` act into a posted, sourced moderator note, or ends the run in the same failure vocabulary
`moderation.pipeline.run_moderation` already uses for the equivalent case.

Expected to fail with an ImportError on `moderation.research` (or on ModerationRun/InterventionAct fields these
fixtures build on) until the coding agent's moderation/research.py lands; that is expected, not a bug here.
"""
import re

import pytest
import research_kit as kit

pytestmark = pytest.mark.django_db


def setup(**scenario_kwargs):
    scenario = kit.make_research_scenario(**scenario_kwargs)
    run = kit.new_research_run(scenario)
    return scenario, run


# --- The success path -------------------------------------------------------------------------------------------

class TestSuccess:
    def test_a_successful_run_posts_a_moderator_reply_and_finishes_done(self, fake):
        scenario, run = setup()
        before = kit.message_count(scenario.conv)
        fake(kit.success_item(tool_results=[kit.tool_result_block([("A source", "https://example.org/a")])]))
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("done", "done")
        assert kit.message_count(scenario.conv) == before + 1
        assert stored.posted_message_id is not None

    def test_the_posted_message_is_moderator_authored_and_replies_to_the_claim(self, fake):
        scenario, run = setup()
        fake(kit.success_item())
        _, stored = kit.go(run)
        posted = stored.posted_message
        assert posted.author_type == "moderator"
        assert posted.participant_id is None
        assert posted.in_reply_to_id == scenario.claim.pk
        assert posted.conversation_id == scenario.conv.pk

    def test_the_posted_message_carries_the_notes_text(self, fake):
        scenario, run = setup()
        note_text = "Independent reporting does not corroborate the specific figure cited."
        fake(kit.success_item(note=kit.research_note_d(text=note_text)))
        _, stored = kit.go(run)
        assert note_text in stored.posted_message.content

    def test_a_call_with_no_search_results_still_posts_and_finishes_done(self, fake):
        """Item 4: zero tool_blocks is not an error; the note is still posted."""
        scenario, run = setup()
        fake(kit.success_item(tool_results=[]))
        _, stored = kit.go(run)
        assert stored.status == "done"
        assert stored.posted_message_id is not None

    def test_a_second_call_on_an_already_finished_run_does_not_call_the_model_again(self, fake):
        """Same re-check style as pipeline.run_moderation: a run not in (pending, running) is returned unchanged."""
        scenario, run = setup()
        client = fake(kit.success_item())
        kit.go(run)
        client2 = fake()
        returned, stored = kit.go(kit.reload(run))
        assert returned.status == "done"
        assert client2.calls == []


# --- Blinding: the prompt carries no participant label, username or side -----------------------------------------

class TestBlinding:
    def test_no_username_or_email_reaches_the_prompt_the_request_or_the_ledger(self, fake):
        from moderation.models import LLMCall

        scenario, run = setup()
        assert {p.user.username for p in scenario.parts.values()} == {kit.USER_A["username"], kit.USER_B["username"]}
        client = fake(kit.success_item())
        kit.go(run)
        sent = kit.all_request_text(client.calls)
        ledger_text = kit.all_request_text(
            [{"system": r.request, "messages": [{"content": r.raw_response}]} for r in LLMCall.objects.all()]
        )
        for needle in kit.FORBIDDEN_STRINGS:
            assert needle not in sent, needle
            assert needle not in ledger_text, needle

    def test_no_participant_label_reaches_the_prompt(self, fake):
        scenario, run = setup()
        client = fake(kit.success_item())
        kit.go(run)
        sent = kit.all_request_text(client.calls)
        assert "Participant A" not in sent and "Participant B" not in sent
        assert not re.search(r'participant="[AB]"', sent)

    def test_no_side_reaches_the_prompt(self, fake):
        """The claim's author is Participant A, side='pro'; the request must not say which side made the claim."""
        scenario, run = setup(side_a="pro", side_b="con")
        client = fake(kit.success_item())
        kit.go(run)
        sent = kit.all_request_text(client.calls)
        assert not re.search(r"\bpro\b", sent, re.IGNORECASE)
        assert not re.search(r"\bcon\b", sent, re.IGNORECASE)

    def test_the_claim_text_and_issue_explanation_do_reach_the_prompt(self, fake):
        """A sanity check that the blinding assertions above are not vacuously true: the call must actually carry
        the claim and the issue's own explanation, just without who said it."""
        scenario, run = setup()
        client = fake(kit.success_item())
        kit.go(run)
        sent = kit.all_request_text(client.calls)
        assert kit.CLAIM_TEXT in sent
        assert kit.ISSUE_EXPLANATION in sent


# --- Failure handling: reuse pipeline.run_moderation's exact reason codes ------------------------------------------

class TestFailures:
    def test_the_kill_switch_marks_the_run_skipped_disabled_and_posts_nothing(self, fake, settings):
        settings.LLM_ENABLED = False
        scenario, run = setup()
        before = kit.message_count(scenario.conv)
        client = fake()
        returned, stored = kit.go(run)
        assert (returned.status, stored.status, stored.failure_reason) == ("skipped_disabled", "skipped_disabled", "llm_disabled")
        assert client.calls == []
        assert kit.message_count(scenario.conv) == before
        assert stored.posted_message is None

    def test_an_over_budget_guard_state_marks_the_run_skipped_budget_with_budget_exceeded(self, fake, tune):
        """Matches pipeline.run_moderation's TestRefusals: BudgetExceeded -> ('skipped_budget', 'budget_exceeded')
        (moderation/pipeline.py's `_stop_for`); the brief says to reuse this exact reason code, not invent a new
        one for research."""
        from decimal import Decimal

        tune(BUDGET_SITE_USD_TOTAL=Decimal("0.0001"))
        scenario, run = setup()
        before = kit.message_count(scenario.conv)
        client = fake()
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("skipped_budget", "skipped_budget")
        assert stored.failure_reason == "budget_exceeded"
        assert client.calls == []
        assert kit.message_count(scenario.conv) == before
        assert stored.posted_message is None

    def test_a_tripped_breaker_marks_the_run_skipped_budget_with_reason_breaker_open(self, fake):
        from moderation import breaker

        breaker.trip("manual", "tripped by the test")
        scenario, run = setup()
        client = fake()
        _, stored = kit.go(run)
        assert (stored.status, stored.failure_reason) == ("skipped_budget", "breaker_open")
        assert client.calls == []

    def test_a_provider_error_fails_the_run_with_reason_api_error(self, fake):
        """Matches pipeline.run_moderation's TestApiErrors: LLMAPIError -> ('failed', 'api_error'), a single call,
        no retry (retries are reserved for structural/output failures, not provider errors)."""
        from moderation.fake_llm import FakeProviderError

        scenario, run = setup()
        before = kit.message_count(scenario.conv)
        client = fake(FakeProviderError(500, "api_error", "upstream exploded"))
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("failed", "failed")
        assert stored.failure_reason == "api_error"
        assert len(client.calls) == 1
        assert "upstream exploded" in stored.error
        assert kit.message_count(scenario.conv) == before
        assert stored.posted_message is None

    def test_an_unparseable_output_fails_the_run_with_reason_structural(self, fake):
        """Matches pipeline.run_moderation's TestStructuralFailure -> ('failed', 'structural') for LLMOutputError.
        Two failures scripted so this passes whichever the coding agent chose for the optional retry-once behaviour
        (docs/step20b_brief.md item 3 leaves that "if it fits cleanly"); either way the run must end up here."""
        scenario, run = setup()
        before = kit.message_count(scenario.conv)
        client = fake(kit.invalid_json, kit.invalid_json)
        returned, stored = kit.go(run)
        assert (returned.status, stored.status) == ("failed", "failed")
        assert stored.failure_reason == "structural"
        assert len(client.calls) in (1, 2)
        assert kit.message_count(scenario.conv) == before
        assert stored.posted_message is None

    def test_every_failed_or_skipped_run_leaves_the_conversation_unchanged(self, fake, settings):
        settings.LLM_ENABLED = False
        scenario, run = setup()
        fake()
        before = kit.message_count(scenario.conv)
        kit.go(run)
        assert kit.message_count(scenario.conv) == before


# --- Defensive re-checks (mirrors pipeline.run_moderation's own belt-and-suspenders checks) -------------------------

class TestDefensiveChecks:
    def test_a_run_not_pending_or_running_is_returned_unchanged_and_makes_no_call(self, fake):
        scenario, run = setup()
        from moderation.models import ModerationRun

        ModerationRun.objects.filter(pk=run.pk).update(status="failed", failure_reason="timeout")
        stale = kit.reload(run)
        client = fake()
        returned = kit.go(stale)[0]
        assert returned.status == "failed"
        assert client.calls == []
