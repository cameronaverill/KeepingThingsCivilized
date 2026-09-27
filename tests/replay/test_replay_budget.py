"""Spending: the ledger under purpose="replay", --max-usd stopping, the evaluation budget (docs/step13_brief.md)."""
import re
from decimal import Decimal

import pytest
import replay_kit as kit
from django.core.management.base import CommandError

NAME = "exp-budget"
# Two million input tokens on claude-sonnet-5 (2 USD per million) plus 50 output tokens: 4.0005 USD per scripted call.
BIG = 2_000_000
BIG_COST = Decimal("4.000500")


def prepared(transcripts, *, assignments="both", replicates=1):
    experiment_plan = kit.load(NAME, transcripts, assignments=assignments, replicates=replicates)
    return kit.plan(experiment_plan, replicates=replicates)


def worst_case(data):
    """The spike's worst-case cost of one run of this transcript, on the configured Master model (what the stop compares)."""
    from django.conf import settings

    from moderation import prompting
    from moderation.management.commands import spike

    master, intervenor = prompting.load_prompt("master"), prompting.load_prompt("intervenor")
    return sum(spike.estimate_worst_case(data, settings.MASTER_MODEL, master, intervenor))


class TestLedgerPurpose:
    def test_every_call_of_a_replay_is_recorded_with_purpose_replay(self, fake):
        specs = prepared(kit.pair("purpose"))
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        assert ({row.purpose for row in kit.ledger()}, len(kit.ledger())) == ({"replay"}, 4)

    def test_no_replay_call_is_counted_as_moderation_spend(self, fake):
        from moderation import budget

        specs = prepared(kit.pair("notmod"))
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        assert (budget.spend(purposes=("moderation",)), budget.spend(purposes=("replay",)) > 0) == (Decimal("0"), True)

    def test_each_ledger_row_points_at_its_replay_run_and_conversation(self, fake):
        specs = prepared(kit.pair("linked"))
        fake(*kit.no_issue_script(len(specs)))
        kit.execute(specs, max_usd=50)
        runs = kit.replay_runs(kit.experiment(NAME))
        assert [(row.run_id, row.conversation_id) for row in kit.ledger()] == [(r.pk, r.conversation_id) for r in runs]

    def test_the_intervenor_call_of_an_intervening_replay_is_a_replay_call_too(self, fake):
        specs = prepared([kit.transcript("twocalls")], assignments="as-is")
        fake(kit.issue_master_answer(specs[0], "stated plainly and civilly"), kit.intervene_answer(specs[0]))
        kit.execute(specs, max_usd=50)
        assert [(row.agent, row.purpose) for row in kit.ledger()] == [("master", "replay"), ("intervenor", "replay")]

    def test_replays_are_not_stopped_by_the_site_caps(self, fake, tune):
        tune(
            BUDGET_SITE_USD_TOTAL=Decimal("0.000001"),
            BUDGET_SITE_USD_PER_DAY=Decimal("0.000001"),
            BUDGET_PER_CONVERSATION_USD=Decimal("0.000001"),
        )
        specs = prepared(kit.pair("sitecaps"))
        fake(*kit.no_issue_script(len(specs)))
        report = kit.execute(specs, max_usd=50)
        assert kit.report_counts(report) == {"done": 4}

    def test_replay_spend_is_not_counted_against_the_site_caps(self, fake):
        from moderation import budget

        specs = prepared(kit.pair("sitecount"), assignments="as-is")
        fake(*kit.no_issue_script(len(specs), input_tokens=BIG))
        kit.execute(specs, max_usd=50)
        assert budget.spend(purposes=budget.SITE_PURPOSES) == Decimal("0")


class TestReportedCost:
    def test_the_report_total_is_the_ledger_sum(self, fake):
        specs = prepared(kit.pair("total"))
        fake(*kit.no_issue_script(len(specs), input_tokens=1000))
        report = kit.execute(specs, max_usd=50)
        assert (kit.report_total_cost(report), kit.report_total_cost(report) > 0) == (kit.replay_spend(), True)

    def test_each_run_costs_what_the_scripted_usage_prices_at(self, fake):
        specs = prepared([kit.transcript("price")], assignments="as-is")
        fake(*kit.no_issue_script(1, input_tokens=1000))
        report = kit.execute(specs, max_usd=50)
        assert kit.report_total_cost(report) == Decimal("0.002500")  # 1000 x 2 + 50 x 10 per million

    def test_the_cost_by_transcript_adds_up_to_the_total_and_names_every_transcript_conversation(self, fake):
        specs = prepared(kit.pair("bytrans"), replicates=2)
        fake(*kit.no_issue_script(len(specs), input_tokens=1000))
        report = kit.execute(specs, max_usd=50)
        by_transcript = kit.report_cost_by_transcript(report)
        assert (len(by_transcript), sum(by_transcript.values()), set(by_transcript.values())) == (
            4,
            kit.report_total_cost(report),
            {Decimal("0.005000")},
        )

    def test_the_summary_prints_the_counts_and_the_total(self, fake):
        specs = prepared(kit.pair("summary"))
        fake(*kit.no_issue_script(len(specs), input_tokens=1000))
        text = kit.report_text(kit.execute(specs, max_usd=50))
        assert (re.search(r"done\D{0,5}4", text) is not None, re.search(r"\$0\.01\d*", text) is not None) == (True, True)


