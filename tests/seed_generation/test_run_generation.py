"""seeding.generate.plan_generation and run_generation: order of calls, saved bases, reuse, retry once, pair check, budget."""
import json
from decimal import Decimal

import gen_kit as kit
import pytest

from config import tunables


def gen():
    from seeding import generate

    return generate


def run(facts, tmp_path, **kwargs):
    kwargs.setdefault("max_usd", Decimal("50"))
    kwargs.setdefault("bases_dir", tmp_path / "bases")
    return gen().run_generation(facts, **kwargs)


def good_pair_script():
    return [kit.answer(side="left"), kit.answer(side="right")]


def short_right():
    """A right reply whose messages are far shorter than the left ones (fails the length check)."""
    return kit.answer_from(kit.with_lengths("right", [30, 30, 30, 60]))


BAD = kit.answer(last="No marker here at all.")


class TestPlan:
    def test_two_calls_per_ready_fact(self):
        assert len(gen().plan_generation([kit.range_fact()])) == 2

    def test_a_not_ready_fact_adds_nothing(self):
        assert len(gen().plan_generation([kit.range_fact(), kit.unready_fact()])) == 2

    def test_two_facts_double_it(self):
        assert len(gen().plan_generation([kit.range_fact(), kit.law_fact()])) == 4

    def test_no_facts_no_items(self):
        assert gen().plan_generation([]) == []

    def test_planning_makes_no_call(self, fake):
        client = fake()
        gen().plan_generation([kit.range_fact()])
        assert (client.calls, kit.ledger()) == ([], [])


