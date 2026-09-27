"""`run_panel`: which ratings are made and in what order, resume at no cost, inactive and human raters, dimensions, the kill
switch, the report, and a stop on a refusal or a provider error (docs/step14_brief.md, 14a)."""
from decimal import Decimal

import llmr_kit as kit
import pytest

TEXT_1 = "Rent control always lowers rents. Anyone who disagrees is an idiot."
TEXT_2 = "The 1990 census counted five million people in the city."


def two_targets():
    topic = kit.shared_topic()
    first = kit.single(TEXT_1, topic=topic)[1]
    second = kit.single(TEXT_2, topic=topic)[1]
    return first, second


def full_script(count):
    return [kit.nothing() for _ in range(count)]


class TestOrder:
    def test_replicate_major_then_target_order_then_rater_name(self, fake):
        haiku_a, sonnet_b = kit.two_raters()  # created b first, then a: name order differs from creation order
        panel = kit.make_panel([sonnet_b, haiku_a])
        first, second = two_targets()
        fake(*full_script(8))
        kit.run_panel(panel, [second, first], replicates=2)  # targets deliberately not in creation order
        made = [(r.replicate, r.target_id, r.rater.name) for r in kit.ratings()]
        assert made == [
            (1, second.pk, "rater-a"), (1, second.pk, "rater-b"), (1, first.pk, "rater-a"), (1, first.pk, "rater-b"),
            (2, second.pk, "rater-a"), (2, second.pk, "rater-b"), (2, first.pk, "rater-a"), (2, first.pk, "rater-b"),
        ]

    def test_the_calls_go_out_in_the_same_order(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([sonnet_b, haiku_a])
        first, second = two_targets()
        fake(*full_script(4))
        kit.run_panel(panel, [second, first])
        assert [(row.conversation_id, row.model) for row in kit.ledger()] == [
            (second.conversation_id, kit.HAIKU), (second.conversation_id, kit.SONNET),
            (first.conversation_id, kit.HAIKU), (first.conversation_id, kit.SONNET),
        ]

    def test_the_order_does_not_depend_on_how_the_raters_were_added_to_the_panel(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, _ = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first])
        assert [row.model for row in kit.ledger()] == [kit.HAIKU, kit.SONNET]

    def test_on_result_is_called_once_after_each_call(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, second = two_targets()
        client = fake(*full_script(4))
        seen = []
        kit.run_panel(panel, [first, second], on_result=lambda result: seen.append(len(client.calls)))
        assert seen == [1, 2, 3, 4]


class TestWhoIsCalled:
    def test_an_inactive_llm_rater_and_a_human_rater_are_not_called(self, fake):
        active = kit.make_rater("active-one", kit.HAIKU)
        retired = kit.make_rater("retired-one", kit.SONNET, active=False)
        human = kit.make_human("human-one")
        panel = kit.make_panel([active, retired, human])
        first, _ = two_targets()
        client = fake(*full_script(3))
        kit.run_panel(panel, [first])
        assert ([call["model"] for call in client.calls], [r.rater.name for r in kit.ratings()]) == ([kit.HAIKU], ["active-one"])

    def test_a_rater_outside_the_panel_is_not_called(self, fake):
        member = kit.make_rater("member", kit.HAIKU)
        kit.make_rater("outsider", kit.SONNET)
        panel = kit.make_panel([member])
        first, _ = two_targets()
        client = fake(*full_script(3))
        kit.run_panel(panel, [first])
        assert [call["model"] for call in client.calls] == [kit.HAIKU]

    def test_no_targets_means_no_calls_and_an_empty_report(self, fake):
        panel = kit.make_panel(kit.two_raters())
        client = fake()
        report = kit.run_panel(panel, [])
        assert (client.calls, kit.ratings(), kit.report_total_cost(report), kit.report_not_run(report)) == ([], [], Decimal("0"), [])


class TestDimensions:
    def test_the_default_dimensions_are_those_of_the_panel(self, fake):
        rater = kit.make_rater("solo")
        panel = kit.make_panel([rater], dimensions=["abusiveness"])
        first, _ = two_targets()
        client = fake(kit.nothing())
        kit.run_panel(panel, [first])
        assert (kit.ratings()[0].dimensions, kit.system_text(client.calls[0]) == kit.load_prompt(["abusiveness"]).text) == (["abusiveness"], True)

    def test_an_explicit_dimension_list_overrides_the_panels(self, fake):
        rater = kit.make_rater("solo")
        panel = kit.make_panel([rater])
        first, _ = two_targets()
        client = fake(kit.nothing())
        kit.run_panel(panel, [first], dimensions=["factual_accuracy"])
        assert (kit.ratings()[0].dimensions, kit.system_text(client.calls[0]) == kit.load_prompt(["factual_accuracy"]).text) == (
            ["factual_accuracy"], True,
        )

    def test_the_prompt_fingerprint_reaches_every_ledger_row_and_rating(self, fake):
        panel = kit.make_panel(kit.two_raters())
        first, _ = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first])
        assert ({row.prompt_version for row in kit.ledger()}, len({row.prompt_sha256 for row in kit.ledger()})) == ({"rater_v1"}, 1)
        assert all("rater_v1" in r.guideline_version and "abusiveness_v1" in r.guideline_version for r in kit.ratings())


