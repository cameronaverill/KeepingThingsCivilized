"""manage.py replay: arguments, --dry-run, the switches that gate a real run (docs/step13_brief.md)."""
import ast
import re
from decimal import Decimal
from pathlib import Path

import pytest
import replay_kit as kit
from replay_kit import DUMMY_KEY

NAME = "exp-cmd"
ROOT = Path(__file__).resolve().parents[2]
EMPTY_COUNTS = {
    "Experiment": 0, "Conversation": 0, "Participant": 0, "Message": 0, "Topic": 0,
    "ModerationRun": 0, "Issue": 0, "LLMCall": 0,
}  # fmt: skip


@pytest.fixture
def folder(tmp_path):
    """A transcript folder: two pairs (8 conversations with both assignments) and one single (2 conversations)."""
    transcripts = kit.pair("alpha") + kit.pair("beta") + [kit.transcript("gamma_single", authors="ABMA")]
    return kit.write_dir(tmp_path / "transcripts", transcripts)


def replay(folder, *args, experiment=NAME):
    return kit.run_command("--experiment", experiment, "--directory", str(folder), *args)


def total_runs(exp=None):
    return len(kit.replay_runs(exp))


class TestRequiredArguments:
    def test_it_refuses_without_an_experiment_name(self, folder):
        result = kit.run_command("--directory", str(folder), "--max-usd", "1")
        assert "experiment" in result.text.lower()
        assert kit.table_counts() == EMPTY_COUNTS

    def test_it_refuses_without_max_usd_and_writes_nothing(self, folder, fake):
        client = fake()
        result = replay(folder)
        assert ("max-usd" in result.text.lower().replace("_", "-"), kit.table_counts(), client.calls) == (True, EMPTY_COUNTS, [])

    @pytest.mark.parametrize("bad", ["0", "-1", "-0.01", "abc", ""])
    def test_it_refuses_a_nonsensical_max_usd_and_writes_nothing(self, folder, bad):
        result = replay(folder, "--max-usd", bad)
        assert (result.exc is not None, kit.table_counts()) == (True, EMPTY_COUNTS)

    @pytest.mark.parametrize("bad", ["0", "-2", "abc"])
    def test_it_refuses_a_nonsensical_replicate_count_and_writes_nothing(self, folder, bad):
        result = replay(folder, "--max-usd", "1", "--replicates", bad)
        assert (result.exc is not None, kit.table_counts()) == (True, EMPTY_COUNTS)

    def test_it_refuses_an_unknown_assignment_and_writes_nothing(self, folder):
        result = replay(folder, "--max-usd", "1", "--assignments", "sideways")
        assert (result.exc is not None, kit.table_counts()) == (True, EMPTY_COUNTS)


