"""review_markdown, the judge rubric file, and the purity of the seeding package."""
import ast
import re
from pathlib import Path

import pytest
import seeding_kit as kit
from seeding.facts import load_facts
from seeding.review import review_markdown
from seeding.seeds import build_seeds

ROOT = Path(__file__).resolve().parents[2]
RUBRIC = ROOT / "rubrics" / "factual_tag_v1.md"


DRAFT = "Draft seeds (not ready)"


class TestReviewDraftSeeds:
    def test_unverified_statistic_with_inflate_favors_shows_draft_table(self):
        out = review_markdown([kit.stat(inflate_favors="left", claim_template="About {v0} zed")])
        assert (DRAFT in out, "| Side |" in out, "About 110 zed" in out, "About 90.9 zed" in out) == (True,) * 4

    def test_draft_table_comes_after_missing_list(self):
        out = review_markdown([kit.stat(inflate_favors="left")])
        assert out.index("Missing:") < out.index(DRAFT) < out.index("| Side |")

    def test_unapproved_non_statistic_with_full_claims_shows_draft_table(self):
        out = review_markdown([kit.nonstat(owner_verified_true=True, error_claims=kit.claims())])
        assert (DRAFT in out, "claim left" in out, "claim right" in out, "mirrors not approved" in out.lower()) == (True,) * 4

    def test_non_statistic_draft_table_has_two_rows_with_dash_level(self):
        out = review_markdown([kit.nonstat(error_claims=kit.claims())])
        assert out.count("\n| left |") + out.count("\n| right |") == 2
        assert ("| left | - | - | claim left |" in out, "| right | - | - | claim right |" in out) == (True, True)

    def test_statistic_table_has_six_rows_with_numeric_levels(self):
        out = review_markdown([kit.ready_range()])
        assert out.count("\n| left |") + out.count("\n| right |") == 6
        assert ("| left | 1 | inflate |" in out, "| right | 3 | deflate |" in out) == (True, True)

    def test_no_draft_table_when_statistic_cannot_build(self):
        out = review_markdown([kit.stat()])
        assert (DRAFT in out, "| Side |" in out) == (False, False)

    def test_no_draft_table_when_non_statistic_has_no_claims(self):
        out = review_markdown([kit.nonstat(owner_verified_true=True)])
        assert (DRAFT in out, "| Side |" in out) == (False, False)

    def test_no_draft_table_when_one_side_only(self):
        out = review_markdown([kit.nonstat(error_claims={"left": "only left"})])
        assert DRAFT not in out

    def test_ready_fact_has_normal_table_without_draft_label(self):
        out = review_markdown([kit.ready_range()])
        assert (DRAFT in out, "| Side |" in out) == (False, True)

    def test_draft_label_only_for_the_not_ready_fact(self):
        out = review_markdown([kit.ready_range(), kit.stat(inflate_favors="left")])
        assert out.count(DRAFT) == 1

    def test_not_ready_statistic_whose_seeds_fail_does_not_raise_or_show_table(self):
        out = review_markdown([kit.stat(inflate_favors="left", true_values=[60], max_value=100)])
        assert (DRAFT in out, "stat_fact" in out) == (False, True)

    def test_shipped_non_statistics_show_draft_seeds(self):
        facts = load_facts()
        out = review_markdown([f for f in facts if f.type != "statistic"])
        assert (out.count("| Side |"), "Seeds cannot be built" in out) == (10, False)


class TestReview:
    def test_returns_string(self):
        assert isinstance(review_markdown([kit.ready_range()]), str)

    def test_ready_fact_shows_metadata(self):
        out = review_markdown([kit.ready_range(source_note="Source XYZ")])
        for needle in ("range_fact", "statistic", "Between 500 and 560 things exist", "Source XYZ"):
            assert needle in out

    def test_ready_fact_shows_all_six_seed_claims(self):
        f = kit.ready_range()
        out = review_markdown([f])
        assert [s.false_claim in out for s in build_seeds(f)] == [True] * 6

    def test_ready_fact_shows_directions_and_sides(self):
        out = review_markdown([kit.ready_range()]).lower()
        assert ("inflate" in out, "deflate" in out, "left" in out, "right" in out) == (True,) * 4

    def test_ready_non_statistic_shows_claims(self):
        out = review_markdown([kit.ready_nonstat("qualitative")])
        assert ("claim left" in out, "claim right" in out, "qualitative_fact" in out) == (True,) * 3

    def test_one_section_per_fact_all_ids_present(self):
        out = review_markdown([kit.ready_range(), kit.ready_nonstat("law"), kit.stat(id="third")])
        assert ("range_fact" in out, "law_fact" in out, "third" in out) == (True,) * 3

    def test_unverified_reason_listed(self):
        assert "owner has not verified" in review_markdown([kit.stat()]).lower()

    def test_inflate_favors_reason_listed(self):
        out = review_markdown([kit.stat(owner_verified_true=True)]).lower()
        assert "inflate_favors not set" in out
        assert "owner has not verified" not in out

    def test_mirrors_reason_listed(self):
        out = review_markdown([kit.nonstat(owner_verified_true=True)]).lower()
        assert "mirrors not approved" in out
        assert "owner has not verified" not in out

    def test_not_ready_fact_shows_no_seed_table(self):
        assert "| Side |" not in review_markdown([kit.stat()])

    def test_ready_fact_shows_seed_table_header(self):
        assert "| Side |" in review_markdown([kit.ready_range()])

    def test_ready_fact_whose_seeds_cannot_be_built_does_not_raise(self):
        out = review_markdown([kit.ready_stat(true_values=[60], max_value=100)])
        assert "stat_fact" in out

    def test_ready_fact_has_no_missing_reasons(self):
        out = review_markdown([kit.ready_range()]).lower()
        assert ("owner has not verified" in out, "inflate_favors not set" in out) == (False, False)

    @pytest.mark.parametrize("fact", [
        kit.stat(), kit.nonstat(), kit.nonstat("qualitative", owner_verified_true=True),
        kit.stat(true_values=[60], max_value=100, owner_verified_true=True),
        kit.ready_nonstat(error_claims=None),
    ])
    def test_never_raises_for_not_ready(self, fact):
        assert isinstance(review_markdown([fact]), str)

    def test_shipped_facts_review_lists_all_ids(self):
        facts = load_facts()
        out = review_markdown(facts)
        assert [f.id in out for f in facts] == [True] * len(facts)

    def test_framing_line_printed_when_set(self):
        out = review_markdown([kit.stat(framing="Used to argue X")])
        assert "Framing: Used to argue X" in out

    def test_no_framing_line_when_unset(self):
        assert "Framing:" not in review_markdown([kit.stat(), kit.ready_range(), kit.nonstat()])

    def test_framing_printed_for_ready_and_non_statistic_facts(self):
        out = review_markdown([kit.ready_range(framing="F-ready"), kit.nonstat("law", framing="F-law")])
        assert ("Framing: F-ready" in out, "Framing: F-law" in out) == (True, True)

    def test_shipped_split_entries_show_their_framing(self):
        facts = load_facts()
        out = review_markdown(facts)
        assert [f"Framing: {f.framing}" in out for f in facts[:8]] == [True] * 8

    def test_empty_list_does_not_raise(self):
        assert isinstance(review_markdown([]), str)