class TestResumeAtNoCost:
    def test_a_second_identical_call_makes_no_request_and_no_new_rating(self, fake):
        panel = kit.make_panel(kit.two_raters())
        first, _ = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first])
        before = (kit.table_counts(), [r.pk for r in kit.ratings()])
        client = fake()  # any call now raises: the script is empty
        report = kit.run_panel(panel, [first])
        assert (client.calls, (kit.table_counts(), [r.pk for r in kit.ratings()]) == before, kit.report_counts(report)["skipped_existing"]) == ([], True, 2)

    def test_the_second_call_costs_nothing(self, fake):
        panel = kit.make_panel(kit.two_raters())
        first, _ = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first])
        spent = kit.judge_spend()
        fake()
        report = kit.run_panel(panel, [first])
        assert (kit.judge_spend(), kit.report_total_cost(report)) == (spent, Decimal("0"))

    def test_only_the_missing_raters_are_called(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, _ = two_targets()
        kit.make_done_rating(haiku_a, first)
        client = fake(kit.nothing())
        kit.run_panel(panel, [first])
        assert [call["model"] for call in client.calls] == [kit.SONNET]

    def test_only_the_missing_targets_are_called(self, fake):
        rater = kit.make_rater("solo", kit.HAIKU)
        panel = kit.make_panel([rater])
        first, second = two_targets()
        kit.make_done_rating(rater, first)
        client = fake(kit.nothing())
        kit.run_panel(panel, [first, second])
        assert [r.target_id for r in kit.ratings(status="done")] == [first.pk, second.pk]
        assert len(client.calls) == 1

    def test_a_new_replicate_number_is_a_new_rating(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, _ = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first], replicates=1)
        client = fake(*full_script(2))
        kit.run_panel(panel, [first], replicates=2)
        assert (len(client.calls), sorted((r.replicate, r.rater.name) for r in kit.ratings())) == (
            2, [(1, "rater-a"), (1, "rater-b"), (2, "rater-a"), (2, "rater-b")],
        )

    def test_a_failed_rating_is_tried_again_and_a_pending_one_is_not_a_reason_to_skip(self, fake):
        rater = kit.make_rater("solo", kit.HAIKU)
        panel = kit.make_panel([rater])
        first, _ = two_targets()
        kit.make_done_rating(rater, first, status="failed")
        kit.make_done_rating(rater, first, status="pending")
        client = fake(kit.nothing())
        kit.run_panel(panel, [first])
        assert (len(client.calls), [r.status for r in kit.ratings()].count("done")) == (1, 1)

    def test_a_run_stopped_by_its_cap_is_finished_by_running_again_with_more_room(self, fake):
        from decimal import Decimal as D

        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        fake(*full_script(4))
        kit.run_panel(panel, [first, second], max_usd=D("0.0001"))  # stops at once
        assert kit.ratings() == []
        fake(*full_script(4))
        kit.run_panel(panel, [first, second])
        assert len(kit.ratings(status="done")) == 4


