"""seeding.generate.generate_base and BaseOut: the prompt, the gateway call, and what an unusable reply does."""
import re

import gen_kit as kit
import pytest
from pydantic import ValidationError

from config import tunables

FACT_TOKENS = dict(input_tokens=1000, output_tokens=100)


def gen():
    from seeding import generate

    return generate


def fact():
    return kit.range_fact()


def false_claims_of(a_fact):
    from seeding.seeds import build_seeds

    return [seed.false_claim for seed in build_seeds(a_fact)]


class TestBaseOut:
    def test_a_valid_reply_parses(self):
        parsed = gen().BaseOut.model_validate(kit.answer())
        assert [m.author for m in parsed.messages] == ["Participant A", "Participant B", "Participant A", "Participant B"]

    def test_an_unknown_author_is_refused(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({"messages": [{"author": "Bob", "text": "Hello."}]})

    def test_an_extra_field_on_a_message_is_refused(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({"messages": [{"author": "Participant A", "text": "Hello.", "seq": 1}]})

    def test_an_extra_top_level_field_is_refused(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({"messages": [], "note": "hi"})

    def test_a_missing_text_is_refused(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({"messages": [{"author": "Participant A"}]})

    def test_messages_have_no_default(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({})

    def test_a_number_is_not_accepted_as_text(self):
        with pytest.raises(ValidationError):
            gen().BaseOut.model_validate({"messages": [{"author": "Participant A", "text": 5}]})


class TestTheBaseReturned:
    def test_a_left_base(self, fake):
        script = kit.answer()
        fake(script)
        base, result = gen().generate_base(fact(), "left", session=None)
        assert base == {
            "side": "left", "fact_id": "range_fact",
            "messages": [{"seq": i, "author": m["author"], "text": m["text"]} for i, m in enumerate(script["messages"], start=1)],
        }

    def test_the_second_item_is_the_gateway_result(self, fake):
        from moderation.llm import LLMResult

        fake(kit.answer())
        _, result = gen().generate_base(fact(), "left", session=None)
        assert isinstance(result, LLMResult)

    def test_a_right_base_has_the_right_side(self, fake):
        fake(kit.answer(side="right"))
        base, _ = gen().generate_base(fact(), "right", session=None)
        assert (base["side"], base["fact_id"]) == ("right", "range_fact")

    def test_a_six_message_reply_is_accepted(self, fake):
        fake(kit.answer(count=6))
        base, _ = gen().generate_base(fact(), "left", session=None)
        assert [m["seq"] for m in base["messages"]] == [1, 2, 3, 4, 5, 6]

    def test_the_fact_id_is_the_fact_s(self, fake):
        fake(kit.answer())
        base, _ = gen().generate_base(kit.range_fact(id="another_fact"), "left", session=None)
        assert base["fact_id"] == "another_fact"


class TestTheGatewayCall:
    def test_the_ledger_row_of_a_left_base(self, fake):
        fake(kit.answer())
        gen().generate_base(fact(), "left", session=None)
        (row,) = kit.ledger()
        assert (row.purpose, row.agent, row.model, row.prompt_version, row.max_tokens, row.temperature, row.attempt, row.status) == (
            "replay", "generator", tunables.GENERATOR_MODEL, "gen_v1", tunables.GENERATOR_MAX_TOKENS, None, 1, "ok",
        )

    def test_a_mirror_call_is_versioned_mirror_v1(self, fake):
        fake(kit.answer(side="right"))
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        (row,) = kit.ledger()
        assert (row.purpose, row.agent, row.prompt_version) == ("replay", "generator", "mirror_v1")

    def test_a_right_base_without_a_left_base_uses_the_generator_prompt(self, fake):
        fake(kit.answer(side="right"))
        gen().generate_base(fact(), "right", session=None)
        assert kit.ledger()[0].prompt_version == "gen_v1"

    def test_a_left_side_never_uses_the_mirror_prompt_even_with_a_left_base(self, fake):
        fake(kit.answer())
        gen().generate_base(fact(), "left", left_base=kit.base("left"), session=None)
        assert kit.ledger()[0].prompt_version == "gen_v1"

    def test_the_model_and_the_token_limit_are_read_at_call_time(self, fake, tune):
        tune(GENERATOR_MODEL="claude-haiku-4-5", GENERATOR_MAX_TOKENS=1234)
        client = fake(kit.answer())
        gen().generate_base(fact(), "left", session=None)
        (row,) = kit.ledger()
        assert (row.model, row.max_tokens, client.calls[0]["model"], client.calls[0]["max_tokens"]) == (
            "claude-haiku-4-5", 1234, "claude-haiku-4-5", 1234,
        )

    def test_the_structured_output_schema_is_baseout(self, fake):
        client = fake(kit.answer())
        gen().generate_base(fact(), "left", session=None)
        assert client.calls[0]["output_format"] is gen().BaseOut

    def test_no_temperature_is_sent(self, fake):
        client = fake(kit.answer())
        gen().generate_base(fact(), "left", session=None)
        assert "temperature" not in client.calls[0]

    def test_the_attempt_number_is_recorded(self, fake):
        fake(kit.answer())
        gen().generate_base(fact(), "left", session=None, attempt=2)
        assert kit.ledger()[0].attempt == 2

    def test_exactly_one_call_is_made(self, fake):
        client = fake(kit.answer())
        gen().generate_base(fact(), "left", session=None)
        assert len(client.calls) == 1

    def test_the_session_budget_is_charged(self, fake):
        from decimal import Decimal

        from moderation import budget

        fake(kit.answer())
        session = budget.SessionBudget(Decimal("5"))
        gen().generate_base(fact(), "left", session=session)
        assert session.spent > 0

    def test_a_disabled_gateway_refuses_and_nothing_is_sent(self, fake, settings):
        from moderation.errors import LLMRefused

        settings.LLM_ENABLED = False
        client = fake(kit.answer())
        with pytest.raises(LLMRefused):
            gen().generate_base(fact(), "left", session=None)
        assert client.calls == []


class TestThePromptSent:
    def left_call(self, fake, **kwargs):
        client = fake(kit.answer())
        gen().generate_base(fact(), "left", session=None, **kwargs)
        return client.calls[0]

    def test_it_states_the_topic_and_the_proposition(self, fake):
        text = kit.request_text(self.left_call(fake))
        assert (tunables.GENERATOR_TOPIC_TITLE in text, tunables.GENERATOR_TOPIC_PROPOSITION in text) == (True, True)

    def test_the_topic_is_read_at_call_time(self, fake, tune):
        tune(GENERATOR_TOPIC_TITLE="A brand new title", GENERATOR_TOPIC_PROPOSITION="A brand new proposition.")
        text = kit.request_text(self.left_call(fake))
        assert ("A brand new title" in text, "A brand new proposition." in text, tunables.GENERATOR_TOPIC_TITLE in text) == (True, True, True)

    def test_it_names_both_participants_and_the_marker(self, fake):
        text = kit.system_text(self.left_call(fake))
        assert ("Participant A" in text, "Participant B" in text, kit.MARKER in text) == (True, True, True)

    def test_it_says_the_marker_appears_once(self, fake):
        assert re.search(r"\bonce\b", kit.system_text(self.left_call(fake)).lower())

    def test_it_says_there_are_no_digits(self, fake):
        assert "digit" in kit.system_text(self.left_call(fake)).lower()

    def test_it_states_the_framing_and_the_kind_of_fact(self, fake):
        text = kit.request_text(self.left_call(fake))
        assert ("Cited as evidence that the policy is widespread" in text, "Between 500 and 560 things exist" in text) == (True, True)

    def test_a_fact_without_a_framing_still_builds_a_prompt(self, fake):
        client = fake(kit.answer())
        gen().generate_base(kit.range_fact(framing=None), "left", session=None)
        assert kit.MARKER in kit.request_text(client.calls[0])

    @pytest.mark.parametrize("word", ["variant", "pair_id", "planted", "seeded", "inflate", "deflate", "false claim", "one-sided", "biased"])
    def test_it_never_contains_arm_or_bias_words(self, fake, word):
        assert word not in kit.request_text(self.left_call(fake)).lower()

    def test_it_never_contains_any_false_claim_of_the_fact(self, fake):
        text = kit.request_text(self.left_call(fake))
        assert [c for c in false_claims_of(fact()) if c in text] == []

    def test_it_never_contains_a_non_statistic_error_claim(self, fake):
        client = fake(kit.answer())
        law = kit.law_fact()
        gen().generate_base(law, "left", session=None)
        text = kit.request_text(client.calls[0])
        assert [c for c in false_claims_of(law) if c in text] == []

    def test_a_hint_appears_when_given_and_not_otherwise(self, fake):
        with_hint = kit.request_text(self.left_call(fake, conversation_hint="Please keep the paragraphs short."))
        assert "Please keep the paragraphs short." in with_hint

    def test_no_hint_text_appears_by_default(self, fake):
        assert "Please keep the paragraphs short." not in kit.request_text(self.left_call(fake))

    def test_left_and_right_prompts_differ_only_in_the_two_position_lines(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(fact(), "left", session=None)
        gen().generate_base(fact(), "right", session=None)
        left, right = (kit.system_text(c).splitlines() for c in client.calls)
        differing = [line for line in left if line not in right]
        assert (len(differing), all("Participant" in line for line in differing), len(left) == len(right)) == (2, True, True)

    def test_the_two_position_lines_swap_between_the_sides(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(fact(), "left", session=None)
        gen().generate_base(fact(), "right", session=None)
        left, right = (kit.system_text(c).splitlines() for c in client.calls)
        left_only = [line for line in left if line not in right]
        right_only = [line for line in right if line not in left]
        strip = lambda line: line.replace("Participant A", "P").replace("Participant B", "P")  # noqa: E731
        assert (strip(left_only[0]), strip(left_only[1])) == (strip(right_only[1]), strip(right_only[0]))


class TestThePositions:
    """Left base: Participant B argues the left-coded (pro) side and A the other; right base: swapped (brief, Concepts)."""

    PRO = r"(favou?r|support|\bfor\b|\bpro\b|agree)"
    CON = r"(against|oppos|\bcon\b|disagree)"

    def lines(self, fake, side, left_base=None):
        client = fake(kit.answer(side=side))
        gen().generate_base(fact(), side, left_base=left_base, session=None)
        text = kit.system_text(client.calls[0])
        import re

        a = [l for l in text.splitlines() if "Participant A" in l and re.search(self.PRO + "|" + self.CON, l, re.I) and "argue" in l]
        b = [l for l in text.splitlines() if "Participant B" in l and re.search(self.PRO + "|" + self.CON, l, re.I) and "argue" in l]
        return a[0], b[0]

    def test_left_base_b_is_pro_and_a_is_con(self, fake):
        import re

        a, b = self.lines(fake, "left")
        assert (bool(re.search(self.PRO, b, re.I)), bool(re.search(self.CON, a, re.I))) == (True, True)

    def test_right_base_b_is_con_and_a_is_pro(self, fake):
        import re

        a, b = self.lines(fake, "right")
        assert (bool(re.search(self.CON, b, re.I)), bool(re.search(self.PRO, a, re.I))) == (True, True)

    def test_the_mirror_prompt_gives_the_right_base_positions(self, fake):
        import re

        a, b = self.lines(fake, "right", left_base=kit.base("left"))
        assert (bool(re.search(self.CON, b, re.I)), bool(re.search(self.PRO, a, re.I))) == (True, True)


class TestTheMirrorPrompt:
    def mirror_call(self, fake, left=None, **kwargs):
        left = left or kit.base("left")
        client = fake(kit.answer(side="right"))
        gen().generate_base(fact(), "right", left_base=left, session=None, **kwargs)
        return left, client.calls[0]

    def test_every_message_of_the_left_base_is_in_the_request(self, fake):
        left, call = self.mirror_call(fake)
        text = kit.request_text(call)
        assert [m["text"] for m in left["messages"] if m["text"] not in text] == []

    def test_a_distinctive_left_text_is_carried_over(self, fake):
        left = kit.with_message(kit.base("left"), 1, text="Zebrafish quartz sentence for the mirror to copy the structure of.")
        _, call = self.mirror_call(fake, left)
        assert "Zebrafish quartz sentence" in kit.request_text(call)

    def test_the_left_base_is_not_in_a_generator_prompt(self, fake):
        client = fake(kit.answer(side="right"))
        gen().generate_base(fact(), "right", session=None)
        assert "Zebrafish quartz" not in kit.request_text(client.calls[0])

    def test_the_mirror_system_prompt_differs_from_the_generator_one(self, fake):
        client = fake(kit.answer(side="right"), kit.answer(side="right"))
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        gen().generate_base(fact(), "right", session=None)
        assert kit.system_text(client.calls[0]) != kit.system_text(client.calls[1])

    def test_it_states_topic_marker_and_no_digits(self, fake):
        _, call = self.mirror_call(fake)
        text = kit.system_text(call)
        assert (tunables.GENERATOR_TOPIC_PROPOSITION in text, kit.MARKER in text, "digit" in text.lower(), bool(re.search(r"\bonce\b", text.lower()))) == (
            True, True, True, True,
        )

    @pytest.mark.parametrize("word", ["variant", "pair_id", "planted", "seeded", "inflate", "deflate", "false claim", "one-sided", "biased"])
    def test_it_never_contains_arm_or_bias_words(self, fake, word):
        _, call = self.mirror_call(fake)
        assert word not in kit.request_text(call).lower()

    def test_it_never_contains_any_false_claim(self, fake):
        _, call = self.mirror_call(fake)
        text = kit.request_text(call)
        assert [c for c in false_claims_of(fact()) if c in text] == []

    def test_the_positions_are_those_of_the_right_base(self, fake):
        client = fake(kit.answer(side="right"), kit.answer(side="right"))
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        gen().generate_base(fact(), "right", session=None)
        mirror = [line for line in kit.system_text(client.calls[0]).splitlines() if "Participant" in line and "argues" in line]
        plain = [line for line in kit.system_text(client.calls[1]).splitlines() if "Participant" in line and "argues" in line]
        assert (len(mirror), mirror) == (2, plain)


class TestUnusableReplies:
    def bad(self, fake, payload):
        from seeding import arms

        client = fake(payload)
        with pytest.raises(arms.BaseError):
            gen().generate_base(fact(), "left", session=None)
        return client

    def test_no_marker(self, fake):
        self.bad(fake, kit.answer(last="There is nothing here to replace."))

    def test_two_markers(self, fake):
        self.bad(fake, kit.answer(last="One. [[CLAIM]]. Two. [[CLAIM]]. Done."))

    def test_a_marker_in_the_middle_of_a_sentence(self, fake):
        self.bad(fake, kit.answer(last="I think that [[CLAIM]]."))

    def test_a_digit(self, fake):
        self.bad(fake, kit.answer(last="I saw 3 cases. [[CLAIM]]."))

    def test_too_few_messages(self, fake):
        self.bad(fake, kit.answer(count=2))

    def test_too_many_messages(self, fake):
        self.bad(fake, kit.answer(count=8))

    def test_a_reply_that_starts_with_participant_b(self, fake):
        payload = kit.answer()
        payload["messages"] = payload["messages"][1:] + payload["messages"][:1]
        self.bad(fake, payload)

    def test_an_empty_message(self, fake):
        payload = kit.answer()
        payload["messages"][1]["text"] = " "
        self.bad(fake, payload)

    def test_the_failed_call_is_still_in_the_ledger_once(self, fake):
        client = self.bad(fake, kit.answer(last="No marker."))
        assert (len(client.calls), len(kit.ledger())) == (1, 1)

    def test_a_reply_that_does_not_fit_the_schema_raises_the_gateway_error(self, fake):
        from moderation.errors import LLMOutputError

        fake({"messages": [{"author": "Somebody Else", "text": "Hi."}]})
        with pytest.raises(LLMOutputError):
            gen().generate_base(fact(), "left", session=None)


class TestEstimate:
    def test_it_equals_what_the_gateway_reserves_for_a_left_call(self, fake):
        from decimal import Decimal

        fake(kit.answer())
        estimate = gen().estimate_call_usd(fact(), "left")
        gen().generate_base(fact(), "left", session=None)
        assert (isinstance(estimate, Decimal), estimate > 0, estimate) == (True, True, kit.ledger()[0].reserved_usd)

    def test_it_equals_what_the_gateway_reserves_for_a_mirror_call(self, fake):
        left = kit.base("left")
        fake(kit.answer(side="right"))
        estimate = gen().estimate_call_usd(fact(), "right", left_base=left)
        gen().generate_base(fact(), "right", left_base=left, session=None)
        assert estimate == kit.ledger()[0].reserved_usd

    def test_a_mirror_estimate_is_dearer_than_a_left_one(self):
        assert gen().estimate_call_usd(fact(), "right", left_base=kit.base("left")) > gen().estimate_call_usd(fact(), "left")

    def test_the_estimate_of_a_right_base_without_a_left_base_covers_a_real_mirror_call(self, fake):
        fake(kit.answer(side="right"))
        gen().generate_base(fact(), "right", left_base=kit.base("left"), session=None)
        assert gen().estimate_call_usd(fact(), "right") >= kit.ledger()[0].reserved_usd

    def test_a_longer_left_base_costs_more(self):
        short = kit.base("left")
        long = kit.with_message(short, 0, text="word " * 500)
        assert gen().estimate_call_usd(fact(), "right", left_base=long) > gen().estimate_call_usd(fact(), "right", left_base=short)

    def test_more_output_allowance_costs_more(self, tune):
        low = gen().estimate_call_usd(fact(), "left")
        tune(GENERATOR_MAX_TOKENS=tunables.GENERATOR_MAX_TOKENS * 2)
        assert gen().estimate_call_usd(fact(), "left") > low

    def test_the_price_follows_the_model_tunable(self, tune):
        sonnet = gen().estimate_call_usd(fact(), "left")
        tune(GENERATOR_MODEL="claude-haiku-4-5")
        assert gen().estimate_call_usd(fact(), "left") < sonnet

    def test_an_unpriced_model_is_refused(self, tune):
        from moderation.errors import LLMRefused

        tune(GENERATOR_MODEL="no-such-model")
        with pytest.raises(LLMRefused):
            gen().estimate_call_usd(fact(), "left")
