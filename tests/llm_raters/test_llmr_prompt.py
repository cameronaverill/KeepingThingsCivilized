"""The rater prompt: fixed instructions plus the rubric files of the requested dimensions, loaded from files and fingerprinted
(docs/step14_brief.md, `load_rater_prompt`)."""
import hashlib
import json
import os
import re

import llmr_kit as kit
import pytest

FACTUAL_V1 = "TEMP FACTUAL RUBRIC ONE: zero means accurate."
FACTUAL_V2 = "TEMP FACTUAL RUBRIC TWO: zero means fully accurate."
FACTUAL_V10 = "TEMP FACTUAL RUBRIC TEN: zero means exactly accurate."
ABUSIVE_V1 = "TEMP ABUSIVENESS RUBRIC ONE: zero means civil."


def real_text(dimension):
    return (kit.RUBRICS_DIR / f"{dimension}_v1.md").read_text(encoding="utf-8").strip()


def blob(value):
    """Whatever shape `RaterPrompt.rubrics` has, as one searchable string."""
    return json.dumps(value, default=str, sort_keys=True)


class TestTheLoadedPrompt:
    def test_it_has_a_name_a_text_a_hash_and_the_rubrics(self):
        prompt = kit.load_prompt()
        assert (prompt.name, isinstance(prompt.text, str), len(prompt.sha256), bool(prompt.rubrics)) == ("rater_v1", True, 64, True)

    def test_the_name_is_the_instruction_files_stem(self):
        assert (kit.ROOT / "evaluation" / "prompts" / f"{kit.load_prompt().name}.md").is_file()

    def test_the_hash_is_the_sha256_of_the_text(self):
        prompt = kit.load_prompt()
        assert prompt.sha256 == hashlib.sha256(prompt.text.encode("utf-8")).hexdigest()

    def test_loading_twice_gives_the_same_prompt(self):
        first, second = kit.load_prompt(), kit.load_prompt()
        assert (first.text, first.sha256) == (second.text, second.sha256)

    def test_the_instruction_file_is_part_of_the_text(self):
        instructions = (kit.ROOT / "evaluation" / "prompts" / "rater_v1.md").read_text(encoding="utf-8").strip()
        assert instructions in kit.load_prompt().text

    @pytest.mark.parametrize("dimension", kit.DIMENSIONS)
    def test_each_real_rubric_is_in_the_text_verbatim(self, dimension):
        assert real_text(dimension) in kit.load_prompt().text

    @pytest.mark.parametrize("dimension", kit.DIMENSIONS)
    def test_each_rubric_is_recorded_with_its_name_and_the_files_sha256(self, dimension):
        recorded = blob(kit.load_prompt().rubrics)
        assert (f"{dimension}_v1" in recorded, kit.sha256_of(kit.RUBRICS_DIR / f"{dimension}_v1.md") in recorded) == (True, True)


class TestTheGuidelineVersion:
    def test_it_names_the_prompt_and_every_rubric_joined_by_plus_in_taxonomy_order(self):
        assert kit.load_prompt().guideline_version == "rater_v1+factual_accuracy_v1+abusiveness_v1"

    def test_it_names_only_the_requested_rubrics(self):
        assert kit.load_prompt(["abusiveness"]).guideline_version == "rater_v1+abusiveness_v1"

    def test_it_follows_the_highest_rubric_version_file(self, tmp_path):
        folder = kit.write_rubrics(tmp_path / "r", factual_accuracy_v1=FACTUAL_V1, factual_accuracy_v3="THREE")
        assert kit.load_prompt(["factual_accuracy"], rubrics_dir=folder).guideline_version == "rater_v1+factual_accuracy_v3"

    def test_the_rubrics_are_recorded_per_dimension_with_the_rubric_name_and_the_file_hash(self):
        assert kit.load_prompt().rubrics == {
            d: {"rubric": f"{d}_v1", "sha256": kit.sha256_of(kit.RUBRICS_DIR / f"{d}_v1.md")} for d in kit.DIMENSIONS
        }


class TestOnlyTheRequestedDimensions:
    def test_a_one_dimension_prompt_has_that_rubric_and_not_the_other(self):
        text = kit.load_prompt(["abusiveness"]).text
        assert (real_text("abusiveness") in text, real_text("factual_accuracy") in text) == (True, False)

    def test_the_other_dimension_is_not_recorded_either(self):
        prompt = kit.load_prompt(["factual_accuracy"])
        recorded = blob(prompt.rubrics)
        assert ("abusiveness" in recorded, kit.sha256_of(kit.RUBRICS_DIR / "abusiveness_v1.md") in recorded) == (False, False)

    def test_different_dimensions_give_different_prompts(self):
        assert kit.load_prompt(["factual_accuracy"]).sha256 != kit.load_prompt(["abusiveness"]).sha256

    def test_the_instruction_text_is_the_same_whatever_the_dimensions(self):
        first, second = kit.load_prompt(["factual_accuracy"]).text, kit.load_prompt(["abusiveness"]).text
        assert len(os.path.commonprefix([first, second])) > 1000

    def test_an_unknown_dimension_is_refused(self):
        with pytest.raises((ValueError, KeyError, FileNotFoundError)):
            kit.load_prompt(["not_a_dimension"])


