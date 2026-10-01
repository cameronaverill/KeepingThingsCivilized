"""run_judging: budget logic, retry, failures, callbacks and (with a path) the JSON Lines file."""
from decimal import Decimal

import judge_kit as kit
import pytest
from moderation.fake_llm import FakeProviderError


def cases(n=3):
    return [kit.make_case(conversation_id=f"range_fact_left_l{i % 3 + 1}", assignment="as-is" if i < 3 else "swapped",
                          arm=f"l{i % 3 + 1}", level=i % 3 + 1) for i in range(n)]


class TestPlainRun:
    def test_every_case_is_judged_in_order_and_the_report_counts_calls_and_cost(self, fake):
        client = fake(*(kit.priced(kit.verdict(tag=t)) for t in ("3", "1", "0")))
        report = kit.run(cases(3))
        assert ([(c.conversation_id, o.tag) for c, o in report.judgments], report.calls, len(client.calls), report.failures,
                report.stopped_reason) == (
            [("range_fact_left_l1", "3"), ("range_fact_left_l2", "1"), ("range_fact_left_l3", "0")], 3, 3, [], None)
        assert report.cost_total == kit.judge_spend() > 0

    def test_a_silent_case_between_two_others_costs_no_call(self, fake):
        client = fake(kit.priced(kit.verdict(tag="3")), kit.priced(kit.verdict(tag="1")))
        report = kit.run([kit.make_case(conversation_id="a_left_l1"), kit.silent_case(conversation_id="b_left_l1"),
                          kit.make_case(conversation_id="c_left_l1")])
        assert ([o.tag for _c, o in report.judgments], report.calls, len(client.calls)) == (["3", "0", "1"], 2, 2)

    def test_on_result_is_called_once_per_judgment(self, fake):
        fake(kit.verdict(), kit.verdict())
        seen = []
        report = kit.run(cases(2), on_result=lambda *args: seen.append(args))
        assert (len(seen), len(report.judgments)) == (2, 2)

    def test_nothing_runs_when_llm_calls_are_switched_off(self, fake, settings):
        settings.LLM_ENABLED = False
        client = fake(kit.verdict())
        report = kit.run(cases(1))
        assert (client.calls, report.judgments, report.calls, report.stopped_reason is not None) == ([], [], 0, True)

    @pytest.mark.parametrize("bad", [Decimal("-1"), Decimal("NaN")])
    def test_a_bad_limit_is_refused(self, fake, bad):
        fake()
        with pytest.raises(ValueError):
            kit.run(cases(1), max_usd=bad)


class TestBudget:
    def test_it_stops_before_a_call_whose_worst_case_passes_the_limit(self, fake):
        one = kit.reserved_for_one_call(fake, kit.make_case())
        client = fake(kit.priced(kit.verdict()), kit.priced(kit.verdict()), kit.priced(kit.verdict()))
        report = kit.run([kit.make_case(conversation_id=f"c{i}_left_l2") for i in range(3)], max_usd=one - Decimal("0.000001"))
        assert (client.calls, report.judgments, report.calls, report.stopped_reason is not None) == ([], [], 0, True)

    def test_a_limit_equal_to_one_worst_case_allows_exactly_one_call(self, fake):
        one = kit.reserved_for_one_call(fake, kit.make_case())
        client = fake(kit.priced(kit.verdict()), kit.priced(kit.verdict()), kit.priced(kit.verdict()))
        report = kit.run([kit.make_case(conversation_id=f"c{i}_left_l2") for i in range(3)], max_usd=one)
        assert (len(client.calls), [c.conversation_id for c, _ in report.judgments], report.calls,
                report.stopped_reason is not None) == (1, ["c0_left_l2"], 1, True)

    def test_the_spend_of_earlier_calls_counts_against_this_runs_limit_only_from_its_own_baseline(self, fake):
        one = kit.reserved_for_one_call(fake, kit.make_case())  # leaves one call's spend in the ledger
        client = fake(kit.priced(kit.verdict()))
        report = kit.run([kit.make_case(conversation_id="fresh_left_l2")], max_usd=one)
        assert (len(client.calls), report.stopped_reason) == (1, None)

    def test_the_stop_reason_names_the_limit(self, fake):
        fake()
        report = kit.run([kit.make_case()], max_usd=Decimal("0.000001"))
        assert "limit" in report.stopped_reason.lower()


class TestRetry:
    def test_an_unusable_answer_is_retried_once_and_then_judged(self, fake):
        client = fake(kit.BAD_VERDICT, kit.priced(kit.verdict(tag="2")))
        report = kit.run([kit.make_case()])
        assert ([o.tag for _c, o in report.judgments], len(client.calls), report.calls, report.failures) == (["2"], 2, 2, [])

    def test_two_unusable_answers_fail_the_case_and_stop_at_two_calls(self, fake):
        client = fake(kit.BAD_VERDICT, kit.BAD_VERDICT, kit.verdict())
        report = kit.run([kit.make_case()])
        assert (report.judgments, len(client.calls), len(client.script), len(report.failures)) == ([], 2, 1, 1)

    def test_a_failed_case_is_named_and_the_next_case_is_still_judged(self, fake):
        fake(kit.BAD_VERDICT, kit.BAD_VERDICT, kit.verdict(tag="3"))
        report = kit.run([kit.make_case(conversation_id="bad_left_l2"), kit.make_case(conversation_id="good_left_l2")])
        assert ([c.conversation_id for c, _ in report.judgments], report.failures[0][0]) == (["good_left_l2"], "bad_left_l2")

    def test_the_wasted_calls_are_still_paid_for_in_the_report(self, fake):
        fake(kit.priced(kit.BAD_VERDICT), kit.priced(kit.BAD_VERDICT))
        report = kit.run([kit.make_case()])
        assert report.cost_total == kit.judge_spend() > 0

    def test_a_provider_error_stops_the_run_without_a_retry(self, fake):
        client = fake(FakeProviderError(500, "api_error", "boom"), kit.verdict())
        report = kit.run([kit.make_case(conversation_id="a_left_l2"), kit.make_case(conversation_id="b_left_l2")])
        assert (report.judgments, len(client.calls), report.stopped_reason is not None) == ([], 1, True)
