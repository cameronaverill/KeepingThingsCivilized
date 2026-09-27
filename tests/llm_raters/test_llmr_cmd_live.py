"""manage.py run_raters --live: exactly the guard of `manage.py replay --live` (docs/step14_brief.md, 14a and 13): --max-usd within
the remaining evaluation budget, a key present, the estimate and the cap printed, a typed `yes` or --yes, and the
process-local switch restored even on error. Modelled on tests/replay/test_replay_command.py."""
import re
from decimal import Decimal

import llmr_kit as kit
import pytest

PANEL = "live-panel"
EXPERIMENT = "live-exp"


@pytest.fixture
def world():
    """A panel of two raters and an experiment of two synthetic conversations with 4 user messages: 8 ratings."""
    raters = kit.two_raters()
    panel = kit.make_panel(raters, name=PANEL)
    experiment = kit.experiment(EXPERIMENT)
    topic = kit.shared_topic()
    kit.build([("A", "Live conversation one, first line."), ("B", "Live conversation one, second line.")], topic=topic, experiment=experiment)
    kit.build([("A", "Live conversation two, first line."), ("B", "Live conversation two, second line.")], topic=topic, experiment=experiment)
    return panel


def command(*args):
    return kit.run_raters("--panel", PANEL, "--experiment", EXPERIMENT, *args)


def script(count=8):
    return [kit.nothing() for _ in range(count)]


