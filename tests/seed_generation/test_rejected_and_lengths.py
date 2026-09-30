"""Rulings after the first real run: rejected bases, a right base saved only after check_pair, lengths in the mirror prompt,
the neutral lead-in and no-size-words instructions, and the retry paths."""
import json
import re
from decimal import Decimal

import gen_kit as kit
import pytest


def gen():
    from seeding import generate

    return generate


def run(facts, tmp_path, **kwargs):
    kwargs.setdefault("max_usd", Decimal("50"))
    kwargs.setdefault("bases_dir", tmp_path / "bases")
    return gen().run_generation(facts, **kwargs)


def good():
    return [kit.answer(side="left"), kit.answer(side="right")]


def short_right():
    return kit.answer_from(kit.with_lengths("right", [30, 30, 30, 60]))


BAD = kit.answer(last="No marker here at all.")


def texts(path):
    return [m["text"] for m in json.loads(path.read_text(encoding="utf-8"))["messages"]]


class TestRightBaseSavedOnlyAfterThePairCheck:
    def test_a_regenerated_right_base_replaces_a_failed_one_in_bases(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert texts(tmp_path / "bases" / "range_fact_right.json") == [m["text"] for m in kit.answer(side="right")["messages"]]

    def test_the_failed_right_base_is_in_rejected_not_in_bases(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert (kit.files_under(tmp_path / "rejected"), texts(tmp_path / "rejected" / "range_fact_right_1.json")) == (
            ["range_fact_right_1.json"], [m["text"] for m in short_right()["messages"]],
        )

    def test_two_failing_right_bases_leave_no_right_base_in_bases(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        report = run([kit.range_fact()], tmp_path)
        assert (kit.files_under(tmp_path / "bases"), len(report.failures), report.transcripts) == (["range_fact_left.json"], 1, [])

    def test_two_failing_right_bases_are_both_written_to_rejected(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "rejected") == ["range_fact_right_1.json", "range_fact_right_2.json"]

    def test_a_good_pair_writes_no_rejected_file(self, fake, tmp_path):
        fake(*good())
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "rejected") == []

    def test_rejected_numbers_continue_on_a_later_run(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        fake(short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        assert kit.files_under(tmp_path / "rejected") == [f"range_fact_right_{n}.json" for n in (1, 2, 3, 4)]

    def test_the_default_rejected_directory_is_generated_rejected(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        gen().run_generation([kit.range_fact()], max_usd=Decimal("50"))
        assert kit.files_under(tmp_path / "generated" / "rejected") == ["range_fact_right_1.json", "range_fact_right_2.json"]

    def test_a_rejected_base_is_never_reused_on_the_next_run(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        client = fake(kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), len(report.transcripts)) == (1, 8)


class TestASavedRightBaseThatNoLongerMatches:
    def saved_run(self, fake, tmp_path):
        fake(*good())
        run([kit.range_fact()], tmp_path)

    def test_it_is_regenerated_not_reused(self, fake, tmp_path):
        self.saved_run(fake, tmp_path)
        (tmp_path / "bases" / "range_fact_right.json").write_text(json.dumps(kit.stamped(kit.with_lengths("right", [30, 30, 30, 60]))), encoding="utf-8")
        client = fake(kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()][-1], len(report.transcripts), report.failures) == (1, "mirror_v7", 8, [])

    def test_the_regenerated_one_is_saved_over_it(self, fake, tmp_path):
        self.saved_run(fake, tmp_path)
        (tmp_path / "bases" / "range_fact_right.json").write_text(json.dumps(kit.stamped(kit.with_lengths("right", [30, 30, 30, 60]))), encoding="utf-8")
        fake(kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert texts(tmp_path / "bases" / "range_fact_right.json") == [m["text"] for m in kit.answer(side="right")["messages"]]

    def test_the_left_base_is_still_reused(self, fake, tmp_path):
        self.saved_run(fake, tmp_path)
        (tmp_path / "bases" / "range_fact_right.json").write_text(json.dumps(kit.stamped(kit.with_lengths("right", [30, 30, 30, 60]))), encoding="utf-8")
        client = fake(kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert (len(client.calls), texts(tmp_path / "bases" / "range_fact_left.json")) == (
            1, [m["text"] for m in kit.answer(side="left")["messages"]],
        )

    def test_a_matching_saved_right_base_is_reused(self, fake, tmp_path):
        self.saved_run(fake, tmp_path)
        client = fake()
        report = run([kit.range_fact()], tmp_path)
        assert (client.calls, report.bases_reused) == ([], 2)


class TestRetryPaths:
    def test_an_invalid_first_reply_then_a_valid_one(self, fake, tmp_path):
        fake(BAD, *good())
        report = run([kit.range_fact()], tmp_path)
        assert ([r.attempt for r in kit.ledger()], report.failures, len(report.transcripts)) == ([1, 2, 1], [], 8)

    def test_the_invalid_first_reply_is_kept_in_rejected_and_the_valid_one_in_bases(self, fake, tmp_path):
        fake(BAD, *good())
        run([kit.range_fact()], tmp_path)
        assert (
            texts(tmp_path / "rejected" / "range_fact_left_1.json"), texts(tmp_path / "bases" / "range_fact_left.json"),
        ) == ([m["text"] for m in BAD["messages"]], [m["text"] for m in kit.answer(side="left")["messages"]])

    def test_a_reply_that_does_not_fit_the_schema_leaves_nothing_to_reject(self, fake, tmp_path):
        fake({"messages": [{"author": "Somebody Else", "text": "Hi."}]}, *good())
        report = run([kit.range_fact()], tmp_path)
        assert (report.failures, kit.files_under(tmp_path / "rejected")) == ([], [])

    def test_a_pair_check_failure_then_a_valid_regenerated_right_base(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        report = run([kit.range_fact()], tmp_path)
        assert (len(client.calls), report.failures, len(report.transcripts), report.facts_done) == (3, [], 8, 1)

    def test_the_regeneration_prompt_carries_the_reason(self, fake, tmp_path):
        client = fake(kit.answer(side="left"), short_right(), kit.answer(side="right"))
        run([kit.range_fact()], tmp_path)
        assert "differs in length" in kit.request_text(client.calls[2])

    def test_both_left_replies_invalid_are_both_rejected(self, fake, tmp_path):
        fake(BAD, BAD, BAD)
        report = run([kit.range_fact()], tmp_path)
        assert (kit.files_under(tmp_path / "rejected"), kit.files_under(tmp_path / "bases"), len(report.failures)) == (
            ["range_fact_left_1.json", "range_fact_left_2.json", "range_fact_left_3.json"], [], 1,
        )

    def test_both_right_replies_invalid_are_rejected_and_reported(self, fake, tmp_path):
        fake(kit.answer(side="left"), BAD, BAD, BAD)
        report = run([kit.range_fact()], tmp_path)
        assert (kit.files_under(tmp_path / "rejected"), [f[0] for f in report.failures], kit.files_under(tmp_path / "bases")) == (
            ["range_fact_right_1.json", "range_fact_right_2.json", "range_fact_right_3.json"], ["range_fact"], ["range_fact_left.json"],
        )

    def test_a_rejected_file_reads_as_a_base(self, fake, tmp_path):
        fake(kit.answer(side="left"), short_right(), short_right())
        run([kit.range_fact()], tmp_path)
        saved = json.loads((tmp_path / "rejected" / "range_fact_right_1.json").read_text(encoding="utf-8"))
        assert (saved["side"], saved["fact_id"], [m["seq"] for m in saved["messages"]]) == ("right", "range_fact", [1, 2, 3, 4])


class TestLengthsInTheMirrorPrompt:
    def mirror(self, fake, left=None):
        left = left or kit.with_lengths("left", [101, 152, 203, 254])
        client = fake(kit.answer(side="right"))
        gen().generate_base(kit.range_fact(), "right", left_base=left, session=None)
        return client.calls[0]

    def test_each_left_message_length_is_stated(self, fake):
        text = kit.system_text(self.mirror(fake))
        assert [f"{n} characters" in text for n in (101, 152, 203, 254)] == [True] * 4

    def test_the_lengths_follow_the_base_given(self, fake):
        text = kit.system_text(self.mirror(fake, kit.with_lengths("left", [111, 122, 133, 144])))
        assert ["101 characters" in text, "111 characters" in text, "144 characters" in text] == [False, True, True]

    def test_the_lengths_are_matched_to_their_messages(self, fake):
        text = kit.system_text(self.mirror(fake))
        assert re.findall(r"[Mm]essage (\d)[^\n]*?(\d+) characters", text) == [("1", "101"), ("2", "152"), ("3", "203"), ("4", "254")]

    @pytest.mark.parametrize("kind", ["mirror", "left", "right"])
    def test_no_placeholder_is_left_in_the_sent_prompt(self, fake, kind):
        if kind == "mirror":
            call = self.mirror(fake)
        else:
            client = fake(kit.answer(side=kind))
            gen().generate_base(kit.range_fact(), kind, session=None, conversation_hint="A note.")
            call = client.calls[0]
        assert "{{" not in kit.request_text(call) and "}}" not in kit.system_text(call)

    def test_the_generator_prompt_has_no_lengths_block(self, fake):
        client = fake(kit.answer())
        gen().generate_base(kit.range_fact(), "left", session=None)
        assert re.search(r"\d+ characters", kit.system_text(client.calls[0])) is None

    def test_it_requires_fifteen_percent(self, fake):
        text = kit.system_text(self.mirror(fake)).lower()
        assert re.search(r"15\s*(percent|%)", text)


class TestPromptInstructions:
    def systems(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(kit.range_fact(), "left", session=None)
        gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        return [kit.system_text(c).lower().replace("\n", " ") for c in client.calls]

    def test_neither_asks_the_model_to_write_a_lead_in(self, fake):
        assert [bool(re.search(r"do not write a lead-?in", t)) for t in self.systems(fake)] == [True, True]

    def test_neither_tells_the_model_to_introduce_the_marker(self, fake):
        assert [bool(re.search(r"introduce the marker", t)) for t in self.systems(fake)] == [False, False]

    def test_both_say_the_last_message_ends_with_the_marker(self, fake):
        assert [bool(re.search(r"ends with the marker", t)) for t in self.systems(fake)] == [True, True]

    def test_both_say_nothing_follows_the_marker(self, fake):
        assert [bool(re.search(r"nothing at all follows", t)) for t in self.systems(fake)] == [True, True]

    @pytest.mark.parametrize("word", ["widespread", "growing", "handful", "majority", "minority", "surge", "mainstream", "fringe", "spreading"])
    def test_both_list_every_banned_word_in_the_rule_for_the_last_message(self, fake, word):
        rules = [re.search(r"last message must not contain any of these words at all: (.*?)\.", t).group(1) for t in self.systems(fake)]
        assert [bool(re.search(rf"\b{word}\b", rule)) for rule in rules] == [True, True]

    def test_only_the_mirror_prompt_states_fifteen_percent(self, fake):
        generator, mirror = self.systems(fake)
        assert (bool(re.search(r"15\s*(percent|%)", generator)), bool(re.search(r"15\s*(percent|%)", mirror))) == (False, True)
