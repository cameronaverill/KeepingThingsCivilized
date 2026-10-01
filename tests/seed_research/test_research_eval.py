"""seeding.research_eval: eligibility, planning, running the research step under a cap, collecting notes."""
from decimal import Decimal

import pytest

import research_kit as kit


def ids(items):
    return sorted(kit.transcript_id(i) for i in items)


# --- eligible_acts ----------------------------------------------------------------------------------------------------

class TestEligibility:
    @pytest.mark.parametrize("act_type", kit.ELIGIBLE)
    def test_each_eligible_type_counts(self, act_type):
        kit.add_conversation(acts=[(act_type, "text")])
        assert len(kit.eligible()[0]) == 1

    @pytest.mark.parametrize("act_type", kit.NOT_ELIGIBLE)
    def test_other_types_do_not(self, act_type):
        kit.add_conversation(acts=[(act_type, "text")])
        assert kit.eligible()[0] == []

    def test_a_conversation_without_acts_is_left_out(self):
        kit.add_conversation(acts=[])
        assert kit.eligible()[0] == []

    def test_rejected_acts_do_not_count(self):
        kit.add_conversation(acts=[], rejected_acts=["a rejected offer"])
        assert kit.eligible()[0] == []

    def test_an_unfinished_replay_run_is_left_out(self):
        kit.add_conversation(run="failed")
        kit.add_conversation(fact_id="law_fact", arm="err", run="running")
        assert kit.eligible()[0] == []

    def test_a_conversation_without_a_replay_run_is_left_out(self):
        kit.add_conversation(run=None)
        assert kit.eligible()[0] == []

    def test_one_act_per_conversation_the_first_by_act_id_and_the_rest_counted(self):
        c = kit.add_conversation(acts=[("ask_clarification", "x"), ("provide_information", "second"), ("offer_research", "third")])
        items, skipped = kit.eligible()
        expected = [a for a in kit.acts_of(c) if a.act_type != "ask_clarification"]
        assert ([kit.act_id(i) for i in items], skipped) == ([expected[0].pk], 1)

    def test_the_first_act_by_id_wins_even_if_a_later_order_is_earlier(self):
        c = kit.add_conversation(acts=[("offer_research", "one"), ("offer_research", "two")])
        first, second = kit.acts_of(c)
        # act ids, not the order field, decide
        type(first).objects.filter(pk=first.pk).update(order=9)
        items, _ = kit.eligible()
        assert [kit.act_id(i) for i in items] == [first.pk]

    def test_one_per_conversation_across_many_conversations(self):
        kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        kit.add_conversation(fact_id="law_fact", side="left", arm="err")
        items, skipped = kit.eligible()
        assert (ids(items), skipped) == (["law_fact_left_err", "range_fact_left_l1", "range_fact_right_l1"], 0)

    def test_the_parsed_fields_are_those_of_seeding_judge(self):
        from seeding import judge

        kit.add_conversation(side="right", arm="l3")
        kit.add_conversation(fact_id="law_fact", side="left", arm="err")
        kit.add_conversation(side="left", arm="true")
        cases = {c.conversation_id: c for c in judge.collect(kit.EXPERIMENT)[0]}
        items, _ = kit.eligible()
        assert len(items) == 3
        for item in items:
            case = cases[kit.transcript_id(item)]
            got = tuple(kit.pick(item, n) for n in ("fact_id", "arm", "side", "level", "is_error_arm", "false_claim", "true_claim"))
            assert got == (case.fact_id, case.arm, case.side, case.level, case.is_error_arm, case.false_claim, case.true_claim)

    def test_concrete_values(self):
        kit.add_conversation(side="right", arm="l3")
        (item,), _ = kit.eligible()
        got = tuple(kit.pick(item, n) for n in ("conversation_id", "fact_id", "arm", "side", "level", "is_error_arm"))
        assert got == ("range_fact_right_l3", "range_fact", "l3", "right", 3, True)
        assert kit.pick(item, "false_claim") == kit.RANGE_FALSE[("right", "l3")]

    def test_a_true_arm_has_no_level_and_no_false_claim(self):
        kit.add_conversation(side="left", arm="true")
        (item,), _ = kit.eligible()
        assert (kit.pick(item, "level"), kit.pick(item, "is_error_arm"), kit.pick(item, "false_claim")) == (None, False, None)

    def test_another_experiment_is_not_mixed_in(self):
        kit.add_conversation("other_exp", side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        assert ids(kit.eligible()[0]) == ["range_fact_right_l1"]

    def test_an_act_without_a_source_message_is_left_out(self):
        c = kit.add_conversation()
        kit.acts_of(c)[0].source_messages.clear()
        assert kit.eligible()[0] == []

    def test_an_unknown_experiment_is_an_error(self):
        with pytest.raises(ValueError):
            kit.eligible("nothing_here")


# --- plan_research ----------------------------------------------------------------------------------------------------

class TestPlan:
    def setup_method(self):
        pass

    def test_everything_eligible_is_planned_when_nothing_ran(self):
        kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        assert len(kit.plan()) == 2

    def test_a_finished_run_is_skipped(self):
        c = kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        kit.finished_research(c)
        assert ids(kit.plan()) == ["range_fact_right_l1"]

    @pytest.mark.parametrize("status", ["failed", "skipped_budget", "skipped_disabled"])
    def test_a_failed_run_is_skipped_unless_retry_failed(self, status):
        c = kit.add_conversation()
        kit.make_research_run(c, status=status)
        assert (kit.plan(), len(kit.plan(retry_failed=True))) == ([], 1)

    def test_a_finished_run_is_not_retried_even_with_retry_failed(self):
        c = kit.add_conversation()
        kit.finished_research(c)
        assert kit.plan(retry_failed=True) == []

    def test_set_globs_filter_on_the_transcript_id(self):
        kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(fact_id="law_fact", side="left", arm="err")
        assert ids(kit.plan(sets=["law_*"])) == ["law_fact_left_err"]
        assert ids(kit.plan(sets=["*_l1", "law_*"])) == ["law_fact_left_err", "range_fact_left_l1"]

    def test_a_conversation_with_two_acts_is_planned_once(self):
        kit.add_conversation(acts=[("offer_research", "a"), ("provide_information", "b")])
        assert len(kit.plan()) == 1


# --- run_research_eval ------------------------------------------------------------------------------------------------

def three_acts():
    kit.add_conversation(side="left", arm="l1")
    kit.add_conversation(side="right", arm="l1")
    kit.add_conversation(fact_id="law_fact", side="left", arm="err")
    return kit.plan()


class TestRun:
    def test_it_creates_one_research_run_per_act_and_posts_the_note(self, fake):
        c = kit.add_conversation()
        client = fake(kit.research_item())
        report = kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        run.refresh_from_db()
        assert (len(client.calls), run.status, run.kind, run.source_act_id) == (1, "done", "research", kit.acts_of(c)[0].pk)
        assert kit.NOTE_TEXT in run.posted_message.content and report.stopped_reason is None

    def test_the_run_is_anchored_on_the_claim_message(self, fake):
        c = kit.add_conversation()
        fake(kit.research_item())
        kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        assert (run.trigger_message.seq_no, run.snapshot_seq) == (4, 4)

    def test_requested_by_is_the_participant_who_did_not_write_the_claim(self, fake):
        c = kit.add_conversation()  # the claim (message 4) is by B
        fake(kit.research_item())
        kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        assert (kit.author_label(run), run.requested_by.label) == ("B", "A")

    def test_requested_by_is_the_other_one_when_the_claim_is_by_a(self, fake):
        from forum.models import Message

        c = kit.add_conversation()
        act = kit.acts_of(c)[0]
        act.source_messages.set([Message.objects.get(conversation=c, seq_no=3)])  # message 3 is by A
        fake(kit.research_item())
        kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        assert (kit.author_label(run), run.requested_by.label, run.trigger_message.seq_no) == ("A", "B", 3)

    def test_the_first_source_message_by_seq_is_the_trigger(self, fake):
        from forum.models import Message

        c = kit.add_conversation()
        act = kit.acts_of(c)[0]
        act.source_messages.set(list(Message.objects.filter(conversation=c, seq_no__in=[4, 2])))
        fake(kit.research_item())
        kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        assert (run.trigger_message.seq_no, run.requested_by.label) == (2, "A")

    def test_the_research_prompt_has_no_label_side_or_requester(self, fake):
        from moderation.prompting import load_prompt
        from moderation.research import build_research_input

        c = kit.add_conversation(side="right", arm="l3")
        act = kit.acts_of(c)[0]
        client = fake(kit.research_item())
        kit.run_eval(kit.plan())
        (call,) = kit.research_calls(client)
        (run,) = kit.research_runs(c)
        sent = kit.request_text(call)
        expected_user = build_research_input(offer_text=act.text, claim_text=run.trigger_message.content,
                                             issues=list(act.source_issues.all()))
        assert call["messages"] == [{"role": "user", "content": expected_user}]
        assert call["system"] == load_prompt("research").text or kit.request_text(call).startswith("# Research prompt")
        for forbidden in ("Participant A", "Participant B", "requested_by", "requester", "stance"):
            assert forbidden not in sent

    def test_the_prompt_is_the_same_whoever_would_click(self, fake):
        """requested_by is recorded, never read: the request is identical for a claim by A and a claim by B."""
        from forum.models import Message

        texts = []
        for seq in (3, 4):
            c = kit.add_conversation(experiment=f"exp{seq}")
            act = kit.acts_of(c)[0]
            act.source_messages.set([Message.objects.get(conversation=c, seq_no=seq)])
            Message.objects.filter(conversation=c, seq_no=seq).update(content="The same claim text.")
            client = fake(kit.research_item())
            kit.run_eval(kit.plan(f"exp{seq}"))
            (call,) = kit.research_calls(client)
            texts.append(kit.request_text(call))
        assert texts[0] == texts[1]

    def test_a_ledger_row_per_call_is_tied_to_the_run(self, fake):
        c = kit.add_conversation()
        fake(kit.research_item())
        kit.run_eval(kit.plan())
        (run,) = kit.research_runs(c)
        rows = kit.ledger()
        assert [(r.agent, r.run_id, r.status) for r in rows] == [("research", run.pk, "ok")]

    def test_it_records_a_cost_per_run_and_in_total(self, fake):
        three = three_acts()
        fake(*[kit.research_item() for _ in three])
        report = kit.run_eval(three)
        costs = [r[2] for r in report.results]
        assert (len(report.results), costs, report.cost_total) == (3, [Decimal("0.005")] * 3, Decimal("0.015"))

    def test_the_report_lists_each_act_with_its_status(self, fake):
        acts = three_acts()
        fake(*[kit.research_item() for _ in acts])
        report = kit.run_eval(acts)
        assert [(r[0].conversation_id, r[1]) for r in report.results] == [(a.conversation_id, "done") for a in acts]

    def test_on_result_is_called_once_per_act(self, fake):
        acts = three_acts()
        fake(*[kit.research_item() for _ in acts])
        seen = []
        kit.run_eval(acts, on_result=lambda *args: seen.append(args))
        assert len(seen) == 3

    def test_a_second_pass_over_the_plan_makes_no_call(self, fake):
        acts = three_acts()
        client = fake(*[kit.research_item() for _ in acts])
        kit.run_eval(acts)
        assert (kit.plan(), len(client.calls)) == ([], 3)

    def test_skip_existing_a_done_run_is_never_run_again_even_if_handed_over(self, fake):
        c = kit.add_conversation()
        kit.finished_research(c)
        (item,), _ = kit.eligible()
        client = fake(kit.research_item())
        kit.run_eval([item])
        assert (client.calls, len(kit.research_runs(c))) == ([], 1)

    def test_an_expected_failure_is_recorded_not_raised_and_the_next_act_still_runs(self, fake):
        acts = three_acts()
        client = fake(kit.invalid_json, kit.invalid_json, kit.research_item(), kit.research_item())
        report = kit.run_eval(acts)
        statuses = [r[1] for r in report.results]
        assert (statuses, len(client.calls)) == (["failed", "done", "done"], 4)

    def test_a_failed_run_is_retried_by_retry_failed_on_the_same_run_row(self, fake):
        c = kit.add_conversation()
        fake(kit.invalid_json, kit.invalid_json)
        kit.run_eval(kit.plan())
        assert kit.plan() == [] and len(kit.plan(retry_failed=True)) == 1
        fake(kit.research_item())
        report = kit.run_eval(kit.plan(retry_failed=True))
        (run,) = kit.research_runs(c)
        run.refresh_from_db()
        assert ([r[1] for r in report.results], run.status, run.posted_message_id is not None) == (["done"], "done", True)

    def test_llm_off_makes_no_call_and_creates_no_run(self, fake, settings):
        c = kit.add_conversation()
        client = fake(kit.research_item())
        settings.LLM_ENABLED = False
        report = kit.run_eval(kit.plan())
        assert (client.calls, kit.research_runs(c), report.results) == ([], [], [])
        assert report.stopped_reason

    def test_no_key_makes_no_call(self, fake, settings):
        c = kit.add_conversation()
        client = fake(kit.research_item())
        settings.ANTHROPIC_API_KEY = ""
        report = kit.run_eval(kit.plan())
        assert (client.calls, kit.research_runs(c)) == ([], []) and report.stopped_reason

    def test_a_negative_cap_is_refused(self):
        with pytest.raises(ValueError):
            kit.run_eval([], max_usd=Decimal("-1"))


class TestEstimate:
    def estimate(self, act):
        from seeding import research_eval

        return research_eval.estimate_research_usd(act)

    def test_the_worst_case_includes_the_search_fee_and_the_token_allowance(self, fake):
        kit.add_conversation()
        (act,) = kit.plan()
        fake(kit.research_item())
        kit.run_eval([act])
        reserved = kit.ledger()[-1].reserved_usd
        # the ledger's own reservation covers the tokens it estimates; the worst case adds three searches at $0.01 and the results
        assert self.estimate(act) >= reserved + 3 * Decimal("0.01")

    def test_the_estimate_grows_with_the_number_of_searches_allowed(self, tune):
        kit.add_conversation()
        (act,) = kit.plan()
        tune(RESEARCH_MAX_USES=1)
        one = self.estimate(act)
        tune(RESEARCH_MAX_USES=3)
        three = self.estimate(act)
        assert three - one >= 2 * Decimal("0.01")

    def test_the_estimate_grows_with_the_output_allowance(self, tune):
        kit.add_conversation()
        (act,) = kit.plan()
        tune(RESEARCH_MAX_TOKENS=1000)
        small = self.estimate(act)
        tune(RESEARCH_MAX_TOKENS=3000)
        assert self.estimate(act) > small

    def test_the_estimate_is_far_above_a_typical_actual_cost(self, fake):
        kit.add_conversation()
        (act,) = kit.plan()
        client = fake(kit.research_item())
        report = kit.run_eval([act])
        assert self.estimate(act) > 5 * report.results[0][2]


class TestCostCap:
    def worst(self, acts):
        from seeding import research_eval

        return [research_eval.estimate_research_usd(a) for a in acts]

    def test_a_zero_cap_makes_no_call_and_creates_no_run(self, fake):
        acts = three_acts()
        client = fake(kit.research_item())
        report = kit.run_eval(acts, max_usd=Decimal("0"))
        assert (client.calls, kit.research_runs(), report.results) == ([], [], [])
        assert report.stopped_reason

    def test_a_cap_just_under_the_first_worst_case_stops_before_the_first_call(self, fake):
        acts = three_acts()
        client = fake(kit.research_item())
        cap = self.worst(acts)[0] - Decimal("0.000001")
        report = kit.run_eval(acts, max_usd=cap)
        assert (client.calls, kit.research_runs()) == ([], []) and report.stopped_reason

    def test_a_cap_equal_to_the_first_worst_case_allows_that_call_only(self, fake):
        acts = three_acts()
        client = fake(*[kit.research_item() for _ in acts])
        cap = self.worst(acts)[0]
        report = kit.run_eval(acts, max_usd=cap)
        assert (len(client.calls), len(report.results), len(kit.research_runs())) == (1, 1, 1)
        assert report.stopped_reason

    def test_the_search_fee_allowance_of_finished_runs_counts_against_the_cap(self, fake):
        """After one run the counted spend is its token cost plus the search-fee allowance, not the (tiny) token cost alone."""
        acts = three_acts()
        w = self.worst(acts)
        client = fake(*[kit.research_item() for _ in acts])
        cap = max(w[0], w[1] + Decimal("0.02"))  # token cost alone (0.005) would leave room for a second call; with the fee allowance (0.03) it must not
        assert cap == w[1] + Decimal("0.02")
        report = kit.run_eval(acts, max_usd=cap)
        assert len(client.calls) == 1 and report.stopped_reason

    def test_a_cap_that_covers_two_counted_runs_and_a_third_worst_case_runs_all_three(self, fake):
        acts = three_acts()
        w = self.worst(acts)
        client = fake(*[kit.research_item() for _ in acts])
        counted = Decimal("0.005") + 3 * Decimal("0.01")
        report = kit.run_eval(acts, max_usd=2 * counted + w[2])
        assert (len(client.calls), report.stopped_reason) == (3, None)

    def test_a_cap_one_micro_dollar_short_of_that_stops_before_the_third(self, fake):
        acts = three_acts()
        w = self.worst(acts)
        client = fake(*[kit.research_item() for _ in acts])
        counted = Decimal("0.005") + 3 * Decimal("0.01")
        report = kit.run_eval(acts, max_usd=2 * counted + w[2] - Decimal("0.000001"))
        assert len(client.calls) == 2 and report.stopped_reason
        assert len(kit.research_runs()) == 2

    def test_a_roomy_cap_runs_everything(self, fake):
        acts = three_acts()
        client = fake(*[kit.research_item() for _ in acts])
        report = kit.run_eval(acts, max_usd=Decimal("100"))
        assert (len(client.calls), report.stopped_reason) == (3, None)


# --- collect_cases ----------------------------------------------------------------------------------------------------

class TestCollect:
    def test_one_case_per_finished_run_with_the_note_stripped_of_its_sources(self, fake):
        c = kit.add_conversation(side="right", arm="l3", acts=[("correct_factual_error", "Please check.")])
        fake(kit.research_item(confidence=0.65))
        kit.run_eval(kit.plan())
        (case,) = kit.collect_research_cases()
        assert kit.NOTE_TEXT == case.note_text
        assert "Sources" not in case.note_text and "example" not in case.note_text
        assert (case.conversation_id, case.fact_id, case.arm, case.side, case.level, case.is_error_arm) == \
            ("range_fact_right_l3", "range_fact", "l3", "right", 3, True)
        assert (case.false_claim, case.true_claim) == (kit.RANGE_FALSE[("right", "l3")], kit.RANGE_TRUE)
        assert (case.n_sources, case.note_words, case.run_status, case.moderator_act_type) == \
            (3, len(kit.NOTE_TEXT.split()), "done", "correct_factual_error")
        assert case.confidence == pytest.approx(0.65)
        assert Decimal(str(case.cost_usd)) == Decimal("0.005")

    def test_the_sources_are_counted_by_their_dash_lines(self):
        c = kit.add_conversation()
        kit.finished_research(c, sources=kit.SOURCES[:2])
        (case,) = kit.collect_research_cases()
        assert (case.n_sources, case.note_text) == (2, kit.NOTE_TEXT)

    def test_no_sources_block_means_zero_sources_and_the_whole_text_is_the_note(self):
        c = kit.add_conversation()
        kit.finished_research(c, sources=[])
        (case,) = kit.collect_research_cases()
        assert (case.n_sources, case.note_text) == (0, kit.NOTE_TEXT)

    def test_dash_lines_inside_the_note_are_not_sources(self):
        c = kit.add_conversation()
        note = "First point.\n- a dash line in the note\nSecond point."
        kit.finished_research(c, note=note, sources=kit.SOURCES[:1])
        (case,) = kit.collect_research_cases()
        assert (case.n_sources, case.note_text, case.note_words) == (1, note, len(note.split()))

    def test_a_source_title_with_a_dash_is_one_source(self):
        c = kit.add_conversation()
        kit.finished_research(c, sources=[("A - B - C", "https://x.example.org/a"), ("D", "https://y.example.org/b")])
        (case,) = kit.collect_research_cases()
        assert case.n_sources == 2

    def test_confidence_is_none_when_the_ledger_has_none(self):
        c = kit.add_conversation()
        kit.finished_research(c)
        (case,) = kit.collect_research_cases()
        assert case.confidence is None

    def test_failed_and_unfinished_runs_give_no_case(self):
        kit.add_conversation(side="left", arm="l1")
        kit.add_conversation(side="right", arm="l1")
        from forum.models import Conversation

        for conversation, status in zip(Conversation.objects.order_by("pk"), ("failed", "pending")):
            kit.make_research_run(conversation, status=status)
        assert kit.collect_research_cases() == []

    def test_only_the_experiments_own_runs_are_collected(self):
        a = kit.add_conversation(side="left", arm="l1")
        b = kit.add_conversation("other_exp", side="right", arm="l1")
        kit.finished_research(a)
        kit.finished_research(b)
        assert [c.conversation_id for c in kit.collect_research_cases()] == ["range_fact_left_l1"]
        assert [c.conversation_id for c in kit.collect_research_cases("other_exp")] == ["range_fact_right_l1"]

    def test_a_true_arm_case(self):
        c = kit.add_conversation(side="left", arm="true")
        kit.finished_research(c)
        (case,) = kit.collect_research_cases()
        assert (case.is_error_arm, case.false_claim, case.true_claim, case.level, case.arm) == (False, None, kit.RANGE_TRUE, None, "true")

    def test_a_law_arm_case(self):
        c = kit.add_conversation(fact_id="law_fact", side="right", arm="err")
        kit.finished_research(c)
        (case,) = kit.collect_research_cases()
        assert (case.fact_id, case.level, case.false_claim, case.true_claim) == ("law_fact", None, kit.jk.LAW_FALSE["right"], kit.LAW_TRUE)

    def test_cases_cover_every_conversation_with_a_note(self, fake):
        acts = three_acts()
        fake(*[kit.research_item() for _ in acts])
        kit.run_eval(acts)
        assert sorted(c.conversation_id for c in kit.collect_research_cases()) == sorted(a.conversation_id for a in acts)

    def test_split_note_uses_the_marker_written_by_the_research_module(self):
        from moderation.research import _compose_message_text
        from seeding import research_eval

        text = _compose_message_text("The note.", [{"title": "T", "url": "https://a.example.org/x"},
                                                  {"title": "U", "url": "https://b.example.org/y"}])
        assert research_eval.split_note(text) == ("The note.", 2)
        assert research_eval.split_note("Just a note.") == ("Just a note.", 0)


class TestAssignments:
    def test_as_is_and_swapped_conversations_are_kept_apart(self, fake):
        kit.add_conversation(assignment="as-is", acts=[("offer_research", "as is")])
        kit.add_conversation(assignment="swapped", acts=[("offer_research", "swapped")])
        as_is, _ = kit.eligible(assignment="as-is")
        swapped, _ = kit.eligible(assignment="swapped")
        assert ([(kit.transcript_id(i), kit.pick(i, "assignment")) for i in as_is],
                [(kit.transcript_id(i), kit.pick(i, "assignment")) for i in swapped]) == (
            [("range_fact_left_l2", "as-is")], [("range_fact_left_l2", "swapped")])
        assert kit.act_id(as_is[0]) != kit.act_id(swapped[0])

    def test_collecting_follows_the_assignment(self):
        a = kit.add_conversation(assignment="as-is", acts=[("offer_research", "as is")])
        b = kit.add_conversation(assignment="swapped", acts=[("offer_research", "swapped")])
        kit.finished_research(a, note="As-is note.")
        kit.finished_research(b, note="Swapped note.")
        from seeding import research_eval

        assert ([c.note_text for c in research_eval.collect_cases(kit.EXPERIMENT, "as-is")],
                [c.note_text for c in research_eval.collect_cases(kit.EXPERIMENT, "swapped")]) == (["As-is note."], ["Swapped note."])
