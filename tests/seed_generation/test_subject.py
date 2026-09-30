"""Rulings after the 12-fact run: Fact.subject, prompts that never show the claim, facts without a subject skipped, a fixed
four-message shape, the new banned-word list. Parametrised over the shipped fact bank as it is at collection time, so an owner
edit of facts.json does not break these tests."""
import json
import re
from decimal import Decimal

import gen_kit as kit
import pytest
from pydantic import ValidationError

from config import tunables
from seeding.facts import Fact, load_facts

SHIPPED = [f for f in load_facts() if f.ready() and f.subject]
IDS = [f.id for f in SHIPPED]


def gen():
    from seeding import generate

    return generate


def forbidden_texts(fact):
    """Everything a prompt must not reveal about a fact: the true claim, the framing and every seeded claim (with and without a full stop)."""
    from seeding.seeds import build_seeds

    texts = [fact.claim_true, fact.claim_true.rstrip(".")]
    if fact.framing:
        texts.append(fact.framing)
    texts += [seed.false_claim for seed in build_seeds(fact)]
    texts += [seed.false_claim.rstrip(".") for seed in build_seeds(fact)]
    return [t for t in texts if t.strip()]


def prompts_for(fake, fact):
    """The full request text of a left, a plain right and a mirror call."""
    client = fake(kit.answer(), kit.answer(side="right"), kit.answer(side="right"))
    gen().generate_base(fact, "left", session=None)
    gen().generate_base(fact, "right", session=None)
    gen().generate_base(fact, "right", left_base=kit.base("left", fact.id), session=None)
    return [kit.request_text(c) for c in client.calls]


class TestTheFactField:
    def test_subject_defaults_to_none(self):
        assert kit.range_fact(subject=None).subject is None

    def test_an_empty_subject_is_refused(self):
        with pytest.raises(ValidationError):
            kit.range_fact(subject="")

    def test_a_blank_subject_is_refused(self):
        with pytest.raises(ValidationError):
            kit.range_fact(subject="   ")

    def test_a_statistic_may_have_a_subject(self):
        assert kit.range_fact(subject="how many things exist").subject == "how many things exist"

    def test_a_law_may_have_a_subject(self):
        assert kit.law_fact(subject="what the law says").subject == "what the law says"

    def test_a_qualitative_fact_may_have_a_subject(self):
        assert kit.law_fact(id="qual_fact", type="qualitative", subject="what people say").subject == "what people say"

    def test_a_fact_without_a_subject_can_still_be_ready(self):
        assert kit.range_fact(subject=None).ready() is True

    def test_the_review_shows_the_subject(self):
        from seeding.review import review_markdown

        assert "- Subject: how many things of a certain kind exist" in review_markdown([kit.range_fact()])

    def test_the_review_shows_no_subject_line_without_one(self):
        from seeding.review import review_markdown

        assert "Subject:" not in review_markdown([kit.range_fact(subject=None)])


class TestUsable:
    def test_a_ready_fact_with_a_subject_is_usable(self):
        assert gen().usable(kit.range_fact()) is True

    def test_a_ready_fact_without_a_subject_is_not(self):
        assert gen().usable(kit.range_fact(subject=None)) is False

    def test_a_not_ready_fact_with_a_subject_is_not(self):
        assert gen().usable(kit.unready_fact()) is False

    def test_a_law_with_a_subject_is_usable(self):
        assert gen().usable(kit.law_fact()) is True


