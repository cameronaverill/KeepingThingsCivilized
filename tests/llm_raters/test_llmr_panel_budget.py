"""`run_panel --max-usd`: it stops before the call whose worst case would take the ledger spend of this call past the cap, and
equality is allowed (docs/step14_brief.md, 14a).

Prices are learned, not assumed. A probe call rates a message with a known token usage, which gives (R, C): the worst-case
reservation a call of that shape has and what it really cost. Every target below has the same text, topic and model as the
probe's, so a call for it reserves exactly R, and the script makes it cost exactly C. The cap is then set to sums of R and C,
one microdollar apart, so that a mutation of the comparison (`>` for `>=`), of the spend (estimates instead of the ledger)
or of the baseline (spend before the call counted) each changes the number of calls."""
from decimal import Decimal

import llmr_kit as kit
import pytest

TEXT = "Rent control always lowers rents. Anyone who disagrees is an idiot."
TOKENS = dict(input_tokens=1000, output_tokens=100)
MICRO = Decimal("0.000001")


def priced_items(count):
    return [kit.priced(kit.nothing(), **TOKENS) for _ in range(count)]


def learn(fake, *, extra=0):
    """Install a FakeLLM (the probe's answer first, then `extra` more), run the probe, return (R, C, client)."""
    client = fake(*priced_items(1 + extra))
    reserved, cost = kit.probe_costs(TEXT, **TOKENS)
    return reserved, cost, client


def three_targets_and_a_panel():
    rater = kit.make_rater("solo", kit.SONNET)
    panel = kit.make_panel([rater])
    return panel, kit.identical_targets(3, TEXT)


class TestTheProbeIsSound:
    def test_the_worst_case_is_above_the_real_cost_and_both_are_positive(self, fake):
        reserved, cost, _ = learn(fake)
        assert reserved > cost > 0

    def test_the_cost_is_that_of_the_scripted_usage(self, fake):
        from moderation import pricing

        _, cost, _ = learn(fake)
        assert cost == pricing.compute_cost(kit.SONNET, input_tokens=1000, output_tokens=100)


class TestTheCapAgainstTheWorstCase:
    def test_a_cap_one_microdollar_below_one_call_runs_nothing(self, fake):
        reserved, _, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        client_calls_before = len(kit.ledger())
        report = kit.run_panel(panel, targets, max_usd=reserved - MICRO)
        assert (len(kit.ledger()) - client_calls_before, len(kit.report_not_run(report)), kit.ratings(rater=panel.raters.get())) == (0, 3, [])

    def test_a_cap_equal_to_one_call_runs_that_call_and_stops(self, fake):
        reserved, _, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=reserved)
        assert (len(kit.ledger()) - 1, len(kit.report_not_run(report)), [r.target_id for r in kit.ratings(status="done", rater=panel.raters.get())]) == (
            1, 2, [targets[0].pk],
        )

    def test_a_cap_of_cost_plus_reserve_runs_two_calls_because_the_ledger_is_used_not_the_estimates(self, fake):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=cost + reserved)
        assert (len(kit.ledger()) - 1, len(kit.report_not_run(report))) == (2, 1)

    def test_one_microdollar_less_than_that_runs_only_one(self, fake):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=cost + reserved - MICRO)
        assert (len(kit.ledger()) - 1, len(kit.report_not_run(report))) == (1, 2)

    def test_a_cap_of_two_costs_plus_reserve_runs_all_three(self, fake):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=2 * cost + reserved)
        assert (len(kit.ledger()) - 1, kit.report_not_run(report)) == (3, [])

    def test_a_generous_cap_runs_everything(self, fake):
        learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=Decimal("50"))
        assert (len(kit.ledger()) - 1, kit.report_not_run(report)) == (3, [])

    def test_the_stopped_targets_are_the_last_ones_and_are_not_rated(self, fake):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        kit.run_panel(panel, targets, max_usd=cost + reserved)
        assert [r.target_id for r in kit.ratings(rater=panel.raters.get())] == [targets[0].pk, targets[1].pk]

    def test_the_report_cost_is_what_the_ledger_says(self, fake):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=cost + reserved)
        assert (kit.report_total_cost(report), kit.judge_spend() - cost) == (2 * cost, 2 * cost)

    def test_the_cap_may_be_given_as_a_string(self, fake):
        reserved, _, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets, max_usd=str(reserved))
        assert len(kit.report_not_run(report)) == 2


class TestOnlyTheSpendOfThisCallCounts:
    def test_judge_spend_from_before_the_call_is_not_counted(self, fake):
        reserved, _, _ = learn(fake, extra=1)
        kit.seed_spend("30", purpose="judge")
        panel, targets = three_targets_and_a_panel()
        report = kit.run_panel(panel, targets[:1], max_usd=reserved)
        assert (len(kit.ledger()) - 2, kit.report_not_run(report)) == (1, [])

    def test_replay_spend_during_the_call_is_not_counted_either(self, fake):
        from moderation.fake_llm import make_message

        reserved, cost, _ = learn(fake)
        panel, targets = three_targets_and_a_panel()

        def seeds_other_spend(kwargs):
            kit.seed_spend("30", purpose="replay")
            return make_message(kit.nothing(), **TOKENS)

        fake(seeds_other_spend, *priced_items(2))
        report = kit.run_panel(panel, targets, max_usd=cost + reserved)
        assert (len(kit.report_not_run(report)), kit.judge_spend() - cost) == (1, 2 * cost)


class TestAGatewayRefusalInTheMiddle:
    def test_the_eval_budget_running_out_after_two_calls_stops_the_run_and_keeps_those_two(self, fake, tune):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        tune(BUDGET_EVAL_USD_TOTAL=3 * cost + reserved - MICRO)  # after the probe and two calls: 3C spent, a reserve of R no longer fits
        report = kit.run_panel(panel, targets, max_usd=Decimal("50"))
        assert (
            len(kit.report_not_run(report)), [r.target_id for r in kit.ratings(status="done", rater=panel.raters.get())],
            [row.status for row in kit.ledger()][-1],
        ) == (1, [targets[0].pk, targets[1].pk], "refused_budget")

    def test_at_exactly_the_eval_budget_the_third_call_is_allowed(self, fake, tune):
        reserved, cost, _ = learn(fake, extra=3)
        panel, targets = three_targets_and_a_panel()
        tune(BUDGET_EVAL_USD_TOTAL=3 * cost + reserved)
        report = kit.run_panel(panel, targets, max_usd=Decimal("50"))
        assert kit.report_not_run(report) == []
