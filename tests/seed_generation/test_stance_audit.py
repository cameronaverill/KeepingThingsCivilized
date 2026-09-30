"""Rulings after reading the 12-fact output: one side per message, and the stance audit (agent generator_audit) that every base
must pass. Everything runs in the per-test tmp working directory (conftest), never in the repo's generated/ folder."""
import json
import re
from decimal import Decimal

import gen_kit as kit
import pytest

from config import tunables


def gen():
    from seeding import generate

    return generate


def fact():
    return kit.range_fact()


def run(facts, tmp_path, **kwargs):
    kwargs.setdefault("max_usd", Decimal("50"))
    kwargs.setdefault("bases_dir", tmp_path / "bases")
    return gen().run_generation(facts, **kwargs)


def make(fake, side="left", labels=None, left_base=None):
    """Generate one base on `side` with the audit answering `labels` (default: passing)."""
    client = fake(kit.answer(side=side), audits=[labels] if labels is not None else None)
    result = gen().generate_base(fact(), side, left_base=left_base, session=None)
    return client, result


class TestAuditPasses:
    @pytest.mark.parametrize("side", ["left", "right"])
    def test_the_intended_stances_pass(self, fake, side):
        client, (base, _) = make(fake, side, kit.labels(side))
        assert (base["side"], len(client.audit_calls)) == (side, 1)

    def test_the_default_auto_labels_pass_for_both_sides(self, fake):
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        gen().generate_base(fact(), "left", session=None)
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        assert len(client.audit_calls) == 2

    def test_a_right_base_from_the_plain_prompt_is_audited_for_the_right_side(self, fake):
        client, (base, _) = make(fake, "right", kit.labels("right"))
        assert base["side"] == "right"

    def test_the_audit_runs_once_after_the_generation_call(self, fake):
        client, _ = make(fake)
        assert (len(client.calls), len(client.audit_calls)) == (1, 1)

    def test_the_result_is_still_the_generation_result(self, fake):
        from moderation.llm import LLMResult

        client, (base, result) = make(fake)
        assert (isinstance(result, LLMResult), result.call_id == kit.ledger()[0].pk) == (True, True)

    def test_a_base_that_fails_validation_is_not_audited(self, fake):
        from seeding import arms

        client = fake(kit.answer(last="No marker at all."))
        with pytest.raises(arms.BaseError):
            gen().generate_base(fact(), "left", session=None)
        assert client.audit_calls == []

    def test_a_disabled_gateway_makes_no_audit_call(self, fake, settings):
        from moderation.errors import LLMRefused

        settings.LLM_ENABLED = False
        client = fake(kit.answer())
        with pytest.raises(LLMRefused):
            gen().generate_base(fact(), "left", session=None)
        assert client.audit_calls == []


