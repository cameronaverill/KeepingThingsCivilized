"""`rate_target` failure handling: one retry on an unusable output exactly like moderation/agents.py, a `failed` rating after the
second, and refusals and API errors that propagate (docs/step14_brief.md, 14a)."""
import llmr_kit as kit
import pytest

TEXT = "Rent control always lowers rents. Anyone who disagrees is an idiot."
GOOD = kit.answer(kit.finding("f1", "abusiveness", "an idiot", 3))


def setup():
    rater = kit.make_rater("rater-under-test")
    world, message = kit.single(TEXT)
    return rater, world, message


def stored_state():
    return (
        [r.status for r in kit.ratings()],
        kit.all_findings(),
    )


class TestOneRetryOnAnUnusableOutput:
    def test_an_answer_that_does_not_fit_the_schema_is_retried_once_and_the_second_answer_is_used(self, fake):
        rater, _, message = setup()
        client = fake(kit.BAD_ANSWER, GOOD)
        rating = kit.rating_of(kit.rate(rater, message))
        assert (len(client.calls), rating.status, [f.local_id for f in kit.findings_of(rating)]) == (2, "done", ["f1"])

    def test_the_second_call_is_attempt_two_and_both_calls_are_in_the_ledger(self, fake):
        rater, _, message = setup()
        fake(kit.BAD_ANSWER, GOOD)
        kit.rate(rater, message)
        assert [(row.attempt, row.status, row.purpose, row.agent) for row in kit.ledger()] == [
            (1, "ok", "judge", "rater"), (2, "ok", "judge", "rater"),
        ]

    def test_the_first_row_records_why_it_was_unusable_and_the_rating_links_the_second(self, fake):
        rater, _, message = setup()
        fake(kit.BAD_ANSWER, GOOD)
        rating = kit.rating_of(kit.rate(rater, message))
        first, second = kit.ledger()
        assert (first.error_code, second.error_code, rating.llm_call_id) == ("invalid_output", "", second.pk)
        assert rating.llm_call == second

    def test_both_calls_are_billed(self, fake):
        rater, _, message = setup()
        fake(kit.priced(kit.BAD_ANSWER, input_tokens=500, output_tokens=40), kit.priced(GOOD, input_tokens=500, output_tokens=40))
        kit.rate(rater, message)
        rows = kit.ledger()
        assert ([row.tokens_in for row in rows], [row.cost_usd > 0 for row in rows], kit.judge_spend()) == (
            [500, 500], [True, True], sum(row.cost_usd for row in rows),
        )

    def test_invalid_json_from_the_sdk_is_retried_once_too(self, fake):
        rater, _, message = setup()
        client = fake(kit.invalid_json_answer, GOOD)
        rating = kit.rating_of(kit.rate(rater, message))
        assert (len(client.calls), rating.status) == (2, "done")

    def test_a_truncated_answer_is_retried_once(self, fake):
        from moderation.fake_llm import make_message

        rater, _, message = setup()
        client = fake(lambda kwargs: make_message(kit.answer(), stop_reason="max_tokens"), GOOD)
        rating = kit.rating_of(kit.rate(rater, message))
        assert (len(client.calls), rating.status) == (2, "done")

    def test_a_refusal_is_retried_once(self, fake):
        from moderation.fake_llm import make_message

        rater, _, message = setup()
        client = fake(lambda kwargs: make_message(None, stop_reason="refusal"), GOOD)
        rating = kit.rating_of(kit.rate(rater, message))
        assert (len(client.calls), rating.status) == (2, "done")

    def test_a_good_first_answer_is_never_retried(self, fake):
        rater, _, message = setup()
        client = fake(GOOD, GOOD)
        kit.rate(rater, message)
        assert (len(client.calls), len(client.script)) == (1, 1)


