"""Step 21, part 1: the clarity dimension in the taxonomy, its rubric and the v3 prompts (brief "Part 1")."""
import re

import dim21_kit as kit
import pytest

PROMPTS = ["master_v3", "intervenor_v3"]
RUBRICS = ["factual_accuracy_v1", "abusiveness_v1", "clarity_v1"]


def prompt_text(name):
    path = kit.PROMPT_DIR / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def rubric_text(name):
    path = kit.RUBRIC_DIR / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


class TestTaxonomy:
    def test_clarity_is_a_flagged_only_dimension_for_unclear_statement(self):
        from moderation import taxonomy

        assert taxonomy.DIMENSIONS["clarity"] == {"issue_type": "unclear_statement", "coverage": "flagged_only"}
        assert taxonomy.dimension_for("unclear_statement") == "clarity"

    def test_the_other_two_dimensions_are_unchanged(self):
        from moderation import taxonomy

        assert taxonomy.DIMENSIONS["factual_accuracy"] == {"issue_type": "possible_factual_error", "coverage": "all_claims"}
        assert taxonomy.DIMENSIONS["abusiveness"] == {"issue_type": "abusive_language", "coverage": "flagged_only"}
        assert set(taxonomy.DIMENSIONS) == {"factual_accuracy", "abusiveness", "clarity"}

    def test_every_dimension_has_one_issue_type_and_no_two_share_one(self):
        from moderation import taxonomy

        types = [info["issue_type"] for info in taxonomy.DIMENSIONS.values()]
        assert len(types) == len(set(types))
        assert all(t in taxonomy.ISSUE_TYPES for t in types)

    def test_clarity_has_a_one_sentence_definition_naming_its_rubric(self):
        from moderation import taxonomy

        definition = taxonomy.DEFINITIONS["clarity"]
        assert definition.strip() and definition.count(". ") == 0 and definition.rstrip().endswith(".")
        assert "clarity_v1" in definition

    def test_every_dimension_has_a_definition_and_a_rubric_file(self):
        from moderation import taxonomy

        for dimension in taxonomy.DIMENSIONS:
            assert dimension in taxonomy.DEFINITIONS
            assert (kit.RUBRIC_DIR / f"{dimension}_v1.md").is_file(), dimension

    def test_the_intensity_range_is_still_zero_to_four(self):
        from moderation import taxonomy

        assert taxonomy.INTENSITY_RANGE == (0, 4)

    def test_the_schema_accepts_an_intensity_for_unclear_statement_and_rejects_out_of_range(self):
        from pydantic import ValidationError

        from moderation.schemas import MasterOutput

        def parse(intensity):
            return MasterOutput.model_validate(
                prk_master(intensity)
            )

        for ok in (0, 1, 2, 3, 4, None):
            assert parse(ok).issues[0].intensity == ok
        for bad in (-1, 5, 99):
            with pytest.raises(ValidationError):
                parse(bad)


def prk_master(intensity):
    import pipeline_run_kit as prk

    return prk.master_d(prk.issue_d("i1", 1, "unclear_statement", "q", intensity=intensity))


class TestRubricFile:
    def test_it_has_the_five_levels_and_examples(self):
        text = rubric_text("clarity_v1")
        assert len(text.strip()) > 400
        for level in "01234":
            assert re.search(rf"(?m)^\s*{level} = ", text), f"no level {level} line"
        assert text.lower().count("example") >= 5

    def test_it_names_the_five_levels_in_the_briefs_words(self):
        lowered = rubric_text("clarity_v1").lower()
        for words in ("clear", "slightly vague", "ambiguous", "hard to respond", "unintelligible"):
            assert words in lowered, words

    def test_it_scores_the_statement_never_the_speaker_or_side(self):
        lowered = squash(rubric_text("clarity_v1")).lower()
        assert re.search(r"score the (statement|phrase|message)", lowered)
        assert "never the speaker" in lowered or "not the speaker" in lowered
        assert "side" in lowered

    @pytest.mark.parametrize("word", ["register", "dialect", "formality", "non-native", "typo"])
    def test_it_says_style_that_does_not_change_the_meaning_scores_zero(self, word):
        assert word in rubric_text("clarity_v1").lower()

    def test_it_says_a_clear_statement_the_reader_disagrees_with_is_zero(self):
        lowered = squash(rubric_text("clarity_v1")).lower()
        assert "disagree" in lowered and re.search(r"\bclear\b", lowered)


