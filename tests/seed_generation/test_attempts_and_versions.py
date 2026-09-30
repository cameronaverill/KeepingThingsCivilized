"""Ruling after run 3 and 2: GENERATOR_MAX_ATTEMPTS tries with the reason as the hint, rejected files numbered by attempt,
plan prices all attempts; prompt_version stored in a base and a base of another version regenerated."""
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


NO_MARKER = kit.answer(last="There is no marker in this one.")
BANNED = kit.answer(last="I hold that the view is widespread here. [[CLAIM]].")
DIGIT = kit.answer(last="I recall 7 cases. [[CLAIM]].")
SHORT = kit.answer_from(kit.with_lengths("right", [30, 30, 30, 60]))


def good():
    return [kit.answer(side="left"), kit.answer(side="right")]


class TestTheTunable:
    def test_the_default_is_three(self):
        assert tunables.GENERATOR_MAX_ATTEMPTS == 3


class TestAttempts:
    def test_a_third_try_can_succeed(self, fake, tmp_path):
        client = fake(NO_MARKER, NO_MARKER, *good())
        report = run([kit.range_fact()], tmp_path)
        assert ([r.attempt for r in kit.ledger()], len(client.calls), report.failures, len(report.transcripts)) == ([1, 2, 3, 1], 4, [], 8)

    def test_three_failures_stop_at_three_calls(self, fake, tmp_path):
        client = fake(NO_MARKER, NO_MARKER, NO_MARKER, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [r.attempt for r in kit.ledger()], [f[0] for f in report.failures], report.transcripts) == (
            3, [1, 2, 3], ["range_fact"], [],
        )

    def test_the_rejected_files_are_numbered_by_attempt(self, fake, tmp_path):
        fake(NO_MARKER, BANNED, DIGIT)
        run([kit.range_fact()], tmp_path)
        assert [
            [m["text"] for m in json.loads((tmp_path / "rejected" / f"range_fact_left_{n}.json").read_text(encoding="utf-8"))["messages"]][-1]
            for n in (1, 2, 3)
        ] == [
            "There is no marker in this one.", "I hold that the view is widespread here. [[CLAIM]].", "I recall 7 cases. [[CLAIM]].",
        ]

    def test_the_rejected_files_of_a_succeeding_run_stop_before_the_good_attempt(self, fake, tmp_path):
        fake(NO_MARKER, NO_MARKER, *good())
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "rejected") == ["range_fact_left_1.json", "range_fact_left_2.json"]

    def test_the_attempt_limit_is_read_from_the_tunables(self, fake, tmp_path, tune):
        tune(GENERATOR_MAX_ATTEMPTS=1)
        client = fake(NO_MARKER, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), len(report.failures)) == (1, 1)

    def test_two_attempts_when_the_tunable_says_two(self, fake, tmp_path, tune):
        tune(GENERATOR_MAX_ATTEMPTS=2)
        client = fake(NO_MARKER, NO_MARKER, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), len(report.failures)) == (2, 1)

    def test_four_attempts_when_the_tunable_says_four(self, fake, tmp_path, tune):
        tune(GENERATOR_MAX_ATTEMPTS=4)
        client = fake(NO_MARKER, NO_MARKER, NO_MARKER, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), report.failures) == (5, [])

    def test_the_right_base_gets_three_attempts_too(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), NO_MARKER, NO_MARKER, kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert ([r.attempt for r in kit.ledger()], report.failures, len(report.transcripts)) == ([1, 1, 2, 3], [], 8)

    def test_a_schema_invalid_reply_uses_up_an_attempt(self, fake, tmp_path):
        bad = {"messages": [{"author": "Somebody Else", "text": "Hi."}]}
        client = fake(bad, bad, bad, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), len(report.failures)) == (3, 1)

    def test_a_stop_from_the_gateway_is_not_retried(self, fake, tmp_path):
        from moderation.fake_llm import FakeProviderError

        client = fake(FakeProviderError(500, "api_error", "boom"), *good())
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls) <= 1, bool(report.stopped_reason)) == (True, True)