class TestAuditFails:
    def fails(self, fake, side, labels):
        from seeding import arms

        client = fake(kit.answer(side=side), audits=[labels])
        with pytest.raises(arms.BaseError) as caught:
            gen().generate_base(fact(), side, session=None)
        return caught.value

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("position", [0, 1, 2, 3])
    def test_a_mixed_message_fails(self, fake, side, position):
        err = self.fails(fake, side, kit.labels(side, mixed=[position]))
        assert (f"message {position + 1}" in str(err), "mixed" in str(err)) == (True, True)

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("position", [0, 1, 2, 3])
    def test_a_flipped_message_fails(self, fake, side, position):
        err = self.fails(fake, side, kit.labels(side, flip=[position]))
        assert f"message {position + 1}" in str(err)

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_a_flipped_a_message_names_participant_a(self, fake, side):
        assert "Participant A" in str(self.fails(fake, side, kit.labels(side, flip=[2])))

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_a_flipped_b_message_names_participant_b(self, fake, side):
        assert "Participant B" in str(self.fails(fake, side, kit.labels(side, flip=[3])))

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_the_other_sides_labels_fail_at_the_first_message(self, fake, side):
        other = "right" if side == "left" else "left"
        assert "message 1" in str(self.fails(fake, side, kit.labels(other)))

    def test_the_error_lists_the_labels(self, fake):
        err = self.fails(fake, "left", ["con", "pro", "pro", "pro"])
        assert "con, pro, pro, pro" in str(err)

    def test_the_first_offending_message_is_named(self, fake):
        err = self.fails(fake, "left", ["con", "con", "pro", "mixed"])
        assert "message 2" in str(err) and "message 4" not in str(err)

    @pytest.mark.parametrize("count", [3, 5])
    def test_a_wrong_number_of_labels_fails(self, fake, count):
        self.fails(fake, "left", kit.labels("left", count=count))

    def test_the_failed_base_is_attached_to_the_error(self, fake):
        err = self.fails(fake, "left", kit.labels("left", mixed=[1]))
        assert [m["text"] for m in err.base["messages"]] == [m["text"] for m in kit.answer()["messages"]]

    def test_the_failure_says_the_audit_failed(self, fake):
        assert "audit" in str(self.fails(fake, "left", kit.labels("left", mixed=[1]))).lower()

    def test_an_unknown_label_is_an_unusable_reply(self, fake):
        from moderation.errors import LLMOutputError

        fake(kit.answer(), audits=[["con", "pro", "maybe", "pro"]])
        with pytest.raises(LLMOutputError) as caught:
            gen().generate_base(fact(), "left", session=None)
        assert [m["text"] for m in caught.value.base["messages"]] == [m["text"] for m in kit.answer()["messages"]]

    def test_the_audit_is_billed_even_when_it_fails(self, fake):
        self.fails(fake, "left", kit.labels("left", mixed=[0]))
        assert len(kit.audit_ledger()) == 1


class TestTheAuditCall:
    def call(self, fake, **kwargs):
        client, _ = make(fake)
        return client.audit_calls[0]

    def test_the_ledger_row(self, fake):
        make(fake)
        (row,) = kit.audit_ledger()
        assert (row.purpose, row.agent, row.model, row.prompt_version, row.max_tokens, row.temperature, row.attempt, row.status) == (
            "replay", "generator_audit", tunables.GENERATOR_MODEL, "audit_v1", tunables.GENERATOR_AUDIT_MAX_TOKENS, None, 1, "ok",
        )

    def test_the_default_token_limit_is_three_hundred(self):
        assert tunables.GENERATOR_AUDIT_MAX_TOKENS == 300

    def test_the_token_limit_and_model_are_read_at_call_time(self, fake, tune):
        tune(GENERATOR_AUDIT_MAX_TOKENS=111, GENERATOR_MODEL="claude-haiku-4-5")
        client, _ = make(fake)
        (row,) = kit.audit_ledger()
        assert (row.max_tokens, row.model, client.audit_calls[0]["max_tokens"], client.audit_calls[0]["model"]) == (
            111, "claude-haiku-4-5", 111, "claude-haiku-4-5",
        )

    def test_the_generation_call_keeps_its_own_limit(self, fake, tune):
        tune(GENERATOR_AUDIT_MAX_TOKENS=111)
        make(fake)
        assert kit.ledger()[0].max_tokens == tunables.GENERATOR_MAX_TOKENS

    def test_the_schema_is_auditout(self, fake):
        assert self.call(fake)["output_format"] is gen().AuditOut

    def test_no_temperature_is_sent(self, fake):
        assert "temperature" not in self.call(fake)

    def test_the_attempt_follows_the_generation_attempt(self, fake):
        fake(kit.answer())
        gen().generate_base(fact(), "left", session=None, attempt=2)
        assert (kit.ledger()[0].attempt, kit.audit_ledger()[0].attempt) == (2, 2)

    def test_the_audit_has_its_own_ledger_row_after_the_generation_row(self, fake):
        make(fake)
        assert [(r.agent, r.prompt_version) for r in kit.all_ledger()] == [("generator", "gen_v7"), ("generator_audit", "audit_v1")]

    def test_the_audit_schema_holds_labels_only(self):
        assert list(gen().AuditOut.model_fields) == ["labels"]