class TestMaxUsdStops:
    def test_a_limit_below_any_run_runs_nothing_and_calls_nothing(self, fake):
        specs = prepared(kit.pair("tiny"))
        client = fake(*kit.no_issue_script(len(specs)))
        report = kit.execute(specs, max_usd="0.000001")
        assert (client.calls, kit.ledger(), len(kit.report_not_run(report)), kit.report_counts(report)) == ([], [], 4, {})

    def test_not_run_lists_every_spec_in_plan_order(self, fake):
        specs = prepared(kit.pair("notrunorder"))
        fake()
        report = kit.execute(specs, max_usd="0.000001")
        assert [kit.spec_key(s) for s in kit.report_not_run(report)] == [kit.spec_key(s) for s in specs]

    def test_a_limit_with_room_runs_everything_and_reports_nothing_left(self, fake):
        specs = prepared(kit.pair("room"))
        fake(*kit.no_issue_script(len(specs)))
        report = kit.execute(specs, max_usd=50)
        assert (kit.report_counts(report), kit.report_not_run(report)) == ({"done": 4}, [])

    def test_the_stop_uses_the_ledger_not_the_estimate(self, fake):
        # Each scripted call really costs 4.0005 USD, far above the (small) worst-case estimate of a run. The limit leaves
        # room for the first run only; an estimate-only stop would let all four through.
        specs = prepared(kit.pair("ledgerstop"))
        client = fake(*kit.no_issue_script(len(specs), input_tokens=BIG))
        report = kit.execute(specs, max_usd="4.01")
        assert (len(client.calls), kit.report_counts(report), len(kit.report_not_run(report))) == (1, {"done": 1}, 3)

    def test_a_limit_exactly_equal_to_the_worst_case_of_the_next_run_lets_it_run(self, fake):
        data = kit.transcript("boundary_eq")
        specs = prepared([data], assignments="as-is")
        fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd=worst_case(data))
        assert kit.report_counts(report) == {"done": 1}

    def test_a_limit_one_micro_dollar_below_the_worst_case_of_the_next_run_does_not(self, fake):
        data = kit.transcript("boundary_lt")
        specs = prepared([data], assignments="as-is")
        client = fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd=worst_case(data) - Decimal("0.000001"))
        assert (client.calls, len(kit.report_not_run(report))) == ([], 1)

    def test_the_specs_left_over_are_the_later_ones(self, fake):
        specs = prepared(kit.pair("laterones"))
        fake(*kit.no_issue_script(len(specs), input_tokens=BIG))
        report = kit.execute(specs, max_usd="4.01")
        assert [kit.spec_key(s) for s in kit.report_not_run(report)] == [kit.spec_key(s) for s in specs[1:]]

    def test_the_call_stops_after_the_run_that_leaves_no_room_for_another(self, fake):
        specs = prepared(kit.pair("overshoot"))
        fake(*kit.no_issue_script(len(specs), input_tokens=BIG))
        report = kit.execute(specs, max_usd="8.01")
        assert (kit.report_total_cost(report), len(kit.report_not_run(report))) == (2 * BIG_COST, 2)

    def test_the_limit_counts_only_this_calls_spend(self, fake):
        kit.seed_spend("7.00", purpose="replay")
        specs = prepared([kit.transcript("thiscall")], assignments="as-is")
        fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd="1")
        assert kit.report_counts(report) == {"done": 1}

    def test_a_stopped_call_can_be_resumed_and_finishes_each_run_exactly_once(self, fake):
        specs = prepared(kit.pair("resumestop"))
        fake(*kit.no_issue_script(len(specs), input_tokens=BIG))
        kit.execute(specs, max_usd="4.01")
        client = fake(*kit.no_issue_script(3, input_tokens=BIG))
        kit.execute(specs, max_usd="50")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (len(client.calls), sorted(r.status for r in runs), len(kit.ledger())) == (3, ["done"] * 4, 4)

    def test_a_negative_limit_is_refused(self, fake):
        specs = prepared([kit.transcript("negative")])
        client = fake()
        with pytest.raises((ValueError, CommandError)):
            kit.execute(specs, max_usd="-1")
        assert (client.calls, kit.ledger()) == ([], [])


class TestEvaluationBudget:
    def test_a_spent_evaluation_budget_makes_the_runs_skipped_budget_without_a_call(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("0.0005"))
        specs = prepared(kit.pair("evalcap"))
        client = fake(*kit.no_issue_script(len(specs)))
        report = kit.execute(specs, max_usd="0.4")
        statuses = kit.report_counts(report)
        assert (client.calls, set(statuses), sum(statuses.values()) + len(kit.report_not_run(report))) == ([], {"skipped_budget"}, 4)

    def test_earlier_replay_and_judge_spend_counts_against_the_evaluation_budget(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("9"))
        kit.seed_spend("8.99", purpose="judge")
        specs = prepared([kit.transcript("judgespend")], assignments="as-is")
        client = fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd="0.5")
        assert (client.calls, set(kit.report_counts(report))) == ([], {"skipped_budget"})

    def test_moderation_spend_does_not_reduce_the_evaluation_budget(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("9"))
        kit.seed_spend("8.99", purpose="moderation")
        specs = prepared([kit.transcript("modspend")], assignments="as-is")
        fake(*kit.no_issue_script(1))
        report = kit.execute(specs, max_usd="0.5")
        assert kit.report_counts(report) == {"done": 1}
