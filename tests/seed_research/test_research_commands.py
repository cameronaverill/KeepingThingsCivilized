"""manage.py run_research_eval, judge_research and summarize_research: arguments, --dry-run, without --live, the live guard,
the JSON Lines file, reruns and the whole chain."""
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

import seed_research_kit as kit

PATH = kit.jsonl_path()
SECRET = "zebrafishquartz"
V = kit.judge_verdict


def eval_cmd(*args):
    return kit.run_research_eval_cmd("--experiment", kit.EXPERIMENT, *args)


def eval_live(*args):
    return eval_cmd("--max-usd", "5", "--live", "--yes", *args)


def judge_cmd(*args):
    return kit.judge_research_cmd("--experiment", kit.EXPERIMENT, *args)


def judge_live(*args):
    return judge_cmd("--max-usd", "5", "--live", "--yes", *args)


def summ_cmd(*args):
    return kit.summarize_research_cmd("--experiment", kit.EXPERIMENT, *args)


def two():
    kit.add_conversation(side="left", arm="l1", acts=[("offer_research", f"Offer {SECRET}.")])
    kit.add_conversation(side="right", arm="l1")


def ledger_count():
    return len(kit.ledger())


class TestRunArguments:
    def test_an_experiment_is_required(self):
        assert kit.run_research_eval_cmd("--dry-run").exc is not None

    def test_max_usd_is_required_without_dry_run(self, fake):
        two()
        client = fake(kit.research_item())
        result = eval_cmd()
        assert (result.exc is not None, client.calls, kit.research_runs()) == (True, [], [])

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan"])
    def test_a_bad_max_usd_is_refused(self, fake, bad):
        two()
        client = fake(kit.research_item())
        result = eval_cmd("--max-usd", bad, "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.research_runs()) == (True, [], [])

    def test_an_unknown_experiment_is_refused(self, fake):
        client = fake(kit.research_item())
        result = kit.run_research_eval_cmd("--experiment", "nothing_here", "--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, client.calls) == (True, [])

    def test_an_experiment_with_no_eligible_act_is_refused(self, fake):
        kit.add_conversation(acts=[("ask_clarification", "x")])
        assert eval_cmd("--dry-run").exc is not None


class TestRunDryRun:
    def test_it_calls_nothing_creates_nothing_and_writes_nothing(self, fake):
        two()
        client = fake(kit.research_item())
        result = eval_cmd("--dry-run")
        assert (result.exc, client.calls, ledger_count(), kit.research_runs(), kit.files_under("generated")) == (None, [], 0, [], [])

    def test_it_ignores_live_and_asks_nothing(self, fake, typed):
        two()
        client = fake(kit.research_item())
        result = eval_cmd("--dry-run", "--live")
        assert (result.exc, client.calls, typed.prompts, kit.research_runs()) == (None, [], [], [])

    def test_it_says_it_is_a_dry_run_and_how_many_would_run(self, fake):
        two()
        fake()
        text = eval_cmd("--dry-run").text
        assert "dry run" in text.lower() and re.search(r"to run: 2", text)

    def test_the_worst_case_printed_includes_the_search_fees(self, fake):
        from seeding import research_eval

        two()
        fake()
        printed = kit.dollars_in(eval_cmd("--dry-run").text)
        total = sum((research_eval.estimate_research_usd(a) for a in research_eval.plan_research(kit.EXPERIMENT)), Decimal("0"))
        assert Decimal(f"{total:.4f}") in printed
        assert total > 2 * 3 * Decimal("0.01")

    def test_it_prints_the_budget_left(self, fake, tune):
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("12.5"))
        two()
        fake()
        assert Decimal("12.5000") in kit.dollars_in(eval_cmd("--dry-run").text)

    def test_max_usd_below_the_worst_case_gives_a_warning(self, fake):
        two()
        fake()
        assert "warning" in eval_cmd("--dry-run", "--max-usd", "0.000001").text.lower()

    def test_set_narrows_the_plan(self, fake):
        two()
        fake()
        assert re.search(r"to run: 1", eval_cmd("--dry-run", "--set", "*_right_*").text)

    def test_done_runs_are_not_counted_to_run(self, fake):
        a = kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        kit.finished_research(a)
        fake()
        assert re.search(r"to run: 1", eval_cmd("--dry-run").text)

    def test_retry_failed_puts_failed_runs_back_in_the_plan(self, fake):
        a = kit.add_conversation(side="left", arm="l1")
        kit.make_research_run(a, status="failed")
        fake()
        assert (re.search(r"to run: 0", eval_cmd("--dry-run").text) is not None, re.search(r"to run: 1", eval_cmd("--dry-run", "--retry-failed").text) is not None) == (True, True)

    def test_it_never_prints_the_key_or_a_text(self, fake):
        two()
        fake()
        text = eval_cmd("--dry-run").text
        assert (kit.DUMMY_KEY in text, SECRET in text) == (False, False)


class TestRunWithoutLive:
    def test_no_call_and_no_run(self, fake):
        two()
        client = fake(kit.research_item())
        result = eval_cmd("--max-usd", "5")
        assert (result.exc, client.calls, ledger_count(), kit.research_runs()) == (None, [], 0, [])

    def test_it_says_calls_are_off_and_asks_nothing(self, fake, typed):
        two()
        fake(kit.research_item())
        assert "off" in eval_cmd("--max-usd", "5").text.lower() and typed.prompts == []


class TestRunLiveGuard:
    def test_a_typed_yes_is_needed(self, fake, typed):
        two()
        client = fake(kit.research_item())
        typed.reply("no")
        result = eval_cmd("--max-usd", "5", "--live")
        assert (result.exc is not None, client.calls, kit.research_runs()) == (True, [], [])

    def test_a_typed_yes_lets_it_run(self, fake, typed):
        kit.add_conversation()
        client = fake(kit.research_item())
        typed.reply("yes")
        result = eval_cmd("--max-usd", "5", "--live")
        assert (result.exc, len(client.calls)) == (None, 1)

    def test_yes_skips_the_question(self, fake, typed):
        kit.add_conversation()
        client = fake(kit.research_item())
        result = eval_live()
        assert (result.exc, len(client.calls), typed.prompts) == (None, 1, [])

    def test_no_key_refuses_a_live_run(self, fake, settings):
        settings.ANTHROPIC_API_KEY = ""
        two()
        client = fake(kit.research_item())
        result = eval_live()
        assert (result.exc is not None, client.calls, kit.research_runs()) == (True, [], [])

    def test_no_key_and_no_response_text_is_printed(self, fake):
        kit.add_conversation()
        fake(kit.research_item(text=f"Note {SECRET}."))
        text = eval_live().text
        assert kit.DUMMY_KEY not in text and SECRET not in text


class TestRunLive:
    def test_every_conversation_is_run_once_and_the_notes_are_posted(self, fake):
        two()
        client = fake(kit.research_item(), kit.research_item())
        result = eval_live()
        runs = kit.research_runs()
        assert (result.exc, len(client.calls), [r.status for r in runs], [r.requested_by.label for r in runs]) == (
            None, 2, ["done", "done"], ["A", "A"])

    def test_a_rerun_makes_no_call(self, fake):
        two()
        fake(kit.research_item(), kit.research_item())
        eval_live()
        client = fake(kit.research_item())
        assert (eval_live().exc, client.calls, len(kit.research_runs())) == (None, [], 2)

    def test_a_small_cap_stops_early_and_says_so(self, fake):
        two()
        client = fake(kit.research_item(), kit.research_item())
        result = eval_cmd("--max-usd", "0.01", "--live", "--yes")
        assert (client.calls, "stopped" in result.text.lower(), kit.research_runs()) == ([], True, [])

    def test_retry_failed_reruns_the_failed_one(self, fake):
        kit.add_conversation()
        fake(kit.invalid_json, kit.invalid_json)
        eval_live()
        client = fake(kit.research_item())
        assert eval_live().exc is None and client.calls == []
        eval_live("--retry-failed")
        assert [r.status for r in kit.research_runs()] == ["done"]

    def test_set_runs_only_the_matching_conversations(self, fake):
        two()
        client = fake(kit.research_item())
        eval_live("--set", "*_left_*")
        assert (len(client.calls), [r.conversation.transcript_id for r in kit.research_runs()]) == (1, ["range_fact_left_l1"])


class TestJudgeCommand:
    def done(self, fake, n=2):
        two()
        fake(*[kit.research_item() for _ in range(n)])
        assert eval_live().exc is None

    def test_it_needs_an_experiment_and_a_max_usd(self, fake):
        self.done(fake)
        client = fake(kit.priced(V()))
        assert kit.judge_research_cmd("--dry-run").exc is not None
        assert judge_cmd().exc is not None and client.calls == []

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan"])
    def test_a_bad_max_usd_is_refused(self, fake, bad):
        self.done(fake)
        client = fake(kit.priced(V()))
        result = judge_cmd("--max-usd", bad, "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])

    def test_an_experiment_without_notes_is_refused(self, fake):
        two()
        client = fake(kit.priced(V()))
        result = judge_cmd("--max-usd", "1", "--live", "--yes")
        assert (result.exc is not None, client.calls) == (True, [])

    def test_max_usd_above_the_evaluation_budget_left_is_refused(self, fake, tune):
        self.done(fake)
        tune(BUDGET_EVAL_USD_TOTAL=Decimal("0.02"))
        client = fake(kit.priced(V()))
        result = judge_cmd("--max-usd", "2", "--live", "--yes")
        assert (result.exc is not None, client.calls, kit.files_under("generated")) == (True, [], [])

    def test_dry_run_calls_nothing_writes_nothing_and_prints_the_worst_case(self, fake):
        self.done(fake)
        before = ledger_count()
        client = fake(kit.priced(V()))
        result = judge_cmd("--dry-run")
        assert (result.exc, client.calls, ledger_count(), kit.files_under("generated")) == (None, [], before, [])
        assert "dry run" in result.text.lower() and re.search(r"to judge: 2", result.text)
        assert any(d > 0 for d in kit.dollars_in(result.text))

    def test_dry_run_worst_case_is_what_the_calls_reserve(self, fake):
        self.done(fake)
        printed = kit.dollars_in(judge_cmd("--dry-run").text)
        before = sum((r.reserved_usd for r in kit.ledger() if r.purpose == "judge"), Decimal("0"))
        fake(kit.priced(V()), kit.priced(V()))
        judge_live()
        reserved = sum((r.reserved_usd for r in kit.ledger() if r.purpose == "judge"), Decimal("0")) - before
        assert Decimal(f"{reserved:.4f}") in printed

    def test_without_live_nothing_is_judged(self, fake):
        self.done(fake)
        client = fake(kit.priced(V()))
        result = judge_cmd("--max-usd", "5")
        assert (result.exc, client.calls, kit.files_under("generated")) == (None, [], []) and "off" in result.text.lower()

    def test_a_typed_yes_is_needed(self, fake, typed):
        self.done(fake)
        client = fake(kit.priced(V()))
        typed.reply("no")
        assert (judge_cmd("--max-usd", "5", "--live").exc is not None, client.calls) == (True, [])

    def test_no_key_refuses_a_live_run(self, fake, settings):
        self.done(fake)
        settings.ANTHROPIC_API_KEY = ""
        client = fake(kit.priced(V()))
        assert (judge_live().exc is not None, client.calls) == (True, [])

    def test_live_writes_one_row_per_note_with_the_case_and_judge_fields(self, fake):
        self.done(fake)
        fake(kit.priced(V(tag="3")), kit.priced(V(tag="1", verdict="unclear")))
        result = judge_live()
        rows = kit.read_jsonl(PATH)
        assert result.exc is None and [(r["conversation_id"], r["tag"], r["verdict"], r["prompt_version"]) for r in rows] == [
            ("range_fact_left_l1", "3", "disputes_claim", "sj_r1"), ("range_fact_right_l1", "1", "unclear", "sj_r1")]
        assert (rows[0]["side"], rows[0]["level"], rows[0]["note_text"], rows[0]["n_sources"], rows[0]["confidence"]) == (
            "left", 1, kit.NOTE_TEXT, 3, 0.7)

    def test_the_file_name_is_research_experiment_jsonl(self, fake):
        self.done(fake)
        fake(kit.priced(V()), kit.priced(V()))
        judge_live()
        assert kit.files_under("generated") == [f"judgments/research_{kit.EXPERIMENT}.jsonl"]

    def test_a_failed_research_run_is_reported_and_not_judged(self, fake):
        kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        fake(kit.research_item(), kit.invalid_json, kit.invalid_json)
        eval_live()
        client = fake(kit.priced(V()))
        result = judge_live()
        assert (len(client.calls), [r["conversation_id"] for r in kit.read_jsonl(PATH)]) == (1, ["range_fact_left_l1"])
        assert "range_fact_right_l1" in result.text

    def test_a_rerun_makes_no_call_and_adds_no_row(self, fake):
        self.done(fake)
        fake(kit.priced(V()), kit.priced(V()))
        judge_live()
        before = Path(PATH).read_text()
        client = fake(kit.priced(V()))
        assert (judge_live().exc, client.calls, Path(PATH).read_text()) == (None, [], before)

    def test_overwrite_judges_again(self, fake):
        self.done(fake)
        fake(kit.priced(V(tag="3")), kit.priced(V(tag="3")))
        judge_live()
        client = fake(kit.priced(V(tag="0")), kit.priced(V(tag="0")))
        judge_live("--overwrite")
        assert (len(client.calls), [r["tag"] for r in kit.read_jsonl(PATH)]) == (2, ["0", "0"])

    def test_the_judge_request_never_has_side_level_arm_or_fact(self, fake):
        self.done(fake)
        client = fake(kit.priced(V()), kit.priced(V()))
        judge_live()
        for call in kit.judge_calls(client):
            u = " ".join(m["content"] for m in call["messages"]).lower()
            for word in ("range_fact", "left", "right", "level", "l1", "sources:", "example.org"):
                assert word not in u, word

    def test_a_small_cap_stops_early_and_says_so(self, fake):
        self.done(fake)
        client = fake(kit.priced(V()))
        result = judge_cmd("--max-usd", "0.000001", "--live", "--yes")
        assert (client.calls, "stopped" in result.text.lower()) == ([], True)

    def test_the_key_and_the_note_are_never_printed(self, fake):
        kit.add_conversation()
        fake(kit.research_item(text=f"Note {SECRET} here."))
        eval_live()
        fake(kit.priced(V(rationale=f"Because {SECRET}.")))
        text = judge_live().text
        assert kit.DUMMY_KEY not in text and SECRET not in text


class TestSummarizeCommand:
    ROWS = [kit.row(side="left", arm="l1", tag="3"), kit.row(side="right", arm="l1", tag="0", verdict="confirms_claim")]

    def write(self, rows=None):
        path = Path(PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in (rows or self.ROWS)), encoding="utf-8")

    def test_it_prints_the_preliminary_summary(self):
        self.write()
        result = summ_cmd()
        assert (result.exc, "PRELIMINARY" in result.text, "n = 2" in result.text) == (None, True, True)

    def test_it_writes_the_markdown_by_default_equal_to_render_markdown(self):
        from seeding import research_analyze

        self.write()
        summ_cmd()
        written = Path(f"generated/research_{kit.EXPERIMENT}.md").read_text(encoding="utf-8")
        assert written == research_analyze.render_markdown(research_analyze.summarize(self.ROWS))

    def test_output_chooses_the_file(self, tmp_path):
        self.write()
        target = tmp_path / "x" / "out.md"
        summ_cmd("--output", str(target))
        assert target.read_text(encoding="utf-8").startswith("# PRELIMINARY") and not Path(f"generated/research_{kit.EXPERIMENT}.md").exists()

    def test_a_missing_file_is_an_error_and_writes_nothing(self):
        result = summ_cmd()
        assert (result.exc is not None, kit.files_under("generated")) == (True, [])

    def test_an_empty_file_is_an_error(self):
        Path(PATH).parent.mkdir(parents=True)
        Path(PATH).write_text("", encoding="utf-8")
        assert summ_cmd().exc is not None

    def test_the_experiment_is_required(self):
        assert kit.summarize_research_cmd().exc is not None

    def test_it_makes_no_call(self, fake):
        self.write()
        client = fake(kit.priced(V()))
        summ_cmd()
        assert (client.calls, kit.ledger()) == ([], [])

    def test_it_reads_the_research_file_not_the_pilot_file(self):
        Path("generated/judgments").mkdir(parents=True)
        Path(f"generated/judgments/{kit.EXPERIMENT}.jsonl").write_text(json.dumps(kit.row()) + "\n", encoding="utf-8")
        assert summ_cmd().exc is not None


def test_the_whole_chain_from_replayed_conversations_to_a_summary(fake):
    for side in ("left", "right"):
        for arm in ("l1", "l2", "l3"):
            kit.add_conversation(side=side, arm=arm)
    kit.add_conversation(side="left", arm="true")
    kit.add_conversation(fact_id="law_fact", side="right", arm="err")
    fake(*[kit.research_item() for _ in range(8)])
    assert eval_live().exc is None
    fake(*[kit.priced(V(tag="3")) for _ in range(8)])
    assert judge_live().exc is None
    result = summ_cmd()
    assert result.exc is None and "PRELIMINARY: n = 8" in result.text
    rows = kit.read_jsonl(PATH)
    assert len(rows) == 8 and {r["requested_by"] if "requested_by" in r else None for r in rows} == {None}