class TestDryRun:
    def test_it_needs_no_max_usd(self, folder):
        assert replay(folder, "--dry-run").exc is None

    def test_it_writes_nothing_at_all(self, folder):
        replay(folder, "--dry-run")
        assert kit.table_counts() == EMPTY_COUNTS

    def test_it_writes_nothing_even_when_max_usd_is_given(self, folder):
        replay(folder, "--dry-run", "--max-usd", "1")
        assert kit.table_counts() == EMPTY_COUNTS

    def test_it_makes_no_api_call_and_builds_no_client(self, folder, fake, typed):
        client = fake(*kit.no_issue_script(20))
        result = replay(folder, "--dry-run", "--live")
        assert (result.exc is None, client.calls, kit.ledger()) == (True, [], [])

    def test_it_works_with_the_kill_switch_off_and_without_a_key(self, folder, settings):
        settings.LLM_ENABLED = False
        settings.ANTHROPIC_API_KEY = ""
        result = replay(folder, "--dry-run")
        assert (result.exc, kit.table_counts()) == (None, EMPTY_COUNTS)

    def test_it_prints_the_transcript_conversation_and_run_counts(self, folder):
        result = replay(folder, "--dry-run")
        numbers = kit.integers_in(result.out)
        assert (5 in numbers, 10 in numbers) == (True, True)

    def test_the_run_count_grows_with_replicates_and_assignments(self, folder):
        many = kit.integers_in(replay(folder, "--dry-run", "--replicates", "3").out)
        assert 30 in many

    def test_as_is_only_halves_the_conversation_count(self, folder):
        numbers = kit.integers_in(replay(folder, "--dry-run", "--assignments", "as-is").out)
        assert (5 in numbers, 10 in numbers) == (True, False)

    def test_it_prints_the_worst_case_cost_estimate(self, folder):
        from django.conf import settings

        from moderation import prompting
        from moderation import transcripts as transcript_files
        from moderation.management.commands.replay import usd

        master, intervenor = prompting.load_prompt("master"), prompting.load_prompt("intervenor")
        transcripts = transcript_files.load_transcripts(folder)
        expected = sum(
            (sum(transcript_files.estimate_worst_case(t, settings.MASTER_MODEL, master, intervenor)) * 2 for t in transcripts.values()),
            Decimal("0"),
        )
        assert usd(expected) in replay(folder, "--dry-run").out

    def test_the_cost_scales_with_replicates(self, folder):
        from django.conf import settings

        from moderation import prompting
        from moderation import transcripts as transcript_files
        from moderation.management.commands.replay import usd

        master, intervenor = prompting.load_prompt("master"), prompting.load_prompt("intervenor")
        transcripts = transcript_files.load_transcripts(folder)
        expected = sum(
            (sum(transcript_files.estimate_worst_case(t, settings.MASTER_MODEL, master, intervenor)) * 2 * 3 for t in transcripts.values()),
            Decimal("0"),
        )
        assert usd(expected) in replay(folder, "--dry-run", "--replicates", "3").out

    def test_the_output_is_the_same_every_time(self, folder):
        assert replay(folder, "--dry-run").out == replay(folder, "--dry-run").out

    def test_it_says_it_is_a_dry_run(self, folder):
        assert "dry run" in replay(folder, "--dry-run").out.lower()

    def test_it_lists_the_transcripts(self, folder):
        out = replay(folder, "--dry-run").out
        assert all(name in out for name in ("alpha_left", "alpha_right", "beta_left", "beta_right", "gamma_single"))


class TestSelection:
    def test_set_selects_transcripts_by_glob_on_their_ids(self, folder):
        replay(folder, "--max-usd", "1", "--set", "alpha_*")
        exp = kit.experiment(NAME)
        assert sorted({c.pair_id for c in kit.conversations(exp)}) == ["alpha"]
        assert len(kit.conversations(exp)) == 4

    def test_set_is_repeatable_and_selects_the_union(self, folder):
        replay(folder, "--max-usd", "1", "--set", "alpha_left", "--set", "gamma_*")
        exp = kit.experiment(NAME)
        assert len(kit.conversations(exp)) == 4

    def test_a_pattern_that_matches_nothing_is_refused_and_writes_nothing(self, folder):
        result = replay(folder, "--max-usd", "1", "--set", "nothing_*")
        assert (result.exc is not None, "nothing_*" in result.text, kit.table_counts()) == (True, True, EMPTY_COUNTS)

    def test_set_with_a_dry_run_counts_only_the_selected_transcripts(self, folder):
        numbers = kit.integers_in(replay(folder, "--dry-run", "--set", "alpha_*").out)
        assert (2 in numbers, 4 in numbers, 10 in numbers) == (True, True, False)

    def test_assignments_as_is_creates_one_conversation_per_transcript(self, folder):
        replay(folder, "--max-usd", "1", "--assignments", "as-is")
        assert len(kit.conversations(kit.experiment(NAME))) == 5

    def test_assignments_swapped_creates_one_conversation_per_transcript(self, folder):
        replay(folder, "--max-usd", "1", "--assignments", "swapped")
        assert len(kit.conversations(kit.experiment(NAME))) == 5

    def test_the_default_is_both_assignments(self, folder):
        replay(folder, "--max-usd", "1")
        assert len(kit.conversations(kit.experiment(NAME))) == 10

    def test_replicates_make_that_many_runs_per_conversation(self, folder):
        replay(folder, "--max-usd", "1", "--replicates", "3", "--set", "gamma_*")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert sorted(r.replicate for r in runs) == [1, 1, 2, 2, 3, 3]

    def test_a_series_member_whose_base_is_not_selected_is_still_checked_and_loaded(self, tmp_path):
        base = kit.transcript("series_base")
        member = kit.transcript(
            "flood_member", authors="ABBBB",
            series={"id": "flood", "factor": "flooding", "level": "flood", "side": "left", "base": "series_base"},
            computed={
                "trigger_message_chars": len(kit.text_of("flood_member", 5)), "trigger_message_words": len(kit.text_of("flood_member", 5).split()),
                "longest_consecutive_run": 4, "repeated_sentence_across_messages": False,
                "unanswered_question_followed_by_two_replies": False,
            },
        )  # fmt: skip
        folder = kit.write_dir(tmp_path / "series", [base, member])
        result = replay(folder, "--max-usd", "1", "--set", "flood_*")
        assert (result.exc, len(kit.conversations(kit.experiment(NAME)))) == (None, 2)


