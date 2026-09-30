"""manage.py generate_conversations: arguments, --dry-run, what a run without --live does, and the --live guard."""
import json
import re
from decimal import Decimal

import gen_kit as kit
import pytest

LAW = "federal_agents_authority"
LAW2 = "noncitizen_criminal_law"
SECRET_TEXT = "zebrafishquartz"


def command(*args):
    return kit.generate_conversations(*args)


def script(pairs=1):
    """Answers for `pairs` facts: a left and a right base each, with a distinctive word in the first message."""
    out = []
    for _ in range(pairs):
        for side in ("left", "right"):
            payload = kit.answer(side=side)
            payload["messages"][0]["text"] += f" {SECRET_TEXT}"
            out.append(payload)
    return out


def counts():
    from moderation.models import LLMCall

    return LLMCall.objects.count()


def worst_case_of(fact_ids):
    from seeding import generate
    from seeding.facts import load_facts

    facts = [f for f in load_facts() if f.id in fact_ids]
    return sum((i.worst_case_usd for i in generate.plan_generation(facts)), Decimal("0"))


def dollars(value):
    return Decimal(f"{value:.4f}")


class TestArguments:
    def test_max_usd_is_required_without_dry_run(self, fake):
        client = fake(*script())
        result = command("--facts", LAW)
        assert ("max-usd" in result.text.lower().replace("_", "-"), result.exc is not None, client.calls, kit.files_under(".")) == (True, True, [], [])

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan"])
    def test_a_bad_max_usd_is_refused(self, fake, bad):
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", bad)
        assert (result.exc is not None, client.calls, kit.files_under(".")) == (True, [], [])

    def test_an_unknown_fact_id_is_refused(self, fake):
        client = fake(*script())
        result = command("--facts", "no_such_fact", "--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, "no_such_fact" in result.text, client.calls, kit.files_under(".")) == (True, True, [], [])

    def test_the_command_exists(self):
        result = command("--dry-run")
        assert result.exc is None


class TestDryRun:
    def test_it_writes_nothing_and_calls_nothing(self, fake):
        client = fake(*script(12))
        result = command("--dry-run")
        assert (result.exc, client.calls, counts(), kit.files_under(".")) == (None, [], 0, [])

    def test_it_says_it_is_a_dry_run(self, fake):
        fake()
        assert "dry run" in command("--dry-run").text.lower()

    def test_it_prints_the_worst_case_of_all_twelve_facts(self, fake):
        from seeding.facts import load_facts

        fake()
        result = command("--dry-run")
        ids = [f.id for f in load_facts() if f.ready()]
        assert dollars(worst_case_of(ids)) in kit.dollars_in(result.text)

    def test_it_prints_twelve_facts_and_twenty_four_calls(self, fake):
        fake()
        text = command("--dry-run").text
        numbers = re.findall(r"(?<![\d.$])\d+(?!\d|\.\d)", text)
        assert ("12" in numbers, "24" in numbers) == (True, True)

    def test_it_prints_the_budget_left(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("77"))
        fake()
        assert Decimal("77.0000") in kit.dollars_in(command("--dry-run").text)

    def test_the_budget_left_falls_by_earlier_replay_spend(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("77"))
        fake()
        from decimal import Decimal as D

        from moderation.models import LLMCall

        LLMCall.objects.create(
            purpose="replay", agent="x", attempt=1, model="claude-sonnet-5", prompt_version="seed", prompt_sha256="0" * 64,
            temperature=None, max_tokens=1, request={}, raw_response="", parsed=None, tokens_in=0, tokens_out=0,
            cache_write_tokens=0, cache_read_tokens=0, reserved_usd=D("7"), cost_usd=D("7"), latency_ms=0,
            stop_reason="", provider_request_id="", status="ok", error="", error_code="",
        )
        assert Decimal("70.0000") in kit.dollars_in(command("--dry-run").text)

    def test_facts_limits_the_plan(self, fake):
        fake()
        text = command("--dry-run", "--facts", f"{LAW},{LAW2}").text
        assert dollars(worst_case_of([LAW, LAW2])) in kit.dollars_in(text)

    def test_a_single_fact_costs_less_than_two(self):
        assert worst_case_of([LAW]) < worst_case_of([LAW, LAW2])

    def test_max_usd_below_the_worst_case_gives_a_warning(self, fake):
        fake()
        text = command("--dry-run", "--max-usd", "0.01").text
        assert "warning" in text.lower()

    def test_it_accepts_max_usd_and_still_writes_nothing(self, fake):
        client = fake(*script(12))
        result = command("--dry-run", "--max-usd", "5")
        assert (result.exc, client.calls, kit.files_under(".")) == (None, [], [])

    def test_it_ignores_live(self, fake, typed):
        client = fake(*script(12))
        result = command("--dry-run", "--live")
        assert (result.exc, client.calls, typed.prompts, kit.files_under(".")) == (None, [], [], [])

    def test_it_never_prints_the_key(self, fake):
        fake()
        assert kit.DUMMY_KEY not in command("--dry-run").text


class TestWithoutLive:
    def test_no_call_is_made_and_nothing_is_written(self, fake):
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5")
        assert (client.calls, counts(), kit.files_under(".")) == ([], 0, [])
        assert result.exc is None

    def test_it_says_calls_are_off(self, fake):
        fake(*script())
        assert "off" in command("--facts", LAW, "--max-usd", "5").text.lower()

    def test_it_asks_nothing(self, fake, typed):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5")
        assert typed.prompts == []

    def test_the_kill_switch_is_restored_afterwards(self, fake, settings):
        from django.conf import settings as live_settings

        fake(*script())
        command("--facts", LAW, "--max-usd", "5")
        assert live_settings.LLM_ENABLED is True

    def test_saved_bases_are_still_turned_into_transcripts_without_a_call(self, fake, tmp_path):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        client = fake()
        result = command("--facts", LAW, "--max-usd", "5", "--output-dir", str(tmp_path / "again"))
        assert (result.exc, client.calls, len(kit.files_under(tmp_path / "again"))) == (None, [], 4)

    def test_a_max_usd_above_the_budget_left_is_refused(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "6")
        assert (result.exc is not None, client.calls, kit.files_under(".")) == (True, [], [])


class TestLiveGuard:
    def test_live_with_no_api_key_is_refused_before_anything_is_asked(self, fake, settings, typed):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live")
        assert (
            "api" in result.text.lower() and "key" in result.text.lower(), client.calls, typed.prompts, kit.files_under("."),
        ) == (True, [], [], [])

    def test_live_with_no_key_is_refused_even_with_yes(self, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert (result.exc is not None, client.calls) == (True, [])

    def test_live_needs_max_usd(self, fake, typed):
        client = fake(*script())
        result = command("--facts", LAW, "--live", "--yes")
        assert ("max-usd" in result.text.lower().replace("_", "-"), client.calls, typed.prompts) == (True, [], [])

    def test_a_limit_above_the_budget_left_is_refused_before_anything_is_asked(self, fake, tune, typed):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("5"))
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "6", "--live")
        assert (result.exc is not None, client.calls, typed.prompts) == (True, [], [])

    def test_the_worst_case_and_the_cap_are_printed_before_the_question(self, fake, typed):
        typed.reply("no")
        fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live")
        shown = kit.dollars_in(result.out)
        assert (dollars(worst_case_of([LAW])) in shown, Decimal("5.0000") in shown, len(typed.prompts)) == (True, True, 1)

    @pytest.mark.parametrize("answer", ["no", "", "y", "YES", "yes please"])
    def test_anything_but_yes_makes_no_call(self, fake, typed, answer):
        typed.reply(answer)
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live")
        assert (result.exc is not None, client.calls, kit.files_under(".")) == (True, [], [])

    def test_end_of_input_makes_no_call(self, fake, typed):
        typed.reply(EOFError())
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live")
        assert (result.exc is not None, client.calls) == (True, [])

    def test_the_switch_is_restored_after_a_refused_confirmation(self, fake, typed):
        from django.conf import settings

        typed.reply("no")
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live")
        assert settings.LLM_ENABLED is True

    def test_yes_typed_runs_the_calls(self, fake, typed):
        typed.reply("yes")
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live")
        assert (result.exc, len(client.calls)) == (None, 2)

    def test_the_yes_flag_skips_the_question(self, fake, typed):
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert (result.exc, len(client.calls), typed.prompts) == (None, 2, [])

    def test_the_switch_is_restored_after_a_live_run(self, fake):
        from django.conf import settings

        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert settings.LLM_ENABLED is True

    def test_the_kill_switch_stays_off_for_a_live_run_when_the_setting_is_off(self, fake, settings):
        settings.LLM_ENABLED = False
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        from django.conf import settings as live

        assert (result.exc, len(client.calls), live.LLM_ENABLED) == (None, 2, False)