class TestTheAuditPrompt:
    def request(self, fake, **kwargs):
        client = fake(kit.answer())
        gen().generate_base(kwargs.get("fact", fact()), "left", session=None)
        call = client.audit_calls[0]
        return kit.system_text(call), call["messages"][0]["content"]

    def test_it_states_the_topic_and_the_proposition(self, fake):
        system, _ = self.request(fake)
        assert (tunables.GENERATOR_TOPIC_TITLE in system, tunables.GENERATOR_TOPIC_PROPOSITION in system) == (True, True)

    def test_it_defines_the_three_labels(self, fake):
        system, _ = self.request(fake)
        assert [f"- {label}:" in system for label in ("pro", "con", "mixed")] == [True, True, True]

    def test_the_messages_are_numbered_with_their_text(self, fake):
        _, body = self.request(fake)
        expected = [m["text"] for m in kit.answer()["messages"][:3]]
        assert [f"Message {i}: {text}" in body for i, text in enumerate(expected, start=1)] == [True, True, True]

    def test_the_marker_is_replaced_by_a_neutral_placeholder(self, fake):
        _, body = self.request(fake)
        assert (kit.MARKER in body, "[factual sentence]" in body, body.rstrip().endswith("[factual sentence].")) == (False, True, True)

    def test_the_placeholder_is_explained_in_the_system_prompt(self, fake):
        system, _ = self.request(fake)
        assert "[factual sentence]" in system and kit.MARKER not in system

    def test_the_message_count_matches_the_base(self, fake):
        _, body = self.request(fake)
        assert re.findall(r"^Message (\d):", body, re.M) == ["1", "2", "3", "4"]

    def test_it_does_not_mention_the_claim_or_the_subject(self, fake):
        system, body = self.request(fake)
        both = (system + body).lower()
        assert ["claim" in both, "how many things of a certain kind exist" in both] == [False, False]

    def test_it_contains_no_claim_text_of_the_fact(self, fake):
        system, body = self.request(fake)
        assert [c for c in ("Between 500 and 560", "Between 550 and 616", kit.LEAD_IN) if c in system + body] == []

    def test_it_is_the_same_for_every_fact(self, fake):
        first, _ = self.request(fake)
        second, _ = self.request(fake, fact=kit.range_fact(id="other_fact", subject="something else entirely"))
        assert first == second

    def test_it_never_names_arms_or_variants(self, fake):
        system, body = self.request(fake)
        assert [w for w in ("variant", "pair_id", "planted", "seeded", "inflate", "deflate") if w in (system + body).lower()] == []

    def test_it_asks_for_no_quality_judgement(self, fake):
        system, _ = self.request(fake)
        assert "do not judge" in system.lower()


