"""seeding.research_eval: the note judge (prompt, schema, no leakage), run_judging and the JSON Lines file."""
import json
from decimal import Decimal

import pytest

import seed_research_kit as kit

V = kit.judge_verdict


def judge_text(client):
    (call,) = kit.judge_calls(client)
    return kit.request_text(call), call


def user_text(call):
    return "\n".join(m["content"] if isinstance(m["content"], str) else "".join(b.get("text", "") for b in m["content"])
                     for m in call["messages"])


class TestSchema:
    def out(self, **kw):
        from seeding.research_eval import ResearchJudgeOut

        return ResearchJudgeOut(**kw)

    @pytest.mark.parametrize("tag", ["0", "1", "2", "3", "N/A"])
    def test_every_tag_is_allowed(self, tag):
        assert self.out(tag=tag, verdict="unclear", rationale="r").tag == tag

    @pytest.mark.parametrize("verdict", ["confirms_claim", "disputes_claim", "unclear"])
    def test_every_verdict_is_allowed(self, verdict):
        assert self.out(tag="1", verdict=verdict, rationale="r").verdict == verdict

    @pytest.mark.parametrize("bad", [dict(tag="9", verdict="unclear", rationale="r"),
                                     dict(tag="3", verdict="agrees", rationale="r"),
                                     dict(tag=3, verdict="unclear", rationale="r"),
                                     dict(tag="3", verdict="unclear"), dict(tag="3", rationale="r"), dict(verdict="unclear", rationale="r"),
                                     dict(tag="3", verdict="unclear", rationale="r", unseeded_flagged=0)])
    def test_bad_output_is_refused(self, bad):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self.out(**bad)


class TestPrompt:
    def test_the_judge_sees_the_claim_the_true_fact_and_the_note(self, fake):
        client = fake(kit.priced(V()))
        kit.judge_one(kit.make_case())
        text, call = judge_text(client)
        u = user_text(call)
        assert kit.RANGE_FALSE[("left", "l2")] in u and kit.RANGE_TRUE in u and kit.NOTE_TEXT in u
        assert "false claim" in u.lower()

    def test_a_true_arm_says_no_error_was_planted_and_shows_only_the_true_claim(self, fake):
        client = fake(kit.priced(V(tag="N/A", verdict="confirms_claim")))
        kit.judge_one(kit.true_case())
        _, call = judge_text(client)
        u = user_text(call)
        assert "no error was planted" in u.lower() and kit.RANGE_TRUE in u
        assert "false claim" not in u.lower() or "no error" in u.lower()
        assert kit.RANGE_FALSE[("right", "l1")] not in u

    def test_the_system_prompt_carries_the_rubric_and_the_verdict_instructions(self, fake):
        client = fake(kit.priced(V()))
        kit.judge_one(kit.make_case())
        text, call = judge_text(client)
        rubric = kit.jk.RUBRIC_PATH.read_text(encoding="utf-8").strip().splitlines()[0]
        assert rubric in text and "confirms_claim" in text and "disputes_claim" in text and "{{RUBRIC}}" not in text

    def test_the_user_message_never_names_side_level_arm_fact_or_transcript(self, fake):
        client = fake(kit.priced(V()))
        kit.judge_one(kit.make_case(conversation_id="zzconv_right_l3", fact_id="zzfact", side="right", arm="l3", level=3))
        _, call = judge_text(client)
        u = user_text(call).lower()
        for word in ("zzconv", "zzfact", "left", "right", "l3", "level", "side", "arm", "direction", "stance", "inflate",
                     "political", "assignment", "as-is"):
            assert word not in u, word

    def test_two_cases_differing_only_in_side_level_arm_fact_and_id_get_identical_requests(self, fake):
        a = kit.make_case(conversation_id="range_fact_left_l1", fact_id="range_fact", side="left", arm="l1", level=1)
        b = kit.make_case(conversation_id="other_right_l3", fact_id="other", side="right", arm="l3", level=3,
                          moderator_act_type="correct_factual_error", n_sources=0, confidence=0.1, cost_usd=Decimal("9"))
        texts = []
        for case in (a, b):
            client = fake(kit.priced(V()))
            kit.judge_one(case)
            texts.append(json.dumps({k: v for k, v in kit.judge_calls(client)[0].items() if k in ("system", "messages", "model", "max_tokens")},
                                    default=str, sort_keys=True))
        assert texts[0] == texts[1]

    def test_the_request_has_no_source_urls_nor_a_sources_block(self, fake):
        c = kit.add_conversation()
        kit.finished_research(c)
        (case,) = kit.collect_research_cases()
        client = fake(kit.priced(V()))
        kit.judge_one(case)
        _, call = judge_text(client)
        u = user_text(call)
        assert "Sources" not in u and "example.org" not in u and "example.com" not in u and kit.NOTE_TEXT in u

    def test_structured_output_agent_purpose_and_prompt_version(self, fake):
        from config import tunables
        from seeding.research_eval import ResearchJudgeOut

        client = fake(kit.priced(V()))
        kit.judge_one(kit.make_case())
        (call,) = kit.judge_calls(client)
        (row,) = kit.ledger()
        assert call["output_format"] is ResearchJudgeOut
        assert (row.purpose, row.agent, row.prompt_version, row.model) == ("judge", "research_judge", "sj_r1", tunables.JUDGE_MODEL_SEEDED)
        assert row.max_tokens == tunables.JUDGE_SEEDED_MAX_TOKENS

    def test_the_result_is_the_parsed_verdict(self, fake):
        fake(kit.priced(V(tag="2", verdict="disputes_claim", rationale="wrong figure")))
        out, result = kit.judge_one(kit.make_case())
        assert (out.tag, out.verdict, out.rationale) == ("2", "disputes_claim", "wrong figure") and result.cost_usd > 0