@pytest.fixture(scope="module")
def rubric():
    return RUBRIC.read_text()


class TestRubric:
    def test_file_exists(self):
        assert RUBRIC.is_file()

    @pytest.mark.parametrize("tag", ["0", "1", "2", "3", "N/A"])
    def test_all_five_tags_defined_as_own_line(self, rubric, tag):
        assert re.search(rf"(?m)^[\s`*#>-]*{re.escape(tag)}[`*]*\s*[=:.)-]", rubric)

    def test_exactly_five_tag_lines(self, rubric):
        assert len(re.findall(r"(?m)^[\s`*#>-]*(?:0|1|2|3|N/A)[`*]*\s*[=:.)-]", rubric)) == 5

    @pytest.mark.parametrize("phrase", [
        "did not spot",
        "did not correct",
        "incorrect correction",
        "no factual claim",
    ])
    def test_tag_meanings(self, rubric, phrase):
        assert phrase in rubric.lower()

    def test_second_task_counts_unseeded_errors(self, rubric):
        low = rubric.lower()
        assert ("count" in low, "not the seeded" in low or "other than the seeded" in low) == (True, True)

    @pytest.mark.parametrize("phrase", ["count only", "do not judge whether"])
    def test_second_task_is_count_only(self, rubric, phrase):
        assert phrase in rubric.lower()

    def test_judge_inputs_named_together_as_given(self, rubric):
        low = rubric.lower().replace("\u2019", "'")
        assert re.search(r"given[^.]*seeded false claim[^.]*true fact[^.]*moderator's response", low)

    @pytest.mark.parametrize("phrase", ["never told", "political side", "severity level"])
    def test_blindness_statement(self, rubric, phrase):
        assert phrase in rubric.lower()


PKG = ROOT / "seeding"
FORBIDDEN = {"django", "anthropic", "requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp", "moderation"}


def py_files():
    return sorted(PKG.rglob("*.py"))


def imported_roots(path):
    roots = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


class TestPurity:
    def test_package_files_exist(self):
        assert [p.name for p in py_files() if p.parent == PKG] == [
            "__init__.py", "analyze.py", "arms.py", "facts.py", "generate.py", "judge.py", "research_analyze.py", "research_eval.py", "review.py", "seeds.py",
        ]

    def test_prompt_files_are_the_step_12_ones(self):
        assert sorted(p.name for p in (PKG / "prompts").glob("*")) == ["audit_v1.md", "generator_v1.md", "judge_research_v1.md", "judge_research_v2.md", "judge_v1.md", "mirror_v1.md"]

    def test_no_forbidden_imports_anywhere_but_the_generator(self):
        # Only seeding/generate.py may reach django/moderation (the gateway); everything else in seeding/ stays pure.
        found = {p.name: sorted(set(imported_roots(p)) & FORBIDDEN) for p in py_files() if p.name not in ("generate.py", "judge.py", "research_eval.py")}
        assert {k: v for k, v in found.items() if v} == {}

    def test_arms_is_pure(self):
        assert sorted(set(imported_roots(PKG / "arms.py")) & FORBIDDEN) == []

    def test_generate_may_use_only_django_and_moderation_of_the_forbidden_roots(self):
        # No provider library and no network module: the gateway (moderation.llm) is the only way to a model.
        allowed = {"django", "moderation"}
        assert sorted((set(imported_roots(PKG / "generate.py")) & FORBIDDEN) - allowed) == []

    def test_generate_imports_anthropic_nowhere(self):
        assert "anthropic" not in imported_roots(PKG / "generate.py")

    def test_importing_does_not_load_django_or_anthropic(self):
        import subprocess
        import sys

        code = ("import sys, seeding.facts, seeding.seeds, seeding.review;"
                "print(sorted(m for m in ('django','anthropic') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        assert (out.returncode, out.stdout.strip()) == (0, "[]")

    def test_tunables_read_by_attribute_at_call_time(self):
        src = (PKG / "seeds.py").read_text()
        assert "from config import tunables" in src
        assert "SEED_LEVEL_FACTORS" in src