class TestTheReasonIsTheHint:
    def prompts(self, fake, tmp_path, *script):
        client = fake(*script)
        run([kit.range_fact()], tmp_path)
        return [kit.system_text(c) for c in client.calls]

    def test_the_first_attempt_has_no_refusal_hint(self, fake, tmp_path):
        first = self.prompts(fake, tmp_path, NO_MARKER, *good())[0]
        assert "refused" not in first.lower()

    def test_the_second_attempt_carries_the_marker_reason(self, fake, tmp_path):
        second = self.prompts(fake, tmp_path, NO_MARKER, *good())[1]
        assert "exactly once" in second

    def test_the_second_attempt_names_a_banned_word(self, fake, tmp_path):
        second = self.prompts(fake, tmp_path, BANNED, *good())[1]
        assert "widespread" in second.split("ADDITIONAL NOTE")[-1] or "banned word 'widespread'" in second

    def test_the_second_attempt_carries_the_digit_reason(self, fake, tmp_path):
        second = self.prompts(fake, tmp_path, DIGIT, *good())[1]
        assert "digit" in second.split("refused")[-1].lower()

    def test_the_third_attempt_carries_the_reason_of_the_second(self, fake, tmp_path):
        third = self.prompts(fake, tmp_path, NO_MARKER, BANNED, *good())[2]
        tail = third.split("refused")[-1]
        assert ("widespread" in tail, "exactly once" in tail) == (True, False)

    def test_the_hint_differs_between_attempts(self, fake, tmp_path):
        prompts = self.prompts(fake, tmp_path, NO_MARKER, BANNED, *good())
        assert len({prompts[0], prompts[1], prompts[2]}) == 3

    def test_a_right_base_retry_carries_the_reason_and_still_contains_the_left_base(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), NO_MARKER, kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        second_mirror = kit.request_text(client.calls[2])
        assert ("exactly once" in second_mirror, kit.answer(side="left")["messages"][0]["text"] in second_mirror) == (True, True)

    def test_a_pair_check_failure_passes_the_reason_to_the_regeneration(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), SHORT, kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert "differs in length" in kit.system_text(client.calls[2])

    def test_a_similarity_failure_passes_the_reason_to_the_regeneration(self, fake, tmp_path):
        copy = kit.answer_from(kit.edited(kit.base("left"), side="right"))
        client = fake(kit.answer(side="left"), copy, kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert ("similar" in kit.system_text(client.calls[2]), len(report.transcripts)) == (True, 8)


class TestRightBasePairFailures:
    def test_the_similarity_failure_is_rejected_not_saved(self, fake, tmp_path):
        copy = kit.answer_from(kit.edited(kit.base("left"), side="right"))
        fake(kit.answer(side="left"), copy, kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "rejected") == ["range_fact_right_1.json"]

    def test_persistent_pair_failures_give_a_failure_and_no_right_base(self, fake, tmp_path):
        copy = kit.answer_from(kit.edited(kit.base("left"), side="right"))
        fake(kit.answer(side="left"), *([copy] * 8))
        report = run([kit.range_fact()], tmp_path)
        assert (len(report.failures), kit.files_under(tmp_path / "bases"), report.transcripts) == (1, ["range_fact_left.json"], [])
        assert len(kit.files_under(tmp_path / "rejected")) >= 2


class TestPlanPricesAllAttempts:
    def test_an_item_costs_three_single_calls(self):
        fact = kit.range_fact()
        left, right = gen().plan_generation([fact])
        assert (left.worst_case_usd, right.worst_case_usd) == (
            gen().estimate_call_usd(fact, "left") * 3, gen().estimate_call_usd(fact, "right") * 3,
        )

    def test_the_price_follows_the_attempt_tunable(self, tune):
        fact = kit.range_fact()
        tune(GENERATOR_MAX_ATTEMPTS=1)
        left, right = gen().plan_generation([fact])
        assert (left.worst_case_usd, right.worst_case_usd) == (gen().estimate_call_usd(fact, "left"), gen().estimate_call_usd(fact, "right"))

    def test_five_attempts_cost_five_times(self, tune):
        fact = kit.range_fact()
        tune(GENERATOR_MAX_ATTEMPTS=5)
        assert gen().plan_generation([fact])[0].worst_case_usd == gen().estimate_call_usd(fact, "left") * 5

    def test_the_command_dry_run_total_follows_the_attempts(self, fake, tune):
        fake()
        three = kit.generate_conversations("--dry-run", "--facts", "federal_agents_authority")
        tune(GENERATOR_MAX_ATTEMPTS=1)
        one = kit.generate_conversations("--dry-run", "--facts", "federal_agents_authority")
        first = [d for d in kit.dollars_in(three.out) if d > 0]
        second = [d for d in kit.dollars_in(one.out) if d > 0]
        assert (first[0] > second[0], abs(first[0] - 3 * second[0]) < Decimal("0.001")) == (True, True)


class TestPromptVersionInTheBase:
    def test_a_generated_left_base_records_gen_v7(self, fake):
        fake(kit.answer())
        base, _ = gen().generate_base(kit.range_fact(), "left", session=None)
        assert base["prompt_version"] == "gen_v7"

    def test_a_generated_mirror_base_records_mirror_v7(self, fake):
        fake(kit.answer(side="right"))
        base, _ = gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        assert base["prompt_version"] == "mirror_v7"

    def test_a_right_base_from_the_plain_prompt_records_gen_v7(self, fake):
        fake(kit.answer(side="right"))
        base, _ = gen().generate_base(kit.range_fact(), "right", session=None)
        assert base["prompt_version"] == "gen_v7"

    def test_the_ledger_and_the_base_agree(self, fake):
        fake(kit.answer(side="right"))
        base, _ = gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        assert kit.ledger()[0].prompt_version == base["prompt_version"]

    def test_validate_base_ignores_the_extra_key(self):
        from seeding import arms

        assert arms.validate_base(kit.stamped(kit.base("left"))) is None

    def test_the_saved_files_carry_the_versions(self, fake, tmp_path):
        fake(*good())
        run([kit.range_fact()], tmp_path)
        saved = [json.loads((tmp_path / "bases" / f"range_fact_{s}.json").read_text(encoding="utf-8"))["prompt_version"] for s in ("left", "right")]
        assert saved == ["gen_v7", "mirror_v7"]

    def test_transcripts_do_not_carry_the_version(self, fake, tmp_path):
        fake(*good())
        report = run([kit.range_fact()], tmp_path)
        assert ["prompt_version" in t for t in report.transcripts] == [False] * 8


class TestASavedBaseOfAnotherVersion:
    def write(self, tmp_path, side, base):
        (tmp_path / "bases").mkdir(exist_ok=True)
        (tmp_path / "bases" / f"range_fact_{side}.json").write_text(json.dumps(base), encoding="utf-8")

    def saved_pair(self, tmp_path, left_version=None, right_version=None):
        self.write(tmp_path, "left", kit.stamped(kit.base("left"), left_version))
        self.write(tmp_path, "right", kit.stamped(kit.base("right"), right_version))

    def test_current_versions_are_reused(self, fake, tmp_path):
        self.saved_pair(tmp_path)
        client = fake()
        report = run([kit.range_fact()], tmp_path)
        assert (client.calls, report.bases_reused, len(report.transcripts)) == ([], 2, 8)

    def test_an_older_left_base_is_regenerated(self, fake, tmp_path):
        self.saved_pair(tmp_path, left_version="gen_v5")
        client = fake(kit.answer(side="left"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()], report.failures, len(report.transcripts)) == (1, ["gen_v7"], [], 8)

    def test_a_left_base_without_a_version_is_regenerated(self, fake, tmp_path):
        self.saved_pair(tmp_path)
        unversioned = kit.base("left")
        self.write(tmp_path, "left", unversioned)
        client = fake(kit.answer(side="left"))
        run([kit.range_fact()], tmp_path)
        assert [r.prompt_version for r in kit.ledger()] == ["gen_v7"]

    def test_the_regenerated_left_base_is_saved_with_the_current_version(self, fake, tmp_path):
        self.saved_pair(tmp_path, left_version="gen_v1")
        fake(kit.answer(side="left"))
        run([kit.range_fact()], tmp_path)
        saved = json.loads((tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8"))
        assert saved["prompt_version"] == "gen_v7"

    def test_an_older_right_base_is_regenerated_and_the_left_is_reused(self, fake, tmp_path):
        self.saved_pair(tmp_path, right_version="mirror_v5")
        client = fake(kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()], len(report.transcripts)) == (1, ["mirror_v7"], 8)

    def test_a_right_base_with_the_left_prompt_version_is_regenerated(self, fake, tmp_path):
        self.saved_pair(tmp_path, right_version="gen_v7")
        client = fake(kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert [r.prompt_version for r in kit.ledger()] == ["mirror_v7"]

    def test_a_regenerated_left_base_forces_a_matching_right_check(self, fake, tmp_path):
        self.saved_pair(tmp_path, left_version="gen_v5")
        new_left = kit.answer_from(kit.with_message(kit.base("left"), 0, text="A wholly different opening turn. " + "thing " * 24))
        client = fake(new_left)
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), len(report.transcripts)) == (1, 8)

    def test_overwrite_ignores_the_version_and_regenerates_both(self, fake, tmp_path):
        self.saved_pair(tmp_path)
        client = fake(*good())
        run([kit.range_fact()], tmp_path, overwrite=True)
        assert len(client.calls) == 2

    def test_an_old_version_is_not_a_failure(self, fake, tmp_path):
        self.saved_pair(tmp_path, left_version="gen_v2", right_version="mirror_v2")
        fake(*good())
        assert run([kit.range_fact()], tmp_path).failures == []