class TestNoSubjectIsSkipped:
    def test_run_generation_skips_and_reports_it(self, fake, tmp_path):
        client = fake(*[kit.answer(side="left"), kit.answer(side="right")])
        report = gen().run_generation(
            [kit.range_fact(id="nosubject_fact", subject=None), kit.range_fact()], max_usd=Decimal("50"), bases_dir=tmp_path / "bases",
        )
        assert (report.skipped_not_ready, len(client.calls), len(report.transcripts), report.failures) == (["nosubject_fact"], 2, 8, [])

    def test_only_a_fact_without_a_subject_means_no_call(self, fake, tmp_path):
        client = fake()
        report = gen().run_generation([kit.range_fact(subject=None)], max_usd=Decimal("50"), bases_dir=tmp_path / "bases")
        assert (client.calls, report.transcripts, report.skipped_not_ready) == ([], [], ["range_fact"])

    def test_no_file_is_written_for_it(self, fake, tmp_path):
        fake()
        gen().run_generation([kit.range_fact(subject=None)], max_usd=Decimal("50"), bases_dir=tmp_path / "bases")
        assert kit.files_under(tmp_path) == []

    def test_planning_skips_it(self):
        assert gen().plan_generation([kit.range_fact(subject=None)]) == []

    def test_generate_base_refuses_it_and_makes_no_call(self, fake):
        client = fake(kit.answer())
        with pytest.raises(ValueError):
            gen().generate_base(kit.range_fact(subject=None), "left", session=None)
        assert (client.calls, kit.ledger()) == ([], [])

    def test_the_estimate_refuses_it(self):
        with pytest.raises(ValueError):
            gen().estimate_call_usd(kit.range_fact(subject=None), "left")

    def test_the_command_skips_it_and_says_so(self, fake, monkeypatch):
        from evaluation.management.commands import generate_conversations as command

        monkeypatch.setattr(command, "load_facts", lambda: [kit.range_fact(id="nosubject_fact", subject=None), kit.law_fact()])
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        result = kit.generate_conversations("--max-usd", "5", "--live", "--yes")
        assert ("nosubject_fact" in result.out, len(client.calls), result.exc) == (True, 2, None)

    def test_the_command_with_only_such_facts_is_an_error(self, fake, monkeypatch):
        from evaluation.management.commands import generate_conversations as command

        monkeypatch.setattr(command, "load_facts", lambda: [kit.range_fact(subject=None)])
        client = fake()
        result = kit.generate_conversations("--dry-run")
        assert (result.exc is not None, client.calls) == (True, [])


class TestThePromptShowsOnlyTheSubject:
    def test_the_subject_is_in_every_kind_of_prompt(self, fake):
        texts = prompts_for(fake, kit.range_fact(subject="zebrafish quartz counts"))
        assert ["zebrafish quartz counts" in t for t in texts] == [True, True, True]

    def test_the_subject_is_read_from_the_fact_each_time(self, fake):
        first = prompts_for(fake, kit.range_fact(subject="alpha subject phrase"))[0]
        second = prompts_for(fake, kit.range_fact(subject="beta subject phrase"))[0]
        assert ("alpha subject phrase" in first, "beta subject phrase" in first, "beta subject phrase" in second) == (True, False, True)

    def test_the_prompt_says_the_model_is_not_told_what_it_says(self, fake):
        texts = prompts_for(fake, kit.range_fact())
        assert ["not told" in t.lower() for t in texts] == [True, True, True]

    def test_the_true_claim_of_a_test_fact_is_absent(self, fake):
        texts = prompts_for(fake, kit.range_fact())
        assert ["Between 500 and 560" in t for t in texts] == [False, False, False]

    def test_the_framing_of_a_test_fact_is_absent(self, fake):
        texts = prompts_for(fake, kit.range_fact())
        assert ["Cited as evidence" in t for t in texts] == [False, False, False]

    def test_no_seeded_claim_of_a_test_fact_is_present(self, fake):
        texts = prompts_for(fake, kit.range_fact())
        assert [[c for c in forbidden_texts(kit.range_fact()) if c in t] for t in texts] == [[], [], []]

    def test_a_law_error_claim_is_absent(self, fake):
        texts = prompts_for(fake, kit.law_fact())
        assert [[c for c in forbidden_texts(kit.law_fact()) if c in t] for t in texts] == [[], [], []]

    def test_a_hint_does_not_bring_the_claim_back(self, fake):
        client = fake(kit.answer())
        gen().generate_base(kit.range_fact(), "left", session=None, conversation_hint="The previous attempt was refused: no marker.")
        assert "Between 500 and 560" not in kit.request_text(client.calls[0])

    @pytest.mark.parametrize("fact", SHIPPED, ids=IDS)
    def test_every_shipped_fact_has_its_subject_in_and_its_claims_out(self, fake, fact):
        texts = prompts_for(fake, fact)
        assert (
            [fact.subject in t for t in texts], [[c for c in forbidden_texts(fact) if c in t] for t in texts],
        ) == ([True, True, True], [[], [], []])

    @pytest.mark.parametrize("fact", SHIPPED, ids=IDS)
    def test_every_shipped_fact_has_a_usable_neutral_subject(self, fact):
        assert (gen().usable(fact), bool(fact.subject.strip()), fact.claim_true.rstrip(".") in fact.subject) == (True, True, False)

    @pytest.mark.parametrize("fact", SHIPPED, ids=IDS)
    def test_a_shipped_subject_states_no_figure(self, fact):
        assert re.search(r"\d", fact.subject) is None


