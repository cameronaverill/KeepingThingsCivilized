"""manage.py run_raters: arguments, which messages are rated, --dry-run, what a run without --live does, and what --live --yes
does (docs/step14_brief.md, 14a). The guard around --live is in test_llmr_cmd_live.py."""
import re
from decimal import Decimal

import llmr_kit as kit
import pytest

PANEL = "cmd-panel"
EXPERIMENT = "cmd-exp"
SECRET = "SECRETTOKEN-zx77"
TOKENS = dict(input_tokens=1000, output_tokens=100)

# Two synthetic conversations of an experiment: the first has a moderator message between two user messages.
FIRST = [("A", f"First conversation opening line one {SECRET}."), ("mod", "A moderator reply."), ("B", "First conversation second user line.")]
SECOND = [("A", "Second conversation opening line."), ("B", "Second conversation reply line.")]


class Setup:
    """A panel of two raters and an experiment of two synthetic conversations (4 user messages, 1 moderator message)."""

    def __init__(self):
        self.raters = kit.two_raters()
        self.panel = kit.make_panel(self.raters, name=PANEL)
        self.experiment = kit.experiment(EXPERIMENT)
        topic = kit.shared_topic()
        self.first = kit.build(FIRST, topic=topic, experiment=self.experiment)
        self.second = kit.build(SECOND, topic=topic, experiment=self.experiment)
        self.user_messages = self.first.user_messages + self.second.user_messages


@pytest.fixture
def world():
    return Setup()


SOLO_TEXT = "Rent control always lowers rents. Anyone who disagrees is an idiot."


def solo_setup(targets=3):
    """One Sonnet rater and an experiment of `targets` identical one-message conversations, so every call is priced alike."""
    kit.make_panel([kit.make_rater("solo", kit.SONNET)], name="solo-panel")
    experiment = kit.experiment("solo-exp")
    topic = kit.shared_topic()
    return [kit.single(SOLO_TEXT, topic=topic, experiment=experiment)[1] for _ in range(targets)]


def solo(*args):
    return kit.run_raters("--panel", "solo-panel", "--experiment", "solo-exp", *args)


def command(*args, panel=PANEL, experiment=EXPERIMENT):
    base = ["--panel", panel]
    if experiment is not None:
        base += ["--experiment", experiment]
    return kit.run_raters(*base, *args)


def full_script(count):
    return [kit.nothing() for _ in range(count)]