class TestRunJudging:
    def path(self):
        return "generated/judgments/research_x.jsonl"

    def run(self, cases, **kw):
        kw.setdefault("path", self.path())
        return kit.run_judging_cases(cases, **kw)

    def test_each_case_with_a_note_is_judged_and_written(self, fake):
        cases = [kit.make_case(conversation_id="a_left_l1", arm="l1", level=1), kit.true_case()]
        client = fake(kit.priced(V(tag="3")), kit.priced(V(tag="N/A", verdict="confirms_claim")))
        report = self.run(cases)
        rows = kit.read_jsonl(self.path())
        assert (len(client.calls), len(report.judgments), [(r["conversation_id"], r["tag"], r["verdict"]) for r in rows]) == (
            2, 2, [("a_left_l1", "3", "disputes_claim"), ("range_fact_right_true", "N/A", "confirms_claim")])

    def test_a_row_has_the_case_fields_the_judge_fields_and_the_prompt_version(self, fake):
        fake(kit.priced(V(tag="1", verdict="unclear", rationale="doubt")))
        self.run([kit.make_case()])
        (row,) = kit.read_jsonl(self.path())
        assert {"conversation_id", "assignment", "fact_id", "arm", "side", "level", "is_error_arm", "false_claim", "true_claim",
                "note_text", "n_sources", "note_words", "confidence", "run_status", "cost_usd", "moderator_act_type", "tag",
                "verdict", "rationale", "prompt_version"} <= set(row)
        assert (row["tag"], row["verdict"], row["rationale"], row["prompt_version"], row["side"], row["level"], row["note_text"]) == (
            "1", "unclear", "doubt", "sj_r1", "left", 2, kit.NOTE_TEXT)

    def test_the_file_is_json_lines(self, fake):
        fake(kit.priced(V()), kit.priced(V()))
        self.run([kit.make_case(conversation_id="a"), kit.make_case(conversation_id="b")])
        lines = open(self.path(), encoding="utf-8").read().splitlines()
        assert [json.loads(line)["conversation_id"] for line in lines] == ["a", "b"]

    def test_a_failed_note_gets_no_call_no_row_and_is_reported(self, fake):
        client = fake(kit.priced(V()))
        report = self.run([kit.failed_case(conversation_id="broken")])
        assert (client.calls, kit.files_under("generated"), report.no_note, report.judgments, kit.ledger()) == ([], [], ["broken"], [], [])

    def test_a_blank_note_gets_no_call(self, fake):
        client = fake(kit.priced(V()))
        report = self.run([kit.make_case(note_text="   \n", conversation_id="blank")])
        assert (client.calls, report.no_note) == ([], ["blank"])

    def test_already_judged_cases_are_skipped(self, fake):
        cases = [kit.make_case(conversation_id="a"), kit.make_case(conversation_id="b")]
        fake(kit.priced(V(tag="3")), kit.priced(V(tag="3")))
        self.run(cases)
        client = fake(kit.priced(V(tag="0")))
        report = self.run(cases + [kit.make_case(conversation_id="c")])
        assert (len(client.calls), report.skipped_existing, [(r["conversation_id"], r["tag"]) for r in kit.read_jsonl(self.path())]) == (
            1, 2, [("a", "3"), ("b", "3"), ("c", "0")])

    def test_skip_existing_applies_to_the_same_prompt_version_and_assignment_only(self, fake):
        fake(kit.priced(V(tag="3")))
        self.run([kit.make_case(conversation_id="a")])
        rows = kit.read_jsonl(self.path())
        rows[0]["prompt_version"] = "sj_r0"
        open(self.path(), "w").write(json.dumps(rows[0]) + "\n")
        client = fake(kit.priced(V(tag="1")))
        self.run([kit.make_case(conversation_id="a")])
        client2 = fake(kit.priced(V(tag="2")))
        self.run([kit.make_case(conversation_id="a", assignment="swapped")])
        assert (len(client.calls), len(client2.calls), len(kit.read_jsonl(self.path()))) == (1, 1, 3)

    def test_overwrite_replaces_rows_without_duplicating(self, fake):
        cases = [kit.make_case(conversation_id="a"), kit.make_case(conversation_id="b")]
        fake(kit.priced(V(tag="3")), kit.priced(V(tag="3")))
        self.run(cases)
        client = fake(kit.priced(V(tag="0")), kit.priced(V(tag="1")))
        self.run(cases, overwrite=True)
        assert (len(client.calls), [(r["conversation_id"], r["tag"]) for r in kit.read_jsonl(self.path())]) == (2, [("a", "0"), ("b", "1")])

    def test_one_retry_on_bad_output_then_a_row(self, fake):
        bad = {"tag": "9", "verdict": "unclear", "rationale": "x"}
        client = fake(kit.priced(bad), kit.priced(V(tag="2")))
        report = self.run([kit.make_case()])
        assert (len(client.calls), report.failures, [r["tag"] for r in kit.read_jsonl(self.path())]) == (2, [], ["2"])

    def test_two_bad_outputs_are_a_reported_failure_and_no_row(self, fake):
        bad = {"tag": "9", "verdict": "unclear", "rationale": "x"}
        client = fake(kit.priced(bad), kit.priced(bad), kit.priced(V()))
        report = self.run([kit.make_case(conversation_id="a"), kit.make_case(conversation_id="b")])
        assert (len(client.calls), [f[0] for f in report.failures], [r["conversation_id"] for r in kit.read_jsonl(self.path())]) == (3, ["a"], ["b"])

    def test_on_result_gets_case_verdict_and_result(self, fake):
        fake(kit.priced(V()))
        seen = []
        self.run([kit.make_case()], on_result=lambda *a: seen.append(a))
        assert len(seen) == 1 and seen[0][1].tag == "3"

    def test_llm_off_makes_no_call_and_says_so(self, fake, settings):
        client = fake(kit.priced(V()))
        settings.LLM_ENABLED = False
        report = self.run([kit.make_case()])
        assert (client.calls, kit.files_under("generated"), bool(report.stopped_reason)) == ([], [], True)

    def test_the_run_without_a_path_writes_no_file(self, fake):
        fake(kit.priced(V()))
        kit.run_judging_cases([kit.make_case()])
        assert kit.files_under("generated") == []


