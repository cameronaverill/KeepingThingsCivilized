"""`rate_target`: the arguments of the gateway call, the ledger row and the stored Rating (docs/step14_brief.md, 14a)."""
import re

import llmr_kit as kit
import pytest

TEXT = "Rent control lowers rents everywhere. The 1990 census counted five million people."


def setup(model=kit.SONNET):
    rater = kit.make_rater("rater-under-test", model)
    world, message = kit.single(TEXT)
    return rater, world, message


class TestTheGatewayCall:
    @pytest.mark.parametrize("model", [kit.SONNET, kit.HAIKU])
    def test_it_makes_exactly_one_call_with_the_raters_model(self, fake, model):
        rater, _, message = setup(model)
        client = fake(kit.nothing())
        kit.rate(rater, message)
        assert [call["model"] for call in client.calls] == [model]

    def test_the_system_prompt_is_the_loaded_rater_prompt_for_all_dimensions(self, fake):
        rater, _, message = setup()
        client = fake(kit.nothing())
        kit.rate(rater, message)
        assert kit.system_text(client.calls[0]) == kit.load_prompt().text

    def test_the_system_prompt_is_sent_as_one_cacheable_block(self, fake):
        rater, _, message = setup()
        client = fake(kit.nothing())
        kit.rate(rater, message)
        (block,) = client.calls[0]["system"]
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_the_output_schema_is_the_raters(self, fake):
        from evaluation.schemas import RaterOutput

        rater, _, message = setup()
        client = fake(kit.nothing())
        kit.rate(rater, message)
        assert client.calls[0]["output_format"] is RaterOutput

    @pytest.mark.parametrize("model", [kit.SONNET, kit.HAIKU])
    def test_no_temperature_is_sent_even_though_the_rater_has_one_and_haiku_would_accept_it(self, fake, model):
        rater, _, message = setup(model)
        assert rater.temperature == 0.0
        client = fake(kit.nothing())
        kit.rate(rater, message)
        assert ("temperature" in client.calls[0], "extra_body" in client.calls[0]) == (False, False)

    def test_the_user_turn_is_one_user_message(self, fake):
        rater, _, message = setup()
        client = fake(kit.nothing())
        kit.rate(rater, message)
        assert [m["role"] for m in client.calls[0]["messages"]] == ["user"]

    def test_the_raters_own_temperature_field_is_left_as_it_was(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        kit.rate(rater, message)
        rater.refresh_from_db()
        assert rater.temperature == 0.0


class TestTheLedgerRow:
    def row(self, fake, model=kit.SONNET):
        rater, world, message = setup(model)
        fake(kit.nothing())
        result = kit.rate(rater, message)
        (row,) = kit.ledger()
        return row, rater, world, message, result

    def test_purpose_agent_attempt_and_model(self, fake):
        row, rater, *_ = self.row(fake)
        assert (row.purpose, row.agent, row.attempt, row.model, row.status) == ("judge", "rater", 1, rater.model, "ok")

    def test_it_is_not_tied_to_a_moderation_run_but_to_the_targets_conversation(self, fake):
        row, _, world, *_ = self.row(fake)
        assert (row.run_id, row.conversation_id) == (None, world.conv.pk)

    def test_the_prompt_version_is_the_prompt_name(self, fake):
        row, *_ = self.row(fake)
        assert row.prompt_version == kit.load_prompt().name == "rater_v1"

    def test_the_prompt_hash_is_recorded(self, fake):
        row, *_ = self.row(fake)
        assert re.fullmatch(r"[0-9a-f]{64}", row.prompt_sha256)

    def test_caching_was_asked_for_and_no_temperature_recorded(self, fake):
        row, *_ = self.row(fake, kit.HAIKU)
        assert (row.request["cache_system"], row.temperature) == (True, None)

    def test_the_reply_allowance_is_a_tunable(self, fake):
        from config import tunables

        row, *_ = self.row(fake)
        allowances = {value for name, value in vars(tunables).items() if "RATER" in name and "TOKENS" in name}
        assert row.max_tokens in allowances

    def test_the_reply_allowance_is_two_thousand_tokens_by_default(self, fake, settings):
        row, *_ = self.row(fake)
        assert (settings.RATER_MAX_TOKENS, row.max_tokens) == (2000, 2000)

    def test_the_call_is_paid_from_the_evaluation_budget(self, fake):
        row, *_ = self.row(fake)
        assert kit.judge_spend() == row.cost_usd


class TestTheStoredRating:
    def test_a_clean_answer_stores_a_done_rating_of_the_target(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert (rating.status, rating.rater_id, rating.target_type, rating.target_id) == ("done", rater.pk, "message", message.pk)

    def test_it_is_the_only_rating_and_has_no_findings(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert (kit.ratings(), kit.findings_of(rating)) == ([rating], [])

    def test_it_links_the_ledger_row(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert rating.llm_call_id == kit.ledger()[0].pk
        assert rating.llm_call == kit.ledger()[0]  # a real foreign key to the ledger row

    def test_the_default_is_replicate_one_and_every_dimension(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert (rating.replicate, sorted(rating.dimensions)) == (1, sorted(kit.DIMENSIONS))

    def test_a_replicate_number_is_stored(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        assert kit.rating_of(kit.rate(rater, message, replicate=3)).replicate == 3

    def test_the_dimensions_asked_for_are_stored_and_are_the_only_rubric_sent(self, fake):
        rater, _, message = setup()
        client = fake(kit.nothing(["abusiveness"]))
        rating = kit.rating_of(kit.rate(rater, message, dimensions=["abusiveness"]))
        system = kit.system_text(client.calls[0])
        assert (rating.dimensions, kit.load_prompt(["abusiveness"]).text == system) == (["abusiveness"], True)

    def test_the_guideline_version_names_the_prompt_and_each_rubric(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert all(part in rating.guideline_version for part in ("rater_v1", "factual_accuracy_v1", "abusiveness_v1"))

    def test_the_guideline_version_names_only_the_rubrics_used(self, fake):
        rater, _, message = setup()
        fake(kit.nothing(["abusiveness"]))
        rating = kit.rating_of(kit.rate(rater, message, dimensions=["abusiveness"]))
        assert ("abusiveness_v1" in rating.guideline_version, "factual_accuracy" in rating.guideline_version) == (True, False)

    def test_started_and_finished_are_recorded_in_order(self, fake):
        rater, _, message = setup()
        fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert rating.started_at is not None and rating.finished_at is not None
        assert rating.started_at <= rating.finished_at

    def test_the_rubric_text_comes_from_the_rubrics_folder_and_its_name_is_recorded(self, fake, settings, tmp_path):
        settings.RATER_RUBRICS_DIR = str(
            kit.write_rubrics(tmp_path / "r", factual_accuracy_v7="TEMP FACTUAL SEVEN", abusiveness_v1="TEMP ABUSIVE ONE")
        )
        rater, _, message = setup()
        client = fake(kit.nothing())
        rating = kit.rating_of(kit.rate(rater, message))
        assert ("TEMP FACTUAL SEVEN" in kit.system_text(client.calls[0]), "factual_accuracy_v7" in rating.guideline_version) == (True, True)

    def test_an_intervention_act_can_be_the_target(self, fake):
        rater, _, message = setup()
        act = kit.act_on(message, "Could you share a source for the census figure?")
        client = fake(kit.answer(kit.finding("f1", "factual_accuracy", "the census figure", 0)))
        result = kit.rate(rater, act)
        (stored,) = kit.findings_of(kit.rating_of(result))
        assert (kit.rating_of(result).target_type, kit.rating_of(result).target_id, stored.quote, len(client.calls)) == (
            "intervention_act", act.pk, "the census figure", 1,
        )
        assert kit.ledger()[0].conversation_id == message.conversation_id


class TestWhoMayRateWhat:
    def test_a_human_rater_is_refused(self, fake):
        _, _, message = setup()
        client = fake(kit.nothing())
        with pytest.raises(ValueError):
            kit.rate(kit.make_human("a-human"), message)
        assert (client.calls, kit.ratings(), kit.ledger()) == ([], [], [])

    def test_an_inactive_llm_rater_is_refused(self, fake):
        rater = kit.make_rater("retired", active=False)
        _, message = kit.single(TEXT)
        client = fake(kit.nothing())
        with pytest.raises(ValueError):
            kit.rate(rater, message)
        assert (client.calls, kit.ratings(), kit.ledger()) == ([], [], [])

    def test_a_moderator_message_cannot_be_rated(self, fake):
        rater = kit.make_rater("rater-mod")
        world = kit.build([("A", TEXT), ("mod", "A moderator reply.")])
        client = fake(kit.nothing())
        with pytest.raises(ValueError):
            kit.rate(rater, world[2])
        assert (client.calls, kit.ratings()) == ([], [])

    @pytest.mark.parametrize("replicate", [0, -1])
    def test_a_replicate_below_one_is_refused(self, fake, replicate):
        rater, _, message = setup()
        client = fake(kit.nothing())
        with pytest.raises(ValueError):
            kit.rate(rater, message, replicate=replicate)
        assert (client.calls, kit.ratings()) == ([], [])
