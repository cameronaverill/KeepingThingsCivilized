"""manage.py judge_responses: arguments, --dry-run, what a run without --live does, the --live guard, the JSON Lines file
and the skip-already-judged rule."""
import json
import re
from dataclasses import asdict
from decimal import Decimal

import judge_kit as kit
import pytest

PATH = f"generated/judgments/{kit.EXPERIMENT}.jsonl"
SECRET = "zebrafishquartz"


def command(*args):
    return kit.judge_responses("--experiment", kit.EXPERIMENT, *args)


def live(*args):
    return command("--max-usd", "5", "--live", "--yes", *args)


def responded(arm="l1", side="left", text=None, **kw):
    return kit.add_conversation(side=side, arm=arm, acts=[("correct_factual_error", text or f"Reply {arm} {side} {SECRET}.")], **kw)


def silent(arm="l3", side="right", **kw):
    return kit.add_conversation(side=side, arm=arm, acts=[], **kw)


def ledger_count():
    return len(kit.ledger())


class TestArguments:
    def test_the_command_exists_and_needs_an_experiment(self):
        result = kit.judge_responses("--dry-run")
        assert result.exc is not None

    def test_max_usd_is_required_without_dry_run(self, fake):
        responded()
        client = fake(kit.verdict())
        result = command()
        assert ("max-usd" in result.text.lower().replace("_", "-"), result.exc is not None, client.calls) == (True, True, [])

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan"])
    def test_a_bad_max_usd_is_refused(self, fake, bad):
        responded()
        client = fake(kit.verdict())
        result = command("--max-usd", bad, "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])

    def test_an_unknown_experiment_is_refused(self, fake):
        client = fake(kit.verdict())
        result = kit.judge_responses("--experiment", "nothing_here", "--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, client.calls) == (True, [])

    def test_max_usd_above_the_evaluation_budget_left_is_refused(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("1"))
        responded()
        client = fake(kit.verdict())
        result = command("--max-usd", "2", "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])


class TestDryRun:
    def test_it_calls_nothing_and_writes_nothing(self, fake):
        responded()
        silent()
        client = fake(kit.verdict())
        result = command("--dry-run")
        assert (result.exc, client.calls, ledger_count(), kit.files_under("generated")) == (None, [], 0, [])

    def test_it_says_it_is_a_dry_run(self, fake):
        responded()
        fake()
        assert "dry run" in command("--dry-run").text.lower()

    def test_it_ignores_live_and_asks_nothing(self, fake, typed):
        responded()
        client = fake(kit.verdict())
        result = command("--dry-run", "--live")
        assert (result.exc, client.calls, typed.prompts, kit.files_under("generated")) == (None, [], [], [])

    def test_it_prints_how_many_cases_need_a_call(self, fake):
        for arm in ("l1", "l2", "l3"):
            responded(arm=arm)
        silent(arm="l1", side="right")
        fake()
        text = command("--dry-run").text
        assert re.search(r"(?<![\d.])3(?![\d.])[^\n]*\bcall", text) is not None

    def test_the_worst_case_printed_is_what_the_calls_reserve(self, fake):
        for arm in ("l1", "l2", "l3"):
            responded(arm=arm)
        silent(side="right", arm="l1")
        fake()
        printed = kit.dollars_in(command("--dry-run").text)
        fake(*(kit.priced(kit.verdict()) for _ in range(3)))
        assert live().exc is None
        reserved = sum((row.reserved_usd for row in kit.ledger()), Decimal("0"))
        assert Decimal(f"{reserved:.4f}") in printed

    def test_a_case_already_judged_is_not_counted_in_the_worst_case(self, fake):
        responded(arm="l1")
        responded(arm="l2")
        fake(kit.priced(kit.verdict()), kit.priced(kit.verdict()))
        assert live().exc is None
        fake()
        assert Decimal("0.0000") in kit.dollars_in(command("--dry-run").text)

    def test_max_usd_below_the_worst_case_gives_a_warning(self, fake):
        responded()
        fake()
        assert "warning" in command("--dry-run", "--max-usd", "0.000001").text.lower()

    def test_it_never_prints_the_key_or_a_response_text(self, fake):
        responded()
        fake()
        text = command("--dry-run").text
        assert (kit.DUMMY_KEY in text, SECRET in text) == (False, False)

    def test_skipped_conversations_are_reported(self, fake):
        responded(arm="l1")
        kit.add_conversation(arm="l2", run="failed")
        fake()
        assert "range_fact_left_l2" in command("--dry-run").text


class TestWithoutLive:
    def test_no_call_is_made_and_no_judgment_by_the_model_is_written(self, fake):
        responded()
        client = fake(kit.verdict())
        result = command("--max-usd", "5")
        assert (result.exc, client.calls, ledger_count(), kit.files_under("generated")) == (None, [], 0, [])

    def test_the_report_says_calls_are_off(self, fake):
        responded()
        fake(kit.verdict())
        assert "off" in command("--max-usd", "5").text.lower()

    def test_it_asks_nothing(self, fake, typed):
        responded()
        fake(kit.verdict())
        command("--max-usd", "5")
        assert typed.prompts == []


class TestLiveGuard:
    def test_a_typed_yes_is_needed(self, fake, typed):
        responded()
        client = fake(kit.priced(kit.verdict()))
        typed.reply("no")
        result = command("--max-usd", "5", "--live")
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])

    def test_a_typed_yes_lets_it_run(self, fake, typed):
        responded()
        client = fake(kit.priced(kit.verdict()))
        typed.reply("yes")
        result = command("--max-usd", "5", "--live")
        assert (result.exc, len(client.calls)) == (None, 1)

    def test_yes_skips_the_question(self, fake, typed):
        responded()
        client = fake(kit.priced(kit.verdict()))
        result = live()
        assert (result.exc, len(client.calls), typed.prompts) == (None, 1, [])

    def test_no_key_refuses_a_live_run(self, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        responded()
        client = fake(kit.verdict())
        result = live()
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])

    def test_the_key_is_never_printed(self, fake):
        responded()
        fake(kit.priced(kit.verdict()))
        assert kit.DUMMY_KEY not in live().text

    def test_no_response_text_is_printed(self, fake):
        responded()
        fake(kit.priced(kit.verdict(rationale=f"Because {SECRET}.")))
        assert SECRET not in live().text