class TestV3Prompts:
    @pytest.mark.parametrize("prompt", PROMPTS)
    def test_the_file_exists_and_is_substantial(self, prompt):
        assert len(prompt_text(prompt).strip()) > 1000

    def test_the_highest_version_on_disk_is_v3_so_it_is_live(self):
        from moderation import prompting

        assert prompting.load_prompt("master").name == "master_v3"
        assert prompting.load_prompt("intervenor").name == "intervenor_v3"

    @pytest.mark.parametrize("prompt", PROMPTS)
    @pytest.mark.parametrize("value", sorted(__import__("moderation.taxonomy", fromlist=["x"]).ISSUE_TYPES))
    def test_every_issue_type_appears(self, prompt, value):
        assert value in prompt_text(prompt)

    @pytest.mark.parametrize("value", sorted(
        set(__import__("moderation.taxonomy", fromlist=["x"]).ACT_TYPES)
        | set(__import__("moderation.taxonomy", fromlist=["x"]).DECISIONS)
        | set(__import__("moderation.taxonomy", fromlist=["x"]).TONES)
        | set(__import__("moderation.taxonomy", fromlist=["x"]).DISPOSITIONS)
    ))
    def test_the_intervenor_prompt_contains_every_action_side_value(self, value):
        assert value in prompt_text("intervenor_v3")

    def test_the_master_prompt_names_every_dimension_every_not_scorable_reason_and_disagreement_kind(self):
        from moderation import taxonomy

        text = prompt_text("master_v3")
        for value in (*taxonomy.DIMENSIONS, *taxonomy.NOT_SCORABLE_REASONS, *taxonomy.DISAGREEMENT_KINDS):
            assert value in text, value

    @pytest.mark.parametrize("act_type", __import__("moderation.taxonomy", fromlist=["x"]).ACT_TYPES)
    def test_the_master_prompt_never_names_an_act_type(self, act_type):
        if "_" in act_type:
            assert act_type not in prompt_text("master_v3")

    @pytest.mark.parametrize("prompt", PROMPTS)
    def test_issue_type_definitions_appear_verbatim(self, prompt):
        from moderation import taxonomy

        text = squash(prompt_text(prompt))
        for issue_type in taxonomy.ISSUE_TYPES:
            assert squash(taxonomy.DEFINITIONS[issue_type]) in text, issue_type

    def test_act_type_definitions_appear_verbatim_in_the_intervenor_prompt(self):
        from moderation import taxonomy

        text = squash(prompt_text("intervenor_v3"))
        for act_type in taxonomy.ACT_TYPES:
            assert squash(taxonomy.DEFINITIONS[act_type]) in text, act_type

    @pytest.mark.parametrize("prompt", PROMPTS)
    @pytest.mark.parametrize("rubric", RUBRICS)
    def test_each_rubric_is_embedded_verbatim_inline(self, prompt, rubric):
        assert rubric_text(rubric).strip() in prompt_text(prompt)

    def test_the_master_reports_unclear_statement_only_for_intensity_one_to_four(self):
        text = squash(prompt_text("master_v3"))
        assert re.search(r"Report `unclear_statement` only[^.]*intensity 1 to 4", text)

    def test_the_master_prompt_keeps_the_other_two_reporting_rules(self):
        text = squash(prompt_text("master_v3"))
        assert "Report `possible_factual_error` only" in text
        assert "intensity 1 to 4" in text

    def test_v3_is_based_on_v1_not_v2(self):
        master, intervenor = prompt_text("master_v3"), prompt_text("intervenor_v3")
        assert "even if no source was offered" not in master
        assert "widely published facts" not in intervenor
        assert "version 3" in master.splitlines()[0].lower() and "version 3" in intervenor.splitlines()[0].lower()

    def test_the_intervenor_prefers_a_clarification_request_to_a_restatement(self):
        text = squash(prompt_text("intervenor_v3")).lower()
        assert "request_clarification" in text and "clarify_argument" in text
        assert re.search(r"prefer[^.]*`?request_clarification`?[^.]*(over|to|rather than|instead of)[^.]*`?clarify_argument`?", text)
        assert re.search(r"`?clarify_argument`? only when[^.]*sure", text)

    def test_the_intervenor_explains_the_summary_rule(self):
        text = squash(prompt_text("intervenor_v3"))
        lowered = text.lower()
        assert "identify_agreement_disagreement" in text
        assert "summary" in lowered and "discussion map" in lowered or "discussion_map" in lowered
        assert "one to three sentences" in lowered
        assert "impersonal" in lowered
        assert "facts or" in lowered and "values" in lowered

    def test_the_intervenor_prompt_does_not_tell_the_model_to_use_participant_labels_in_the_summary(self):
        lowered = squash(prompt_text("intervenor_v3")).lower()
        assert "the other side" in lowered, "the rule must forbid viewer-relative words by name"
        assert "participant a" in lowered

    @pytest.mark.parametrize("prompt", PROMPTS)
    def test_the_standing_rules_survive(self, prompt):
        text = prompt_text(prompt).lower()
        assert "neutral" in text or "equal" in text
        assert "politic" in text and "infer" in text and "label" in text
        assert re.search(r"\bdata\b", text) and "ignore" in text and "instruction" in text
