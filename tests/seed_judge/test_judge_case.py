"""judge_case: the prompt the judge is sent, the call it makes, its output schema, and the no-call shortcut."""
import re
from decimal import Decimal

import judge_kit as kit
import pytest
from moderation.errors import LLMOutputError
from moderation.fake_llm import FakeProviderError  # noqa: F401  (kept for readers: other errors are the gateway's business)


def sent(client, index=0):
    return kit.request_text(client.calls[index])


def user_part(client, index=0):
    call = client.calls[index]
    return "\n".join(m["content"] if isinstance(m["content"], str) else "".join(b.get("text", "") for b in m["content"])
                     for m in call["messages"])


class TestPromptContents:
    def test_it_contains_the_whole_rubric(self, fake):
        client = fake(kit.verdict())
        kit.judge(kit.make_case())
        assert kit.RUBRIC_PATH.read_text(encoding="utf-8").strip() in sent(client)

    def test_an_error_arm_prompt_contains_the_false_claim_the_true_fact_and_the_response(self, fake):
        client = fake(kit.verdict())
        case = kit.make_case(response_text="Zebra quartz says the register lists five hundred to five sixty.")
        kit.judge(case)
        text = user_part(client)
        assert (case.false_claim in text, case.true_claim in text, case.response_text in text) == (True, True, True)

    def test_a_true_arm_prompt_says_no_error_was_planted_and_shows_only_the_true_claim(self, fake):
        client = fake(kit.verdict(tag="N/A"))
        case = kit.true_case()
        kit.judge(case)
        text = user_part(client)
        assert ("no error was planted" in text.lower(), text.count(case.true_claim) >= 1, "None" not in text) == (True, True, True)

    def test_a_true_arm_prompt_does_not_present_any_false_claim(self, fake):
        client = fake(kit.verdict(tag="N/A"))
        kit.judge(kit.true_case())
        text = sent(client)
        for other in kit.RANGE_FALSE.values():
            assert other not in text

    def test_the_system_prompt_tells_the_judge_the_only_sensible_true_arm_tag_is_na(self, fake):
        client = fake(kit.verdict(tag="N/A"))
        kit.judge(kit.true_case())
        system = client.calls[0]["system"]
        system = system if isinstance(system, str) else "".join(b.get("text", "") for b in system)
        assert "N/A" in system and "unseeded_flagged" in system


class TestBlindness:
    """The judge is never given the side, level, direction, fact id, arm name, assignment or participant stances."""

    def test_two_cases_that_differ_only_in_side_level_arm_fact_and_assignment_send_the_same_request(self, fake):
        client = fake(kit.verdict(), kit.verdict())
        kit.judge(kit.make_case())
        kit.judge(kit.make_case(conversation_id="law_fact_right_l3:x", assignment="swapped", fact_id="law_fact", arm="l3",
                                side="right", level=3))
        first, second = client.calls
        assert (first["system"], first["messages"]) == (second["system"], second["messages"])

    def test_a_case_whose_response_differs_sends_a_different_request(self, fake):
        client = fake(kit.verdict(), kit.verdict())
        kit.judge(kit.make_case(response_text="One response."))
        kit.judge(kit.make_case(response_text="Another response."))
        assert kit.request_text(client.calls[0]) != kit.request_text(client.calls[1])

    def test_a_distinctive_fact_id_and_conversation_id_never_reach_the_model(self, fake):
        client = fake(kit.verdict())
        kit.judge(kit.make_case(fact_id="zzq_distinctive_fact", conversation_id="zzq_distinctive_fact_left_l2"))
        assert "zzq_distinctive" not in sent(client)

    @pytest.mark.parametrize("arm, side, level", [("l1", "left", 1), ("l2", "right", 2), ("l3", "left", 3), ("err", "right", None)])
    def test_arm_names_and_sides_do_not_appear_as_words(self, fake, arm, side, level):
        client = fake(kit.verdict())
        kit.judge(kit.make_case(arm=arm, side=side, level=level, conversation_id=f"f_{side}_{arm}"))
        text = user_part(client)
        assert re.search(rf"\b(?:{arm}|{side}|err|level)\b", text, re.I) is None

    def test_a_true_arm_request_is_also_independent_of_side_and_fact(self, fake):
        client = fake(kit.verdict(tag="N/A"), kit.verdict(tag="N/A"))
        kit.judge(kit.true_case())
        kit.judge(kit.true_case(conversation_id="law_fact_left_true", fact_id="law_fact", side="left", assignment="swapped"))
        first, second = client.calls
        assert (first["system"], first["messages"]) == (second["system"], second["messages"])