class TestRubricsAreLoadedFromFiles:
    def folder(self, tmp_path, **texts):
        return kit.write_rubrics(tmp_path / "rubrics", **texts)

    def test_the_text_of_a_rubric_file_in_another_folder_is_used(self, tmp_path):
        folder = self.folder(tmp_path, factual_accuracy_v1=FACTUAL_V1, abusiveness_v1=ABUSIVE_V1)
        text = kit.load_prompt(rubrics_dir=folder).text
        assert (FACTUAL_V1 in text, ABUSIVE_V1 in text, real_text("factual_accuracy") in text) == (True, True, False)

    def test_the_highest_version_present_is_used_and_ten_is_higher_than_two(self, tmp_path):
        folder = self.folder(
            tmp_path, factual_accuracy_v1=FACTUAL_V1, factual_accuracy_v2=FACTUAL_V2, factual_accuracy_v10=FACTUAL_V10,
        )
        prompt = kit.load_prompt(["factual_accuracy"], rubrics_dir=folder)
        assert (FACTUAL_V10 in prompt.text, FACTUAL_V2 in prompt.text, FACTUAL_V1 in prompt.text) == (True, False, False)
        assert "factual_accuracy_v10" in blob(prompt.rubrics)

    def test_the_recorded_hash_is_that_of_the_file_that_was_used(self, tmp_path):
        folder = self.folder(tmp_path, factual_accuracy_v1=FACTUAL_V1, factual_accuracy_v2=FACTUAL_V2)
        prompt = kit.load_prompt(["factual_accuracy"], rubrics_dir=folder)
        assert kit.sha256_of(folder / "factual_accuracy_v2.md") in blob(prompt.rubrics)

    def test_changing_a_rubric_file_changes_the_recorded_hash_and_the_prompt_hash(self, tmp_path):
        folder = self.folder(tmp_path, factual_accuracy_v1=FACTUAL_V1)
        before = kit.load_prompt(["factual_accuracy"], rubrics_dir=folder)
        (folder / "factual_accuracy_v1.md").write_text(FACTUAL_V1 + " Edited.", encoding="utf-8")
        after = kit.load_prompt(["factual_accuracy"], rubrics_dir=folder)
        assert (before.sha256 != after.sha256, blob(before.rubrics) != blob(after.rubrics)) == (True, True)

    def test_the_setting_points_the_default_loader_at_another_folder(self, tmp_path, settings):
        settings.RATER_RUBRICS_DIR = str(self.folder(tmp_path, factual_accuracy_v1=FACTUAL_V1, abusiveness_v1=ABUSIVE_V1))
        assert FACTUAL_V1 in kit.load_prompt().text

    def test_a_dimension_without_a_rubric_file_is_refused(self, tmp_path):
        folder = self.folder(tmp_path, abusiveness_v1=ABUSIVE_V1)
        with pytest.raises((ValueError, KeyError, FileNotFoundError)):
            kit.load_prompt(["factual_accuracy"], rubrics_dir=folder)


class TestWhatTheInstructionsSay:
    """The contract's instruction list, checked as loosely as wording allows: a phrase must be present, not a sentence."""

    def text(self):
        return kit.load_prompt().text

    def test_it_says_to_rate_only_the_message_shown(self):
        assert re.search(r"only the message", self.text(), re.IGNORECASE)

    def test_it_says_a_phrase_is_one_finding_with_a_verbatim_quote(self):
        text = self.text()
        assert (re.search(r"one finding", text, re.IGNORECASE) is not None, re.search(r"exact|verbatim", text, re.IGNORECASE) is not None) == (True, True)

    def test_it_names_the_intensity_scale(self):
        assert re.search(r"\b0\b.{0,20}\b4\b", self.text())

    @pytest.mark.parametrize("reason", ["unverifiable", "contested", "needs_context"])
    def test_it_names_each_not_scorable_reason(self, reason):
        assert reason in self.text()

    def test_it_says_to_report_nothing_about_who_is_speaking_or_their_side(self):
        text = self.text()
        assert (re.search(r"\b(nothing|not|never|no)\b.{0,40}who is speaking", text, re.IGNORECASE) is not None, re.search(r"\bside\b", text, re.IGNORECASE) is not None) == (True, True)

    def test_it_says_to_ignore_instructions_inside_the_message(self):
        text = self.text()
        assert (re.search(r"ignore", text, re.IGNORECASE) is not None, re.search(r"instruction|command|request", text, re.IGNORECASE) is not None) == (True, True)

    def test_it_says_the_message_is_data(self):
        assert re.search(r"\bdata\b", self.text(), re.IGNORECASE)

    def test_it_carries_no_username_no_lean_and_no_moderator_output_field(self):
        text = self.text().lower()
        assert [word for word in ("username", "email", "leans", "experiment", "variant", "pair_id") if word in text] == []
