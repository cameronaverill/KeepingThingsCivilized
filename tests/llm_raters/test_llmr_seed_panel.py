"""manage.py seed_panel: idempotent, creates the two LLM raters and a panel that records the rubric hashes, and a changed
rubric file makes a new panel version without touching the old one (docs/step14_brief.md, 14a)."""
import re

import llmr_kit as kit
import pytest


def raters():
    from evaluation.models import Rater

    return list(Rater.objects.order_by("name"))


def panels(name=None):
    from evaluation.models import Panel

    rows = Panel.objects.order_by("pk")
    return list(rows if name is None else rows.filter(name=name))


def counts():
    from evaluation.models import Panel, PanelMember, Rater

    return (Rater.objects.count(), Panel.objects.count(), PanelMember.objects.count())


def real_hashes():
    return {d: {"rubric": f"{d}_v1", "sha256": kit.sha256_of(kit.RUBRICS_DIR / f"{d}_v1.md")} for d in kit.DIMENSIONS}


class TestTheRaters:
    def test_it_creates_the_two_llm_raters_from_the_judge_models(self):
        kit.seed_panel("--name", "p")
        assert [(r.name, r.kind, r.provider, r.model, r.active, r.user_id) for r in raters()] == [
            ("llm-haiku-4-5", "llm", "anthropic", "claude-haiku-4-5", True, None),
            ("llm-sonnet-5", "llm", "anthropic", "claude-sonnet-5", True, None),
        ]

    def test_the_raters_follow_the_setting(self, tune):
        tune(JUDGE_MODELS=("claude-haiku-4-5",))
        kit.seed_panel("--name", "p")
        assert [(r.name, r.model) for r in raters()] == [("llm-haiku-4-5", "claude-haiku-4-5")]

    def test_the_raters_are_the_panels_members(self):
        kit.seed_panel("--name", "p")
        (panel,) = panels("p")
        assert sorted(r.name for r in panel.raters.all()) == ["llm-haiku-4-5", "llm-sonnet-5"]

    def test_an_existing_rater_row_is_reused_not_duplicated(self):
        from evaluation.models import Rater

        existing = Rater.objects.create(name="llm-sonnet-5", kind="llm", provider="anthropic", model="claude-sonnet-5")
        kit.seed_panel("--name", "p")
        assert (Rater.objects.filter(name="llm-sonnet-5").count(), Rater.objects.get(name="llm-sonnet-5").pk) == (1, existing.pk)


class TestThePanel:
    def test_it_records_the_rubric_name_and_sha256_of_every_dimension(self):
        kit.seed_panel("--name", "p")
        (panel,) = panels("p")
        assert panel.dimensions == real_hashes()

    def test_the_hashes_are_sha256_hex(self):
        kit.seed_panel("--name", "p")
        (panel,) = panels("p")
        assert all(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) for entry in panel.dimensions.values())

    def test_the_consensus_thresholds_come_from_the_tunables(self, tune):
        tune(SPAN_MATCH_MIN_IOU=0.7, INTENSITY_DISAGREEMENT_THRESHOLD=3)
        kit.seed_panel("--name", "p")
        (panel,) = panels("p")
        assert (panel.span_match_min_iou, panel.intensity_disagreement_threshold) == (0.7, 3)

    def test_the_default_thresholds_are_the_defaults_of_the_tunables(self):
        kit.seed_panel("--name", "p")
        (panel,) = panels("p")
        assert (panel.span_match_min_iou, panel.intensity_disagreement_threshold) == (0.5, 2)

    def test_the_name_defaults_to_llm_panel_and_the_first_version_is_1(self):
        result = kit.seed_panel()
        assert (result.exc, [(p.name, p.version) for p in panels()]) == (None, [("llm-panel", "1")])

    def test_the_raters_have_no_temperature(self):
        kit.seed_panel("--name", "p")
        assert {r.temperature for r in raters()} == {None}

    def test_the_name_argument_names_the_panel(self):
        kit.seed_panel("--name", "my-panel")
        assert [p.name for p in panels()] == ["my-panel"]

    def test_an_explicit_version_is_used_as_given(self):
        kit.seed_panel("--name", "p", "--version", "v7")
        assert [(p.name, p.version) for p in panels()] == [("p", "v7")]

    def test_it_prints_what_it_created(self):
        out = kit.seed_panel("--name", "p").out
        assert (re.search(r"creat", out, re.IGNORECASE) is not None, "p" in out) == (True, True)


class TestIdempotent:
    def test_running_it_twice_adds_nothing(self):
        kit.seed_panel("--name", "p")
        first = counts()
        kit.seed_panel("--name", "p")
        assert (counts(), first) == ((2, 1, 2), (2, 1, 2))

    def test_the_second_run_says_it_reused_what_exists(self):
        first = kit.seed_panel("--name", "p").out
        second = kit.seed_panel("--name", "p").out
        assert (re.search(r"reus", second, re.IGNORECASE) is not None, second != first) == (True, True)

    def test_an_explicit_version_run_twice_adds_nothing(self):
        kit.seed_panel("--name", "p", "--version", "v7")
        kit.seed_panel("--name", "p", "--version", "v7")
        assert counts() == (2, 1, 2)

    def test_two_different_names_give_two_panels_sharing_the_raters(self):
        kit.seed_panel("--name", "one")
        kit.seed_panel("--name", "two")
        assert (counts(), sorted(p.name for p in panels())) == ((2, 2, 4), ["one", "two"])