class TestRequiredArguments:
    def test_a_panel_is_required(self, world):
        result = kit.run_raters("--experiment", EXPERIMENT, "--max-usd", "1")
        assert (result.exc is not None, "panel" in result.text.lower()) == (True, True)

    def test_an_unknown_panel_is_refused_and_nothing_is_written(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", panel="no-such-panel")
        assert (result.exc is not None, "no-such-panel" in result.text, kit.table_counts()) == (True, True, before)

    def test_an_unknown_panel_version_is_refused(self, world):
        result = command("--max-usd", "1", "--version", "99")
        assert result.exc is not None

    def test_something_to_rate_is_required(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", experiment=None)
        assert (result.exc is not None, kit.table_counts()) == (True, before)

    def test_an_unknown_experiment_is_refused(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", experiment="no-such-experiment")
        assert (result.exc is not None, "no-such-experiment" in result.text, kit.table_counts()) == (True, True, before)

    def test_an_unknown_conversation_id_is_refused(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", "--conversation", "99999", experiment=None)
        assert (result.exc is not None, kit.table_counts()) == (True, before)

    def test_an_experiment_and_conversations_together_are_refused(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", "--conversation", str(world.first.conv.pk))
        assert (result.exc is not None, kit.table_counts()) == (True, before)

    def test_max_usd_is_required_for_a_real_run_and_nothing_is_written(self, world, fake):
        client = fake(*full_script(8))
        before = kit.table_counts()
        result = command()
        assert ("max-usd" in result.text.lower().replace("_", "-"), client.calls, kit.table_counts()) == (True, [], before)

    @pytest.mark.parametrize("bad", ["0", "-1", "-0.01", "abc", ""])
    def test_a_nonsensical_max_usd_is_refused_and_nothing_is_written(self, world, bad):
        before = kit.table_counts()
        result = command("--max-usd", bad)
        assert (result.exc is not None, kit.table_counts()) == (True, before)

    @pytest.mark.parametrize("bad", ["0", "-2", "abc"])
    def test_a_nonsensical_replicate_count_is_refused_and_nothing_is_written(self, world, bad):
        before = kit.table_counts()
        result = command("--max-usd", "1", "--replicates", bad)
        assert (result.exc is not None, kit.table_counts()) == (True, before)

    def test_an_unknown_dimension_is_refused_and_nothing_is_written(self, world):
        before = kit.table_counts()
        result = command("--max-usd", "1", "--dimension", "not_a_dimension")
        assert (result.exc is not None, kit.table_counts()) == (True, before)


class TestWhichMessagesAreRated:
    def test_the_user_messages_of_the_experiment_are_rated_and_no_moderator_message(self, world, fake):
        client = fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        rated = sorted({r.target_id for r in kit.ratings()})
        assert (rated, len(client.calls)) == (sorted(m.pk for m in world.user_messages), 8)

    def test_every_rater_of_the_panel_rates_every_message(self, world, fake):
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        assert sorted((r.rater.name, r.target_id) for r in kit.ratings()) == sorted(
            (name, m.pk) for name in ("rater-a", "rater-b") for m in world.user_messages
        )

    def test_conversations_of_other_experiments_are_left_alone(self, world, fake):
        other = kit.build([("A", "An unrelated conversation.")], experiment=kit.experiment("other-exp"))
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        assert other[1].pk not in {r.target_id for r in kit.ratings()}

    def test_conversation_ids_select_conversations_and_may_be_repeated(self, world, fake):
        third = kit.build([("A", "Third conversation line.")])
        client = fake(*full_script(6))
        command("--max-usd", "5", "--live", "--yes", "--conversation", str(world.second.conv.pk), "--conversation", str(third.conv.pk), experiment=None)
        assert (sorted({r.target_id for r in kit.ratings()}), len(client.calls)) == (
            sorted([world.second[1].pk, world.second[2].pk, third[1].pk]), 6,
        )

    def test_a_real_users_conversation_is_refused_and_nothing_is_written_or_called(self, world, fake):
        human = kit.build([("A", "A real user wrote this.")], usernames={"A": "quokka_real_a", "B": "quokka_real_b"})
        client = fake(*full_script(4))
        before = kit.table_counts()
        result = command("--max-usd", "5", "--live", "--yes", "--conversation", str(human.conv.pk), experiment=None)
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], before)

    def test_a_real_users_conversation_is_accepted_with_allow_human_source(self, world, fake):
        human = kit.build([("A", "A real user wrote this.")], usernames={"A": "quokka_real_c", "B": "quokka_real_d"})
        client = fake(*full_script(4))
        result = command("--max-usd", "5", "--live", "--yes", "--allow-human-source", "--conversation", str(human.conv.pk), experiment=None)
        assert (result.exc, len(client.calls), {r.target_id for r in kit.ratings()}) == (None, 2, {human[1].pk})

    def test_a_real_users_conversation_inside_an_experiment_is_never_rated_without_the_flag(self, world, fake):
        human = kit.build([("A", "A real user wrote this.")], usernames={"A": "quokka_real_e", "B": "quokka_real_f"}, experiment=world.experiment)
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        assert human[1].pk not in {r.target_id for r in kit.ratings()}


class TestOptions:
    def test_dimension_restricts_the_dimensions_rated(self, world, fake):
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes", "--dimension", "abusiveness")
        assert {tuple(r.dimensions) for r in kit.ratings()} == {("abusiveness",)}

    def test_dimension_is_repeatable(self, world, fake):
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes", "--dimension", "abusiveness", "--dimension", "factual_accuracy")
        assert {tuple(sorted(r.dimensions)) for r in kit.ratings()} == {("abusiveness", "factual_accuracy")}

    def test_one_dimension_only_puts_only_that_rubric_in_the_prompt(self, world, fake):
        client = fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes", "--dimension", "abusiveness")
        assert {kit.system_text(call) for call in client.calls} == {kit.load_prompt(["abusiveness"]).text}

    def test_replicates_make_that_many_ratings_per_rater_and_message(self, world, fake):
        client = fake(*full_script(16))
        command("--max-usd", "5", "--live", "--yes", "--replicates", "2")
        assert (len(client.calls), sorted({r.replicate for r in kit.ratings()})) == (16, [1, 2])

    def test_the_default_dimensions_are_those_of_the_panel(self, world, fake):
        client = fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        assert {kit.system_text(call) for call in client.calls} == {kit.load_prompt().text}

    def test_version_picks_the_panel_of_that_version(self, world, fake):
        kit.make_panel(world.raters, name=PANEL, version="2")
        fake(*[kit.answer(kit.finding("f1", "abusiveness", "conversation", 1)) for _ in range(8)])
        command("--max-usd", "5", "--live", "--yes", "--version", "1")
        assert {c.panel_id for c in kit.consensus_rows()} == {world.panel.pk}

    def test_without_a_version_the_newest_panel_of_that_name_is_used(self, world, fake):
        newer = kit.make_panel(world.raters, name=PANEL, version="2")
        fake(*[kit.answer(kit.finding("f1", "abusiveness", "conversation", 1)) for _ in range(8)])
        command("--max-usd", "5", "--live", "--yes")
        assert {c.panel_id for c in kit.consensus_rows()} == {newer.pk}


class TestDryRun:
    def test_it_needs_no_max_usd(self, world):
        assert command("--dry-run").exc is None

    def test_it_writes_nothing_at_all(self, world):
        before = kit.table_counts()
        command("--dry-run", "--max-usd", "1")
        assert kit.table_counts() == before

    def test_it_makes_no_api_call_and_asks_nothing_even_with_live(self, world, fake, typed):
        client = fake(*full_script(8))
        result = command("--dry-run", "--live")
        assert (result.exc, client.calls, kit.ledger(), typed.prompts) == (None, [], [], [])

    def test_it_works_with_the_kill_switch_off_and_without_a_key(self, world, settings):
        settings.LLM_ENABLED = False
        settings.ANTHROPIC_API_KEY = ""
        assert command("--dry-run").exc is None

    def test_it_prints_the_message_and_rating_counts(self, world):
        numbers = kit.integers_in(command("--dry-run").out)
        assert (4 in numbers, 8 in numbers) == (True, True)

    def test_the_rating_count_grows_with_replicates(self, world):
        assert 16 in kit.integers_in(command("--dry-run", "--replicates", "2").out)

    def test_it_prints_the_worst_case_cost_of_every_call(self, fake):
        fake(kit.priced(kit.nothing(), **TOKENS))
        reserved, _ = kit.probe_costs(SOLO_TEXT, **TOKENS)
        solo_setup(3)
        out = solo("--dry-run").out
        assert any(abs(figure - 3 * reserved) <= Decimal("0.00005") for figure in kit.dollars_in(out))

    def test_the_output_is_the_same_every_time(self, world):
        assert command("--dry-run").out == command("--dry-run").out

    def test_it_says_it_is_a_dry_run(self, world):
        assert "dry run" in command("--dry-run").out.lower()

    def test_it_never_prints_a_message_text(self, world):
        assert SECRET not in command("--dry-run").text

class TestWithoutLive:
    def test_no_call_is_made_and_no_money_is_spent(self, world, fake):
        client = fake(*full_script(8))
        result = command("--max-usd", "5")
        assert (result.exc, client.calls, kit.judge_spend(), [row for row in kit.ledger() if row.status == "ok"]) == (None, [], Decimal("0"), [])

    def test_nothing_at_all_is_written(self, world, fake):
        fake(*full_script(8))
        before = kit.table_counts()
        command("--max-usd", "5")
        assert (kit.table_counts(), kit.ratings(), kit.consensus_rows()) == (before, [], [])

    def test_the_kill_switch_setting_is_restored_afterwards(self, world, settings):
        command("--max-usd", "5")
        assert settings.LLM_ENABLED is True

    def test_the_tunables_module_is_left_alone(self, world):
        from config import tunables

        before = tunables.LLM_ENABLED
        command("--max-usd", "5")
        assert tunables.LLM_ENABLED is before

    def test_it_also_holds_when_the_tunable_is_on_and_a_client_is_installed(self, world, fake, settings):
        settings.LLM_ENABLED = True
        client = fake(*full_script(8))
        command("--max-usd", "5", "--yes")
        assert client.calls == []

    def test_yes_without_live_switches_nothing_on(self, world, fake):
        client = fake(*full_script(8))
        command("--max-usd", "5", "--yes")
        assert (client.calls, kit.ratings()) == ([], [])

    def test_nothing_is_ever_asked_without_live(self, world, typed):
        command("--max-usd", "5")
        assert typed.prompts == []

    def test_the_summary_says_all_eight_were_not_run(self, world):
        out = command("--max-usd", "5").out
        assert re.search(r"8\D{0,12}not run|not run\D{0,12}8", out) is not None

    def test_it_says_that_calls_are_off(self, world):
        assert re.search(r"\boff\b", command("--max-usd", "5").out, re.IGNORECASE)


class TestLiveWithYes:
    def test_with_live_and_yes_the_ratings_go_through_the_fake_client_and_are_done(self, world, fake):
        client = fake(*full_script(8))
        result = command("--max-usd", "5", "--live", "--yes")
        assert (result.exc, len(client.calls), {r.status for r in kit.ratings()}) == (None, 8, {"done"})

    def test_the_spend_is_recorded_as_judge_and_nothing_else(self, world, fake):
        fake(*[kit.priced(kit.nothing(), **TOKENS) for _ in range(8)])
        command("--max-usd", "5", "--live", "--yes")
        assert ({row.purpose for row in kit.ledger()}, kit.judge_spend() > 0) == ({"judge"}, True)

    def test_the_summary_reports_the_done_ratings(self, world, fake):
        fake(*full_script(8))
        out = command("--max-usd", "5", "--live", "--yes").out
        assert re.search(r"8 done|done\D{0,5}8", out) is not None

    def test_the_findings_of_the_answers_are_stored_with_the_offsets_of_the_message(self, world, fake):
        target = world.second[1]
        start = target.content.index("conversation")
        fake(*[kit.answer(kit.finding("f1", "abusiveness", "conversation", 1)) for _ in range(8)])
        command("--max-usd", "5", "--live", "--yes")
        mine = [f for f in kit.all_findings() if f.rating.target_id == target.pk]
        assert sorted((f.rating.rater.name, f.start, f.end, f.quote) for f in mine) == [
            ("rater-a", start, start + 12, "conversation"), ("rater-b", start, start + 12, "conversation"),
        ]

    def test_the_consensus_is_built_for_every_message_when_both_raters_agree(self, world, fake):
        fake(*[kit.answer(kit.finding("f1", "abusiveness", "conversation", 1)) for _ in range(8)])
        command("--max-usd", "5", "--live", "--yes")
        assert (sorted(c.target_id for c in kit.consensus_rows()), {c.n_raters for c in kit.consensus_rows()}) == (
            sorted(m.pk for m in world.user_messages), {2},
        )

    def test_running_it_again_makes_no_call_and_no_rating(self, world, fake):
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        before = kit.table_counts()
        client = fake()
        result = command("--max-usd", "5", "--live", "--yes")
        assert (result.exc, client.calls, kit.table_counts()) == (None, [], before)

    def test_the_cap_stops_the_run_and_the_summary_says_how_many_were_not_run(self, fake):
        fake(*[kit.priced(kit.nothing(), **TOKENS) for _ in range(4)])
        reserved, _ = kit.probe_costs(SOLO_TEXT, **TOKENS)
        solo_setup(3)
        out = solo("--max-usd", str(reserved), "--live", "--yes").out
        assert (len(kit.ratings(status="done")) - 1, re.search(r"2\D{0,12}not run|not run\D{0,12}2", out) is not None) == (1, True)

    def test_the_kill_switch_is_on_only_inside_the_command_when_the_tunable_is_off(self, world, fake, settings):
        settings.LLM_ENABLED = False
        client = fake(*full_script(8))
        result = command("--max-usd", "5", "--live", "--yes")
        assert (result.exc, len(client.calls), settings.LLM_ENABLED) == (None, 8, False)

    def test_it_leaves_the_tunables_module_alone_when_the_tunable_is_off(self, world, fake, settings):
        from config import tunables

        settings.LLM_ENABLED = False
        fake(*full_script(8))
        command("--max-usd", "5", "--live", "--yes")
        assert tunables.LLM_ENABLED is False


class TestNeverPrintsWhatMustNotBePrinted:
    def test_a_live_run_prints_no_key_and_no_message_text(self, world, fake):
        fake(*full_script(8))
        result = command("--max-usd", "5", "--live", "--yes")
        assert (kit.DUMMY_KEY in result.text, SECRET in result.text, "Second conversation opening line" in result.text) == (False, False, False)

    def test_a_run_without_live_prints_no_key_and_no_message_text(self, world):
        result = command("--max-usd", "5")
        assert (kit.DUMMY_KEY in result.text, SECRET in result.text) == (False, False)

    def test_a_refusal_prints_no_key_and_no_message_text(self, world):
        result = command("--max-usd", "-1")
        assert (kit.DUMMY_KEY in result.text, SECRET in result.text) == (False, False)