class TestTheKillSwitch:
    """Nothing is written at all: no rating, no finding, no ledger row. Each triple is reported as not run, `llm_disabled`."""

    def switched_off(self, fake, settings, *, key=True, enabled=False):
        settings.LLM_ENABLED = enabled
        settings.ANTHROPIC_API_KEY = kit.DUMMY_KEY if key else ""
        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        client = fake(*full_script(4))
        before = kit.table_counts()
        report = kit.run_panel(panel, [first, second])
        return client, report, (first, second), panel, before

    def test_nothing_is_sent_and_nothing_is_spent(self, fake, settings):
        client, report, *_ = self.switched_off(fake, settings)
        assert (client.calls, kit.judge_spend(), kit.ledger(), kit.report_total_cost(report)) == ([], Decimal("0"), [], Decimal("0"))

    def test_nothing_is_written_to_the_database(self, fake, settings):
        *_, before = self.switched_off(fake, settings)
        assert kit.table_counts() == before

    def test_in_particular_no_failed_rating_is_stored(self, fake, settings):
        self.switched_off(fake, settings)
        assert (kit.ratings(), kit.all_findings(), kit.consensus_rows()) == ([], [], [])

    def test_every_triple_is_reported_not_run_with_the_reason_llm_disabled(self, fake, settings):
        _, report, *_ = self.switched_off(fake, settings)
        assert (kit.report_not_run_reasons(report), kit.report_counts(report)["done"], kit.report_counts(report)["failed"]) == (
            ["llm_disabled"] * 4, 0, 0,
        )

    def test_the_not_run_entries_say_which_rater_target_and_replicate(self, fake, settings):
        _, report, (first, second), _, _ = self.switched_off(fake, settings)
        assert [(e["rater"], e["target_id"], e["replicate"]) for e in kit.report_not_run(report)] == [
            ("rater-a", first.pk, 1), ("rater-b", first.pk, 1), ("rater-a", second.pk, 1), ("rater-b", second.pk, 1),
        ]

    def test_a_missing_key_is_the_same(self, fake, settings):
        client, report, _, _, before = self.switched_off(fake, settings, key=False, enabled=True)
        assert (client.calls, kit.table_counts(), kit.report_not_run_reasons(report)) == ([], before, ["llm_disabled"] * 4)

    def test_what_was_already_done_is_still_skipped_when_the_calls_are_off(self, fake, settings):
        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        fake(*full_script(2))
        kit.run_panel(panel, [first])
        settings.LLM_ENABLED = False
        report = kit.run_panel(panel, [first, second])
        assert (kit.report_counts(report)["skipped_existing"], kit.report_not_run_reasons(report)) == (2, ["llm_disabled"] * 2)

    def test_switching_the_calls_on_afterwards_rates_those_targets_normally(self, fake, settings):
        _, _, (first, second), panel, _ = self.switched_off(fake, settings)
        settings.LLM_ENABLED = True
        client = fake(*full_script(4))
        kit.run_panel(panel, [first, second])
        assert (len(client.calls), len(kit.ratings(status="done")), len(kit.ratings())) == (4, 4, 4)

    def test_a_gateway_that_raises_llm_disabled_in_the_middle_ends_the_run_the_same_way(self, fake, monkeypatch):
        from moderation import llm
        from moderation.errors import LLMDisabled

        real_call, seen = llm.call, []

        def call_once_then_refuse(**kwargs):
            seen.append(kwargs["model"])
            if len(seen) > 1:
                raise LLMDisabled("switched off in the middle")
            return real_call(**kwargs)

        monkeypatch.setattr(llm, "call", call_once_then_refuse)
        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        fake(*full_script(4))
        report = kit.run_panel(panel, [first, second])
        assert (
            len(seen), [r.status for r in kit.ratings()], kit.report_not_run_reasons(report),
        ) == (2, ["done"], ["llm_disabled"] * 3)