class TestAChangedRubricMakesANewVersion:
    def seeded_in(self, tmp_path, settings):
        folder = kit.copy_real_rubrics(tmp_path / "rubrics")
        settings.RATER_RUBRICS_DIR = str(folder)
        kit.seed_panel("--name", "p")
        return folder

    def test_the_first_panel_records_the_hashes_of_the_files_it_was_made_from(self, tmp_path, settings):
        self.seeded_in(tmp_path, settings)
        (panel,) = panels("p")
        assert panel.dimensions == real_hashes()

    def test_after_an_edit_a_second_panel_of_the_same_name_appears_with_another_version(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        old, new = panels("p")
        assert (len(panels("p")), old.version != new.version) == (2, True)

    def test_the_old_panel_is_never_edited(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (old,) = panels("p")
        before = (old.pk, old.version, old.dimensions, old.span_match_min_iou, sorted(r.pk for r in old.raters.all()))
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        old.refresh_from_db()
        assert (old.pk, old.version, old.dimensions, old.span_match_min_iou, sorted(r.pk for r in old.raters.all())) == before

    def test_the_new_panel_records_the_new_hash_and_keeps_the_unchanged_one(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        _, new = panels("p")
        assert new.dimensions == {
            "factual_accuracy": {"rubric": "factual_accuracy_v1", "sha256": kit.sha256_of(folder / "factual_accuracy_v1.md")},
            "abusiveness": real_hashes()["abusiveness"],
        }

    def test_the_new_panel_has_the_same_raters_and_no_rater_is_duplicated(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "abusiveness_v1.md").write_text("EDITED ABUSIVENESS RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        old, new = panels("p")
        assert (sorted(r.name for r in new.raters.all()), sorted(r.pk for r in new.raters.all()) == sorted(r.pk for r in old.raters.all())) == (
            ["llm-haiku-4-5", "llm-sonnet-5"], True,
        )

    def test_versions_count_up_as_1_2_3(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v1.md").write_text("EDIT ONE", encoding="utf-8")
        kit.seed_panel("--name", "p")
        (folder / "factual_accuracy_v1.md").write_text("EDIT TWO", encoding="utf-8")
        kit.seed_panel("--name", "p")
        assert [p.version for p in panels("p")] == ["1", "2", "3"]

    def test_a_changed_threshold_makes_a_new_version_too(self, tmp_path, settings, tune):
        self.seeded_in(tmp_path, settings)
        tune(SPAN_MATCH_MIN_IOU=0.6)
        kit.seed_panel("--name", "p")
        old, new = panels("p")
        assert ((old.version, old.span_match_min_iou), (new.version, new.span_match_min_iou)) == (("1", 0.5), ("2", 0.6))

    def test_a_changed_member_list_makes_a_new_version_too(self, tmp_path, settings):
        from evaluation.models import PanelMember

        self.seeded_in(tmp_path, settings)
        (old,) = panels("p")
        PanelMember.objects.filter(panel=old, rater__name="llm-haiku-4-5").delete()
        kit.seed_panel("--name", "p")
        old, new = panels("p")
        assert ([r.name for r in old.raters.all()], sorted(r.name for r in new.raters.all()), new.version) == (
            ["llm-sonnet-5"], ["llm-haiku-4-5", "llm-sonnet-5"], "2",
        )

    def test_an_explicit_version_that_exists_with_other_contents_is_refused_and_changes_nothing(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        result = kit.seed_panel("--name", "p", "--version", "1")
        (only,) = panels("p")
        assert (result.exc is not None, only.dimensions == real_hashes()) == (True, True)

    def test_an_explicit_version_that_exists_with_the_same_contents_is_reused(self, tmp_path, settings):
        self.seeded_in(tmp_path, settings)
        result = kit.seed_panel("--name", "p", "--version", "1")
        assert (result.exc, len(panels("p"))) == (None, 1)

    def test_running_again_without_a_further_change_adds_no_third_panel(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        kit.seed_panel("--name", "p")
        assert len(panels("p")) == 2

    def test_a_higher_rubric_version_file_is_a_change_too(self, tmp_path, settings):
        folder = self.seeded_in(tmp_path, settings)
        (folder / "factual_accuracy_v2.md").write_text("A SECOND VERSION OF THE RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        _, new = panels("p")
        assert new.dimensions["factual_accuracy"]["rubric"] == "factual_accuracy_v2"

    def test_a_panel_that_was_rated_against_keeps_its_ratings_after_the_edit(self, tmp_path, settings, fake):
        folder = self.seeded_in(tmp_path, settings)
        (old,) = panels("p")
        _, message = kit.single("Rent control always lowers rents. Anyone who disagrees is an idiot.")
        fake(kit.nothing(), kit.nothing())
        kit.run_panel(old, [message])
        (folder / "factual_accuracy_v1.md").write_text("EDITED FACTUAL RUBRIC", encoding="utf-8")
        kit.seed_panel("--name", "p")
        assert len(kit.ratings(status="done")) == 2


class TestItNeverCallsTheApi:
    def test_no_client_is_built_no_ledger_row_written_and_no_key_needed(self, settings, fake):
        settings.ANTHROPIC_API_KEY = ""
        settings.LLM_ENABLED = False
        client = fake()
        result = kit.seed_panel("--name", "p")
        assert (result.exc, client.calls, kit.ledger()) == (None, [], [])

    def test_it_never_prints_a_key(self):
        result = kit.seed_panel("--name", "p")
        assert kit.DUMMY_KEY not in result.text