def series_member(base, tid, *, edit=None, factor="message_length"):
    """A series member derived from `base`: a copy with message 4 made longer (and, with `edit`, another change), with a
    `computed` block that is correct for its messages."""
    import copy

    from moderation.transcripts import compute_features

    member = copy.deepcopy(base)
    member["id"], member["pair_id"], member["variant"] = tid, None, None
    member["messages"][3]["text"] += " And some more rhetorical elaboration follows here."
    if edit:
        edit(member)
    member["series"] = {"id": "len", "factor": factor, "level": "long", "side": "left", "base": base["id"]}
    member["computed"] = compute_features(member["messages"], member["trigger_seq"])
    return member


class TestSeriesMembersAreCheckedAgainstTheirBase:
    def test_a_member_that_differs_from_its_base_only_in_message_four_is_accepted(self, tmp_path):
        base = kit.transcript("len_base")
        folder = kit.write_dir(tmp_path / "ok", [base, series_member(base, "len_ok")])
        assert replay(folder, "--max-usd", "1").exc is None

    def test_a_member_whose_earlier_message_differs_from_the_base_is_refused_and_writes_nothing(self, tmp_path):
        base = kit.transcript("len_base2")
        member = series_member(base, "len_bad", edit=lambda m: m["messages"][1].update(text="A different second message."))
        folder = kit.write_dir(tmp_path / "bad", [base, member])
        result = replay(folder, "--max-usd", "1")
        assert (result.exc is not None, "len_bad" in result.text, kit.table_counts()) == (True, True, EMPTY_COUNTS)

    def test_a_member_whose_base_is_not_in_the_folder_is_refused_and_writes_nothing(self, tmp_path):
        base = kit.transcript("len_base3")
        member = series_member(base, "len_orphan")
        folder = kit.write_dir(tmp_path / "orphan", [member])
        result = replay(folder, "--max-usd", "1")
        assert (result.exc is not None, "len_base3" in result.text, kit.table_counts()) == (True, True, EMPTY_COUNTS)

    def test_a_dry_run_checks_the_series_too(self, tmp_path):
        base = kit.transcript("len_base4")
        member = series_member(base, "len_orphan4")
        folder = kit.write_dir(tmp_path / "orphan_dry", [member])
        assert replay(folder, "--dry-run").exc is not None


class TestDryRunSelectionWithASeriesBase:
    def test_a_dry_run_of_a_series_member_whose_base_is_not_selected_is_accepted(self, tmp_path):
        base = kit.transcript("dry_series_base")
        member = series_member(base, "dry_series_member")
        folder = kit.write_dir(tmp_path / "dry_series", [base, member])
        result = replay(folder, "--dry-run", "--set", "dry_series_member")
        assert (result.exc, "dry_series_member" in result.out, kit.table_counts()) == (None, True, EMPTY_COUNTS)