class TestTheJudgmentsFile:
    def test_one_object_per_case_with_the_case_fields_and_the_verdict(self, fake):
        responded(arm="l1", side="left", text="The register says five hundred to five sixty.")
        fake(kit.priced(kit.verdict(tag="3", unseeded=2, rationale="Named the range.")))
        assert live().exc is None
        (row,) = kit.read_jsonl(PATH)
        assert row == {
            "conversation_id": "range_fact_left_l1", "assignment": "as-is", "fact_id": "range_fact", "arm": "l1",
            "side": "left", "level": 1, "is_error_arm": True, "false_claim": kit.RANGE_FALSE[("left", "l1")],
            "true_claim": kit.RANGE_TRUE, "run_status": "done", "intervened": True, "n_issues": 0, "n_acts": 1,
            "response_text": "The register says five hundred to five sixty.", "response_words": 8,
            "act_types": ["correct_factual_error"], "issue_quotes": [], "tag": "3", "unseeded_flagged": 2,
            "rationale": "Named the range.", "prompt_version": "sj_v1",
        }

    def test_a_case_with_no_response_is_saved_without_a_call(self, fake):
        silent(arm="l2", side="right")
        kit.add_conversation(side="left", arm="true", acts=[])
        client = fake()
        assert live().exc is None
        rows = {r["conversation_id"]: r for r in kit.read_jsonl(PATH)}
        assert (client.calls, rows["range_fact_right_l2"]["tag"], rows["range_fact_left_true"]["tag"],
                rows["range_fact_right_l2"]["rationale"], rows["range_fact_right_l2"]["unseeded_flagged"]) == (
            [], "0", "N/A", "no moderator response", 0)

    def test_the_file_is_json_lines_one_line_per_case(self, fake):
        responded(arm="l1")
        responded(arm="l2")
        fake(kit.priced(kit.verdict()), kit.priced(kit.verdict()))
        live()
        lines = open(PATH, encoding="utf-8").read().splitlines()
        assert (len(lines), [json.loads(line)["conversation_id"] for line in lines]) == (
            2, ["range_fact_left_l1", "range_fact_left_l2"])

    def test_both_assignments_are_saved_as_separate_rows(self, fake):
        responded(arm="l1", assignment="as-is")
        responded(arm="l1", assignment="swapped")
        fake(kit.priced(kit.verdict(tag="3")), kit.priced(kit.verdict(tag="0")))
        live()
        rows = kit.read_jsonl(PATH)
        assert sorted((r["assignment"], r["tag"]) for r in rows) == [("as-is", "3"), ("swapped", "0")]

    def test_a_failed_case_leaves_no_row(self, fake):
        responded(arm="l1")
        fake(kit.BAD_VERDICT, kit.BAD_VERDICT)
        live()
        assert kit.files_under("generated") == []