class TestNeededBeforeAnythingHappens:
    def test_live_needs_max_usd(self, world, fake, typed):
        client = fake(*script())
        before = kit.table_counts()
        result = command("--live", "--yes")
        assert ("max-usd" in result.text.lower().replace("_", "-"), client.calls, kit.table_counts(), typed.prompts) == (True, [], before, [])

    def test_live_with_no_api_key_is_refused_before_anything_is_asked_or_written(self, world, fake, settings, typed):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*script())
        before = kit.table_counts()
        result = command("--max-usd", "1", "--live")
        assert (
            "api" in result.text.lower() and "key" in result.text.lower(), client.calls, kit.table_counts(), typed.prompts,
        ) == (True, [], before, [])

    def test_live_with_no_api_key_is_refused_even_with_yes(self, world, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*script())
        before = kit.table_counts()
        result = command("--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], before)


class TestTheEvaluationBudgetBoundsTheLimit:
    def test_a_limit_above_the_remaining_budget_is_refused_before_anything_is_asked(self, world, fake, tune, typed):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("3", purpose="replay")
        client = fake(*script())
        before = kit.table_counts()
        result = command("--max-usd", "2.01", "--live")
        assert (result.exc is not None, "budget" in result.text.lower(), client.calls, kit.table_counts(), typed.prompts) == (
            True, True, [], before, [],
        )

    def test_a_limit_equal_to_the_remaining_budget_is_accepted(self, world, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("3", purpose="replay")
        fake(*script())
        assert command("--max-usd", "2.00", "--live", "--yes").exc is None

    def test_judge_spend_reduces_the_remaining_budget_as_well_as_replay_spend(self, world, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("1", purpose="replay")
        kit.seed_spend("1", purpose="judge")
        fake(*script())
        assert command("--max-usd", "3.01", "--live", "--yes").exc is not None

    def test_other_spend_does_not_reduce_the_remaining_budget(self, world, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("4", purpose="moderation")
        kit.seed_spend("4", purpose="spike")
        fake(*script())
        assert command("--max-usd", "5", "--live", "--yes").exc is None

    def test_the_default_budget_bounds_the_limit(self, world, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("10"))
        assert command("--max-usd", "10.01").exc is not None

    def test_the_bound_applies_without_live_too(self, world, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        before = kit.table_counts()
        result = command("--max-usd", "5.01")
        assert (result.exc is not None, kit.table_counts()) == (True, before)


class TestTypedConfirmation:
    def test_typing_yes_lets_the_run_go_ahead(self, world, fake, typed):
        typed.reply("yes")
        client = fake(*script())
        result = command("--max-usd", "1", "--live")
        assert (result.exc, len(client.calls), len(typed.prompts)) == (None, 8, 1)

    def test_yes_with_surrounding_whitespace_is_accepted(self, world, fake, typed):
        typed.reply("  yes \n")
        fake(*script())
        assert command("--max-usd", "1", "--live").exc is None

    @pytest.mark.parametrize("answer", ["no", "", "y", "n", "yes please", "YES", "Yes", "true"])
    def test_anything_but_yes_is_refused_and_nothing_is_written_or_called(self, world, fake, typed, answer):
        typed.reply(answer)
        client = fake(*script())
        before = kit.table_counts()
        result = command("--max-usd", "1", "--live")
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], before)

    def test_end_of_input_is_a_refusal(self, world, fake, typed):
        typed.reply(EOFError())
        client = fake(*script())
        before = kit.table_counts()
        result = command("--max-usd", "1", "--live")
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], before)

    def test_a_refusal_leaves_the_kill_switch_as_it_was(self, world, typed, settings):
        settings.LLM_ENABLED = False
        typed.reply("no")
        command("--max-usd", "1", "--live")
        assert settings.LLM_ENABLED is False

    def test_yes_on_the_command_line_asks_nothing(self, world, fake, typed):
        fake(*script())
        command("--max-usd", "1", "--live", "--yes")
        assert typed.prompts == []

    def test_the_estimate_and_the_cap_are_printed_before_the_question(self, world, typed):
        typed.reply("no")
        dry = command("--dry-run").out
        live = command("--max-usd", "1.25", "--live").out
        estimate = [figure for figure in kit.dollars_in(dry) if figure > 0]
        assert (estimate != [], all(figure in kit.dollars_in(live) for figure in estimate), "$1.2500" in live) == (True, True, True)

    def test_the_refusal_message_says_nothing_was_called(self, world, typed):
        typed.reply("no")
        assert "no call" in command("--max-usd", "1", "--live").text.lower()

    def test_a_dry_run_asks_nothing_even_with_live(self, world, typed):
        command("--dry-run", "--live")
        assert typed.prompts == []


class TestTheSwitchIsProcessLocal:
    def test_live_turns_the_switch_on_for_this_process_only_when_the_tunable_is_off(self, world, fake, settings):
        settings.LLM_ENABLED = False
        client = fake(*script())
        result = command("--max-usd", "1", "--live", "--yes")
        assert (result.exc, len(client.calls), settings.LLM_ENABLED) == (None, 8, False)

    def test_live_leaves_the_tunables_module_alone(self, world, fake, settings):
        from config import tunables

        settings.LLM_ENABLED = False
        fake(*script())
        command("--max-usd", "1", "--live", "--yes")
        assert tunables.LLM_ENABLED is False

    def test_the_setting_is_unchanged_afterwards_when_it_was_on(self, world, fake, settings):
        fake(*script())
        command("--max-usd", "1", "--live", "--yes")
        assert settings.LLM_ENABLED is True

    def test_the_switch_is_restored_even_when_the_run_fails_partway(self, world, fake, settings):
        settings.LLM_ENABLED = False
        fake(*script(2))  # too short a script: the third call fails inside the run
        with pytest.raises(AssertionError):
            command("--max-usd", "1", "--live", "--yes")
        assert settings.LLM_ENABLED is False

    def test_without_live_the_switch_is_forced_off_for_the_process_and_restored(self, world, fake, settings):
        client = fake(*script())
        command("--max-usd", "1")
        assert (client.calls, settings.LLM_ENABLED) == ([], True)


class TestSpendAgainstTheLimit:
    def test_the_command_stops_at_max_usd_using_the_ledger(self, fake):
        text = "Rent control always lowers rents. Anyone who disagrees is an idiot."
        tokens = dict(input_tokens=1000, output_tokens=100)
        fake(*[kit.priced(kit.nothing(), **tokens) for _ in range(4)])
        reserved, cost = kit.probe_costs(text, **tokens)
        kit.make_panel([kit.make_rater("solo", kit.SONNET)], name="solo-panel")
        experiment = kit.experiment("solo-exp")
        topic = kit.shared_topic()
        for _ in range(3):
            kit.single(text, topic=topic, experiment=experiment)
        out = kit.run_raters("--panel", "solo-panel", "--experiment", "solo-exp", "--max-usd", str(reserved + cost), "--live", "--yes").out
        assert (len(kit.ratings(status="done")) - 1, re.search(r"1\D{0,12}not run|not run\D{0,12}1", out) is not None) == (2, True)