class TestTheAuditInTheRun:
    def script(self, *generation, audits=None):
        return generation, audits

    def test_a_failed_audit_is_retried_with_the_reason_as_the_hint(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="left"), kit.answer(side="right"),
                      audits=[kit.labels("left", flip=[1]), kit.labels("left")])
        report = run([fact()], tmp_path)
        second = kit.system_text(client.calls[1])
        assert ([r.attempt for r in kit.ledger()], report.failures, len(report.transcripts)) == ([1, 2, 1], [], 8)
        assert "stance audit failed" in second and "message 2" in second

    def test_the_hint_names_the_labels(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="left"), kit.answer(side="right"),
                      audits=[["con", "con", "con", "pro"], kit.labels("left")])
        run([fact()], tmp_path)
        assert "con, con, con, pro" in kit.system_text(client.calls[1])

    def test_the_audit_attempts_follow_the_generation_attempts(self, fake, tmp_path):
        fake(kit.answer(side="left"), kit.answer(side="left"), kit.answer(side="right"),
             audits=[kit.labels("left", mixed=[0]), kit.labels("left")])
        run([fact()], tmp_path)
        assert [r.attempt for r in kit.audit_ledger()] == [1, 2, 1]

    def test_the_failed_base_is_written_to_rejected_and_the_good_one_to_bases(self, fake, tmp_path):
        bad = kit.answer(side="left")
        bad["messages"][0]["text"] = "This first message is the rejected draft. " + "thing " * 20
        fake(bad, kit.answer(side="left"), kit.answer(side="right"), audits=[kit.labels("left", flip=[0]), kit.labels("left")])
        run([fact()], tmp_path)
        rejected = json.loads((tmp_path / "rejected" / "range_fact_left_1.json").read_text(encoding="utf-8"))
        saved = json.loads((tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8"))
        assert (rejected["messages"][0]["text"].startswith("This first message"), saved["messages"][0]["text"].startswith("This first")) == (True, False)

    def test_three_audit_failures_give_a_failure_and_three_rejected_files(self, fake, tmp_path):
        client = fake(*[kit.answer(side="left")] * 4, audits=[kit.labels("left", mixed=[1])] * 3)
        report = run([fact()], tmp_path)
        assert (len(client.calls), len(client.audit_calls), [f[0] for f in report.failures], report.transcripts) == (3, 3, ["range_fact"], [])
        assert (kit.files_under(tmp_path / "rejected"), kit.files_under(tmp_path / "bases")) == (
            ["range_fact_left_1.json", "range_fact_left_2.json", "range_fact_left_3.json"], [],
        )

    def test_the_failure_reason_mentions_the_audit(self, fake, tmp_path):
        fake(*[kit.answer(side="left")] * 3, audits=[kit.labels("left", mixed=[1])] * 3)
        report = run([fact()], tmp_path)
        assert "audit" in report.failures[0][1].lower()

    def test_a_right_base_failing_the_audit_is_retried_as_a_mirror_with_the_reason(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="right"), kit.answer(side="right"),
                      audits=[kit.labels("left"), kit.labels("right", flip=[0]), kit.labels("right")])
        report = run([fact()], tmp_path)
        retry = client.calls[2]
        assert (len(report.transcripts), kit.files_under(tmp_path / "rejected")) == (8, ["range_fact_right_1.json"])
        assert ("stance audit failed" in kit.system_text(retry), kit.answer(side="left")["messages"][0]["text"] in kit.request_text(retry)) == (True, True)

    def test_a_left_base_that_failed_the_audit_is_never_saved(self, fake, tmp_path):
        fake(*[kit.answer(side="left")] * 3, audits=[kit.labels("left", flip=[3])] * 3)
        run([fact()], tmp_path)
        assert kit.files_under(tmp_path / "bases") == []

    def test_an_unusable_audit_reply_uses_up_an_attempt_and_rejects_the_base(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="left"), kit.answer(side="right"),
                      audits=[["con", "pro", "maybe", "pro"], kit.labels("left")])
        report = run([fact()], tmp_path)
        assert (report.failures, kit.files_under(tmp_path / "rejected"), len(client.calls)) == ([], ["range_fact_left_1.json"], 3)

    def test_a_provider_error_in_the_audit_stops_the_run_cleanly(self, fake, tmp_path):
        from moderation.fake_llm import FakeProviderError

        client = fake(kit.answer(side="left"), audits=[FakeProviderError(500, "api_error", "boom")])
        report = run([fact()], tmp_path)
        assert (bool(report.stopped_reason), report.transcripts, kit.files_under(tmp_path / "bases")) == (True, [], [])

    def test_bases_reused_from_disk_are_not_audited_again(self, fake, tmp_path):
        fake(kit.answer(side="left"), kit.answer(side="right"))
        run([fact()], tmp_path)
        client = fake()
        run([fact()], tmp_path)
        assert (client.calls, client.audit_calls) == ([], [])

    def test_a_good_run_audits_each_base_once(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        run([fact()], tmp_path)
        assert (len(client.calls), len(client.audit_calls)) == (2, 2)

    def test_the_audit_reasons_are_never_saved_in_a_base(self, fake, tmp_path):
        fake(kit.answer(side="left"), kit.answer(side="right"))
        run([fact()], tmp_path)
        saved = json.loads((tmp_path / "bases" / "range_fact_left.json").read_text(encoding="utf-8"))
        assert sorted(saved) == ["fact_id", "messages", "prompt_version", "side"]

    def test_base_versions_are_v7_and_an_older_base_is_regenerated(self, fake, tmp_path):
        (tmp_path / "bases").mkdir()
        for side, version in (("left", "gen_v6"), ("right", "mirror_v6")):
            (tmp_path / "bases" / f"range_fact_{side}.json").write_text(json.dumps(kit.stamped(kit.base(side), version)), encoding="utf-8")
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        report = run([fact()], tmp_path)
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()], len(report.transcripts)) == (2, ["gen_v7", "mirror_v7"], 8)