class TestRerun:
    def test_a_rerun_makes_no_call_and_adds_no_row(self, fake):
        responded(arm="l1")
        silent(arm="l2", side="right")
        fake(kit.priced(kit.verdict()))
        live()
        before = open(PATH, encoding="utf-8").read()
        client = fake()
        result = live()
        assert (result.exc, client.calls, open(PATH, encoding="utf-8").read()) == (None, [], before)

    def test_a_rerun_judges_only_the_new_conversation(self, fake):
        responded(arm="l1")
        fake(kit.priced(kit.verdict(tag="3")))
        live()
        responded(arm="l2")
        client = fake(kit.priced(kit.verdict(tag="1")))
        live()
        assert (len(client.calls), [(r["conversation_id"], r["tag"]) for r in kit.read_jsonl(PATH)]) == (
            1, [("range_fact_left_l1", "3"), ("range_fact_left_l2", "1")])

    def test_overwrite_judges_again_and_replaces_the_row(self, fake):
        responded(arm="l1")
        fake(kit.priced(kit.verdict(tag="3")))
        live()
        client = fake(kit.priced(kit.verdict(tag="0")))
        live("--overwrite")
        assert (len(client.calls), [r["tag"] for r in kit.read_jsonl(PATH)]) == (1, ["0"])

    def test_a_row_of_another_prompt_version_does_not_count_as_judged(self, fake):
        responded(arm="l1")
        fake(kit.priced(kit.verdict(tag="3")))
        live()
        row = kit.read_jsonl(PATH)[0]
        row["prompt_version"] = "sj_v0"
        row["tag"] = "2"
        with open(PATH, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        client = fake(kit.priced(kit.verdict(tag="1")))
        live()
        assert (len(client.calls), sorted((r["prompt_version"], r["tag"]) for r in kit.read_jsonl(PATH))) == (
            1, [("sj_v0", "2"), ("sj_v1", "1")])

    def test_a_row_of_the_other_assignment_does_not_count_as_judged(self, fake):
        responded(arm="l1", assignment="as-is")
        fake(kit.priced(kit.verdict(tag="3")))
        live()
        responded(arm="l1", assignment="swapped")
        client = fake(kit.priced(kit.verdict(tag="1")))
        live()
        assert (len(client.calls), sorted(r["assignment"] for r in kit.read_jsonl(PATH))) == (1, ["as-is", "swapped"])


class TestBudgetStop:
    def test_the_report_says_it_stopped_early(self, fake):
        for arm in ("l1", "l2", "l3"):
            responded(arm=arm)
        client = fake(kit.priced(kit.verdict()), kit.priced(kit.verdict()), kit.priced(kit.verdict()))
        result = command("--max-usd", "0.000001", "--live", "--yes")
        assert (client.calls, "stopped" in result.text.lower()) == ([], True)


def test_case_dataclass_round_trips_through_the_file(fake):
    """The saved object is the Case's own fields plus the verdict (no field is lost or renamed)."""
    from seeding import judge

    responded(arm="l3")
    fake(kit.priced(kit.verdict()))
    live()
    (case,) = judge.collect_cases(kit.EXPERIMENT)
    (row,) = kit.read_jsonl(PATH)
    assert {k: row[k] for k in asdict(case)} == asdict(case)