class TestARealRunWithFakeAnswers:
    def test_it_writes_bases_and_four_transcripts_for_a_law(self, fake):
        fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert (result.exc, kit.files_under("generated")) == (None, [
            f"bases/{LAW}_left.json", f"bases/{LAW}_right.json",
            f"transcripts/{LAW}_left_err.json", f"transcripts/{LAW}_left_true.json",
            f"transcripts/{LAW}_right_err.json", f"transcripts/{LAW}_right_true.json",
        ])

    def test_the_output_directory_is_honoured(self, fake, tmp_path):
        fake(*script())
        out = tmp_path / "elsewhere"
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes", "--output-dir", str(out))
        assert (kit.files_under(out), (tmp_path / "generated" / "transcripts").exists()) == (
            sorted(f"{LAW}_{s}_{a}.json" for s in ("left", "right") for a in ("true", "err")), False,
        )

    def test_the_files_pass_the_replay_validator(self, fake, tmp_path):
        from moderation.management.commands.spike import validate_transcript

        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes", "--output-dir", str(tmp_path / "o"))
        paths = sorted((tmp_path / "o").glob("*.json"))
        assert [validate_transcript(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths] == [None] * 4

    def test_a_subset_of_two_facts_makes_four_calls(self, fake):
        client = fake(*script(2))
        command("--facts", f"{LAW},{LAW2}", "--max-usd", "5", "--live", "--yes")
        assert len(client.calls) == 4

    def test_a_subset_writes_only_those_facts(self, fake):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert {name.split("/")[1].split("_left")[0].split("_right")[0] for name in kit.files_under("generated")} == {LAW}

    def test_a_statistic_fact_gives_eight_transcripts(self, fake):
        fake(*script())
        command("--facts", "incarceration_rates", "--max-usd", "5", "--live", "--yes")
        assert len([n for n in kit.files_under("generated") if n.startswith("transcripts/")]) == 8

    def test_the_ledger_shows_replay_generator_calls(self, fake):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert {(r.purpose, r.agent) for r in kit.ledger()} == {("replay", "generator")}

    def test_the_summary_reports_the_calls(self, fake):
        fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        assert "Calls made: 2" in result.out

    def test_the_limit_stops_the_run_and_writes_no_transcript(self, fake):
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "0.0001", "--live", "--yes")
        assert (result.exc, client.calls, kit.files_under(".")) == (None, [], [])

    def test_a_second_run_reuses_the_bases_but_will_not_overwrite_the_transcripts(self, fake):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        before = {n: open(f"generated/{n}", encoding="utf-8").read() for n in kit.files_under("generated")}
        client = fake()
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        after = {n: open(f"generated/{n}", encoding="utf-8").read() for n in kit.files_under("generated")}
        assert (result.exc is not None, client.calls, after) == (True, [], before)

    def test_overwrite_regenerates_and_replaces(self, fake):
        fake(*script())
        command("--facts", LAW, "--max-usd", "5", "--live", "--yes")
        client = fake(*script())
        result = command("--facts", LAW, "--max-usd", "5", "--live", "--yes", "--overwrite")
        assert (result.exc, len(client.calls)) == (None, 2)


class TestSecrets:
    @pytest.mark.parametrize("args", [
        ("--dry-run",),
        ("--facts", LAW, "--max-usd", "5"),
        ("--facts", LAW, "--max-usd", "5", "--live", "--yes"),
        ("--facts", LAW, "--max-usd", "5", "--live"),
    ])
    def test_the_key_never_appears_in_the_output(self, fake, typed, args):
        typed.reply("no")
        fake(*script())
        assert kit.DUMMY_KEY not in command(*args).text

    def test_no_message_text_is_printed(self, fake):
        fake(*script())
        assert SECRET_TEXT not in command("--facts", LAW, "--max-usd", "5", "--live", "--yes").text

    def test_the_command_source_does_not_read_the_environment_or_env_file(self):
        source = (kit.ROOT / "evaluation" / "management" / "commands" / "generate_conversations.py").read_text(encoding="utf-8")
        assert [w for w in ("os.environ", "getenv", "dotenv", ".env'", '.env"') if w in source] == []