class TestAHappyRun:
    def test_it_returns_all_eight_transcripts_in_order(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert [t["id"] for t in report.transcripts] == [f"range_fact_{s}_{a}" for s, a in kit.ARM_ORDER]

    def test_it_makes_two_calls_left_then_mirror(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert (report.calls, [r.prompt_version for r in kit.ledger()], [r.attempt for r in kit.ledger()]) == (2, ["gen_v7", "mirror_v7"], [1, 1])

    def test_every_call_is_a_replay_purpose_generator_call(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        assert {(r.purpose, r.agent, r.model, r.status) for r in kit.ledger()} == {("replay", "generator", tunables.GENERATOR_MODEL, "ok")}

    def test_the_mirror_call_is_given_the_left_base_the_model_wrote(self, fake, tmp_path):
        left = kit.with_message(kit.base("left"), 0, text="Zebrafish quartz opening for the mirror to see. " + "thing " * 20)
        client = fake(kit.answer_from(left), kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert ("Zebrafish quartz opening" in kit.request_text(client.calls[1]), "Zebrafish quartz" in kit.request_text(client.calls[0])) == (True, False)

    def test_the_bases_are_saved_by_fact_and_side(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "bases") == ["range_fact_left.json", "range_fact_right.json"]

    def test_a_saved_base_is_the_base_the_transcripts_were_built_from(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        saved = json.loads((tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8"))
        expected = kit.answer(side="left")["messages"]
        assert (saved["side"], saved["fact_id"], [(m["author"], m["text"]) for m in saved["messages"]], report.transcripts[0]["messages"][0]["text"]) == (
            "left", "range_fact", [(a, m["text"]) for m, a in zip(expected, ["Participant A", "Participant B", "Participant A", "Participant B"])], expected[0]["text"],
        )

    def test_the_default_bases_directory_is_generated_bases(self, fake, tmp_path):
        fake(*good_pair_script())
        gen().run_generation([kit.range_fact()], max_usd=Decimal("50"))
        assert kit.files_under(tmp_path / "generated") == ["bases/range_fact_left.json", "bases/range_fact_right.json"]

    def test_every_transcript_passes_the_replay_validator(self, fake, tmp_path):
        fake(*good_pair_script())
        assert [kit.validate(t) for t in run([kit.range_fact()], tmp_path).transcripts] == [None] * 8

    def test_run_generation_writes_no_transcript_files(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        assert not (tmp_path / "generated" / "transcripts").exists()

    def test_the_report_has_no_failures_and_counts_the_fact(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert (report.failures, report.facts_done, report.bases_generated, report.bases_reused, report.stopped_reason) == ([], 1, 2, 0, None)

    def test_the_cost_is_what_the_ledger_says(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert (report.cost_total > 0, report.cost_total) == (True, sum(r.cost_usd for r in kit.all_ledger()))

    def test_two_facts_are_generated_in_order(self, fake, tmp_path):
        fake(*good_pair_script(), *good_pair_script())
        report = run([kit.range_fact(), kit.law_fact()], tmp_path)
        assert ([t["id"] for t in report.transcripts][:1], len(report.transcripts), report.calls) == (["range_fact_left_true"], 12, 4)

    def test_a_law_fact_gives_four_transcripts(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.law_fact()], tmp_path)
        assert [t["id"] for t in report.transcripts] == ["law_fact_left_true", "law_fact_left_err", "law_fact_right_true", "law_fact_right_err"]

    def test_the_same_script_gives_the_same_transcripts(self, fake, tmp_path):
        fake(*good_pair_script())
        first = run([kit.range_fact()], tmp_path / "one").transcripts
        fake(*good_pair_script())
        second = run([kit.range_fact()], tmp_path / "two").transcripts
        assert json.dumps(first) == json.dumps(second)


class TestNotReady:
    def test_a_not_ready_fact_is_skipped_and_reported(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.unready_fact(), kit.range_fact()], tmp_path)
        assert (report.skipped_not_ready, report.calls, len(report.transcripts)) == (["unready_fact"], 2, 8)

    def test_only_not_ready_facts_means_no_call(self, fake, tmp_path):
        client = fake()
        report = run([kit.unready_fact()], tmp_path)
        assert (client.calls, report.transcripts, report.skipped_not_ready) == ([], [], ["unready_fact"])


class TestReuseOfSavedBases:
    def test_a_second_run_makes_no_call_and_gives_the_same_transcripts(self, fake, tmp_path):
        fake(*good_pair_script())
        first = run([kit.range_fact()], tmp_path)
        client = fake()
        second = run([kit.range_fact()], tmp_path)
        assert (client.calls, second.calls, second.bases_reused, second.bases_generated, json.dumps(second.transcripts)) == (
            [], 0, 2, 0, json.dumps(first.transcripts),
        )

    def test_a_saved_left_base_is_reused_and_only_the_right_is_generated(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        (tmp_path / "bases" / "range_fact_right.json").unlink()
        client = fake(kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), report.bases_reused, [r.prompt_version for r in kit.ledger()][-1]) == (1, 1, "mirror_v7")

    def test_a_saved_right_base_is_reused_and_only_the_left_is_generated(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        (tmp_path / "bases" / "range_fact_left.json").unlink()
        client = fake(kit.answer(side="left"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), report.bases_reused, len(report.transcripts)) == (1, 1, 8)

    def test_overwrite_regenerates_both_bases(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        replacement = kit.with_message(kit.base("left"), 0, text="Replacement opening line without digits. " + "thing " * 20)
        client = fake(kit.answer_from(replacement), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path, overwrite=True)
        saved = json.loads((tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8"))
        assert (len(client.calls), report.bases_reused, saved["messages"][0]["text"].startswith("Replacement opening")) == (2, 0, True)

    def test_a_saved_base_for_another_fact_is_not_used(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        text = (tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8")
        (tmp_path / "bases" / "law_fact_left.json").write_text(text, encoding="utf-8")  # holds range_fact as its fact_id
        client = fake(*good_pair_script())
        report = run([kit.law_fact()], tmp_path)
        assert (report.transcripts, report.failures != [], client.calls) == ([], True, [])

    def test_an_invalid_saved_base_is_not_silently_used(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        path = tmp_path / "bases" / "range_fact_left.json"
        broken = json.loads(path.read_text(encoding="utf-8"))
        broken["messages"][3]["text"] = "the marker has been lost"
        path.write_text(json.dumps(broken), encoding="utf-8")
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (client.calls == [], report.failures != [], report.transcripts) == (True, True, [])


class TestRetryOnce:
    def test_an_invalid_base_is_retried_once_with_attempt_two(self, fake, tmp_path):
        fake(BAD, kit.answer(side="left"), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (report.calls, [r.attempt for r in kit.ledger()], report.failures, len(report.transcripts)) == (3, [1, 2, 1], [], 8)

    def test_a_reply_that_does_not_fit_the_schema_is_retried_once(self, fake, tmp_path):
        fake({"messages": [{"author": "Somebody Else", "text": "Hi."}]}, kit.answer(side="left"), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert ([r.attempt for r in kit.ledger()], report.failures, len(report.transcripts)) == ([1, 2, 1], [], 8)

    def test_the_mirror_is_retried_once_too(self, fake, tmp_path):
        fake(kit.answer(side="left"), BAD, kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert ([r.prompt_version for r in kit.ledger()], [r.attempt for r in kit.ledger()], len(report.transcripts)) == (
            ["gen_v7", "mirror_v7", "mirror_v7"], [1, 1, 2], 8,
        )

    def test_a_second_failure_is_reported_and_not_retried_again(self, fake, tmp_path):
        client = fake(BAD, BAD, BAD, kit.answer(side="left"), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [f[0] for f in report.failures], report.transcripts, report.facts_done) == (3, ["range_fact"], [], 0)

    def test_a_failed_fact_leaves_no_base_file(self, fake, tmp_path):
        fake(BAD, BAD, BAD)
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "bases") == []

    def test_a_failed_fact_does_not_stop_the_next_one(self, fake, tmp_path):
        fake(BAD, BAD, BAD, *good_pair_script())
        report = run([kit.range_fact(), kit.law_fact()], tmp_path)
        assert ([f[0] for f in report.failures], [t["id"] for t in report.transcripts][:1], report.facts_done) == (
            ["range_fact"], ["law_fact_left_true"], 1,
        )

    def test_a_good_left_base_is_kept_when_the_right_one_fails(self, fake, tmp_path):
        fake(kit.answer(side="left"), BAD, BAD, BAD)
        report = run([kit.range_fact()], tmp_path)
        assert (kit.files_under(tmp_path / "bases"), len(report.failures)) == (["range_fact_left.json"], 1)


class TestPairCheck:
    def test_a_right_base_of_the_wrong_length_is_regenerated_once(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        # The attempt number of the regeneration is not pinned by the contract (a fresh call, or attempt 2), so it is not asserted.
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()], len(report.transcripts)) == (
            3, ["gen_v7", "mirror_v7", "mirror_v7"], 8,
        )

    def test_the_saved_right_base_is_the_regenerated_one(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        saved = json.loads((tmp_path / "bases" / "range_fact_right.json").read_text(encoding="utf-8"))
        assert saved["messages"][0]["text"] == kit.answer(side="right")["messages"][0]["text"]

    def test_the_pair_check_failure_leaves_the_left_base_saved(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        assert "range_fact_left.json" in kit.files_under(tmp_path / "bases")

    def test_two_wrong_lengths_are_reported_as_a_failure(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), short_right(), short_right(), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [f[0] for f in report.failures], report.transcripts) == (3, ["range_fact"], [])

    def test_a_pair_of_matching_lengths_needs_no_regeneration(self, fake, tmp_path):
        client = fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        assert len(client.calls) == 2


class TestBudget:
    def worst_left(self):
        return gen().estimate_call_usd(kit.range_fact(), "left")

    def test_a_limit_below_the_first_worst_case_makes_no_call(self, fake, tmp_path):
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=self.worst_left() - Decimal("0.000001"))
        assert (client.calls, kit.ledger(), report.transcripts, bool(report.stopped_reason)) == ([], [], [], True)

    def test_a_limit_equal_to_the_first_worst_case_allows_the_first_call(self, fake, tmp_path):
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=self.worst_left())
        assert len(client.calls) >= 1

    def test_it_stops_before_the_call_that_would_pass_the_limit(self, fake, tmp_path):
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=self.worst_left())
        assert (len(client.calls), bool(report.stopped_reason), report.transcripts, kit.files_under(tmp_path / "bases")) == (
            1, True, [], ["range_fact_left.json"],
        )

    def test_earlier_spend_of_this_run_counts_against_the_next_call(self, fake, tmp_path):
        # A limit equal to the worst case of the mirror call: the first call passes (its worst case is lower), but once it has
        # cost anything, spent plus the mirror's worst case is over the limit, so the mirror call must not be made.
        left = kit.answer_from(kit.base("left"))
        mirror_worst = gen().estimate_call_usd(kit.range_fact(), "right", left_base=kit.base("left"))
        assert mirror_worst >= self.worst_left()
        client = fake(left, kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path, max_usd=mirror_worst)
        # The run checks before calling: the gateway is not even asked (a refusal would leave a second ledger row).
        assert (len(client.calls), len(kit.ledger()), bool(report.stopped_reason), report.transcripts) == (1, 1, True, [])

    def test_the_stop_is_not_a_failure_of_the_fact(self, fake, tmp_path):
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=self.worst_left())
        assert report.failures == []

    def test_a_zero_limit_makes_no_call(self, fake, tmp_path):
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=0)
        assert (client.calls, bool(report.stopped_reason)) == ([], True)

    def test_a_negative_limit_is_refused(self, fake, tmp_path):
        with pytest.raises(ValueError):
            run([kit.range_fact()], tmp_path, max_usd=Decimal("-1"))

    def test_saved_bases_cost_nothing_even_with_a_zero_limit(self, fake, tmp_path):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        client = fake()
        report = run([kit.range_fact()], tmp_path, max_usd=0)
        assert (client.calls, len(report.transcripts), report.stopped_reason) == ([], 8, None)

    def test_earlier_spend_in_the_ledger_does_not_count_against_this_run(self, fake, tmp_path):
        from decimal import Decimal as D

        from moderation.models import LLMCall

        LLMCall.objects.create(
            purpose="replay", agent="x", attempt=1, model=tunables.GENERATOR_MODEL, prompt_version="seed", prompt_sha256="0" * 64,
            temperature=None, max_tokens=1, request={}, raw_response="", parsed=None, tokens_in=0, tokens_out=0,
            cache_write_tokens=0, cache_read_tokens=0, reserved_usd=D("40"), cost_usd=D("40"), latency_ms=0,
            stop_reason="", provider_request_id="", status="ok", error="", error_code="",
        )
        fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path, max_usd=D("20"))
        assert (report.calls, len(report.transcripts)) == (2, 8)

    def test_the_reported_cost_is_this_run_only(self, fake, tmp_path):
        fake(*good_pair_script())
        first = run([kit.range_fact()], tmp_path / "a")
        fake(*good_pair_script())
        second = run([kit.range_fact()], tmp_path / "b")
        assert second.cost_total == first.cost_total


class TestFailuresOfTheGateway:
    def test_switched_off_makes_no_call_and_reports_the_stop(self, fake, tmp_path, settings):
        settings.LLM_ENABLED = False
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert (client.calls, kit.ledger(), report.transcripts, bool(report.stopped_reason), kit.files_under(tmp_path)) == ([], [], [], True, [])

    def test_no_key_makes_no_call(self, fake, tmp_path, settings):
        settings.ANTHROPIC_API_KEY = ""
        client = fake(*good_pair_script())
        report = run([kit.range_fact()], tmp_path)
        assert (client.calls, bool(report.stopped_reason)) == ([], True)

    def test_a_provider_error_stops_the_run_cleanly(self, fake, tmp_path):
        from moderation.fake_llm import FakeProviderError

        client = fake(FakeProviderError(500, "api_error", "boom"), *good_pair_script())
        report = run([kit.range_fact(), kit.law_fact()], tmp_path)
        assert (len(client.calls), bool(report.stopped_reason), report.transcripts) == (1, True, [])

    def test_a_disabled_run_still_uses_saved_bases(self, fake, tmp_path, settings):
        fake(*good_pair_script())
        run([kit.range_fact()], tmp_path)
        settings.LLM_ENABLED = False
        report = run([kit.range_fact()], tmp_path)
        assert (len(report.transcripts), report.stopped_reason) == (8, None)


class TestSourceRules:
    def test_generate_never_opens_a_transaction(self):
        import ast

        tree = ast.parse((kit.ROOT / "seeding" / "generate.py").read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "atomic" not in names

    def test_the_only_gateway_call_names_the_replay_purpose(self):
        source = (kit.ROOT / "seeding" / "generate.py").read_text(encoding="utf-8")
        assert (source.count("llm.call("), source.count("purpose=PURPOSE"), 'PURPOSE = "replay"' in source) == (2, 2, True)