class TestTheCall:
    def test_the_ledger_row_carries_the_pinned_names(self, fake, tune):
        tune(JUDGE_MODEL_SEEDED="claude-haiku-4-5", JUDGE_SEEDED_MAX_TOKENS=321)
        client = fake(kit.verdict())
        kit.judge(kit.make_case())
        (row,) = kit.ledger()
        assert (row.purpose, row.agent, row.model, row.prompt_version, row.max_tokens, row.temperature, row.status) == (
            "judge", "seeded_judge", "claude-haiku-4-5", "sj_v1", 321, None, "ok")
        assert (client.calls[0]["model"], client.calls[0]["max_tokens"]) == ("claude-haiku-4-5", 321)

    def test_the_default_model_and_token_limit_are_the_tunables(self, fake):
        from config import tunables

        client = fake(kit.verdict())
        kit.judge(kit.make_case())
        assert (client.calls[0]["model"], client.calls[0]["max_tokens"]) == (
            tunables.JUDGE_MODEL_SEEDED, tunables.JUDGE_SEEDED_MAX_TOKENS)

    def test_the_request_asks_for_the_judge_schema(self, fake):
        client = fake(kit.verdict())
        kit.judge(kit.make_case())
        assert client.calls[0]["output_format"].__name__ == "JudgeOut"

    def test_it_makes_exactly_one_call_and_returns_the_parsed_output_and_the_result(self, fake):
        from seeding.judge import JudgeOut

        client = fake(kit.priced(kit.verdict(tag="2", unseeded=3, rationale="Wrong figure given.")))
        out, result = kit.judge(kit.make_case())
        assert (len(client.calls), out, isinstance(out, JudgeOut)) == (
            1, JudgeOut(tag="2", unseeded_flagged=3, rationale="Wrong figure given."), True)
        assert (result.parsed, result.cost_usd > 0) == (out, True)

    def test_the_judged_call_is_recorded_as_judge_spend(self, fake):
        fake(kit.priced(kit.verdict()))
        _out, result = kit.judge(kit.make_case())
        assert kit.judge_spend() == result.cost_usd


class TestOutputSchema:
    @pytest.mark.parametrize("tag", ["0", "1", "2", "3", "N/A"])
    def test_every_rubric_tag_is_accepted(self, fake, tag):
        fake(kit.verdict(tag=tag))
        out, _ = kit.judge(kit.make_case())
        assert out.tag == tag

    @pytest.mark.parametrize(
        "payload",
        [
            {"tag": "4", "unseeded_flagged": 0, "rationale": "x"},
            {"tag": 3, "unseeded_flagged": 0, "rationale": "x"},
            {"tag": "n/a", "unseeded_flagged": 0, "rationale": "x"},
            {"tag": "3", "unseeded_flagged": "1", "rationale": "x"},
            {"tag": "3", "rationale": "x"},
            {"tag": "3", "unseeded_flagged": 0},
            {"tag": "3", "unseeded_flagged": 0, "rationale": "x", "extra": 1},
        ],
    )
    def test_an_answer_outside_the_schema_is_an_output_error(self, fake, payload):
        fake(payload)
        with pytest.raises(LLMOutputError):
            kit.judge(kit.make_case())


class TestNothingPosted:
    @pytest.mark.parametrize(
        "case_factory, tag",
        [(kit.silent_case, "0"), (lambda: kit.silent_case(**{"arm": "true", "side": "right", "level": None, "is_error_arm": False,
                                                            "false_claim": None}), "N/A")],
        ids=["error arm", "true arm"],
    )
    def test_no_call_is_made_and_the_tag_is_fixed(self, fake, case_factory, tag):
        client = fake()
        out, result = kit.judge(case_factory())
        assert (client.calls, kit.ledger(), out.tag, out.unseeded_flagged, out.rationale, result.cost_usd) == (
            [], [], tag, 0, "no moderator response", Decimal("0"))

    def test_it_costs_nothing_even_with_a_zero_limit(self, fake):
        client = fake()
        report = kit.run([kit.silent_case()], max_usd=0)
        assert (client.calls, [c.conversation_id for c, _ in report.judgments], report.stopped_reason) == (
            [], ["range_fact_left_l2"], None)