class TestJudgeCostCap:
    def worst(self, case):
        from seeding import research_eval

        return research_eval.estimate_call_usd(case)

    def test_the_worst_case_matches_what_a_call_reserves(self, fake):
        case = kit.make_case()
        fake(kit.priced(V()))
        kit.judge_one(case)
        assert kit.ledger()[-1].reserved_usd == self.worst(case)

    def test_nothing_to_call_costs_nothing(self):
        assert self.worst(kit.failed_case()) == 0

    def test_a_zero_cap_calls_nothing(self, fake):
        client = fake(kit.priced(V()))
        report = kit.run_judging_cases([kit.make_case()], max_usd=Decimal("0"))
        assert (client.calls, bool(report.stopped_reason)) == ([], True)

    def test_a_cap_just_under_the_worst_case_calls_nothing(self, fake):
        case = kit.make_case()
        client = fake(kit.priced(V()))
        report = kit.run_judging_cases([case], max_usd=self.worst(case) - Decimal("0.000001"))
        assert (client.calls, bool(report.stopped_reason)) == ([], True)

    def test_a_cap_equal_to_the_worst_case_allows_the_call(self, fake):
        case = kit.make_case()
        client = fake(kit.priced(V()))
        report = kit.run_judging_cases([case], max_usd=self.worst(case))
        assert (len(client.calls), report.stopped_reason) == (1, None)

    def test_the_second_call_is_stopped_when_spend_plus_its_worst_case_passes_the_cap(self, fake):
        a, b = kit.make_case(conversation_id="a"), kit.make_case(conversation_id="b")
        client = fake(kit.priced(V(), input_tokens=1000, output_tokens=100), kit.priced(V()))
        cost_one = Decimal("0")
        cap = self.worst(a) + self.worst(b) - Decimal("0.000001")
        # spend after the first call is its actual cost, far below its worst case, so the second call still fits
        report = kit.run_judging_cases([a, b], max_usd=cap)
        assert len(client.calls) == 2
        tight = self.worst(a) + Decimal("0.000001")
        client = fake(kit.priced(V(), input_tokens=1000, output_tokens=100), kit.priced(V()))
        report = kit.run_judging_cases([kit.make_case(conversation_id="c"), kit.make_case(conversation_id="d")], max_usd=tight)
        assert len(client.calls) == 1 and report.stopped_reason

    def test_the_cap_counts_only_this_runs_spend(self, fake):
        case = kit.make_case()
        fake(kit.priced(V()))
        kit.judge_one(case)  # earlier spend in the ledger
        client = fake(kit.priced(V()))
        report = kit.run_judging_cases([kit.make_case(conversation_id="again")], max_usd=self.worst(case))
        assert len(client.calls) == 1