class TestFilesAreValidatedBeforeAnyWrite:
    def test_an_invalid_folder_is_refused_before_the_confirmation_is_asked(self, folder, tune, typed):
        tune(MAX_MESSAGE_CHARS=60)
        result = replay(folder, "--max-usd", "1", "--live")
        assert (result.exc is not None, typed.prompts, kit.table_counts()) == (True, [], EMPTY_COUNTS)

    def test_an_over_limit_message_anywhere_in_the_folder_refuses_the_run_naming_the_file(self, folder, tune):
        tune(MAX_MESSAGE_CHARS=60)
        result = replay(folder, "--max-usd", "1", "--set", "alpha_*")
        assert (result.exc is not None, "alpha" in result.text, kit.table_counts()) == (True, True, EMPTY_COUNTS)

    def test_a_malformed_file_refuses_the_run_naming_the_file(self, folder):
        (folder / "broken.json").write_text("{not json", encoding="utf-8")
        result = replay(folder, "--max-usd", "1")
        assert (result.exc is not None, "broken.json" in result.text, kit.table_counts()) == (True, True, EMPTY_COUNTS)

    def test_an_empty_directory_is_refused(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = replay(empty, "--max-usd", "1")
        assert (result.exc is not None, kit.table_counts()) == (True, EMPTY_COUNTS)


class TestEvaluationBudgetLimit:
    def test_a_limit_above_the_remaining_evaluation_budget_is_refused_and_writes_nothing(self, folder, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("3", purpose="judge")
        before = kit.table_counts()
        result = replay(folder, "--max-usd", "2.01")
        assert (result.exc is not None, "budget" in result.text.lower(), kit.table_counts()) == (True, True, before)

    def test_a_limit_equal_to_the_remaining_budget_is_accepted(self, folder, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("3", purpose="replay")
        result = replay(folder, "--max-usd", "2.00")
        assert (result.exc, len(kit.conversations(kit.experiment(NAME)))) == (None, 10)

    def test_replay_spend_reduces_the_remaining_budget_as_well_as_judge_spend(self, folder, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("1", purpose="replay")
        kit.seed_spend("1", purpose="judge")
        assert replay(folder, "--max-usd", "3.01").exc is not None

    def test_other_spend_does_not_reduce_the_remaining_budget(self, folder, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("4", purpose="moderation")
        kit.seed_spend("4", purpose="spike")
        assert replay(folder, "--max-usd", "5").exc is None

    def test_the_default_budget_bounds_the_limit(self, folder, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("10"))
        assert replay(folder, "--max-usd", "10.01").exc is not None


class TestWithoutLive:
    def test_every_run_is_recorded_skipped_disabled_and_nothing_is_called(self, folder, fake):
        client = fake(*kit.no_issue_script(20))
        result = replay(folder, "--max-usd", "1")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (result.exc, client.calls, len(runs), {r.status for r in runs}) == (None, [], 10, {"skipped_disabled"})

    def test_no_money_is_spent(self, folder, fake):
        fake(*kit.no_issue_script(20))
        replay(folder, "--max-usd", "1")
        assert (kit.replay_spend(), [row for row in kit.ledger() if row.status == "ok"]) == (Decimal("0"), [])

    def test_the_kill_switch_setting_is_restored_afterwards(self, folder, settings):
        replay(folder, "--max-usd", "1")
        assert settings.LLM_ENABLED is True

    def test_the_kill_switch_stays_off_when_it_was_off(self, folder, settings):
        settings.LLM_ENABLED = False
        replay(folder, "--max-usd", "1")
        assert settings.LLM_ENABLED is False

    def test_the_tunables_module_is_left_alone(self, folder):
        from config import tunables

        before = tunables.LLM_ENABLED
        replay(folder, "--max-usd", "1")
        assert tunables.LLM_ENABLED is before

    def test_it_also_holds_when_no_client_is_installed_at_all(self, folder):
        result = replay(folder, "--max-usd", "1", "--set", "gamma_*")
        assert (result.exc, {r.status for r in kit.replay_runs()}) == (None, {"skipped_disabled"})

    def test_the_summary_counts_the_skipped_runs(self, folder):
        out = replay(folder, "--max-usd", "1").out
        assert re.search(r"skipped_disabled\D{0,5}10", out) is not None

    def test_the_factors_are_recorded_even_when_the_runs_are_skipped(self, folder):
        replay(folder, "--max-usd", "1", "--set", "gamma_*", "--assignments", "as-is")
        (run,) = kit.replay_runs()
        assert set(run.config_snapshot["factors"]) == {
            "trigger_message_chars", "trigger_message_words", "longest_consecutive_run",
            "repeated_sentence_across_messages", "unanswered_question_followed_by_two_replies",
        }  # fmt: skip


class TestLiveWithYes:
    def test_with_live_the_runs_are_executed_through_the_fake_client(self, folder, fake):
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live", "--yes")
        runs = kit.replay_runs(kit.experiment(NAME))
        assert (result.exc, len(client.calls), {r.status for r in runs}, len(runs)) == (None, 10, {"done"}, 10)

    def test_the_spend_is_recorded_under_purpose_replay_and_none_under_moderation(self, folder, fake):
        fake(*kit.no_issue_script(10, input_tokens=1000))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        assert ({row.purpose for row in kit.ledger()}, kit.replay_spend()) == ({"replay"}, Decimal("0.025000"))

    def test_the_summary_reports_the_done_runs(self, folder, fake):
        fake(*kit.no_issue_script(10))
        out = replay(folder, "--max-usd", "1", "--live", "--yes").out
        assert re.search(r"done\D{0,5}10", out) is not None

    def test_live_stops_at_max_usd(self, folder, fake):
        fake(*kit.no_issue_script(10, input_tokens=2_000_000))
        replay(folder, "--max-usd", "4.01", "--live", "--yes")
        assert len(kit.replay_runs(kit.experiment(NAME))) == 1

    def test_the_summary_says_how_many_runs_were_not_run(self, folder, fake):
        fake(*kit.no_issue_script(10, input_tokens=2_000_000))
        out = replay(folder, "--max-usd", "4.01", "--live", "--yes").out
        assert re.search(r"not run\D{0,5}9", out, re.IGNORECASE) is not None

    def test_the_kill_switch_setting_is_unchanged_afterwards_when_it_was_on(self, folder, fake, settings):
        fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        assert settings.LLM_ENABLED is True

    def test_live_turns_the_switch_on_for_this_process_only_when_the_tunable_is_off(self, folder, fake, settings):
        settings.LLM_ENABLED = False
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live", "--yes")
        assert (result.exc, len(client.calls), settings.LLM_ENABLED) == (None, 10, False)

    def test_live_leaves_the_tunables_module_alone(self, folder, fake, settings):
        from config import tunables

        settings.LLM_ENABLED = False
        fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        assert tunables.LLM_ENABLED is False

    def test_the_switch_is_restored_even_when_the_run_fails_partway(self, folder, fake, settings):
        settings.LLM_ENABLED = False
        fake(*kit.no_issue_script(2))  # too short a script: the third call fails inside the run
        with pytest.raises(AssertionError):
            replay(folder, "--max-usd", "1", "--live", "--yes")
        assert settings.LLM_ENABLED is False

    def test_live_needs_max_usd(self, folder, fake, typed):
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--live", "--yes")
        assert ("max-usd" in result.text.lower().replace("_", "-"), client.calls, kit.table_counts()) == (True, [], EMPTY_COUNTS)

    def test_live_with_no_api_key_is_refused_before_anything_is_asked_or_written(self, folder, fake, settings, typed):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live")
        assert (
            "api" in result.text.lower() and "key" in result.text.lower(),
            client.calls,
            kit.table_counts(),
            typed.prompts,
        ) == (True, [], EMPTY_COUNTS, [])

    def test_live_with_no_api_key_is_refused_even_with_yes(self, folder, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], EMPTY_COUNTS)

    def test_live_above_the_remaining_budget_is_refused_before_anything_is_asked(self, folder, fake, tune, typed):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        kit.seed_spend("3", purpose="replay")
        client = fake(*kit.no_issue_script(10))
        before = kit.table_counts()
        result = replay(folder, "--max-usd", "2.01", "--live")
        assert (result.exc is not None, client.calls, kit.table_counts(), typed.prompts) == (True, [], before, [])


class TestLiveConfirmation:
    def test_typing_yes_lets_the_run_go_ahead(self, folder, fake, typed):
        typed.reply("yes")
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live")
        assert (result.exc, len(client.calls), len(typed.prompts)) == (None, 10, 1)

    def test_yes_with_surrounding_whitespace_is_accepted(self, folder, fake, typed):
        typed.reply("  yes \n")
        fake(*kit.no_issue_script(10))
        assert replay(folder, "--max-usd", "1", "--live").exc is None

    @pytest.mark.parametrize("answer", ["no", "", "y", "n", "yes please", "YES", "Yes", "true"])
    def test_anything_but_yes_is_refused_and_nothing_is_written_or_called(self, folder, fake, typed, answer):
        typed.reply(answer)
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live")
        assert (result.exc is not None, client.calls, kit.table_counts(), kit.ledger()) == (True, [], EMPTY_COUNTS, [])

    def test_end_of_input_is_a_refusal(self, folder, fake, typed):
        typed.reply(EOFError())
        client = fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live")
        assert (result.exc is not None, client.calls, kit.table_counts()) == (True, [], EMPTY_COUNTS)

    def test_a_refusal_leaves_the_kill_switch_as_it_was(self, folder, fake, typed, settings):
        settings.LLM_ENABLED = False
        typed.reply("no")
        replay(folder, "--max-usd", "1", "--live")
        assert settings.LLM_ENABLED is False

    def test_yes_on_the_command_line_asks_nothing(self, folder, fake, typed):
        fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        assert typed.prompts == []

    def test_without_live_nothing_is_ever_asked(self, folder, typed):
        replay(folder, "--max-usd", "1")
        assert typed.prompts == []

    def test_a_dry_run_asks_nothing_even_with_live(self, folder, typed):
        replay(folder, "--dry-run", "--live")
        assert typed.prompts == []

    def test_yes_without_live_does_not_switch_anything_on(self, folder, fake):
        client = fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--yes")
        assert (client.calls, {r.status for r in kit.replay_runs()}) == ([], {"skipped_disabled"})

    def test_the_worst_case_and_the_cap_are_printed_before_the_question(self, folder, typed):
        from django.conf import settings

        from moderation import prompting
        from moderation import transcripts as transcript_files
        from moderation.management.commands.replay import usd

        typed.reply("no")
        master, intervenor = prompting.load_prompt("master"), prompting.load_prompt("intervenor")
        transcripts = transcript_files.load_transcripts(folder)
        expected = sum(
            (sum(transcript_files.estimate_worst_case(t, settings.MASTER_MODEL, master, intervenor)) * 2 for t in transcripts.values()),
            Decimal("0"),
        )
        result = replay(folder, "--max-usd", "1.25", "--live")
        assert (usd(expected) in result.out, "$1.2500" in result.out) == (True, True)

    def test_the_refusal_message_says_nothing_was_called(self, folder, typed):
        typed.reply("no")
        result = replay(folder, "--max-usd", "1", "--live")
        assert "no call" in result.text.lower()


class TestRerunningTheCommand:
    def test_a_second_identical_live_call_creates_nothing_and_calls_nothing(self, folder, fake):
        fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        before = kit.table_counts()
        client = fake()
        result = replay(folder, "--max-usd", "1", "--live", "--yes")
        assert (result.exc, kit.table_counts(), client.calls) == (None, before, [])

    def test_a_skipped_pass_followed_by_a_live_pass_ends_with_one_done_run_each(self, folder, fake):
        replay(folder, "--max-usd", "1")
        fake(*kit.no_issue_script(10))
        replay(folder, "--max-usd", "1", "--live", "--yes")
        done = [r for r in kit.replay_runs(kit.experiment(NAME)) if r.status == "done"]
        assert (len(done), len({(r.conversation_id, r.replicate) for r in done})) == (10, 10)

    def test_an_interrupted_live_call_is_finished_by_the_next_one_at_no_extra_cost(self, folder, fake):
        fake(*kit.no_issue_script(10, input_tokens=2_000_000))
        replay(folder, "--max-usd", "8.01", "--live", "--yes")
        client = fake(*kit.no_issue_script(8))
        replay(folder, "--max-usd", "50", "--live", "--yes")
        assert (len(client.calls), len(kit.replay_runs(kit.experiment(NAME))), len(kit.ledger())) == (8, 10, 10)


class TestNothingSecretIsPrinted:
    def test_the_key_never_appears_in_the_output(self, folder, fake):
        fake(*kit.no_issue_script(10))
        result = replay(folder, "--max-usd", "1", "--live", "--yes")
        assert DUMMY_KEY not in result.text

    def test_the_key_never_appears_in_a_refusal_a_dry_run_or_a_skipped_pass(self, folder, typed):
        typed.reply("no")
        texts = [replay(folder, "--max-usd", "1", "--live").text, replay(folder, "--dry-run").text, replay(folder, "--max-usd", "1").text]
        assert [DUMMY_KEY in t for t in texts] == [False, False, False]


class TestSourceHygiene:
    FILES = (ROOT / "moderation" / "replay.py", ROOT / "moderation" / "management" / "commands" / "replay.py")

    def constants(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    def imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        return names

    def test_neither_file_opens_an_env_file(self):
        env_paths = [c for path in self.FILES for c in self.constants(path) if c.strip() in (".env", "/.env") or c.endswith("/.env")]
        assert env_paths == []

    def test_neither_file_imports_dotenv_or_the_anthropic_sdk(self):
        modules = [m for path in self.FILES for m in self.imports(path)]
        assert [m for m in modules if m.split(".")[0] in ("dotenv", "anthropic")] == []

    def test_neither_file_calls_print(self):
        calls = [
            n.func.id
            for path in self.FILES
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "print" not in calls