class TestTheFixedShape:
    def test_the_tunables(self):
        assert (tunables.GENERATOR_MIN_MESSAGES, tunables.GENERATOR_MAX_MESSAGES) == (4, 4)

    def test_the_prompts_say_exactly_four_messages(self, fake):
        texts = [t.lower().replace("\n", " ") for t in prompts_for(fake, kit.range_fact())]
        assert [bool(re.search(r"exactly four messages", t)) for t in texts] == [True, True, True]

    def test_the_prompts_name_the_alternation(self, fake):
        texts = [t.replace("\n", " ") for t in prompts_for(fake, kit.range_fact())]
        assert ["Participant A, Participant B, Participant A, Participant B" in t for t in texts] == [True, True, True]

    @pytest.mark.parametrize("count", [2, 3, 5, 6])
    def test_a_reply_of_another_length_is_refused_and_retried(self, fake, tmp_path, count):
        client = fake(kit.answer(count=count), kit.answer(count=count), kit.answer(count=count), kit.answer(), kit.answer(side="right"))
        report = gen().run_generation([kit.range_fact()], max_usd=Decimal("50"), bases_dir=tmp_path / "bases")
        assert (len(client.calls), len(report.failures)) == (3, 1)

    def test_a_reply_of_five_messages_ending_with_participant_a_is_refused(self, fake):
        from seeding import arms

        fake(kit.answer(count=5))
        with pytest.raises(arms.BaseError):
            gen().generate_base(kit.range_fact(), "left", session=None)

    def test_a_reply_of_four_messages_is_accepted(self, fake):
        fake(kit.answer(count=4))
        base, _ = gen().generate_base(kit.range_fact(), "left", session=None)
        assert len(base["messages"]) == 4