class TestASecondFailure:
    def test_two_unusable_answers_store_one_failed_rating_without_findings(self, fake):
        rater, _, message = setup()
        client = fake(kit.BAD_ANSWER, kit.BAD_ANSWER, GOOD)
        result = kit.rate(rater, message)
        rating = kit.rating_of(result)
        assert (len(client.calls), rating.status, kit.findings_of(rating), kit.rejected_of(result)) == (2, "failed", [], [])

    def test_there_is_no_third_attempt(self, fake):
        rater, _, message = setup()
        client = fake(kit.BAD_ANSWER, kit.BAD_ANSWER, GOOD)
        kit.rate(rater, message)
        assert len(client.script) == 1

    def test_both_attempts_are_in_the_ledger_and_the_rating_points_at_one_of_them(self, fake):
        rater, _, message = setup()
        fake(kit.BAD_ANSWER, kit.BAD_ANSWER)
        rating = kit.rating_of(kit.rate(rater, message))
        rows = kit.ledger()
        assert ([row.attempt for row in rows], rating.llm_call_id in {row.pk for row in rows}) == ([1, 2], True)

    def test_it_is_the_only_rating_and_it_is_finished(self, fake):
        rater, _, message = setup()
        fake(kit.BAD_ANSWER, kit.BAD_ANSWER)
        rating = kit.rating_of(kit.rate(rater, message))
        assert (kit.ratings(), rating.finished_at is not None) == ([rating], True)

    def test_a_failed_rating_still_records_the_dimensions_and_the_guideline_version(self, fake):
        rater, _, message = setup()
        fake(kit.BAD_ANSWER, kit.BAD_ANSWER)
        rating = kit.rating_of(kit.rate(rater, message, dimensions=["abusiveness"]))
        assert (rating.dimensions, "rater_v1" in rating.guideline_version) == (["abusiveness"], True)


class TestRefusalsAndErrorsPropagate:
    def refused(self, exc_type, fake, *script):
        rater, _, message = setup()
        client = fake(*script)
        with pytest.raises(exc_type):
            kit.rate(rater, message)
        return client

    def nothing_stored(self):
        statuses, findings = stored_state()
        return ("done" in statuses, findings)

    def test_the_kill_switch_raises_llm_disabled_and_no_request_is_made(self, fake, settings):
        from moderation.errors import LLMDisabled

        settings.LLM_ENABLED = False
        client = self.refused(LLMDisabled, fake, GOOD)
        assert (client.calls, [row.status for row in kit.ledger()], self.nothing_stored()) == ([], ["refused_disabled"], (False, []))

    def test_a_missing_key_raises_llm_disabled(self, fake, settings):
        from moderation.errors import LLMDisabled

        settings.ANTHROPIC_API_KEY = ""
        client = self.refused(LLMDisabled, fake, GOOD)
        assert (client.calls, self.nothing_stored()) == ([], (False, []))

    def test_an_exhausted_budget_raises_budget_exceeded_and_makes_no_request(self, fake, tune):
        from decimal import Decimal

        from moderation.errors import BudgetExceeded

        tune(BUDGET_EVAL_USD_TOTAL=Decimal("0.0001"))
        client = self.refused(BudgetExceeded, fake, GOOD)
        assert (client.calls, [row.status for row in kit.ledger()], self.nothing_stored()) == ([], ["refused_budget"], (False, []))

    def test_an_open_breaker_raises_breaker_open_and_makes_no_request(self, fake):
        from moderation import breaker
        from moderation.errors import BreakerOpen

        breaker.trip("manual", "tripped by the test")
        client = self.refused(BreakerOpen, fake, GOOD)
        assert (client.calls, [row.status for row in kit.ledger()], self.nothing_stored()) == ([], ["refused_breaker"], (False, []))

    def test_an_unreadable_ledger_raises_budget_unavailable(self, fake, monkeypatch):
        from moderation import budget
        from moderation.errors import BudgetUnavailable

        def broken(**kwargs):
            raise BudgetUnavailable("the ledger cannot be read")

        monkeypatch.setattr(budget, "check_caps", broken)
        client = self.refused(BudgetUnavailable, fake, GOOD)
        assert (client.calls, self.nothing_stored()) == ([], (False, []))

    def test_a_model_that_is_not_priced_raises_a_refusal(self, fake):
        from moderation.errors import LLMRefused

        rater = kit.make_rater("odd-model", "claude-not-in-the-price-table")
        _, message = kit.single(TEXT)
        client = fake(GOOD)
        with pytest.raises(LLMRefused):
            kit.rate(rater, message)
        assert (client.calls, self.nothing_stored()) == ([], (False, []))

    def test_a_provider_error_raises_llm_api_error_and_is_not_retried(self, fake):
        from moderation.errors import LLMAPIError
        from moderation.fake_llm import FakeProviderError

        client = self.refused(LLMAPIError, fake, FakeProviderError(500, "api_error", "boom"), GOOD)
        assert (len(client.calls), len(client.script), self.nothing_stored()) == (1, 1, (False, []))

    def test_a_refusal_is_not_retried(self, fake, settings):
        from moderation.errors import LLMDisabled

        settings.LLM_ENABLED = False
        self.refused(LLMDisabled, fake, GOOD)
        assert [row.attempt for row in kit.ledger()] == [1]