class TestTheAuditCost:
    def test_the_estimate_grows_with_the_audit_token_limit(self, tune):
        low = gen().estimate_call_usd(fact(), "left")
        tune(GENERATOR_AUDIT_MAX_TOKENS=tunables.GENERATOR_AUDIT_MAX_TOKENS * 10)
        assert gen().estimate_call_usd(fact(), "left") > low

    def test_the_estimate_grows_with_the_audit_token_limit_for_a_mirror_too(self, tune):
        left = kit.base("left")
        low = gen().estimate_call_usd(fact(), "right", left_base=left)
        tune(GENERATOR_AUDIT_MAX_TOKENS=tunables.GENERATOR_AUDIT_MAX_TOKENS * 10)
        assert gen().estimate_call_usd(fact(), "right", left_base=left) > low

    def test_the_audit_part_is_exactly_the_difference(self, tune):
        from moderation import budget

        base_estimate = gen().estimate_call_usd(fact(), "left")
        tune(GENERATOR_AUDIT_MAX_TOKENS=tunables.GENERATOR_AUDIT_MAX_TOKENS + 100)
        extra = gen().estimate_call_usd(fact(), "left") - base_estimate
        # 100 more output tokens at the model's output price, nothing else
        one = budget.reservation_usd(tunables.GENERATOR_MODEL, estimated_input_tokens=0, max_tokens=100)
        assert extra == one

    def test_the_estimate_covers_the_generation_reservation_plus_a_real_audit(self, fake):
        make(fake)
        assert gen().estimate_call_usd(fact(), "left") >= kit.ledger()[0].reserved_usd + kit.audit_ledger()[0].reserved_usd

    def test_the_plan_prices_the_audit_in_every_attempt(self, tune):
        low_left, low_right = gen().plan_generation([fact()])
        tune(GENERATOR_AUDIT_MAX_TOKENS=tunables.GENERATOR_AUDIT_MAX_TOKENS * 10)
        high_left, high_right = gen().plan_generation([fact()])
        assert (high_left.worst_case_usd > low_left.worst_case_usd, high_right.worst_case_usd > low_right.worst_case_usd) == (True, True)
        assert high_left.worst_case_usd == gen().estimate_call_usd(fact(), "left") * 3

    def test_the_dry_run_total_includes_the_audit(self, fake, tune):
        fake()
        before = kit.generate_conversations("--dry-run", "--facts", "federal_agents_authority").out
        tune(GENERATOR_AUDIT_MAX_TOKENS=tunables.GENERATOR_AUDIT_MAX_TOKENS * 10)
        after = kit.generate_conversations("--dry-run", "--facts", "federal_agents_authority").out
        assert [d for d in kit.dollars_in(after) if d > 0][0] > [d for d in kit.dollars_in(before) if d > 0][0]

    def test_a_limit_just_below_the_estimate_including_the_audit_makes_no_call(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        report = run([fact()], tmp_path, max_usd=gen().estimate_call_usd(fact(), "left") - Decimal("0.000001"))
        assert (client.calls, client.audit_calls, bool(report.stopped_reason)) == ([], [], True)

    def test_a_limit_equal_to_the_estimate_allows_the_first_call(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        run([fact()], tmp_path, max_usd=gen().estimate_call_usd(fact(), "left"))
        assert len(client.calls) >= 1

    def test_the_audit_spend_counts_towards_the_next_stop(self, fake, tmp_path):
        # Limit = the mirror's worst case. After the first base (generation plus audit) has cost something, the mirror call is not made.
        mirror_worst = gen().estimate_call_usd(fact(), "right", left_base=kit.base("left"))
        assert mirror_worst >= gen().estimate_call_usd(fact(), "left")
        client = fake(kit.answer_from(kit.base("left")), kit.answer(side="right"))
        report = run([fact()], tmp_path, max_usd=mirror_worst)
        assert (len(client.calls), bool(report.stopped_reason), report.transcripts) == (1, True, [])

    def test_the_reported_cost_includes_the_audit_calls(self, fake, tmp_path):
        fake(kit.answer(side="left"), kit.answer(side="right"))
        report = run([fact()], tmp_path)
        generation_only = sum(r.cost_usd for r in kit.ledger())
        assert (report.cost_total > generation_only, report.cost_total == sum(r.cost_usd for r in kit.all_ledger())) == (True, True)

    def test_a_previous_audit_spend_does_not_count_against_a_new_run(self, fake, tmp_path):
        fake(kit.answer(side="left"), kit.answer(side="right"))
        first = run([fact()], tmp_path / "a")
        fake(kit.answer(side="left"), kit.answer(side="right"))
        second = run([fact()], tmp_path / "b")
        assert second.cost_total == first.cost_total


class TestPromptRulesOneSidePerMessage:
    def systems(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(fact(), "left", session=None)
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        return [kit.system_text(c).replace("\n", " ") for c in client.calls]

    def test_both_say_one_side_in_every_message(self, fake):
        assert [bool(re.search(r"argues ONE side in EVERY message", t)) for t in self.systems(fake)] == [True, True]

    def test_both_forbid_adopting_the_other_sides_view(self, fake):
        assert [bool(re.search(r"Never let a participant adopt the\s+other", t)) for t in self.systems(fake)] == [True, True]

    def test_both_limit_a_concession_to_an_acknowledgement(self, fake):
        assert [bool(re.search(r"concession is only an acknowledgement", t)) for t in self.systems(fake)] == [True, True]

    def test_neither_asks_for_copying_the_rhetorical_move(self, fake):
        assert ["rhetorical move" in t.lower() for t in self.systems(fake)] == [False, False]

    def test_the_mirror_says_the_participants_trade_positions(self, fake):
        assert bool(re.search(r"TRADE positions", self.systems(fake)[1]))

    def test_the_mirror_keeps_the_purpose_of_each_message(self, fake):
        assert bool(re.search(r"A opens, B rebuts, A pushes back, B closes", self.systems(fake)[1]))

    def test_the_audit_prompt_file_exists_and_names_the_labels(self):
        text = (kit.ROOT / "seeding" / "prompts" / "audit_v1.md").read_text(encoding="utf-8")
        assert all(word in text for word in ("pro", "con", "mixed", "{{TITLE}}", "{{PROPOSITION}}"))

    def test_the_audit_prompt_file_never_mentions_the_claim_or_the_marker(self):
        text = (kit.ROOT / "seeding" / "prompts" / "audit_v1.md").read_text(encoding="utf-8").lower()
        assert ["claim" in text, "[[claim]]" in text, "subject" in text] == [False, False, False]