class TestTheReport:
    def test_the_counts_the_cost_and_the_per_rater_cost(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, second = two_targets()
        fake(*[kit.priced(kit.nothing(), input_tokens=1000, output_tokens=100) for _ in range(4)])
        report = kit.run_panel(panel, [first, second])
        by_model = {model: sum(row.cost_usd for row in kit.ledger() if row.model == model) for model in (kit.HAIKU, kit.SONNET)}
        assert (
            kit.report_counts(report)["done"], kit.report_total_cost(report), kit.report_cost_by_rater(report),
        ) == (4, kit.judge_spend(), {"rater-a": by_model[kit.HAIKU], "rater-b": by_model[kit.SONNET]})

    def test_the_two_models_really_cost_different_amounts(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, _ = two_targets()
        fake(*[kit.priced(kit.nothing(), input_tokens=1000, output_tokens=100) for _ in range(2)])
        report = kit.run_panel(panel, [first])
        costs = kit.report_cost_by_rater(report)
        assert costs["rater-a"] < costs["rater-b"]

    def test_dropped_findings_are_counted_in_the_report(self, fake):
        haiku_a, sonnet_b = kit.two_raters()
        panel = kit.make_panel([haiku_a, sonnet_b])
        first, _ = two_targets()
        good = kit.finding("g", "abusiveness", "an idiot", 3)
        fake(
            kit.answer(good, kit.finding("b1", "factual_accuracy", "not in the text", 2)),
            kit.answer(good, kit.finding("b2", "fallacy", "an idiot", 2), kit.finding("b3", "abusiveness", "also absent", 1)),
        )
        report = kit.run_panel(panel, [first])
        assert (kit.report_rejected(report), len(kit.all_findings())) == (3, 2)

    def test_the_summary_is_printable_text(self, fake):
        panel = kit.make_panel(kit.two_raters())
        first, _ = two_targets()
        fake(*full_script(2))
        assert isinstance(kit.report_text(kit.run_panel(panel, [first])), str)


class TestAStopThatIsNotAnError:
    """A guard refusal or a provider error ends the run cleanly: nothing is raised, what is left is `not_run`."""

    def test_a_budget_refusal_from_the_gateway_stops_the_run_and_reports_the_rest(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("0.0001"))
        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        client = fake(*full_script(4))
        report = kit.run_panel(panel, [first, second])
        assert (client.calls, len(kit.report_not_run(report)), kit.ratings(status="done")) == ([], 4, [])

    def test_a_breaker_that_trips_stops_the_run_and_reports_the_rest(self, fake):
        from moderation import breaker

        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        def trips(kwargs):
            from moderation.fake_llm import make_message

            breaker.trip("manual", "tripped mid-run")
            return make_message(kit.nothing())

        client = fake(trips, *full_script(3))
        report = kit.run_panel(panel, [first, second])
        assert (len(client.calls), len(kit.ratings(status="done")), len(kit.report_not_run(report))) == (1, 1, 3)

    def test_a_provider_error_stops_the_run_without_raising(self, fake):
        from moderation.fake_llm import FakeProviderError

        panel = kit.make_panel(kit.two_raters())
        first, second = two_targets()
        client = fake(kit.nothing(), FakeProviderError(500, "api_error", "boom"), *full_script(3))
        report = kit.run_panel(panel, [first, second])
        assert (len(client.calls), len(kit.ratings(status="done")), len(kit.report_not_run(report))) == (2, 1, 3)