class TestTheBannedWordList:
    def systems(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(kit.range_fact(), "left", session=None)
        gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        return [kit.system_text(c).replace("\n", " ") for c in client.calls]

    def listed(self, text):
        rule = re.search(r"last message must not contain any of these words at all: (.*?)\.", text).group(1)
        return set(re.findall(r"[a-z]+", rule))

    def test_the_prompts_list_exactly_the_words_of_the_constant(self, fake):
        from seeding import arms

        assert [self.listed(t) for t in self.systems(fake)] == [set(arms.BANNED_WORDS)] * 2

    def test_the_constant_is_the_nine_words(self):
        from seeding import arms

        assert set(arms.BANNED_WORDS) == {"widespread", "growing", "handful", "majority", "minority", "surge", "mainstream", "fringe", "spreading"}

    def test_the_prompts_no_longer_list_the_removed_words(self, fake):
        assert [self.listed(t) & {"most", "many", "only", "few"} for t in self.systems(fake)] == [set(), set()]


class TestTheVersions:
    def test_the_constants(self):
        assert gen().PROMPT_VERSIONS == {"generate": "gen_v7", "mirror": "mirror_v7"}

    def test_a_base_of_the_previous_version_is_regenerated(self, fake, tmp_path):
        import json

        (tmp_path / "bases").mkdir()
        for side, version in (("left", "gen_v5"), ("right", "mirror_v5")):
            (tmp_path / "bases" / f"range_fact_{side}.json").write_text(json.dumps(kit.stamped(kit.base(side), version)), encoding="utf-8")
        client = fake(kit.answer(side="left"), kit.answer(side="right"))
        report = gen().run_generation([kit.range_fact()], max_usd=Decimal("50"), bases_dir=tmp_path / "bases")
        assert (len(client.calls), [r.prompt_version for r in kit.ledger()], len(report.transcripts)) == (2, ["gen_v7", "mirror_v7"], 8)


class TestAuthorsAreAssignedByCode:
    def test_the_prompts_do_not_mention_an_author_field(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"), kit.answer(side="right"))
        gen().generate_base(kit.range_fact(), "left", session=None)
        gen().generate_base(kit.range_fact(), "right", session=None)
        gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        assert ["author" in kit.system_text(c).lower() for c in client.calls] == [False, False, False]

    def test_the_prompts_ask_for_no_speaker_labels(self, fake):
        client = fake(kit.answer(), kit.answer(side="right"))
        gen().generate_base(kit.range_fact(), "left", session=None)
        gen().generate_base(kit.range_fact(), "right", left_base=kit.base("left"), session=None)
        assert [bool(re.search(r"without speaker labels", kit.system_text(c).replace("\n", " "))) for c in client.calls] == [True, True]

    def test_the_prompts_still_state_the_alternation(self, fake):
        client = fake(kit.answer())
        gen().generate_base(kit.range_fact(), "left", session=None)
        assert "Participant A, Participant B, Participant A, Participant B" in kit.system_text(client.calls[0]).replace("\n", " ")

    def test_the_structured_schema_sent_has_no_author_property(self, fake):
        client = fake(kit.answer())
        gen().generate_base(kit.range_fact(), "left", session=None)
        assert "author" not in json.dumps(client.calls[0]["output_format"].model_json_schema())

    def test_the_code_assigns_a_b_a_b(self, fake):
        fake(kit.answer())
        base, _ = gen().generate_base(kit.range_fact(), "left", session=None)
        assert [m["author"] for m in base["messages"]] == ["Participant A", "Participant B", "Participant A", "Participant B"]

    def test_the_code_assigns_the_same_authors_whatever_the_text_says(self, fake):
        payload = kit.answer()
        payload["messages"][0]["text"] = "Participant B: I would like to start. " + "thing " * 20
        fake(payload)
        base, _ = gen().generate_base(kit.range_fact(), "left", session=None)
        assert base["messages"][0]["author"] == "Participant A"

    def test_six_texts_alternate_when_the_range_allows_six(self, fake, tune):
        tune(GENERATOR_MIN_MESSAGES=4, GENERATOR_MAX_MESSAGES=6)
        fake(kit.answer(count=6))
        base, _ = gen().generate_base(kit.range_fact(), "left", session=None)
        assert [m["author"][-1] for m in base["messages"]] == list("ABABAB")

    def test_an_odd_number_of_texts_is_refused_because_b_must_be_last(self, fake, tune):
        from seeding import arms

        tune(GENERATOR_MIN_MESSAGES=3, GENERATOR_MAX_MESSAGES=5)
        fake(kit.answer(count=5))
        with pytest.raises(arms.BaseError):
            gen().generate_base(kit.range_fact(), "left", session=None)

    def test_a_reply_with_an_author_key_uses_up_an_attempt(self, fake, tmp_path):
        bad = {"messages": [dict(m, author="Participant A") for m in kit.answer()["messages"]]}
        client = fake(bad, bad, bad, kit.answer(), kit.answer(side="right"))
        report = gen().run_generation([kit.range_fact()], max_usd=Decimal("50"), bases_dir=tmp_path / "bases")
        assert (len(client.calls), len(report.failures)) == (3, 1)
